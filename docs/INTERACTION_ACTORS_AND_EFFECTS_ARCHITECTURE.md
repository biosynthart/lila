# Interaction Actor Model + Effects Architecture

**Status:** Design Phase  
**Created:** 2026-05-30  
**Owner:** līlā Ecosystem Engine Team  

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current Architecture Analysis](#current-architecture-analysis)
3. [Target Architecture Overview](#target-architecture-overview)
4. [Effects Model — Immutable Delta Descriptions](#effects-model--immutable-delta-descriptions)
5. [Interaction Actor Protocol](#interaction-actor-protocol)
6. [Phase 1: Effects Extraction + Interaction Actors](#phase-1-effects-extraction--interaction-actors)
7. [Phase 2: Flow + Guard Actors](#phase-2-flow--guard-actors)
8. [Phase 3: Distributed Readiness](#phase-3-distributed-readiness)
9. [Serialization Layer — Pluggable Format Design](#serialization-layer--pluggable-format-design)
10. [Effect Application Order & Conflict Resolution](#effect-application-order--conflict-resolution)
11. [File Structure (Target)](#file-structure-target)
12. [Migration Checklist](#migration-checklist)
13. [Open Questions](#open-questions)

---

## Executive Summary

This document describes a three-phase refactoring of the līlā ecosystem engine from its current monolithic `EcosystemEngine` class (~1907 lines, all logic inline) into an **Interaction Actor Model** with an **Immutable Effects System**. The result is:

- **Distributed-ready**: Effects are immutable data structures that can be serialized, transmitted across network boundaries, and replayed deterministically.
- **Truly parallel**: All actors run concurrently (read-only state → effects emission), then effects are applied atomically in a single batch pass. No entity mutates another's state during actor execution.
- **Extensible**: Adding a new interaction type requires only one actor class + its effect types. The engine core never changes.
- **Testable**: Actors are pure functions of their context — unit-testable without the full simulation harness.

---

## Current Architecture Analysis

### `engine.py` (~1907 lines) — Monolithic Hybrid Automaton

The current engine is a single class that owns all simulation state and implements every behavior inline:

```
EcosystemEngine (monolithic, 1907 lines)
├── step() → 7-phase sequential loop over ALL entities
│   ├── Phase 1: _apply_flow()     → inline consumer/producer/decomposer flow
│   ├── Phase 2: _resolve_interactions() → inline flee/predation/herbivory/pollination
│   ├── Phase 3: _evaluate_guards() → inline state machine per role
│   ├── Phase 4: _apply_voxel_effects() → inline soil mutations
│   ├── Phase 5: water/soil evaporation
│   ├── Phase 6: motor inference (BYOM)
│   └── Phase 7: spawn/kill (deferred lists)
├── Interaction event handlers (_predation_event, _consumption_event, etc.)
│   → directly mutate entity dicts (e.g., predator["state_vars"]["hunger"] = ...)
├── Movement target selection — inline spatial queries + heuristics
├── Reproduction — inline child creation with inheritance logic
├── Plant spreading — inline vegetative propagation
└── Water management, rain, evaporation — all inline
```

### `interactions.py` (~300 lines) — Compile-Time Templates Only

Defines interaction templates (Herbivory, Predation, Pollination, Decomposition) that are evaluated at init time to build an interaction matrix. But the **actual execution** of these interactions is still in `engine.py` (`_predation_event`, `_consumption_event`, `_pollination_event`). The templates provide parameters; the engine provides behavior.

### `traits.py` + `trait_compiler.py` — Compile-Time Intelligence

Handle allometric derivation and compile-time interaction matrix building. This separation is already correct — the trait compiler does expensive work once at init, leaving only O(1) dict lookups per tick.

### Key Problem

The engine is both the **orchestrator** and the **behavior implementation**. Every interaction's detection logic, resolution logic, and state mutation are interleaved in one class. Adding a new interaction type means adding more methods to this already 1907-line file. State mutations happen immediately during phase execution — there is no separation between "what should happen" and "apply what happened."

---

## Target Architecture Overview

```
EcosystemEngine (thin orchestrator, ~300 lines)
├── Spatial Index (query layer)
├── Entity Registry
├── Voxel Grid
│
├── Actor Registry
│   ├── Interaction Actors (Phase 1)
│   │   ├── FleeActor          → detects predators, emits FleeEffect
│   │   ├── PredationActor     → detects prey proximity, emits StateVarDelta + DeathEffect
│   │   ├── HerbivoryActor     → detects plants in range, emits StateVarDelta (both sides)
│   │   ├── PollinationActor   → detects fruiting flowers, emits StateVarDelta + LingerEffect
│   │   └── DecompositionActor → detects organic matter, emits VoxelDelta
│   │
│   ├── Flow Actors (Phase 2)
│   │   ├── ConsumerFlow       → hunger/energy/hydration/repro drive evolution
│   │   ├── ProducerFlow       → growth/water uptake/Liebig's law
│   │   └── DecomposerFlow     → activity/population dynamics
│   │
│   └── Guard Actors (Phase 2)
│       ├── ConsumerGuards     → hysteresis-based state transitions
│       ├── ProducerGuards     → wilting/fruiting/dormancy
│       └── DecomposerGuards   → active/blooming/dormant
│
├── Effect Bus (collects, batches, applies effects)
│   ├── Collect: gather all effects from all actors this tick
│   ├── Resolve: handle conflicts (e.g., entity dies mid-tick)
│   └── Apply: single-pass atomic application to state
│
└── Serialization Layer (Phase 3)
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

## Phase 1: Effects Extraction + Interaction Actors

**Goal**: Extract predation, herbivory, pollination, and flee interactions into actor classes that return effects instead of mutating state directly. The engine becomes a thin coordinator.

### File Structure (Phase 1)

```
ecosim/
├── engine.py              ← Refactored: ~400 lines (orchestrator only)
├── effects.py             ← NEW: All Effect dataclasses + effect bus
├── actors/                ← NEW directory
│   ├── __init__.py        ← Actor registry, base classes
│   ├── interaction_actors.py  ← FleeActor, PredationActor, HerbivoryActor, PollinationActor
│   └── movement_actors.py     ← (Phase 2 — deferred)
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
                   for ix in ctx.voxel_grid.get_interactions(p.species_id, s))  # via trait compiler lookup
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
                    ixns = ctx.voxel_grid.get_interactions(p.species_id, other_species)  # via trait compiler
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
    # ... visited flowers tracking ...
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
            ixns = ctx.voxel_grid.get_interactions(ctx.params.species_id, other_species)
            
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

### Engine Refactoring — Phase 1 `step()` Method

The engine's step method becomes a thin coordinator:

```python
def step(self, dt: float = 0.1) -> dict[str, Any]:
    """Advance the simulation by one tick (Phase 1 refactored)."""
    self.tick += 1
    self._events.clear()
    self._spawns.clear()
    self._removals.clear()
    self._rebuild_spatial_index()
    
    # Build read-only context for all actors
    contexts = self._build_actor_contexts(dt)
    
    # Phase 1: Flow — continuous state variable updates (Phase 2 only; skip in Phase 1)
    # In Phase 1, flow logic remains inline. Moved to Phase 2 actors.
    for entity in list(self.entities.values()):
        if is_alive(entity):
            self._apply_flow_legacy(entity, dt)  # Keep existing inline flow for now
    
    # Phase 2: Interactions — entity↔entity events (NOW ACTOR-BASED)
    all_effects = []
    
    for ctx in contexts:
        actor = self._interaction_actors.get(ctx.entity["id"])
        if actor and is_alive(ctx.entity):
            effects = actor.resolve(ctx)
            all_effects.extend(effects)
    
    # Apply interaction effects atomically
    self._apply_effects(all_effects, dt)
    
    # Phase 3: Guards — discrete state transitions (Phase 2 only; skip in Phase 1)
    for entity in list(self.entities.values()):
        if is_alive(entity):
            self._evaluate_guards_legacy(entity)  # Keep existing inline guards for now
    
    # Phase 4: Voxel effects — entity impact on soil (inline, unchanged)
    for entity in list(self.entities.values()):
        if is_alive(entity):
            self._apply_voxel_effects(entity, dt)
    
    # Phase 5-7: Water, motor, spawn/kill (unchanged)
    ...
    
    return self._build_tick_packet(dt)
```

### Effect Bus — Collect and Apply

```python
class EffectBus:
    """Collects effects from all actors and applies them atomically.
    
    This is the key mechanism that enables truly parallel actor execution:
    1. All actors run concurrently (or sequentially, same result) because they only read state.
    2. Effects are collected into a single list.
    3. Conflicts are resolved (e.g., entity removed mid-tick).
    4. Effects are applied in priority order to the shared state.
    """
    
    def apply_effects(
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

## Phase 2: Flow + Guard Actors

**Goal**: Extract `_flow_consumer`, `_flow_producer`, `_flow_decomposer` into flow actors and `_guards_*` methods into guard actors. The engine becomes a thin orchestrator calling actor registry.

### File Structure (Phase 2)

```
ecosim/
├── engine.py              ← Refactored: ~300 lines (orchestrator only)
├── effects.py             ← Unchanged from Phase 1
├── actors/                ← Expanded directory
│   ├── __init__.py        ← Actor registry, base classes
│   ├── interaction_actors.py  ← FleeActor, PredationActor, HerbivoryActor, PollinationActor (Phase 1)
│   ├── flow_actors.py     ← NEW: ConsumerFlowActor, ProducerFlowActor, DecomposerFlowActor
│   └── guard_actors.py    ← NEW: ConsumerGuardActor, ProducerGuardActor, DecomposerGuardActor
├── interactions.py        ← Unchanged
├── traits.py              ← Unchanged
├── trait_compiler.py      ← Unchanged
├── entities.py            ← Unchanged
├── voxel_manager.py       ← Unchanged
└── biome.py               ← Unchanged
```

### ConsumerFlowActor — Before vs After

**Before (in engine.py, ~120 lines of inline flow logic):**

The current `_flow_consumer` handles: hunger buildup, energy drain/recovery, hydration loss, drinking recovery, near-water bonus, reproductive drive, health degradation under starvation/dehydration, colony health, and movement toward targets. All rate constants come from `DerivedParams`.

**After (ConsumerFlowActor):**

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
            if p.locomotion in ("flight_insect", "flight_bird"):
                drain *= max(0.1, min(1.0, p.metabolic_rate * 5.0))
            if p.diet_type == "nectarivore" and ctx.entity["state"] == "FORAGING":
                drain = 0.0
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="energy",
                delta=-drain, tick=ctx.tick,
            ))
        elif ctx.entity["state"] in ENERGY_RECOVERY_STATES:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="energy",
                delta=p.energy_recovery, tick=ctx.tick,
            ))
        
        # Lingering at a resource (e.g. pollination visit) also recovers energy
        if ctx.entity.get("_linger", 0) > 0:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="energy",
                delta=p.energy_recovery, tick=ctx.tick,
            ))
        
        # ── Hydration — temperature-driven loss, soil-based recovery when drinking ──
        if ctx.entity["state"] == "DRINKING":
            gx, gy, gz = ctx.voxel_grid.world_to_grid(*ctx.entity["position"])
            soil_moisture = ctx.voxel_grid.get("moisture", gx, gy, gz)
            recovery = DRINK_RECOVERY_RATE * soil_moisture
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="hydration",
                delta=recovery, tick=ctx.tick,
            ))
            # Drinking depletes local soil moisture and water source
            effects.append(VoxelDelta(
                layer="moisture", x=gx, y=gy, z=gz,
                delta=-DRINK_SOIL_DRAIN * self._get_rate_multiplier("thirst"),
                tick=ctx.tick,
            ))
        else:
            thirst = p.thirst_rate * (temp / 30.0)
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="hydration",
                delta=-thirst, tick=ctx.tick,
            ))
        
        # ── Near-water bonus ──
        if self._is_near_water(ctx):
            hunger_relief = p.hunger_rate * WATER_PROXIMITY_HUNGER_FACTOR
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="hunger",
                delta=-hunger_relief, tick=ctx.tick,
            ))
            if p.diet_type == "nectarivore":
                effects.append(StateVarDelta(
                    entity_id=ctx.entity["id"], var_name="hydration",
                    delta=p.thirst_rate * WATER_PROXIMITY_HUNGER_FACTOR,
                    tick=ctx.tick,
                ))
            if "colony_health" in sv:
                effects.append(StateVarDelta(
                    entity_id=ctx.entity["id"], var_name="colony_health",
                    delta=p.energy_recovery * WATER_PROXIMITY_COLONY_FACTOR,
                    tick=ctx.tick,
                ))
        
        # ── Reproductive drive — builds when healthy, decays under stress ──
        if (sv["energy"] > REPRO_BUILD_MIN_ENERGY
                and sv["hunger"] < REPRO_BUILD_MAX_HUNGER
                and sv.get("health", 1.0) > REPRO_BUILD_MIN_HEALTH):
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="reproductive_drive",
                delta=p.repro_drive_build * self._get_rate_multiplier("reproduction"),
                tick=ctx.tick,
            ))
        elif sv["hunger"] > REPRO_DECAY_HUNGER or sv["energy"] < REPRO_DECAY_ENERGY:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="reproductive_drive",
                delta=-p.repro_drive_decay, tick=ctx.tick,
            ))
        
        # ── Health — degrades under critical starvation or dehydration ──
        if sv["hunger"] > STARVATION_HUNGER:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="health",
                delta=-p.health_drain_starving, tick=ctx.tick,
            ))
        if sv["hydration"] < DEHYDRATION_HYDRATION:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="health",
                delta=-p.health_drain_dehydrated, tick=ctx.tick,
            ))
        
        # ── Colony health — accelerated drain under stress ──
        if "colony_health" in sv:
            if sv["hunger"] > COLONY_STRESS_HUNGER or sv["energy"] < COLONY_STRESS_ENERGY:
                drain = p.health_drain_starving * (1.0 + sv["hunger"] * 2.0)
                effects.append(StateVarDelta(
                    entity_id=ctx.entity["id"], var_name="colony_health",
                    delta=-drain, tick=ctx.tick,
                ))
        
        return effects
    
    @staticmethod
    def _ensure_consumer_vars(sv: dict[str, Any]) -> None:
        sv.setdefault("hunger", 0.0)
        sv.setdefault("energy", 1.0)
        sv.setdefault("hydration", 1.0)
        sv.setdefault("health", 1.0)
        sv.setdefault("reproductive_drive", 0.0)
        sv.setdefault("age", 0.0)
    
    def _is_near_water(self, ctx: InteractionContext) -> bool:
        pos = ctx.entity["position"]
        for source in ctx.water_sources:
            if source["water_level"] < WATER_DRY_THRESHOLD:
                continue
            dx = pos[0] - source["position"][0]
            dz = pos[2] - source["position"][2]
            dist = math.sqrt(dx * dx + dz * dz)
            if dist <= source["radius"] + 1.0:
                return True
        return False
    
    def _get_rate_multiplier(self, name: str) -> float:
        """Access rate multipliers from engine config (injected via context or closure)."""
        # In Phase 2, the engine passes these through a RateConfig in the context
        ...
```

### ProducerFlowActor — Before vs After

**Before**: `_flow_producer` (~80 lines) handles evapotranspiration, water uptake from soil, growth via Liebig's law, nutrient uptake, health degradation, tree collapse pressure, and vegetative spreading.

**After**: `ProducerFlowActor` returns effects for hydration changes, growth increments, nutrient store updates, health drain deltas, and a special `SpawnEntity` effect for vegetative spreading (when conditions are met).

### DecomposerFlowActor — Before vs After

**Before**: `_flow_decomposer` (~20 lines) handles activity equilibrium and population dynamics.

**After**: `DecomposerFlowActor` returns effects for activity changes, population growth/decay, and a `VoxelDelta` effect for organic matter consumption + nutrient release (currently handled in `_apply_voxel_effects`, but logically belongs to the decomposer's flow).

### Guard Actors — Before vs After

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
        elif sv["age"] >= lifespan:
            effects.extend([
                StateTransition(entity_id=ctx.entity["id"], new_state="DYING", tick=ctx.tick),
                RemoveEntity(entity_id=ctx.entity["id"], tick=ctx.tick),
                EventRecord(event_type="DEATH_NATURAL", source_id=ctx.entity["id"],
                           target_id=None, position=list(ctx.entity["position"]),
                           tick=ctx.tick),
            ])
        
        # ── Colony swarming ──
        elif "colony_health" in sv and sv["colony_health"] < 0.3:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="SWARMING", tick=ctx.tick,
            ))
        
        # ── Fleeing (managed by interaction resolver) ──
        elif ctx.entity["state"] == "FLEEING":
            if ctx.entity.get("_target") is None:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
        
        # ── Drinking (hysteresis) ──
        elif ctx.entity["state"] == "DRINKING":
            if sv.get("hydration", 1.0) >= p.hydration_exit:
                new_state = "FORAGING" if p.diet_type == "nectarivore" else "IDLE"
                effects.extend([
                    StateTransition(entity_id=ctx.entity["id"], new_state=new_state, tick=ctx.tick),
                    ClearTarget(entity_id=ctx.entity["id"], tick=ctx.tick),
                ])
        elif sv.get("hydration", 1.0) < p.hydration_enter and p.diet_type != "nectarivore":
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="DRINKING", tick=ctx.tick,
            ))
        
        # ── Resting (hysteresis) ──
        elif ctx.entity["state"] == "RESTING":
            if sv["energy"] >= p.energy_exit:
                new_state = "FORAGING" if p.diet_type == "nectarivore" else "IDLE"
                effects.extend([
                    StateTransition(entity_id=ctx.entity["id"], new_state=new_state, tick=ctx.tick),
                    ClearTarget(entity_id=ctx.entity["id"], tick=ctx.tick),
                ])
        elif sv["energy"] < p.energy_enter:
            if not (p.diet_type == "nectarivore" and ctx.entity["state"] == "FORAGING"):
                effects.extend([
                    StateTransition(entity_id=ctx.entity["id"], new_state="RESTING", tick=ctx.tick),
                    ClearTarget(entity_id=ctx.entity["id"], tick=ctx.tick),
                ])
        
        # ── Foraging / Hunting (hysteresis) ──
        elif ctx.entity["state"] in ("FORAGING", "HUNTING"):
            if sv["hunger"] < p.hunger_exit and p.diet_type != "nectarivore":
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
            elif p.diet_type in ("carnivore", "insectivore") and sv["hunger"] > CARNIVORE_HUNT_HUNGER:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="HUNTING", tick=ctx.tick,
                ))
        elif sv["hunger"] >= p.hunger_enter:
            if p.diet_type in ("carnivore", "insectivore") and sv["hunger"] > CARNIVORE_HUNT_HUNGER:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="HUNTING", tick=ctx.tick,
                ))
            else:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="FORAGING", tick=ctx.tick,
                ))
        
        # ── Default ──
        else:
            if ctx.entity["state"] not in ACTIVE_MOVEMENT_STATES | {"RESTING", "REPRODUCING", "SWARMING"}:
                new_state = "FORAGING" if p.diet_type == "nectarivore" else "IDLE"
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state=new_state, tick=ctx.tick,
                ))
        
        # ── Reproduction (checked independently) ──
        if (sv.get("reproductive_drive", 0) > p.repro_drive_threshold 
                and self._has_mate(ctx)
                and ctx.entity["state"] not in ("DYING", "REPRODUCING", "SWARMING")):
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="REPRODUCING", tick=ctx.tick,
            ))
        
        # Emit state change event if something changed
        if effects and old_state != ctx.entity["state"]:
            for eff in effects:
                if isinstance(eff, StateTransition):
                    effects.append(EventRecord(
                        event_type="STATE_CHANGE",
                        source_id=ctx.entity["id"],
                        target_id=None,
                        position=list(ctx.entity["position"]),
                        extra={"prev_state": old_state, "new_state": eff.new_state},
                        tick=ctx.tick,
                    ))
                    break
        
        return effects
    
    @staticmethod
    def _has_mate(ctx: InteractionContext) -> bool:
        """Check if a compatible mate is within sensory range."""
        p = ctx.params
        if p is None:
            return False
        for other in ctx.nearby_entities:
            if (other.get("species") == ctx.entity.get("species")
                    and other["state_vars"].get("reproductive_drive", 0) > 0.3):
                return True
        return False
