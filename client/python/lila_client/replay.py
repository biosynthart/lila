"""līlā Python Client — Replay mode for post-mortem analysis.

Reads a session's JSONL telemetry log and replays events in the viewer,
allowing you to scrub through time and inspect what happened at each tick.

Usage:
    lila-client-replay ~/.lila/logs/demo-alpha-001.jsonl [--speed 2.0]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import dearpygui.dearpygui as dpg
from .imgui_view import TelemetryBuffer

logger = logging.getLogger("lila.client.replay")


class ReplaySession:
    """Loads and replays a JSONL telemetry log file."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.events: list[dict] = []
        self.session_id: Optional[str] = None
        self._load()

    def _load(self) -> None:
        logger.info("Loading replay from %s", self.log_path)
        with open(self.log_path) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    self.events.append(event)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON on line %d", line_num)
        logger.info("Loaded %d events", len(self.events))

    @property
    def tick_range(self) -> tuple[int, int]:
        ticks = [e.get("tick") for e in self.events if e.get("tick") is not None]
        return (min(ticks) if ticks else 0, max(ticks) if ticks else 100)

    def get_events_up_to(self, tick: int) -> list[dict]:
        return [e for e in self.events if e.get("tick") is None or e["tick"] <= tick]


def run_replay(log_path: Path, speed: float = 1.0) -> None:
    """Run a replay session in the Dear PyGui viewer."""
    session = ReplaySession(log_path)
    telemetry = TelemetryBuffer()

    replay_tick = [session.tick_range[0]]
    max_tick = max(session.tick_range[1], 1)
    min_tick = session.tick_range[0]
    playing = [True]
    event_index = [0]
    last_frame = [time.monotonic()]

    def build_ui():
        dpg.create_context()
        dpg.create_viewport(title=f"līlā — replay: {log_path.name}", width=1100, height=700)
        dpg.setup_dearpygui()

        with dpg.window(label="Replay", tag="replay_window", width=1100):
            # Controls
            with dpg.group(horizontal=True):
                dpg.add_button(label="⏸ Pause", tag="btn_play",
                               callback=lambda: playing.__setitem__(0, not playing[0]))
                dpg.add_button(label="⟲ Reset", tag="btn_reset",
                               callback=lambda: _reset())
                dpg.add_spacer(width=10)
                dpg.add_slider_int(label="##scrubber", tag="scrubber",
                                   default_value=min_tick, min_value=min_tick,
                                   max_value=max_tick, width=400,
                                   callback=lambda s, a: _scrub_to(a))
                dpg.add_text(tag="tick_display")

            dpg.add_separator()

            # Telemetry timeline
            dpg.add_text("Events:")
            with dpg.group(horizontal=True, tag="lvl_filters"):
                for level in ("DEBUG", "INFO", "WARN", "ERROR"):
                    dpg.add_checkbox(label=level, default_value=True, tag=f"lvl_{level}")

            dpg.add_separator()
            with dpg.child_window(tag="telemetry_child", border=False):
                dpg.add_listbox(tag="telemetry_list")

    def _reset():
        replay_tick[0] = min_tick
        event_index[0] = 0
        rebuild_events()

    def _scrub_to(tick_val):
        replay_tick[0] = tick_val
        rebuild_events()

    def rebuild_events():
        events = session.get_events_up_to(replay_tick[0])
        telemetry._events.clear()
        telemetry._events.extend(events)
        event_index[0] = len(events)

    def render_callback():
        now = time.monotonic()
        dt = min(now - last_frame[0], 0.05)
        last_frame[0] = now

        # Play/pause button label
        dpg.set_value("btn_play", "▶ Play" if playing[0] else "⏸ Pause")

        # Advance tick
        if playing[0]:
            replay_tick[0] += int(dt * speed * 0.5)
            if replay_tick[0] > max_tick:
                replay_tick[0] = max_tick
                playing[0] = False

            target_events = session.get_events_up_to(replay_tick[0])
            for event in target_events[event_index[0]:]:
                telemetry.add(event)
            event_index[0] = len(target_events)

        # Update slider and display
        dpg.set_value("scrubber", replay_tick[0])
        dpg.set_value("tick_display", f"Tick: {replay_tick[0]} / {max_tick}  |  Events: {len(telemetry.events)}")

        # Update telemetry list
        lines = []
        for event in telemetry.filtered_events()[:100]:
            level = event.get("level", "INFO")
            tick = f"T{event['tick']}" if event.get("tick") is not None else "T—"
            eid = f" {event['entity_id']}" if event.get("entity_id") else ""
            lines.append(f"[{tick}] [{level:5s}] {event.get('src','?'):8s} {event.get('evt','?')}{eid}")
        dpg.configure_item("telemetry_list", items=lines)

    build_ui()
    dpg.show_viewport()
    dpg.set_frame_callback(-1, lambda s, a: render_callback())
    dpg.start_dearpygui()
    dpg.destroy_context()


def main() -> None:
    """CLI entry point for replay mode."""
    parser = argparse.ArgumentParser(description="līlā Replay — post-mortem analysis")
    parser.add_argument("log_file", type=Path, help="Path to JSONL telemetry log file")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()

    if not args.log_file.exists():
        print(f"Error: log file not found: {args.log_file}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    run_replay(args.log_file, speed=args.speed)


if __name__ == "__main__":
    main()
