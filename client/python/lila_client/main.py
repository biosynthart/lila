"""līlā Python Client — Main entry point.

Usage:
    lila-client [--host localhost] [--port 8001] [--world path/to/world.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pygame

logger = logging.getLogger("lila.client")


# ─── CLI Entry Point ──────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point for the līlā debug client."""
    parser = argparse.ArgumentParser(description="līlā Ecosystem Debug Client")
    parser.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    parser.add_argument("--port", type=int, default=8001, help="Server port (default: 8001)")
    parser.add_argument(
        "--world", type=str, default=None,
        help="Path to world definition JSON (auto-loads from server if omitted)",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    client = LilaClient(host=args.host, port=args.port)

    if args.world:
        with open(args.world) as f:
            world_def = json.load(f)
        client.send_world(world_def)
    else:
        logger.info("Will fetch world.json from server on connect")

    client.run()


# ─── Client Application ──────────────────────────────────────────────────────


class LilaClient:
    """Main application class tying together WS, world model, and pygame."""

    def __init__(self, host: str = "localhost", port: int = 8001):
        from .websocket import WebSocketClient
        from .world_model import WorldModel

        self.host = host
        self.port = port

        # Core components
        self.ws = WebSocketClient(host=host, port=port)
        self.world = WorldModel()

        # Session state
        self.session_started = False
        self.current_tick = 0
        self._last_heartbeat_time = time.monotonic()
        self._pending_events: list[dict] = []  # agency events to send upstream
        self._particles: list[dict] = []

        # Pygame surfaces
        self.screen: pygame.Surface | None = None
        self.clock: pygame.time.Clock | None = None

    def send_world(self, world_def: dict) -> None:
        """Send a world definition to start a simulation session."""
        self.ws.send_world_definition(world_def)

    def run(self) -> None:
        """Start the client and enter the pygame render loop."""
        from .constants import CELL_PX, GRID_SIZE, PADDING, HEARTBEAT_INTERVAL_MS
        from .pygame_renderer import draw_all
        from .agency import step_agency
        from .reconciliation import reconcile

        pygame.init()
        pygame.display.set_caption("līlā — ecosystem")

        canvas_size = GRID_SIZE * CELL_PX + PADDING * 2
        self.screen = pygame.display.set_mode((canvas_size, canvas_size))
        self.clock = pygame.time.Clock()

        # Start WebSocket background thread
        self.ws.start()

        last_frame_time = time.monotonic()
        running = True
        while running:
            # ── Handle pygame events ──
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_r:
                        self.ws.send_control("rain", intensity=0.8)

            # ── Process messages from server ──
            self._process_messages()

            # ── Step local agency (60 Hz, between server ticks) ──
            now = time.monotonic()
            dt = min((now - last_frame_time) / 1000, 0.05)  # cap at 50ms
            last_frame_time = now

            client_events = step_agency(self.world, dt)
            self._pending_events.extend(client_events)

            # ── Send heartbeat if interval elapsed ──
            if now - self._last_heartbeat_time >= HEARTBEAT_INTERVAL_MS / 1000:
                self._send_heartbeat()
                self._last_heartbeat_time = now

            # ── Render ──
            draw_all(self.screen, self.world, self._particles)

            # ── Overlay text ──
            font = pygame.font.SysFont("monospace", 11)
            status = "● running" if self.ws.connected else "● connecting..."
            color_green = (100, 200, 100)
            color_red = (200, 100, 100)
            status_surf = font.render(status, True, color_green if self.ws.connected else color_red)
            self.screen.blit(status_surf, (PADDING, 4))

            info_text = f"tick: {self.current_tick}  entities: {len(self.world.entities)}  fps: {self.clock.get_fps():.0f}"
            info_surf = font.render(info_text, True, (180, 175, 165))
            self.screen.blit(info_surf, (canvas_size - info_surf.get_width() - PADDING, 4))

            pygame.display.flip()
            self.clock.tick(60)

        pygame.quit()
        self.ws.stop()

    def _process_messages(self) -> None:
        """Process all pending messages from the WebSocket."""
        while True:
            msg = self.ws.recv_message()
            if msg is None:
                break

            if msg.type == "session_started":
                self.session_started = True
                if "species" in msg.data:
                    self.world.species_defs = msg.data["species"]

            elif msg.type == "tick":
                self._handle_tick_packet(msg.data)

    def _handle_tick_packet(self, packet: dict) -> None:
        """Process a tick packet from the server."""
        from .reconciliation import reconcile

        self.current_tick = packet.get("tick", 0)

        for u in packet.get("entity_updates", []):
            self.world.apply_update(u)
        for s in packet.get("entity_spawns", []):
            self.world.apply_spawn(s)
        for eid in packet.get("entity_removals", []):
            self.world.apply_removal(eid)
        if "voxel_deltas" in packet:
            self.world.apply_voxel_deltas(packet["voxel_deltas"])
        if "water_sources" in packet:
            self.world.apply_water_sources(packet["water_sources"])

        # Reconcile client positions with server references
        reconcile(self.world)

    def _send_heartbeat(self) -> None:
        """Send client heartbeat with entity positions and pending events."""
        positions = {}
        for ent in self.world.entities.values():
            if ent.is_alive and ent.is_mobile_consumer:
                positions[ent.id] = [round(ent.x, 4), 0.0, round(ent.z, 4)]
        if not positions and not self._pending_events:
            return  # nothing to report

        events = self._pending_events
        self._pending_events = []
        self.ws.send_heartbeat(positions=positions, events=events)


if __name__ == "__main__":
    main()