```

### Engine Refactoring — Phase 2 `step()` Method

After Phase 2, the engine's step method is minimal:

```python
def step(self, dt: float = 0.1) -> dict[str, Any]:
    """Advance the simulation by one tick (Phase 2 refactored)."""
    self.tick += 1
    self._events.clear()
    self._spawns.clear()
    self._removals.clear()
    self._rebuild_spatial_index()
    
    # Build read-only contexts for all actors
    contexts = self._build_actor_contexts(dt)
    
    # Phase 1: Flow — continuous state variable updates (ACTOR-BASED)
    flow_effects = []
    for ctx in contexts:
        if is_alive(ctx.entity):
            actor = self._flow_actors.get(ctx.entity["id"])
            if actor:
                flow_effects.extend(actor.resolve(ctx))
    
    # Phase 2: Interactions — entity↔entity events (ACTOR-BASED)
    interaction_effects = []
    for ctx in contexts:
        if is_alive(ctx.entity):
            actor = self._interaction_actors.get(ctx.entity["id"])
            if actor:
                interaction_effects.extend(actor.resolve(ctx))
    
    # Phase 3: Guards — discrete state transitions (ACTOR-BASED)
    guard_effects = []
    for ctx in contexts:
        if is_alive(ctx.entity):
            actor = self._guard_actors.get(ctx.entity["id"])
            if actor:
                guard_effects.extend(actor.resolve(ctx))
    
    # ── Apply all effects atomically, phase by phase ──
    self.effect_bus.apply_batch(flow_effects, ...)       # Phase 1 effects
    self.effect_bus.apply_batch(interaction_effects, ...) # Phase 2 effects
    self.effect_bus.apply_batch(guard_effects, ...)       # Phase 3 effects
    
    # Phase 4: Voxel effects — entity impact on soil (inline, unchanged)
    for entity in list(self.entities.values()):
        if is_alive(entity):
            self._apply_voxel_effects(entity, dt)
    
    # Phase 5-7: Water, motor, spawn/kill (unchanged)
    ...
    
    return self._build_tick_packet(dt)
