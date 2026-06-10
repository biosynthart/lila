<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā — Project State (v0.0.1-alpha)

## Current Status

**Tagged release: v0.0.1-alpha** — published, repo public on GitHub.

līlā is a BYOM (Bring Your Own Model) ecosystem simulation engine. Users define a world in JSON — species, biome, soil, water — and the engine grows an autonomous ecosystem from simple rules. The server runs the hybrid automaton (ecology, physics, ML inference); clients render the result via WebSocket at 10 Hz.

The project thesis — explored in ["The Unseen Hand"](https://www.hellolifeforms.com/p/the-unseen-hand) — is that the most impactful AI is small, specialized, and invisible. Tiny ML models guide lifelike motion and behavior; the user never sees inference happening, they just see a world that feels alive.

The name comes from the Sanskrit concept of [līlā](https://www.embodiedphilosophy.com/what-is-lila/) — the spontaneous, purposeless creative unfolding of reality. There's no win condition. The world plays as itself.

**Current direction:** The engine is transitioning from hand-crafted per-species rules to a **trait-based architecture** using allometric scaling laws (Metabolic Theory of Ecology). Species become points in trait space; the engine derives all behavior parameters from body mass and functional traits. This also makes līlā a compelling **substrate for automated ALife search** (ASAL framework) — an ecologically-grounded simulation where FM-guided search discovers interesting ecosystem configurations.

**Copyright:** BioSynthArt Studios LLC. **License:** Apache 2.0.
**Source control:** GitHub at `github.com/hellolifeforms/lila` (org: hellolifeforms).
**CI:** GitHub Actions (`.github/workflows/test.yml`) — pytest + ruff, Python 3.11/3.12. Badge in README.
**Social:** @hellolifeforms on Bluesky, Postcorporate on Substack.

---

## Architecture Overview

```
┌─────────────────────────┐
│    Browser Visualizer   │  ← v0.0.1-alpha (shipped, single HTML file)
│    Godot 4.x Client     │  ← deferred to Milestone 4
│    Headless Renderer    │  ← Shipped (PIL, 256×256, for ASAL search)
└──────────┬──────────────┘
           │ WebSocket (delta-encoded tick packets)
┌──────────▼──────────────┐
│    Worker               │  ← Shipped. HTTP + WS on single port
│    (one per session)    │     Serves viz HTML, streams ticks
└──────────┬──────────────┘
           │
┌──────────▼─────────────────────────────────────────────────────────┐
│    ecosim (Python package, stdlib only)                            │
│  ┌─────────────────┐  ┌───────────────────────────────────────┐    │
│  │ Hybrid Automaton│  │ Trait System (Milestone 2)            │    │
│  │ Flow + Guards   │  │ TraitVector + Compiler                │    │
│  ├─────────────────┤  │ Allometric Derivations                │    │
│  │ Voxel Manager   │  │ Interaction Templates                 │    │
│  │ 5 layers (M2)   │  ├───────────────────────────────────────┤    │
│  │ Water System    │  │ Actor Effects Architecture            │    │
│  │ Dynamic levels  │  │ EffectBus + Flow/Guard/IX Actors      │    │
│  ├─────────────────┤  │ Dual-path: trait-based / legacy       │    │
│  │ Two-Pool Soil   │  ├───────────────────────────────────────┤    │
│  │ Fast/Slow (M2)  │  │ BYOM Adapters                         │    │
│  └─────────────────┘  │ mlp/static/random                     │    │
│                       ├───────────────────────────────────────┤    │
│                       │ World Randomizer                      │    │
│                       │ D4 transforms                         │    │
│                       └───────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
           │
┌──────────▼────────────────────────────────────────────┐
│    search/ (Shipped — Track A, rate-tuning search)    │
│    ASAL Substrate Protocol (Init/Step/Render)         │
│    Headless PIL Renderer (256×256)                    │
│    CLIP ViT-B/32 Evaluator                            │
│    Illumination Search (diversity GA)                 │
│    Simulation Atlas (UMAP + grid sampling)            │
│    ─────────────────────────────────────────────────  │
│    Target Search (CMA-ES + text prompts)      pending │
│    Open-Ended Search (temporal novelty)       pending │
│    Trait-Based θ Expansion (Milestone 2 dep)  pending │
└───────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
lila/
├── server/
│   ├── pyproject.toml              # lila-ecosim package, stdlib only
│   ├── .python-version             # uv Python version (3.12)
│   ├── uv.lock                     # deterministic dependency lockfile
│   ├── ecosim/                     # core simulation library
│   │   ├── __init__.py
│   │   ├── engine.py               # hybrid automaton (trait-based, actor-driven, ~737 lines)
│   │   ├── entities.py             # entity schemas, init_entity()
│   │   ├── biome.py                # biome presets → BiomeConfig
│   │   ├── config.py               # [M2] SIM_CONFIG loader — tunable params from JSON
│   │   ├── voxel_manager.py        # [M2] VoxelGrid protocol + UniformVoxelGrid (5 layers: nutrients_fast/slow, moisture, temp, OM)
│   │   ├── world_processes.py      # World-process handlers (evaporation, water replenish, soil drain/deposit, nutrient pool dynamics)
│   │   ├── environment_manager.py  # [M2] Environment state — biome, climate, voxels, water sources, rain
│   │   ├── model_adapter.py        # MotorAdapter protocol, ContextSpec
│   │   ├── worker.py               # async WS tick loop + HTTP viz server
│   │   ├── traits.py               # [M2] TraitVector, allometric derivations
│   │   ├── interactions.py         # [M2] InteractionTemplate grammar
│   │   ├── trait_compiler.py       # [M2] TraitCompiler: traits → engine params
│   │   ├── constants.py            # [M3] Universal simulation constants (single source of truth)
│   │   ├── effects.py              # [M3] Effect dataclasses + EffectBus
│   │   ├── actors/                 # [M3] Actor system (flow, guard, interaction, movement)
│   │   │   ├── __init__.py        # InteractionContext, FlowActor/GuardActor bases, registries
│   │   │   ├── flow_actors.py     # ConsumerFlowActor, ProducerFlowActor, DecomposerFlowActor (+ MovementActor integration)
│   │   │   ├── guard_actors.py    # ConsumerGuardActor, ProducerGuardActor, DecomposerGuardActor
│   │   │   ├── interaction_actors.py  # FleeActor, PredationActor, HerbivoryActor, PollinationActor
│   │   │   └── movement_actors.py  # MovementActor — target selection as effect-emitting actor (492 lines)
│   │   ├── layout.py             # [M3] LayoutManager — world loading + randomization pipeline (306 lines)
│   │   ├── spatial_index.py      # [M3] SpatialIndex protocol + BruteForceSpatialIndex (169 lines)
│   │   ├── movement_system.py    # [M3] MovementSystem — gate policy + kinematics (138 lines)
│   │   └── adapters/
│   │       ├── __init__.py         # create_adapter() factory
│   │       ├── mlp.py              # reference MLP (~500 params, pure Python)
│   │       ├── static.py           # hand-tuned latent per state
│   │       └── random.py           # random latents for testing
│   ├── config/
│   │   ├── sim_config.json         # tunable simulation parameters (overrides defaults)
│   │   └── biomes.json             # biome preset definitions
│   ├── examples/
│   │   ├── demo_world.json         # temperate meadow with randomization (updated rates, butterfly species)
│   │   └── temperate_meadow_8sp.json # [M3] 8-species trait-based world
│   ├── tests/
│   │   ├── smoke_test.py           # 50-tick integration test
│   │   ├── test_actors.py          # [M3] EffectBus + effect priority tests (70)
│   │   ├── test_ecosim.py          # unit tests (12 tests)
│   │   ├── test_traits.py          # [M2] allometric derivation tests (54)
│   │   ├── test_movement_actor.py  # [M3] movement actor behavior tests (36)
│   │   ├── test_voxel_grid.py      # VoxelGrid protocol + query_overlap/walk_layer (28 tests)
│   │   ├── test_nutrients.py       # [M2] two-pool nutrient flow tests (~20)
│   │   └── test_reproduction_actor.py  # reproduction actor behavior tests (~15)
│   └── weights/
│       └── (motion_v0.json)        # placeholder for trained weights
│
├── client/
│   ├── browser/
│   │   └── index.html              # canvas-based 2D ecosystem visualizer
│   └── godot/                      # [M4] Godot 4.x client
│
├── search/                         # ASAL substrate + search (shipped Track A)
│   ├── pyproject.toml              # deps: torch, open-clip, cma, umap, pillow
│   ├── lila_search/
│   │   ├── __init__.py
│   │   ├── substrate.py            # LilaSubstrate: Init/Step/Render protocol
│   │   ├── renderer.py             # headless PIL renderer (256×256)
│   │   ├── theta.py                # θ parameterization (17-dim EcoRates)
│   │   ├── evaluator.py            # CLIP ViT-B/32 embedding
│   │   ├── illumination.py         # diversity GA with farthest-point selection
│   │   └── viz/
│   │       └── atlas.py            # UMAP projection + grid-sampled atlas
│   ├── scripts/
│   │   └── run_illumination.py     # CLI entry point
│   └── tests/
│       ├── test_theta.py           # θ spec + world config generation
│       ├── test_renderer.py        # headless renderer (mock engine)
│       └── test_substrate.py       # integration tests (requires ecosim)
│
├── training/                       # ML training pipeline (not core)
│   ├── pyproject.toml
│   ├── data/
│   ├── scripts/
│   └── notebooks/
│
├── deploy/
│   └── compose/                    # ← primary getting-started path
│       ├── docker-compose.yml
│       ├── Dockerfile.worker
│       └── README.md
│
├── docs/
│   ├── model_adapter_spec.md       # BYOM guide — how to build adapters
│   ├── data_contract.md            # v0.2 protocol spec
│   ├── architecture.md
│   ├── species_spec.md             # 0.1-alpha species + skeleton rigs
│   ├── lessons_learned.md          # debugging war stories
│   ├── trait_species_guide.md      # [M2] how biologists add species
│   └── asal_substrate_guide.md     # [M3] how to use līlā with ASAL
│
├── .github/workflows/
│   └── test.yml                    # CI: pytest + ruff, Python 3.11/3.12
│
├── DEVELOPING.md                   # uv workflow, dev setup
├── LICENSE                         # Apache 2.0
├── README.md                       # project overview, quick start, roadmap
├── TRAIT_TRANSITION_PLAN.md        # detailed Phase 1-3 implementation plan
└── TWO_POOL_NUTRIENT_SPEC.md       # two-pool soil nutrient spec
```

Items marked `[M2]`, `[M3]`, `[M4]` indicate which milestone introduces them.

---

## What Shipped in v0.0.1-alpha

### Core Engine (ecosim)

**Hybrid automaton** — seven-phase tick loop: flow → interactions → guards → voxel effects → water replenishment → soil evaporation → motor inference → removals → spawns.

**Entity types:** ANIMAL, BIRD, INSECT, PLANT, TREE, MICROORGANISM. Each has type-specific flow equations, guard conditions with hysteresis, and valid state sets.

**Behavioral intelligence** (no ML required):
- Purposeful movement — entities seek food, water, flowers, and mates based on state
- Grazing chain — deer seek nearest grass, fall back to wildflowers when grass is gone
- Pollination chain — butterflies seek FRUITING wildflowers, linger 1.5–3s, then seek next bloom. Flower cooldown prevents re-pollination
- Water seeking — thirsty animals walk to nearest pond, drink, drain the source
- Mate seeking — grid-wide search when reproductive drive is high, proximity check for actual reproduction
- Flee response — prey flees from carnivores with clamped escape targets

**Guard hysteresis bands:**
- Hydration: enter DRINKING at 0.2, exit at 0.6
- Energy: enter RESTING at 0.2, exit at 0.5 (animals) / 0.15→0.4 (insects)
- Hunger: enter FORAGING at 0.3, exit at 0.15
- Reproduction: drive > 0.8 AND mate within sensory range (animals) / > 0.7 (insects)

**Plant ecology:**
- Vegetative spreading — grass (range 2, frequent) and flowers (range 3.5, less frequent) with soil checks, density limits, and parent resource cost
- Dormancy — plants go DORMANT at health 0 instead of dying. Roots persist. Recovery when soil moisture > 0.25 and nutrients > 0.15
- Dormancy timeout — 2000 ticks without recovery → permanent death
- FRUITING threshold — growth ≥ 0.5 and health > 0.4

**Water system:**
- Dynamic water levels — each source tracks `water_level` (0–1), controls effective radius
- Evaporation drains water sources, groundwater replenishes, drinking animals deplete
- Background soil evaporation across the full grid
- Dried-up sources (< 5%) skipped by pathfinding

**Ecosystem collapse:**
- Tree collapse pressure when support_count (non-tree, non-insect, non-dormant) ≤ 2
- Generational decline — children inherit parent stress (hunger × 0.3, energy × 0.9, colony_health × 0.9)
- Reproduction costs parent colony_health (insects)
- Starvation acceleration — colony_health drain scales with hunger level

**Rain system:**
- `apply_rain(intensity)` — boosts soil moisture (+0.24), nutrients (+0.024), water source levels (+0.32), plant hydration (+0.16), plant health (+0.08), animal hydration (+0.08)
- Suppresses soil evaporation and plant evapotranspiration for 80 ticks
- Triggered via WebSocket control message `{"type": "rain", "intensity": 0.8}`

**Rate multipliers** (configurable per world):
- `consumption`, `hunger`, `thirst`, `growth`, `reproduction`, `water_replenishment`
- All default to 1.0. Stress testing via JSON, no code changes.

**World randomization** (JSON-driven):
- D4 symmetry transforms (4 rotations × 2 flips = 8 orientations)
- Position jitter (configurable range)
- Extra grass (0–4) and wildflower (0–2) spawns
- Water source position and radius variation
- State variable jitter (±5%)
- Plants pushed out of water sources post-randomization
- Opt-in: omit `"randomize"` key for exact JSON positions

**BYOM adapter system:**
- `MotorAdapter` protocol — `context_spec()` + `infer()`
- `ContextSpec` with typed fields, source routing, normalization
- Type-specific specs via `context_spec_for(entity_type)`
- Three built-in: `mlp` (500 params, Xavier init), `static` (per-state latents), `random` (testing)
- `create_adapter()` factory

**Voxel manager:**
- `VoxelGrid` Protocol abstracts storage strategy (uniform grid → octree swap without changing call sites)
- `UniformVoxelGrid` implements the protocol with `query_overlap()` for spherical footprint queries and `walk_layer()` for sparse iteration
- Four layers: nutrients, moisture, temperature, organic_matter
- Threshold-gated dirty tracking for delta packets
- `initialize_from_soil` — correctly initializes all three computed layers (break bug fixed)

**World-process handlers:**
- Pluggable handlers dispatched through EffectBus at their own frequencies
- SoilEvaporationHandler, WaterReplenishHandler, SoilDrainHandler, SoilDepositHandler
- Handlers depend on VoxelGrid protocol, not concrete classes

### Browser Visualizer

- Canvas-based 2D renderer at 60fps with 10Hz tick interpolation
- Moisture heatmap (subtle teal→amber gradient)
- Water sources with radial gradient, animated ripples, dynamic radius/opacity tracking water level
- Deer as directional triangles with state labels and motion latent halos
- Butterflies with animated wing flaps and pollination glows
- Oaks with canopy radius shadows
- Grass clusters scaling with growth, tinting with hydration
- Wildflowers pulsing golden during FRUITING
- Dormant plants as faded brown root markers
- Event particles (grazing=green, pollination=gold, death=dark)
- Stats panel (tick, entities, events, fps) + scrolling event log
- All state transitions logged from tick 1
- Session started message on connect
- Entity type inference from ID prefixes
- **☔ Rain button** — sends rainfall control message, visual feedback
- **⏺ Record button** — 10-second canvas capture via MediaRecorder, codec fallback (VP9→VP8→WebM→MP4), auto-download
- Legend with all entity types + water

### Worker

- Combined HTTP + WebSocket on single port
- `process_request` compatible with websockets 13+ (`connection, request` → `Response` objects)
- `SimulationSession` with pause/resume/stop/rain controls
- Control message dispatch table
- Drift-compensated tick loop
- File resolution for viz and world (repo, Docker, env vars)
- CLI headless mode for benchmarking

### Infrastructure

- **Docker Compose** — single command: `docker compose up --build`
- **Dockerfile** — python:3.12-slim, `pip install ".[worker]"`
- **GitHub CI** — pytest (163 tests) + ruff lint, Python 3.11/3.12
- **uv workflow** — `uv sync` for local dev, deterministic lockfile
- **pyproject.toml** — setuptools backend (Docker-compatible), optional dep groups (worker, gateway, dev, all), ruff/pytest/pyright config, script entry points

### Documentation

- **README.md** — positioning (engine, not game), quick start, architecture diagram, BYOM examples, species table, interaction chains, roadmap, controls, contributing note, CI badge
- **DEVELOPING.md** — uv workflow, pip fallback, dependency groups, project layout
- **docs/model_adapter_spec.md** — protocol, context spec, state codes, full worked example, type-specific specs, training/weights, built-in adapter comparison
- **docs/lessons_learned.md** — debugging war stories from the build session
- **deploy/compose/README.md** — Docker quick start with controls

---

## 0.1-Alpha Species Set

Five species, two skeletons, five interaction chains:

| Species      | Type   | Skeleton         | Role                                          |
|--------------|--------|------------------|-----------------------------------------------|
| Deer         | ANIMAL | quadruped_medium | Grazer, seeks grass → flowers → water → mates |
| Butterfly    | INSECT | insect_wing      | Pollinator, seeks flowers → water fallback    |
| Oak          | TREE   | none             | Structure, shade, collapse indicator          |
| Meadow Grass | PLANT  | none             | Ground cover, spreads via runners             |
| Wildflower   | PLANT  | none             | Bloom cycle, pollination target               |

**Interaction chains:**
1. **Grazing** — deer hunger → forages nearest grass → consumption → grass spreads if soil is moist
2. **Pollination** — wildflower FRUITING → butterfly flies to it → pollinates → lingers → seeks next
3. **Water** — thirst → walk to pond → drink → pond level drops → soil dries
4. **Stress cascade** — overgrazing → flowers consumed → butterflies lose food → cluster at ponds → ponds dry → collapse
5. **Dormancy & recovery** — plants die to roots → rain → soil moisture rises → roots revive → flowers bloom → butterflies return

---

## Completed Milestones

### Milestone 0 — Engine Foundation ✅

1. ✅ WebSocket `process_request` fix for websockets 13+
2. ✅ Smoke test imports verified (`ecosim.*`)
3. ✅ Voxel `initialize_from_soil` break bug fixed
4. ✅ Docker build verified end-to-end
5. ✅ Dev requirements (uv + pyproject.toml + lockfile)

### Milestone 1 — v0.0.1-alpha Release ✅

6. ✅ README with positioning, quick start, controls, CI badge
7. ✅ `docs/model_adapter_spec.md` — BYOM guide
8. ✅ GitHub CI (pytest + ruff, Python 3.11/3.12)
9. ✅ Tagged v0.0.1-alpha, repo public

### Bonus — Simulation Tuning ✅

10. ✅ Purposeful movement (food/flower/water/mate seeking)
11. ✅ Water sources with dynamic levels and drought
12. ✅ Plant dormancy and rain-triggered recovery
13. ✅ Rain control (button + WebSocket + engine)
14. ✅ Record button for GIF/video capture
15. ✅ Butterfly pollination lifecycle (linger, cooldown, skip dormant)
16. ✅ Generational decline and reproduction costs
17. ✅ Ecosystem collapse cascade
18. ✅ Rate multipliers for stress testing
19. ✅ World randomization (D4 transforms, jitter, extra plants)
20. ✅ `docs/lessons_learned.md`

### Milestone — ASAL Search Track A ✅

21. ✅ Headless PIL renderer — engine state → 256×256 RGB numpy array
22. ✅ θ parameterization — 17-dim EcoRates (rate multipliers, biome, water, entity counts, rain)
23. ✅ `theta_to_world_config()` — flat vector → valid `demo_world.json` format
24. ✅ `LilaSubstrate` — ASAL Init(θ)/Step/Render protocol wrapping EcosystemEngine
25. ✅ `CLIPEvaluator` — CLIP ViT-B/32 embedding with batched multi-rollout support
26. ✅ Illumination search — diversity-driven GA, farthest-point selection, configurable population/generations
27. ✅ Parallel CPU rollouts via ProcessPoolExecutor (`--workers N`)
28. ✅ Simulation atlas — UMAP projection + grid-sampled thumbnail composite
29. ✅ Diversity curve + embedding scatter visualizations
30. ✅ CLI entry point (`run_illumination.py`) with full arg parsing
31. ✅ Unit tests (test_theta, test_renderer with mock engine) — 23 tests passing
32. ✅ Integration tests (test_substrate, requires ecosim) — 5 tests passing
33. ✅ First illumination run: 64 pop, 100 gen, 2000-tick rollouts, RTX 5060 Ti, ~100 min
34. ✅ Diversity climbed 0.005 → 0.022 (min NN dist), mean NN dist still rising at termination
35. ✅ Atlas shows distinct ecological regimes: drought-stressed, deer explosions, plant-dominated, balanced
36. ✅ README updated with search section, atlas image, roadmap reflects shipped search
37. ✅ `search/` package with own pyproject.toml, uv workflow, .gitignore for results/

---

## Milestone 2: Trait-Based Architecture ✅

**Goal:** Replace per-species hard-coded rules with functional trait derivations. Split the single nutrient layer into fast/slow pools with mineralization. All existing tests must still pass. The hybrid automaton tick loop does not change.

**Motivation:** The current engine encodes ecological knowledge as per-species rules. Every new species requires hand-tuned guard thresholds, interaction logic, and flow equations — O(n²) design effort. The trait-based approach encodes knowledge as allometric scaling laws and interaction templates, making new species a JSON definition rather than new code. This is informed by the Madingley General Ecosystem Model (Harfoot et al. 2014) and the Metabolic Theory of Ecology (Brown et al. 2004).

**Reference documents:** `TRAIT_TRANSITION_PLAN.md` (Phase 1), `TWO_POOL_NUTRIENT_SPEC.md`

### Completed Steps ✅

#### Step 2.1 — Audit Current Hard-Coded Parameters ✅
Extracted every species-specific constant from `engine.py`, `entities.py`, and `biome.py`. Reference table in `TRAIT_TRANSITION_PLAN.md` (Step 1.1). Calibration target for the derivation layer.

**Deliverable:** `ecosim/engine_audit.py`

#### Step 2.2 — Define TraitVector Schema ✅
Dataclass capturing functional traits: body_mass_kg, diet_type, diet_breadth, locomotion, thermoregulation, reproductive_strategy, thermal_range, drought_tolerance, sensory_range_multiplier, spread_mode, root_persistence, etc. A species is a point in trait space.

**Deliverable:** `ecosim/traits.py` (417 lines) — `TraitVector`, `DerivedParams` dataclasses

### Step 2.3 — Allometric Derivation Functions ✅
Pure functions in `ecosim/traits.py` (stdlib only): `TraitVector → DerivedParams`. Core equations:
- Metabolic rate: BMR = B₀ × M^0.75 (endotherm) / M^0.69 (ectotherm) — Kleiber 1932, Gillooly 2001
- Cruising speed: v = v₀ × M^0.25 (terrestrial) / M^0.17 (insect flight) — Peters 1983, Dudley 2000
- Sensory range: ∝ M^0.5 — derived from home range scaling (McNab 1963)
- Flow rates (hunger, thirst, energy): proportional to metabolic rate
- Guard thresholds: hysteresis bands scaled by normalized metabolic rate
- Consumption rate: proportional to metabolic rate

Calibration constants chosen so that deer traits (80 kg, endotherm, quadruped) produce values matching the current hard-coded parameters within 5%.

**Deliverable:** `ecosim/traits.py` — `derive_metabolic_rate()`, `derive_speed()`, `derive_sensory_range()`, `derive_flow_rates()`, `derive_guard_thresholds()`, `derive_consumption_rate()`

### Step 2.4 — Interaction Template Grammar ✅
Four parameterized templates replace per-species-pair code:
- **Herbivory** — actor diet_breadth matches target resource_tags, preference ordering by specificity
- **Predation** — actor diet_breadth matches target functional_group, body mass ratio constraints (0.1–2× for mammalian carnivory, 1–1000× for insectivory)
- **Pollination** — actor floral_affinity matches target pollination_syndrome, target must be FRUITING, linger time + cooldown derived
- **Decomposition** — actor diet_type "decomposer", targets voxel organic_matter layer (unique: interacts with voxels, not entities), mineralization boost factor

Competition is implicit via shared resource depletion. Water access derives from metabolic rate.

**Deliverable:** `ecosim/interactions.py` (343 lines) — `InteractionTemplate` base + 4 concrete templates

### Step 2.5 — TraitCompiler ✅
Runs once at world initialization. Takes list of TraitVectors + BiomeConfig, produces: per-entity DerivedParams, sparse interaction matrix, resource tag registry, flee index, diet preference ordering.

**Deliverable:** `ecosim/trait_compiler.py` (285 lines) — `TraitCompiler`, `CompiledEcology`, `compile_world()`, `parse_species_from_json()`

### Step 2.6 — Two-Pool Nutrient Refactor ✅
Split `nutrients` voxel layer into `nutrients_fast` and `nutrients_slow` (voxel layers 4 → 5):
- **nutrients_fast** (plant-available): quick turnover, depleted by plant growth, refilled by rain and dissolution from slow pool
- **nutrients_slow** (mineralized reserve): long-term soil health, fed by decomposition of organic_matter, slowly dissolves into fast pool
- **organic_matter** (existing): dead entity biomass deposited here on death, converted to slow nutrients via mineralization

New per-tick fluxes in voxel effects phase:
- Mineralization: organic_matter → nutrients_slow (rate 0.002/tick, accelerated by decomposer entities)
- Dissolution: nutrients_slow → nutrients_fast (rate 0.005/tick)
- Leaching: nutrients_fast drains slowly (rate 0.001/tick)

Updated touchpoints: rain split (0.020 fast + 0.004 slow), dormancy recovery uses weighted effective nutrients (fast + slow × 0.3), plant spreading checks fast pool only, entity death deposits biomass to organic_matter layer.

Three new rate multipliers: `mineralization`, `dissolution`, `nutrient_leaching` (all default 1.0).

**Deliverable:** Refactored `ecosim/voxel_manager.py` (316 lines) — 5 layers (`"nutrients_fast", "nutrients_slow", "moisture", "temperature", "organic_matter"`), legacy alias `"nutrients"` → `"nutrients_fast"`, inter-pool fluxes via `NutrientPoolDynamicsHandler`. `ecosim/world_processes.py` (399 lines) — NutrientPoolDynamicsHandler + existing handlers. All 163 tests pass.

### Step 2.7 — Refactor engine.py ✅
Replaced `if entity["type"] ==` branches with DerivedParams lookups via `self.compiled.*`. Engine dispatches on functional role (consumer/producer/decomposer), never on entity class:
```
if params.diet_type == "autotroph":     → _flow_producer
elif params.diet_type == "decomposer":  → _flow_decomposer
else:                                   → _flow_consumer
```
All numeric constants the tick loop uses come from DerivedParams. Only 1 remaining `entity["type"]` reference (spatial hash TODO, not species dispatch).

All worlds must include a ``species_definitions`` key. Worlds without it
will fail at init with a clear error — there is no fallback.

**Deliverable:** Refactored `ecosim/engine.py` (1772 lines) — reads from `self.compiled.derived_params`, `self.compiled.get_interactions()`, `self.compiled.get_diet_order()`, `self.compiled.get_flee_targets()`

### Step 2.8 — Write Trait Vectors for All Species ✅
All **eight species** defined as trait vectors in JSON:
- Original five: deer, butterfly, oak, meadow_grass, wildflower
- Three new (Phase 2): wolf, songbird, mushroom

When compiled, produce parameters matching the Step 2.1 audit within 5%.

**Deliverable:** `examples/species_definitions.json` — 8 species trait vectors. `demo_world.json` updated with `species_definitions` key.

### Test Suite ✅
- **163 tests passing** across `test_actors.py` (70) + `test_ecosim.py` (12) + `test_traits.py` (54) + `test_movement_actor.py` (36) + `test_voxel_grid.py` (28) + `test_nutrients.py` (~20) + `test_reproduction_actor.py` (~15)
- Interaction template tests: herbivory matching/preference, predation with mass ratios, pollination with linger/cooldown, decomposition mineralization boost
- Compiler tests: derived params for all species, interaction matrix population, flee index (empty for 5sp, populated with wolf), diet preferences, decomposer registry
- JSON parsing: parse_species_from_json, missing key handling, full definitions file
- Two-pool nutrient flow tests: mineralization, dissolution, leaching fluxes via NutrientPoolDynamicsHandler

### Step 2.9 — Calibration & Regression Testing ⏸️ Shelved
Shelved in favor of scalability work (distributed engine, spatial hash, octree voxels). Revisit after distributed architecture is stable.
- [ ] Compare DerivedParams output against audit table (manual verification)
- [ ] `tests/test_nutrients.py` — two-pool nutrient flow tests (blocked on Step 2.6)
- [ ] `tests/test_regression.py` — 2000-tick baseline comparison
- [ ] Population curves, state transitions, event counts within ±10–15% of baseline

### Milestone 2 Deliverables
**Shipped:**
- `ecosim/traits.py` — TraitVector, DerivedParams, allometric derivation functions (417 lines)
- `ecosim/interactions.py` — InteractionTemplate base + 4 concrete templates (343 lines)
- `ecosim/trait_compiler.py` — TraitCompiler class (285 lines)
- Refactored `engine.py` — reads from DerivedParams, dispatches on functional role (~737 lines after decomposition)
- `ecosim/config.py` — SIM_CONFIG loader with JSON override support (136 lines)
- `ecosim/environment_manager.py` — Environment state encapsulation (biome, climate, voxels, water sources, rain)
- Refactored `voxel_manager.py` — 5 layers (nutrients_fast/slow, moisture, temp, OM), legacy alias support (316 lines)
- Updated `world_processes.py` — NutrientPoolDynamicsHandler + existing handlers (399 lines)
- `config/sim_config.json` + `config/biomes.json` — external override files
- `examples/species_definitions.json` — 8 species trait vectors
- Updated `examples/demo_world.json` — includes `species_definitions`, updated rates, butterfly species rename
- `tests/test_actors.py` — 70 tests for EffectBus, effect priority, conflict resolution
- `tests/test_traits.py` — 54 tests for derivations, templates, compiler, backward compat
- `tests/test_nutrients.py` — two-pool nutrient flow tests (mineralization, dissolution, leaching)
- `tests/test_reproduction_actor.py` — reproduction actor behavior tests

**Shelved (scalability first):**
- `tests/test_regression.py` — 2000-tick baseline comparison
- `docs/trait_species_guide.md` — how to add species via trait vectors

**New files: 7 (config.py, environment_manager.py, config/sim_config.json, config/biomes.json, test_nutrients.py, test_reproduction_actor.py, species_definitions.json). Modified files: 6. No new external dependencies.**

---

## Milestone 3: Actor Effects Architecture ✅ (Phase 1 Complete)

**Goal:** Extract entity↔entity interactions from the monolithic engine into an actor-based system with immutable effects, enabling parallel execution, deterministic replay, and network transport.

### Completed Steps ✅

#### Step 3.1 — Effect Dataclasses + EffectBus ✅
All simulation effects defined as frozen dataclasses in `ecosim/effects.py` (339 lines):
- **StateVarDelta** — increment/decrement a state variable
- **SetStateVar** — set to absolute value
- **StateTransition** — change discrete state (FORAGING, FLEEING, DYING...)
- **VoxelDelta / VoxelBatchDelta** — environmental changes
- **SpawnEntity / RemoveEntity** — entity lifecycle
- **LingerEffect / ClearTarget / SetTarget** — behavior modifiers
- **EventRecord** — simulation events for client broadcast

**EffectBus** (`apply_batch()`) collects all effects from all actors, sorts by priority (terminal operations first), resolves conflicts (removed entities skip remaining effects), and applies atomically in a single pass.

Priority order: REMOVE_ENTITY → STATE_TRANSITION → SET_STATE_VAR → LINGER/CLEAR_TARGET/SET_TARGET → STATE_VAR_DELTA → VOXEL → SPAWN_ENTITY → EVENT_RECORD.

**Deliverable:** `ecosim/effects.py` (339 lines)

#### Step 3.2 — Actor Protocol + Context ✅
Base classes in `ecosim/actors/__init__.py` (229 lines):
- **InteractionContext** — frozen dataclass with read-only snapshot: tick, entity, voxel_grid, biome, compiled ecology, params, nearby_entities, water_sources, climate, rate_multipliers
- **InteractionActor** — abstract base class with `resolve(ctx) → list[Effect]` protocol
- **FlowActor / GuardActor** — subtypes for Phase 2 ✅ (implemented)
- **build_interaction_registry(compiled)** — maps species names to actor instances from the compiled ecology

**Deliverable:** `ecosim/actors/__init__.py` (387 lines — Phase 1 + Phase 2: FlowContext/GuardContext, registries, builders)

#### Step 3.3 — Interaction Actors ✅
Four interaction actors in `ecosim/actors/interaction_actors.py` (554 lines):
- **FleeActor** — detects predators via flee_targets from interaction matrix, emits StateTransition(FLEEING) + SetTarget(escape_pos)
- **PredationActor** — detects prey proximity within PREDATION_CATCH_DISTANCE, emits StateVarDelta(hunger/energy for predator), SetStateVar(health=0.0) + RemoveEntity(prey), VoxelDelta(organic_matter deposit), EventRecord(PREDATION)
- **HerbivoryActor** — detects plants in FORAGING range with hunger > threshold, emits StateVarDelta(hunger relief for herbivore), SetStateVar(growth/health reduction for plant), EventRecord(CONSUMPTION)
- **PollinationActor** — detects FRUITING flowers within pollinator range, emits SetStateVar(health boost for flower), StateVarDelta(hunger/hydration relief for pollinator), LingerEffect + ClearTarget(pollinator), SetStateVar(_pollination_cooldown for flower), EventRecord(POLLINATION)

All actors are pure functions: read-only context → list of effects. No side effects during actor execution.

**Deliverable:** `ecosim/actors/interaction_actors.py` (554 lines)

#### Step 3.4 — Engine Integration + Dual-Path Architecture ✅
The engine's step() method uses an **actor-based architecture**:

**Trait path** (all worlds require `species_definitions`):
- Phase 1 flow: flow_actor_registry[species].resolve(ctx) → EffectBus.apply_flow_batch()
- Phase 2 interactions: actor_registry[species].resolve(ctx) → EffectBus.apply_batch()
- Phase 3 guards: guard_actor_registry[species].resolve(ctx) → EffectBus.apply_effects_with_om_deposit()
- Phase 4 voxel effects: engine emits SoilDrain/SoilDeposit intents → handlers via EffectBus
- Phase 5 world processes: engine emits SoilEvaporation/WaterReplenish intents → handlers via EffectBus

All worlds must include a ``species_definitions`` key. Worlds without it
will fail at init with a clear error — there is no fallback.

**Deliverable:** Refactored `ecosim/engine.py` (2353 lines)

#### Step 3.5 — Legacy Guard/Flow Removal ✅
The legacy inline functions (_flow_animal, _flow_plant, etc.) were removed in favor of the actor system. All worlds now require `species_definitions` and use trait-based actors exclusively.

### Test Suite ✅
- **163 tests passing** across `test_actors.py` (70) + `test_ecosim.py` (12) + `test_traits.py` (54) + `test_movement_actor.py` (36) + `test_voxel_grid.py` (28)
- Smoke test shows state variables evolving correctly for both trait and legacy worlds
- Bee colony transitions to FORAGING, events fire, entities move toward targets

### Milestone 3 Phase 1 Deliverables ✅
**Shipped:**
- `ecosim/effects.py` — Effect dataclasses + EffectBus (547 lines after Phase 2 additions)
- `ecosim/actors/__init__.py` — InteractionContext, InteractionActor base, FlowActor/GuardActor subtypes, registries, builders (387 lines)
- `ecosim/actors/interaction_actors.py` — FleeActor, PredationActor, HerbivoryActor, PollinationActor (554 lines)
- Refactored `engine.py` — actor-based architecture: trait-based actors for all phases, decomposed into focused modules (782 lines after extraction)

### Milestone 3 Phase 2 Deliverables ✅
**Shipped:**
- `ecosim/actors/flow_actors.py` — ConsumerFlowActor, ProducerFlowActor, DecomposerFlowActor (577 lines)
- `ecosim/actors/guard_actors.py` — ConsumerGuardActor, ProducerGuardActor, DecomposerGuardActor (624 lines)
- New effect: `DepositOrganicMatter` — organic matter deposition on entity death
- EffectBus additions: `apply_flow_batch()`, `apply_effects_with_om_deposit()`
- Engine step(): flow/guard/interaction actors used for all worlds; world-process handlers dispatched through EffectBus

**New files: 2. Modified files: 3. No new external dependencies.**

### Engine Decomposition ✅
The monolithic engine has been decomposed into focused modules:
- **LayoutManager** (`layout.py`, 306 lines) — entity initialization, water source parsing, grid bounds calculation, full randomization pipeline (D4 transforms, jitter, extra spawns, push-from-water)
- **SpatialIndex** (`spatial_index.py`, 169 lines) — strategy interface (SpatialIndex Protocol + BruteForceSpatialIndex) for neighbor queries; pluggable for future spatial hash swap
- **MovementSystem** (`movement_system.py`, 138 lines) — movement gate policy and kinematics. Public API: `step(entity, params, dt)`
- **MovementActor** (`actors/movement_actors.py`, 492 lines) — target selection as pure-function actor emitting SetTarget/ClearTarget effects
- **Dead code removal** — ~347 lines of deprecated movement logic stripped from engine

**Result:** `engine.py` reduced from ~1338 → 782 lines. Test suite: 94 → 163 tests.

---

## Recent Changes (Post Phase 2)

### Simulation Config Loader (`ecosim/config.py`, 136 lines) ✅
Extracted all tunable simulation parameters from hardcoded values in actors, interactions, engine, and world processes into a structured config system. `SIM_CONFIG` is loaded at import time from defaults, overridable via `config/sim_config.json`.

**Config domains:** consumer_physiology, plant_physiology, soil_dynamics, decomposer_physiology, movement, reproduction, interactions (mass ratio windows, pollination params), engine_defaults.

**Deliverable:** `ecosim/config.py` (136 lines) — `load_sim_config()`, `_deep_merge()`, `SIM_CONFIG` singleton. `config/sim_config.json` and `config/biomes.json` for external overrides.

### Environment Manager (`ecosim/environment_manager.py`, ~180 lines) ✅
Encapsulates the physical environment of an ecosystem: biome config, climate state, voxel grid, water source management. Provides unified interface for environmental updates (rain, evaporation, water distribution). LayoutManager is called internally via `load_layout()`.

**Deliverable:** `ecosim/environment_manager.py` — `EnvironmentManager` class with `load_layout()`, `add_water_source()`, `apply_rain()` methods. Rain now splits nutrient boost between fast/slow pools.

### Constants Module (`ecosim/constants.py`, 158 lines) ✅
Extracted all numeric simulation constants from `engine.py` and actor files into a single source of truth module. Every constant used by the engine — drinking rates, reproductive thresholds, plant physiology, pollination distances, water physics, rain parameters, soil evaporation — now lives in one place. No module defines its own copies.

**Deliverable:** `ecosim/constants.py` (158 lines) — 60+ constants organized by domain (drinking, reproduction, stress, plant physiology, spreading, dormancy, collapse, pollination, predation, movement, dispersal, child inheritance, water, rain with fast/slow nutrient splits, soil evaporation, organic matter, decomposition, mineralization, dissolution, leaching)

### SetEntityAttr Effect Type ✅
New effect type for entity-level attributes that live on the entity dict rather than in `state_vars`. Used for internal tracking variables like `_pollination_cooldown`, `_pollination_visits`, `_wander_cooldown`.

**Deliverable:** `ecosim/effects.py` — `SetEntityAttr` dataclass + EffectBus handler (same priority as SetStateVar)

### Pollinator Dispersal Mechanics ✅
The pollination actor now enforces realistic dispersal behavior:
- **Per-flower visitor cap** (`POLLINATOR_MAX_PER_FLOWER = 5`) — prevents unlimited clustering at a single flower
- **Visit limit** (`POLLINATOR_VISIT_LIMIT = 4`) — after N consecutive visits, pollinator enters forced WANDERING exploration
- **Wander cooldown** (`POLLINATOR_WANDER_COOLDOWN = 30`) — ticks to wander before re-entering FORAGING
- **Crowd radius** (`POLLINATOR_CROWD_RADIUS = 2.5`) — radius to count "at flower" pollinators for cap enforcement
- **Post-visit cooldown** (`POLLINATOR_POST_VISIT_COOLDOWN = 15`) — ticks after linger ends before re-pollination is allowed; prevents immediate re-pollination at the same or adjacent flowers
- **Physical proximity check** — pollinator must arrive within `POLLINATION_VISIT_DISTANCE` (2.0) to actually pollinate; nearby_entities includes all flowers in sensory range but only close ones are visited

### Actor Registry Improvements ✅
- `InteractionActorRegistry` now maps species IDs to *lists* of actors (was single actor per entity). A species can have multiple interaction actors (e.g., FleeActor + HerbivoryActor for deer).
- `FlowContext` and `GuardContext` gained `_get_params` callable — allows actors to query DerivedParams for other entities by species_id.

### Engine Refactoring ✅
- Constants extracted from engine.py into `constants.py` module (engine.py reduced from ~2465 → 1338 lines)
- All actor files import constants from the shared module instead of defining local copies
- Ruff linting resolved: StrEnum migration, unused imports removed, import sort order fixed

### Engine Decomposition ✅
The monolithic engine has been decomposed into focused modules:
- **LayoutManager** (`layout.py`, 306 lines) — entity initialization, water source parsing, grid bounds calculation, full randomization pipeline (D4 transforms, jitter, extra spawns, push-from-water). Extracted ~130 lines from engine.
- **SpatialIndex** (`spatial_index.py`, 169 lines) — strategy interface (SpatialIndex Protocol + BruteForceSpatialIndex) for neighbor queries. Pluggable for future spatial hash swap. Includes canonical `distance_2d()` helper. Extracted ~40 lines from engine.
- **MovementSystem** (`movement_system.py`, 138 lines) — movement gate policy (linger/cooldown decrements, ACTIVE_MOVEMENT_STATES check, pollinator exception) and kinematics (_move_toward_target). Public API: `step(entity, params, dt)`. Extracted ~59 lines from engine.
- **MovementActor** (`actors/movement_actors.py`, 492 lines) — target selection extracted from engine into pure-function actor emitting SetTarget/ClearTarget effects. Priority chain: swarming → drinking → mate-seeking → foraging → hunting → idle pollinator → wander. Integrated into ConsumerFlowActor.resolve().
- **Dead code removal** — ~347 lines of deprecated movement logic stripped from engine (_pick_movement_target, _find_nearest_food_by_preference, _find_nearest_prey, etc.) superseded by MovementActor.

**Result:** `engine.py` reduced from ~2465 → 782 lines. All 163 tests pass.

### Test Suite ✅
- **163 tests passing** across `test_actors.py` (70) + `test_ecosim.py` (12) + `test_traits.py` (54) + `test_movement_actor.py` (36) + `test_voxel_grid.py` (28)
- New pollinator dispersal tests: per-flower cap, visit limit enforcement, wander cooldown, post-visit cooldown
- SetEntityAttr effect application tests

### World-Process Handlers (`ecosim/world_processes.py`, 211 lines) ✅
Extracted inline engine methods into pluggable handlers dispatched through EffectBus at their own frequencies. Each handler implements `WorldProcessHandler` and declares which effect types it consumes.

**Handlers:**
- **SoilEvaporationHandler** — evaporates soil moisture based on climate conditions, uses `walk_layer()` for sparse iteration (replaces O(grid²) full scan)
- **WaterReplenishHandler** — updates water source levels and soil moisture footprints using `query_overlap()` for footprint-aware cell queries
- **SoilDrainHandler** — entity-driven nutrient/moisture uptake; distributes drain across all cells under entity footprint when `radius` is set, falls back to single-cell without it
- **SoilDepositHandler** — decomposer OM→nutrient conversion and death deposits; same radius-aware distribution as SoilDrain

**Effect types:**
- `SoilEvaporation`, `WaterReplenish`, `SoilDrain`, `SoilDeposit` — world-process intents emitted by engine, resolved by handlers through EffectBus
- `SoilDrain` and `SoilDeposit` carry optional `radius: float | None` field for footprint-aware distribution

**Engine integration:**
- Phase 4 (voxel effects): engine emits SoilDrain/SoilDeposit intents with entity position + amount; handlers resolve overlap using grid's `query_overlap()`
- Phase 5 (world processes): engine emits SoilEvaporation and WaterReplenish intents; handlers run at configurable frequencies via EffectBus
- Autotroph effects now pass `canopy_radius` as drain footprint radius — trees drain from all cells under their canopy, not just one cell
- Rain system uses `walk_layer()` for soil moisture/nutrient boosts (skips empty regions)
- Water source initialization uses `query_overlap()` for footprint seeding

**Deliverable:** `ecosim/world_processes.py` (211 lines) — 4 handlers + registration helper. All handlers depend on VoxelGrid protocol, not concrete classes.

### VoxelGrid Protocol (`ecosim/voxel_manager.py`, 287 lines) ✅
Abstracts voxel storage strategy via `VoxelGrid` Protocol so that engine and handler code can work with uniform grids today and swap in an octree later without changing call sites. Implements **phases 0-2** of [issue #64](https://github.com/hellolifeforms/lila/issues/64).

**Phase 0 — Protocol definition:**
- `VoxelGrid` Protocol defines: `get()`, `set()`, `add()`, `world_to_grid()`, `query_overlap()`, `walk_layer()`, `get_delta_packet()`
- Renamed current class to `UniformVoxelGrid`; kept `VoxelManager` as backward-compat alias
- Added `_distance_3d_sq()` helper for squared Euclidean distance

**Phase 1 — Engine adoption (behavior-preserving refactor):**
- `query_overlap(center, radius)` — finds all grid cells whose centers fall within a spherical region using bounding-box early-out; used by SoilDrain/Deposit handlers and water footprint updates
- `walk_layer(layer, callback)` — sparse iteration over existing cells only; replaces O(grid²) full walks in evaporation and rainfall handlers
- All engine touchpoints updated: autotroph drain uses canopy_radius as footprint, rain uses walk_layer(), water init uses query_overlap()

**Phase 2 — Effect-based world processes (already done via #63/#65):**
- Handlers already dispatch through EffectBus at their own frequencies
- SoilDrain/SoilDeposit effects carry optional radius field for footprint-aware distribution
- All handlers depend on VoxelGrid protocol, not concrete class

**Test suite:** `test_voxel_grid.py` (28 tests) covering:
- Protocol conformance (isinstance checks, required attributes/methods)
- `query_overlap()` correctness (bounding box early-out, cell center distance, edge clamping, different cell sizes)
- `walk_layer()` sparsity (only visits existing cells, skips defaults)
- Backward compatibility (`VoxelManager` alias works identically)
- Handler integration (drain with/without radius, deposit with radius, evaporation walk_layer usage, rain suppression)

**Remaining work:** [Issue #68](https://github.com/hellolifeforms/lila/issues/68) tracks phases 3-4:
- Phase 3: `OctreeVoxelGrid` implementation (~300-500 lines, same protocol interface)
- Phase 4: Refinement policy (configurable triggers, periodic coarsening)

---

## In Progress — Hybrid Model + Simulation (Milestone 4)

**Goal:** Extend BYOM from motor inference to full phase-level pluggability. The engine can run in pure physics mode to generate training data, then swap learned models into individual simulation phases.

**Epic:** [#80](https://github.com/hellolifeforms/lila/issues/80) — Extend BYOM from motor inference to full phase-level pluggability

### Architecture Vision

```
Phase 1  Flow          ──► Physics OR LearnedFlowAdapter
Phase 2  Interactions  ──► Templates OR LearnedInteraction
Phase 3  Guards        ──► Thresholds OR BehaviorAdapter (#20)
Phase 4  Voxel FX      ──► Handlers OR VoxelAdapter (#79)
Phase 5  Water/Soil    ──► Handlers OR WorldAdapter
Phase 6  Motor         ──► MotorAdapter (already implemented)
Phase 7  Spawn/Kill    ──► EffectBus (always physics for now)
```

Each phase has a **physics default** and an optional **model override**. The engine dispatches to whichever is registered. Physics always runs in "pure" mode for data generation.

### Phase Plan + Issues

| # | Issue | Role |
|---|-------|------|
| [#77](https://github.com/hellolifeforms/lila/issues/77) | Telemetry emitter: config snapshot + time-series aggregates + event batching | Foundation — three-stream data collection |
| [#78](https://github.com/hellolifeforms/lila/issues/78) | Surrogate model + sensitivity analysis over sim_config parameter space | Understanding — which of ~48 tunable params actually matter |
| [#79](https://github.com/hellolifeforms/lila/issues/79) | Learned diffusion: replace or gate voxel nutrient diffusion with a model | First replacement — O(N×4) → O(1) forward pass |
| [#20](https://github.com/hellolifeforms/lila/issues/20) | Behavior-level adapter | Guard augmentation — learned threshold bias per entity |
| [#21](https://github.com/hellolifeforms/lila/issues/21) | Narrative-level adapter | Macro intelligence — ecosystem-scale event injection |

### Telemetry Streams (from #77)

Three streams feed the training pipeline:

1. **Config snapshot** (once per run): biome, merged sim_config, world rates, species count, grid dimensions, seed
2. **Time-series aggregates** (every K ticks): per-species mean/std of state vars, voxel layer statistics (mean/min/max/var), event window counts, population counts by species/state
3. **Event log** (batched): existing EventRecord stream with tick ranges

### Parameter Space Summary (~299 constants)

| Layer | Source | Count | Trainable? |
|-------|--------|-------|------------|
| Physics exponents | `traits.py` | ~35 | No — biological priors (0.75, 0.69) |
| Threshold constants | `constants.py` | ~94 | Stable inputs — changing them changes behavior semantics |
| Tunable knobs | `sim_config.json` | ~38 | **Primary targets** — multipliers, buffers, cooldowns |
| Biome modifiers | `biomes.json` | ~80 (4×20) | Conditional inputs gated by biome ID |
| Derived params | trait compiler output | ~50 × N_species | Indirectly via traits |
| World rates | world JSON `rates` key | 10 | **Exposed levers** — already tunable per-world |

**Actionable parameter space: ~48–58 dimensions.**

### Concrete Use Cases

- **Surrogate model for parameter search** — predict ecosystem outcomes from config without running full sim. Enables Bayesian optimization over sim_config.
- **Learned diffusion (first phase replacement)** — U-Net predicts voxel delta over one diffusion period, or gates physics diffusion when gradients are flat.
- **Sensitivity analysis / effective dimensionality** — Sobol screening + random forest feature importance to identify which params actually matter vs. dead weight.
- **Early-warning predictor** — sliding window of telemetry → predict ecosystem collapse K ticks ahead. Enables adaptive tick rate.

### Design Principles

1. Physics is the ground truth — models augment or approximate, never replace physics as training data source
2. Each phase is independently swappable — swap diffusion without touching flow, guards, or interactions
3. Telemetry is cheap by default — <5% overhead on tick loop, aggregates over raw per-entity data
4. BYOM protocol consistency — all learned adapters follow MotorAdapter pattern: context spec → flat float vectors → predictions

**Reference doc:** [`docs/ECOSIM_PARAMETER_TELEMETRY_SPACE.md`](docs/ECOSIM_PARAMETER_TELEMETRY_SPACE.md)

---

## ⏸️ Shelved — Milestone 3: Emergent Dynamics Validation + Trait-Based Search

Shelved in favor of scalability work. The distributed engine architecture is now the priority.

**Goal:** Validate the trait architecture with long-running simulations of all 8 species. Expand the ASAL search pipeline from rate-tuning (Track A, shipped) to trait-based search (Track B).

**Dependencies:** Milestone 2 must complete (two-pool nutrients). Track A search infrastructure is complete and stable.

**Reference documents:** `TRAIT_TRANSITION_PLAN.md` (Phases 2–3)

### Actor Effects Architecture — Phase 2 Complete ✅
The actor system is complete across all three phases:
- [x] ConsumerFlowActor, ProducerFlowActor, DecomposerFlowActor — continuous state evolution as effect-emitting actors
- [x] ConsumerGuardActor, ProducerGuardActor, DecomposerGuardActor — discrete state transitions as effect-emitting actors
- [x] Engine step() uses flow/guard/interaction actors for all worlds; world-process handlers dispatched through EffectBus

### New Species — Trait Vectors Defined ✅, Validation Pending ❌

All three species are defined in `examples/species_definitions.json`. Interaction templates match correctly per unit tests. Long-running simulation validation pending.

**Wolf** — completes the food chain: grass → deer → wolf. diet_type: carnivore, diet_breadth: ["herbivore"], body_mass_kg: 40. Predation template matches wolf→deer automatically. Deer flee response triggers from carnivore detection. Expected emergent dynamic: Lotka-Volterra oscillations, trophic cascade (wolves reduce deer → grass recovers).

**Songbird** — new trophic niche: insectivore + frugivore. diet_breadth: ["pollinator", "forb:fruiting"], body_mass_kg: 0.025. Tests insectivory mass-ratio window (predator >> prey, unlike mammalian predation). Expected: songbird-butterfly predation reduces pollination pressure.

**Mushroom** — closes the nutrient loop. diet_type: decomposer, targets organic_matter voxel layer. r_selected (clutch_size 5, fast generation). Accelerates mineralization rate locally. Expected: measurably faster soil recovery near decomposer clusters, 3–4× reduction in ecosystem recovery time after collapse events.

### ⏸️ Shelved — Emergent Dynamics Validation
Shelved. Revisit after distributed engine is stable.
With 8 species, run 10,000-tick simulations documenting which interaction chains emerge without being coded:
- [ ] Wolf-deer predation with population oscillations
- [ ] Trophic cascade: wolves reduce deer → grass recovers → wildflowers bloom
- [ ] Songbird-butterfly predation reducing pollination rates
- [ ] Mushroom decomposition accelerating soil recovery after death events
- [ ] Cross-trophic competition: songbirds and butterflies competing for fruiting flowers
- [ ] Thermal range exclusions in extreme biome settings

### ⏸️ Shelved — Trait-Based Search (Track B)
Shelved. Revisit after distributed engine is stable.

Expand the shipped search pipeline from 17-dim rate tuning to trait-space search:

**θ expansion** — `theta.py` grows to encode trait vectors (body masses, diet types, thermal tolerances, locomotion modes) alongside the existing rate/biome dimensions. `theta_to_world_config()` emits `species_definitions` for the trait compiler.

**Three θ variants:**
- **EcoRates** (~17 dimensions, shipped) — rate multipliers + biome. "What tuning produces interesting dynamics?"
- **EcoTopology** (~50–80 dimensions) — rates + species composition + trait vectors. "What organisms produce interesting ecologies?"
- **EcoAdapt** (~550–600 dimensions) — topology + MLP adapter weights. "What learned behaviors produce the most lifelike dynamics?"

**Target search** — CMA-ES optimization toward text prompts via CLIP text embedding. Warm-start from illumination results.

**Open-ended search** — maximize temporal novelty in CLIP embedding space over long rollouts. Find ecosystems that don't reach equilibrium.

**Physical plausibility constraints** — square-cube law, thermal homeostasis limits, trophic sanity checks on θ.

### Milestone 3 Deliverables (Shelved)
All items shelved until after scalability work.
- Three new species as JSON trait vectors (zero engine code) ✅ shipped, validation pending
- Updated interaction templates with parameterized mass-ratio windows ✅ shipped
- `examples/temperate_meadow_8sp.json` — 8-species trait-based world ✅ shipped
- Emergent dynamics validation report ⏸️ shelved
- Expanded `theta.py` with EcoTopology and EcoAdapt variants ⏸️ shelved
- `lila_search/target.py` — CMA-ES target search ⏸️ shelved
- `lila_search/open_ended.py` — temporal novelty search ⏸️ shelved
- `docs/asal_substrate_guide.md` ⏸️ shelved

---

## Active Work — Distributed Simulation Engine (Phase 1 In Progress)

**Goal:** Scale the simulation beyond a single engine instance by partitioning the world into spatially separate **tiles** that communicate via efficient message passing. Target: 5×5 tile grid, each running a 32×32 voxel grid with up to 50 initial entities.

**Reference doc:** [`docs/DISTRIBUTED_ENGINE_ARCHITECTURE.md`](docs/DISTRIBUTED_ENGINE_ARCHITECTURE.md)

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    WorldOrchestrator                         │
│                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                │
│  │ Tile(0,0) │   │ Tile(0,1) │   │ Tile(0,2) │   ...         │
│  │ Engine   │   │ Engine   │   │ Engine   │                  │
│  │ 32×32    │   │ 32×32    │   │ 32×32    │                  │
│  │ ≤50 ents │   │ ≤50 ents │   │ ≤50 ents │                  │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘                  │
│       │              │              │                         │
│       └──────────┬───┴──────────────┘                         │
│                  │  Cross-tile messages (shared memory)        │
│                  ▼                                             │
│           Message Bus                                         │
│    MigrationMessage, GhostUpdate, GlobalEvent                 │
└─────────────────────────────────────────────────────────────┘
```

### Completed ✅

| Step | Description | Deliverable |
|------|-------------|-------------|
| 1.1 | Message types and config dataclasses | `distributed/messages.py`, `distributed/config.py` |
| 1.2 | Tile class with ghost injection/removal, migration detection | `distributed/tile.py` (350+ lines) |
| 1.3 | WorldOrchestrator tick sync + message routing + packet assembly | `distributed/orchestrator.py` (450+ lines) |
| 1.4 | TileWorldLayout for per-tile config generation from master spec | `distributed/world_layout.py` (200+ lines) |
| 1.5 | Package init with public API exports | `distributed/__init__.py` |
| 1.6 | Test suite: config, messages, ghost IDs, layout partitioning, coordinate mapping, ghost mirroring | `tests/test_distributed.py` (32 tests) |

### Key Design Decisions

- **Tile** — wraps one `EcosystemEngine`, adds boundary awareness via ghost injection/removal and migration detection. Zero modifications to the core engine.
- **Ghost entities** — read-only replicas of boundary entities injected into neighbor tiles' spatial indexes. Marked with `_ghost: true` prefix IDs (`ghost:{row}:{col}:{eid}`). Excluded from tick packets.
- **Migration** — detected after each tick when entity positions exceed grid bounds. Applied by orchestrator (remove from source, insert into target with remapped local coordinates).
- **Global ↔ local coordinates** — entities use local coords within tiles; orchestrator maps to global for client rendering and migration routing.
- **World layout generation** — `TileWorldLayout` partitions master spec entities/water sources across tiles by global position.

### Remaining Phase 1 Work

| Step | Description |
|------|-------------|
| 1.7 | Integration tests with real EcosystemEngine instances (ghost injection during tick, migration across tiles) |
| 1.8 | Demo script: 5×5 tile world with entity migration visualization |
| 1.9 | Browser visualizer updates for global coordinate rendering and tile boundary display |

### Phase 2: Multi-Node Preparation (Future)
- Message serialization format — compact binary protocol
- Network transport abstraction — swap shared memory for TCP/UDP
- Latency compensation — speculative execution at boundaries

### Phase 3: Multi-Node Deployment (Future)
- Distributed orchestrator with node assignment
- Fault tolerance — engine restart with state replay from effect log
- Dynamic rebalancing when population density shifts

**Dependencies:**
- Spatial hash (SpatialIndex strategy swap) — needed for efficient boundary queries
- OctreeVoxelGrid (issue #68, phases 3–4) — sparse storage reduces message volume at boundaries

---

## Pending — Milestone 4: Godot Client + Trained Motion Model

**Goal:** 3D visualization of trait-based ecosystems with latent-driven skeletal animation. Built against the stable trait-based engine from Milestone 2, not the current hand-coded species.

**Why deferred:** The Godot client should be built against the trait-based engine, not the current per-species architecture — building it now means refactoring it after the trait transition. The engine work (Milestones 2–3) generates more interesting content (trophic cascades, FM-discovered ecosystems) than the Godot client would at this stage. The browser visualizer is sufficient for validating all near-term work.

### 3D Assets

1. **Blender models** — low-poly faceted deer (200–400 faces, quadruped_medium rig), butterfly (<50 faces, insect_wing rig), oak tree, grass clump, wildflower. Flat shading, no smoothing. Asset pipeline documented in `LILA_ASSET_PIPELINE_CONTEXT.md`.

### Godot Project

2. **Project scaffolding** — project.godot, autoloads (session_manager, skeleton_registry, event_bus), base_entity.gd.
3. **WebSocket + tick receiver** — connect to worker, parse packets (5 voxel layers), dispatch to subsystems.
4. **Position interpolation** — smooth between 10Hz ticks at 60fps render.
5. **Motion retargeter** — latent vector → bone transforms via per-bone weight matrix. `R_final(bone) = R_base + Σ(latent[i] × W[bone][i])`. This is the thesis demo.
6. **Voxel renderer** — ImageTexture3D for moisture layer, ground plane shader. Optional soil health overlay (nutrients_slow as subtle gradient).
7. **Event particles** — CONSUMPTION (leaf burst), POLLINATION (golden trail), RAIN (droplets).
8. **Water rendering** — shader-based pond with dynamic radius from tick packets.

### Trained Motion Model

9. **Motion data acquisition** — source animation clips for deer locomotion (walk, trot, graze, drink, rest) and butterfly flight (cruise, hover, land).
10. **Feature extraction** — `training/scripts/extract_features.py`: animation clips → context→motion training pairs.
11. **Training** — `training/scripts/train.py`: PyTorch training loop targeting the MLP architecture (10→16→12→8→4). Context spec from trait-based engine (richer context vectors than v0.0.1).
12. **Evaluation** — `training/scripts/evaluate.py`: latent space visualization, reconstruction quality.
13. **Weight export** — `training/scripts/export_weights.py`: PyTorch → ecosim JSON format.
14. **Integration** — load trained weights in demo world, compare against static/random adapters.

### Server

15. **Minimal gateway** — FastAPI, accepts WS connections, proxies to worker. `SessionOrchestrator` protocol + `LocalOrchestrator`. Proves multi-session architecture.

---

## Future (v0.2+)

### Ecosystem Richness
- Reproduction with genetic variation (trait inheritance with mutation)
- Seasonal cycles — temperature/rainfall oscillations driving phenology
- Multiple biome presets (desert, arctic, tropical) with biome-specific trait constraints
- L-systems for procedural plant clusters and terrain objects

### Engine Scaling
- Behavior-level adapter (ML-influenced guard conditions via trait context)
- Narrative-level adapter (ecosystem-scale intelligence, event injection)
- Bounded fields for dense actor clusters (insect swarms, grass patches)
- Tick-rate/bandwidth optimization

**Active work (promoted from Future):**
- Spatial hash for O(1) neighbor queries (SpatialIndex strategy swap) — currently brute-force O(n²)
- OctreeVoxelGrid implementation (issue #68, phases 3–4) — same VoxelGrid protocol interface
- Distributed simulation engine — spatially partitioned engines with efficient message passing; single-node multi-engine first

### ASAL Extensions
- Video-language FM evaluation (temporal dynamics without frame sampling)
- 3D FM evaluation (via Godot renderer output)
- Substrate contribution to ASAL codebase (JAX port of core dynamics, or Python bridge)
- Cross-substrate comparison: līlā vs Boids vs Lenia on same ASAL search objectives
- Automated trait vector discovery — FM-guided search for novel functional groups

### World Building
- Scene editor UI — click to place entities, drag sliders for rates and traits
- Trait database import from PanTHERIA (mammals), TRY (plants), EltonTraits (diet/foraging)

### Deployment
- Cloud-agnostic orchestration (ECS/K8s/Fly adapters)
- Multi-session gateway with Redis session state
- Spectator mode — read-only WebSocket for observers

---

## Key Gotchas (see docs/lessons_learned.md for details)

1. WebSocket `process_request` signature varies between websockets versions — check return types first.
2. `elif` chains in Python are exclusive — combine guard conditions with `and` to avoid blocking downstream branches.
3. Entities must seek targets purposefully, not wander randomly.
4. Pollination needs cooldowns on flowers, not memory on insects.
5. Children must inherit parent stress or populations become immortal.
6. Reproductive drive needs a dead zone between build and decay conditions.
7. Rain must work at multiple levels (soil, plants, water sources, evaporation suppression) or it's too weak.
8. Plants should go dormant, not die — root persistence enables recovery.
9. World randomization must be JSON-driven and opt-in.
10. Don't use hatchling in Docker — setuptools is pre-installed everywhere.
11. **(New)** Allometric scaling laws are well-validated for animals but weaker for plants. Keep plant-specific traits (spread_range, spread_mode, root_persistence) as explicit fields rather than deriving from body mass.
12. **(New)** The two-pool nutrient split must preserve `initialize_from_soil` correctness — all five layers initialized. The original break bug (skipped layers 2–3) was from an `elif` chain; verify with 5 layers.

---

## Design Decisions (Locked)

- **BYOM adapter architecture.** Engine accepts adapters via dict. Three built-in (mlp, static, random). Custom adapters implement `MotorAdapter` protocol.
- **Model level hierarchy.** Motor (implemented), Behavior (reserved), Narrative (reserved).
- **ecosim is stdlib-only.** Zero external dependencies in the core package. Trait system, interactions, and trait compiler use only stdlib math + dataclasses.
- **Docker Compose is the primary path.** Clone, compose up, open browser.
- **Skeleton mapping is client-side.** Server sends `skeleton_id` + motion latent. Client owns rigs.
- **Voxel layers: 5.** nutrients_fast, nutrients_slow, moisture, temperature, organic_matter. Updated from 4 → 5 per two-pool nutrient decision.
- **Motion latent dimensions: 4.** Expandable later.
- **Grid: 32³ default, cell_size 1.0.**
- **Tick rate: ~100ms (10 Hz).**
- **Randomization is opt-in via JSON.** No `"randomize"` key = deterministic positions.
- **Plants go dormant, not dead.** Root persistence is ecologically accurate and enables recovery narratives.
- **Solo creative project.** Contributions welcome, creative direction maintained by author.
- **(New) Trait-based species architecture.** Species defined as functional trait vectors in JSON. Engine derives behavior parameters from allometric scaling laws. Interaction templates handle combinatorics. Per-species engine code is a legacy path.
- **(New) Two-pool nutrient system.** Fast pool (plant-available, quick turnover) + slow pool (mineralized reserve, long-term soil health). Mineralization, dissolution, and leaching fluxes run per tick. Decomposer entities accelerate mineralization locally.
- **(New) ASAL substrate compatibility.** Engine exposes Init/Step/Render protocol for FM-guided search over trait space. search/ package has its own dependencies (torch, clip, cma); ecosim core stays clean.
- **(New) Engine-first priority.** Godot client deferred until trait-based engine is stable. Browser visualizer sufficient for validating trait system, two-pool nutrients, and ASAL search.

---

## Planning Documents

- **TRAIT_TRANSITION_PLAN.md** — Detailed implementation plan for the Phase 1–3 transition from hand-crafted rules to trait-based architecture with ASAL substrate integration. Includes TraitVector schema, allometric derivation functions, interaction template grammar, TraitCompiler design, engine refactor sequence, calibration strategy, and ASAL search loop implementations.

- **TWO_POOL_NUTRIENT_SPEC.md** — Implementation spec for the two-pool nutrient system. Covers pool dynamics equations, rate constants, voxel manager changes (4→5 layers), every engine touchpoint that reads/writes nutrients, rain split ratios, dormancy recovery update, death→organic_matter deposits, timescale analysis for three recovery scenarios, test plan, and backward compatibility.

- **LILA_ASSET_PIPELINE_CONTEXT.md** — AI-generated 3D asset pipeline research. Covers Flux.1 Schnell → BiRefNet → Hunyuan 3D v2.1 pipeline, deer mesh prototype results, rigging plan. Relevant to Milestone 4 (Godot client).

- **docs/ECOSIM_PARAMETER_TELEMETRY_SPACE.md** — Hybrid model + simulation architecture. Covers telemetry streams (config snapshot, time-series aggregates, event batching), parameter space stratification (~299 constants → ~48 actionable), concrete use cases (surrogate model, learned diffusion, sensitivity analysis, early-warning predictor), and five-phase implementation plan with issue tracking.

---

## Key References

**Ecological theory:**
- Kleiber, M. (1932). Body size and metabolism. *Hilgardia*. — BMR = B₀ × M^0.75
- Brown, J.H. et al. (2004). Toward a metabolic theory of ecology. *Ecology*. — Metabolic Theory of Ecology
- Gillooly, J.F. et al. (2001). Effects of size and temperature on metabolic rate. *Science*. — Ectotherm scaling exponent 0.69
- Peters, R.H. (1983). *The Ecological Implications of Body Size*. Cambridge. — Movement speed scaling
- Harfoot, M.B.J. et al. (2014). Emergent global patterns from a mechanistic general ecosystem model. *PLoS Biology*. — The Madingley Model

**ALife/search:**
- Kumar, A. et al. (2024). Automating the Search for Artificial Life with Foundation Models. *Artificial Life* (MIT Press). — ASAL framework: FM-guided search across ALife substrates
- Project page: https://asal.sakana.ai/ — Code: https://github.com/SakanaAI/asal

**Allometric scaling:**
- Hirt, M.R. et al. (2017). A general scaling law reveals why the largest animals are not the fastest. *Nature Ecology & Evolution*. — Hump-shaped speed-mass relationship
- McNab, B.K. (1963). Bioenergetics and the determination of home range size. *American Naturalist*. — Home range ∝ M^0.75
- Dudley, R. (2000). *The Biomechanics of Insect Flight*. Princeton. — Insect flight speed scaling

---

## Project Links

- **GitHub:** https://github.com/hellolifeforms/lila
- **Essay:** https://www.hellolifeforms.com/p/the-unseen-hand
- **Series:** "The Geometry Beneath" on www.hellolifeforms.com
- **Bluesky:** https://bsky.app/profile/hellolifeforms.bsky.social
- **līlā concept:** https://www.embodiedphilosophy.com/what-is-lila/
- **ASAL:** https://asal.sakana.ai/
- **Madingley Model:** https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.1001841

---

## Performance

Worker benchmarks (23 entities, 32³ grid):
- Step time: 0.60–0.83ms per tick
- Throughput: 1200–1680 Hz (120× headroom above 10Hz target)
- Browser viz: 60fps with tick interpolation
- Docker image: ~50MB (python:3.12-slim + websockets)

**Performance note for Milestone 2:** The trait compiler runs once at init, not per tick. Per-tick lookups into DerivedParams are dict access — O(1). Two-pool nutrient fluxes add three multiply-and-clamp operations per active voxel cell per tick. Expected impact: negligible relative to the ~1ms step time budget.
