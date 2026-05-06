<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā 0.1-Alpha — Species & Skeleton Specification

## Design Goal

Five species, two skeletons, five interaction chains. The minimum set
that demonstrates the thesis: small invisible ML makes a world feel
alive. Every species earns its place by contributing to a visible
ecological chain, not by being decorative.

---

## Species Set

### Deer (the star)

The entity that sells the motion latent system. A low-poly faceted
quadruped with five visually distinct movement styles driven by the
latent vector — not by canned animation clips.

```json
{
  "id": "deer_01",
  "type": "ANIMAL",
  "species": "deer",
  "position": [16.0, 0.0, 14.0],
  "metadata": {
    "diet": "herbivore",
    "body_mass": 60.0,
    "metabolism_rate": 1.0,
    "sensory_range": 12.0,
    "movement_speed": 3.0,
    "lifespan": 800.0,
    "reproduction_threshold": 0.8
  },
  "skeleton_id": "quadruped_medium"
}
```

**Ecological role:** Primary consumer. Grazes meadow grass (falls back
to wildflowers when grass is gone), drinks from water sources, deposits
organic matter on death. Drives the grazing loop.

**Motor latent targets** (what the trained model should produce):

| State | Movement style | Latent character |
|---|---|---|
| IDLE | Subtle weight shifting between legs, ear flicks, occasional head turn | Low energy, high regularity |
| FORAGING | Head lowered, slow deliberate walk, pauses between steps | Moderate energy, rhythmic |
| DRINKING | Head fully lowered, legs slightly splayed, still body | Low energy, stable |
| FLEEING | Full galloping stride, body lowered, ears flat | High energy, irregular |
| RESTING | Legs tucked under body, head up or resting on ground | Minimal energy, very stable |

### Butterfly (the co-star)

Same adapter, radically different body plan. Demonstrates the latent
system's generality — the same 4-dim vector drives two completely
different movement vocabularies.

```json
{
  "id": "butterfly_01",
  "type": "INSECT",
  "species": "monarch",
  "position": [10.0, 0.0, 8.0],
  "metadata": {
    "diet": "herbivore",
    "colony_size": 1,
    "metabolism_rate": 0.6,
    "pollination_range": 6.0,
    "movement_speed": 2.0,
    "lifespan": 150.0
  },
  "skeleton_id": "insect_wing"
}
```

**Ecological role:** Pollinator. Drawn to FRUITING wildflowers,
triggers POLLINATION events on contact, lingers 1.5–3s at each flower,
then seeks the next bloom. Flower cooldown prevents re-pollination of
the same flower. Also seeks water sources when thirsty. Drives the
pollination loop.

**Motor latent targets:**

| State | Movement style | Latent character |
|---|---|---|
| FORAGING | Gentle fluttering, meandering path, frequent direction changes | Moderate energy, irregular |
| POLLINATING | Hovering near flower, wings slowed, deliberate approach | Low energy, high regularity |
| DRINKING | Wings slowed near water, low hovering altitude | Low energy, steady |
| RESTING | Wings folded or minimal movement, near-stationary | Minimal energy, stable |

**Note:** `colony_size: 1` — unlike the general INSECT type which
represents a colony, butterflies in the 0.1-alpha are individual
entities. The engine's colony abstraction treats colony_size=1 as
a single organism. This is a pragmatic simplification; proper swarm
rendering is a v0.2 feature (bounded fields).

### Oak Tree (structure)

The anchor of the scene. Provides visual scale, shade (affects local
temperature voxels), and heavy nutrient/moisture draw from the soil.

```json
{
  "id": "oak_01",
  "type": "TREE",
  "species": "meadow_oak",
  "position": [8.0, 0.0, 8.0],
  "metadata": {
    "metabolism": "photosynthetic",
    "growth_rate": 0.005,
    "root_depth": 2.0,
    "canopy_radius": 4.0,
    "height_max": 12.0,
    "trunk_radius": 0.6,
    "shade_factor": 0.35,
    "nutrient_demand": { "nitrogen": 0.02, "phosphorus": 0.01 },
    "water_demand": 0.05
  }
}
```

