<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā — Trait-Based Architecture Transition Plan

> From hand-crafted species rules to allometrically-derived ecological dynamics,
> with an ASAL-compatible substrate protocol for FM-guided ecosystem search.
>
> **Status:** Phase 1 complete ✅. Phases 2–3 (emergent dynamics validation, trait-based
> search) are ⏸️ shelved until after the distributed engine scalability work.

---

## Why This Transition

The current engine encodes ecological knowledge as **per-species rules**. Every species
has hand-tuned guard thresholds, hard-coded interaction logic, and type-specific flow
equations. This worked for five species and twenty tuning milestones, but the cost of
a sixth species is not additive — it's multiplicative, because every new species
potentially interacts with every existing one. The engine needs O(n²) *design effort*
per species, which is intractable.

The solution, validated by the Madingley General Ecosystem Model and the Metabolic
Theory of Ecology, is to encode knowledge as **functional traits** and **derive** the
rules from well-established allometric scaling laws. A species becomes a point in
trait space; the engine computes its behavior from first principles.

This transition also enables ASAL-style automated search (Akarsh Kumar et al., 2024).
Once the engine accepts parameterized trait vectors, the search space is biologically
meaningful — searching over body masses, diet types, and thermal tolerances, not
arbitrary rate multipliers. Foundation models evaluate rendered simulation output for
open-endedness, target phenomena, or diversity, and evolutionary search optimizes the
trait configurations that produce the most interesting ecological dynamics.

---

## Phase 1 — Trait Derivation Layer (Refactor, No Rewrite)

**Goal:** Express the existing five species as trait vectors. Build a derivation layer
that produces the *same* engine parameters currently hard-coded. All 12 existing tests
must still pass. The hybrid automaton tick loop does not change.

### Step 1.1 — Audit Current Hard-Coded Parameters

Walk through `engine.py`, `entities.py`, and `biome.py` and extract every species-specific
constant into a reference table. This is the target output of the derivation layer.

**Per-entity-type parameters to extract:**

| Parameter | Current Location | Example (Deer) | Example (Butterfly) |
|-----------|-----------------|----------------|---------------------|
| Guard: hunger_enter | engine.py guards | 0.3 | 0.3 |
| Guard: hunger_exit | engine.py guards | 0.15 | 0.15 |
| Guard: hydration_enter | engine.py guards | 0.2 | — |
| Guard: hydration_exit | engine.py guards | 0.6 | — |
| Guard: energy_enter | engine.py guards | 0.2 | 0.15 |
| Guard: energy_exit | engine.py guards | 0.5 | 0.4 |
| Guard: repro_drive_threshold | engine.py guards | 0.8 | 0.7 |
| Flow: hunger_rate | engine.py flow | per-tick Δ | per-tick Δ |
| Flow: thirst_rate | engine.py flow | per-tick Δ | — |
| Flow: energy_decay | engine.py flow | per-tick Δ | per-tick Δ |
| Flow: repro_drive_build | engine.py flow | per-tick Δ | per-tick Δ |
| Movement: speed | engine.py movement | units/tick | units/tick |
| Movement: sensory_range | engine.py movement | grid units | grid units |
| Interaction: consumption_rate | engine.py interactions | per-event Δ | — |
| Interaction: diet_targets | engine.py interactions | [grass, wildflower] | [wildflower:FRUITING] |
| Interaction: flee_from | engine.py interactions | [carnivore types] | — |
| Plant: spread_range | engine.py spawns | 2.0 (grass) | 3.5 (flower) |
| Plant: spread_frequency | engine.py spawns | high (grass) | low (flower) |
| Plant: dormancy_recovery_moisture | engine.py guards | 0.25 | 0.25 |
| Plant: dormancy_recovery_nutrients | engine.py guards | 0.15 | 0.15 |
| Plant: fruiting_growth_threshold | engine.py guards | 0.5 | 0.5 |

**Deliverable:** A Python dict or dataclass per species containing every parameter the
engine currently reads from `if entity_type ==` branches. This is the "ground truth"
the derivation layer must reproduce.

### Step 1.2 — Define the Trait Schema

Each species is described by a trait vector in JSON. The traits are chosen to be
(a) biologically meaningful, (b) measurable from real-world databases, and
(c) sufficient to derive all parameters from Step 1.1.

```python
@dataclass
class TraitVector:
    """Functional traits for a species. All derivations flow from these."""

    # === Identity ===
    species_id: str              # "deer", "butterfly", "oak", etc.
    functional_group: str        # "herbivore", "pollinator", "producer", "decomposer"
    entity_class: str            # maps to current EntityType: ANIMAL, INSECT, PLANT, TREE

    # === Body Plan ===
    body_mass_kg: float          # THE key trait. Most rates derive from this.
    locomotion: str              # "quadruped", "flight_insect", "sessile", "rooted"
    skeleton_id: str | None      # "quadruped_medium", "insect_wing", None

    # === Metabolism ===
    thermoregulation: str        # "endotherm", "ectotherm", "autotroph"
    mass_specific_bmr: float | None  # override if known; otherwise derived from body_mass

    # === Diet & Trophic ===
    diet_type: str               # "herbivore", "nectarivore", "carnivore", "omnivore",
                                 # "autotroph", "decomposer"
    diet_breadth: list[str]      # resource tags consumed: ["graminoid", "forb"],
                                 # ["forb:fruiting"], ["herbivore_medium"]
    trophic_level: float         # 1.0 = producer, 2.0 = primary consumer, 3.0 = predator

    # === Reproduction ===
    reproductive_strategy: str   # "K_selected", "r_selected"
    clutch_size: int             # offspring per reproduction event
    generation_time_ticks: int   # minimum ticks between reproduction events

    # === Environmental Tolerances ===
    thermal_range: tuple[float, float]   # (min_C, max_C) — viable temperature range
    drought_tolerance: float     # 0.0 (needs constant water) to 1.0 (desert-adapted)
    shade_tolerance: float       # 0.0 (full sun) to 1.0 (understory specialist)

    # === Sensory & Movement ===
    sensory_range_multiplier: float  # 1.0 = default for body size; >1 = enhanced
    movement_budget: float       # fraction of energy allocated to movement (0–1)

    # === Plant-Specific (ignored for animals) ===
    spread_mode: str | None      # "runner", "seed_wind", "seed_animal", None
    spread_range: float | None   # max spread distance in grid units
    root_persistence: bool       # True = goes dormant instead of dying
    canopy_radius: float | None  # shade footprint for trees
```

