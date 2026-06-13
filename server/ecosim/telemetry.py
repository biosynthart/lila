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
# conditions of the License.

"""
līlā Telemetry Bus — structured event logging with file writer + WS broadcast.

Stdlib-only. Designed to be optionally injected into the worker without
affecting the zero-dependency core (ecosim engine).

Every event is a JSON line with fixed keys:
    ts        — monotonic timestamp (float)
    tick      — simulation tick number (int or null for client-side events)
    level     — DEBUG | INFO | WARN | ERROR
    src       — origin module: engine|motor|worker|client|telemetry
    evt       — event name (snake_case, e.g. "intent_emit", "guard_fire")
    entity_id — affected entity (str or null)
    detail    — free-form dict with event-specific data

File output: ~/.lila/logs/<session_id>.jsonl  (rotated by size)
WS broadcast: /telemetry endpoint on the worker server
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from collections import deque
from typing import Any

logger = logging.getLogger("lila.telemetry")

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_LOG_DIR = pathlib.Path.home() / ".lila" / "logs"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per log file before rotation
MAX_MEMORY_BUFFER = 5000          # keep last N events in memory for HTTP API

# ── Event Schema ─────────────────────────────────────────────────────────────


def make_event(
    *,
    tick: int | None,
    level: str,
    src: str,
    evt: str,
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured telemetry event."""
    return {
        "ts": time.monotonic(),
        "tick": tick,
        "level": level.upper(),
        "src": src,
        "evt": evt,
        "entity_id": entity_id,
        "detail": detail or {},
    }


# ── Telemetry Bus ────────────────────────────────────────────────────────────


