<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā — Data Contract v0.2

This document defines the foundational schemas that all client-server
communication is built on. Nothing gets implemented until these are stable.

### Design decisions (locked)

- **Skeleton mapping**: client-side convention. The server sends a `skeleton_id`
  string; the client maintains its own registry mapping IDs to local rig resources.
- **Voxel layers**: 4 — `nutrients`, `moisture`, `temperature`, `organic_matter`.
- **Motion latent dimensions**: 4 (expandable later).
- **Event channel**: explicit `events` array in tick packets for client-side effects.
- **Plants go dormant, not dead.** Root persistence is ecologically accurate and
  enables recovery narratives.
- **Randomization is opt-in via JSON.** Omit the `"randomize"` key for exact
  JSON positions.
- **Deferred to v2**: tick-rate/bandwidth optimisation, time-of-day, seasons.

---

## 1. World Definition

Sent by the client when the user presses **"Become Alive!"**. This is the seed
for the entire simulation — the server uses it to configure biome rules,
initialise entity state machines, allocate the voxel grid, and select the
model adapter.

```json
{
  "version": "0.1",
  "session_id": "uuid-string",

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
  ],

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
  }
}
```

### Supported entity types and their metadata keys

| Type             | Required metadata                                                                                     |
|------------------|-------------------------------------------------------------------------------------------------------|
| `ANIMAL`         | `diet`, `body_mass`, `metabolism_rate`, `sensory_range`, `movement_speed`, `lifespan`, `reproduction_threshold` |
| `BIRD`           | same as ANIMAL plus `flight_ceiling`, `wing_span`                                                     |
| `INSECT`         | `diet`, `colony_size`, `metabolism_rate`, `pollination_range`, `movement_speed`, `lifespan`            |
| `PLANT`          | `metabolism`, `growth_rate`, `root_depth`, `canopy_radius`, `nutrient_demand`, `water_demand`          |
| `TREE`           | same as PLANT plus `height_max`, `trunk_radius`, `shade_factor`                                       |
| `MICROORGANISM`  | `function` (decomposer/fixer/pathogen), `colony_density`, `activity_rate`, `optimal_ph`               |

Entities with skeletons include `"skeleton_id"`. Entities without
skeletons (plants, trees) simply omit the field.

### Water sources

Water sources are defined in `environment.water_sources` as an array of objects:

| Field         | Type      | Description                                              |
|---------------|-----------|----------------------------------------------------------|
| `position`    | [x, y, z] | Center of the water source                              |
| `radius`      | float     | Maximum radius at full water level                       |

At runtime, the engine tracks `water_level` (0–1) per source, which
controls the effective radius and visual opacity. Evaporation drains
water sources, groundwater replenishes them, and drinking animals
deplete them. When `water_level` falls below 5%, the source is
considered dried up and skipped by entity pathfinding.

### Model selection

The `model` block selects which BYOM adapter the engine uses for
motor-level inference. See `docs/model_adapter_spec.md` for the full
protocol.

| Field     | Type   | Description                                               |
|-----------|--------|-----------------------------------------------------------|
| `adapter` | string | Adapter name: `"mlp"`, `"static"`, or `"random"`         |
| `seed`    | int    | Optional. RNG seed for reproducible weight init / output  |
| `weights` | string | Optional. Path to saved weight file (MLP only)            |

If the `model` key is omitted, the engine defaults to the `static`
adapter (hand-tuned latent per discrete state).

### Rate multipliers

The `rates` block scales simulation rates. All values default to `1.0`
if the key is omitted. Override individual rates to tune ecosystem
dynamics without code changes (e.g., stress testing with
`"hunger": 3.0`).

| Key                  | Affects                                              |
|----------------------|------------------------------------------------------|
| `consumption`        | How fast entities consume food/resources              |
| `hunger`             | Rate of hunger increase                               |
| `thirst`             | Rate of hydration decrease                            |
| `growth`             | Plant/tree growth rate                                |
| `reproduction`       | Reproductive drive accumulation rate                  |
| `water_replenishment`| Groundwater replenishment of water sources            |

### World randomization

The `randomize` key is **opt-in**. Omit it entirely for deterministic,
exact-JSON positions. When present:

| Field              | Type         | Description                                              |
|--------------------|--------------|----------------------------------------------------------|
| `transform`        | bool         | If `true`, apply a random D4 symmetry transform (4 rotations × 2 flips = 8 orientations) |
| `jitter`           | float        | Max random offset per entity position (grid units)       |
| `extra_grass`      | [min, max]   | Random extra grass spawns per session                    |
| `extra_flowers`    | [min, max]   | Random extra wildflower spawns per session               |

Plants are pushed out of water sources post-randomization to prevent
invalid placements. State variables receive ±5% jitter when
randomization is active.

### Supported biomes and their rule implications

| Biome        | Climate defaults                          | Key simulation effects                              |
|--------------|-------------------------------------------|------------------------------------------------------|
| `TROPICAL`   | high temp, high humidity, high rainfall   | fast plant growth, high evaporation, fast decay       |
| `TEMPERATE`  | moderate temp, moderate humidity          | seasonal cycles, balanced metabolism                  |
| `ARCTIC`     | low temp, low humidity, low light         | high metabolic cost, slow growth, dormancy triggers   |
| `DESERT`     | high temp, very low humidity, high light  | extreme water stress, nocturnal activity bias         |

---

## 2. Entity State Schema

Each entity type has a fixed set of **continuous state variables** (floats that
change every tick via flow equations) and a set of **discrete states** (enum
values that switch via guard conditions). The server initialises these from
entity metadata at session start — the client never sends state variables.

### 2a. Continuous state variables (per type)

These are the variables the hybrid automaton's flow equations operate on.
All values are normalised to `[0.0, 1.0]` unless otherwise noted.

**ANIMAL / BIRD**

| Variable     | Initial | Flow direction | Description                                  |
|--------------|---------|----------------|----------------------------------------------|
| `hunger`     | 0.0     | increasing      | Rises with `metabolism_rate × biome_modifier` |
| `energy`     | 1.0     | decreasing      | Falls with movement; recovers while resting   |
| `hydration`  | 1.0     | decreasing      | Falls with temp; recovered at water sources   |
| `age`        | 0.0     | increasing      | Monotonic; death guard at `lifespan`          |
| `health`     | 1.0     | variable        | Damage, disease, starvation effects           |
| `reproductive_drive` | 0.0 | increasing | Rises when `energy > reproduction_threshold`  |

**PLANT / TREE**

| Variable     | Initial | Flow direction | Description                                    |
|--------------|---------|----------------|------------------------------------------------|
| `hydration`  | 1.0     | decreasing      | Falls with evapotranspiration (temp × humidity)|
| `growth`     | 0.1     | increasing      | Rises with light, water, nutrients; caps at 1.0|
| `nutrient_store` | 0.5 | variable       | Uptake from soil voxels, consumed by growth    |
| `health`     | 1.0     | variable        | Drought stress, herbivory damage               |
| `age`        | 0.0     | increasing      | Monotonic                                      |

**INSECT**

| Variable     | Initial | Flow direction | Description                                   |
|--------------|---------|----------------|-----------------------------------------------|
| `hunger`     | 0.0     | increasing      | Per-individual in colony abstraction          |
| `energy`     | 1.0     | decreasing      | Colony-level energy budget                    |
| `hydration`  | 1.0     | decreasing      | Falls over time; recovered at water sources   |
| `colony_health` | 1.0  | variable       | Predation, resource scarcity, reproduction cost |
| `reproductive_drive` | 0.0 | increasing  | Rises with energy; triggers at > 0.7          |
| `age`        | 0.0     | increasing      | Generation counter                            |

**MICROORGANISM**

| Variable     | Initial | Flow direction | Description                                    |
|--------------|---------|----------------|------------------------------------------------|
| `population` | 0.5     | variable        | Grows with organic matter, falls outside pH range |
| `activity`   | 0.5     | variable        | Drives decomposition/fixation rate             |

### 2b. Discrete states and guard conditions

The hybrid automaton switches discrete state when a guard condition evaluates
true. Guards are checked every tick **after** flow updates.

**Guard hysteresis:** Guards use hysteresis bands to prevent rapid state
oscillation. Each guard has an *entry threshold* and an *exit threshold*.
An entity enters a state when the entry condition is met, but does not leave
until the exit condition is met. For example, an animal enters DRINKING when
`hydration < 0.2` but doesn't exit DRINKING until `hydration > 0.6`.

**ANIMAL / BIRD states**