**Ecological role:** Ecosystem structure. Draws heavily from soil,
creates a nutrient/moisture gradient that pushes grass and flowers
to grow further from the trunk. On death, deposits significant
organic matter. Collapse pressure activates when support_count
(non-tree, non-insect, non-dormant entities) drops to ≤ 2.

**Rendering:** No skeleton. Vertex shader for gentle canopy sway
driven by `wind_speed` from climate data. Growth state controls
canopy density/scale. WILTING state desaturates leaf color.
FRUITING state not visually distinguished in 0.1-alpha (oak
acorns are not part of the interaction chains).

### Meadow Grass (ground cover)

Scattered across the grid. The deer's food source and the most
visible indicator of ecosystem health — overgrazed areas turn
sparse, recovering areas fill back in.

```json
{
  "id": "grass_01",
  "type": "PLANT",
  "species": "meadow_grass",
  "position": [14.0, 0.0, 12.0],
  "metadata": {
    "metabolism": "photosynthetic",
    "growth_rate": 0.06,
    "root_depth": 0.1,
    "canopy_radius": 0.0,
    "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 },
    "water_demand": 0.02
  }
}
```

**Ecological role:** Primary producer, grazing target. Fast growth
rate means visible recovery after grazing. Shallow roots = low
nutrient draw but vulnerable to moisture stress. Spreads vegetatively
(range 2, frequent) when soil conditions allow, subject to density
limits and parent resource cost.

**Rendering:** No skeleton. Instanced low-poly clumps (MultimeshInstance3D).
Scale driven by `growth` state var (0.1 = stubble, 1.0 = full height).
Color shifts from yellow-brown (low hydration) to rich green (high
hydration). CONSUMPTION events trigger a visible scale reduction at
the grazed position.

**Population:** The demo world places 12 grass entities scattered
across the grid to create a visible meadow. They're cheap to simulate
(simple flow equations, no movement).

### Wildflower (pollination target)

The bridge between the grazing and pollination loops. When healthy
and hydrated enough to reach FRUITING state, it blooms and attracts
butterflies.

```json
{
  "id": "flower_01",
  "type": "PLANT",
  "species": "wildflower",
  "position": [11.0, 0.0, 6.0],
  "metadata": {
    "metabolism": "photosynthetic",
    "growth_rate": 0.09,
    "root_depth": 0.15,
    "canopy_radius": 0.0,
    "nutrient_demand": { "nitrogen": 0.008, "phosphorus": 0.004 },
    "water_demand": 0.025
  }
}
```

**Ecological role:** Pollination target. Reaches FRUITING
(`growth >= 0.5 AND health > 0.4`) → blooms → butterfly arrives →
POLLINATION event → health boost. Creates a visible cause-and-effect
chain for the player. Spreads vegetatively (range 3.5, less frequent
than grass).

**Dormancy:** At `health <= 0`, wildflowers go DORMANT instead of
dying. Roots persist. Recovery triggers when soil moisture > 0.25
and nutrients > 0.15. If dormant for 2000 ticks without recovery,
the plant is permanently removed.

**Rendering:** No skeleton. Single low-poly mesh. Growth state drives
stem height. FRUITING state swaps or tints the top geometry to show
bloom (color pop — saturated petals). WILTING state droops via vertex
shader (rotation around stem base). 5 placed in the demo world.

---

## Interaction Chains

Five interaction chains demonstrate emergent ecosystem behavior from
simple rules:

1. **Grazing** — deer hunger rises → enters FORAGING → seeks nearest
   grass (falls back to wildflowers) → CONSUMPTION event → grass health
   drops → grass spreads to nearby cells if soil is moist

2. **Pollination** — wildflower reaches FRUITING (growth ≥ 0.5, health
   > 0.4) → butterfly detects it → flies to flower → POLLINATION event
   → lingers 1.5–3s → flower enters cooldown → butterfly seeks next
   bloom. Dormant/cooldown flowers are skipped.

