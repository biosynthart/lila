# līlā — Python Debug Client

ImGui-based debug client for the [līlā](../../README.md) ecosystem simulation engine. Connects to a running worker via WebSocket, visualizes world state in real time, and captures structured telemetry for post-mortem analysis.

## Quick Start

```bash
# 1. Install dependencies (uv required)
cd client/python
uv sync --frozen

# 2. Run the server (in another terminal)
cd ../../server && uv run python -m ecosim.worker --port 8001

# 3. Launch the debug client
uv run lila-client --host localhost --port 8001
```

## Usage

### Live Debug Session

Connect to a running worker and watch telemetry stream in real time:

```bash
uv run lila-client --host localhost --port 8001 [--world path/to/world.json]
```

If `--world` is omitted, the client attempts to fetch `/world.json` from the server.

### Replay (Post-Mortem)

Replay a recorded session's telemetry log with a time scrubber:

```bash
uv run lila-replay ~/.lila/logs/demo-alpha-001.jsonl --speed 2x
```

## Architecture

```
┌──────────────┐     WebSocket      ┌─────────────────┐
│  ImGui Loop   │ ◄────────────────► │  Simulation     │
│  (main thread)│                    │  Worker         │
│              │     /telemetry WS   │  :8001          │
│  ┌──────────┐│                      │                 │
│  │ World    ││                      │  Telemetry Bus  │
│  │ Model    ││                      │  (JSONL file)   │
│  └──────────┘│                      └─────────────────┘
│              │
│  ┌──────────┐│
│  │Telemetry ││
│  │Timeline  ││
│  └──────────┘│
│              │
│  ┌──────────┐│
│  │Entity    ││
│  │Inspector ││
│  └──────────┘│
└──────────────┘
```

### Key Modules

| Module | Purpose |
|--------|---------|
| `websocket.py` | Async WS client in background thread. Bridges asyncio ↔ ImGui via queues. Handles `/ws` (simulation) and `/telemetry` (event stream). |
| `world_model.py` | Local scene graph mirroring the server's entity state. Spatial queries for agency logic. |
| `imgui_view.py` | ImGui render pass: world view, telemetry timeline with filters, entity inspector panel. |
| `replay.py` | Loads JSONL logs and replays events with a tick scrubber for post-mortem analysis. |

### Telemetry Integration

The server's embedded [telemetry bus](../../server/ecosim/telemetry.py) streams structured events to the client in real time:

- **Intent emit** — drives, eligibility flags, reference positions per entity per tick
- **Event log** — consumption, death, reproduction, state transitions with causal context
- **Absorption trace** — what the server received from client heartbeats and how it was handled

All events are written to `~/.lila/logs/<session_id>.jsonl` for offline replay.

## Dependencies

| Package | Purpose |
|---------|---------|
| [dearpygui](https://github.com/hoffstadt/DearPyGui) | High-level Python ImGui binding (simpler API, no boilerplate) |
| [websockets](https://websockets.readthedocs.io/) | Async WebSocket client for simulation + telemetry streams |
| [numpy](https://numpy.org/) | Moisture heatmap rendering, spatial math |

## Development

```bash
# Install in editable mode with dev dependencies
uv sync --frozen

# Run directly
uv run python -m lila_client.main --host localhost --port 8001
```
