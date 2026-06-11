<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# Intent-Based Client Agency Architecture

> *The server is the nervous system. The client is the body.*

This document describes the shift from a fully server-authoritative tick model to an **intent-based architecture** where the server guides via desire and disposition, and the client executes local agency between ticks. Deviation is treated as emergence, not error.

---

## 1. Philosophy — "The Unseen Hand" Applied to Architecture

The original design was a classic authoritative-server pattern: the server computed exact positions at 10 Hz and streamed them to a passive client that interpolated between frames. The client had no agency — it was a dumb renderer.

This is ecologically wrong. Organisms don't receive GPS coordinates from their nervous system. They receive **intent** — hunger, thirst, fear, reproductive drive — and their body translates those signals into movement through local perception of the environment. A deer doesn't know its exact latitude; it knows grass smells nearby and a wolf's shadow is behind it.

The new architecture mirrors this:

| Old (Authoritative) | New (Intent-Based) |
|---|---|
| Server sends `position: [x, y, z]` every 100ms | Server sends `state`, `drive`, `motion_latent`, `ref_position` every 2s |
| Client interpolates between positions | Client evaluates drives + local perception → decides movement |
| Divergence = error → snap back | Divergence = information → absorb and redirect |
| All target selection on server | Target selection split: server guides, client executes locally |
| Server does everything at 10 Hz | Server does ecology math at 0.5 Hz; client does animation/behavior at 60 Hz |

**Key principle**: The server maintains ecological truth (population counts, soil nutrients, who ate whom). The client handles behavioral presentation (how the deer walks to the grass, how it hesitates near water). If the client's deer wanders slightly off-course, that's not a bug — it's emergence. The nervous system acknowledges the new position and continues guiding from there.

---

## 2. Architecture Overview

### 2.1 Compute Split