3. **Water** — entity hydration drops → enters DRINKING at hydration
   < 0.2 → walks to nearest water source (dried-up sources skipped) →
   drinks → water_level drops → effective radius shrinks → soil around
   source dries → stays in DRINKING until hydration > 0.6

4. **Stress cascade** — overgrazing depletes grass → deer consume
   wildflowers as fallback → butterflies lose food sources → cluster
   at water sources → water sources deplete → soil dries across grid →
   remaining plants wilt → tree collapse pressure activates

5. **Dormancy & recovery** — plants at health 0 go DORMANT (roots
   persist) → rain event → soil moisture rises → nutrients replenish →
   dormant plants recover → wildflowers reach FRUITING → butterflies
   return → pollination resumes → ecosystem stabilizes

---

## Skeleton Rig Specifications

The server sends `skeleton_id` and `motion_latent`. The client owns
everything about how those map to bone transforms. This section
defines the rigs that the client must implement.

### quadruped_medium — 18 bones

Designed for mid-sized four-legged herbivores. Enough articulation
for visually distinct gaits without the complexity of a production
animation rig.

```
root
├── hip
│   ├── upper_back.L → lower_back.L → hoof_back.L
│   ├── upper_back.R → lower_back.R → hoof_back.R
│   └── tail
├── spine_mid
│   └── chest
│       ├── upper_front.L → lower_front.L → hoof_front.L
│       ├── upper_front.R → lower_front.R → hoof_front.R
│       └── neck → head
│                  ├── ear.L
│                  └── ear.R
```

**Bone count:** 18 (root + hip + 6 back-leg + tail + spine_mid +
chest + 6 front-leg + neck + head + 2 ears)

**Key animation channels per behavior:**

| Behavior | Primary bones | Motion character |
|---|---|---|
| Idle weight shift | hip (subtle roll), upper_back.L/R (alternating slight bend) | Slow sinusoidal |
| Ear flick | ear.L, ear.R (independent quick rotations) | Stochastic, brief |
| Grazing walk | all legs (walk cycle), neck + head (lowered pitch) | Rhythmic, slow |
| Drinking | neck + head (deep pitch down), front legs (slightly splayed) | Static hold |
| Gallop | all legs (asymmetric stride), spine_mid (flexion), head (extended forward) | Fast, asymmetric |
| Resting | all legs (folded), spine_mid (lowered), head (variable) | Minimal, breathing only |

### insect_wing — 10 bones

Minimal rig for a single flying insect. Wing bones drive the primary
visual motion; antenna add secondary liveliness.

```
root
└── thorax
    ├── wing.L → wing_tip.L
    ├── wing.R → wing_tip.R
    ├── antenna.L
    ├── antenna.R
    └── abdomen → body_tip
```

**Bone count:** 10 (root, thorax, wing.L, wing_tip.L, wing.R,
wing_tip.R, antenna.L, antenna.R, abdomen, body_tip)

**Key animation channels per behavior:**

| Behavior | Primary bones | Motion character |
|---|---|---|
| Fluttering flight | wing.L/R (symmetric flap), wing_tip.L/R (slight lag) | Fast sinusoidal, ~8 Hz base |
| Hovering near flower | wing.L/R (reduced amplitude), abdomen (slight curl) | Slower, steadier |
| Erratic swarming | wing.L/R (asymmetric flap), thorax (roll/yaw jitter) | Fast, irregular |
| Antenna idle | antenna.L/R (independent gentle wave) | Slow, continuous |

---

## Motion Latent → Bone Transform Mapping

This is entirely client-side. The server produces a 4-dim latent
vector per entity per tick. The client maps it to bone rotations.

### Method: Linear Latent Blend

For each bone `b` with base pose rotation `R_base`:

```
R_final(b) = R_base(b) + Σ(latent[i] × W[b][i])  for i in 0..3
```