class TelemetryBus:
    """Append-only telemetry bus with file writer and WS broadcast.

    Usage::

        bus = TelemetryBus(session_id="demo-001")
        bus.start()
        bus.emit(tick=42, level="INFO", src="engine", evt="guard_fire",
                 entity_id="deer_01", detail={"from": "FORAGING", "to": "RESTING"})

    The bus runs a background asyncio task for file I/O and WS fan-out.
    Call ``await bus.stop()`` to shut down cleanly.
    """

    def __init__(
        self,
        session_id: str = "unknown",
        log_dir: pathlib.Path | None = None,
        max_file_size: int = MAX_FILE_SIZE,
        max_memory: int = MAX_MEMORY_BUFFER,
    ):
        self.session_id = session_id
        self.log_dir = (log_dir or DEFAULT_LOG_DIR)
        self.max_file_size = max_file_size
        self._max_memory = max_memory

        # In-memory ring buffer for HTTP API queries
        self._buffer: deque[dict[str, Any]] = deque(maxlen=self._max_memory)

        # File writer state
        self._file: Any | None = None
        self._current_file_size: int = 0
        self._log_path: pathlib.Path | None = None

        # WS subscribers (asyncio.WriteTransport + protocol frames)
        self._subscribers: list[TelemetrySubscriber] = []

        # Background task for async I/O
        self._task: asyncio.Task | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background I/O task. Call from running event loop."""
        if self._running:
            return
        self._running = True
        try:
            self._task = asyncio.create_task(self._io_loop())
        except RuntimeError as e:
            logger.warning("No running event loop for telemetry start: %s", e)

    async def stop(self) -> None:
        """Stop the background I/O task and flush remaining events."""
        self._running = False
        # Signal the queue to drain
        await self._queue.put(None)  # sentinel
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass

    # ── Emit API ─────────────────────────────────────────────────────────

    def emit(
        self,
        *,
        tick: int | None = None,
        level: str = "INFO",
        src: str = "worker",
        evt: str,
        entity_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Emit a telemetry event. Non-blocking — pushes to internal queue."""
        event = make_event(
            tick=tick, level=level, src=src, evt=evt,
            entity_id=entity_id, detail=detail,
        )
        self._buffer.append(event)
        if not self._running:
            # If background task isn't running yet (e.g. during init),
            # write synchronously to avoid losing early events.
            self._write_sync(event)
        else:
            try:
                self._queue.put_nowait(("event", event))
            except asyncio.QueueFull:
                logger.warning("Telemetry queue full, dropping event: %s", evt)

    # Convenience methods for common levels
    def debug(self, **kwargs):  self.emit(level="DEBUG", **kwargs)
    def info(self, **kwargs):   self.emit(level="INFO", **kwargs)
    def warn(self, **kwargs):   self.emit(level="WARN", **kwargs)
    def error(self, **kwargs):  self.emit(level="ERROR", **kwargs)

    # ── WS Subscriber Management ─────────────────────────────────────────

    def add_subscriber(self, subscriber: TelemetrySubscriber) -> None:
        """Add a WebSocket subscriber for real-time event streaming."""
        self._subscribers.append(subscriber)

    def remove_subscriber(self, subscriber: TelemetrySubscriber) -> None:
        """Remove a WebSocket subscriber."""
        try:
            self._subscribers.remove(subscriber)
        except ValueError:
            pass

    # ── Query API (for HTTP endpoints) ───────────────────────────────────

    def query(
        self,
        *,
        tick: int | None = None,
        src: str | None = None,
        level: str | None = None,
        evt: str | None = None,
        entity_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query recent events from the in-memory buffer."""
        results = []
        for event in reversed(self._buffer):
            if tick is not None and event.get("tick") != tick:
                continue
            if src is not None and event.get("src") != src:
                continue
            if level is not None and event.get("level") != level.upper():
                continue
            if evt is not None and event.get("evt") != evt:
                continue
            if entity_id is not None and event.get("entity_id") != entity_id:
                continue
            results.append(event)
            if len(results) >= limit:
                break
        return list(reversed(results))

    def stats(self) -> dict[str, Any]:
        """Aggregate statistics from the in-memory buffer."""
        counts: dict[str, int] = {}
        by_src: dict[str, int] = {}
        by_level: dict[str, int] = {}
        for event in self._buffer:
            evt_name = event.get("evt", "unknown")
            counts[evt_name] = counts.get(evt_name, 0) + 1
            src = event.get("src", "unknown")
            by_src[src] = by_src.get(src, 0) + 1
            level = event.get("level", "UNKNOWN")
            by_level[level] = by_level.get(level, 0) + 1

        return {
            "session_id": self.session_id,
            "total_events_buffered": len(self._buffer),
            "by_event_type": counts,
            "by_source": by_src,
            "by_level": by_level,
            "log_file": str(self._log_path) if self._log_path else None,
        }

    # ── Background I/O Loop ──────────────────────────────────────────────

    async def _io_loop(self) -> None:
        """Background task: write events to file and broadcast to WS subscribers."""
        batch: list[dict[str, Any]] = []
        _batch_size = 10  # flush every N events or on timeout

        while self._running:
            try:
                # Wait for events with a short timeout to allow periodic flushes
                item = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except TimeoutError:
                item = None

            if item is None:
                break  # sentinel — shutdown

            action, event = item
            batch.append(event)

            # Flush batch when it reaches size or on timeout path
            if len(batch) >= _batch_size:
                await self._flush_batch(batch)
                batch.clear()

        # Final flush on shutdown
        if batch:
            await self._flush_batch(batch)

    async def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        """Write batch to file and broadcast to WS subscribers."""
        lines = "\n".join(json.dumps(e, default=str) for e in batch) + "\n"

        # File write
        self._write_to_file(lines)

        # WS broadcast (non-blocking best-effort)
        dead: list[TelemetrySubscriber] = []
        for sub in self._subscribers:
            try:
                await asyncio.wait_for(sub.send(lines), timeout=0.1)
            except Exception:
                dead.append(sub)

        for sub in dead:
            self.remove_subscriber(sub)

    # ── File I/O ─────────────────────────────────────────────────────────

    def _write_sync(self, event: dict[str, Any]) -> None:
        """Synchronous write (used when background task isn't running yet)."""
        line = json.dumps(event, default=str) + "\n"
        self._write_to_file(line)

    def _write_to_file(self, data: str) -> None:
        """Append data to the current log file, rotating if needed."""
        # Ensure log directory exists
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Open new file or rotate
        if self._file is None or self._current_file_size >= self.max_file_size:
            self._rotate_file()

        try:
            self._file.write(data)
            self._current_file_size += len(data.encode("utf-8"))
        except OSError as e:
            logger.error("Failed to write telemetry log: %s", e)

    def _rotate_file(self) -> None:
        """Close current file and open a new one (with rotation suffix)."""
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass

        # Find next available filename
        base = self.log_dir / f"{self.session_id}.jsonl"
        counter = 0
        while True:
            path = base.with_stem(f"{base.stem}.{counter}") if counter > 0 else base
            if not path.exists() or counter == 0:
                break
            counter += 1

        self._log_path = path
        try:
            self._file = open(path, "a", encoding="utf-8")
            self._current_file_size = 0
            logger.info("Telemetry log: %s", path)
        except OSError as e:
            logger.error("Failed to open telemetry log file: %s", e)
            self._file = None

    # ── Shutdown ─────────────────────────────────────────────────────────

    def __del__(self) -> None:
        """Ensure file handle is closed."""
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass


# ── WebSocket Subscriber ─────────────────────────────────────────────────────


class TelemetrySubscriber:
    """Wrapper around a WebSocket connection for telemetry streaming.

    Supports URL-based filtering so clients can subscribe to subsets of events.
    """

    def __init__(
        self,
        websocket: Any,  # websockets.WebSocketServerProtocol or similar
        filters: dict[str, str] | None = None,
    ):
        self.websocket = websocket
        self.filters = filters or {}

    def matches(self, event: dict[str, Any]) -> bool:
        """Check if an event passes this subscriber's filters."""
        for key, value in self.filters.items():
            if event.get(key) != value and str(event.get(key)) != value:
                return False
        return True

    async def send(self, data: str) -> None:
        """Send data to the WebSocket (best-effort)."""
        await self.websocket.send(data)


# ── HTTP Handler Helpers ─────────────────────────────────────────────────────


def build_telemetry_query_params(path: str) -> dict[str, str]:
    """Parse query parameters from a /telemetry or /logs request path."""
    params: dict[str, str] = {}
    if "?" not in path:
        return params

    query_string = path.split("?", 1)[1]
    for part in query_string.split("&"):
        if "=" in part:
            key, value = part.split("=", 1)
            import urllib.parse
            params[urllib.parse.unquote(key)] = urllib.parse.unquote(value)

    return params


def build_telemetry_response(
    bus: TelemetryBus,
    path: str,
) -> tuple[int, dict[str, str], bytes]:
    """Build an HTTP response for /logs endpoints.

    Returns (status_code, headers, body).
    """
    params = build_telemetry_query_params(path)

    if path.rstrip("/") == "/logs/stats":
        stats = bus.stats()
        return 200, {"Content-Type": "application/json"}, json.dumps(stats).encode()

    if path.rstrip("/") == "/logs/download":
        session_id = params.get("session", bus.session_id)
        log_path = bus.log_dir / f"{session_id}.jsonl"
        if not log_path.exists():
            return 404, {"Content-Type": "text/plain"}, b"Log file not found"

        data = log_path.read_bytes()
        return (
            200,
            {
                "Content-Type": "application/x-ndjson",
                "Content-Disposition": f'attachment; filename="{session_id}.jsonl"',
            },
            data,
        )

    # Default: /logs with optional filters
    limit = int(params.get("limit", 100))
    tick = params.get("tick")
    src = params.get("src")
    level = params.get("level")
    evt = params.get("evt")
    entity_id = params.get("entity_id")

    events = bus.query(
        tick=int(tick) if tick else None,
        src=src,
        level=level,
        evt=evt,
        entity_id=entity_id,
        limit=min(limit, 1000),
    )

    return (
        200,
        {"Content-Type": "application/x-ndjson"},
        "\n".join(json.dumps(e) for e in events).encode(),
    )


# ── Telemetry-aware tick packet wrapper ──────────────────────────────────────


def wrap_tick_packet(
    bus: TelemetryBus,
    packet: dict[str, Any],
    session_id: str = "unknown",
) -> dict[str, Any]:
    """Log key events from a tick packet and return the packet unchanged.

    Call this right after engine.step() to capture intent_emit telemetry
    without modifying the engine core.
    """
    tick = packet.get("tick")

    # Log entity state changes (guard firings)
    for ev in packet.get("events", []):
        bus.emit(
            tick=tick,
            level="INFO" if ev.get("type") not in ("DEATH_NATURAL", "DEATH_STARVE") else "WARN",
            src="engine",
            evt=f"event_{ev.get('type', 'unknown').lower()}",
            entity_id=ev.get("source_id"),
            detail={
                "target_id": ev.get("target_id"),
                "position": ev.get("position"),
                **({k: v for k, v in ev.items() if k not in ("type", "source_id", "target_id", "position")}),
            },
        )

    # Log spawns and removals
    for spawn in packet.get("entity_spawns", []):
        bus.emit(
            tick=tick, level="INFO", src="engine", evt="spawn",
            entity_id=spawn["id"],
            detail={"type": spawn.get("type"), "species": spawn.get("species")},
        )

    for eid in packet.get("entity_removals", []):
        bus.emit(
            tick=tick, level="WARN", src="engine", evt="removal",
            entity_id=eid,
        )

    # Log intent summary (drives + eligibility) per entity
    for update in packet.get("entity_updates", []):
        eid = update["id"]
        drive = update.get("drive", {})
        bus.emit(
            tick=tick, level="DEBUG", src="engine", evt="intent_emit",
            entity_id=eid,
            detail={
                "state": update.get("state"),
                "ref_position": update.get("ref_position"),
                "drive": drive,
                "can_consume": update.get("_can_consume", False),
                "can_predate": update.get("_can_predate", False),
                "can_pollinate": update.get("_can_pollinate", False),
                "repro_eligible": update.get("_repro_eligible", False),
                "ack": update.get("_ack", False),
            },
        )

    return packet


# ── Absorption telemetry wrapper ─────────────────────────────────────────────


def log_absorption(
    bus: TelemetryBus,
    tick: int,
    positions: dict[str, list[float]],
    events: list[dict[str, Any]],
) -> None:
    """Log client absorption details for coherence debugging.

    Call this from the worker after absorb_heartbeat() to capture what
    the server received and how it was handled.
    """
    # Log position absorptions with divergence info
    for eid, pos in positions.items():
        bus.emit(
            tick=tick, level="DEBUG", src="worker", evt="position_absorbed",
            entity_id=eid,
            detail={"reported_position": pos},
        )

    # Log event absorptions
    for ev in events:
        etype = ev.get("type", "unknown")
        bus.emit(
            tick=tick, level="INFO", src="worker", evt=f"client_event_{etype.lower()}",
            entity_id=ev.get("source_id"),
            detail={
                "target_id": ev.get("target_id"),
                "parent_id": ev.get("parent_id"),
                "position": ev.get("position"),
            },
        )
