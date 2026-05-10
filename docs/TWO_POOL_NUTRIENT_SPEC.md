<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā — Two-Pool Nutrient Split: Implementation Spec

> **Decision:** Split the single `nutrients` voxel layer into `nutrients_fast` and
> `nutrients_slow`. Add a mineralization flux between pools. This unlocks
> meaningful temporal separation in soil recovery and gives the Phase 2
> decomposer species a mechanistically distinct role.
>
> **Design decision update:** Voxel layers changes from 4 → 5.
> `[nutrients, moisture, temperature, organic_matter]` →
> `[nutrients_fast, nutrients_slow, moisture, temperature, organic_matter]`

---

## Conceptual Model

```
                    ┌──────────────────────┐
                    │   Dead Entities      │
                    │   (animal/plant)     │
                    └──────────┬───────────┘
                               │ death event: +biomass
                               ▼
                    ┌──────────────────────┐
                    │   organic_matter     │  Layer 5 (existing, unchanged)
                    │   (dead biomass)     │  Slow decay, spatial
                    └──────────┬───────────┘
                               │ decomposition rate (accelerated by decomposers)
                               ▼
┌──────────┐       ┌──────────────────────┐
│   Rain   │─────> │   nutrients_slow     │  Layer 2 (NEW)
│ (mineral │       │   (mineralized pool) │  Stable, slow-release
│  input)  │       │   Long-term soil     │  Represents soil health
└──────────┘       │   health indicator   │
    │              └──────────┬───────────┘
    │                         │ dissolution rate
    │                         ▼
    │              ┌─────────────────────┐
    └─────────────>│   nutrients_fast    │  Layer 1 (replaces old "nutrients")
                   │   (plant-available) │  Quick turnover
                   │   Dissolved, labile │  Plants consume from here
                   └──────────┬──────────┘
                              │ plant uptake
                              ▼
                   ┌─────────────────────┐
                   │   Plant Growth      │
                   │   (health, growth)  │
                   └─────────────────────┘
```

**Why two pools, not one:**

With a single nutrient pool, rain and decomposition are interchangeable — they
both increment the same number. This means:

- A heavily overgrazed meadow recovers at the same rate from one rain event as
  a meadow with active decomposition. That's ecologically wrong.
- There's no concept of "soil health" distinct from "nutrients available right now."
  A lush meadow and a depleted one with recent rain look identical.
- The decomposer species (Phase 2 mushroom) has no distinct mechanistic role —
  it just does what rain does, slower.

With two pools:

- **Fast pool** (nutrients_fast): what plants actually eat. Depletes quickly
  under heavy growth. Refills quickly from rain (mineral nutrients washed in)
  and from dissolution of the slow pool. This is the short-term signal.
- **Slow pool** (nutrients_slow): long-term soil health. Builds up from
  decomposition of organic matter. Depletes slowly. Feeds the fast pool via
  mineralization/dissolution. This is the memory of the soil — a meadow that's
  been healthy for thousands of ticks has a deep slow pool that buffers against
  short-term stress.

This creates two recovery timescales:
- **Rain recovery** (fast): rain → nutrients_fast. Plants respond within tens
  of ticks. But if the slow pool is depleted, the fast pool drains quickly again
  once rain stops. The meadow "bounces" but doesn't sustain.
- **Decomposition recovery** (slow): dead matter → organic_matter →
  nutrients_slow → nutrients_fast. Takes hundreds of ticks but builds lasting
  soil health. This is the ecological role of decomposers.

---

## Pool Dynamics (Per-Tick Equations)

All values are clamped to [0.0, 1.0] after each tick.

### Mineralization: organic_matter → nutrients_slow

Organic matter slowly converts to mineralized nutrients. The base rate is
constant; decomposer entities accelerate it locally (Phase 2).

```python
# Base mineralization (always active, represents microbial background)
delta_slow = organic_matter[cell] * MINERALIZATION_RATE
nutrients_slow[cell] += delta_slow
organic_matter[cell] -= delta_slow

# Decomposer acceleration (Phase 2, when decomposer entities exist)
# Each nearby decomposer multiplies the local rate
# decomposer_factor = 1.0 + (nearby_decomposer_count * DECOMPOSER_BOOST)
# delta_slow *= decomposer_factor
```

