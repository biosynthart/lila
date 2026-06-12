# Copyright 2025 BioSynthArt Studios LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
līlā Simulation Worker.

Runs a single ecosystem session over WebSocket. One worker per active
session. The lifecycle is:

  1. Client connects via WebSocket
  2. Client sends a world definition JSON (the "Become Alive!" payload)
  3. Worker initializes the engine and begins the tick loop
  4. Worker streams tick packets as JSON at ~100ms intervals
  5. Client can send control messages (pause, resume, shutdown)
  6. Worker shuts down cleanly on disconnect or shutdown command

The worker is deliberately thin — it's async I/O glue around the
synchronous EcosystemEngine. All simulation logic lives in the engine;
the worker just calls step() and ships the result.

Architecture note: the tick loop and the client listener run as two
concurrent asyncio tasks. This prevents the classic deadlock where
a blocking read on the client socket stalls the tick loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from typing import Any

from .adapters import create_adapter
from .engine import EcosystemEngine
from .telemetry import (
    TelemetryBus,
    TelemetrySubscriber,
    build_telemetry_response,
    log_absorption,
    wrap_tick_packet,
)

logger = logging.getLogger("lila.worker")

# -- Configuration -----------------------------------------------------------

DEFAULT_TICK_RATE = 2.0     # seconds between ticks (0.5 Hz — intent-based)
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8001
MAX_TICK_DRIFT = 0.05       # max acceptable drift before skipping sleep
HEARTBEAT_INTERVAL = 1.0    # seconds between client heartbeat sends

# Global telemetry registry — maps session_id → TelemetryBus.
# Allows /logs and /telemetry endpoints to access active sessions.
_telemetry_registry: dict[str, TelemetryBus] = {}


# -- Session -----------------------------------------------------------------