| State          | Entry condition                                  | Exit condition                        | Notes                                |
|----------------|--------------------------------------------------|---------------------------------------|--------------------------------------|
| `IDLE`         | no other guard active                            | any guard fires                       | Default resting state                |
| `FORAGING`     | `hunger >= 0.3`                                  | `hunger < 0.15`                       | Seeks nearest grass, falls back to wildflowers |
| `HUNTING`      | `hunger >= 0.3 AND diet == "carnivore"`          | `hunger < 0.15`                       | Targets nearest prey entity          |
| `FLEEING`      | `predator_in_range == true`                      | predator out of range                 | Overrides all other states; clamped escape targets |
| `RESTING`      | `energy < 0.2`                                   | `energy > 0.5`                        | Stationary; energy recovery flow     |
| `DRINKING`     | `hydration < 0.2`                                | `hydration > 0.6`                     | Walks to nearest water source; drinks and depletes water_level |
| `REPRODUCING`  | `reproductive_drive > 0.8 AND mate_in_range`     | reproduction complete                 | Grid-wide mate search; proximity check; spawns new entity |
| `DYING`        | `health <= 0 OR age >= lifespan`                 | —                                     | Terminal; entity removed next tick   |

**PLANT / TREE states**

| State          | Entry condition                                  | Exit condition                        | Notes                                |
|----------------|--------------------------------------------------|---------------------------------------|--------------------------------------|
| `GROWING`      | `hydration > 0.3 AND nutrient_store > 0.2`       | hydration or nutrients drop below entry | Default healthy state               |
| `WILTING`      | `hydration <= 0.3 OR nutrient_store <= 0.2`      | conditions improve above GROWING entry | Reduced growth; visual drooping     |
| `DORMANT`      | `health <= 0`                                    | `soil_moisture > 0.25 AND soil_nutrients > 0.15` | Roots persist; 2000-tick timeout → permanent death |
| `FRUITING`     | `growth >= 0.5 AND health > 0.4`                 | growth or health drop below entry     | Produces food; attracts pollinators  |

Plants **never enter a DEAD state directly** from health depletion. At
`health <= 0` they transition to DORMANT. The root system persists and the
plant can recover when soil conditions improve. If a dormant plant fails to
recover within 2000 ticks, it is permanently removed.

Vegetative spreading: grass (range 2, frequent) and wildflowers (range 3.5,
less frequent) can spread to nearby cells if soil moisture and nutrients are
sufficient, density limits are not exceeded, and the parent pays a resource
cost.

**INSECT states**

| State          | Entry condition                                  | Exit condition                        | Notes                                |
|----------------|--------------------------------------------------|---------------------------------------|--------------------------------------|
| `FORAGING`     | `hunger >= 0.2`                                  | `hunger < 0.1`                        | Seeks nearest food source            |
| `POLLINATING`  | `near FRUITING plant AND energy > 0.3`           | linger complete (1.5–3s) or flower enters cooldown | Cross-pollinates; flower cooldown prevents re-pollination |
| `RESTING`      | `energy < 0.15`                                  | `energy > 0.4`                        | Stationary; energy recovery          |
| `DRINKING`     | `hydration < 0.2`                                | `hydration > 0.6`                     | Seeks water source                   |
| `REPRODUCING`  | `reproductive_drive > 0.7`                       | reproduction complete                 | Costs parent colony_health           |
| `DYING`        | `colony_health <= 0`                             | —                                     | Colony collapse; entity removed      |

**MICROORGANISM states**

| State          | Entry condition                                  | Exit condition                        | Notes                                |
|----------------|--------------------------------------------------|---------------------------------------|--------------------------------------|
| `ACTIVE`       | `soil_ph in optimal_range AND moisture > 0.2`    | conditions leave range                | Performing decomposition/fixation    |
| `DORMANT`      | `soil_ph outside range OR moisture <= 0.2`       | conditions return to range            | Minimal activity                     |
| `BLOOMING`     | `organic_matter > 0.8`                           | organic matter consumed below 0.8     | Population explosion                 |

### 2c. Ecosystem dynamics

**Generational decline:** Children inherit parent stress. Spawned entities
receive `hunger × 0.3`, `energy × 0.9`, and `colony_health × 0.9` from
the parent, preventing populations from becoming immortal through rapid
reproduction.