**MINERALIZATION_RATE:** 0.002 per tick (tuned so organic_matter has a half-life
of ~350 ticks without decomposers, ~70 ticks with 3 nearby decomposers)

### Dissolution: nutrients_slow → nutrients_fast

Slow pool feeds fast pool at a steady rate. This is the "background fertility"
that makes healthy soil valuable.

```python
delta_fast = nutrients_slow[cell] * DISSOLUTION_RATE
nutrients_fast[cell] += delta_fast
nutrients_slow[cell] -= delta_fast
```

**DISSOLUTION_RATE:** 0.005 per tick (slow pool half-life ~140 ticks, meaning
a fully charged slow pool sustains the fast pool for hundreds of ticks even
with no new input)

### Plant Uptake: nutrients_fast → plant growth

Plants consume from the fast pool only. Uptake rate is proportional to growth
rate (larger/faster-growing plants consume more).

```python
# Already exists conceptually in the flow phase.
# Currently reads "nutrients" — change to read "nutrients_fast"
uptake = plant_growth_rate * UPTAKE_FRACTION
nutrients_fast[cell] -= uptake
# Clamped: if nutrients_fast < 0, growth is limited
```

### Rain Input

Rain delivers mineral nutrients directly to the fast pool (dissolved in
rainwater) and a smaller amount to the slow pool (particulate deposition).

```python
# Currently: nutrients += 0.024 * intensity
# New:
nutrients_fast[cell] += 0.020 * intensity   # ~83% of original rain nutrient boost
nutrients_slow[cell] += 0.004 * intensity   # ~17% — builds long-term health
```

The split preserves the total nutrient input from rain (0.024) while directing
most of it to the immediately available pool.

### Soil Evaporation / Nutrient Leaching

The fast pool should experience slow leaching (nutrients wash deeper into soil
or are lost). The slow pool is stable.

```python
# Fast pool leaching (new, runs in the soil evaporation phase)
nutrients_fast[cell] -= nutrients_fast[cell] * NUTRIENT_LEACH_RATE

# Slow pool: no leaching (stable soil organic matter doesn't leach significantly)
```

**NUTRIENT_LEACH_RATE:** 0.001 per tick (very slow, but creates long-term
pressure to maintain inputs)

### Death Event: entity → organic_matter

When an entity dies, its biomass is deposited into the organic_matter layer at
its grid position. The amount deposited is proportional to body mass (from the
trait vector in the trait-based architecture, or a constant per entity type
in the current architecture).

```python
# In the removals phase, when an entity is removed:
cell = grid_position(entity)
biomass_deposit = entity_biomass(entity)  # body_mass_kg normalized to 0–1 scale
organic_matter[cell] += biomass_deposit
# Clamped to 1.0
```

**Normalization:** body_mass_kg mapped to organic_matter deposit via a scaling
constant. An 80 kg deer deposits ~0.15 organic_matter. A 0.01 kg grass blade
deposits ~0.002. A 5000 kg oak deposits 0.5+ (capped at cell max, spillover
to neighbors).

---

## Voxel Manager Changes

### Layer Index Update

```python
# Before:
LAYER_NUTRIENTS = 0
LAYER_MOISTURE = 1
LAYER_TEMPERATURE = 2
LAYER_ORGANIC_MATTER = 3
NUM_LAYERS = 4

# After:
LAYER_NUTRIENTS_FAST = 0
LAYER_NUTRIENTS_SLOW = 1
LAYER_MOISTURE = 2
LAYER_TEMPERATURE = 3
LAYER_ORGANIC_MATTER = 4
NUM_LAYERS = 5
```

### initialize_from_soil Update

The biome's `soil_nutrients` value now initializes both pools:

```python
def initialize_from_soil(self, soil_config: dict):
    """Initialize voxel grid from biome soil configuration."""
    base_nutrients = soil_config.get("nutrients", 0.5)
    moisture = soil_config.get("moisture", 0.3)
    temperature = soil_config.get("temperature", 0.5)

    for cell in self._all_cells():
        # Fast pool: 40% of base nutrients (immediately available)
        self.set(cell, LAYER_NUTRIENTS_FAST, base_nutrients * 0.4)
        # Slow pool: 60% of base nutrients (long-term reserve)
        self.set(cell, LAYER_NUTRIENTS_SLOW, base_nutrients * 0.6)
        # Rest unchanged
        self.set(cell, LAYER_MOISTURE, moisture)
        self.set(cell, LAYER_TEMPERATURE, temperature)
        # organic_matter starts at 0 (no dead biomass yet)
        self.set(cell, LAYER_ORGANIC_MATTER, 0.0)
```

The 40/60 split means a new world starts with some immediate fertility (fast)
and a deeper reserve (slow). The ratio is a biome parameter that could vary:
desert = 20/80 (little available, deep mineral reserve), tropical = 60/40
(lots available, less mineral reserve due to leaching).

**IMPORTANT:** The `initialize_from_soil` break bug (documented in gotchas)
was that it silently skipped layers 2-3 due to a `break` in an `elif` chain.
With 5 layers, this must be verified: all five layers must be initialized.

### Dirty Tracking

The threshold-gated dirty tracking works identically for 5 layers — each cell×layer
pair is tracked independently. No structural change needed, just the expanded
layer count.

### Delta Packets

Delta packets sent over WebSocket include the layer index. Clients that don't
understand the new layer indices will ignore them (forward compatibility).
The browser visualizer currently renders the moisture heatmap — it doesn't render
the nutrient layer visually, so adding a fifth layer doesn't break the existing
client.

If/when the visualizer shows nutrients, it should display `nutrients_fast +
nutrients_slow * 0.3` as an "effective fertility" value — weighted toward the
immediately available pool but acknowledging the reserve.

---

## Engine Touchpoints (Every Place "nutrients" Is Currently Read/Written)

These are the exact code locations that need to change. Each one is a
find-and-replace scoped to specific semantic meaning.

### 1. Plant Dormancy Recovery Check

```python
# BEFORE (engine.py, guard phase):
if soil_moisture > 0.25 and nutrients > 0.15:
    # Plant recovers from dormancy

# AFTER:
# Recovery requires BOTH some immediate nutrients AND some soil health.
# This means rain alone isn't sufficient for recovery if the soil is
# completely depleted — you need either time (dissolution from slow pool)
# or decomposer activity.
effective_nutrients = nutrients_fast + nutrients_slow * 0.3
if soil_moisture > 0.25 and effective_nutrients > 0.15:
    # Plant recovers from dormancy
```

**Why the weighted sum:** Pure fast-pool check would let rain alone trigger
recovery (which is the current behavior — preserve it for now). Pure slow-pool
check would make recovery impossibly slow. The weighted sum means: "rain helps
a lot, but a depleted soil can't fully recover from rain alone."

The `0.3` weight on the slow pool means:
- Fast = 0.15 alone → recovery (rain was enough)
- Fast = 0.05, Slow = 0.33 → effective = 0.05 + 0.1 = 0.15 → recovery
  (modest fast + decent soil health)
- Fast = 0, Slow = 0.3 → effective = 0.09 → no recovery
  (soil has reserves but nothing plant-available yet)

### 2. Plant Spreading Soil Check

```python
# BEFORE:
if nutrients > threshold and moisture > threshold:
    # Allow vegetative spreading

# AFTER:
# Spreading only needs immediate nutrients — the plant is investing
# energy now, not building long-term reserves.
if nutrients_fast > threshold and moisture > threshold:
    # Allow vegetative spreading
```

### 3. Rain Application (apply_rain)

```python
# BEFORE:
nutrients += 0.024 * intensity

# AFTER:
nutrients_fast += 0.020 * intensity   # dissolved mineral input
nutrients_slow += 0.004 * intensity   # particulate/sediment deposition
```

### 4. Voxel Effects Phase (Per-Tick Soil Processes)

This is where the new inter-pool fluxes run. Add to the existing voxel
effects phase, after moisture updates:

