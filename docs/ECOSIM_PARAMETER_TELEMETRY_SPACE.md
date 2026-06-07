# Ecosim Parameter Space & Telemetry for Hybrid Model + Simulation

## Vision: The Engine as a BYOM Pluggable System

Lila is already BYOM at the **motor level** — learned models produce motion latents that drive skeletal animation. The goal is to extend this so *any simulation phase* can be replaced or augmented by a learned model, creating a hybrid architecture where physics and learning coexist:

```
┌─────────────────────────────────────────────────────────────────┐
│                    EcosystemEngine.step()                       │
│                                                                 │
│  Phase 1  Flow          ──► Physics OR LearnedFlowAdapter       │
│  Phase 2  Interactions  ──► Templates OR LearnedInteraction     │
│  Phase 3  Guards        ──► Thresholds OR BehaviorAdapter       │
│  Phase 4  Voxel FX      ──► Handlers OR LearnedVoxelAdapter     │
│  Phase 5  Water/Soil    ──► Handlers OR LearnedWorldAdapter     │
│  Phase 6  Motor         ──► MotorAdapter (already BYOM)         │
│  Phase 7  Spawn/Kill    ──► EffectBus (always physics)          │
└─────────────────────────────────────────────────────────────────┘
```

Each phase has a **physics default** and an optional **model override**. The engine dispatches to whichever is registered. This means:

- You can run the full physics sim to *generate training data*
- Train a model on that data
- Swap in the learned model for one or more phases
- Run faster, at different resolution, or with emergent behaviors the physics alone doesn't produce

---

## The Parameter Space (~299 Constants)

Not all constants are equal as inputs to models. They fall into layers:

| Layer | Source | Count | Role in Training |
|-------|--------|-------|------------------|
| Physics exponents | `traits.py` | ~35 | Fixed biological priors (0.75, 0.69) — **not trainable** |
| Threshold constants | `constants.py` | ~94 | Guard boundaries — **stable inputs**, changing them changes behavior semantics |
| Tunable knobs | `sim_config.json` | ~38 | Multipliers, buffers, cooldowns — **primary training targets** |
| Biome modifiers | `biomes.json` | ~80 (4×20) | Per-biome scaling — **conditional inputs** gated by biome ID |
| Derived params | trait compiler output | ~50 × N_species | Output of allometric derivation — **indirectly trainable via traits** |
| World rates | world JSON `rates` key | 10 | Runtime multipliers — **exposed levers**, already tunable per-world |

**The actionable parameter space is ~48–58 dimensions** (38 sim_config + 10 world rates + a handful of threshold constants). The rest are fixed physics or derived.

### Detailed Inventory by Source

#### `constants.py` — World-level physics (~94)

Single source of truth for universal simulation physics:

- **Drinking & hydration (3):** recovery rate, soil drain, water drain
- **Near-water survival bonus (2):** hunger relief factor, colony recovery factor
- **Reproductive drive conditions (6):** min energy/hunger/health to build, decay thresholds, mate-seek trigger
- **Critical stress thresholds (4):** starvation hunger, dehydration hydration, colony stress hunger/energy
- **Plant physiology (6):** base water demand, soil uptake rate, growth rate, default nutrient demand, critical hydration/nutrients
- **Plant spreading requirements (8):** min health/hydration/growth, soil moisture/nutrients, density radius, parent costs
- **Dormancy recovery (1):** exit health threshold
- **Ecosystem collapse (3):** support threshold, health/hydration multipliers
- **Pollination (2):** health boost, max linger ticks
- **Predation & herbivory distances (7):** flee trigger, catch distance, consume distance, visit distance, min hunger, escape distance, carnivore hunt hunger
- **Movement (3):** arrival threshold, wander range, pollinator critical hunger
- **Pollinator dispersal (5):** max per flower, visit limit, wander cooldown, crowd radius, post-visit cooldown
- **Child entity inheritance (8):** hunger/energy/colony/health floors + inherit factors, spawn offset
- **Water source physics (5):** evaporation rate, replenish rate, moisture target, refill rate, dry threshold
- **Rain (11):** moisture boost, nutrient fast/slow boosts, water source boost, suppression ticks, plant hydration/health, animal hydration, repro recovery ticks/multiplier
- **Soil evaporation (4):** base rate, temp scale, humidity factor, moisture floor
- **Organic matter deposit (3):** scale, min, max
- **Two-pool nutrient dynamics (4):** mineralization rate, dissolution rate, leach rate, decomp efficiency
- **Active state frozensets (3):** movement states, energy drain states, recovery states

