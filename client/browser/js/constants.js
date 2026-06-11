// ═══════════════════════════════════════════════════════
// līlā — Constants and Configuration
// ═══════════════════════════════════════════════════════

export const GRID_SIZE = 32;
export const CELL_PX = 18;
export const PADDING = 40;
export const CANVAS_SIZE = GRID_SIZE * CELL_PX + PADDING * 2;

// Server tick rate (seconds) — intent-based mode at 0.5 Hz
export const SERVER_TICK_RATE = 2.0;

// Heartbeat interval (ms) — how often client sends positions/events upstream
export const HEARTBEAT_INTERVAL_MS = 1000;

// Reconciliation thresholds
export const RECONCILE_SNAP_THRESHOLD_MULT = 3.0; // snap if > 3× expected travel
export const RECONCILE_NUDGE_FACTOR = 0.3;        // soft nudge absorbs 30% per heartbeat

// Max event log entries
export const MAX_EVENT_LOG = 8;

// ─── Colors ───────────────────────────────────────────
export const COLORS = {
  bg:          '#0f100f',
  grid:        'rgba(184, 180, 168, 0.04)',
  gridMajor:   'rgba(184, 180, 168, 0.08)',

  // Moisture heatmap
  moistureHigh: [48, 58, 52],
  moistureMid:  [42, 44, 38],
  moistureLow:  [72, 62, 42],

  // Entities
  deer:        '#c4956a',
  deerHead:    '#d4aa7a',
  bird:        '#8a7b6b',
  birdTail:    '#6b5e52',
  birdSong:    'rgba(196, 170, 120, ',   // prefix for alpha
  butterfly:   '#a87cc4',
  butterflyBody: '#7a5a8f',
  oak:         '#3d6b3d',
  oakCanopy:   'rgba(61, 107, 61, 0.12)',
  grass:       '#6b8f5e',
  grassWilt:   '#7a7254',
  wildflower:  '#7a8f5e',
  flowerBloom: '#c4a64a',

  // Events
  consumption: '#8faa6e',
  pollination: '#c4a64a',
  death:       '#6e5a5a',

  // Water
  waterFill:   [45, 85, 110],
  waterEdge:   [55, 105, 125],
  waterShine:  [70, 130, 150],

  // Text
  label:       'rgba(184, 180, 168, 0.35)',
};