```

---

## Phase 3: Distributed Readiness

**Goal**: Make the system ready for distributed simulation — read-only context views, effect serialization, spatial index abstraction.

### File Structure (Phase 3)

```
ecosim/
├── engine.py              ← Unchanged from Phase 2 (~300 lines)
├── effects.py             ← Unchanged from Phase 1
├── actors/                ← Unchanged from Phase 2
│   ├── __init__.py
│   ├── interaction_actors.py
│   ├── flow_actors.py
│   └── guard_actors.py
├── serialization/         ← NEW directory
│   ├── __init__.py        ← Serializer registry, base class
│   ├── json_serializer.py ← Default JSON serializer (WebSocket-compatible)
│   └── interface.py       ← Pluggable serializer protocol
├── spatial/               ← NEW directory (Phase 3)
│   ├── __init__.py
│   └── index.py           ← Spatial index abstraction (hash grid, quadtree, etc.)
├── interactions.py        ← Unchanged
├── traits.py              ← Unchanged
├── trait_compiler.py      ← Unchanged
├── entities.py            ← Unchanged
├── voxel_manager.py       ← Unchanged
└── biome.py               ← Unchanged
```

### Pluggable Serialization Interface

```python
# serialization/interface.py

from abc import ABC, abstractmethod
from typing import Any
from ..effects import Effect