### Step 1.3 — Write the Allometric Derivation Functions

These are pure functions: `TraitVector → DerivedParams`. They live in a new file
`ecosim/traits.py`. They use **no external dependencies** (stdlib math only, honoring
the ecosim constraint).

**Core allometric equations to implement:**

```python
import math

# === Metabolic Rate (Kleiber's Law) ===
# BMR = B0 * M^0.75
# B0 ≈ 70 for mammals in kcal/day, but we normalize to per-tick rates.
# The exponent 0.75 is the consensus value (Kleiber 1932, Brown et al. 2004).
# For ectotherms, use 0.69 (Gillooly et al. 2001).

def derive_metabolic_rate(mass_kg: float, thermoregulation: str) -> float:
    """Returns normalized metabolic rate (arbitrary units, per tick)."""
    exponent = 0.75 if thermoregulation == "endotherm" else 0.69
    # B0 chosen so that an 80kg deer ≈ current engine hunger_rate
    return B0_NORMALIZED * (mass_kg ** exponent)


# === Movement Speed ===
# Cruising speed scales as M^0.25 for terrestrial animals (Peters 1983).
# Maximum speed has a hump-shaped relationship (Hirt et al. 2017), but
# cruising/foraging speed is well-approximated by the power law.
# Insects: flight speed scales as M^0.17 (Dudley 2000).

def derive_speed(mass_kg: float, locomotion: str) -> float:
    """Returns movement speed in grid units per tick."""
    if locomotion == "flight_insect":
        return SPEED_BASE_INSECT * (mass_kg ** 0.17)
    elif locomotion in ("quadruped", "biped"):
        return SPEED_BASE_TERRESTRIAL * (mass_kg ** 0.25)
    else:  # sessile, rooted
        return 0.0


# === Sensory Range ===
# Scales with home range, which scales as M^0.75 (McNab 1963) to M^1.0
# (Kelt & Van Vuren 2001). We use M^0.5 as a moderate estimate for
# sensory detection range (not home range) within our 32³ grid.

def derive_sensory_range(mass_kg: float, multiplier: float) -> float:
    """Returns detection radius in grid units."""
    return SENSORY_BASE * (mass_kg ** 0.5) * multiplier


# === Hunger / Thirst / Energy Rates ===
# All consumption rates scale with metabolic rate.
# Hunger rate = metabolic_rate * hunger_fraction
# Thirst rate = metabolic_rate * water_fraction (endotherms need more water)

def derive_flow_rates(metabolic_rate: float, traits: 'TraitVector') -> dict:
    """Returns per-tick flow deltas for hunger, thirst, energy."""
    hunger_rate = metabolic_rate * HUNGER_METABOLIC_FRACTION
    thirst_rate = metabolic_rate * WATER_METABOLIC_FRACTION
    if traits.thermoregulation == "ectotherm":
        thirst_rate *= 0.3  # ectotherms lose less water
    energy_decay = metabolic_rate * ENERGY_METABOLIC_FRACTION
    return {
        "hunger_rate": hunger_rate,
        "thirst_rate": thirst_rate,
        "energy_decay": energy_decay,
    }


# === Guard Thresholds ===
# Hysteresis bands scale inversely with metabolic rate — smaller/faster
# metabolisms hit thresholds sooner (tighter margins).
# Enter threshold = base_enter * (1 + metabolic_adjustment)
# Exit threshold  = base_exit  * (1 - metabolic_adjustment)
# where metabolic_adjustment compresses bands for high-metabolism species.

def derive_guard_thresholds(metabolic_rate: float, traits: 'TraitVector') -> dict:
    """Returns hysteresis enter/exit pairs for each guard condition."""
    # Normalize metabolic rate relative to reference species (deer = 1.0)
    m_norm = metabolic_rate / REFERENCE_METABOLIC_RATE
    adjustment = min(0.15, 0.1 * math.log(m_norm + 0.01) + 0.1)

    if traits.reproductive_strategy == "r_selected":
        repro_threshold = 0.7  # lower bar, reproduce more readily
    else:
        repro_threshold = 0.8  # K-selected, higher bar

    return {
        "hunger_enter": 0.3 * (1 + adjustment),
        "hunger_exit": 0.15 * (1 - adjustment),
        "hydration_enter": 0.2,
        "hydration_exit": 0.6,
        "energy_enter": 0.2 * (1 + adjustment),
        "energy_exit": 0.5 * (1 - adjustment),
        "repro_drive_threshold": repro_threshold,
    }


# === Consumption Rate ===
# How much resource an entity consumes per feeding event.
# Scales with metabolic rate — larger animals eat more per bite.

def derive_consumption_rate(metabolic_rate: float) -> float:
    return metabolic_rate * CONSUMPTION_METABOLIC_FRACTION
```

**Calibration constants** (`B0_NORMALIZED`, `SPEED_BASE_TERRESTRIAL`, etc.) are chosen
so that when you plug in deer traits (80 kg, endotherm, quadruped), the derivation
produces values matching the current hard-coded parameters from Step 1.1. This is the
"same dynamics" guarantee. The constants live in `ecosim/traits.py` as module-level
values with comments explaining the calibration.

### Step 1.4 — Define the Interaction Template Grammar

Replace per-species-pair interaction code with a small set of parameterized templates.

```python
@dataclass
class InteractionTemplate:
    """A class of ecological interaction, parameterized by trait compatibility."""
    interaction_type: str   # "herbivory", "predation", "pollination",
                            # "competition", "mutualism", "decomposition"

    def matches(self, actor: TraitVector, target: TraitVector) -> bool:
        """Does this interaction apply between actor and target?"""
        ...

    def compute_rates(self, actor: TraitVector, target: TraitVector) -> dict:
        """Returns interaction-specific parameters (consumption, linger time, etc.)."""
        ...
```