```python
# NEW: Nutrient pool dynamics (runs every tick for every active cell)
for cell in active_cells:
    om = self.voxel_manager.get(cell, LAYER_ORGANIC_MATTER)
    slow = self.voxel_manager.get(cell, LAYER_NUTRIENTS_SLOW)
    fast = self.voxel_manager.get(cell, LAYER_NUTRIENTS_FAST)

    # 1. Mineralization: organic_matter → nutrients_slow
    mineralized = om * MINERALIZATION_RATE
    om -= mineralized
    slow += mineralized

    # 2. Dissolution: nutrients_slow → nutrients_fast
    dissolved = slow * DISSOLUTION_RATE
    slow -= dissolved
    fast += dissolved

    # 3. Leaching: nutrients_fast slowly drains
    leached = fast * NUTRIENT_LEACH_RATE
    fast -= leached

    # Clamp all to [0, 1]
    self.voxel_manager.set(cell, LAYER_ORGANIC_MATTER, max(0, min(1, om)))
    self.voxel_manager.set(cell, LAYER_NUTRIENTS_SLOW, max(0, min(1, slow)))
    self.voxel_manager.set(cell, LAYER_NUTRIENTS_FAST, max(0, min(1, fast)))
```

### 5. Plant Growth Flow Phase

```python
# BEFORE:
# Plant growth influenced by nutrients (exact code depends on engine.py)
growth_factor = nutrients * moisture * ...

# AFTER:
# Growth draws from fast pool only
growth_factor = nutrients_fast * moisture * ...
# AND: successful growth depletes the fast pool
nutrients_fast -= growth_amount * UPTAKE_FRACTION
```

### 6. Entity Death (Removals Phase)

```python
# BEFORE:
# Entity simply removed from entity list

# AFTER:
# Deposit biomass into organic_matter layer
cell = self._grid_cell(entity["x"], entity["y"], entity["z"])
deposit = self._biomass_deposit(entity)
current_om = self.voxel_manager.get(cell, LAYER_ORGANIC_MATTER)
self.voxel_manager.set(cell, LAYER_ORGANIC_MATTER,
                        min(1.0, current_om + deposit))
# Then remove entity as before
```

```python
def _biomass_deposit(self, entity: dict) -> float:
    """Convert entity to organic matter deposit amount."""
    # Phase 1 (pre-trait): use fixed values per entity type
    DEPOSITS = {
        "ANIMAL": 0.15,    # deer-sized
        "BIRD": 0.01,
        "INSECT": 0.002,
        "PLANT": 0.005,
        "TREE": 0.4,
        "MICROORGANISM": 0.001,
    }
    return DEPOSITS.get(entity.get("type", ""), 0.01)

    # Phase 2 (trait-based): derive from body_mass_kg
    # return min(0.5, entity["body_mass_kg"] * BIOMASS_DEPOSIT_SCALE)
```

---

## Rate Constants Summary

| Constant | Value | Unit | Ecological Meaning |
|----------|-------|------|-------------------|
| MINERALIZATION_RATE | 0.002 | per tick | organic_matter → nutrients_slow conversion |
| DISSOLUTION_RATE | 0.005 | per tick | nutrients_slow → nutrients_fast release |
| NUTRIENT_LEACH_RATE | 0.001 | per tick | nutrients_fast drainage |
| UPTAKE_FRACTION | 0.01 | per growth event | fast pool consumed by plant growth |
| BIOMASS_DEPOSIT_SCALE | 0.002 | per kg | body_mass_kg → organic_matter deposit |
| RAIN_FAST_FRACTION | 0.020 | per rain × intensity | rain → nutrients_fast |
| RAIN_SLOW_FRACTION | 0.004 | per rain × intensity | rain → nutrients_slow |
| INIT_FAST_RATIO | 0.4 | ratio | fraction of base nutrients → fast pool |
| INIT_SLOW_RATIO | 0.6 | ratio | fraction of base nutrients → slow pool |

**Calibration strategy:** Run the existing demo_world for 2000 ticks with these
constants. Compare plant population dynamics, dormancy/recovery timing, and
post-rain behavior against the baseline from the single-pool engine. Adjust
DISSOLUTION_RATE and INIT ratios until the two-pool system produces dynamics
that feel qualitatively similar but with the new temporal separation visible
in extended runs (5000+ ticks).

