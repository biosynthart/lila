<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā search — ASAL-compatible ecosystem search

Discovers diverse ecosystem configurations using foundation-model-guided
illumination search. Wraps the existing ecosim engine in an ASAL substrate
protocol (`Init`/`Step`/`Render`) and searches over rate multipliers, biome
parameters, and entity counts to find maximally diverse simulations.

## Setup

```bash
cd search && uv sync --extra dev
```

ecosim must be importable for integration tests and search runs.

**GPU recommended** for CLIP inference. The search runs on CPU if no GPU
is available, just slower (~3× for CLIP embedding).

## Quick start

```bash
# Smoke test (~30s, validates full pipeline)
uv run python -m scripts.run_illumination \
    --pop-size 8 --generations 3 --steps 200 --frames 5 \
    -o results/smoke

# Full run (~100 min on RTX 5060 Ti, 4 workers)
uv run python -m scripts.run_illumination \
    --pop-size 64 --generations 100 --steps 2000 --frames 20 \
    --workers 4 --atlas-grid 8 \
    -o results/illuminate_v1
```

## Output

```
results/
├── atlas.png              # Simulation atlas (UMAP grid of thumbnails)
├── scatter.png            # Embedding space scatter plot
├── diversity.png          # Min NN distance over generations
├── thetas_final.npy       # Parameter vectors (pop_size, 17)
├── embeddings_final.npy   # CLIP embeddings (pop_size, 512)
├── metadata.json          # Run config and metrics
├── thumbnails/            # Rendered frame per ecosystem
└── checkpoints/           # Periodic saves during search
```

Don't check results into git — binary blobs. Use GitHub Releases for
sharing artifacts. Add atlas images to `docs/assets/` for the README.

## Replay in browser

Every simulation in the atlas is deterministic — θ + seed reproduces
it tick-for-tick. Export any atlas entry as a world config and replay
it through the browser visualizer with the full canvas renderer.

```bash
# Export atlas entry #42 as a world config
uv run python -m scripts.export_world results/illuminate_v1 42

# Replay in browser (from server/)
cd ../server
WORLD_FILE=../search/replay.json uv run python -m ecosim.worker
# Open http://localhost:8001
```

The exported config includes the `"rain"` key from the search, so
auto-rain fires at the discovered interval — matching what CLIP saw.

Browse the atlas image, pick a tile, find its index (row × 8 + col
for an 8×8 atlas, or check `thumbnails/sim_NNNN.png`), export, replay.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/run_illumination.py` | Run illumination search with full CLI args |
| `scripts/export_world.py` | Export atlas entry → world config JSON for replay |

### run_illumination.py

```
--pop-size N       Population size (default: 64)
--children N       Children per generation (default: 32)
--generations N    Number of generations (default: 100)
--steps N          Simulation ticks per rollout (default: 2000)
--frames N         Frames captured per rollout (default: 20)
--mutation-scale F Mutation scale relative to ranges (default: 0.1)
--seed N           Random seed (default: 0)
--workers N        Parallel CPU rollout workers (default: 1)
--atlas-grid N     Atlas grid cells per side (default: 8)
--device STR       Torch device for CLIP (default: auto)
--skip-atlas       Skip atlas generation
-o, --output DIR   Output directory
```

### export_world.py

```
uv run python -m scripts.export_world RESULTS_DIR INDEX [-o OUTPUT] [--seed N]
```

Prints the full θ breakdown and writes a world config JSON ready
for the worker.

## Architecture

```
lila_search/
├── renderer.py        Engine state → 256×256 RGB (PIL, no browser)
├── theta.py           17-dim parameter space + θ → world config
├── substrate.py       ASAL protocol: Init(θ)/Step/Render wrapping engine
├── evaluator.py       CLIP ViT-B/32 embedding with batched multi-rollout
├── illumination.py    Diversity GA with farthest-point selection
└── viz/atlas.py       UMAP projection + grid-sampled atlas image
```

The search package **imports from ecosim but never modifies it**. ecosim
stays stdlib-only. All heavy dependencies (torch, CLIP, umap) live here.

## Tests

```bash
# Unit tests (no ecosim needed — uses mock engine)
uv run pytest tests/test_theta.py tests/test_renderer.py -v

# Integration tests (requires ecosim)
uv run pytest tests/test_substrate.py -v
```

## What this searches over (Track A)

17 dimensions:
- 6 rate multipliers (consumption, hunger, thirst, growth, reproduction, water replenishment)
- 3 biome values (soil nitrogen, soil moisture, climate temperature)
- 5 entity counts (deer, butterfly, oak, grass, wildflower)
- 2 water source params (count, radius)
- 1 rain interval

This finds "interesting tunings of the same five species." When the
trait-based architecture lands, θ expands to encode body masses, diets,
and thermal tolerances — the search becomes "interesting ecologies."

## First run results

64 population, 100 generations, 2000-tick rollouts, RTX 5060 Ti 16GB,
4 parallel workers. ~100 minutes.

- Initial diversity (min NN dist): 0.005
- Final diversity: 0.022 (4× improvement)
- Mean NN distance still climbing at termination
- Atlas shows distinct ecological regimes: drought-stressed sparse worlds,
  deer population explosions, plant-dominated high-moisture meadows,
  balanced mixed communities

See [WORKING_WITH_RESULTS.md](WORKING_WITH_RESULTS.md) for analysis
recipes, embedding exploration, target search, and replay instructions.

## Adapting to your engine API

If the ecosim attribute names differ from what the renderer expects,
edit the `_extract_*` functions in `renderer.py`. These isolate all
engine API assumptions in one place:

- `_extract_entities(engine)` → list of entity dicts
- `_extract_moisture_grid(engine)` → 32×32 numpy array
- `_extract_water_sources(engine)` → list of water source dicts

Similarly, if `theta_to_world_config()` produces a dict that doesn't
match your `EcosystemEngine.__init__()` format, adjust it in `theta.py`.