**Six templates cover the current five interaction chains plus future expansions:**

**Herbivory** — Actor: `diet_type in (herbivore, omnivore)`. Target: `entity_class in (PLANT, TREE)`.
Match condition: any tag in actor's `diet_breadth` matches target's resource tags
(`graminoid` for grass, `forb` for wildflower, `mast` for oak acorns).
Derived params: consumption rate (from actor metabolic rate), preference ordering
(match specificity — `graminoid` before `forb` for a grazer with both in diet_breadth).

**Predation** — Actor: `diet_type in (carnivore, omnivore)`. Target: any tag in
actor's `diet_breadth` matches target's functional group. Additional constraint:
body mass ratio between 0.1× and 2× (predators don't take prey much larger than
themselves). Derived params: capture probability (speed ratio), consumption rate,
flee trigger on target.

**Pollination** — Actor: `diet_type == nectarivore` or `pollinator` in functional roles.
Target: PLANT with `pollination_syndrome` matching actor's `floral_affinity`.
Match condition: target must be in FRUITING state (growth ≥ threshold, health > threshold).
Derived params: linger time (inversely proportional to actor metabolic rate),
cooldown on target (prevents re-pollination).

**Competition** — Implicit. When two entities share `diet_breadth` tags and forage
in the same area, resource depletion creates competition without explicit code.
The engine already handles this through resource tracking — no template needed,
just ensure resource tags are checked consistently.

**Water Access** — All mobile entities with `thermoregulation != autotroph` seek
water when hydration drops below threshold. This is already trait-derivable
(thirst rate from metabolic rate), not a species-specific interaction.

**Decomposition** — Actor: `diet_type == decomposer`. Target: dead organic matter.
Converts dead entity biomass into soil nutrients. Template params derived from
actor metabolic rate.

### Step 1.5 — Build the Trait Compiler

The `TraitCompiler` runs once at world initialization. It takes the list of
`TraitVector` objects from the world JSON and produces:

1. **Per-entity derived params** — a `DerivedParams` dataclass for each entity,
   containing all the values the tick loop needs (guard thresholds, flow rates,
   speed, sensory range, consumption rate).