These constants should also be exposed as rate multipliers in the world JSON
(alongside the existing six rate multipliers) for stress testing:

```json
{
  "rate_multipliers": {
    "consumption": 1.0,
    "hunger": 1.0,
    "thirst": 1.0,
    "growth": 1.0,
    "reproduction": 1.0,
    "water_replenishment": 1.0,
    "mineralization": 1.0,
    "dissolution": 1.0,
    "nutrient_leaching": 1.0
  }
}
```

---

## Timescale Analysis

At the default constants, here's what the pool dynamics look like over time:

**Scenario 1: Rain event on depleted soil**
- Tick 0: nutrients_fast = 0, nutrients_slow = 0.1, organic_matter = 0
- Rain (intensity 0.8): nutrients_fast jumps to 0.016, nutrients_slow to 0.103
- Ticks 1-50: nutrients_fast slowly drains via leaching and plant uptake,
  but dissolution from slow pool feeds it at ~0.0005/tick
- Tick 100: nutrients_fast ≈ 0.05 (enough for some growth, declining)
- Tick 200: nutrients_fast ≈ 0.02 (slow pool nearly drained too)
- **Result:** Brief green-up, then decline. Rain alone doesn't sustain.

**Scenario 2: Healthy soil, no disturbance**
- Tick 0: nutrients_fast = 0.2, nutrients_slow = 0.3, organic_matter = 0.05
- Per tick: dissolution adds ~0.0015 to fast, leaching removes ~0.0002,
  mineralization adds ~0.0001 to slow from organic_matter
- Steady state: fast pool stays high (~0.18-0.22), slow pool slowly
  declines but very gradually
- **Result:** Sustained growth. The slow pool acts as a battery.

**Scenario 3: Overgrazing then recovery with decomposers (Phase 2)**
- Tick 0: Heavy grazing has depleted fast pool to 0.02, slow pool to 0.05
- Ticks 1-200: Dead grass deposits organic_matter (OM accumulates to ~0.1)
- Without decomposers: OM → slow at 0.002/tick = 0.0002/tick. Very slow.
  Recovery takes 1000+ ticks.
- With 3 decomposers nearby: OM → slow rate × 4 = 0.0008/tick.
  Slow pool rebuilds to 0.15 by tick 200. Dissolution feeds fast pool.
  Plants can recover by tick 300.
- **Result:** Decomposers cut recovery time by 3-4×. Mechanistically distinct
  from rain.

---

## Phase 2 Connection: Decomposer Integration

When the mushroom species arrives in Phase 2, it connects to this system
through the interaction template:

```python
class Decomposition(InteractionTemplate):
    interaction_type = "decomposition"

    def matches(self, actor: TraitVector, target_cell: VoxelCell) -> bool:
        """Decomposers 'interact' with voxel cells, not entities."""
        return (actor.diet_type == "decomposer"
                and target_cell.organic_matter > 0.01)

    def apply(self, actor: TraitVector, cell: GridPosition,
              voxel_manager: VoxelManager):
        """Accelerate mineralization at this cell."""
        om = voxel_manager.get(cell, LAYER_ORGANIC_MATTER)
        boost = actor.body_mass_kg * DECOMPOSER_METABOLIC_FACTOR
        extra_mineralized = om * MINERALIZATION_RATE * boost
        voxel_manager.add(cell, LAYER_NUTRIENTS_SLOW, extra_mineralized)
        voxel_manager.add(cell, LAYER_ORGANIC_MATTER, -extra_mineralized)
```

Note that decomposition is unique among interaction templates: the "target"
is a voxel cell, not another entity. The decomposer senses high organic_matter
cells (like herbivores sense plants) and moves toward them. Its presence
accelerates the OM → slow pool conversion. This is the only interaction
template that operates on voxel state rather than entity state.

