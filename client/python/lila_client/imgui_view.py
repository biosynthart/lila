"""līlā Python Client — Dear PyGui debug viewer."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Optional

import dearpygui.dearpygui as dpg
from .constants import COLORS, MAX_TELEMETRY_BUFFER, ENTITY_COLOR_MAP, GRID_SIZE, CELL_PX, PADDING
from .world_model import WorldEntity


class TelemetryBuffer:
    """In-memory telemetry event buffer with filtering."""

    def __init__(self, max_size: int = MAX_TELEMETRY_BUFFER):
        self._events: deque[dict] = deque(maxlen=max_size)
        self.filter_src: set[str] = set()
        self.filter_level: set[str] = {"DEBUG", "INFO", "WARN", "ERROR"}
        self.filter_evt: set[str] = set()

    def add(self, event: dict) -> None:
        self._events.append(event)
        if not self.filter_src:
            self.filter_src.add(event.get("src", "unknown"))
        if not self.filter_evt:
            self.filter_evt.add(event.get("evt", "unknown"))

    @property
    def events(self):
        return self._events

    def filtered_events(self) -> list[dict]:
        results = []
        for event in self._events:
            if self.filter_src and event.get("src") not in self.filter_src:
                continue
            if event.get("level") not in self.filter_level:
                continue
            if self.filter_evt and event.get("evt") not in self.filter_evt:
                continue
            results.append(event)
        return list(reversed(results))

    @property
    def unique_sources(self) -> set[str]:
        return {e.get("src", "unknown") for e in self._events}

    @property
    def unique_events(self) -> set[str]:
        return {e.get("evt", "unknown") for e in self._events}


class DearPyGuiViewer:
    """Dear PyGui viewer for the līlā debug client."""

    def __init__(self, world):
        self.world = world
        self.telemetry = TelemetryBuffer()
        self.selected_entity_id: Optional[str] = None
        self._frame_count = 0
        self._fps_time = time.monotonic()
        self.display_fps = 0

    def build(self) -> None:
        """Build the Dear PyGui UI (called once at startup)."""
        # Theme colors

        # ── Main window ──────────────────────────────────────────────
        with dpg.window(label="Main", tag="main_window", width=1200, height=800, no_collapse=True):

            # Status bar (top)
            with dpg.child_window(tag="status_bar", height=30, border=False, menubar=False):
                dpg.add_text("● running", tag="status_text", color=(100, 200, 100))
                dpg.add_text("līlā debug client", color=(180, 175, 165))
                dpg.add_spacer(width=100)
                dpg.add_text(tag="stat_entities")
                dpg.add_spacer(width=10)
                dpg.add_text(tag="stat_telemetry")
                dpg.add_spacer(width=10)
                dpg.add_text(tag="stat_fps")

            dpg.add_separator()

            # ── Left: World view ──────────────────────────────────────
            with dpg.child_window(tag="world_panel", width=-320, border=True):
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="⏸ Pause", tag="btn_pause",
                        callback=lambda: self._on_pause(),
                    )
                    dpg.add_button(
                        label="☔ Rain", tag="btn_rain",
                        callback=lambda: self._on_rain(),
                    )

                # Scene canvas (drawlist)
                canvas_size = GRID_SIZE * CELL_PX + PADDING * 2
                with dpg.child_window(tag="scene_panel", height=canvas_size + 20, border=False, no_scrollbar=True):
                    dpg.add_drawlist(
                        width=canvas_size, height=canvas_size, tag="scene_drawlist",
                    )

                dpg.add_separator()
                dpg.add_text("Entities:", tag="entity_header")
                with dpg.child_window(tag="entity_child", border=False):
                    dpg.add_listbox(tag="entity_list", callback=self._on_entity_select)

            # ── Right: Telemetry ──────────────────────────────────────
            with dpg.child_window(tag="telemetry_panel", width=310, height=-150, border=True):
                dpg.add_text("Telemetry", bullet=True)

                # Source filters
                dpg.add_text("Sources:")
                with dpg.group(tag="src_filters", horizontal=True):
                    pass  # checkboxes added dynamically

                dpg.add_separator()

                # Level filters
                dpg.add_text("Levels:")
                with dpg.group(tag="lvl_filters", horizontal=True):
                    for level in ("DEBUG", "INFO", "WARN", "ERROR"):
                        dpg.add_checkbox(label=level, default_value=True, tag=f"lvl_{level}", callback=self._on_level_filter)

                dpg.add_separator()

                # Event timeline
                with dpg.child_window(tag="telemetry_child", border=False):
                    dpg.add_listbox(tag="telemetry_list",
                                    callback=self._on_telemetry_select)

            # ── Bottom: Entity Inspector ──────────────────────────────
            with dpg.window(tag="inspector_window", label="Entity Inspector",
                            pos=(620, 400), width=560, height=350,
                            no_collapse=True, collapsed=True):
                dpg.add_text(tag="inspector_content")

    # ── Scene Rendering ──────────────────────────────────────────────

    def _render_scene(self) -> None:
        """Draw the world scene on the drawlist (called each frame from render())."""
        with dpg.drawlist("scene_drawlist") as drawlist:

            def world_to_canvas(wx, wz):
                return (PADDING + wx * CELL_PX, PADDING + wz * CELL_PX)

            # ── Moisture heatmap ──
            moisture = self.world.moisture if hasattr(self.world, 'moisture') else None
            if moisture and len(moisture) == GRID_SIZE * GRID_SIZE:
                for z in range(GRID_SIZE):
                    for x in range(GRID_SIZE):
                        val = moisture[z * GRID_SIZE + x]
                        cx, cz = world_to_canvas(x, z)
                        if val > 0.5:
                            f = (val - 0.5) * 2
                            r = int(COLORS["moistureMid"][0] + (COLORS["moistureHigh"][0] - COLORS["moistureMid"][0]) * f)
                            g = int(COLORS["moistureMid"][1] + (COLORS["moistureHigh"][1] - COLORS["moistureMid"][1]) * f)
                            b = int(COLORS["moistureMid"][2] + (COLORS["moistureHigh"][2] - COLORS["moistureMid"][2]) * f)
                        else:
                            f = val * 2
                            r = int(COLORS["moistureLow"][0] + (COLORS["moistureMid"][0] - COLORS["moistureLow"][0]) * f)
                            g = int(COLORS["moistureLow"][1] + (COLORS["moistureMid"][1] - COLORS["moistureLow"][1]) * f)
                            b = int(COLORS["moistureLow"][2] + (COLORS["moistureMid"][2] - COLORS["moistureLow"][2]) * f)
                        dpg.draw_rectangle(
                            (cx, cz), (cx + CELL_PX, cz + CELL_PX),
                            color=(r, g, b, 255), fill=(r, g, b, 255),
                            tag=f"mc_{x}_{z}", parent=drawlist,
                        )

            # ── Grid lines ──
            for i in range(GRID_SIZE + 1):
                major = i % 8 == 0
                color = (184, 180, 168, 40) if major else (184, 180, 168, 15)
                thickness = 1.0 if major else 0.5
                p = PADDING + i * CELL_PX
                dpg.draw_line(
                    (p, PADDING), (p, PADDING + GRID_SIZE * CELL_PX),
                    color=color, thickness=thickness, tag=f"gv_{i}", parent=drawlist,
                )
                dpg.draw_line(
                    (PADDING, p), (PADDING + GRID_SIZE * CELL_PX, p),
                    color=color, thickness=thickness, tag=f"gh_{i}", parent=drawlist,
                )

            # ── Water sources ──
            for ws in getattr(self.world, 'water_sources', []) or []:
                wx, _, wz = ws.get("position", [0, 0, 0])
                cx, cz = world_to_canvas(wx, wz)
                radius = ws.get("radius", 1.0) * CELL_PX
                level = ws.get("water_level", 1.0)
                if level < 0.02 or radius < 1:
                    continue
                alpha = int((0.3 + level * 0.4) * 255)
                dpg.draw_circle(
                    (cx + CELL_PX / 2, cz + CELL_PX / 2), radius,
                    color=(*COLORS["waterFill"], alpha), fill=(*COLORS["waterFill"], alpha),
                    tag=f"wf_{wx}_{wz}", parent=drawlist,
                )
                dpg.draw_circle(
                    (cx + CELL_PX / 2, cz + CELL_PX / 2), radius,
                    color=(*COLORS["waterEdge"], int(alpha * 0.5)),
                    thickness=1.0, tag=f"we_{wx}_{wz}", parent=drawlist,
                )

            # ── Entities (layered by type) ──
            layer_order = ["TREE", "PLANT", "MICROORGANISM", "ANIMAL", "BIRD", "INSECT"]
            for etype in layer_order:
                for ent in self.world.entities.values():
                    if not ent.is_alive and ent.state != "DORMANT":
                        continue
                    if ent.type != etype:
                        continue
                    if math.isnan(ent.x) or math.isnan(ent.z):
                        continue
                    cx, cz = world_to_canvas(ent.x, ent.z)
                    color_key = ENTITY_COLOR_MAP.get((ent.type, ent.species), "deer")
                    color_rgb = COLORS.get(color_key, (150, 150, 150))

                    if ent.type == "TREE":
                        r = 4.0 * CELL_PX * 0.5 * (ent.drive.get("growth", 0.5) if ent.drive else 0.5)
                        r = max(r, 4)
                        dpg.draw_circle((cx, cz), r, color=(*color_rgb, 80), fill=(*color_rgb, 80), tag=f"tc_{ent.id}", parent=drawlist)
                        dpg.draw_circle((cx, cz), max(3, r * 0.6), color=(*color_rgb, 255), fill=(*color_rgb, 255), tag=f"tb_{ent.id}", parent=drawlist)
                    elif ent.type == "ANIMAL":
                        r = 5 if ent.state == "RESTING" else 7
                        dpg.draw_circle((cx, cz), r, color=(*color_rgb, 200), fill=(*color_rgb, 200), tag=f"a_{ent.id}", parent=drawlist)
                        dpg.draw_circle((cx, cz), 2, color=(*color_rgb, 255), fill=(*color_rgb, 255), tag=f"ah_{ent.id}", parent=drawlist)
                    elif ent.type == "BIRD":
                        dpg.draw_circle((cx, cz), 5, color=(*color_rgb, 200), fill=(*color_rgb, 200), tag=f"b_{ent.id}", parent=drawlist)
                    elif ent.type == "INSECT":
                        dpg.draw_circle((cx, cz), 4, color=(*color_rgb, 180), fill=(*color_rgb, 180), tag=f"i_{ent.id}", parent=drawlist)
                    elif ent.type == "PLANT":
                        r = 2 + (ent.drive.get("growth", 0.1) if ent.drive else 0.1) * 4
                        dpg.draw_circle((cx, cz), max(1, r), color=(*color_rgb, 180), fill=(*color_rgb, 180), tag=f"p_{ent.id}", parent=drawlist)
                    elif ent.type == "MICROORGANISM":
                        r = 2 + (ent.drive.get("activity", 0.5) if ent.drive else 0.5) * 3
                        dpg.draw_circle((cx, cz), max(1, r), color=(*color_rgb, 120), fill=(*color_rgb, 120), tag=f"mg_{ent.id}", parent=drawlist)

    # ── Frame Rendering ──────────────────────────────────────────────

    def render(self) -> None:
        """Update UI each frame (called by render callback)."""
        self._frame_count += 1
        now = time.monotonic()
        if now - self._fps_time >= 1.0:
            self.display_fps = self._frame_count
            self._frame_count = 0
            self._fps_time = now

        # ── Update status bar ────────────────────────────────────────
        paused = dpg.get_value("btn_pause") == "⏸ Pause"
        status_color = (200, 100, 100) if paused else (100, 200, 100)
        dpg.set_value("status_text", f"{'● paused' if paused else '● running'}")
        dpg.configure_item("status_text", color=status_color)

        dpg.set_value("stat_entities", f"entities: {len(self.world.entities)}")
        dpg.set_value("stat_telemetry", f"telemetry: {len(self.telemetry.events)}")
        dpg.set_value("stat_fps", f"fps: {self.display_fps}")

        # ── Update entity list ───────────────────────────────────────
        entities = sorted(self.world.entities.values(), key=lambda e: (e.type, e.id))
        items = []
        for ent in entities[:80]:
            color_key = ENTITY_COLOR_MAP.get((ent.type, ent.species), "deer")
            color = COLORS.get(color_key, (150, 150, 150))
            items.append((f"{ent.id}  [{ent.state}]", ent.id, color))

        dpg.configure_item("entity_list", items=[i[0] for i in items])
        # Store color info for highlighting
        self._entity_items = items

        # Highlight selected
        if self.selected_entity_id:
            idx = next((i for i, it in enumerate(items) if it[1] == self.selected_entity_id), -1)
            dpg.set_item_focus_request("entity_list")
            if idx >= 0:
                dpg.set_value("entity_list", idx)

        # ── Update telemetry list ────────────────────────────────────
        events = self.telemetry.filtered_events()[:100]
        lines = []
        for event in events:
            level = event.get("level", "INFO")
            tick = f"T{event['tick']}" if event.get("tick") is not None else "T—"
            eid = f" {event['entity_id']}" if event.get("entity_id") else ""
            lines.append(f"[{tick}] [{level:5s}] {event.get('src','?'):8s} {event.get('evt','?')}{eid}")

        dpg.configure_item("telemetry_list", items=lines)
        self._telemetry_lines = list(zip(lines, events))

        # ── Update source filters ────────────────────────────────────
        current_sources = self.telemetry.unique_sources
        existing_tags = {t for t in dpg.get_item_children("src_filters")}
        for src in current_sources - {t.replace("src_", "") for t in existing_tags}:
            dpg.add_checkbox(
                label=src, tag=f"src_{src}", default_value=True,
                parent="src_filters", callback=self._on_src_filter,
            )

        # ── Update entity inspector ──────────────────────────────────
        if self.selected_entity_id:
            ent = self.world.entities.get(self.selected_entity_id)
            if ent:
                dpg.configure_item("inspector_window", collapsed=False,
                                   label=f"Entity: {ent.id}")
                self._build_inspector(ent)

    # ── Callbacks ──────────────────────────────────────────────────────

    def _on_pause(self, sender, app_data):
        dpg.set_value(sender, "▶ Resume" if dpg.get_value(sender) == "⏸ Pause" else "⏸ Pause")

    def _on_rain(self, sender, app_data):
        # Signal rain control to send via WS
        pass  # Handled via viewer.rain_triggered

    def _on_entity_select(self, sender, app_data):
        idx = app_data if isinstance(app_data, int) else (app_data[0] if app_data else -1)
        if 0 <= idx < len(self._entity_items):
            self.selected_entity_id = self._entity_items[idx][1]

    def _on_telemetry_select(self, sender, app_data):
        idx = app_data if isinstance(app_data, int) else (app_data[0] if app_data else -1)
        if 0 <= idx < len(self._telemetry_lines):
            event = self._telemetry_lines[idx][1]
            if event.get("entity_id"):
                self.selected_entity_id = event["entity_id"]

    def _on_level_filter(self, sender, app_data):
        level = sender.replace("lvl_", "")
        if app_data:
            self.telemetry.filter_level.add(level)
        else:
            self.telemetry.filter_level.discard(level)

    def _on_src_filter(self, sender, app_data):
        src = sender.replace("src_", "")
        if app_data:
            self.telemetry.filter_src.add(src)
        else:
            self.telemetry.filter_src.discard(src)
        if not self.telemetry.filter_src:
            self.telemetry.filter_src = set()

    def _build_inspector(self, ent: WorldEntity) -> None:
        """Build/update the entity inspector panel."""
        with dpg.item_handler_registry(tag="inspector_registry"):
            pass

        # Clear and rebuild
        dpg.delete_item("inspector_content", children_only=True)

        with dpg.group(tag="inspector_content"):
            dpg.add_text(f"ID: {ent.id}")
            dpg.add_text(f"Type: {ent.type}  Species: {ent.species}")
            dpg.add_text(f"State: {ent.state}")
            dpg.add_separator()

            dpg.add_text("Position:", bullet=True)
            dpg.add_text(f"  Server ref: ({ent.ref_x:.2f}, {ent.ref_z:.2f})")
            dpg.add_text(f"  Client pos: ({ent.x:.2f}, {ent.z:.2f})")
            dx = ent.x - ent.ref_x
            dz = ent.z - ent.ref_z
            div = math.sqrt(dx*dx + dz*dz)
            dpg.add_text(f"  Divergence: {div:.3f} units")
            dpg.add_separator()

            if ent.drive:
                dpg.add_text("Drives:", bullet=True)
                for key, value in ent.drive.items():
                    dpg.add_text(f"  {key}: {value:.4f}")
                dpg.add_separator()

            dpg.add_text("Eligibility:", bullet=True)
            for flag in ("can_consume", "can_predate", "can_pollinate",
                         "repro_eligible", "can_drink", "spread_eligible"):
                val = getattr(ent, flag, False)
                sym = "✓" if val else "✗"
                col = (100, 200, 100) if val else (100, 100, 100)
                dpg.add_text(f"  {sym} {flag}", color=col)

            if ent.motion_latent:
                dpg.add_separator()
                dpg.add_text("Motion Latent:", bullet=True)
                dpg.add_text(f"  [{', '.join(f'{v:.3f}' for v in ent.motion_latent)}]")