2. **Interaction matrix** — for each entity pair, which interaction templates
   apply and with what parameters. Stored as a sparse structure (most pairs
   don't interact). This replaces the current `if entity_type ==` dispatch in
   the interaction phase.

3. **Resource tag registry** — maps plant species to their resource tags,
   so the herbivory template can match diet_breadth against available food.

```python
class TraitCompiler:
    """Compiles trait vectors into engine-ready parameters."""

    def __init__(self, trait_vectors: list[TraitVector], biome: BiomeConfig):
        self.traits = {tv.species_id: tv for tv in trait_vectors}
        self.biome = biome

    def compile(self) -> CompiledEcology:
        """Returns all derived parameters for the engine."""
        derived = {}
        for sid, tv in self.traits.items():
            metabolic = derive_metabolic_rate(tv.body_mass_kg, tv.thermoregulation)
            derived[sid] = DerivedParams(
                metabolic_rate=metabolic,
                speed=derive_speed(tv.body_mass_kg, tv.locomotion),
                sensory_range=derive_sensory_range(tv.body_mass_kg,
                                                    tv.sensory_range_multiplier),
                flow_rates=derive_flow_rates(metabolic, tv),
                guard_thresholds=derive_guard_thresholds(metabolic, tv),
                consumption_rate=derive_consumption_rate(metabolic),
            )

        interactions = self._build_interaction_matrix()

        return CompiledEcology(
            derived_params=derived,
            interaction_matrix=interactions,
            resource_tags=self._build_resource_tags(),
        )

    def _build_interaction_matrix(self) -> dict:
        """For each (actor, target) pair, find matching templates."""
        matrix = {}
        templates = [Herbivory(), Predation(), Pollination(), Decomposition()]
        for actor_id, actor_tv in self.traits.items():
            for target_id, target_tv in self.traits.items():
                if actor_id == target_id:
                    continue
                matches = []
                for tmpl in templates:
                    if tmpl.matches(actor_tv, target_tv):
                        params = tmpl.compute_rates(actor_tv, target_tv)
                        matches.append((tmpl.interaction_type, params))
                if matches:
                    matrix[(actor_id, target_id)] = matches
        return matrix
```

### Step 1.6 — Refactor engine.py to Read Derived Params

This is the most delicate step. The tick loop structure stays identical. What changes
is *where* each phase gets its constants.

**Before:**
```python
# In the guard phase
if entity["type"] == "ANIMAL":
    if entity["hydration"] < 0.2:  # hard-coded
        entity["state"] = "DRINKING"
```

**After:**
```python
# In the guard phase
params = compiled.derived_params[entity["species_id"]]
if entity["hydration"] < params.guard_thresholds["hydration_enter"]:
    entity["state"] = "DRINKING"
```

Each `if entity["type"] ==` branch becomes a lookup into `DerivedParams`. The seven
tick phases are refactored one at a time, with tests run after each:

1. **Flow phase** — replace hard-coded hunger/thirst/energy deltas with
   `params.flow_rates`. Run tests.
2. **Guard phase** — replace hard-coded thresholds with `params.guard_thresholds`.
   Run tests.
3. **Interaction phase** — replace species-specific interaction code with
   interaction matrix lookups. Run tests.
4. **Movement phase** — replace hard-coded speeds with `params.speed` and
   `params.sensory_range`. Run tests.
5. **Spawning/reproduction phase** — replace hard-coded clutch sizes and
   spread ranges with derived values. Run tests.
6. **Voxel effects** — these are mostly biome-driven, not species-driven.
   Minimal change expected.
7. **Motor inference** — no change. BYOM adapters already use the protocol.

### Step 1.7 — Write Trait Vectors for Existing Species

Express each current species as a trait vector in the world JSON. These vectors,
when compiled, must produce parameters matching the audit from Step 1.1.

```json
{
  "species_definitions": [
    {
      "species_id": "deer",
      "functional_group": "herbivore",
      "entity_class": "ANIMAL",
      "body_mass_kg": 80.0,
      "locomotion": "quadruped",
      "skeleton_id": "quadruped_medium",
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
      "sensory_range_multiplier": 1.0,
      "movement_budget": 0.4,
      "resource_tags": []
    },
    {
      "species_id": "butterfly",
      "functional_group": "pollinator",
      "entity_class": "INSECT",
      "body_mass_kg": 0.0005,
      "locomotion": "flight_insect",
      "skeleton_id": "insect_wing",
      "thermoregulation": "ectotherm",
      "diet_type": "nectarivore",
      "diet_breadth": ["forb:fruiting"],
      "trophic_level": 2.0,
      "reproductive_strategy": "r_selected",
      "clutch_size": 3,
      "generation_time_ticks": 2000,
      "thermal_range": [10, 35],
      "drought_tolerance": 0.1,
      "shade_tolerance": 0.5,
      "sensory_range_multiplier": 1.2,
      "movement_budget": 0.6,
      "resource_tags": [],
      "floral_affinity": ["forb"]
    },
    {
      "species_id": "oak",
      "functional_group": "producer",
      "entity_class": "TREE",
      "body_mass_kg": 5000.0,
      "locomotion": "rooted",
      "skeleton_id": null,
      "thermoregulation": "autotroph",
      "diet_type": "autotroph",
      "diet_breadth": [],
      "trophic_level": 1.0,
      "reproductive_strategy": "K_selected",
      "clutch_size": 1,
      "generation_time_ticks": 20000,
      "thermal_range": [-10, 40],
      "drought_tolerance": 0.5,
      "shade_tolerance": 0.2,
      "sensory_range_multiplier": 0.0,
      "movement_budget": 0.0,
      "canopy_radius": 3.0,
      "root_persistence": true,
      "resource_tags": ["mast"]
    },
    {
      "species_id": "meadow_grass",
      "functional_group": "producer",
      "entity_class": "PLANT",
      "body_mass_kg": 0.01,
      "locomotion": "sessile",
      "skeleton_id": null,
      "thermoregulation": "autotroph",
      "diet_type": "autotroph",
      "diet_breadth": [],
      "trophic_level": 1.0,
      "reproductive_strategy": "r_selected",
      "clutch_size": 2,
      "generation_time_ticks": 500,
      "thermal_range": [5, 35],
      "drought_tolerance": 0.2,
      "shade_tolerance": 0.4,
      "sensory_range_multiplier": 0.0,
      "movement_budget": 0.0,
      "spread_mode": "runner",
      "spread_range": 2.0,
      "root_persistence": true,
      "resource_tags": ["graminoid"]
    },
    {
      "species_id": "wildflower",
      "functional_group": "producer",
      "entity_class": "PLANT",
      "body_mass_kg": 0.05,
      "locomotion": "sessile",
      "skeleton_id": null,
      "thermoregulation": "autotroph",
      "diet_type": "autotroph",
      "diet_breadth": [],
      "trophic_level": 1.0,
      "reproductive_strategy": "r_selected",
      "clutch_size": 1,
      "generation_time_ticks": 800,
      "thermal_range": [5, 35],
      "drought_tolerance": 0.15,
      "shade_tolerance": 0.3,
      "sensory_range_multiplier": 0.0,
      "movement_budget": 0.0,
      "spread_mode": "runner",
      "spread_range": 3.5,
      "root_persistence": true,
      "resource_tags": ["forb"],
      "pollination_syndrome": "insect_generalist"
    }
  ]
}
```

### Step 1.8 — Calibration & Regression Testing

1. Run `TraitCompiler` on the five trait vectors above.
2. Compare every value in `DerivedParams` against the audit table from Step 1.1.
3. Adjust calibration constants (`B0_NORMALIZED`, `SPEED_BASE_*`, etc.) until
   derived values match hard-coded values within 5%.
4. Run the full test suite (12 unit tests + smoke test).
5. Run the demo_world for 2000 ticks and compare entity population curves,
   state transition counts, and event counts against a baseline recording
   from the current engine. Acceptable drift: ±10% on population counts,
   identical state machine topology (same states reachable in same order).

### Step 1.9 — Backward Compatibility

The old world JSON format (without `species_definitions`) must still work.
If no trait vectors are present, the engine falls back to the current hard-coded
paths. This is a deprecation bridge, not a permanent design.

```python
if "species_definitions" in world_config:
    compiled = TraitCompiler(parse_traits(world_config), biome).compile()
else:
    compiled = LegacyParams()  # wraps current hard-coded values
```

### Phase 1 Deliverables

- `ecosim/traits.py` — TraitVector, DerivedParams, allometric derivation functions
- `ecosim/interactions.py` — InteractionTemplate base + 4 concrete templates
- `ecosim/trait_compiler.py` — TraitCompiler class
- Refactored `engine.py` — reads from DerivedParams instead of hard-coded constants
- Refactored `voxel_manager.py` — 5 layers (nutrients_fast, nutrients_slow,
  moisture, temperature, organic_matter), mineralization/dissolution/leaching
  fluxes, death→organic_matter deposits
- Updated `examples/demo_world.json` — includes `species_definitions` key +
  3 new rate multipliers (mineralization, dissolution, nutrient_leaching)
- New tests in `tests/test_traits.py` — unit tests for every derivation function
- New tests in `tests/test_nutrients.py` — two-pool flow tests (see nutrient spec)
- New test: `tests/test_regression.py` — 2000-tick comparison against baseline

**New files: 4. Modified files: 4. No new external dependencies.**

---

## Phase 2 — New Species by Trait Vector Only

**Goal:** Add three new species to the demo world by writing trait vectors in JSON.
Zero new engine code. The interaction templates and allometric derivations handle
everything.

### Step 2.1 — Wolf (Predator)

The first real test of the architecture. A wolf completes the food chain:
grass → deer → wolf.

```json
{
  "species_id": "wolf",
  "functional_group": "predator",
  "entity_class": "ANIMAL",
  "body_mass_kg": 40.0,
  "locomotion": "quadruped",
  "skeleton_id": "quadruped_medium",
  "thermoregulation": "endotherm",
  "diet_type": "carnivore",
  "diet_breadth": ["herbivore"],
  "trophic_level": 3.0,
  "reproductive_strategy": "K_selected",
  "clutch_size": 3,
  "generation_time_ticks": 8000,
  "thermal_range": [-15, 35],
  "drought_tolerance": 0.4,
  "shade_tolerance": 0.5,
  "sensory_range_multiplier": 1.5,
  "movement_budget": 0.5,
  "resource_tags": []
}
```

**What should happen automatically:**

- Predation template matches: wolf `diet_breadth` ["herbivore"] overlaps with
  deer's `functional_group` "herbivore". Body mass ratio 40/80 = 0.5 is within
  the 0.1–2.0 predation window.
- Deer's flee response triggers: the engine sees a carnivore within sensory range
  with body mass sufficient to be threatening. No deer-specific flee code needed.
- Wolf hunger rate derived from 40 kg endotherm: `70 * 40^0.75` ≈ lower than
  deer's rate (smaller body), meaning wolves hunt frequently.
- Wolf speed derived from `40^0.25` ≈ 2.51 relative units. Deer speed from
  `80^0.25` ≈ 2.99. Deer are faster but wolves have higher sensory range
  multiplier, creating a pursuit dynamic.
- No wolf-butterfly interaction: wolf `diet_breadth` ["herbivore"] doesn't match
  butterfly's functional_group "pollinator".

**Validation:** Run 5000 ticks. Verify: wolves hunt deer, deer population declines
to a sustainable level, grass recovers (reduced grazing pressure), trophic cascade
emerges without any new engine code.

### Step 2.2 — Songbird (New Trophic Niche)

An insectivore that introduces a new diet pathway. Eats insects (butterflies),
disperses seeds.

```json
{
  "species_id": "songbird",
  "functional_group": "insectivore",
  "entity_class": "BIRD",
  "body_mass_kg": 0.025,
  "locomotion": "flight_bird",
  "skeleton_id": "bird_small",
  "thermoregulation": "endotherm",
  "diet_type": "omnivore",
  "diet_breadth": ["pollinator", "forb:fruiting"],
  "trophic_level": 2.5,
  "reproductive_strategy": "r_selected",
  "clutch_size": 4,
  "generation_time_ticks": 3000,
  "thermal_range": [5, 35],
  "drought_tolerance": 0.2,
  "shade_tolerance": 0.6,
  "sensory_range_multiplier": 2.0,
  "movement_budget": 0.5,
  "resource_tags": []
}
```

**Architecture test:** The predation template should match songbird → butterfly
(diet_breadth includes "pollinator", body mass ratio 0.025/0.0005 = 50×, but
this exceeds the 2× cap — so we either adjust the predation window for
insectivory or add body-mass-ratio rules per diet category). This is a real
design question the architecture must handle. Insectivory has different mass
ratios than mammalian predation. Solution: the predation template's mass ratio
window is parameterized per `diet_type` or `diet_breadth` category:

- Carnivore hunting herbivores: ratio 0.1–2.0×
- Insectivore hunting insects: ratio 1.0–1000× (predator is always much larger)

This is one parameterized constant, not a species-specific rule.

### Step 2.3 — Mushroom (Decomposer)

Closes the nutrient loop. Decomposes dead organic matter, enriches soil.

```json
{
  "species_id": "mushroom",
  "functional_group": "decomposer",
  "entity_class": "MICROORGANISM",
  "body_mass_kg": 0.001,
  "locomotion": "sessile",
  "skeleton_id": null,
  "thermoregulation": "ectotherm",
  "diet_type": "decomposer",
  "diet_breadth": ["dead_organic_matter"],
  "trophic_level": 1.0,
  "reproductive_strategy": "r_selected",
  "clutch_size": 5,
  "generation_time_ticks": 300,
  "thermal_range": [5, 30],
  "drought_tolerance": 0.1,
  "shade_tolerance": 0.9,
  "sensory_range_multiplier": 0.0,
  "movement_budget": 0.0,
  "spread_mode": "spore",
  "spread_range": 4.0,
  "root_persistence": false,
  "resource_tags": []
}
```

**Architecture test:** This requires the decomposition interaction template to
work with the voxel system. When an entity dies, instead of just disappearing,
it leaves a "dead_organic_matter" marker. Nearby mushrooms consume it and
boost soil nutrients. This connects the entity lifecycle to the voxel layer
in a general way — not a mushroom-specific way.

### Step 2.4 — Emergent Dynamics Validation

With 8 species, run a suite of 10,000-tick simulations with varied initial
conditions. Document which interaction chains emerge *without being coded*:

- [ ] Wolf-deer predation with population oscillations (Lotka-Volterra dynamics)
- [ ] Trophic cascade: wolves reduce deer → grass recovers → wildflowers bloom
- [ ] Songbird-butterfly predation reducing pollination rates
- [ ] Mushroom decomposition accelerating soil recovery after death events
- [ ] Cross-trophic competition: songbirds and butterflies competing for fruiting flowers
- [ ] Thermal range exclusions: some species drop out in extreme biome settings

### Phase 2 Deliverables

- Three new species as JSON trait vectors (zero new engine code)
- Updated interaction templates with parameterized mass-ratio windows
- Decomposition template + dead_organic_matter voxel integration
- Extended demo world: `examples/temperate_meadow_8sp.json`
- Emergent dynamics validation report
- `docs/trait_species_guide.md` — how a biologist adds a new species

---

## Phase 3 — ASAL Substrate Protocol & FM-Guided Search

**Goal:** Formalize līlā as an ASAL-compatible substrate. Build the headless
rendering pipeline, parameterize the search space, and implement the three
ASAL search modes (supervised target, open-endedness, illumination).

### Step 3.1 — Substrate Protocol

Define the three-function interface that ASAL expects. This lives in a new
top-level `search/` directory with its own `pyproject.toml` (depends on ecosim
core, plus torch, CLIP, and search/viz libraries).

```python
from typing import Protocol
import numpy as np

class ALifeSubstrate(Protocol):
    """ASAL-compatible substrate interface."""

    def init(self, theta: np.ndarray, seed: int = 0) -> dict:
        """Initialize simulation state from parameter vector theta."""
        ...

    def step(self, state: dict) -> dict:
        """Advance simulation by one tick. Returns new state."""
        ...

    def render(self, state: dict) -> np.ndarray:
        """Render current state as RGB image (H, W, 3) uint8."""
        ...

    def theta_spec(self) -> ThetaSpec:
        """Describes the parameter space: names, ranges, types."""
        ...
```

### Step 3.2 — θ Parameterization (Three Variants)

Each variant exposes a different slice of the ecological parameter space:

**EcoRates** (~15 dimensions)
- 6 rate multipliers (consumption, hunger, thirst, growth, reproduction, water_replenishment)
- Biome base values (soil_nutrients, soil_moisture, temperature — 3 dims)
- Water source config (count, mean_radius, mean_water_level — 3 dims)
- Rain frequency and intensity (2 dims)
- Entity count scaling factor (1 dim)

**EcoTopology** (~50–80 dimensions)
- Everything in EcoRates
- Per-species: count (how many to spawn), spatial distribution parameters
- Species presence vector (binary: which of the 8 species are included)
- Water source positions (2D × count)
- Initial state variable perturbations

**EcoAdapt** (~550–600 dimensions)
- Everything in EcoTopology
- MLP adapter weights (500 params for the reference MLP)
- Per-species motor adapter selection

Each variant implements `theta_to_world_config(theta) -> dict` which converts
the flat parameter vector into a valid world JSON that the engine can load.

### Step 3.3 — Headless Renderer

A lightweight Python renderer (PIL or pure numpy) that takes `EcosystemEngine`
state and produces a top-down 2D image. No browser, no WebSocket.

**What to render (semantically important for CLIP embedding):**

- Soil moisture as background gradient (teal → amber, matching browser viz)
- Water sources as blue circles with radius proportional to water_level
- Each species as a distinct colored shape at its grid position:
  - Animals/birds: directional triangles (colored by species)
  - Insects: small dots with wing indicators
  - Plants: circles scaled by growth, colored by health
  - Trees: large circles with canopy halos
  - Dormant plants: faded brown markers
- State labels not needed (CLIP works on visual patterns, not text)

Target: 256×256 px, ~1ms render time. Lives in `search/renderer.py`.
Uses PIL (Pillow) which is the only new dependency for this module.

```python
def headless_render(engine: EcosystemEngine, size: int = 256) -> np.ndarray:
    """Render engine state as RGB numpy array."""
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)

    # Background: soil moisture heatmap
    _draw_moisture_background(draw, engine.voxel_manager, size)

    # Water sources
    for ws in engine.water_sources:
        _draw_water_source(draw, ws, size)

    # Entities by species
    for entity in engine.entities:
        _draw_entity(draw, entity, engine.compiled.traits, size)

    return np.array(img)
```

### Step 3.4 — FM Evaluation Pipeline

The evaluation loop renders periodic frames from a simulation rollout and
embeds them with a vision-language foundation model.

```python
import torch
import clip

class FMEvaluator:
    """Evaluates simulation rollouts using a vision-language foundation model."""

    def __init__(self, model_name: str = "ViT-B/32", device: str = "cuda"):
        self.model, self.preprocess = clip.load(model_name, device)
        self.device = device

    def embed_frames(self, frames: list[np.ndarray]) -> torch.Tensor:
        """Embed a sequence of rendered frames into FM space."""
        images = [self.preprocess(Image.fromarray(f)) for f in frames]
        batch = torch.stack(images).to(self.device)
        with torch.no_grad():
            embeddings = self.model.encode_image(batch)
        return embeddings / embeddings.norm(dim=-1, keepdim=True)

    def supervised_target_score(self, embeddings: torch.Tensor,
                                 prompts: list[str]) -> float:
        """Score: how well does the rollout match a sequence of text prompts?"""
        text_tokens = clip.tokenize(prompts).to(self.device)
        with torch.no_grad():
            text_emb = self.model.encode_text(text_tokens)
            text_emb = text_emb / text_emb.norm(dim=-1, keepdim=True)
        # Match each prompt to the corresponding temporal frame
        scores = (embeddings @ text_emb.T).diag()
        return scores.mean().item()

    def open_endedness_score(self, embeddings: torch.Tensor) -> float:
        """Score: how much novel territory does the trajectory cover?"""
        novelties = []
        archive = [embeddings[0]]
        for i in range(1, len(embeddings)):
            distances = [1 - (embeddings[i] @ a).item() for a in archive]
            novelty = min(distances)  # nearest neighbor distance
            novelties.append(novelty)
            archive.append(embeddings[i])
        return sum(novelties) / len(novelties)

    def illumination_distance(self, embedding: torch.Tensor,
                               archive: list[torch.Tensor]) -> float:
        """Score: how far is this simulation from its nearest neighbor?"""
        if not archive:
            return float("inf")
        distances = [1 - (embedding @ a).item() for a in archive]
        return min(distances)
```

### Step 3.5 — Search Loop Implementations

Three search modes, matching ASAL's framework:

**Supervised Target Search** — "Find the ecological parameters that produce
a sequence of events matching these prompts."

```python
def search_supervised(substrate, evaluator, prompts, generations=100,
                      population=50, rollout_ticks=2000, render_every=100):
    """CMA-ES search for theta that matches prompt sequence."""
    import cma
    theta_spec = substrate.theta_spec()
    es = cma.CMAEvolutionStrategy(theta_spec.initial, theta_spec.sigma0,
                                   {"bounds": [theta_spec.lower, theta_spec.upper],
                                    "popsize": population})
    for gen in range(generations):
        candidates = es.ask()
        scores = []
        for theta in candidates:
            state = substrate.init(np.array(theta))
            frames = []
            for t in range(rollout_ticks):
                state = substrate.step(state)
                if t % render_every == 0:
                    frames.append(substrate.render(state))
            embeddings = evaluator.embed_frames(frames)
            score = evaluator.supervised_target_score(embeddings, prompts)
            scores.append(-score)  # CMA-ES minimizes
        es.tell(candidates, scores)
    return es.result.xbest
```

Example ecological prompts:

- Single target: `"a thriving meadow with grazing animals"`
- Temporal sequence: `["a lush green meadow", "overgrazing and bare soil",
  "rain falling on dry ground", "new growth emerging from soil"]`
- Open-ended: no prompt needed — maximizes trajectory novelty

**Open-Endedness Search** — "Find the ecosystem configuration that stays
interesting the longest."

Uses the same CMA-ES loop but with `open_endedness_score` as the objective.
Longer rollouts (5000–10000 ticks) to test sustained novelty.

**Illumination** — "Map the space of possible ecosystems."

Uses a genetic algorithm with diversity pressure. Maintains an archive of
diverse solutions. New candidates are evaluated on how different they are
from everything in the archive.

### Step 3.6 — Simulation Atlas Visualization

After illumination, produce a 2D UMAP projection of all discovered ecosystems,
with rendered thumbnails at each point. This is the "atlas of possible ecologies"
visualization.

```python
def build_simulation_atlas(archive: list[dict], output_path: str):
    """Generate UMAP visualization of discovered ecosystems."""
    import umap
    embeddings = np.stack([a["embedding"] for a in archive])
    reducer = umap.UMAP(n_components=2, metric="cosine")
    coords = reducer.fit_transform(embeddings)

    # Create atlas image with thumbnails at UMAP coordinates
    fig, ax = plt.subplots(figsize=(20, 20))
    for i, (x, y) in enumerate(coords):
        thumbnail = archive[i]["final_frame"]
        # ... plot thumbnail at (x, y)
    fig.savefig(output_path, dpi=150)
```

### Step 3.7 — Physical Plausibility Constraints

Unlike abstract ASAL substrates, līlā can reject physically impossible
configurations before evaluation, saving compute.

```python
def validate_theta(theta: np.ndarray, spec: ThetaSpec) -> bool:
    """Reject biologically impossible configurations."""
    world = theta_to_world_config(theta)
    for species in world["species_definitions"]:
        mass = species["body_mass_kg"]
        locomotion = species["locomotion"]
        thermo = species["thermoregulation"]

        # Square-cube law: flying insects can't exceed ~0.1 kg
        if locomotion == "flight_insect" and mass > 0.1:
            return False

        # Endotherms below ~2g can't thermoregulate
        if thermo == "endotherm" and mass < 0.002:
            return False

        # Terrestrial animals above ~10,000 kg are structurally implausible
        if locomotion == "quadruped" and mass > 10000:
            return False

        # Trophic sanity: carnivores need prey species present
        if species["diet_type"] == "carnivore":
            prey_present = any(
                s["functional_group"] in species["diet_breadth"]
                for s in world["species_definitions"]
                if s["species_id"] != species["species_id"]
            )
            if not prey_present:
                return False

    return True
```

### Phase 3 Deliverables

- `search/` directory with own pyproject.toml
- `search/substrate.py` — ALifeSubstrate protocol + LilaSubstrate implementation
- `search/renderer.py` — headless PIL renderer
- `search/evaluator.py` — FM evaluation pipeline (CLIP + DINOv2)
- `search/search.py` — three search mode implementations
- `search/theta.py` — θ parameterization for EcoRates, EcoTopology, EcoAdapt
- `search/atlas.py` — simulation atlas visualization
- `search/constraints.py` — physical plausibility validation
- `examples/search_configs/` — example search configurations
- `docs/asal_substrate_guide.md` — how to use līlā as an ASAL substrate

---

## File Layout After All Three Phases

```
lila/
├── server/
│   ├── ecosim/
│   │   ├── engine.py              # refactored: reads DerivedParams
│   │   ├── entities.py            # updated: species_id field
│   │   ├── traits.py              # NEW: TraitVector, allometric derivations
│   │   ├── interactions.py        # NEW: InteractionTemplate grammar
│   │   ├── trait_compiler.py      # NEW: TraitCompiler
│   │   ├── biome.py               # unchanged
│   │   ├── voxel_manager.py       # minor: dead_organic_matter support
│   │   ├── model_adapter.py       # unchanged
│   │   ├── worker.py              # unchanged
│   │   └── adapters/              # unchanged
│   ├── tests/
│   │   ├── test_ecosim.py         # existing, must still pass
│   │   ├── smoke_test.py          # existing, must still pass
│   │   ├── test_traits.py         # NEW: allometric derivation tests
│   │   └── test_regression.py     # NEW: 2000-tick baseline comparison
│   └── examples/
│       ├── demo_world.json        # updated: species_definitions key
│       └── temperate_meadow_8sp.json  # NEW: 8-species world
│
├── search/                        # NEW: entire directory
│   ├── pyproject.toml             # deps: torch, clip, cma, umap, pillow, matplotlib
│   ├── substrate.py
│   ├── renderer.py
│   ├── evaluator.py
│   ├── search.py
│   ├── theta.py
│   ├── atlas.py
│   ├── constraints.py
│   └── examples/
│       ├── search_target.py       # example: find a thriving meadow
│       ├── search_openended.py    # example: find the most open-ended ecosystem
│       └── search_illuminate.py   # example: map the ecology space
│
├── docs/
│   ├── trait_species_guide.md     # NEW: how biologists add species
│   └── asal_substrate_guide.md    # NEW: how to use līlā with ASAL
│
└── (everything else unchanged)
```

---

## Critical Constraints Preserved

- **ecosim remains stdlib-only.** `traits.py`, `interactions.py`, and
  `trait_compiler.py` use only `math`, `dataclasses`, and typing from stdlib.
  All FM/search dependencies live in `search/`.
- **Docker Compose still works.** The trait system is internal to ecosim.
  No new containers needed.
- **Tick rate budget preserved.** The trait compiler runs once at init, not per tick.
  Per-tick lookups into `DerivedParams` are dict access — O(1), no regression.
- **32³ grid, 4D motion latent, 10Hz tick rate** — all locked, unchanged.
- **Voxel layers: 5.** nutrients_fast, nutrients_slow, moisture, temperature,
  organic_matter. Updated from 4 → 5 per two-pool nutrient decision.
- **Randomization remains opt-in.** Trait vectors are deterministic; randomization
  still controlled by the `"randomize"` key.
- **Plants still go dormant.** `root_persistence: true` in the trait vector
  maps to the existing dormancy logic.
- **BYOM adapter protocol unchanged.** Adapters don't know about traits.
  They receive the same context spec and return the same 4D latent.

---

## Allometric References

These are the scaling laws used in the derivation functions:

| Relationship | Equation | Source |
|---|---|---|
| Basal metabolic rate | BMR = B₀ × M^0.75 (endotherm) | Kleiber 1932, Brown et al. 2004 |
| Metabolic rate (ectotherm) | BMR = B₀ × M^0.69 | Gillooly et al. 2001 |
| Cruising speed (terrestrial) | v = v₀ × M^0.25 | Peters 1983 |
| Flight speed (insect) | v = v₀ × M^0.17 | Dudley 2000 |
| Home range / sensory | HR ∝ M^0.75 | McNab 1963, Kelt & Van Vuren 2001 |
| Max speed (hump-shaped) | v_max = v_theor × (1 - e^(-k×τ)) | Hirt et al. 2017 |
| Consumption rate | ∝ BMR | Brown et al. 2004 (MTE) |
| Generation time | T_gen ∝ M^0.25 | Western 1979 |

**Key reference for the overall approach:**

- Harfoot et al. 2014. "Emergent Global Patterns of Ecosystem Structure and
  Function from a Mechanistic General Ecosystem Model." PLoS Biology.
  (The Madingley Model — trait-based, allometric, no species-specific code.)

---

## Sequence & Dependencies

```
Phase 1.1   Audit hard-coded params       ← no dependencies, start here
Phase 1.2   Define TraitVector schema     ← informs 1.3
Phase 1.3   Allometric derivations        ← needs 1.1 for calibration targets
Phase 1.4   Interaction templates         ← needs 1.2 for trait matching
Phase 1.5   TraitCompiler                 ← needs 1.3 + 1.4
Phase 1.5a  Two-pool nutrient refactor    ← see TWO_POOL_NUTRIENT_SPEC.md
            (voxel layers 4→5, mineralization/dissolution/leaching,
             rain split, dormancy check update, death→organic_matter,
             3 new rate multipliers) — do before 1.6 so the engine
             refactor picks up the new layer indices
Phase 1.6   Refactor engine.py            ← needs 1.5 + 1.5a, most delicate step
Phase 1.7   Write trait vectors           ← needs 1.2 schema
Phase 1.8   Calibration & regression      ← needs 1.6 + 1.7, blocks Phase 2
Phase 1.9   Backward compatibility        ← safety net, do alongside 1.6

Phase 2.1   Wolf trait vector             ← needs Phase 1 complete
Phase 2.2   Songbird trait vector         ← reveals mass-ratio edge cases
Phase 2.3   Mushroom trait vector         ← connects to two-pool decomposition
Phase 2.4   Emergent dynamics report      ← validates the architecture

Phase 3.1   Substrate protocol            ← needs Phase 1 (trait-parameterized engine)
Phase 3.2   θ parameterization            ← needs 3.1 (includes mineralization/
                                             dissolution/leaching as searchable dims)
Phase 3.3   Headless renderer             ← independent, can start during Phase 2
Phase 3.4   FM evaluation pipeline        ← needs 3.3
Phase 3.5   Search loop implementations   ← needs 3.2 + 3.4
Phase 3.6   Simulation atlas viz          ← needs 3.5 results
Phase 3.7   Plausibility constraints      ← needs 1.2 trait schema
```

---

## Open Questions (Decisions Needed Before or During Implementation)

1. **Allometric exponent for ectotherm metabolism:** Literature ranges from 0.69
   to 0.75. Does līlā use a single exponent with a thermoregulation coefficient,
   or separate exponents? The Madingley Model uses separate. Recommend: separate,
   matching Gillooly et al. 2001.

2. **Predation mass-ratio windows by diet category:** Mammalian predation (0.1–2×),
   insectivory (1–1000×), piscivory (0.01–10×). Should these be hard constants or
   part of the trait vector? Recommend: hard constants per diet category, stored in
   `interactions.py`. They're well-established ecological relationships, not tunable
   parameters.

3. **Dead organic matter representation:** ✅ **DECIDED.** Entity death deposits
   biomass into the organic_matter voxel layer at the death position. Amount
   proportional to body mass. Organic matter mineralizes into the new
   nutrients_slow pool, which dissolves into nutrients_fast. Decomposer entities
   accelerate mineralization locally. See `TWO_POOL_NUTRIENT_SPEC.md` for full
   implementation spec. **Design decision update: Voxel layers 4 → 5.**

4. **Plant trait derivations:** Allometric scaling is best validated for animals.
   Plant growth rates, spread distances, and dormancy thresholds are less cleanly
   allometric. Recommend: keep plant-specific traits (spread_range, spread_mode,
   root_persistence) as explicit trait fields rather than deriving them from
   body mass. The trait system still eliminates per-species engine code; it just
   doesn't pretend plant ecology follows mammalian allometry.

5. **FM choice for Phase 3:** CLIP (ViT-B/32) is the ASAL default and runs on
   16GB VRAM. DINOv2 is vision-only (no text prompts for supervised target).
   SigLIP or newer VLMs may be better but are heavier. Recommend: start with
   CLIP ViT-B/32, add DINOv2 as an option for open-endedness/illumination where
   text prompts aren't needed.

6. **JAX port consideration:** ASAL's codebase is JAX-native for end-to-end
   differentiability. līlā is pure Python. Porting the engine to JAX would
   enable gradient-based search but is a major rewrite. Recommend: don't port.
   CMA-ES (gradient-free) works well for ASAL's non-NCA substrates and handles
   600-dimensional spaces fine. The ecological grounding of the search space
   may make gradient-free search more efficient anyway, since parameters have
   meaningful directions.

7. **Narrative for the blog series:** This transition is a natural continuation
   of "The Unseen Hand" thesis. The story arc: manual design → trait-based
   derivation → FM-guided discovery. The AI isn't just driving motion (motor
   adapters) — it's searching for the ecological configurations that produce
   the most lifelike dynamics. The unseen hand operates at two levels now.
