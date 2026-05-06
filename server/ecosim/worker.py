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

logger = logging.getLogger("lila.worker")

# -- Configuration -----------------------------------------------------------

DEFAULT_TICK_RATE = 0.1     # seconds between ticks (10 Hz)
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8001
MAX_TICK_DRIFT = 0.05       # max acceptable drift before skipping sleep


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

        # Stats
        self.ticks_completed = 0
        self.total_step_time = 0.0

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

    def step(self) -> dict[str, Any]:
        """Run a single tick and return the packet."""
        t0 = time.monotonic()
        packet = self.engine.step(self.tick_rate)
        elapsed = time.monotonic() - t0

        self.ticks_completed += 1
        self.total_step_time += elapsed

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
                    packet["session_id"] = self.world_config.get(
                        "session_id", "unknown"
                    )

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

async def handle_connection(websocket) -> None:
    """
    Handle a single WebSocket connection through its full lifecycle:
    receive world def → run simulation → clean shutdown.
    """
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

    # Step 2: Send acknowledgement
    ack = json.dumps({
        "type": "session_started",
        "session_id": session_id,
        "tick_rate": DEFAULT_TICK_RATE,
        "entity_count": len(world_config.get("entities", [])),
    })
    await websocket.send(ack)

    # Step 3: Initialize session and run
    session = SimulationSession(world_config)

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

        return Response(404, "Not Found", Headers(), b"Not found")

    logger.info(
        "Starting worker on http://%s:%d (viz) + ws://%s:%d/ws (simulation)",
        host, port, host, port,
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