The mushroom's trait vector drives this behavior:
- `diet_type: "decomposer"` → triggers decomposition template
- `diet_breadth: ["dead_organic_matter"]` → seeking behavior targets high-OM cells
- `body_mass_kg: 0.001` → small boost per individual, but they reproduce fast
  (r_selected, clutch_size 5), so local clusters amplify the effect

---

## ASAL Substrate Impact (Phase 3)

The two-pool system adds three new dimensions to the θ parameter space:

**EcoRates variant:** MINERALIZATION_RATE, DISSOLUTION_RATE, NUTRIENT_LEACH_RATE
as searchable parameters. "What soil chemistry turnover rates produce the most
open-ended ecosystem dynamics?"

**EcoTopology variant:** Initial fast/slow ratio as a per-cell or per-biome
parameter. "What starting soil health distribution produces the most interesting
trophic cascades?"

**Headless renderer:** The nutrient visualization becomes a two-channel signal.
Fast pool maps to brightness (immediate fertility visible to CLIP). Slow pool
maps to a subtle undertone (soil health visible over time). The FM can
distinguish "looks green because it rained" from "looks green because the soil
is healthy" — and these are ecologically different things.

---

## Browser Visualizer Impact

The current moisture heatmap (teal→amber) is the only voxel-layer visualization.
Nutrients aren't currently rendered. Two options:

**Option A (minimal):** No change. The nutrient pools affect entity behavior
(plant growth, dormancy recovery) which the visualizer already shows through
plant appearance (size, color, dormancy markers). The pools are "felt, not seen."
Aligned with the "unseen hand" thesis.

**Option B (if desired later):** Add a toggleable "soil health" overlay that
visualizes `nutrients_slow` as a subtle brown→dark green gradient underneath
the moisture heatmap. This would make long-term soil depletion visible before
plants go dormant — an early warning signal.

**Recommendation:** Option A for now. The emergent plant behavior is the
visualization. Add Option B when/if the Godot client needs richer terrain shading.

---

## Implementation Sequence

This work slots into Phase 1 of the transition plan between Steps 1.5 and 1.6.
The trait compiler needs to know about two nutrient pools when deriving plant
growth thresholds.

```
Step 1.5a — Two-pool voxel refactor
  1. Update layer indices in voxel_manager.py (4 → 5 layers)
  2. Update initialize_from_soil with fast/slow split
  3. Add mineralization + dissolution + leaching to voxel effects phase
  4. Update rain application (split nutrient boost)
  5. Update dormancy recovery check (weighted effective nutrients)
  6. Update plant spreading soil check (fast pool only)
  7. Add biomass deposit on entity death
  8. Run existing test suite — all 12 tests must pass
  9. Run 2000-tick regression test — calibrate rate constants

Step 1.5b — Rate multiplier exposure
  1. Add mineralization, dissolution, nutrient_leaching to rate_multipliers
  2. Update demo_world.json with defaults (1.0)
  3. Test: world JSON without new multipliers still works (defaults to 1.0)
```

**Time estimate:** The voxel refactor is 1-2 sessions of focused work. Most
changes are mechanical (layer index updates, splitting a single addition into
two additions). The calibration step is where iteration lives — getting the
rate constants to produce dynamics that feel right takes experimentation.

---

## Test Plan

### Unit Tests (test_ecosim.py additions)