class SimulationSession:
    """
    Manages the lifecycle of a single ecosystem simulation.

    Decoupled from the WebSocket transport so the tick loop can be
    tested independently (see run_headless()).
    """

    def __init__(
        self,
        world_config: dict[str, Any],
        tick_rate: float = DEFAULT_TICK_RATE,
    ):
        self.tick_rate = tick_rate
        self.world_config = world_config

        # Resolve motor adapter from world config
        model_cfg = world_config.get("model", {})
        adapter_name = model_cfg.get("adapter", "static")
        adapter_kwargs: dict[str, Any] = {}

        if "weights" in model_cfg:
            adapter_kwargs["weights"] = model_cfg["weights"]
        if "seed" in model_cfg:
            adapter_kwargs["seed"] = model_cfg["seed"]
        if "latent_dim" in model_cfg:
            adapter_kwargs["latent_dim"] = model_cfg["latent_dim"]

        try:
            motor_adapter = create_adapter(adapter_name, **adapter_kwargs)
            logger.info("Motor adapter: %s", adapter_name)
        except ValueError:
            logger.warning(
                "Unknown adapter '%s', falling back to static", adapter_name
            )
            motor_adapter = create_adapter("static")

        self.engine = EcosystemEngine(
            world_config,
            adapters={"motor": motor_adapter},
        )

        # Control flags
        self._running = False
        self._paused = False

        # Auto-rain from world config (used by search replay)
        rain_cfg = world_config.get("rain", {})
        self._auto_rain_interval = rain_cfg.get("interval", 0)
        self._auto_rain_intensity = rain_cfg.get("intensity", 0.8)

        # Stats
        self.ticks_completed = 0
        self.total_step_time = 0.0

        # Telemetry bus (injected by worker, optional)
        self.telemetry: TelemetryBus | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        logger.info("Session paused at tick %d", self.engine.tick)

    def resume(self) -> None:
        self._paused = False
        logger.info("Session resumed at tick %d", self.engine.tick)

    def stop(self) -> None:
        self._running = False
        logger.info("Session stopped at tick %d", self.engine.tick)

    def rain(self, intensity: float = 0.5) -> None:
        """Trigger rainfall — boosts soil moisture and water sources."""
        self.engine.apply_rain(min(1.0, max(0.1, intensity)))
        logger.info(
            "Rain triggered at tick %d (intensity=%.1f)",
            self.engine.tick, intensity,
        )

    def absorb_heartbeat(self, msg: dict[str, Any]) -> None:
        """Absorb client heartbeat — positions and interaction events.

        Heartbeat messages from the client carry:
          - positions: { entity_id: [x, y, z], ... } — for reconciliation
          - events: [{ type, source_id, target_id, ... }, ...] —
            client-reported interactions (repro, consumption, predation)

        The server absorbs these into its simulation state through the
        engine's absorption layer. See EcosystemEngine.absorb_client_positions()
        and absorb_client_events() for details.
        """
        positions = msg.get("positions", {})
        if positions:
            self.engine.absorb_client_positions(positions)

        events = msg.get("events", [])
        if events:
            self.engine.absorb_client_events(events)

        # Log absorption for coherence debugging
        if self.telemetry and (positions or events):
            log_absorption(
                self.telemetry,
                tick=self.engine.tick,
                positions=positions,
                events=events,
            )

    def step(self) -> dict[str, Any]:
        """Run a single tick and return the packet."""
        t0 = time.monotonic()
        packet = self.engine.step(self.tick_rate)
        elapsed = time.monotonic() - t0

        self.ticks_completed += 1
        self.total_step_time += elapsed

        # Auto-rain from world config (search replay)
        if (self._auto_rain_interval > 0
                and self.ticks_completed > 0
                and self.ticks_completed % self._auto_rain_interval == 0):
            self.engine.apply_rain(self._auto_rain_intensity)
            logger.debug(
                "Auto-rain at tick %d (interval=%d, intensity=%.1f)",
                self.engine.tick, self._auto_rain_interval,
                self._auto_rain_intensity,
            )

        return packet

    async def run_tick_loop(
        self,
        send_fn,
    ) -> None:
        """
        Main tick loop. Calls step() at the configured rate and passes
        each packet to send_fn (an async callable).

        Handles timing carefully: if a tick takes longer than tick_rate,
        we skip the sleep rather than accumulating drift. If we're
        paused, we sleep without stepping.
        """
        self._running = True
        logger.info(
            "Tick loop started: %d entities, biome=%s, rate=%.1fHz",
            len(self.engine.entities),
            self.engine.biome_name,
            1.0 / self.tick_rate,
        )

        try:
            while self._running:
                loop_start = time.monotonic()

                if not self._paused:
                    packet = self.step()
                    session_id = self.world_config.get("session_id", "unknown")
                    packet["session_id"] = session_id

                    # Telemetry: log tick events (intent_emit, spawns, removals)
                    if self.telemetry:
                        wrap_tick_packet(self.telemetry, packet, session_id)
                        # Piggyback telemetry events on tick packets for client streaming
                        recent = self.telemetry.query(limit=50)
                        if recent:
                            packet["_telemetry"] = recent

                    try:
                        await send_fn(json.dumps(packet))
                    except Exception as e:
                        logger.error("Send failed: %s", e)
                        self._running = False
                        break

                # Sleep for the remainder of the tick interval
                elapsed = time.monotonic() - loop_start
                sleep_time = self.tick_rate - elapsed

                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                elif sleep_time < -MAX_TICK_DRIFT:
                    logger.warning(
                        "Tick %d overran by %.1fms",
                        self.engine.tick,
                        -sleep_time * 1000,
                    )

        finally:
            avg_ms = (
                (self.total_step_time / self.ticks_completed * 1000)
                if self.ticks_completed > 0
                else 0
            )
            logger.info(
                "Tick loop ended: %d ticks, avg step=%.2fms",
                self.ticks_completed,
                avg_ms,
            )


# -- Control message handling ------------------------------------------------

# Control messages the client can send during the simulation.
# Kept deliberately minimal for 0.1-alpha.

CONTROL_HANDLERS = {
    "pause": lambda session, _msg: session.pause(),
    "resume": lambda session, _msg: session.resume(),
    "shutdown": lambda session, _msg: session.stop(),
    "rain": lambda session, msg: session.rain(msg.get("intensity", 0.5)),
    # Client agency heartbeat — positions + interaction events
    "heartbeat": lambda session, msg: session.absorb_heartbeat(msg),
}


async def handle_client_messages(
    session: SimulationSession,
    recv_fn,
) -> None:
    """
    Listen for control messages from the client.

    Runs concurrently with the tick loop. Handles:
      - {"type": "pause"}
      - {"type": "resume"}
      - {"type": "shutdown"}

    Unknown message types are logged and ignored (forward-compatible
    with future commands like entity injection).
    """
    try:
        while session.is_running:
            try:
                raw = await asyncio.wait_for(recv_fn(), timeout=1.0)
            except TimeoutError:
                continue
            except Exception:
                # Connection closed or error
                logger.info("Client disconnected")
                session.stop()
                break

            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid JSON from client: %s", raw[:200])
                continue

            msg_type = msg.get("type", "")
            handler = CONTROL_HANDLERS.get(msg_type)

            if handler:
                handler(session, msg)
            else:
                logger.debug("Unknown message type: %s", msg_type)

    except asyncio.CancelledError:
        pass


