# Interaction Actor Model + Effects Architecture

**Status:** Phase 1 Complete · Phase 2 (Flow/Guard Actors) Pending  
**Created:** 2026-05-30  
**Last Updated:** 2026-05-30  
**Owner:** līlā Ecosystem Engine Team  

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current Architecture Analysis](#current-architecture-analysis)
3. [Target Architecture Overview](#target-architecture-overview)
4. [Effects Model — Immutable Delta Descriptions](#effects-model--immutable-delta-descriptions)
5. [Interaction Actor Protocol](#interaction-actor-protocol)
6. [Phase 1: Effects Extraction + Interaction Actors ✅ COMPLETE](#phase-1-effects-extraction--interaction-actors-complete)
7. [Phase 2: Flow + Guard Actors (Pending)](#phase-2-flow--guard-actors-pending)
8. [Phase 3: Distributed Readiness (Future)](#phase-3-distributed-readiness-future)
9. [Serialization Layer — Pluggable Format Design](#serialization-layer--pluggable-format-design)
10. [Effect Application Order & Conflict Resolution](#effect-application-order--conflict-resolution)
11. [File Structure (Current)](#file-structure-current)
12. [Migration Checklist](#migration-checklist)
13. [Open Questions](#open-questions)

---

## Executive Summary

This document describes the three-phase refactoring of the līlā ecosystem engine from its monolithic `EcosystemEngine` class into an **Interaction Actor Model** with an **Immutable Effects System**. The result is:

- **Distributed-ready**: Effects are immutable data structures that can be serialized, transmitted across network boundaries, and replayed deterministically.
- **Truly parallel**: All actors run concurrently (read-only state → effects emission), then effects are applied atomically in a single batch pass. No entity mutates another's state during actor execution.
- **Extensible**: Adding a new interaction type requires only one actor class + its effect types. The engine core never changes.
- **Testable**: Actors are pure functions of their context — unit-testable without the full simulation harness.

---

## Current Architecture Analysis

### `engine.py` (~2353 lines) — Hybrid Automaton with Actor Integration

The current engine is a single class that owns all simulation state and implements behavior through a **dual-path architecture**:

```
EcosystemEngine (hybrid orchestrator, ~2353 lines)
├── step() → 7-phase sequential loop over ALL entities
│   ├── Phase 1: _apply_flow()     → trait-based or legacy flow routing
│   │   ├── Trait path: diet_type dispatch → _flow_consumer/producer/decomposer
│   │   └── Legacy path: entity type dispatch → _flow_animal/plant/insect/microorganism
│   ├── Phase 2: Interactions      → ACTOR-BASED (trait) or inline (legacy)
│   │   ├── Trait path: actor_registry[species].resolve(ctx) → EffectBus.apply_batch()
│   │   └── Legacy path: _resolve_interactions(entity) — inline flee/predation/herbivory/pollination
│   ├── Phase 3: Guards            → trait-based or legacy guard routing
│   │   ├── Trait path: diet_type dispatch → _guards_consumer/producer/decomposer
│   │   └── Legacy path: entity type dispatch → _guards_animal/plant/insect/microorganism
│   ├── Phase 4: Voxel effects     → inline soil mutations
│   ├── Phase 5: Water/soil evaporation
│   ├── Phase 6: Motor inference (BYOM)
│   └── Phase 7: Spawn/Kill (deferred lists)
├── Actor Registry                 → build_interaction_registry(compiled)
├── Effect Bus                     → collect, resolve conflicts, apply atomically
├── Legacy Flow Functions          → _flow_animal, _flow_plant, _flow_insect, _flow_microorganism
├── Legacy Guard Functions         → _guards_animal, _guards_plant, _guards_insect, _guards_microorganism
└── Legacy Helpers                 → _find_mate_legacy, _reproduction_event_legacy, etc.
```

### Key Design: Dual-Path Architecture

The engine supports **two parallel execution paths**:

1. **Trait-based path** (new worlds with `species_definitions`): Uses the actor system — actors return immutable Effect objects that are collected then applied atomically. Flow and guards route by `diet_type`.
2. **Legacy path** (worlds without `species_definitions`): Falls back to inline entity-type-based logic for flow, interactions, and guards. This ensures backward compatibility with all existing world files.

The `_is_legacy` flag determines which path is taken at each phase boundary.

---

## Target Architecture Overview

```
EcosystemEngine (thin orchestrator, ~300 lines)
├── Spatial Index (query layer)
├── Entity Registry
├── Voxel Grid
│
├── Actor Registry
│   ├── Interaction Actors (Phase 1 ✅ COMPLETE)
│   │   ├── FleeActor          → detects predators, emits FleeEffect
│   │   ├── PredationActor     → detects prey proximity, emits StateVarDelta + DeathEffect
│   │   ├── HerbivoryActor     → detects plants in range, emits StateVarDelta (both sides)
│   │   ├── PollinationActor   → detects fruiting flowers, emits StateVarDelta + LingerEffect
│   │   └── DecompositionActor → detects organic matter, emits VoxelDelta
│   │
│   ├── Flow Actors (Phase 2 ⏳ PENDING)
│   │   ├── ConsumerFlow       → hunger/energy/hydration/repro drive evolution
│   │   ├── ProducerFlow       → growth/water uptake/Liebig's law
│   │   └── DecomposerFlow     → activity/population dynamics
│   │
│   └── Guard Actors (Phase 2 ⏳ PENDING)
│       ├── ConsumerGuards     → hysteresis-based state transitions
│       ├── ProducerGuards     → wilting/fruiting/dormancy
│       └── DecomposerGuards   → active/blooming/dormant
│
├── Effect Bus (collects, batches, applies effects) ✅ COMPLETE
│   ├── Collect: gather all effects from all actors this tick
│   ├── Resolve: handle conflicts (e.g., entity dies mid-tick)
│   └── Apply: single-pass atomic application to state
│
└── Serialization Layer (Phase 3 ⏳ FUTURE)
    ├── JSON serializer (default, WebSocket-compatible)
    ├── Pluggable interface for msgpack/protobuf later
    └── Effect log for deterministic replay
```

### Core Design Principles

1. **Read-only actors**: Each actor receives a read-only snapshot of state and emits effects. No side effects during actor execution.
2. **Batch application**: All effects from all actors are collected, then applied in a single atomic pass at the end of each phase (or tick).
3. **Pure function pattern**: `resolve(ctx) → list[Effect]` — given the same context, always produces the same effects. Deterministic by construction.
4. **Separation of concerns**: The engine manages lifecycle and spatial indexing; actors manage behavior logic.

---

## Effects Model — Immutable Delta Descriptions

Effects are immutable dataclasses that describe *what changed* rather than performing mutations. They serve as the universal currency between actors and the effect applier.

### Effect Base Class

```python
from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class EffectType(str, Enum):
    # State variable changes
    STATE_VAR_DELTA = "state_var_delta"       # Increment/decrement a state var
    SET_STATE_VAR = "set_state_var"           # Set to absolute value
    
    # Entity lifecycle
    SPAWN_ENTITY = "spawn_entity"             # Create new entity
    REMOVE_ENTITY = "remove_entity"           # Remove existing entity
    STATE_TRANSITION = "state_transition"     # Change discrete state
    
    # Environmental changes
    VOXEL_DELTA = "voxel_delta"               # Change voxel layer value
    VOXEL_BATCH_DELTA = "voxel_batch_delta"   # Multiple voxel changes at once
    
    # Entity behavior modifiers
    LINGER_EFFECT = "linger_effect"           # Stay at location for N ticks
    CLEAR_TARGET = "clear_target"             # Reset movement target
    SET_TARGET = "set_target"                 # Set new movement target
    
    # Events (for client broadcast)
    EVENT_RECORD = "event_record"             # Log simulation event


@dataclass(frozen=True, kw_only=True)
class Effect:
    """Base class for all simulation effects."""
    effect_type: EffectType
    tick: int
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (used by JSON serializer)."""
        return asdict(self)  # or custom serialization


@dataclass(frozen=True, kw_only=True)
class StateVarDelta(Effect):
    """An entity's state variable changes by a delta."""
    effect_type: EffectType = EffectType.STATE_VAR_DELTA
    entity_id: str
    var_name: str       # "hunger", "energy", "health", ...
    delta: float         # can be negative (drain) or positive (recovery)


@dataclass(frozen=True, kw_only=True)
class SetStateVar(Effect):
    """Set a state variable to an absolute value."""
    effect_type: EffectType = EffectType.SET_STATE_VAR
    entity_id: str
    var_name: str
    value: float


@dataclass(frozen=True, kw_only=True)
class StateTransition(Effect):
    """Request a discrete state change."""
    effect_type: EffectType = EffectType.STATE_TRANSITION
    entity_id: str
    new_state: str       # "FORAGING", "FLEEING", "DYING", ...


@dataclass(frozen=True, kw_only=True)
class VoxelDelta(Effect):
    """Change to the voxel grid."""
    effect_type: EffectType = EffectType.VOXEL_DELTA
    layer: str           # "moisture", "nutrients", "organic_matter"
    x: int
    y: int
    z: int
    delta: float


@dataclass(frozen=True, kw_only=True)
class VoxelBatchDelta(Effect):
    """Multiple voxel changes at once (batched for efficiency)."""
    effect_type: EffectType = EffectType.VOXEL_BATCH_DELTA
    changes: list[tuple[str, int, int, int, float]]  # (layer, x, y, z, delta)


@dataclass(frozen=True, kw_only=True)
class SpawnEntity(Effect):
    """Request a new entity be created."""
    effect_type: EffectType = EffectType.SPAWN_ENTITY
    entity_id: str
    type: str            # "ANIMAL", "PLANT", etc.
    species: str | None
    position: list[float]  # [x, y, z]
    metadata: dict[str, Any]
    state_vars: dict[str, float]
    skeleton_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class RemoveEntity(Effect):
    """Request an entity be removed."""
    effect_type: EffectType = EffectType.REMOVE_ENTITY
    entity_id: str


@dataclass(frozen=True, kw_only=True)
class LingerEffect(Effect):
    """Entity stays at current location for N ticks."""
    effect_type: EffectType = EffectType.LINGER_EFFECT
    entity_id: str
    linger_ticks: int


@dataclass(frozen=True, kw_only=True)
class ClearTarget(Effect):
    """Clear an entity's movement target."""
    effect_type: EffectType = EffectType.CLEAR_TARGET
    entity_id: str


@dataclass(frozen=True, kw_only=True)
class SetTarget(Effect):
    """Set a new movement target for an entity."""
    effect_type: EffectType = EffectType.SET_TARGET
    entity_id: str
    position: list[float]  # [x, y, z]


@dataclass(frozen=True, kw_only=True)
class EventRecord(Effect):
    """Log a simulation event for client broadcast."""
    effect_type: EffectType = EffectType.EVENT_RECORD
    event_type: str       # "PREDATION", "CONSUMPTION", "STATE_CHANGE", ...
    source_id: str | None
    target_id: str | None
    position: list[float]  # [x, y, z]
    extra: dict[str, Any] = field(default_factory=dict)


# ── Effect Application Order Priority ────────────────────────────────────────
# When multiple effects target the same entity in one tick, apply in this order:
#   1. REMOVE_ENTITY (entity is gone; no further effects matter)
#   2. STATE_TRANSITION → DYING/DEAD (entity entering terminal state)
#   3. SET_STATE_VAR (absolute values before deltas — ensures correct base)
#   4. LINGER_EFFECT / CLEAR_TARGET / SET_TARGET (behavior modifiers)
#   5. STATE_VAR_DELTA (incremental changes)
#   6. VOXEL_BATCH_DELTA / VOXEL_DELTA (environmental changes, no entity conflict)
#   7. SPAWN_ENTITY (new entities don't conflict with existing ones)
#   8. EVENT_RECORD (side-effect-free logging)


EFFECT_PRIORITY: dict[EffectType, int] = {
    EffectType.REMOVE_ENTITY: 0,
    EffectType.STATE_TRANSITION: 1,
    EffectType.SET_STATE_VAR: 2,
    EffectType.LINGER_EFFECT: 3,
    EffectType.CLEAR_TARGET: 4,
    EffectType.SET_TARGET: 5,
    EffectType.STATE_VAR_DELTA: 6,
    EffectType.VOXEL_BATCH_DELTA: 7,
    EffectType.VOXEL_DELTA: 7,
    EffectType.SPAWN_ENTITY: 8,
    EffectType.EVENT_RECORD: 9,
}
```

### Why Immutable Dataclasses?

1. **Deterministic replay**: Serialize effects → send to another node → apply in order → identical state. No shared mutable dicts.
2. **Network transport**: Frozen dataclasses serialize cleanly to JSON (and later msgpack/protobuf).
3. **Conflict detection**: When two actors produce conflicting effects on the same entity, the effect bus can detect and resolve them before application.
4. **Testing**: Each actor is a pure function — pass in context, assert output effects. No mock state needed.

---

## Interaction Actor Protocol

Each actor implements a clean interface:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InteractionContext:
    """Read-only snapshot passed to actors.
    
    Actors receive this context and must not mutate it. All state access
    goes through read-only views or queries.
    """
    tick: int
    
    # The entity this actor is evaluating (read-only view)
    entity: dict[str, Any]
    
    # Trait-derived parameters for the entity's species
    params: DerivedParams | None  # None in legacy mode
    
    # Spatial query results — entities within sensory range
    nearby_entities: list[dict[str, Any]] = field(default_factory=list)
    
    # Read-only voxel access (no mutations through context)
    voxel_grid: VoxelManager = field(repr=False)
    
    # Water sources (read-only)
    water_sources: list[dict[str, Any]] = field(default_factory=list, repr=False)
    
    # Biome and climate configuration
    biome: BiomeConfig
    climate: dict[str, float] = field(default_factory=dict)


class InteractionActor(ABC):
    """Base protocol for all simulation actors.
    
    Each actor implements resolve() which takes a read-only context
    and returns a list of effects describing what should change.
    The engine's effect bus collects all effects from all actors,
    then applies them atomically.
    """
    
    @abstractmethod
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        """Evaluate conditions and return effects to apply.
        
        Args:
            ctx: Read-only simulation context snapshot.
            
        Returns:
            List of immutable Effect objects describing state changes.
            Empty list if no action is needed.
        """
        ...


class FlowActor(InteractionActor):
    """Subtype for continuous flow actors (Phase 2)."""
    
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        raise NotImplementedError


class GuardActor(InteractionActor):
    """Subtype for discrete state transition actors (Phase 2)."""
    
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        raise NotImplementedError
```

---

## Phase 1: Effects Extraction + Interaction Actors ✅ COMPLETE

**Status:** Implemented and tested. All interaction types (flee, predation, herbivory, pollination) are actor-based for trait worlds. Legacy worlds use inline fallback.

### File Structure (Current)

```
ecosim/
├── engine.py              ← Hybrid orchestrator (~2353 lines)
│                           ├── Trait path: actor_registry + effect_bus
│                           └── Legacy path: inline flow/guards/interactions
├── effects.py             ← ✅ Effect dataclasses + EffectBus (339 lines)
├── actors/                ← ✅ Actor system directory
│   ├── __init__.py        ← ✅ InteractionContext, InteractionActor base, build_interaction_registry()
│   └── interaction_actors.py  ← ✅ FleeActor, PredationActor, HerbivoryActor, PollinationActor (481 lines)
├── interactions.py        ← Unchanged: compile-time templates + InteractionParams
├── traits.py              ← Unchanged
├── trait_compiler.py      ← Unchanged
├── entities.py            ← Unchanged
├── voxel_manager.py       ← Unchanged
└── biome.py               ← Unchanged
```

### PredationActor — Before vs After

**Before (in engine.py, directly mutating):**

```python
def _predation_event(self, predator: dict, prey: dict, p: DerivedParams) -> None:
    """Execute a predation event: predator kills and consumes prey."""
    predator["state_vars"]["hunger"] = max(
        0.0, predator["state_vars"]["hunger"] - p.predation_relief)
    predator["state_vars"]["energy"] = min(
        1.0, predator["state_vars"]["energy"] + p.predation_energy_gain)
    prey["state"] = "DYING"
    self._schedule_removal(prey)
    self._deposit_organic_matter(prey, self._get_params(prey))
    self._events.append({
        "type": "PREDATION", "tick": self.tick,
        "source_id": predator["id"], "target_id": prey["id"],
        "position": list(prey["position"]),
    })
```

**After (PredationActor, returning effects):**

```python
class PredationActor(InteractionActor):
    """Carnivore/insectivore attempts to catch nearby prey.
    
    Detection: predator in HUNTING state with hunger > 0.3, prey within
    PREDATION_CATCH_DISTANCE (1.5), and species match from interaction matrix.
    """
    
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        if ctx.params is None:
            return []
        
        p = ctx.params
        if p.diet_type not in ("carnivore", "insectivore", "omnivore"):
            return []
        
        # Check hunting state and hunger threshold
        if ctx.entity["state"] != "HUNTING" or ctx.entity["state_vars"]["hunger"] <= 0.3:
            return []
        
        # Find catchable prey from interaction matrix
        prey_species = [
            s for s, _ in self._get_diet_order(p.species_id)
            if any(ix.interaction_type == "predation"
                   for ix in ctx.compiled.get_interactions(p.species_id, s))
        ]
        
        prey = None
        best_dist = float("inf")
        for other in ctx.nearby_entities:
            if other.get("species") not in prey_species:
                continue
            d = self._distance(ctx.entity["position"], other["position"])
            if d < PREDATION_CATCH_DISTANCE and d < best_dist:
                best_dist = d
                prey = other
        
        if prey is None:
            return []
        
        # Build effects — no mutations, just descriptions of what should happen
        gx, gy, gz = ctx.voxel_grid.world_to_grid(*prey["position"])
        deposit_amount = self._compute_om_deposit(prey, p)
        
        effects: list[Effect] = [
            StateVarDelta(
                entity_id=ctx.entity["id"],
                var_name="hunger",
                delta=-p.predation_relief,
                tick=ctx.tick,
            ),
            StateVarDelta(
                entity_id=ctx.entity["id"],
                var_name="energy",
                delta=p.predation_energy_gain,
                tick=ctx.tick,
            ),
            SetStateVar(
                entity_id=prey["id"],
                var_name="health",
                value=0.0,
                tick=ctx.tick,
            ),
            StateTransition(
                entity_id=prey["id"],
                new_state="DYING",
                tick=ctx.tick,
            ),
            RemoveEntity(entity_id=prey["id"], tick=ctx.tick),
            VoxelDelta(
                layer="organic_matter", x=gx, y=gy, z=gz,
                delta=deposit_amount, tick=ctx.tick,
            ),
            EventRecord(
                event_type="PREDATION",
                source_id=ctx.entity["id"],
                target_id=prey["id"],
                position=list(prey["position"]),
                tick=ctx.tick,
            ),
        ]
        
        return effects
    
    @staticmethod
    def _distance(a: list[float], b: list[float]) -> float:
        dx = a[0] - b[0]
        dz = a[2] - b[2]
        return math.sqrt(dx * dx + dz * dz)
    
    @staticmethod
    def _compute_om_deposit(entity: dict, params: DerivedParams | None) -> float:
        if params is not None:
            deposit = min(OM_DEPOSIT_MAX, params.metabolic_rate * OM_DEPOSIT_SCALE)
            return max(deposit, OM_DEPOSIT_MIN)
        mass = entity.get("metadata", {}).get("body_mass", 10.0)
        return min(0.3, mass / 500.0)
```

### HerbivoryActor — Before vs After

**Before (in engine.py):**

```python
def _consumption_event(self, herbivore: dict, plant: dict, p: DerivedParams) -> None:
    """Execute an herbivory event: herbivore grazes on plant."""
    herbivore["state_vars"]["hunger"] = max(
        0.0, herbivore["state_vars"]["hunger"] - p.herbivory_relief)
    plant["state_vars"]["growth"] = max(
        0.0, plant["state_vars"]["growth"] - p.consumption_damage_growth * self.rate_consumption)
    plant["state_vars"]["health"] = max(
        0.0, plant["state_vars"]["health"] - p.consumption_damage_health * self.rate_consumption)
    self._events.append({
        "type": "CONSUMPTION", "tick": self.tick,
        "source_id": herbivore["id"], "target_id": plant["id"],
        "position": list(plant["position"]),
    })
```

**After (HerbivoryActor):**

```python
class HerbivoryActor(InteractionActor):
    """Herbivore/omnivore attempts to consume nearby plants.
    
    Detection: entity in FORAGING state with hunger > HERBIVORY_MIN_HUNGER,
    plant within HERBIVORY_CONSUME_DISTANCE (1.0), species match from diet order.
    """
    
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        if ctx.params is None:
            return []
        
        p = ctx.params
        if ctx.entity["state"] != "FORAGING" or ctx.entity["state_vars"]["hunger"] <= HERBIVORY_MIN_HUNGER:
            return []
        
        diet_order = self._get_diet_order(p.species_id)
        if not diet_order:
            return []
        
        # Find best target by preference ordering
        best_target = None
        best_pref = 999
        
        for other in ctx.nearby_entities:
            if other["state"] in ("DEAD", "DYING", "DORMANT"):
                continue
            if self._distance(ctx.entity["position"], other["position"]) >= HERBIVORY_CONSUME_DISTANCE:
                continue
            
            other_species = other.get("species", "")
            for target_species, pref in diet_order:
                if other_species == target_species:
                    ixns = ctx.compiled.get_interactions(p.species_id, other_species)
                    for ix in ixns:
                        if (ix.interaction_type == "herbivory"
                                and other.get("state_vars", {}).get("growth", 0) > 0.1
                                and pref < best_pref):
                            best_pref = pref
                            best_target = other
                    break
        
        if best_target is None:
            return []
        
        plant = best_target
        rate_consumption = self._get_rate_multiplier("consumption")  # from engine config
        
        effects: list[Effect] = [
            StateVarDelta(
                entity_id=ctx.entity["id"],
                var_name="hunger",
                delta=-p.herbivory_relief,
                tick=ctx.tick,
            ),
            SetStateVar(
                entity_id=plant["id"],
                var_name="growth",
                value=max(0.0, plant["state_vars"]["growth"] - p.consumption_damage_growth * rate_consumption),
                tick=ctx.tick,
            ),
            SetStateVar(
                entity_id=plant["id"],
                var_name="health",
                value=max(0.0, plant["state_vars"]["health"] - p.consumption_damage_health * rate_consumption),
                tick=ctx.tick,
            ),
            EventRecord(
                event_type="CONSUMPTION",
                source_id=ctx.entity["id"],
                target_id=plant["id"],
                position=list(plant["position"]),
                tick=ctx.tick,
            ),
        ]
        
        return effects
```

### FleeActor — Before vs After

**Before (in engine.py):**

```python
def _resolve_flee(self, e: dict, p: DerivedParams, pos: list[float]) -> None:
    """Check for nearby predators and trigger flee response."""
    flee_from = self.compiled.get_flee_targets(p.species_id)
    if not flee_from or p.speed <= 0:
        return
    nearby = self._entities_in_range(pos, p.sensory_range, e["id"])
    for other in nearby:
        if other.get("species", "") in flee_from:
            if self._distance(pos, other["position"]) < FLEE_TRIGGER_DISTANCE:
                old_state = e["state"]
                e["state"] = "FLEEING"
                e["_target"] = self._flee_direction(pos, other["position"])
                if old_state != "FLEEING":
                    self._emit_state_change(e, old_state, "FLEEING")
```

**After (FleeActor):**

```python
class FleeActor(InteractionActor):
    """Check for nearby predators and trigger flee response.
    
    Detection: entity has flee targets from interaction matrix, predator
    within FLEE_TRIGGER_DISTANCE (2.0).
    """
    
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        if ctx.params is None:
            return []
        
        p = ctx.params
        if p.speed <= 0:
            return []
        
        flee_targets = self._get_flee_targets(p.species_id)
        if not flee_targets:
            return []
        
        # Check each nearby entity for predator match
        for other in ctx.nearby_entities:
            if other.get("species", "") in flee_targets:
                if self._distance(ctx.entity["position"], other["position"]) < FLEE_TRIGGER_DISTANCE:
                    escape_pos = self._flee_direction(
                        ctx.entity["position"], other["position"]
                    )
                    
                    old_state = ctx.entity["state"]
                    effects: list[Effect] = [
                        StateTransition(
                            entity_id=ctx.entity["id"],
                            new_state="FLEEING",
                            tick=ctx.tick,
                        ),
                        SetTarget(
                            entity_id=ctx.entity["id"],
                            position=escape_pos,
                            tick=ctx.tick,
                        ),
                    ]
                    
                    if old_state != "FLEEING":
                        effects.append(EventRecord(
                            event_type="STATE_CHANGE",
                            source_id=ctx.entity["id"],
                            target_id=None,
                            position=list(ctx.entity["position"]),
                            extra={"prev_state": old_state, "new_state": "FLEEING"},
                            tick=ctx.tick,
                        ))
                    
                    return effects  # First predator triggers flee; no need to check others
        
        return []
```

### PollinationActor — Before vs After

**Before (in engine.py):**

```python
def _pollination_event(self, pollinator: dict, plant: dict,
                       p: DerivedParams, ix_params) -> None:
    """Execute a pollination event: pollinator visits flower."""
    plant["state_vars"]["health"] = min(
        1.0, plant["state_vars"]["health"] + POLLINATION_HEALTH_BOOST)
    pollinator["state_vars"]["hunger"] = max(
        0.0, pollinator["state_vars"]["hunger"] - p.pollination_relief)
    if "hydration" in pollinator["state_vars"]:
        pollinator["state_vars"]["hydration"] = min(
            1.0, pollinator["state_vars"]["hydration"] + p.pollination_relief * 0.5)
    pollinator["_linger"] = ix_params.linger_ticks
    pollinator["_target"] = None
    plant["_pollination_cooldown"] = ix_params.cooldown_ticks
```

**After (PollinationActor):**

```python
class PollinationActor(InteractionActor):
    """Pollinator visits a nearby FRUITING flower.
    
    Detection: entity has floral_affinity, plant is in FRUITING state,
    not on pollination cooldown, species match from interaction matrix.
    """
    
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        if ctx.params is None or not ctx.params.floral_affinity:
            return []
        
        # Skip if already lingering at a flower
        if ctx.entity.get("_linger", 0) > 0:
            return []
        
        for other in ctx.nearby_entities:
            other_species = other.get("species", "")
            ixns = ctx.compiled.get_interactions(ctx.params.species_id, other_species)
            
            for ix in ixns:
                if (ix.interaction_type == "pollination"
                        and other["state"] == "FRUITING"
                        and other.get("_pollination_cooldown", 0) <= 0):
                    
                    plant = other
                    
                    # Build visited flowers tracking effect
                    expiry_tick = ctx.tick + ix.linger_ticks + ix.cooldown_ticks
                    
                    effects: list[Effect] = [
                        SetStateVar(
                            entity_id=plant["id"],
                            var_name="health",
                            value=min(1.0, plant["state_vars"]["health"] + POLLINATION_HEALTH_BOOST),
                            tick=ctx.tick,
                        ),
                        StateVarDelta(
                            entity_id=ctx.entity["id"],
                            var_name="hunger",
                            delta=-ctx.params.pollination_relief,
                            tick=ctx.tick,
                        ),
                    ]
                    
                    # Nectar is mostly water — restores hydration for nectarivores
                    if "hydration" in ctx.entity["state_vars"]:
                        effects.append(StateVarDelta(
                            entity_id=ctx.entity["id"],
                            var_name="hydration",
                            delta=ctx.params.pollination_relief * 0.5,
                            tick=ctx.tick,
                        ))
                    
                    # Linger at flower
                    effects.extend([
                        LingerEffect(
                            entity_id=ctx.entity["id"],
                            linger_ticks=ix.linger_ticks,
                            tick=ctx.tick,
                        ),
                        ClearTarget(entity_id=ctx.entity["id"], tick=ctx.tick),
                        SetStateVar(
                            entity_id=plant["id"],
                            var_name="_pollination_cooldown",  # internal tracking var
                            value=float(ix.cooldown_ticks),
                            tick=ctx.tick,
                        ),
                    ])
                    
                    effects.append(EventRecord(
                        event_type="POLLINATION",
                        source_id=ctx.entity["id"],
                        target_id=plant["id"],
                        position=list(plant["position"]),
                        extra={
                            "linger_ticks": ix.linger_ticks,
                            "cooldown_ticks": ix.cooldown_ticks,
                            "expiry_tick": expiry_tick,
                        },
                        tick=ctx.tick,
                    ))
                    
                    return effects  # One pollination per tick
        
        return []
```

### Engine Refactoring — Phase 1 `step()` Method (Trait Path)

The engine's step method for trait-based worlds uses the actor system:

```python
def step(self, dt: float = 0.1) -> dict[str, Any]:
    """Advance the simulation by one tick."""
    self.tick += 1
    self._events.clear()
    self._spawns.clear()
    self._removals.clear()
    self._rebuild_spatial_index()
    
    # Phase 1: Flow — trait-based or legacy routing (see below)
    if self._is_legacy:
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._apply_flow(entity, dt)
    else:
        flow_effects = []
        for entity in list(self.entities.values()):
            if not is_alive(entity):
                continue
            actor = self._get_flow_actor(entity.get("species"))
            if actor:
                ctx = self._build_interaction_context(entity, dt)
                effects = actor.resolve(ctx)
                flow_effects.extend(effects)
        apply_flow_effects(flow_effects, ...)  # (Phase 2 — pending)
    
    # Phase 2: Interactions — ACTOR-BASED for trait worlds
    if self._is_legacy:
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._resolve_interactions(entity)
    else:
        interaction_effects = []
        for entity in list(self.entities.values()):
            if not is_alive(entity):
                continue
            actor = self.actor_registry.get(entity.get("species"))
            if actor:
                ctx = self._build_interaction_context(entity, dt)
                effects = actor.resolve(ctx)
                interaction_effects.extend(effects)
        # Apply interaction effects atomically
        self.effect_bus.apply_batch(
            interaction_effects,
            self.entities, self.voxels,
            self._spawns, self._removals, self._events,
        )
    
    # Phase 3: Guards — trait-based or legacy routing (see below)
    if self._is_legacy:
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._evaluate_guards(entity)
    else:
        guard_effects = []
        for entity in list(self.entities.values()):
            if not is_alive(entity):
                continue
            actor = self._get_guard_actor(entity.get("species"))
            if actor:
                ctx = self._build_interaction_context(entity, dt)
                effects = actor.resolve(ctx)
                guard_effects.extend(effects)
        apply_guard_effects(guard_effects, ...)  # (Phase 2 — pending)
    
    # Phase 4-7: Voxel effects, water, motor, spawn/kill (unchanged)
    ...
```

### Effect Bus — Collect and Apply ✅ IMPLEMENTED

```python
class EffectBus:
    """Collects effects from all actors and applies them atomically.
    
    This is the key mechanism that enables truly parallel actor execution:
    1. All actors run concurrently (or sequentially, same result) because they only read state.
    2. Effects are collected into a single list.
    3. Conflicts are resolved (e.g., entity removed mid-tick).
    4. Effects are applied in priority order to the shared state.
    """
    
    def apply_batch(
        self,
        effects: list[Effect],
        entities: dict[str, dict],
        voxels: VoxelManager,
        spawns: list,
        removals: list,
        events: list,
    ) -> None:
        """Apply a batch of effects to simulation state.
        
        Args:
            effects: All effects collected from all actors this tick.
            entities: Entity registry (mutated in place).
            voxels: Voxel grid manager (mutated in place).
            spawns: Deferred spawn list (populated by SPAWN_ENTITY effects).
            removals: Deferred removal list (populated by REMOVE_ENTITY effects).
            events: Event log for client broadcast.
        """
        # Sort by priority — terminal operations first
        sorted_effects = sorted(effects, key=lambda e: EFFECT_PRIORITY.get(e.effect_type, 9))
        
        # Track which entities are removed this tick (for conflict resolution)
        removed_ids: set[str] = set()
        
        for effect in sorted_effects:
            if isinstance(effect, RemoveEntity):
                removals.append(effect.entity_id)
                removed_ids.add(effect.entity_id)
            
            elif isinstance(effect, StateTransition):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    old_state = entity["state"]
                    entity["state"] = effect.new_state
                    # Emit state change event if different
                    if old_state != effect.new_state:
                        events.append({
                            "type": "STATE_CHANGE", "tick": effect.tick,
                            "source_id": effect.entity_id, "target_id": None,
                            "position": entity["position"],
                            "prev_state": old_state, "new_state": effect.new_state,
                        })
            
            elif isinstance(effect, SetStateVar):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    entity["state_vars"][effect.var_name] = effect.value
            
            elif isinstance(effect, StateVarDelta):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    sv = entity["state_vars"]
                    current = sv.get(effect.var_name, 0.0)
                    new_val = max(0.0, min(1.0, current + effect.delta))
                    sv[effect.var_name] = new_val
            
            elif isinstance(effect, LingerEffect):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    entity["_linger"] = effect.linger_ticks
                    entity["velocity"] = [0.0, 0.0, 0.0]
            
            elif isinstance(effect, ClearTarget):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    entity["_target"] = None
            
            elif isinstance(effect, SetTarget):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    entity["_target"] = effect.position
            
            elif isinstance(effect, VoxelDelta):
                voxels.add(effect.layer, effect.x, effect.y, effect.z, effect.delta)
            
            elif isinstance(effect, SpawnEntity):
                spawns.append({
                    "id": effect.entity_id,
                    "type": effect.type,
                    "species": effect.species,
                    "position": effect.position,
                    "metadata": effect.metadata,
                    "state_vars": effect.state_vars,
                    "skeleton_id": effect.skeleton_id,
                })
            
            elif isinstance(effect, EventRecord):
                events.append({
                    "type": effect.event_type,
                    "tick": effect.tick,
                    "source_id": effect.source_id,
                    "target_id": effect.target_id,
                    "position": effect.position,
                    **effect.extra,
                })
```

---

## Phase 2: Flow + Guard Actors (Pending)

**Status:** NOT YET IMPLEMENTED. Flow and guard logic remains inline in `engine.py` for both trait-based and legacy paths. The actor infrastructure is ready — only the flow/guard actor classes need to be written.

### File Structure (Target for Phase 2)

```
ecosim/
├── engine.py              ← Refactored: ~300 lines (orchestrator only)
├── effects.py             ← Unchanged from Phase 1 ✅
├── actors/                ← Expanded directory
│   ├── __init__.py        ← Actor registry, base classes ✅
│   ├── interaction_actors.py  ← FleeActor, PredationActor, HerbivoryActor, PollinationActor ✅
│   ├── flow_actors.py     ← ⏳ PENDING: ConsumerFlowActor, ProducerFlowActor, DecomposerFlowActor
│   └── guard_actors.py    ← ⏳ PENDING: ConsumerGuardActor, ProducerGuardActor, DecomposerGuardActor
├── interactions.py        ← Unchanged
├── traits.py              ← Unchanged
├── trait_compiler.py      ← Unchanged
├── entities.py            ← Unchanged
├── voxel_manager.py       ← Unchanged
└── biome.py               ← Unchanged
```

### ConsumerFlowActor — Before vs After (Design)

**Before (in engine.py, ~120 lines of inline flow logic):**

The current `_flow_consumer` handles: hunger buildup, energy drain/recovery, hydration loss, drinking recovery, near-water bonus, reproductive drive, health degradation under starvation/dehydration, colony health, and movement toward targets. All rate constants come from `DerivedParams`.

**After (ConsumerFlowActor — design only):**

```python
class ConsumerFlowActor(FlowActor):
    """Continuous flow for all mobile consumers.
    
    Handles: hunger buildup, energy drain/recovery, hydration loss,
    drinking recovery, near-water bonus, reproductive drive, health
    degradation under starvation/dehydration, colony health.
    
    Movement is handled by a separate MovementActor (or inline in engine).
    """
    
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        if ctx.params is None:
            return []
        
        p = ctx.params
        sv = ctx.entity["state_vars"]
        self._ensure_consumer_vars(sv)  # Ensure all keys exist
        
        effects: list[Effect] = []
        biome_mod = ctx.biome.hunger_rate_modifier * ctx.biome.metabolic_scaling
        temp = ctx.climate.get("temperature", 20.0)
        
        # ── Hunger — increases with metabolism ──
        hunger_delta = p.hunger_rate * biome_mod * self._get_rate_multiplier("hunger")
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="hunger",
            delta=hunger_delta, tick=ctx.tick,
        ))
        
        # ── Energy — drains during activity, recovers at rest ──
        if ctx.entity["state"] in ACTIVE_ENERGY_DRAIN_STATES:
            drain = p.energy_drain * ctx.biome.energy_drain_modifier
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="energy",
                delta=-drain, tick=ctx.tick,
            ))
        elif ctx.entity["state"] in ENERGY_RECOVERY_STATES:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="energy",
                delta=p.energy_recovery, tick=ctx.tick,
            ))
        
        # ... (hydration, near-water bonus, reproductive drive, health)
        
        return effects
```

### Guard Actors — Before vs After (Design)

**Before**: `_guards_consumer`, `_guards_producer`, `_guards_decomposer` (~150 lines total) handle discrete state transitions with hysteresis.

**After**: Each guard actor returns `StateTransition` effects and optionally `EventRecord` for state changes:

```python
class ConsumerGuardActor(GuardActor):
    """Guard evaluation for consumers.
    
    State machine priority (highest to lowest):
    1. Death (health ≤ 0 or age ≥ lifespan)
    2. Colony swarming (colony_health < 0.3)
    3. Fleeing (set by interaction resolver, cleared when target reached)
    4. Reproduction (drive > threshold AND mate available)
    5. Drinking (hydration hysteresis: enter < 0.2, exit ≥ 0.6)
    6. Resting (energy hysteresis: enter < 0.2, exit ≥ 0.5)
    7. Foraging/Hunting (hunger hysteresis: enter ≥ 0.3, exit < 0.15)
    8. Idle (default)
    
    Returns StateTransition effects for each state change detected.
    """
    
    def resolve(self, ctx: InteractionContext) -> list[Effect]:
        if ctx.params is None:
            return []
        
        p = ctx.params
        sv = ctx.entity["state_vars"]
        old_state = ctx.entity["state"]
        meta = ctx.entity["metadata"]
        effects: list[Effect] = []
        
        # ── Death ──
        if p.generation_time_ticks > 0:
            lifespan = p.generation_time_ticks
        else:
            lifespan = meta.get("lifespan", 1000.0)
        
        health_key = "colony_health" if "colony_health" in sv else "health"
        if sv.get(health_key, 1.0) <= 0.0:
            effects.extend([
                StateTransition(entity_id=ctx.entity["id"], new_state="DYING", tick=ctx.tick),
                RemoveEntity(entity_id=ctx.entity["id"], tick=ctx.tick),
                EventRecord(event_type="DEATH_STARVE", source_id=ctx.entity["id"],
                           target_id=None, position=list(ctx.entity["position"]),
                           tick=ctx.tick),
            ])
        # ... (rest of guard logic)
        
        return effects
```

---

## Phase 3: Distributed Readiness (Future)

**Status:** NOT STARTED. Serialization layer for network transport and deterministic replay.

### Goals

1. **JSON serializer**: Convert Effect objects to/from JSON for WebSocket transmission.
2. **Pluggable format interface**: Abstract serialization behind a protocol so msgpack/protobuf can be swapped in later without changing actor code.
3. **Effect log**: Record all effects per tick for deterministic replay and debugging.
4. **Network transport**: Send effect batches from server to client, or between simulation nodes.

---

## Serialization Layer — Pluggable Format Design

```python
class EffectSerializer(ABC):
    """Protocol for serializing/deserializing effects."""
    
    @abstractmethod
    def serialize(self, effects: list[Effect]) -> bytes | str: ...
    
    @abstractmethod
    def deserialize(self, data: bytes | str) -> list[Effect]: ...


class JsonSerializer(EffectSerializer):
    """Default JSON serializer — WebSocket-compatible."""
    
    def serialize(self, effects: list[Effect]) -> str:
        return json.dumps([e.to_dict() for e in effects])
    
    def deserialize(self, data: str) -> list[Effect]:
        raw = json.loads(data)
        # Map dicts back to Effect subclasses by effect_type
        ...


class MsgpackSerializer(EffectSerializer):
    """Future: more compact binary format."""
    ...
```

---

## Effect Application Order & Conflict Resolution

When multiple effects target the same entity in one tick, apply in this order:

1. **REMOVE_ENTITY** — entity is gone; no further effects matter
2. **STATE_TRANSITION → DYING/DEAD** — entity entering terminal state
3. **SET_STATE_VAR** — absolute values before deltas (ensures correct base)
4. **LINGER_EFFECT / CLEAR_TARGET / SET_TARGET** — behavior modifiers
5. **STATE_VAR_DELTA** — incremental changes
6. **VOXEL_BATCH_DELTA / VOXEL_DELTA** — environmental changes, no entity conflict
7. **SPAWN_ENTITY** — new entities don't conflict with existing ones
8. **EVENT_RECORD** — side-effect-free logging

The `EffectBus.apply_batch()` method sorts effects by priority before application and tracks removed IDs to prevent cascading mutations on dead entities.

---

## File Structure (Current)

```
ecosim/
├── engine.py              ← Hybrid orchestrator (~2353 lines)
│                           ├── Trait path: actor_registry + effect_bus for interactions
│                           └── Legacy path: inline flow/guards/interactions
├── effects.py             ← ✅ Effect dataclasses + EffectBus (339 lines)
├── actors/                ← ✅ Actor system directory
│   ├── __init__.py        ← ✅ InteractionContext, InteractionActor base, build_interaction_registry()
│   └── interaction_actors.py  ← ✅ FleeActor, PredationActor, HerbivoryActor, PollinationActor (481 lines)
├── interactions.py        ← Compile-time templates + InteractionParams
├── traits.py              ← TraitVector, DerivedParams, allometric derivations
├── trait_compiler.py      ← TraitCompiler, CompiledEcology, LegacyParams, compile_world()
├── entities.py            ← Entity schemas, init_entity(), is_alive(), is_mobile()
├── voxel_manager.py       ← Sparse 3D grid, delta tracking
├── biome.py               ← Biome presets → BiomeConfig
├── model_adapter.py       ← MotorAdapter protocol, ContextSpec
└── worker.py              ← Async WS tick loop + HTTP viz server
```

---

## Migration Checklist

### Phase 1 ✅ COMPLETE
- [x] Effect dataclasses defined in `effects.py`
- [x] EffectBus implemented with priority-based application and conflict resolution
- [x] InteractionContext dataclass for read-only actor input
- [x] InteractionActor base class with resolve() protocol
- [x] FleeActor — detects predators, emits StateTransition + SetTarget effects
- [x] PredationActor — detects prey proximity, emits StateVarDelta + DeathEffect
- [x] HerbivoryActor — detects plants in range, emits StateVarDelta (both sides)
- [x] PollinationActor — detects fruiting flowers, emits StateVarDelta + LingerEffect
- [x] Actor registry: `build_interaction_registry(compiled)` maps species → actor instances
- [x] Engine step() uses actor system for trait worlds, inline fallback for legacy
- [x] Legacy flow functions restored (entity-type-based routing) — commit ec021eb
- [x] Legacy guard functions restored (entity-type-based routing) — commit ec021eb
- [x] All 84 tests passing

### Phase 2 ⏳ PENDING
- [ ] ConsumerFlowActor — hunger/energy/hydration/repro drive evolution
- [ ] ProducerFlowActor — growth/water uptake/Liebig's law
- [ ] DecomposerFlowActor — activity/population dynamics
- [ ] ConsumerGuardActor — hysteresis-based state transitions
- [ ] ProducerGuardActor — wilting/fruiting/dormancy
- [ ] DecomposerGuardActor — active/blooming/dormant
- [ ] Engine step() refactored to use flow/guard actors for trait worlds
- [ ] Legacy flow/guard functions kept as fallback (already done)

### Phase 3 ⏳ FUTURE
- [ ] JsonSerializer for Effect objects
- [ ] Pluggable serializer interface (msgpack/protobuf ready)
- [ ] Effect log for deterministic replay
- [ ] Network transport tests

---

## Open Questions

1. **Flow actor granularity**: Should flow actors emit individual StateVarDelta effects per variable, or batch them into a single SET_STATE_VAR effect? Batching reduces effect count but loses the ability to apply deltas in priority order.

2. **Movement handling**: Movement mutates entity position directly (requires engine-level access). Should this remain inline in the engine, or should a MovementActor emit SetTarget effects that the engine resolves?

3. **Legacy world migration path**: Should we encourage users to add `species_definitions` to their worlds, or is the legacy path sufficient for simple use cases? The dual-path architecture supports both indefinitely.

4. **Effect bus performance**: For large simulations (1000+ entities), sorting effects by priority each tick adds O(n log n) overhead. Could we batch-sort once per phase instead of per entity?

5. **Deterministic replay**: Should the effect log include the full context snapshot, or just the effects? Full context enables exact replay but increases storage; effects-only is more compact but requires re-running the simulation to reconstruct state.

6. **Actor composition**: Some behaviors span multiple phases (e.g., pollination involves interaction detection + lingering behavior). Should we support composite actors that coordinate across phases, or keep each actor phase-scoped?