class Serializer(ABC):
    """Protocol for effect serializers.
    
    Implementations convert effects to/from wire format (bytes or string).
    The engine uses the registered serializer to:
    1. Serialize effects for WebSocket transmission
    2. Deserialize effects received from other simulation nodes
    3. Log effects for deterministic replay
    
    Default implementation: JSON (human-readable, WebSocket-compatible).
    Future implementations: msgpack (binary efficiency), protobuf (schema enforcement).
    """
    
    @abstractmethod
    def serialize(self, effect: Effect) -> bytes | str:
        """Serialize a single effect to wire format."""
        ...
    
    @abstractmethod
    def deserialize(self, data: bytes | str) -> Effect:
        """Deserialize an effect from wire format."""
        ...
    
    @abstractmethod
    def serialize_batch(self, effects: list[Effect]) -> bytes | str:
        """Serialize a batch of effects (more efficient than individual)."""
        ...
    
    @abstractmethod
    def deserialize_batch(self, data: bytes | str) -> list[Effect]:
        """Deserialize a batch of effects."""
        ...


class JsonSerializer(Serializer):
    """Default JSON serializer — human-readable, WebSocket-compatible.
    
    Uses the Effect.to_dict() method for serialization. Compatible with
    the existing WebSocket protocol and browser-based replay tools.
    """
    
    def serialize(self, effect: Effect) -> str:
        import json
        return json.dumps(effect.to_dict())
    
    def deserialize(self, data: str) -> Effect:
        import json
        d = json.loads(data)
        return self._dict_to_effect(d)
    
    def serialize_batch(self, effects: list[Effect]) -> str:
        import json
        return json.dumps([e.to_dict() for e in effects])
    
    def deserialize_batch(self, data: str) -> list[Effect]:
        import json
        raw = json.loads(data)
        return [self._dict_to_effect(d) for d in raw]
    
    @staticmethod
    def _dict_to_effect(d: dict[str, Any]) -> Effect:
        """Convert a dict back to the appropriate Effect subclass."""
        from ..effects import (
            StateVarDelta, SetStateVar, StateTransition, VoxelDelta,
            SpawnEntity, RemoveEntity, LingerEffect, EventRecord,
            ClearTarget, SetTarget, VoxelBatchDelta,
        )
        
        type_map = {
            "state_var_delta": StateVarDelta,
            "set_state_var": SetStateVar,
            "state_transition": StateTransition,
            "voxel_delta": VoxelDelta,
            "spawn_entity": SpawnEntity,
            "remove_entity": RemoveEntity,
            "linger_effect": LingerEffect,
            "event_record": EventRecord,
            "clear_target": ClearTarget,
            "set_target": SetTarget,
            "voxel_batch_delta": VoxelBatchDelta,
        }
        
        cls = type_map.get(d["effect_type"])
        if cls is None:
            raise ValueError(f"Unknown effect type: {d['effect_type']}")
        
        return cls(**{k: v for k, v in d.items() if k != "effect_type"})


class SerializerRegistry:
    """Registry for pluggable serializers.
    
    Usage:
        registry = SerializerRegistry()
        registry.register("json", JsonSerializer())       # Default
        registry.register("msgpack", MsgPackSerializer())  # Future
        registry.register("protobuf", ProtobufSerializer())  # Future
        
        # Use default
        serializer = registry.get_default()
        
        # Or specify by name
        serializer = registry.get("msgpack")
    """
    
    def __init__(self):
        self._serializers: dict[str, Serializer] = {}
        self._default: str | None = None
    
    def register(self, name: str, serializer: Serializer) -> None:
        self._serializers[name] = serializer
        if self._default is None:
            self._default = name
    
    def get_default(self) -> Serializer:
        if self._default is None:
            raise RuntimeError("No serializers registered")
        return self._serializers[self._default]
    
    def get(self, name: str) -> Serializer:
        return self._serializers[name]