Where `W[b][i]` is a per-bone, per-latent-dimension weight that
determines how much that latent dimension affects that bone's
rotation. This weight matrix is stored client-side in the skeleton
resource.

**quadruped_medium:** 18 bones × 4 dims × 3 axes = 216 blend weights
**insect_wing:** 10 bones × 4 dims × 3 axes = 120 blend weights

These are hand-tunable. For 0.1-alpha, the initial values can be
set by:
1. Defining 4 "extreme pose" targets (e.g., dim 0 high = legs extended,
   dim 0 low = legs tucked)
2. Computing the per-bone deltas from base pose to each extreme
3. Using those deltas as the weight columns

With random server-side model weights, this produces varied but
smooth animation. With trained weights, it produces ecologically
meaningful motion styles.

### Interpolation

The client receives new latent vectors at ~10 Hz. Between ticks,
it interpolates in latent space (not bone space):

```
latent_current = lerp(latent_prev, latent_next, t / dt)
```

Then applies the bone mapping to the interpolated latent. This
produces smooth animation even at low tick rates.

---

## Demo World Definition

The complete world definition for the 0.1-alpha demo. Temperate
biome (more moderate dynamics than tropical, easier to observe).

```json
{
  "version": "0.1",
  "session_id": "demo-alpha-001",

  "environment": {
    "type": "MEADOW",
    "biome": "TEMPERATE",
    "climate": {
      "temperature": 22.0,
      "humidity": 0.6,
      "rainfall": 0.4,
      "wind_speed": 0.15,
      "light_level": 0.85
    },
    "soil": {
      "nitrogen": 0.7,
      "phosphorus": 0.6,
      "potassium": 0.5,
      "moisture": 0.65,
      "organic_matter": 0.4,
      "ph": 6.8
    },
    "water_sources": [
      { "position": [6.0, 0.0, 20.0], "radius": 3.0 },
      { "position": [25.0, 0.0, 7.0], "radius": 2.0 }
    ],
    "voxel_grid": {
      "dimensions": [32, 32, 32],
      "cell_size": 1.0
    }
  },

  "model": {
    "adapter": "mlp",
    "seed": 42
  },

  "rates": {
    "consumption": 3.0,
    "hunger": 2.5,
    "thirst": 2.0,
    "growth": 0.6,
    "reproduction": 2.0,
    "water_replenishment": 0.4
  },

  "randomize": {
    "jitter": 1.5,
    "extra_grass": [0, 4],
    "extra_flowers": [0, 2],
    "transform": true
  },

  "entities": [
    {
      "id": "deer_01",
      "type": "ANIMAL",
      "species": "deer",
      "position": [16.0, 0.0, 14.0],
      "metadata": {
        "diet": "herbivore",
        "body_mass": 60.0,
        "metabolism_rate": 1.0,
        "sensory_range": 12.0,
        "movement_speed": 3.0,
        "lifespan": 800.0,
        "reproduction_threshold": 0.8
      },
      "skeleton_id": "quadruped_medium"
    },
    {
      "id": "deer_02",
      "type": "ANIMAL",
      "species": "deer",
      "position": [20.0, 0.0, 18.0],
      "metadata": {
        "diet": "herbivore",
        "body_mass": 55.0,
        "metabolism_rate": 1.1,
        "sensory_range": 12.0,
        "movement_speed": 3.2,
        "lifespan": 800.0,
        "reproduction_threshold": 0.8
      },
      "skeleton_id": "quadruped_medium"
    },

    {
      "id": "butterfly_01",
      "type": "INSECT",
      "species": "monarch",
      "position": [10.0, 0.0, 8.0],
      "metadata": {
        "diet": "herbivore",
        "colony_size": 1,
        "metabolism_rate": 0.6,
        "pollination_range": 6.0,
        "movement_speed": 2.0,
        "lifespan": 150.0
      },
      "skeleton_id": "insect_wing"
    },
    {
      "id": "butterfly_02",
      "type": "INSECT",
      "species": "monarch",
      "position": [18.0, 0.0, 10.0],
      "metadata": {
        "diet": "herbivore",
        "colony_size": 1,
        "metabolism_rate": 0.6,
        "pollination_range": 6.0,
        "movement_speed": 2.0,
        "lifespan": 150.0
      },
      "skeleton_id": "insect_wing"
    },

    {
      "id": "oak_01",
      "type": "TREE",
      "species": "meadow_oak",
      "position": [8.0, 0.0, 8.0],
      "metadata": {
        "metabolism": "photosynthetic",
        "growth_rate": 0.005,
        "root_depth": 2.0,
        "canopy_radius": 4.0,
        "height_max": 12.0,
        "trunk_radius": 0.6,
        "shade_factor": 0.35,
        "nutrient_demand": { "nitrogen": 0.02, "phosphorus": 0.01 },
        "water_demand": 0.05
      }
    },
    {
      "id": "oak_02",
      "type": "TREE",
      "species": "meadow_oak",
      "position": [24.0, 0.0, 22.0],
      "metadata": {
        "metabolism": "photosynthetic",
        "growth_rate": 0.005,
        "root_depth": 2.0,
        "canopy_radius": 3.5,
        "height_max": 10.0,
        "trunk_radius": 0.5,
        "shade_factor": 0.3,
        "nutrient_demand": { "nitrogen": 0.02, "phosphorus": 0.01 },
        "water_demand": 0.05
      }
    },

    { "id": "grass_01", "type": "PLANT", "species": "meadow_grass", "position": [12.0, 0.0, 12.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_02", "type": "PLANT", "species": "meadow_grass", "position": [14.0, 0.0, 10.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_03", "type": "PLANT", "species": "meadow_grass", "position": [16.0, 0.0, 16.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_04", "type": "PLANT", "species": "meadow_grass", "position": [18.0, 0.0, 14.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_05", "type": "PLANT", "species": "meadow_grass", "position": [20.0, 0.0, 12.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_06", "type": "PLANT", "species": "meadow_grass", "position": [22.0, 0.0, 16.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_07", "type": "PLANT", "species": "meadow_grass", "position": [10.0, 0.0, 14.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_08", "type": "PLANT", "species": "meadow_grass", "position": [14.0, 0.0, 18.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_09", "type": "PLANT", "species": "meadow_grass", "position": [16.0, 0.0, 20.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_10", "type": "PLANT", "species": "meadow_grass", "position": [20.0, 0.0, 20.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_11", "type": "PLANT", "species": "meadow_grass", "position": [24.0, 0.0, 14.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },
    { "id": "grass_12", "type": "PLANT", "species": "meadow_grass", "position": [26.0, 0.0, 18.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.005, "phosphorus": 0.002 }, "water_demand": 0.02 } },

    { "id": "flower_01", "type": "PLANT", "species": "wildflower", "position": [11.0, 0.0, 6.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.09, "root_depth": 0.15, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.008, "phosphorus": 0.004 }, "water_demand": 0.025 } },
    { "id": "flower_02", "type": "PLANT", "species": "wildflower", "position": [15.0, 0.0, 8.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.09, "root_depth": 0.15, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.008, "phosphorus": 0.004 }, "water_demand": 0.025 } },
    { "id": "flower_03", "type": "PLANT", "species": "wildflower", "position": [19.0, 0.0, 9.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.09, "root_depth": 0.15, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.008, "phosphorus": 0.004 }, "water_demand": 0.025 } },
    { "id": "flower_04", "type": "PLANT", "species": "wildflower", "position": [22.0, 0.0, 11.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.09, "root_depth": 0.15, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.008, "phosphorus": 0.004 }, "water_demand": 0.025 } },
    { "id": "flower_05", "type": "PLANT", "species": "wildflower", "position": [13.0, 0.0, 11.0], "metadata": { "metabolism": "photosynthetic", "growth_rate": 0.09, "root_depth": 0.15, "canopy_radius": 0.0, "nutrient_demand": { "nitrogen": 0.008, "phosphorus": 0.004 }, "water_demand": 0.025 } }
  ]
}
```

