"""līlā Python Client — Constants and Configuration."""

import pathlib

# ─── Connection ──────────────────────────────────────────────────────────────

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8001
WS_PATH = "/ws"
TELEMETRY_PATH = "/telemetry"
RECONNECT_DELAY = 3.0       # seconds between reconnection attempts
HEARTBEAT_INTERVAL_MS = 1000  # ms between client heartbeat sends

# ─── Grid / World ────────────────────────────────────────────────────────────

GRID_SIZE = 32
CELL_PX = 18
PADDING = 40
CANVAS_SIZE = GRID_SIZE * CELL_PX + PADDING * 2

# Server tick rate (seconds) — intent-based mode at 0.5 Hz
SERVER_TICK_RATE = 2.0

# Reconciliation thresholds
RECONCILE_SNAP_THRESHOLD_MULT = 3.0
RECONCILE_NUDGE_FACTOR = 0.3

# ─── Colors (matching browser visualizer) ────────────────────────────────────

COLORS = {
    "bg":          (15, 16, 15),
    "grid":        (184, 180, 168, 10),
    "gridMajor":   (184, 180, 168, 20),

    # Moisture heatmap
    "moistureHigh": (48, 58, 52),
    "moistureMid":  (42, 44, 38),
    "moistureLow":  (72, 62, 42),

    # Entities
    "deer":        (196, 149, 106),
    "bird":        (138, 123, 107),
    "butterfly":   (168, 124, 196),
    "oak":         (61, 107, 61),
    "grass":       (107, 143, 94),
    "wildflower":  (196, 166, 74),

    # Events
    "consumption": (143, 170, 110),
    "pollination": (196, 166, 74),
    "death":       (110, 90, 90),

    # Water
    "waterFill":   (45, 85, 110),
    "waterEdge":   (55, 105, 125),
    "waterShine":  (70, 130, 150),

    # Telemetry levels
    "level_DEBUG": (128, 128, 128),
    "level_INFO":  (140, 180, 140),
    "level_WARN":  (200, 170, 60),
    "level_ERROR": (200, 80, 80),
}

# Entity type → color key mapping
ENTITY_COLOR_MAP = {
    ("ANIMAL", "deer"):       "deer",
    ("BIRD", "songbird"):     "bird",
    ("INSECT", "monarch"):    "butterfly",
    ("TREE", "meadow_oak"):   "oak",
    ("PLANT", "meadow_grass"): "grass",
    ("PLANT", "wildflower"):  "wildflower",
}

# ─── Telemetry ────────────────────────────────────────────────────────────────

MAX_TELEMETRY_BUFFER = 5000   # max events kept in memory
TELEMETRY_LOG_DIR = pathlib.Path.home() / ".lila" / "logs"