**Ecosystem collapse:** Tree collapse pressure activates when `support_count`
(non-tree, non-insect, non-dormant entities) drops to ≤ 2. Starvation
acceleration causes `colony_health` drain to scale with hunger level.

---

## 3. Tick Packet

Sent by the server to the client every simulation step (~100ms). Uses delta
encoding — only changed data is included. The client maintains its own local
state and patches it with each packet.

```json
{
  "tick": 14207,
  "dt": 0.1,
  "session_id": "uuid-string",

  "environment_delta": {
    "climate": {
      "temperature": 21.8
    },
    "water_sources": [
      {
        "index": 0,
        "water_level": 0.72,
        "effective_radius": 2.16
      }
    ]
  },

  "entity_updates": [
    {
      "id": "deer_01",
      "state": "FORAGING",
      "position": [15.2, 0.0, 13.1],
      "velocity": [0.4, 0.0, 0.3],
      "state_vars": {
        "hunger": 0.45,
        "energy": 0.72
      },
      "motion_latent": [0.82, -0.31, 0.15, 0.67]
    },
    {
      "id": "grass_01",
      "state": "GROWING",
      "state_vars": {
        "hydration": 0.91,
        "growth": 0.34
      }
    }
  ],

  "entity_spawns": [
    {
      "id": "deer_02",
      "type": "ANIMAL",
      "species": "deer",
      "position": [15.0, 0.0, 13.5],
      "skeleton_id": "quadruped_medium",
      "state": "IDLE",
      "state_vars": {
        "hunger": 0.135,
        "energy": 0.9,
        "hydration": 1.0,
        "age": 0.0,
        "health": 1.0,
        "reproductive_drive": 0.0
      },
      "motion_latent": [0.0, 0.0, 0.0, 0.0]
    }
  ],

  "entity_removals": ["grass_04"],

  "events": [
    {
      "type": "CONSUMPTION",
      "tick": 14207,
      "source_id": "deer_01",
      "target_id": "grass_03",
      "position": [15.1, 0.0, 13.0]
    },
    {
      "type": "POLLINATION",
      "tick": 14207,
      "source_id": "butterfly_01",
      "target_id": "flower_02",
      "position": [15.0, 0.0, 8.0]
    }
  ],

  "voxel_deltas": {
    "nutrients": {
      "15,0,13": 0.72,
      "15,0,14": 0.68
    },
    "moisture": {
      "6,0,20": 0.45
    }
  }
}
```

### Tick packet field reference

| Field               | Type       | Presence    | Description                                                   |
|---------------------|------------|-------------|---------------------------------------------------------------|
| `tick`              | int        | always      | Monotonic tick counter                                        |
| `dt`                | float      | always      | Time step in seconds                                          |
| `session_id`        | string     | always      | Session identifier                                            |
| `environment_delta` | object     | when changed| Only includes climate/soil/water_source fields that changed this tick |
| `entity_updates`    | array      | when changed| Per-entity patches; only changed `state_vars` are included    |
| `entity_spawns`     | array      | on spawn    | Full entity definitions for newly created entities            |
| `entity_removals`   | array      | on death    | IDs of entities to remove from client scene                   |
| `events`            | array      | when fired  | Discrete ecosystem events for client-side effects (see below) |
| `voxel_deltas`      | object     | when changed| Keyed by layer (`nutrients`, `moisture`); sparse coord→value  |

### Motion latent vector

The `motion_latent` field is a fixed-length float array of **4 dimensions**.
It encodes the **style** of movement inferred by the server's ML model from
the entity's current context:

- Dimensions are not human-interpretable by design — they're learned features.
- The client interpolates between consecutive latent vectors in latent space
  (not in bone space) to produce smooth animation transitions.
- Entities without skeletons omit this field.

### Voxel delta encoding rules

- Coordinates are `"x,y,z"` strings (integer grid positions within the 32³ grid).
- Values are floats in `[0.0, 1.0]`.
- The server only emits a delta when `|old_value - new_value| > threshold`
  (default threshold: `0.05`).
- Deltas are keyed by layer name (see voxel layers table below).
- The client maintains a local copy of each layer as an `Image` (R channel)
  and uploads to GPU as `ImageTexture3D` on delta receipt.

### Voxel layers (locked at 4)