#### `traits.py` — Allometric calibration (~35)

Calibration constants feeding the allometric derivation pipeline:

- **Metabolic base:** B0_ENDOTHERM, B0_ECTOTHERM, REFERENCE_METABOLIC_RATE
- **Flow rate fractions (6):** hunger, thirst, energy drain/recovery, repro build/decay, ectotherm water factor
- **Health drain fractions (3):** starving, dehydrated, nutrient
- **Speed bases (3):** terrestrial, insect, bird
- **Sensory base (1)**
- **Interaction relief fractions (5):** herbivory, predation, pollination, predation energy, consumption growth/health damage
- **Guard adjustment (3):** scale, max, min
- **Floor values (12):** hunger rate, thirst rate, energy drain/recovery, repro build, sensory range, consumption, herbivory/predation/pollination relief, health drain

Plus `TraitVector` (~24 fields with defaults) and `DerivedParams` (~50 derived fields).

#### `sim_config.json` — Tunable JSON config (~38)

Loaded at runtime via `config.py`, with baked-in defaults:

- **consumer_physiology (5):** temp normalization, near-water buffer, water drain multiplier, swarm entry/exit thresholds
- **plant_physiology (7):** spread offspring params, evapotranspiration temp norm, slow nutrient weight, dormancy hydration floor
- **soil_dynamics (1):** nutrient diffusion toggle
- **decomposer_physiology (4):** active population threshold, blooming OM/population thresholds, dormant activity threshold
- **movement (7):** grid max default, mate min distance, food growth viability, water source distances, wander margin, arrival double
- **reproduction (1):** colony health repro cost factor
- **interactions (~12):** mass ratio windows (4 diet types), pollination linger/cooldown, herbivory/predation multipliers, capture probability params, metabolic rate floor, linger exponent, decomposition boost min/scale, speed coefficients (3 locomotion types)
- **engine_defaults (1):** default dt

#### `biomes.json` — Biome presets (~80 = 4 × ~20)

Four biomes (TROPICAL, TEMPERATE, ARCTIC, DESERT), each with:

- Metabolism modifiers (3): hunger rate, energy drain, metabolic scaling
- Water cycle (2): evaporation rate, rainfall recharge
- Plant growth (3): growth rate modifier, light availability
- Soil dynamics (2): decomposition rate, nutrient diffusion rate
- Microbial (1): microbial activity modifier
- Dormancy thresholds (7): consumer moisture wake, plant recovery health/hydration multipliers + floor, decomposer smoothing/growth/decay rates
- Water physics (2): soil dry rate outside footprint, soil moisture floor outside water

#### `entities.py` — State definitions (~26)

State frozensets per entity type (ANIMAL: 8 states, PLANT: 5, INSECT: 9, MICROORGANISM: 3), default state vars per type, and initial states.

#### `effects.py` — Effect system (~15)

EffectType enum (13 types) + EFFECT_PRIORITY map (12 levels).

#### `voxel_manager.py` — Grid constants (~7)

LAYERS tuple (nutrients_fast, nutrients_slow, moisture, temperature, organic_matter), DEFAULT_VALUE = 1.0, DIRTY_THRESHOLD = 0.05, nutrient split ratio fast=40%/slow=60%.

#### Inline / small constants (~4)