# ── Future serializer stubs (for when msgpack/protobuf are added) ───────────

class MsgPackSerializer(Serializer):
    """Binary msgpack serializer — efficient for high-throughput simulation.
    
    TODO: Implement when performance profiling shows JSON is a bottleneck.
    Expected 3-5x size reduction and 2-3x serialization speed improvement.
    """
    pass


class ProtobufSerializer(Serializer):
    """Protocol Buffers serializer — schema enforcement, cross-language compatibility.
    
    TODO: Define .proto schemas for all Effect types when distributed simulation
    requires cross-language interoperability (e.g., Rust/C++ simulation nodes).
    """
    pass
```

### Read-Only Context Views

In Phase 3, `InteractionContext` becomes truly read-only by using frozen dataclass views or copy-on-read patterns:

```python
# actors/__init__.py — Frozen context wrapper

from dataclasses import replace


def make_readonly_context(ctx: InteractionContext) -> InteractionContext:
    """Create a read-only view of the interaction context.
    
    In Phase 3, actors receive this frozen view to guarantee they cannot
    accidentally mutate simulation state during resolve(). The engine's
    effect bus is the only code path that mutates state.
    
    Returns a new InteractionContext with entity data copied (shallow copy
    of nested dicts) so actors can read but not write.
    """
    # Shallow copy of entity dict — actors read fields but don't mutate
    readonly_entity = {k: v for k, v in ctx.entity.items()}
    
    return replace(ctx, entity=readonly_entity)
```

### Spatial Index Abstraction

The current spatial index is a brute-force O(n) per query. Phase 3 introduces an abstraction layer:

```python
# spatial/index.py

from abc import ABC, abstractmethod
from typing import Protocol


class EntityPosition(Protocol):
    id: str
    position: list[float]  # [x, y, z]


class SpatialIndex(ABC):
    """Abstract spatial index for entity neighbor queries.
    
    Implementations:
    - BruteForceIndex: O(n) per query (current implementation, simple)
    - HashGridIndex: O(1) average per query (recommended for >100 entities)
    - QuadtreeIndex: O(log n) per query (good for uneven distributions)
    
    The engine uses this to build the `nearby_entities` list in each actor's context.
    """
    
    @abstractmethod
    def rebuild(self, entities: dict[str, EntityPosition]) -> None:
        """Rebuild index from current entity positions."""
        ...
    
    @abstractmethod
    def query_in_range(
        self, pos: list[float], radius: float, exclude_id: str | None = None,
    ) -> list[EntityPosition]:
        """Find all entities within radius of pos."""
        ...


class BruteForceIndex(SpatialIndex):
    """Current implementation — O(n) per query. Simple and correct."""
    
    def __init__(self):
        self._positions: dict[str, list[float]] = {}
    
    def rebuild(self, entities: dict[str, EntityPosition]) -> None:
        self._positions = {eid: e.position for eid, e in entities.items()}
    
    def query_in_range(
        self, pos: list[float], radius: float, exclude_id: str | None = None,
    ) -> list[EntityPosition]:
        results = []
        r2 = radius * radius
        for eid, epos in self._positions.items():
            if eid == exclude_id:
                continue
            dx = pos[0] - epos[0]
            dz = pos[2] - epos[2]
            if dx * dx + dz * dz <= r2:
                results.append({"id": eid, "position": list(epos)})
        return results


class HashGridIndex(SpatialIndex):
    """Hash-grid spatial index — O(1) average per query.
    
    Divides space into cells of size `radius`. Each entity is placed in one cell.
    Query checks only the cell containing pos and adjacent cells.
    
    Recommended for >100 entities. ~5-10x faster than brute force at scale.
    """
    
    def __init__(self, cell_size: float = 2.0):
        self._cell_size = cell_size
        self._cells: dict[tuple[int, int], list[EntityPosition]] = {}
    
    def rebuild(self, entities: dict[str, EntityPosition]) -> None:
        self._cells.clear()
        for eid, entity in entities.items():
            x, z = entity.position[0], entity.position[2]
            cx, cz = int(x / self._cell_size), int(z / self._cell_size)
            key = (cx, cz)
            if key not in self._cells:
                self._cells[key] = []
            self._cells[key].append({"id": eid, "position": list(entity.position)})
    
    def query_in_range(
        self, pos: list[float], radius: float, exclude_id: str | None = None,
    ) -> list[EntityPosition]:
        results = []
        r2 = radius * radius
        cx, cz = int(pos[0] / self._cell_size), int(pos[2] / self._cell_size)
        
        # Check cell and adjacent cells
        check_cells = [
            (cx + dx, cz + dz)
            for dx in range(-1, 2)
            for dz in range(-1, 2)
        ]
        
        for ccx, ccz in check_cells:
            key = (ccx, ccz)
            if key not in self._cells:
                continue
            for entity in self._cells[key]:
                if entity["id"] == exclude_id:
                    continue
                dx = pos[0] - entity["position"][0]
                dz = pos[2] - entity["position"][2]
                if dx * dx + dz * dz <= r2:
                    results.append(entity)
        
        return results
```

---

## Serialization Layer — Pluggable Format Design

### Why Pluggable?

The current WebSocket protocol uses JSON for all data transfer. This is fine for development and small-scale simulation, but as the system scales to distributed nodes with thousands of entities, we need:

1. **JSON** (default): Human-readable, browser-compatible, easy debugging
2. **msgpack**: Binary format — 3-5x smaller payload, 2-3x faster serialization
3. **protobuf**: Schema-enforced, cross-language compatible (Rust/C++ simulation nodes)

### Design

```python
# The engine holds a reference to the serializer registry:
class EcosystemEngine:
    def __init__(self, world_config, adapters=None):
        ...
        self.serializer_registry = SerializerRegistry()
        self.serializer_registry.register("json", JsonSerializer())  # Default
        
        # Optional: allow caller to register custom serializers
        if "serializers" in world_config.get("simulation", {}):
            for name, config in world_config["simulation"]["serializers"].items():
                serializer = self._load_serializer(name, config)
                self.serializer_registry.register(name, serializer)
    
    def get_serializer(self, format_name: str | None = None) -> Serializer:
        """Get the serializer to use for this tick's effect log."""
        if format_name is None:
            return self.serializer_registry.get_default()
        return self.serializer_registry.get(format_name)