**Entity counts:** 2 deer, 2 butterflies, 2 oaks, 12 grass, 5 wildflowers = **23 entities** + 2 water sources

**Spatial layout rationale:** Entities are placed in the central region
of the 32×32 grid (positions 8–26), leaving the edges as buffer space.
Grass is scattered broadly so deer have to move to forage. Wildflowers
are clustered in the lower-x region near the butterflies. Oaks anchor
two corners to create spatial structure. Water sources are placed at
opposite ends of the meadow (SW at [6, 20] and NE at [25, 7]) so
entities must travel to drink.

**Demo rates:** The demo world ships with accelerated rates
(`consumption: 3.0`, `hunger: 2.5`, etc.) so the grazing→drought→
dormancy→rain→recovery cycle plays out in minutes rather than hours.
Set all values to `1.0` for a slower, more naturalistic pace.

---

## Rendering Notes (Godot 4.x)

### Visual Style

Low-poly faceted aesthetic. Flat shading with no smoothing. This is
deliberate — faceted surfaces make bone rotations visible (you can see
individual faces rotate), which demonstrates the latent-driven animation
more clearly than smooth shading would. It also looks intentional rather
than cheap, and is forgiving on model quality.

### Per-Species Rendering

| Species | Mesh type | Skeleton | State visualization |
|---|---|---|---|
| Deer | Single .glb with skeleton | quadruped_medium | Latent-driven bone animation |
| Butterfly | Single .glb with skeleton | insect_wing | Latent-driven wing/body animation |
| Oak | Static .glb, vertex shader for sway | none | Scale by `growth`, desaturate on WILTING |
| Grass | MultimeshInstance3D (instanced clumps) | none | Scale by `growth`, color by `hydration` |
| Wildflower | Single .glb per instance | none | Stem height by `growth`, color pop on FRUITING, droop on WILTING |