# -- WebSocket server --------------------------------------------------------

async def handle_telemetry_subscriber(websocket) -> None:
    """Handle a /telemetry WebSocket connection — streams real-time events."""
    import urllib.parse

    # Parse filter parameters from the query string
    path = getattr(websocket, "path", "/telemetry")
    params = {}
    if "?" in path:
        for part in path.split("?", 1)[1].split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[urllib.parse.unquote(k)] = urllib.parse.unquote(v)

    # Find the most recent active session's bus
    target_bus = None
    for bus in reversed(_telemetry_registry.values()):
        target_bus = bus
        break

    if target_bus is None:
        await websocket.send(json.dumps({"error": "no active session"}))
        await websocket.close()
        return

    subscriber = TelemetrySubscriber(websocket, filters=params)
    target_bus.add_subscriber(subscriber)

    try:
        # Keep the connection alive — client sends nothing, we push events
        async for _ in websocket:
            pass  # client messages are ignored (firehose mode)
    except Exception:
        pass
    finally:
        target_bus.remove_subscriber(subscriber)


async def handle_connection(websocket) -> None:
    """
    Dispatch WebSocket connections based on path:
      /ws        → simulation session
      /telemetry → real-time event stream subscriber
    """
    path = getattr(websocket, "path", "/ws")

    if path == "/telemetry":
        await handle_telemetry_subscriber(websocket)
        return

    # Default: simulation session on /ws
    remote = getattr(websocket, "remote_address", ("unknown", 0))
    logger.info("Client connected: %s", remote)

    # Step 1: Wait for world definition
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=30.0)
    except TimeoutError:
        logger.error("Client did not send world definition within 30s")
        await websocket.close(1008, "Timeout waiting for world definition")
        return
    except Exception as e:
        logger.error("Error receiving world definition: %s", e)
        return

    try:
        world_config = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Invalid world definition JSON: %s", e)
        await websocket.close(1003, "Invalid JSON")
        return

    # Validate minimum structure
    if "environment" not in world_config:
        await websocket.close(1003, "Missing 'environment' in world definition")
        return

    session_id = world_config.get("session_id", "unknown")
    logger.info(
        "World definition received: session=%s, entities=%d",
        session_id,
        len(world_config.get("entities", [])),
    )

    # Step 2: Initialize session (before ack so we can include species defs)
    session = SimulationSession(world_config)

    # Telemetry bus for this session
    telemetry = TelemetryBus(session_id=session_id)
    telemetry.start()
    _telemetry_registry[session_id] = telemetry
    session.telemetry = telemetry

    telemetry.info(
        tick=0, src="worker", evt="session_init",
        detail={"entity_count": len(world_config.get("entities", []))},
    )

    # Build species definitions for client-side agency
    species_defs = session.engine.get_species_definitions()

    # Step 3: Send acknowledgement with species reference
    ack = json.dumps({
        "type": "session_started",
        "session_id": session_id,
        "tick_rate": DEFAULT_TICK_RATE,
        "entity_count": len(world_config.get("entities", [])),
        "species": species_defs,  # lightweight species reference for client
    })
    await websocket.send(ack)

    # Step 4: Run tick loop and client listener as concurrent tasks

    # Run tick loop and client listener as concurrent tasks
    tick_task = asyncio.create_task(
        session.run_tick_loop(websocket.send)
    )
    listen_task = asyncio.create_task(
        handle_client_messages(session, websocket.recv)
    )

    # Wait for either task to complete (usually tick loop stops
    # because the client disconnected or sent shutdown)
    done, pending = await asyncio.wait(
        [tick_task, listen_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel the other task
    session.stop()
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Log any exceptions from completed tasks
    for task in done:
        if task.exception():
            logger.error("Task error: %s", task.exception())

    logger.info("Session %s ended: %d ticks", session_id, session.ticks_completed)

    # Shut down telemetry bus (flushes remaining events to disk)
    if telemetry:
        telemetry.warn(
            tick=session.engine.tick, src="worker", evt="session_end",
            detail={"ticks_completed": session.ticks_completed},
        )
        await telemetry.stop()
        _telemetry_registry.pop(session_id, None)


async def start_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    viz_dir: str | None = None,
    world_file: str | None = None,
) -> None:
    """
    Start the combined HTTP + WebSocket server.

    Serves the browser visualizer on HTTP and handles simulation
    sessions over WebSocket at /ws. Single port, single container.

    HTTP routes:
      GET /           → viz/index.html
      GET /index.html → viz/index.html
      GET /world.json → demo world definition
      Other paths     → 404

    WebSocket:
      /ws → simulation session
    """
    # Import here so the module is importable without websockets
    # installed (e.g., for testing SimulationSession directly)
    try:
        import websockets
        from websockets.datastructures import Headers
        from websockets.http11 import Response
    except ImportError:
        logger.error(
            "websockets package not installed. "
            "Install with: pip install websockets"
        )
        return

    # Locate static files
    import pathlib

    # Search order for viz directory
    viz_search = [
        viz_dir,
        os.environ.get("VIZ_DIR"),
        str(pathlib.Path(__file__).parent.parent.parent / "client" / "browser"),  # repo: client/browser/
        "/app/viz",  # Docker container
        str(pathlib.Path(__file__).parent.parent / "viz"),  # fallback
    ]
    viz_path = None
    for candidate in viz_search:
        if candidate and pathlib.Path(candidate).is_dir():
            viz_path = pathlib.Path(candidate)
            break

    # Search order for world definition
    world_search = [
        world_file,
        os.environ.get("WORLD_FILE"),
        str(pathlib.Path(__file__).parent.parent / "examples" / "demo_world.json"),  # server/examples/
        "/app/demo_world.json",  # Docker container
    ]
    world_path = None
    for candidate in world_search:
        if candidate and pathlib.Path(candidate).is_file():
            world_path = pathlib.Path(candidate)
            break

    # Cache static content at startup
    viz_html = None
    if viz_path and (viz_path / "index.html").is_file():
        viz_html = (viz_path / "index.html").read_bytes()
        logger.info("Serving visualizer from %s", viz_path)
    else:
        logger.warning("Visualizer not found — HTTP will return 404")

    # MIME types for static file serving
    _MIME = {
        ".html": "text/html; charset=utf-8",
        ".css":  "text/css; charset=utf-8",
        ".js":   "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".svg":  "image/svg+xml",
        ".ico":  "image/x-icon",
    }

    world_json = None
    if world_path:
        world_json = world_path.read_bytes()
        logger.info("Serving world definition from %s", world_path)
    else:
        logger.warning("demo_world.json not found — /world.json will 404")

    # HTTP request handler (runs before WebSocket upgrade)
    async def process_request(connection, request):
        """Serve static files on non-WebSocket paths."""
        if request.path == "/ws":
            return None  # Proceed with WebSocket upgrade

        # Telemetry WS endpoint — streams real-time events
        if request.path == "/telemetry":
            return None  # Proceed with WebSocket upgrade (handled separately)

        # /logs HTTP API — query recent telemetry events
        if request.path.startswith("/logs"):
            target_bus = None
            for bus in reversed(_telemetry_registry.values()):
                target_bus = bus
                break
            if target_bus is None:
                return Response(
                    404, "Not Found",
                    Headers({"Content-Type": "text/plain"}),
                    b"No active telemetry session",
                )
            status, headers_dict, body = build_telemetry_response(target_bus, request.path)
            resp_headers = Headers(headers_dict)
            return Response(status, "OK" if status == 200 else "Not Found", resp_headers, body)

        if request.path in ("/", "/index.html"):
            if viz_html:
                return Response(
                    200, "OK",
                    Headers({"Content-Type": "text/html; charset=utf-8"}),
                    viz_html,
                )
            return Response(404, "Not Found", Headers(), b"Visualizer not found")

        if request.path == "/world.json":
            if world_json:
                return Response(
                    200, "OK",
                    Headers({"Content-Type": "application/json"}),
                    world_json,
                )
            return Response(404, "Not Found", Headers(), b"World definition not found")

        # Serve arbitrary static files from viz directory (css/, js/, etc.)
        if viz_path:
            import urllib.parse
            safe_path = urllib.parse.unquote(request.path.lstrip("/"))
            file_path = (viz_path / safe_path).resolve()
            # Ensure the resolved path is still under viz_path (no directory traversal)
            try:
                file_path.relative_to(viz_path.resolve())
            except ValueError:
                return Response(403, "Forbidden", Headers(), b"Access denied")

            if file_path.is_file():
                ext = file_path.suffix.lower()
                content_type = _MIME.get(ext, "application/octet-stream")
                try:
                    data = file_path.read_bytes()
                    return Response(
                        200, "OK",
                        Headers({"Content-Type": content_type}),
                        data,
                    )
                except OSError as e:
                    logger.warning("Failed to read %s: %s", safe_path, e)

        return Response(404, "Not Found", Headers(), b"Not found")

    logger.info(
        "Starting worker on http://%s:%d (viz+logs) + ws://%s:%d/ws (sim) + ws://%s:%d/telemetry",
        host, port, host, port, host, port,
    )

    # Handle graceful shutdown via SIGTERM/SIGINT
    stop = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    async with websockets.serve(
        handle_connection,
        host,
        port,
        process_request=process_request,
        max_size=2**20,        # 1MB max message (world defs can be large)
        ping_interval=20,      # keepalive
        ping_timeout=10,
    ):
        logger.info("Worker listening")
        await stop.wait()

    logger.info("Worker shut down")


# -- Headless mode (testing without WebSocket) -------------------------------

async def run_headless(
    world_config: dict[str, Any],
    num_ticks: int = 100,
    tick_rate: float = DEFAULT_TICK_RATE,
    print_interval: int = 10,
) -> SimulationSession:
    """
    Run a simulation without a WebSocket connection.

    Useful for testing, benchmarking, and the CLI viewer. Returns
    the session object for inspection after completion.
    """
    session = SimulationSession(world_config, tick_rate=tick_rate)
    tick_count = 0

    async def mock_send(data: str) -> None:
        nonlocal tick_count
        tick_count += 1
        if tick_count % print_interval == 0:
            packet = json.loads(data)
            living = len(packet.get("entity_updates", []))
            events = len(packet.get("events", []))
            print(
                f"  tick {packet['tick']:5d}  |  "
                f"entities: {living:3d}  |  "
                f"events: {events:2d}  |  "
                f"voxel deltas: {sum(len(v) for v in packet.get('voxel_deltas', {}).values()):3d}"
            )

    # Override running flag to stop after num_ticks
    original_step = session.step

    def counted_step():
        packet = original_step()
        if session.ticks_completed >= num_ticks:
            session.stop()
        return packet

    session.step = counted_step

    print(f"Running headless: {num_ticks} ticks at {1/tick_rate:.0f}Hz")
    print(f"Entities: {len(session.engine.entities)}, Biome: {session.engine.biome_name}")
    print("-" * 60)

    await session.run_tick_loop(mock_send)

    avg_ms = (
        session.total_step_time / session.ticks_completed * 1000
        if session.ticks_completed > 0
        else 0
    )
    print("-" * 60)
    print(
        f"Complete: {session.ticks_completed} ticks, "
        f"avg step={avg_ms:.2f}ms, "
        f"max throughput={1000/avg_ms:.0f}Hz" if avg_ms > 0 else ""
    )

    return session


# -- Entry point -------------------------------------------------------------

def main() -> None:
    """CLI entry point for the worker."""
    import argparse

    parser = argparse.ArgumentParser(description="līlā Simulation Worker")
    parser.add_argument(
        "--host", default=os.environ.get("WORKER_HOST", DEFAULT_HOST),
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int,
        default=int(os.environ.get("WORKER_PORT", DEFAULT_PORT)),
        help="Bind port (default: 8001)",
    )
    parser.add_argument(
        "--headless", type=str, default=None,
        help="Run headless from a world definition JSON file",
    )
    parser.add_argument(
        "--ticks", type=int, default=200,
        help="Number of ticks in headless mode (default: 200)",
    )
    parser.add_argument(
        "--tick-rate", type=float, default=DEFAULT_TICK_RATE,
        help="Seconds per tick (default: 0.1)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.headless:
        with open(args.headless) as f:
            world_config = json.load(f)
        asyncio.run(run_headless(
            world_config,
            num_ticks=args.ticks,
            tick_rate=args.tick_rate,
        ))
    else:
        asyncio.run(start_server(host=args.host, port=args.port))


if __name__ == "__main__":
    main()
