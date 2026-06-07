<!--
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā — Ecosystem Simulation Engine

> *Define a world in JSON. The engine handles ecology, physics, and population dynamics.*
> Nothing is scripted. Everything emerges.

[![CI](https://github.com/hellolifeforms/lila/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/hellolifeforms/lila/actions/workflows/test.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

<!-- TODO: Replace with a 3–5s loop showing emergent dynamics (grazing → drought → recovery) -->
![līlā ecosystem demo](docs/assets/demo.gif)

`līlā` is an open-source engine that grows autonomous ecosystems from simple rules. You define species, biomes, and resources — the engine handles hunger cycles, predator-prey loops, soil nutrient flows, water depletion, dormancy, and recovery. Organisms don't follow scripts; their behavior emerges from continuous state variables, hybrid automata guards, and environmental feedback.

> **What you see right now is a 2D debug visualizer** — a window into the engine's state, not the final form. The engine is the product: a headless simulation server that streams tick packets over WebSocket to any client. A 3D Godot client with skeletal animation is planned for v0.1.0. The thesis isn't pretty graphics — it's that tiny ML models, invisible to the user, make a world feel alive. See ["The Unseen Hand"](https://hellolifeforms.substack.com/p/the-unseen-hand) for the full argument.

### Built for

- **Game & world developers** — plug lifelike ecosystem behavior into your project via WebSocket or headless mode
- **AI/ML researchers** — drop in custom motion and behavior models with the BYOM adapter protocol (MLP, ONNX, REST, anything)
- **ALife & simulation practitioners** — trait-based species definitions plus an ASAL-compatible substrate for FM-guided search
- **Educators & creators** — watch ecological principles emerge in real time. No win condition. The world plays as itself.

### Quick start (Docker)

```bash
git clone https://github.com/hellolifeforms/lila.git
cd lila/deploy/compose
docker compose up --build
```

Open **http://localhost:8001** — a temperate meadow is already running at 10 Hz. Click **☔ Rain** to trigger recovery cycles, or **⏺ Record** to capture dynamics as WebM (convert to GIF with `ffmpeg -i lila-recording.webm -vf "fps=15,scale=480:-1" -loop 0 docs/assets/demo.gif`). Stop with `docker compose down`.

### Why līlā?

- **Stdlib core** — zero external dependencies in `ecosim`. Fast, reproducible, easy to audit
- **BYOM adapters** — swap in custom ML models without modifying engine code
- **Trait-based species** — add organisms via JSON vectors; behavior derives from allometric scaling laws (Kleiber's Law, metabolic theory of ecology)
- **Actor effects system** — immutable effect pipeline enables deterministic replay and clean modularity
- **ASAL compatible** — exposes `Init(θ) / Step(θ) / Render(θ)` for foundation-model ecosystem search

### Where to go next

- **Run & visualize** — [Quick start](#quick-start-docker) above; recording and controls below
- **Define a world** — [Define a world in JSON](#define-a-world-in-json) just below
- **Bring Your Own Model** — [`docs/model_adapter_spec.md`](docs/model_adapter_spec.md) for the adapter protocol, context specs, and export pipeline
- **Trait architecture** — [`LILA_PROJECT_STATE.md`](LILA_PROJECT_STATE.md#milestone-2-trait-based-architecture--partial--two-pool-nutrients-pending) for allometric scaling and interaction templates
- **ASAL search** — [`search/README.md`](search/README.md) for illumination search, CLIP evaluation, and atlas generation
- **Develop & contribute** — [`DEVELOPING.md`](DEVELOPING.md) for the `uv` workflow, tests, and architecture deep dives

> *The name [līlā](https://www.embodiedphilosophy.com/what-is-lila/) comes from the Sanskrit concept of spontaneous, purposeless creative unfolding. There's no win condition. The world plays as itself.*

---

## Define a world in JSON

A world is a JSON file describing the environment, the rate multipliers that govern its physics, the entities living in it, and the trait vectors that define each species. The engine reads this once at startup and derives everything else. Here's a slimmed excerpt of `examples/demo_world.json` — the full file ships in `server/examples/`:

<hr>
<details>
<summary><strong>Example world definition</strong> (click to expand)</summary>

```json
{
  "version": "0.1",
  "session_id": "demo-alpha-001",

  "environment": {
    "type": "MEADOW",
    "biome": "TEMPERATE",
    "climate": {
      "temperature": 22.0, "humidity": 0.6,
      "rainfall": 0.4, "light_level": 0.85
    },
    "soil": { "nutrients": 0.65, "moisture": 0.65, "organic_matter": 0.4 },
    "voxel_grid": { "dimensions": [32, 32, 32], "cell_size": 1.0 },
    "water_sources": [
      { "position": [6.0, 0.0, 20.0], "radius": 3.0 },
      { "position": [25.0, 0.0, 7.0], "radius": 2.0 }
    ]
  },

  "model":   { "adapter": "mlp", "seed": 42 },

  "rates": {
    "consumption": 4.0, "hunger": 1.0, "thirst": 1.0,
    "growth": 0.6, "reproduction": 1.0, "water_replenishment": 0.4
  },

  "randomize": {
    "jitter": 1.5, "transform": true,
    "extra_grass": [0, 4], "extra_flowers": [0, 2]
  },

  "entities": [
    { "id": "deer_01",    "type": "ANIMAL", "species": "deer",         "position": [16.0, 0.0, 14.0] },
    { "id": "butterfly_01", "type": "INSECT", "species": "butterfly", "position": [10.0, 0.0,  8.0] },
    { "id": "oak_01",     "type": "TREE",   "species": "meadow_oak",   "position": [ 8.0, 0.0,  8.0] },
    { "id": "grass_01",   "type": "PLANT",  "species": "meadow_grass", "position": [12.0, 0.0, 12.0] },
    { "id": "flower_01",  "type": "PLANT",  "species": "wildflower",   "position": [11.0, 0.0,  6.0] }
    // ...and so on
  ],

  "species_definitions": [
    {
      "species_id": "deer",
      "entity_class": "ANIMAL",
      "functional_group": "herbivore",
      "body_mass_kg": 80.0,
      "locomotion": "quadruped",
      "thermoregulation": "endotherm",
      "diet_type": "herbivore",
      "diet_breadth": ["graminoid", "forb"],
      "trophic_level": 2.0,
      "reproductive_strategy": "K_selected",
      "clutch_size": 1,
      "generation_time_ticks": 5000,
      "thermal_range": [0, 40],
      "drought_tolerance": 0.3,
      "shade_tolerance": 0.3,
      "movement_budget": 0.4
    }
    // ...one entry per species. Allometric scaling derives the rest.
  ]
}
```

</details>
</hr>
<br>
A few things worth noticing:

- **No behavior code in here.** "deer" doesn't say *how* to graze, only that it's an 80 kg quadrupedal endotherm herbivore that eats graminoids and forbs. The engine's interaction templates handle the rest.
- **`rates` are multipliers, not absolutes.** They scale the global constants in `constants.py`. Bumping `hunger: 2.5` makes everyone get hungry 2.5× faster — useful for stress-testing without rewriting code.
- **`randomize`** controls reproducibility: same seed plus same world equals same simulation, but the `transform`, `jitter`, and `extra_*` knobs let you sample variations without editing the file.

See [`server/examples/`](server/examples/) for the full demo and additional preset worlds.

---

## How it works

```
    Browser / Godot client
           │
           │ WebSocket (tick packets at 10 Hz)
           │
    ┌──────▼───────┐
    │   Worker     │  One per active ecosystem
    │   HTTP + WS  │  Serves viz + streams ticks
    └──────┬───────┘
           │
    ┌──────▼───────────────────────────────────┐
    │   ecosim (Python, stdlib only)           │
    │                                          │
    │   engine ─── hybrid automaton            │
    │              flow equations + guards     │
    │              hysteresis on transitions   │
    │                                          │
    │   traits ── species as trait vectors     │
    │             allometric derivation        │
    │             interaction templates        │
    │                                          │
    │   adapters ─ BYOM motor models           │
    │              mlp / static / random       │
    │              ...or bring your own        │
    │                                          │
    │   voxels ─── sparse 3D grid (5 layers)   │
    │              nutrients_fast, slow ✅     │
    │              moisture, temperature,      │
    │              organic matter              │
    └──────────────────────────────────────────┘
```

Each tick, the engine runs seven phases: continuous flow updates, entity interactions, guard condition checks (with hysteresis to prevent oscillation), voxel layer effects, motor model inference, removals, and spawns. The result is a delta-encoded tick packet streamed to the client over WebSocket.

The engine has **zero external dependencies** — stdlib Python only. All numeric constants live in a single `constants.py` module. The actor system (flow, guard, interaction) uses immutable effects applied atomically via EffectBus. The worker adds `websockets`. That's the entire server.

## Bring Your Own Model

The simulation engine handles physics and ecology. Models handle intelligence. The adapter system defines a clean socket where they meet.

```python
from ecosim.engine import EcosystemEngine
from ecosim.adapters import create_adapter

# Reference MLP (pure Python, ~500 params)
engine = EcosystemEngine(world, adapters={
    "motor": create_adapter("mlp", seed=42),
})

# Pre-trained weights
engine = EcosystemEngine(world, adapters={
    "motor": create_adapter("mlp", weights="weights/motion_v1.json"),
})

# No model at all — simulation still works
engine = EcosystemEngine(world)
```

A custom adapter implements three methods (though `context_spec_for` can just delegate to `context_spec` if you don't need type-specific inputs):

```python
class MyMotorAdapter:
    def context_spec(self) -> ContextSpec:
        """What inputs your model needs."""
        ...

    def context_spec_for(self, entity_type: str) -> ContextSpec:
        """Type-specific inputs. Defaults to context_spec()."""
        return self.context_spec()

    def infer(self, contexts: list[list[float]]) -> list[list[float]]:
        """Batch of context vectors → batch of latent vectors."""
        ...
```

The engine builds context vectors from entity state according to your spec, calls `infer()`, and writes the latent vectors back to entities. Your model could be a neural network, an ONNX runtime, a REST call to a cloud endpoint, or a lookup table. The engine doesn't care.

Three model levels are defined:

| Level     | Cadence       | What it does                              | Status                  |
|-----------|---------------|-------------------------------------------|-------------------------|
| Motor     | every tick    | Drives animation style via latent vectors | **implemented**         |
| Behavior  | every tick    | Influences state transition decisions     | designed, not yet wired |
| Narrative | every N ticks | Shapes macro-scale ecosystem dynamics     | designed, not yet wired |

See [`docs/model_adapter_spec.md`](docs/model_adapter_spec.md) for the full guide to building your own adapter.

## Use cases

**Education** — watch ecological principles emerge in real time. Predator-prey dynamics, nutrient cycling, competitive exclusion — experienced, not memorized.

**Game development** — plug lifelike ecosystem behavior into your world. The BYOM adapter system lets you bring your own motion and behavior models trained on your animation data.

**Research** — run controlled ecosystem experiments at scale. Reproducible seeds, configurable biomes, exportable event logs and population data.

**Creative exploration** — just watch. There's no win condition. The world plays as itself.

**Artificial life research** — an ASAL-compatible substrate with ecological semantics. Search for interesting ecosystems using foundation models, not hand-tuning.

## The 0.1-alpha ecosystem

The current demo runs a temperate meadow with **eight species defined via trait vectors**:

| Species      | Type   | Role                                                    |
|--------------|--------|---------------------------------------------------------|
| Deer         | ANIMAL | Grazer, seeks grass → flowers → water → mates           |
| Butterfly    | INSECT | Pollinator, seeks fruiting wildflowers                  |
| Oak tree     | TREE   | Anchors the scene, creates shade and nutrient gradients |
| Meadow grass | PLANT  | Ground cover, fast-growing grazing target               |
| Wildflower   | PLANT  | Blooms when healthy, attracts butterflies               |
| Wolf         | ANIMAL | Predator — completes food chain (grass → deer → wolf)   |
| Songbird     | BIRD   | Insectivore + frugivore — new trophic niche             |
| Mushroom     | MICROORGANISM | Decomposer — closes the nutrient loop            |

All species behavior is **derived from functional traits** using allometric scaling laws (Kleiber's Law, metabolic theory of ecology). Adding a new species requires only a JSON trait vector — no engine code changes. Interaction templates (herbivory, predation, pollination, decomposition) handle the combinatorics automatically.

Three interaction chains emerge without scripting:

- **Grazing loop** — deer hunger rises → deer forages toward nearest grass → grass consumed → grass spreads from runners if soil is moist
- **Pollination loop** — wildflower reaches fruiting → butterfly flies to it → pollinates → lingers → seeks next flower
- **Water loop** — thirst rises → deer walks to nearest pond → drinks → pond level drops → soil moisture falls
- **Stress cascade** — overgrazing → grass dies back → deer eat wildflowers → no flowers left → butterflies lose food → butterflies cluster at ponds → ponds dry up → everything collapses
- **Dormancy & recovery** — plants die back to dormant root systems → user triggers rainfall → soil moisture rises → roots detect moisture → plants regrow from the same locations

## Project structure

```
lila/
├── server/
│   ├── ecosim/              # Core simulation (stdlib only)
│   │   ├── engine.py        # Hybrid automaton
│   │   ├── entities.py      # Entity schemas
│   │   ├── traits.py        # Trait definitions
│   │   ├── trait_compiler.py# Trait → derived params compiler
│   │   ├── interactions.py  # Interaction templates
│   │   ├── biome.py         # Biome presets
│   │   ├── voxel_manager.py # VoxelGrid protocol + UniformVoxelGrid (multi-resolution ready)
│   │   ├── world_processes.py  # World-process handlers (evaporation, water replenish, soil drain/deposit)
│   │   ├── constants.py     # Universal simulation constants (single source of truth)
│   │   ├── model_adapter.py # BYOM protocol
│   │   ├── effects.py       # Effect dataclasses + EffectBus (immutable effect pipeline)
│   │   ├── actors/          # Actor system (flow, guard, interaction, movement)
│   │   │   ├── __init__.py  # InteractionContext, FlowActor/GuardActor bases, registries
│   │   │   ├── flow_actors.py    # ConsumerFlowActor, ProducerFlowActor, DecomposerFlowActor (+ MovementActor integration)
│   │   │   ├── guard_actors.py   # ConsumerGuardActor, ProducerGuardActor, DecomposerGuardActor
│   │   │   ├── interaction_actors.py  # FleeActor, PredationActor, HerbivoryActor, PollinationActor
│   │   │   └── movement_actors.py  # MovementActor — target selection as effect-emitting actor
│   │   ├── layout.py        # LayoutManager — world loading, D4 transforms, randomization
│   │   ├── spatial_index.py # SpatialIndex protocol + BruteForceSpatialIndex (neighbor queries)
│   │   ├── movement_system.py  # MovementSystem — gate policy + kinematics for mobile entities
│   │   ├── worker.py        # WebSocket server
│   │   └── adapters/        # Built-in motor models
│   ├── examples/            # Demo world definitions
│   └── tests/
│       ├── test_voxel_grid.py    # VoxelGrid protocol + query_overlap/walk_layer (28 tests)
├── search/
│   ├── lila_search/         # ASAL-compatible search (see below)
│   │   ├── substrate.py     # Init/Step/Render protocol
│   │   ├── renderer.py      # Headless PIL renderer
│   │   ├── theta.py         # Parameter space definition
│   │   ├── evaluator.py     # CLIP embedding
│   │   ├── illumination.py  # Diversity-driven GA
│   │   └── viz/atlas.py     # UMAP atlas visualization
│   ├── scripts/             # CLI entry points
│   └── tests/
├── client/
│   ├── browser/             # Canvas-based 2D visualizer
│   └── godot/               # 3D client (in development)
├── training/                # Example ML training pipeline
├── deploy/
│   └── compose/             # Docker Compose (start here)
└── docs/
```

### Actor-based architecture

The engine dispatches behavior through an **actor system** with three actor types:
- **Flow actors** — continuous state evolution (hunger, thirst, growth) as effect-emitting actors
- **Guard actors** — discrete state transitions (FORAGING → DRINKING) with hysteresis
- **Interaction actors** — entity↔entity interactions (predation, herbivory, pollination, fleeing)

Each actor reads a frozen `InteractionContext` and emits immutable effects. The `EffectBus` collects all effects, sorts by priority, resolves conflicts, and applies them atomically in a single pass. This enables deterministic replay and clean separation of concerns.

### Extracted subsystems

The monolithic engine has been decomposed into focused modules:
- **LayoutManager** (`layout.py`) — entity initialization, water source parsing, grid bounds calculation, and the full randomization pipeline (D4 transforms, jitter, extra spawns, push-from-water)
- **SpatialIndex** (`spatial_index.py`) — neighbor queries via a strategy interface (current: BruteForceSpatialIndex; pluggable for future spatial hash swap)
- **MovementSystem** (`movement_system.py`) — movement gate policy (linger/cooldown decrements, ACTIVE_MOVEMENT_STATES check, pollinator exception) and kinematics (move toward target at species speed, clamp to grid, clear on arrival)
- **VoxelGrid protocol** (`voxel_manager.py`) — abstracts storage strategy via Protocol interface; `UniformVoxelGrid` implements it with `query_overlap()` for spherical footprint queries and `walk_layer()` for sparse iteration; swap-in ready for future octree implementation
- **World-process handlers** (`world_processes.py`) — pluggable handlers dispatched through EffectBus at their own frequencies: SoilEvaporationHandler, WaterReplenishHandler, SoilDrainHandler, SoilDepositHandler. Handlers depend on the VoxelGrid protocol, not concrete classes

## Background

The project thesis is that the most impactful AI is small, specialized, and invisible to the user. Not a chatbot, not a copilot — a 500-parameter network running at 10 Hz, producing a 4-dimensional latent vector that nobody ever sees, but that drives the difference between an entity that *moves* and one that *behaves*.

The current 2D visualizer shows the engine state: positions, discrete states, soil moisture. The Godot client (v0.1.0) will map those latent vectors to skeletal animation — that's where the thesis becomes visceral. For now, watch the event log and population dynamics. The intelligence is already there; the rendering will catch up.

For the full argument, see ["The Unseen Hand"](https://postcorporate.substack.com/p/the-unseen-hand) on Substack.

## Ecosystem search

līlā is also an [ASAL](https://asal.sakana.ai/)-compatible substrate for foundation-model-guided ecosystem search. The engine wraps in a standard `Init(θ) / Step(θ) / Render(θ)` protocol; a headless renderer produces frames; CLIP embeds them; a diversity-driven genetic algorithm discovers maximally varied ecosystem configurations.

The first illumination run searched a 17-dimensional parameter space — rate multipliers, biome conditions, entity counts, water configuration — across 64 populations for 100 generations, each rolling out 2000 ticks with 20 CLIP-embedded frames. The result is a simulation atlas: a map of ecologically distinct worlds discovered by the search, projected into 2D with UMAP.

![Simulation atlas](docs/assets/atlas.png)

Each tile is a different ecosystem found by the search — drought-stressed sparse worlds, deer population explosions, plant-dominated high-moisture meadows, balanced mixed communities. None were hand-designed. The search found them by maximizing diversity in CLIP embedding space.

This is currently rate tuning over five fixed species. The **trait-based architecture** (shipped) enables the next step: θ encodes body masses, diets, and thermal tolerances, and the engine derives behavior allometrically. The search becomes "what organisms produce the most interesting ecologies?" — not "what tuning of the same organisms looks different?" For more on the connection between ecological substrates and artificial life search, see ["Life as It Could Be"](https://postcorporate.substack.com/p/life-as-it-could-be).

```bash
# Run illumination search
cd search && uv sync
uv run python -m scripts.run_illumination \
    --pop-size 64 --generations 100 --steps 2000 --frames 20 \
    --workers 4 -o results/illuminate
```

See [`search/README.md`](search/README.md) for setup, output format, and analysis recipes.

## Roadmap

The engine has transitioned from hand-crafted per-species rules to a **trait-based architecture** using allometric scaling laws. Species are defined as functional trait vectors in JSON — body mass, diet type, metabolic class, locomotion mode — and the engine derives all behavior parameters from established ecological scaling laws (Kleiber's Law, metabolic theory of ecology). Adding a wolf means writing a JSON trait vector, not new Python code.

The actor system extracts entity↔entity interactions into pure functions that emit immutable effects. All numeric constants live in `constants.py` as the single source of truth.

**Shipped:**
- Trait-based species architecture — body mass → derived behavior via allometric scaling
- Interaction templates — herbivory, predation, pollination, decomposition (parameterized, no per-species code)
- Trait compiler — runs once at init, produces DerivedParams + interaction matrix for the engine
- 8 species defined as trait vectors (deer, butterfly, oak, grass, wildflower, wolf, songbird, mushroom)
- Actor effects architecture — immutable EffectBus with flow/guard/interaction/movement actors
- MovementActor — target selection extracted from engine into pure-function actor emitting SetTarget/ClearTarget effects
- Engine decomposition — LayoutManager, SpatialIndex, MovementSystem, MovementActor, EnvironmentManager
- VoxelGrid protocol + UniformVoxelGrid with `query_overlap()` and `walk_layer()`
- World-process handlers dispatched through EffectBus (evaporation, water replenish, soil drain/deposit)
- Two-pool soil nutrient system — fast/slow pools with mineralization, dissolution, leaching fluxes
- Simulation config loader (`config.py`) — tunable params from JSON, override via `sim_config.json`
- Universal constants module (`constants.py`) — single source of truth for all simulation physics
- Pollinator dispersal mechanics — per-flower caps, visit limits, wander cooldowns, post-visit cooldowns
- ASAL substrate protocol — `Init(θ) / Step(θ) / Render(θ)` wrapping ecosim
- Headless renderer for FM-guided evaluation (PIL, 256×256)
- Illumination search — diversity-driven GA with CLIP ViT-B/32
- Simulation atlas — UMAP projection of discovered ecosystems

**Near-term:**
- Spatial hash for O(1) neighbor queries (SpatialIndex strategy swap)
- Calibration & regression testing (2000-tick baseline with two-pool nutrients)
- Emergent dynamics validation with 8 species (trophic cascades, Lotka-Volterra oscillations)
- Trait-based search — θ encodes organism traits, not just rate multipliers
- Target search — CMA-ES optimization toward text prompts via CLIP

**Medium-term:**
- Searchable physics — allometric exponents as θ dimensions
- Godot 3D client with latent-driven skeletal animation
- Open-ended search — temporal novelty across simulation rollouts

## Contributing

līlā is in early alpha. Contributions welcome — especially:

- **New species** — today: entity metadata + flow equation tuning. Soon: a JSON trait vector and the engine derives the rest
- **Motor adapters** — train a model, export weights, share it
- **Biome presets** — new environments with tuned simulation constants
- **Ecological modeling** — allometric scaling, interaction templates, soil nutrient dynamics. If you know metabolic theory of ecology, there's real work here
- **Client work** — the Godot client needs skeleton rigs, shaders, and scene work
- **ALife/search integration** — the ASAL substrate protocol is working. Target search, open-ended search, and trait-based θ expansion are next. If you've worked with ASAL, Lenia, or similar frameworks, there's real work here
- **Bug reports** — the [known issues](docs/lessons_learned.md) are documented, but there are certainly more

## Acknowledgments

līlā was co-developed with multiple AI systems:
- **[Pi.dev](https://pi.dev)** — coding agent for implementation, refactoring, and project state tracking
- **[Claude](https://claude.ai)** (Anthropic) — architecture design, simulation tuning, documentation
- **[Qwen3.6-27B-MTP-GGUF](https://github.com/unslothai/unsloth)** via [Unsloth](https://github.com/unslothai/unsloth) — local reasoning and code review

## License

Apache 2.0 — see [LICENSE](LICENSE).

Copyright 2025 BioSynthArt Studios LLC.

Follow the project: [@hellolifeforms](https://bsky.app/profile/hellolifeforms.bsky.social) on Bluesky.