```
┌─────────────────────────────────────────────────────────────┐
│  SERVER (0.5 Hz — heavy ecological math)                    │
│                                                             │
│  • Voxel grid updates (nutrients, moisture, organic matter) │
│  • Population lifecycle (birth, death, dormancy)            │
│  • State machine transitions (FORAGING → DRINKING → etc.)   │
│  • Interaction validation & absorption                      │
│  • Motor adapter inference (BYOM latent vectors)            │
│  • Species definition broadcast                             │
└───────────────┬─────────────────────────────────────────────┘
                │ WebSocket (JSON, ~2s intervals)
                │ intent packets + heartbeat responses
                ▼
┌─────────────────────────────────────────────────────────────┐
│  CLIENT (60 Hz — local agency & rendering)                  │
│                                                             │
│  • World model (local entity registry + species defs)       │
│  • Agency engine: evaluate drives → select targets          │
│  • Movement execution with latent-modulated style           │
│  • Interaction triggering (proximity-based, client-side)    │
│  • Position reconciliation (gravity well toward ref_pos)    │
│  • Heartbeat transmission (positions + events upstream)     │
│  • Canvas rendering / skeletal animation                    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Action Mapping — Who Decides What?

| Action | Client decides/triggers | Server authoritative/applies |
|---|---|---|
| **Movement** | ✓ target selection, path modulation | state transitions, speed caps |
| **Herbivory** | ✓ proximity check → reports consumption | ✓ validates diet, applies effects, rate-caps |
| **Predation** | ✓ chase + proximity → reports predation | ✓ validates trophic level, kills prey, updates stats |
| **Pollination** | ✓ finds FRUITING flower → visits | ✓ validates floral affinity, applies pollen transfer |
| **Reproduction** | ✓ seeks mate → reports repro event | ✓ validates sex/drive/threshold, spawns offspring |
| **Drinking** | ✓ approaches water edge | ✓ validates hydration state, applies recovery |
| **Fleeing** | ✓ finds threat locally → runs away | ✓ sets FLEEING state via guard conditions |
| **Decomposition** | — (client doesn't trigger) | ✓ voxel-level mineralization |
| **Plant growth/spreading** | — (plants are stationary) | ✓ state transitions, rhizome spread validation |

---

## 3. Protocol

### 3.1 Session Initialization

**Client → Server**: World definition JSON (unchanged from v0.2 data contract).

**Server → Client**: `session_started` acknowledgement with species reference:

```json
{
  "type": "session_started",
  "session_id": "demo-alpha-001",
  "tick_rate": 2.0,
  "entity_count": 27,
  "species": {
    "deer": {
      "type": "ANIMAL",
      "movement_speed": 3.0,
      "diet_order": [["meadow_grass", 1], ["wildflower", 2]],
      "flee_targets": ["wolf"],
      "is_pollinator": false,
      "pollination_targets": [],
      "has_roost_affinity": false,
      "mating_radius": 12.0
    },
    "songbird": {
      "type": "BIRD",
      "movement_speed": 5.0,
      "diet_order": [["monarch", 1]],
      "flee_targets": ["wolf"],
      "is_pollinator": false,
      "pollination_targets": [],
      "has_roost_affinity": true,
      "mating_radius": 8.0
    }
  }
}
```

The `species` map gives the client everything it needs for local target selection without exposing full simulation internals (allometric scaling factors, interaction matrix weights, guard thresholds).

### 3.2 Tick Packet (Server → Client) — Intent Format

Every ~2 seconds, the server sends an intent packet:

```json
{
  "tick": 47,
  "dt": 2.0,
  "entity_updates": [
    {
      "id": "deer_01",
      "state": "FORAGING",
      "ref_position": [16.3, 0.0, 14.1],
      "drive": {
        "hunger": 0.42,
        "energy": 0.78,
        "hydration": 0.91,
        "health": 0.95,
        "reproductive_drive": 0.12
      },
      "motion_latent": [0.3, -0.1, 0.6, 0.2],
      "_can_consume": true,
      "_can_predate": false,
      "_can_pollinate": false,
      "_repro_eligible": false,
      "_can_drink": false
    }
  ],
  "entity_spawns": [...],
  "entity_removals": ["mushroom_03"],
  "events": [
    { "type": "CONSUMPTION", "source_id": "deer_01", "target_id": "grass_07" }
  ],
  "voxel_deltas": { "moisture": { "16,0,14": 0.72 } },
  "water_sources": [
    { "position": [8, 0, 20], "radius": 3.0, "water_level": 0.85 }
  ]
}
```

**Field semantics**:

| Field | Meaning | Client usage |
|---|---|---|
| `state` | Discrete state machine value (`FORAGING`, `DRINKING`, `FLEEING`, etc.) | Primary behavior selector in agency engine |
| `ref_position` | Server's expected position — **gravity well, not command** | Reconciliation anchor; client may deviate within bounds |
| `drive` | Continuous desire variables (0.0–1.0) | Modulates urgency, triggers secondary behaviors |
| `motion_latent` | 4D vector from BYOM motor adapter: `[pace, caution, posture, social]` | Modulates movement style (speed, wobble, hesitation) |
| `_can_*` flags | Server-side permission gates derived from state + drives | Eligibility checks before client triggers interactions |
| `_ack` | Server absorbed client deviation this tick | Client syncs x/z to ref_position on receipt |

**What's NOT sent**: No authoritative `position` field. The old format included exact coordinates that the client was expected to render verbatim. That's gone — replaced by `ref_position` which is a suggestion, not a command.

### 3.3 Heartbeat (Client → Server)

Every ~1 second, the client sends its local state upstream:

```json
{
  "type": "heartbeat",
  "positions": {
    "deer_01": [17.5, 0.0, 15.2],
    "songbird_03": [22.1, 0.0, 9.8]
  },
  "events": [
    {
      "type": "consumption",
      "source_id": "deer_01",
      "target_id": "grass_07",
      "position": [15.3, 0.0, 13.8]
    }
  ]
}
```

**Field semantics**:

| Field | Meaning | Server usage |
|---|---|---|
| `positions` | Client-agency positions for all mobile entities | Reconciliation: soft-nudge or snap server position |
| `events[].type` | Interaction type (`consumption`, `predation`, `pollination`, `repro`) | Validates against ecological rules, applies effects |
| `events[].source_id` / `target_id` | Entity IDs involved | Looks up entities in simulation state |
| `events[].position` | Where the interaction occurred (client's view) | Sanity check: must be within sensory range of source |

### 3.4 Event Types

Client can report these event types via heartbeat:

```
consumption   — herbivore ate a plant
predation     — predator killed prey
pollination   — pollinator visited a flower
repro         — reproduction attempt (parent + offspring count)
```

The server absorbs events through `EcosystemEngine.absorb_client_events()` which validates against ecological rules and applies effects with rate caps. Invalid events are silently dropped (the client is optimistic; the server is authoritative on outcomes).

---

## 4. Reconciliation Strategy — "Acknowledge + Redirect"

When the client's position diverges from the server's reference, the system doesn't snap or punish. It **acknowledges** and **redirects**.

### 4.1 Server-Side Absorption (`absorb_client_positions`)

```
for each entity in heartbeat.positions:
    divergence = distance(server_pos, client_pos)
    expected_travel = speed * tick_rate * 2.5

    if divergence <= expected_travel:
        # Within bounds — soft nudge server position toward client
        server_pos += (client_pos - server_pos) * 0.3
    else:
        # Significant divergence — snap to client, mark for _ack
        server_pos = client_pos
        pending_acks.add(entity_id)