```

### Effect Log for Deterministic Replay

The engine maintains an effect log that can be serialized and replayed:

```python
class EffectLog:
    """Append-only log of all effects produced during simulation.
    
    Used for:
    1. Deterministic replay (send to another node, replay tick-by-tick)
    2. Debugging (inspect what happened at each tick)
    3. Client broadcast (stream effects instead of full state snapshots)
    """
    
    def __init__(self, serializer: Serializer | None = None):
        self._log: list[list[Effect]] = []  # Per-tick effect batches
        self._serializer = serializer or JsonSerializer()
    
    def append(self, tick: int, effects: list[Effect]) -> None:
        self._log.append((tick, effects))
    
    def get_tick_effects(self, tick: int) -> list[Effect] | None:
        for t, effects in self._log:
            if t == tick:
                return effects
        return None
    
    def serialize_log(self, start_tick: int = 0, end_tick: int | None = None) -> bytes | str:
        """Serialize a range of ticks for transmission or storage."""
        batched = []
        for t, effects in self._log:
            if t < start_tick:
                continue
            if end_tick is not None and t > end_tick:
                break
            batched.extend(effects)
        return self._serializer.serialize_batch(batched)
    
    def replay(self, engine: EcosystemEngine, effects_data: bytes | str) -> None:
        """Replay a serialized effect log on an engine instance."""
        effects = self._serializer.deserialize_batch(effects_data)
        # Group by tick and apply in order
        ...
```

---

## Effect Application Order & Conflict Resolution

### The Problem

When two actors produce effects targeting the same entity in one tick, we need deterministic ordering. For example:
- Predator A produces `RemoveEntity(prey)` 
- Another predator B also detects the same prey and produces its own predation effects

### Resolution Strategy: Tick-Based Atomicity with Priority Ordering

```python
# 1. All actors run concurrently (or sequentially — same result) because they only READ state.
# 2. Effects are collected into a single list per phase.
# 3. Effects within each batch are sorted by EFFECT_PRIORITY.
# 4. During application, removed entities block further effects on them.

def apply_batch(self, effects: list[Effect], ...) -> None:
    # Sort by priority — terminal operations first
    sorted_effects = sorted(effects, key=lambda e: EFFECT_PRIORITY.get(e.effect_type, 9))
    
    removed_ids: set[str] = set()
    
    for effect in sorted_effects:
        if isinstance(effect, RemoveEntity):
            removals.append(effect.entity_id)
            removed_ids.add(effect.entity_id)
        
        elif isinstance(effect, StateTransition):
            entity = entities.get(effect.entity_id)
            if entity and effect.entity_id not in removed_ids:
                # Apply state transition...
        
        elif isinstance(effect, (StateVarDelta, SetStateVar)):
            entity = entities.get(effect.entity_id)
            if entity and effect.entity_id not in removed_ids:
                # Apply state variable change...