`world_processes.py`: DIFFUSION_PERIOD = 5, NUTRIENT_DIFFUSION_ENABLED toggle. `layout.py`: _margin = 0.5, water source default radius = 2.0, state var noise ±0.05.

---

## Telemetry Architecture

Three streams feed the training pipeline:

### Stream 1: Config Snapshot (logged once per run)

```json
{
  "run_id": "...",
  "biome": "TEMPERATE",
  "sim_config_merged": { ... },
  "world_rates": { ... },
  "species_count": 12,
  "entity_count_initial": 45,
  "grid_dimensions": [32, 32, 32],
  "seed": 42
}
```

### Stream 2: Time-Series Aggregates (sampled every K ticks)

Not per-entity — that's O(entities × ticks) and mostly steady-state drift. Instead:

- **Per-species aggregates:** mean/std of hunger, energy, hydration, health, reproductive_drive, colony_health
- **Voxel grid statistics:** mean, min, max, spatial variance per layer (not individual cells)
- **Event counts in windows:** deaths, reproductions, predations, pollinations per K-tick window
- **Population counts** by species and state

### Stream 3: Event Log (already exists via `EventRecord`)

Batch with tick ranges. Already structured as `{type, source_id, target_id, position, tick}`.

---

## Concrete Use Cases

### A. Surrogate Model for Parameter Search

*Goal:* predict ecosystem outcomes from config without running the full sim.

- **Inputs:** ~48 tunable params + species trait vectors + biome ID
- **Targets:** aggregate metrics at T=1000, T=5000, T=10000 (biodiversity index, time-to-collapse, carrying capacity)
- **Training data:** 1000–10000 runs via Latin hypercube / Sobol sampling over the tunable space
- **Model:** MLP or Gaussian Process — input space is small enough

Enables Bayesian optimization over sim_config to find configs producing interesting dynamics without burning compute on the engine.

### B. Learned Diffusion (Phase 4 Replacement)

*Goal:* replace or approximate the O(N_cells × 4) diffusion step with a learned forward pass, or use it as an adaptive gate.

- **Inputs:** voxel grid snapshot at tick t (5 layers, 32³ cells — treat as small 3D image)
- **Targets:** voxel grid delta over one diffusion period (t → t+5)
- **Model:** U-Net or small 3D CNN; learn the *delta* since diffusion is a small perturbation
- **Training data:** enable diffusion, log voxel snapshots every 5 ticks across biomes

**Two modes of use:**

1. **Replacement:** learned model predicts next-state delta directly — O(1) forward pass vs. O(N_cells × 4) neighbor lookups
2. **Gating (more practical):** learned predictor estimates |delta|. If below ε, skip the actual diffusion step entirely. This gives adaptive frequency — run diffusion only when gradients are steep (after rain events, decomposition blooms).

### C. Sensitivity Analysis / Effective Dimensionality

*Goal:* find which of ~48 tunable params actually matter vs. dead weight.

- Run Sobol or Morris screening over the tunable space
- Train random forest on config → outcome, extract feature importances
- Result: know which constants to expose as user knobs and which can be frozen

Many sim_config params may only matter in edge cases (e.g., `colony_swarm_exit_threshold` only matters when insect colony_health drops below 0.35). Sensitivity analysis tells you what's actually leveraged.

### D. Early-Warning Predictor

*Goal:* predict ecosystem collapse or regime shift K ticks ahead.

- **Inputs:** sliding window of telemetry (aggregated state vars + event rates over last W ticks)
- **Targets:** binary — collapse within K ticks?
- **Model:** 1D CNN, Temporal Fusion Transformer, or logistic regression on hand-crafted features

Useful for adaptive simulation: if collapse is predicted, increase tick rate to capture dynamics faithfully; if steady-state, skip phases or reduce frequency.

### E. Behavior Adapter (Phase 3 Augmentation) — maps to existing issue #20

*Goal:* learned model biases guard condition thresholds based on full entity context.