# On next tick packet: set _ack=True for entities in pending_acks
# Client sees _ack and syncs its position to ref_position
```

### 4.2 Client-Side Reconciliation (`reconcile`)

After receiving a new tick packet, the client reconciles its positions with server references:

```javascript
for each mobile entity:
    if entity._ack:
        // Server acknowledged our deviation — trust it fully
        ent.x = ent.refX;  ent.z = ent.refZ;
    else:
        divergence = distance(ent.x/z, ent.refX/refZ)
        expected_travel = speed * SERVER_TICK_RATE

        if divergence <= expected_travel * 2.5:
            // Within bounds — soft nudge toward reference (gravity well)
            ent.x -= dx * 0.15;  ent.z -= dz * 0.15;
        else:
            // Significant divergence — lerp more aggressively
            ent.x -= dx * 0.5;   ent.z -= dz * 0.5;
```

The server sends `_ack` on the next packet after absorbing a large deviation, which tells the client "I've accepted your position, let's continue from here." This prevents the ping-pong where both sides keep correcting each other.

### 4.3 Why Not Snap?

Three reasons:

1. **Ecological truth**: The server knows soil nutrients, water levels, and population density at every voxel. If a client's deer wanders into an area with no grass, the server won't let it eat there — eligibility flags (`_can_consume`) will be false regardless of position.

2. **Bandwidth**: Snapping every frame would require constant correction messages. The gravity-well approach converges naturally over 1–3 ticks without extra bandwidth.

3. **Emergence**: If a client's deer takes a slightly different path to the same grass patch, that's not an error — it's behavioral variation. The system should allow it within reason.

---

## 5. Client-Side Agency Engine

The agency engine runs at ~60 Hz on the client between server ticks. For each mobile entity, it:

1. **Reads intent**: `state`, `drive` values, `_can_*` eligibility flags
2. **Perceives locally**: queries world model for nearest food/water/threats/mates
3. **Evaluates behavior**: priority chain (flee → drink → mate → forage → hunt → pollinate → wander)
4. **Executes movement**: moves toward selected target with latent-modulated style
5. **Triggers interactions**: on arrival, checks proximity and eligibility, reports events upstream

### 5.1 Behavior Priority Chain

```javascript
function evaluateBehavior(ent, world) {
    // Fleeing (highest priority — threat detected locally)
    if (state === 'FLEEING') return evaluateFleeing(ent, world);

    // Drinking
    if (state === 'DRINKING' || canDrink && hydration < 0.3)
        return evaluateDrinking(ent, world);

    // Reproduction seeking
    if (reproEligible && reproductive_drive > 0.5) {
        const mate = evaluateMateSeeking(ent, world);
        if (mate) return mate;
    }

    // Foraging / Herbivory
    if (state === 'FORAGING' && canConsume)
        return evaluateForaging(ent, world);

    // Hunting / Predation
    if ((state === 'HUNTING' || state === 'FORAGING') && canPredatate)
        return evaluateHunting(ent, world);

    // Pollination
    if (canPollinate && is_pollinator)
        return evaluatePollination(ent, world);

    // Default: wander with latent-modulated style
    return evaluateWandering(ent, world);
}
```

### 5.2 Motion Latent → Movement Style

The 4D motion latent vector from the BYOM motor adapter modulates movement characteristics:

| Dimension | Meaning | Effect on movement |
|---|---|---|
| `latent[0]` | Pace / urgency | Speed multiplier (high = faster, low = slower) |
| `latent[1]` | Caution / alertness | Path wobble (high = more hesitation/curvature) |
| `latent[2]` | Posture | Visual only (future: skeletal animation blend weights) |
| `latent[3]` | Social orientation | Visual only (future: head/body rotation toward flock) |

Currently dimensions 0 and 1 affect movement; 2 and 3 are reserved for future skeletal animation.

### 5.3 Interaction Cooldowns

To prevent event spam when an entity lingers at a target, the client maintains per-target cooldowns (2 seconds). Once a consumption/predation/pollination/repro event is reported for a given `(source_id, target_id)` pair, no further events fire for that pair until the cooldown expires.

---

## 6. Code Changes

### 6.1 Server-Side (`server/ecosim/`)

**`engine.py`**:
- `_build_tick_packet()` — rewritten to emit intent fields (`state`, `drive`, `motion_latent`, `ref_position`, eligibility flags) instead of authoritative positions
- `absorb_client_positions(positions)` — reconciles client-reported positions with server state using soft-nudge or snap + _ack strategy
- `absorb_client_events(events)` — absorbs client-reported interactions (consumption, predation, pollination, repro) with validation and rate caps
- `get_species_definitions()` — exports lightweight species reference (`type`, `movement_speed`, `diet_order`, `flee_targets`, `is_pollinator`, `pollination_targets`) for client-side agency

**`worker.py`**:
- `DEFAULT_TICK_RATE` changed from 0.1s (10 Hz) → 2.0s (0.5 Hz)
- `SimulationSession.absorb_heartbeat(msg)` — routes heartbeat positions and events to engine absorption methods
- `session_started` ack now includes `species` definitions for client-side agency initialization
- `"heartbeat"` added to `CONTROL_HANDLERS`
- `process_request()` — extended to serve arbitrary static files from viz directory (css/, js/) with proper MIME types and path traversal protection

### 6.2 Client-Side (`client/browser/`)

The monolithic `index.html` (~1200 lines of mixed HTML/CSS/JS) was split into modular files:

| File | Lines | Responsibility |
|---|---|---|
| `index.html` | 46 | Thin shell — HTML structure + CSS/JS imports |
| `css/style.css` | 176 | All visual styles (panels, legend, buttons) |
| `js/constants.js` | 61 | Colors, grid config, tick rates, thresholds |
| `js/world-model.js` | 280 | Entity registry, species defs, spatial queries (`findNearest`, `findNearestWater`, `findNearestMate`) |
| `js/agency.js` | 370 | **Client-side behavior engine** — evaluates drives + eligibility → target selection → movement execution → interaction triggers with cooldowns |
| `js/heartbeat.js` | 65 | Upstream position/event reporting to server (1 Hz) |
| `js/reconciliation.js` | 50 | Position reconciliation (trust within bounds, nudge/snap on divergence, _ack handling) |
| `js/renderer.js` | 510 | All canvas drawing functions (entities, water, moisture heatmap, particles) |
| `js/particles.js` | 38 | Particle system for event visualizations |
| `js/main.js` | 340 | Entry point — WebSocket setup, render loop coordination, UI controls |

### 6.3 Test Harness (`server/tests/client_harness.py`)

Headless Python client that simulates a full browser session:
- Connects via WebSocket, sends world definition
- Receives `session_started` with species definitions
- Validates intent packet format (state + drive + motion_latent + ref_position)
- Tests heartbeat reconciliation (soft-nudge within bounds)
- Tests event absorption (consumption reported → server applies)
- Tests divergence snap + _ack (large deviation → server snaps and acknowledges)
- Tests entity lifecycle across multiple ticks

Run manually against a live server:
```bash
# Terminal 1: start server
uv run python -m ecosim.worker --log-level DEBUG