```

### Conflict Resolution Rules

| Scenario | Resolution |
|----------|-----------|
| Two predators target same prey | First predator's `RemoveEntity` takes priority (sorted first). Second predator's effects on the prey are silently dropped. The second predator gets no effect — it should re-evaluate next tick. |
| Herbivory + predation on same plant | Both can apply: herbivory reduces growth/health, predation removes entity. If predation `RemoveEntity` fires first, herbivory effects are dropped. |
| Flow actor drains hydration while guard actor sets state to DRINKING | Flow effects (STATE_VAR_DELTA) have lower priority than guard effects (STATE_TRANSITION). Guard transitions apply first, then flow adjusts values based on new state. |
| Two actors produce conflicting SET_STATE_VAR on same var | Last one in sorted order wins (deterministic by effect type ordering). In practice, this shouldn't happen — each actor should only set vars it controls. |

### Cross-Phase Ordering

Effects from different phases are applied sequentially:
1. Flow effects → state variables evolve
2. Interaction effects → entities interact based on evolved state
3. Guard effects → state transitions based on post-interaction state

This preserves the existing 7-phase semantics while enabling parallel actor execution within each phase.

---

## File Structure (Target)

```
ecosim/
├── __init__.py              ← Package exports: EcosystemEngine, Effect types, Actor base classes
├── engine.py                ← Thin orchestrator (~300 lines after Phase 2)
│   ├── step()               ← Orchestrates phases, collects effects, applies them
│   ├── _build_actor_contexts() ← Builds read-only context for each entity
│   ├── _rebuild_spatial_index() ← Rebuilds spatial index for neighbor queries
│   └── _build_tick_packet() ← Assembles WebSocket packet from state + events
│
├── effects.py               ← All Effect dataclasses + EffectBus + priority ordering
│   ├── Effect (base)
│   ├── StateVarDelta, SetStateVar
│   ├── StateTransition, RemoveEntity, SpawnEntity
│   ├── VoxelDelta, VoxelBatchDelta
│   ├── LingerEffect, ClearTarget, SetTarget
│   └── EventRecord
│
├── actors/                  ← All actor implementations
│   ├── __init__.py          ← ActorRegistry, InteractionActor base class
│   │                           + FlowActor, GuardActor subclasses
│   ├── interaction_actors.py  ← FleeActor, PredationActor, HerbivoryActor, PollinationActor
│   ├── flow_actors.py         ← ConsumerFlowActor, ProducerFlowActor, DecomposerFlowActor
│   └── guard_actors.py        ← ConsumerGuardActor, ProducerGuardActor, DecomposerGuardActor
│
├── serialization/           ← Pluggable effect serializers (Phase 3)
│   ├── __init__.py          ← SerializerRegistry export
│   ├── interface.py         ← Serializer ABC + SerializerRegistry
│   └── json_serializer.py   ← JsonSerializer implementation
│
├── spatial/                 ← Spatial index abstraction (Phase 3)
│   ├── __init__.py          ← SpatialIndex, BruteForceIndex, HashGridIndex exports
│   └── index.py             ← Index implementations
│
├── interactions.py          ← Unchanged: InteractionTemplate classes + InteractionParams
├── traits.py                ← Unchanged: TraitVector, DerivedParams, derive_all()
├── trait_compiler.py        ← Unchanged: CompiledEcology, TraitCompiler, compile_world()
├── entities.py              ← Unchanged: init_entity(), is_alive(), state definitions
├── voxel_manager.py         ← Unchanged: VoxelManager (sparse 3D grid)
├── biome.py                 ← Unchanged: BiomeConfig, BIOME_PRESETS
└── model_adapter.py         ← Unchanged: MotorAdapter protocol + ContextSpec
```

### Line Count Estimates

| File | Current | After Phase 1 | After Phase 2 | Notes |
|------|---------|---------------|---------------|-------|
| `engine.py` | ~1907 | ~400 | ~300 | Thin orchestrator only |
| `effects.py` | 0 | ~250 | ~250 | All effect dataclasses + bus |
| `actors/interaction_actors.py` | 0 | ~350 | ~350 | Flee, Predation, Herbivory, Pollination |
| `actors/flow_actors.py` | 0 | 0 | ~400 | Consumer, Producer, Decomposer flow |
| `actors/guard_actors.py` | 0 | 0 | ~250 | Consumer, Producer, Decomposer guards |
| `serialization/interface.py` | 0 | 0 | 0 | ~100 (Phase 3) |
| `serialization/json_serializer.py` | 0 | 0 | 0 | ~80 (Phase 3) |
| `spatial/index.py` | 0 | 0 | 0 | ~200 (Phase 3) |
| **Total new code** | — | ~1250 | ~1650 | Replaces ~1907 lines of monolithic engine |

---

## Migration Checklist

### Phase 1: Effects Extraction + Interaction Actors

- [ ] Create `effects.py` with all Effect dataclasses and `EffectBus.apply_effects()`
- [ ] Create `actors/__init__.py` with base classes (`InteractionActor`, `InteractionContext`)
- [ ] Implement `FleeActor` — extract from `_resolve_flee()`
- [ ] Implement `PredationActor` — extract from `_predation_event()` + `_resolve_predation()`
- [ ] Implement `HerbivoryActor` — extract from `_consumption_event()` + `_resolve_herbivory()`
- [ ] Implement `PollinationActor` — extract from `_pollination_event()` + `_resolve_pollination()`
- [ ] Refactor engine's Phase 2 to use actor registry instead of inline methods
- [ ] Update effect application: collect effects → sort by priority → apply atomically
- [ ] Verify all existing tests pass (behavior must be identical)
- [ ] Add unit tests for each actor (pure function testing: context in, effects out)

### Phase 2: Flow + Guard Actors

- [ ] Implement `ConsumerFlowActor` — extract from `_flow_consumer()` (~120 lines)
- [ ] Implement `ProducerFlowActor` — extract from `_flow_producer()` (~80 lines)
- [ ] Implement `DecomposerFlowActor` — extract from `_flow_decomposer()` (~20 lines)
- [ ] Implement `ConsumerGuardActor` — extract from `_guards_consumer()` (~100 lines)
- [ ] Implement `ProducerGuardActor` — extract from `_guards_producer()` (~50 lines)
- [ ] Implement `DecomposerGuardActor` — extract from `_guards_decomposer()` (~20 lines)
- [ ] Refactor engine's Phase 1 and Phase 3 to use actor registry
- [ ] Move rate multipliers into context (or keep as engine config passed through context)
- [ ] Verify all existing tests pass
- [ ] Add unit tests for each flow/guard actor

### Phase 3: Distributed Readiness

- [ ] Create `serialization/interface.py` with `Serializer` ABC and `SerializerRegistry`
- [ ] Implement `JsonSerializer` (default, WebSocket-compatible)
- [ ] Create stubs for `MsgPackSerializer` and `ProtobufSerializer` (documented TODOs)
- [ ] Add effect log (`EffectLog`) for deterministic replay
- [ ] Make `InteractionContext` truly read-only (frozen views or copy-on-read)
- [ ] Create `spatial/index.py` with `SpatialIndex` ABC
- [ ] Implement `BruteForceIndex` (current behavior, wrapped) and `HashGridIndex` (new)
- [ ] Add spatial index to engine constructor (configurable via world config)
- [ ] Document distributed simulation architecture in a separate doc

### Testing Strategy

1. **Unit tests for actors**: Each actor is a pure function — test with mock contexts, assert effects output. No simulation harness needed.
2. **Integration tests**: Run full `step()` cycle and verify tick packets match expected values (same as current tests).
3. **Determinism tests**: Serialize effect log → replay on fresh engine → verify identical final state.
4. **Performance benchmarks**: Compare step time before/after refactoring, especially with HashGridIndex at scale (>100 entities).

---

## Open Questions

### 1. Movement Target Selection — Actor or Engine?

Currently `_pick_movement_target()` is inline in the engine (~80 lines of complex logic for pollinator flower-seeking, herbivore food-seeking, predator prey-seeking, etc.). Options:

- **Option A**: Create a `MovementActor` that returns `SetTarget` effects. This keeps movement behavior actor-based but adds complexity (the actor needs access to diet order, floral affinity, visited flowers tracking).
- **Option B**: Keep movement target selection in the engine as a helper called by actors. Actors emit `SetTarget` effects; the engine computes the actual target position.
- **Recommendation**: Option A — create a `MovementActor` that encapsulates all target selection logic. It's behavior code that belongs with other actor behaviors, not in the orchestrator.

### 2. Reproduction and Plant Spreading — Actor or Engine?

Both `_reproduction_event()` and `_try_plant_spread()` are inline in the engine. They produce `SpawnEntity` effects (new child entities). Options:

- **Option A**: Create a `ReproductionActor` that handles both animal reproduction and plant spreading. It would be called during the guard phase (when reproductive drive is high) or flow phase (for vegetative spreading).
- **Option B**: Keep in engine as deferred spawn logic, triggered by guard effects (`StateTransition → REPRODUCING`).
- **Recommendation**: Option A — create a `ReproductionActor` that produces `SpawnEntity` + `EventRecord` effects. This keeps all entity lifecycle management within the actor model.

### 3. Water Source Management — Actor or Engine?

Water evaporation, replenishment, and soil moisture footprints are world-level processes (not per-entity). They don't fit neatly into the actor model since they're not triggered by entities. Options:

- **Option A**: Create a `WorldProcessActor` that handles water/soil evaporation as a global effect batch (`VoxelBatchDelta`).
- **Option B**: Keep in engine as-is — these are ambient world processes, not entity behaviors.
- **Recommendation**: Option B — keep water management inline. It's not an interaction between entities; it's background physics that affects all entities equally.

### 4. Motor Inference (BYOM) — Unchanged?

The motor adapter inference (`_apply_motor_inference()`) is already decoupled from the engine logic via the `MotorAdapter` protocol. No changes needed in this refactoring. It remains a post-processing step after all effects are applied.

### 5. Effect Log Size and Performance

At 10 Hz with ~100 entities, each producing ~3-5 effects per tick, we get ~300-500 effects/tick. Over a 1-hour simulation (3600 ticks), that's ~1-2 million effects. Considerations:

- **Memory**: Store only in memory during active simulation; flush to disk periodically.
- **Serialization**: Use msgpack for effect logs once performance profiling shows JSON is a bottleneck.
- **Compression**: Apply gzip/zstd compression when writing to disk or transmitting over network.

### 6. Distributed Sharding Strategy (Future)

When the system scales to distributed nodes, each node owns a shard of entities:

```
Node A: entities [0-99], spatial region [x:0-16, z:0-16]
Node B: entities [100-199], spatial region [x:17-32, z:0-16]
Node C: entities [200-299], spatial region [x:0-16, z:17-32]
Node D: entities [300-399], spatial region [x:17-32, z:17-32]
```

Cross-shard interactions (e.g., predator in Node A eats prey in Node B) require:
- **Vector clocks** for causal ordering of effects across nodes
- **Effect reconciliation** when two nodes independently process the same interaction
- **Spatial index replication** at shard boundaries for neighbor queries

This is documented as a future consideration — not part of Phase 1-3 implementation.

---

## Appendix A: Effect Type Reference

| Effect | Purpose | Mutates | Priority |
|--------|---------|---------|----------|
| `StateVarDelta` | Increment/decrement entity state var | Entity state_vars | 6 |
| `SetStateVar` | Set entity state var to absolute value | Entity state_vars | 2 |
| `StateTransition` | Change entity discrete state | Entity state | 1 |
| `RemoveEntity` | Remove entity from simulation | Entity registry | 0 |
| `SpawnEntity` | Create new entity | Spawn list → Entity registry | 8 |
| `VoxelDelta` | Change voxel layer value | Voxel grid | 7 |
| `VoxelBatchDelta` | Multiple voxel changes at once | Voxel grid | 7 |
| `LingerEffect` | Entity stays at location for N ticks | Entity _linger | 3 |
| `ClearTarget` | Reset entity movement target | Entity _target | 4 |
| `SetTarget` | Set new movement target | Entity _target | 5 |
| `EventRecord` | Log simulation event for client | Event log | 9 |

## Appendix B: Actor Registry Pattern

```python
# actors/__init__.py