- **Inputs:** entity state vars + nearby entity summary + biome
- **Targets:** threshold adjustments (e.g., hunger_enter shifted by ±Δ)
- **Training data:** log guard transitions from physics runs, learn when the deterministic thresholds produce suboptimal behavior

This is the BEHAVIOR adapter level already defined in `model_adapter.py`.

---

## Phase Plan

### Phase 1: Telemetry Infrastructure (foundation for everything)

Build the data collection layer so all downstream use cases share it.

- Telemetry emitter module with three streams (config snapshot, time-series aggregates, event log)
- Configurable sampling interval K and aggregation functions
- Output to parquet/JSONL for easy loading into training pipelines
- Batch runner: sample configs via Sobol/Latin hypercube, run N sims, collect all telemetry

### Phase 2: Surrogate Model + Sensitivity Analysis (immediate value)

Train on Phase 1 data to understand the parameter space.

- Train surrogate model predicting ecosystem outcomes from config
- Run sensitivity analysis to identify effective dimensionality
- Use results to prune sim_config down to the params that actually matter
- Bayesian optimization loop: propose configs → run physics → update surrogate → repeat

### Phase 3: Learned Diffusion (first phase replacement)

Replace or gate the most expensive world-process handler.

- Enable diffusion in physics, collect voxel snapshots as training data
- Train U-Net / CNN to predict diffusion delta over one period
- Implement `LearnedDiffusionAdapter` plugging into Phase 4
- Benchmark: learned vs. physics diffusion accuracy and speedup
- Adaptive gating mode: skip physics diffusion when learned model predicts |delta| < ε

### Phase 4: Behavior Adapter (maps to issue #20)

Augment guard conditions with learned bias.

- Implement BEHAVIOR adapter protocol in engine (already defined as placeholder in `model_adapter.py`)
- Train on guard transition data from physics runs
- Learned model produces threshold adjustments per entity per tick
- Benchmark: does learned behavior produce more interesting dynamics than deterministic thresholds?

### Phase 5: Narrative Adapter + Early Warning (maps to issue #21)

Macro-level intelligence operating at ecosystem scale.

- Implement NARRATIVE adapter protocol in engine (already defined as placeholder)
- Train early-warning predictor on time-series telemetry
- Narrative model can inject events, modulate climate, or adjust parameters
- Operates at slower cadence (every N ticks), shapes emergent dynamics over long horizons

---

## Key Thresholds for Monitoring

These constants from `constants.py` and `sim_config.json` define the "danger zones" that telemetry should flag:

| Constant | Value | Meaning |
|----------|-------|---------|
| STARVATION_HUNGER | 0.8 | Individual health drain begins |
| DEHYDRATION_HYDRATION | 0.15 | Individual dehydration stress |
| COLLAPSE_SUPPORT_THRESHOLD | 2 | Ecosystem collapse trigger (tree support count) |
| COLONY_STRESS_HUNGER | 0.7 | Colony health starts draining |
| colony_swarm_entry_threshold | 0.3 | Insect enters SWARMING behavior |
| colony_swarm_exit_threshold | 0.35 | Insect exits SWARMING |
| DORMANCY_RECOVERY_EXIT_HEALTH | 0.2 | Plant exits dormancy |
| CARNIVORE_HUNT_HUNGER | 0.5 | Carnivore switches to HUNTING |

---

## Design Principles

1. **Physics is the ground truth.** Models augment or approximate — they don't replace physics as the source of training data. The engine always runs in "pure physics" mode for data generation.

2. **Each phase is independently swappable.** You can swap diffusion without touching flow, guards, or interactions. This keeps experiments isolated and composable.

3. **Telemetry is cheap by default.** Aggregates over raw per-entity data. Voxel statistics over individual cells. The emitter should add <5% overhead to the tick loop.

4. **BYOM protocol consistency.** All learned adapters follow the same pattern as `MotorAdapter`: declare a context spec, receive flat float vectors, return predictions. No engine internals leak into model code.