### Event Particles (0.1-alpha scope)

Only two event types get visual effects in the first release:

| Event | Effect |
|---|---|
| CONSUMPTION | Small leaf/grass particle burst at `position`. Grass entity scales down. |
| POLLINATION | Pollen particle trail from butterfly to flower. Brief golden shimmer on flower. |

DEATH_NATURAL and DEATH_STARVE trigger entity fade-out (opacity → 0
over 1 second, then `queue_free()`). No particle effect needed.

All other events (PREDATION, REPRODUCTION, DECOMPOSITION, ROOT_UPTAKE,
STATE_CHANGE) are logged to debug console but not visually rendered in
0.1-alpha.

### Voxel Visualization (0.1-alpha scope)

One layer rendered: **moisture**. Displayed as a ground-plane heatmap
using a shader that samples the moisture `ImageTexture3D` at y=0:

- High moisture (>0.7): deeper green/blue tint
- Medium (0.3–0.7): neutral
- Low moisture (<0.3): dry brown/yellow tint

This creates a visible feedback loop: deer drink at high-moisture
areas → moisture drops → ground color changes → moisture recovers
from rainfall → color returns. The nutrient layer is simulated but
not rendered in 0.1-alpha.

---

## What's Deliberately Excluded from 0.1-Alpha

| Feature | Why excluded | When it comes back |
|---|---|---|
| Predation | No carnivore in species set. Keeps demo peaceful and focused on grazing/pollination | v0.2 with wolf or fox |
| Genetic variation | Reproduction is shipped but offspring are clones with inherited stress. Genetic variation adds complexity | v0.2 |
| Microorganisms | Simulated if added, but invisible. Decomposition effects visible in voxel layer | v0.2 with soil viz |
| Scene editor | World def is hardcoded JSON. "Become Alive!" just sends it | v0.2 |
| Multiple biomes | Engine supports all four, client only renders temperate | v0.2 |
| Audio | Not needed to demonstrate the thesis | v0.2 |
| Time of day / seasons | Deferred in the data contract | v2 |