from typing import Protocol


class ActorRegistry:
    """Maps entity IDs to their responsible actor instances.
    
    The engine populates this at init time based on each entity's
    functional role (consumer/producer/decomposer) and interaction
    capabilities (has floral_affinity, is carnivore, etc.).
    """
    
    def __init__(self):
        self._interaction_actors: dict[str, InteractionActor] = {}
        self._flow_actors: dict[str, FlowActor] = {}
        self._guard_actors: dict[str, GuardActor] = {}
    
    def register_interaction(self, entity_id: str, actor: InteractionActor) -> None:
        self._interaction_actors[entity_id] = actor
    
    def register_flow(self, entity_id: str, actor: FlowActor) -> None:
        self._flow_actors[entity_id] = actor
    
    def register_guard(self, entity_id: str, actor: GuardActor) -> None:
        self._guard_actors[entity_id] = actor
    
    def get_interaction_actor(self, entity_id: str) -> InteractionActor | None:
        return self._interaction_actors.get(entity_id)
    
    def get_flow_actor(self, entity_id: str) -> FlowActor | None:
        return self._flow_actors.get(entity_id)
    
    def get_guard_actor(self, entity_id: str) -> GuardActor | None:
        return self._guard_actors.get(entity_id)


def build_registry(compiled: CompiledEcology, biome: BiomeConfig) -> ActorRegistry:
    """Build the actor registry from compiled ecology.
    
    Called once at engine init. Each entity gets registered with the
    appropriate actors based on its species' traits.
    """
    registry = ActorRegistry()
    
    for sid, params in compiled.derived_params.items():
        # Flow actor — always present (every entity has a flow role)
        if params.diet_type == "autotroph":
            registry.register_flow(sid, ProducerFlowActor())
        elif params.diet_type == "decomposer":
            registry.register_flow(sid, DecomposerFlowActor())
        else:
            registry.register_flow(sid, ConsumerFlowActor())
        
        # Guard actor — always present (every entity has a state machine)
        if params.diet_type == "autotroph":
            registry.register_guard(sid, ProducerGuardActor())
        elif params.diet_type == "decomposer":
            registry.register_guard(sid, DecomposerGuardActor())
        else:
            registry.register_guard(sid, ConsumerGuardActor())
        
        # Interaction actors — only for entities with interactions
        if params.diet_type not in ("autotroph", "decomposer"):
            registry.register_interaction(sid, FleeActor())
            if params.diet_type in ("carnivore", "insectivore", "omnivore"):
                registry.register_interaction(sid, PredationActor())
            # Herbivory is implicit for herbivores/omnivores (checked by actor)
            registry.register_interaction(sid, HerbivoryActor())
        
        if params.floral_affinity:
            registry.register_interaction(sid, PollinationActor())
    
    return registry
```

## Appendix C: Comparison — Before vs After Architecture

### Before (Current)

```
EcosystemEngine.step()
├── for each entity: _apply_flow(entity)          ← inline mutation
│   └── if consumer: _flow_consumer(entity, p)     ← 120 lines of mutations
│   └── if producer: _flow_producer(entity, p)     ← 80 lines of mutations
│   └── if decomposer: _flow_decomposer(entity, p) ← 20 lines of mutations
├── for each entity: _resolve_interactions(entity) ← inline mutation
│   └── _resolve_flee(entity, p, pos)              ← directly sets e["state"] = "FLEEING"
│   └── _resolve_predation(entity, p, pos)         ← directly mutates predator + prey dicts
│   └── _resolve_herbivory(entity, p, pos)         ← directly mutates herbivore + plant dicts
│   └── _resolve_pollination(entity, p, pos)       ← directly mutates pollinator + plant dicts
├── for each entity: _evaluate_guards(entity)      ← inline mutation
│   └── if consumer: _guards_consumer(entity, p)   ← 100 lines of state transitions
│   └── if producer: _guards_producer(entity, p)   ← 50 lines of state transitions
│   └── if decomposer: _guards_decomposer(entity, p) ← 20 lines of state transitions
├── for each entity: _apply_voxel_effects(entity)  ← inline mutation
├── water evaporation + replenishment              ← inline world-level mutations
├── motor inference                                ← unchanged (already decoupled)
└── spawn/kill from deferred lists                 ← apply deferred changes
```

**Problems**: Every phase mutates state directly. No separation between "what should happen" and "apply what happened." Adding a new interaction requires adding methods to the engine class. State mutations are interleaved with detection logic, making it impossible to serialize or replay interactions independently of state.

### After (Target)

```
EcosystemEngine.step()
├── rebuild_spatial_index()                        ← O(n) index build
├── build_actor_contexts(dt)                       ← read-only snapshots for all entities
│
├── Phase 1: Flow effects                          ← ALL READ-ONLY, NO MUTATIONS
│   └── for each entity: flow_actor.resolve(ctx) → list[Effect]
│       └── ConsumerFlowActor / ProducerFlowActor / DecomposerFlowActor
│
├── Phase 2: Interaction effects                   ← ALL READ-ONLY, NO MUTATIONS
│   └── for each entity: interaction_actor.resolve(ctx) → list[Effect]
│       └── FleeActor / PredationActor / HerbivoryActor / PollinationActor
│
├── Phase 3: Guard effects                         ← ALL READ-ONLY, NO MUTATIONS
│   └── for each entity: guard_actor.resolve(ctx) → list[Effect]
│       └── ConsumerGuardActor / ProducerGuardActor / DecomposerGuardActor
│
├── effect_bus.apply_batch(flow_effects, ...)      ← atomic single-pass application
├── effect_bus.apply_batch(interaction_effects, ...)
├── effect_bus.apply_batch(guard_effects, ...)
│
├── Phase 4: Voxel effects (inline)                ← unchanged for now
├── Phase 5-7: Water/motor/spawn-kill              ← unchanged for now
│
└── build_tick_packet(dt)                          ← assemble WebSocket response
```

**Benefits**: 
1. Actors are pure functions — deterministic, testable, serializable.
2. Effects are immutable data structures — can be logged, replayed, transmitted.
3. Engine is thin orchestrator (~300 lines) — easy to understand and modify.
4. Adding a new interaction = adding one actor class + its effect types. No engine changes.
5. Truly parallel: actors run concurrently (or sequentially with same result), effects applied atomically after all actors complete.

---

*End of document.*
