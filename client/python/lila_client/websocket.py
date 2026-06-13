"""līlā Python Client — WebSocket connection manager.

Handles connecting to the server, sending world definitions, receiving tick packets,
and subscribing to telemetry events. Runs asyncio in a background thread with
thread-safe queues for communication with the ImGui main loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("lila.client.ws")


@dataclass
class WsMessage:
    """A message received from the server."""
    type: str          # "tick", "session_started", "telemetry"
    data: dict | list  # parsed JSON content or raw telemetry lines


class WebSocketClient:
    """Async WebSocket client running in a background thread.

    The ImGui main loop runs synchronously on the main thread. This class
    bridges asyncio (for WS I/O) with the synchronous render loop using
    thread-safe queues.

    Usage::

        ws = WebSocketClient(host="localhost", port=8001)
        ws.start()
        ws.send_world_definition(world_json)

        # In ImGui render loop:
        msg = ws.recv_message(block=False)
        if msg and msg.type == "tick":
            world.apply_tick(msg.data)
    """

    def __init__(self, host: str = "localhost", port: int = 8001):
        self.host = host
        self.port = port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ready_event = threading.Event()  # signals when loop is ready

        # Outgoing queue (main thread → asyncio thread)
        self._send_queue: asyncio.Queue = asyncio.Queue()

        # Incoming queue (asyncio thread → main thread)
        self._recv_buffer: deque[WsMessage] = deque(maxlen=1000)
        self._recv_lock = threading.Lock()

        # Connection state
        self.connected = False
        self.session_id: Optional[str] = None
        self._world_sent = False  # track if we've sent the world definition

    def start(self) -> None:
        """Start the background asyncio thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background thread and close connections."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)

    # ─── Public API (call from main/ImGui thread) ──────────────────────

    def send_world_definition(self, world_def: dict[str, Any]) -> None:
        """Send a world definition to start a simulation session."""
        self._thread_safe_send(json.dumps(world_def))

    def send_control(self, msg_type: str, **kwargs) -> None:
        """Send a control message (pause, resume, shutdown, rain)."""
        payload = {"type": msg_type}
        payload.update(kwargs)
        self._thread_safe_send(json.dumps(payload))

    def send_heartbeat(self, positions: dict[str, list[float]], events: list[dict]) -> None:
        """Send a heartbeat with client-reported positions and events."""
        payload = {
            "type": "heartbeat",
            "positions": positions,
            "events": events,
        }
        self._thread_safe_send(json.dumps(payload))

    def recv_message(self, block: bool = False, timeout: float = 0.0) -> Optional[WsMessage]:
        """Receive a message from the server (non-blocking by default)."""
        with self._recv_lock:
            if not self._recv_buffer:
                return None
            return self._recv_buffer.popleft()

    def recv_all(self) -> list[WsMessage]:
        """Drain all pending messages."""
        with self._recv_lock:
            msgs = list(self._recv_buffer)
            self._recv_buffer.clear()
            return msgs

    # ─── Telemetry Subscription ────────────────────────────────────────

    def subscribe_telemetry(
        self,
        filters: dict[str, str] | None = None,
    ) -> None:
        """Subscribe to the server's telemetry stream.

        Filters are URL query params (e.g. {"src": "engine", "level": "WARN"}).
        """
        if not self._running:
            return
        # Wait for the asyncio loop to be ready (with timeout)
        if not self._ready_event.wait(timeout=5.0):
            logger.warning("Timed out waiting for WS loop to start")
            return
        # Signal the asyncio loop to open a telemetry connection
        try:
            asyncio.run_coroutine_threadsafe(
                self._open_telemetry(filters), self._loop,
            )
        except RuntimeError:
            pass  # no running loop yet

    # ─── Internal (asyncio thread) ──────────────────────────────────────

    def _run_loop(self) -> None:
        """Run the asyncio event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready_event.set()  # signal that loop is ready
        try:
            self._loop.run_until_complete(self._main_coroutine())
        finally:
            self._loop.close()

    async def _main_coroutine(self) -> None:
        """Main coroutine: connect, receive, dispatch."""
        while self._running:
            try:
                await self._connect_and_run()
            except Exception as e:
                logger.error("WS connection error: %s", e)

            if not self._running:
                break
            logger.info("Reconnecting in %.0fs...", 3.0)
            await asyncio.sleep(3.0)

    async def _connect_and_run(self) -> None:
        """Connect to the server and run receive/send loops."""
        import websockets

        uri = f"ws://{self.host}:{self.port}/ws"
        logger.info("Connecting to %s", uri)

        try:
            async with websockets.connect(uri, max_size=2**20) as ws:
                self.connected = True
                logger.info("Connected to %s", uri)

                # Send world definition if not already sent
                if not self._world_sent:
                    world_def = await self._fetch_world_def()
                    if world_def:
                        await ws.send(json.dumps(world_def))
                        self._world_sent = True
                        logger.info("World definition sent to server")

                # Run receive and send loops concurrently
                recv_task = asyncio.create_task(self._recv_loop(ws))
                send_task = asyncio.create_task(self._send_loop(ws))

                done, pending = await asyncio.wait(
                    [recv_task, send_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        except (ConnectionRefusedError, OSError) as e:
            logger.warning("Connection failed: %s", e)
        finally:
            self.connected = False

    async def _recv_loop(self, ws) -> None:
        """Receive messages from the server."""
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "tick")

                if msg_type == "session_started":
                    self.session_id = data.get("session_id")
                    logger.info("Session started: %s (%d entities)",
                               self.session_id, data.get("entity_count", 0))
                    # Store species definitions for client-side agency
                    if "species" in data:
                        pass  # Will be consumed by world model

                msg = WsMessage(type=msg_type, data=data)
                with self._recv_lock:
                    self._recv_buffer.append(msg)

        except asyncio.CancelledError:
            pass

    async def _send_loop(self, ws) -> None:
        """Send messages from the outgoing queue."""
        try:
            while self._running:
                raw = await self._send_queue.get()
                if raw is None:
                    break  # sentinel
                await ws.send(raw)
        except asyncio.CancelledError:
            pass

    async def _open_telemetry(self, filters: dict[str, str] | None) -> None:
        """Open a separate WebSocket for telemetry streaming."""
        import websockets

        query = ""
        if filters:
            parts = [f"{k}={v}" for k, v in filters.items()]
            query = "?" + "&".join(parts)

        uri = f"ws://{self.host}:{self.port}/telemetry{query}"
        logger.info("Subscribing to telemetry: %s", uri)

        try:
            async with websockets.connect(uri, max_size=2**20) as ws:
                while self._running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        # Telemetry comes as JSONL lines (possibly batched)
                        for line in raw.strip().split("\n"):
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            msg = WsMessage(type="telemetry", data=event)
                            with self._recv_lock:
                                self._recv_buffer.append(msg)
                    except asyncio.TimeoutError:
                        continue
        except (ConnectionRefusedError, OSError) as e:
            logger.warning("Telemetry subscription failed: %s", e)

    async def _fetch_world_def(self) -> Optional[dict]:
        """Fetch world definition from server's /world.json endpoint."""
        try:
            import urllib.request
            url = f"http://{self.host}:{self.port}/world.json"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("Could not fetch world.json: %s", e)
        return None

    def _thread_safe_send(self, data: str) -> None:
        """Push a message to the send queue from any thread."""
        if not self._loop or not self._running:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._send_queue.put(data), self._loop,
            )
        except RuntimeError:
            pass

    def __del__(self) -> None:
        self.stop()
