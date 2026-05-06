<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā — CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

līlā is a BYOM (Bring Your Own Model) ecosystem simulation engine. Users define
a world in JSON; the engine grows an autonomous ecosystem from simple rules.
The server runs the hybrid automaton; clients render via WebSocket at 10 Hz.

## Commands

```bash
# Dev setup
uv sync                          # install all deps (requires uv)
uv sync --extra worker           # include WebSocket worker deps

# Run
uv run python -m ecosim.worker   # start worker (HTTP + WS on single port)
docker compose up --build        # preferred: clone + run in one step

# Test & lint
uv run pytest server/tests/      # 12 unit tests + smoke test
uv run ruff check server/        # lint (CI runs both on 3.11 + 3.12)
```

## Architecture

```
Browser / Godot Client
    ↓ WebSocket (delta-encoded tick packets, 10 Hz)
Worker  (HTTP + WS, single port — serves viz HTML + streams ticks)
    ↓
ecosim  (Python package, stdlib only — zero external deps in core)
    ├── EcosystemEngine   engine.py     hybrid automaton, 7-phase tick loop
    ├── VoxelManager      voxel_manager.py  sparse 3D grid, dirty delta tracking
    ├── Water system      engine.py     dynamic levels, evaporation, groundwater
    └── BYOM adapters     adapters/     mlp / static / random
```

Seven-phase tick loop (in order):
flow → interactions → guards → voxel effects → water → soil evaporation
→ motor inference → removals → spawns

## Key Constraints

- **ecosim is stdlib-only.** Never add external imports to `server/ecosim/`.
  Worker deps (websockets etc.) go in the `[worker]` optional group in pyproject.toml.
- **Docker is the primary getting-started path.** Don't break the Dockerfile or
  compose setup. Base image is `python:3.12-slim`; setuptools backend (not hatchling).
- **Tick rate is ~100ms (10 Hz).** Step time budget is ~1ms. Profile before
  adding O(n²) loops — neighbor queries are currently brute-force.
- **Grid is 32³, cell_size 1.0, motion latent dims = 4.** These are locked for v0.
- **Randomization is opt-in.** No `"randomize"` key in a world JSON = exact positions.
  Never make randomization the default.
- **Plants go dormant, not dead.** `health == 0` → DORMANT state; roots persist.
  2000-tick timeout → permanent death. Do not change this to immediate death.

## BYOM Adapter Protocol

Custom adapters implement `MotorAdapter` (see `ecosim/model_adapter.py`):
- `context_spec() → ContextSpec`  — declares what inputs the model needs
- `infer(context) → list[float]`  — returns motion latent (length 4)

See `docs/model_adapter_spec.md` for the full BYOM guide.

## Common Gotchas

- `elif` chains are exclusive — use `and` to combine guard conditions, not `elif`.
- `initialize_from_soil` must set all three computed voxel layers (nutrients,
  moisture, temperature) — the break bug from v0 silently skipped layers 2–3.
- WebSocket `process_request` signature changed in websockets 13+: it now takes
  `(connection, request)` and must return a `Response` object.
- Guard hysteresis bands have separate enter/exit thresholds — don't collapse
  them to a single value or populations oscillate.
- Rain must affect soil, plants, water sources, AND suppress evaporation or the
  effect is too weak to matter ecologically.

## Project Layout (server/)

```
ecosim/
  engine.py          EcosystemEngine — main hybrid automaton
  entities.py        entity schemas, init_entity()
  biome.py           BiomeConfig presets
  voxel_manager.py   sparse grid, dirty delta tracking
  model_adapter.py   MotorAdapter protocol, ContextSpec
  worker.py          async WS tick loop + HTTP server
  adapters/
    mlp.py           reference MLP (~500 params, pure Python)
    static.py        per-state hand-tuned latents
    random.py        random latents for testing
examples/
  demo_world.json    temperate meadow (canonical test world)
tests/
  smoke_test.py      50-tick integration test
  test_ecosim.py     12 unit tests
```

## CI

GitHub Actions (`.github/workflows/test.yml`) — pytest + ruff on Python 3.11 and 3.12.
All PRs must pass before merge.

## Docs

- `docs/model_adapter_spec.md`   BYOM adapter guide
- `docs/data_contract.md`        v0.2 WebSocket protocol spec
- `docs/lessons_learned.md`      debugging war stories — read before deep changes
- `docs/species_spec.md`         species + skeleton rigs (for Godot client work)