```python
def test_two_pool_initialization():
    """Verify both nutrient pools initialized from soil config."""
    vm = VoxelManager(grid_size=4)
    vm.initialize_from_soil({"nutrients": 0.5, "moisture": 0.3})
    cell = (2, 2, 0)
    assert abs(vm.get(cell, LAYER_NUTRIENTS_FAST) - 0.2) < 0.01   # 40% of 0.5
    assert abs(vm.get(cell, LAYER_NUTRIENTS_SLOW) - 0.3) < 0.01   # 60% of 0.5

def test_mineralization_flow():
    """Organic matter converts to slow nutrients over time."""
    vm = VoxelManager(grid_size=4)
    cell = (2, 2, 0)
    vm.set(cell, LAYER_ORGANIC_MATTER, 0.5)
    vm.set(cell, LAYER_NUTRIENTS_SLOW, 0.0)
    # Simulate 100 ticks of mineralization
    for _ in range(100):
        om = vm.get(cell, LAYER_ORGANIC_MATTER)
        mineralized = om * MINERALIZATION_RATE
        vm.set(cell, LAYER_ORGANIC_MATTER, om - mineralized)
        vm.set(cell, LAYER_NUTRIENTS_SLOW,
               vm.get(cell, LAYER_NUTRIENTS_SLOW) + mineralized)
    # OM should have decayed, slow should have grown
    assert vm.get(cell, LAYER_ORGANIC_MATTER) < 0.42
    assert vm.get(cell, LAYER_NUTRIENTS_SLOW) > 0.08

def test_dissolution_flow():
    """Slow nutrients dissolve into fast pool."""
    vm = VoxelManager(grid_size=4)
    cell = (2, 2, 0)
    vm.set(cell, LAYER_NUTRIENTS_SLOW, 0.5)
    vm.set(cell, LAYER_NUTRIENTS_FAST, 0.0)
    for _ in range(100):
        slow = vm.get(cell, LAYER_NUTRIENTS_SLOW)
        dissolved = slow * DISSOLUTION_RATE
        vm.set(cell, LAYER_NUTRIENTS_SLOW, slow - dissolved)
        vm.set(cell, LAYER_NUTRIENTS_FAST,
               vm.get(cell, LAYER_NUTRIENTS_FAST) + dissolved)
    assert vm.get(cell, LAYER_NUTRIENTS_FAST) > 0.15
    assert vm.get(cell, LAYER_NUTRIENTS_SLOW) < 0.35

def test_rain_splits_nutrients():
    """Rain adds to both pools with correct ratio."""
    vm = VoxelManager(grid_size=4)
    cell = (2, 2, 0)
    vm.set(cell, LAYER_NUTRIENTS_FAST, 0.0)
    vm.set(cell, LAYER_NUTRIENTS_SLOW, 0.0)
    # Simulate rain at intensity 1.0
    vm.set(cell, LAYER_NUTRIENTS_FAST, 0.020)
    vm.set(cell, LAYER_NUTRIENTS_SLOW, 0.004)
    assert abs(vm.get(cell, LAYER_NUTRIENTS_FAST) - 0.020) < 0.001
    assert abs(vm.get(cell, LAYER_NUTRIENTS_SLOW) - 0.004) < 0.001

def test_death_deposits_organic_matter():
    """Entity death adds biomass to organic_matter layer."""
    # Integration test with engine
    ...

def test_dormancy_recovery_effective_nutrients():
    """Dormancy recovery uses weighted sum of both pools."""
    fast = 0.10
    slow = 0.20
    effective = fast + slow * 0.3  # = 0.16
    assert effective > 0.15  # Should allow recovery
    fast_only = 0.10
    effective_no_slow = fast_only + 0.0 * 0.3  # = 0.10
    assert effective_no_slow < 0.15  # Should NOT allow recovery
```

### Regression Test (test_regression.py)

Run 2000-tick simulation with both single-pool (legacy) and two-pool engines.
Compare:
- Plant population count at ticks 500, 1000, 1500, 2000 (within ±15%)
- Number of dormancy → active transitions (within ±20%)
- Number of death events (within ±20%)
- Post-rain recovery timing: ticks from rain event to first dormancy recovery
  (two-pool may be slightly faster due to fast pool, acceptable)

---

## Backward Compatibility

Worlds without the new rate multipliers use defaults (1.0 for all three new
constants). The only breaking change is the layer index shift:

- Code that references `LAYER_NUTRIENTS` by name → update to `LAYER_NUTRIENTS_FAST`
- Code that references layers by numeric index (0, 1, 2, 3) → update to new indices
- Client code parsing delta packets with layer indices → forward-compatible if
  it ignores unknown indices, breaking if it assumes exactly 4 layers

**WebSocket protocol:** The delta packet format includes layer index as an integer.
Existing clients seeing layer index 4 (organic_matter, unchanged) are fine.
New layer index 1 (nutrients_slow) might be unexpected. The browser visualizer
doesn't render nutrient layers, so it ignores these deltas. The Godot client
(not yet built) will be designed for 5 layers from the start.