# Terminal 2: run harness
uv run python tests/client_harness.py --host localhost --port 8001
```

---

## 7. Migration Notes

### From Authoritative to Intent-Based

If you have an existing client that expects authoritative positions, the migration path is:

1. **Server sends both** during transition: keep `position` alongside `ref_position` and intent fields
2. **Client reads intent first**, falls back to `position` if intent fields are missing
3. **Remove `position`** once all clients support intent format

### Bandwidth Impact

At 0.5 Hz with ~30 entities, a typical tick packet is ~4-6 KB (vs. ~2 KB at 10 Hz with positions only). Heartbeats add ~1 KB/s upstream. Net increase is modest because the server sends far fewer packets per second (5 vs. 100).

### What Breaks

- Clients that expect `position` in entity updates will see NaN/undefined — they must read `ref_position` instead
- The old `world.json` format still works for world definition, but clients should use the species definitions from `session_started` rather than parsing entity metadata inline
- Headless mode (`--headless`) is unaffected — it uses `SimulationSession.step()` directly without WebSocket

---

## 8. Future Work

- **Skeletal animation**: dimensions 2 and 3 of motion latent are reserved for posture/social orientation blend weights in Godot client
- **Adaptive tick rate**: server could increase tick frequency during high-activity periods (many state transitions) and decrease during quiescence
- **Partial heartbeats**: client only needs to send positions that changed significantly since last heartbeat
- **Event acknowledgment**: server could echo absorbed events back with outcome (`accepted`/`rejected` + reason) for client-side feedback
- **Multi-client support**: current design is one-worker-per-session; intent-based architecture makes it easier to add spectator clients that receive tick packets without sending heartbeats