| Layer            | Channel | Description                                      |
|------------------|---------|--------------------------------------------------|
| `nutrients`      | R       | Combined N/P/K availability at each voxel        |
| `moisture`       | R       | Water saturation level                           |
| `temperature`    | R       | Local temperature (affected by shade, decay heat)|
| `organic_matter` | R       | Dead plant/animal material available for decomposition |

---

## 4. Control Messages

The client sends control messages to the server as JSON over the same
WebSocket connection used for tick packets. The server dispatches on
the `type` field.

### Control message schema

```json
{
  "type": "control_type",
  ...additional fields per type
}
```

### Supported control messages

| Type       | Additional fields         | Description                                              |
|------------|---------------------------|----------------------------------------------------------|
| `pause`    | —                         | Pause the simulation tick loop                           |
| `resume`   | —                         | Resume the simulation tick loop                          |
| `stop`     | —                         | Stop and tear down the simulation session                |
| `rain`     | `"intensity": float (0–1)` | Trigger a rain event across the entire world            |

### Rain effects

`apply_rain(intensity)` applies the following effects (values shown at
`intensity = 1.0`, scaled linearly):

| Target              | Effect                     | Amount at 1.0 |
|---------------------|----------------------------|---------------|
| Soil moisture       | Boost                      | +0.24         |
| Soil nutrients      | Boost                      | +0.024        |
| Water source levels | Boost                      | +0.32         |
| Plant hydration     | Boost                      | +0.16         |
| Plant health        | Boost                      | +0.08         |
| Animal hydration    | Boost                      | +0.08         |
| Soil evaporation    | Suppressed                 | 80 ticks      |
| Plant evapotranspiration | Suppressed            | 80 ticks      |

---

## 5. Event Channel

Discrete ecosystem events that the client uses to trigger transient visual and
audio effects. Events are fire-and-forget — they don't affect simulation state
(that's already handled by the automaton). They exist purely so the client can
make the world feel alive.

### Event schema

Every event carries a common envelope:

```json
{
  "type": "EVENT_TYPE",
  "tick": 14207,
  "source_id": "entity_that_caused_it",
  "target_id": "entity_that_received_it_or_null",
  "position": [x, y, z]
}
```

### Event types

Events are grouped by implementation status.

**Shipped in v0.0.1-alpha** — emitted by the engine today:

| Event type       | Source          | Target         | Client effect                                          |
|------------------|----------------|----------------|--------------------------------------------------------|
| `CONSUMPTION`    | herbivore      | plant          | Leaf/grass particles at position; plant scale reduction |
| `POLLINATION`    | insect         | plant          | Pollen particle trail between source and target        |
| `REPRODUCTION`   | parent entity  | spawned entity | Birth/bloom effect at position                         |
| `DEATH_NATURAL`  | dying entity   | —              | Fade/collapse animation                                |
| `DEATH_STARVE`   | dying entity   | —              | Wilt/collapse with desaturation effect                 |
| `STATE_CHANGE`   | any entity     | —              | Generic; carries `prev_state` and `new_state` in extra fields |

**Reserved for future species** — defined in the schema but not emitted
until the relevant species are added:

| Event type       | Source          | Target         | Requires                | Client effect                         |
|------------------|----------------|----------------|-------------------------|---------------------------------------|
| `PREDATION`      | predator        | prey           | Carnivore species (v0.2)| Impact particles; prey death anim     |
| `DECOMPOSITION`  | microorganism   | dead matter    | Microorganism species (v0.2) | Soil-darkening particle effect   |
| `ROOT_UPTAKE`    | plant/tree      | —              | Soil visualization (v0.2) | Nutrient-flow lines from soil to root |

The `STATE_CHANGE` event includes additional fields:

```json
{
  "type": "STATE_CHANGE",
  "tick": 14207,
  "source_id": "deer_01",
  "target_id": null,
  "position": [15.2, 0.0, 13.1],
  "prev_state": "IDLE",
  "new_state": "FORAGING"
}
```

### Client-side event handling rules

- Events are **non-blocking** — the client queues them and processes during
  `_process()`. A dropped event is invisible; a delayed entity update is not.
- Events should trigger effects at the `position` field, not at the entity's
  current interpolated position (which may have drifted during latency).
- The client is free to throttle, batch, or drop events under load.
  The server makes no guarantees about event delivery order within a single tick.
