# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Effects System — Immutable Delta Descriptions

Effects are immutable dataclasses that describe *what changed* rather than
performing mutations directly. They serve as the universal currency between
actors and the effect applier (engine).

Design Principles:
- Frozen dataclasses → deterministic replay, network transport, conflict detection
- Priority ordering → terminal operations (RemoveEntity) apply before deltas
- Batch application → all effects collected per phase, applied atomically after actors complete

See Also:
- ``actors/__init__.py`` — InteractionActor base class + InteractionContext
- ``actors/interaction_actors.py`` — FleeActor, PredationActor, HerbivoryActor, PollinationActor
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# Effect Types
# ═══════════════════════════════════════════════════════════════════════════════

class EffectType(str, Enum):
    """All effect types the simulation can produce."""
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
    DEPOSIT_OM = "deposit_organic_matter"     # Deposit OM on entity death

    # Entity behavior modifiers
    LINGER_EFFECT = "linger_effect"           # Stay at location for N ticks
    CLEAR_TARGET = "clear_target"             # Reset movement target
    SET_TARGET = "set_target"                 # Set new movement target
    SET_ENTITY_ATTR = "set_entity_attr"       # Set entity-level attribute (not state_var)

    # Events (for client broadcast)
    EVENT_RECORD = "event_record"             # Log simulation event


# ═══════════════════════════════════════════════════════════════════════════════
# Effect Priority Ordering
# ═══════════════════════════════════════════════════════════════════════════════

EFFECT_PRIORITY: dict[EffectType, int] = {
    EffectType.REMOVE_ENTITY: 0,
    EffectType.STATE_TRANSITION: 1,
    EffectType.SET_STATE_VAR: 2,
    EffectType.SET_ENTITY_ATTR: 2,         # Same priority as SetStateVar
    EffectType.LINGER_EFFECT: 3,
    EffectType.CLEAR_TARGET: 4,
    EffectType.SET_TARGET: 5,
    EffectType.STATE_VAR_DELTA: 6,
    EffectType.VOXEL_BATCH_DELTA: 7,
    EffectType.VOXEL_DELTA: 7,
    EffectType.DEPOSIT_OM: 7,              # Same priority as voxel changes
    EffectType.SPAWN_ENTITY: 8,
    EffectType.EVENT_RECORD: 9,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Base Effect Class
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, kw_only=True)
class Effect:
    """Base class for all simulation effects."""
    effect_type: EffectType
    tick: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (used by JSON serializer)."""
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# State Variable Effects
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# Entity Lifecycle Effects
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, kw_only=True)
class StateTransition(Effect):
    """Request a discrete state change."""
    effect_type: EffectType = EffectType.STATE_TRANSITION
    entity_id: str
    new_state: str       # "FORAGING", "FLEEING", "DYING", ...


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
    initial_attrs: dict[str, float] | None = field(default=None)  # entity-level attrs


@dataclass(frozen=True, kw_only=True)
class RemoveEntity(Effect):
    """Request an entity be removed."""
    effect_type: EffectType = EffectType.REMOVE_ENTITY
    entity_id: str


# ═══════════════════════════════════════════════════════════════════════════════
# Organic Matter Deposit (on death)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, kw_only=True)
class DepositOrganicMatter(Effect):
    """Deposit organic matter at entity's position on death.

    Carries all data needed for deposit calculation — the engine
    processes this during removal handling.
    """
    effect_type: EffectType = EffectType.DEPOSIT_OM
    entity_id: str
    type: str            # "ANIMAL", "PLANT", etc.
    species: str | None
    position: list[float]  # [x, y, z]
    metadata: dict[str, Any]
    params: dict[str, float] | None = None  # DerivedParams as dict (for OM calculation)


# ═══════════════════════════════════════════════════════════════════════════════
# Environmental Effects
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# Behavior Modifier Effects
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# Event Recording
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, kw_only=True)
class SetEntityAttr(Effect):
    """Set an entity-level attribute (direct on entity dict, not in state_vars).

    Used for internal tracking variables like _pollination_cooldown,
    _pollination_visits, _wander_cooldown that live on the entity dict
    rather than in state_vars.
    """
    effect_type: EffectType = EffectType.SET_ENTITY_ATTR
    entity_id: str
    attr_name: str        # e.g. "_pollination_cooldown", "_pollination_visits"
    value: float


@dataclass(frozen=True, kw_only=True)
class EventRecord(Effect):
    """Log a simulation event for client broadcast."""
    effect_type: EffectType = EffectType.EVENT_RECORD
    event_type: str       # "PREDATION", "CONSUMPTION", "STATE_CHANGE", ...
    source_id: str | None
    target_id: str | None
    position: list[float]  # [x, y, z]
    extra: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Effect Bus — Collect and Apply Atomically
# ═══════════════════════════════════════════════════════════════════════════════

class EffectBus:
    """Collects effects from all actors and applies them atomically.

    This is the key mechanism that enables truly parallel actor execution:
    1. All actors run concurrently (or sequentially, same result) because they only read state.
    2. Effects are collected into a single list per phase.
    3. Conflicts are resolved (e.g., entity removed mid-tick).
    4. Effects are applied in priority order to the shared state.

    Usage:
        bus = EffectBus()
        effects = actor.resolve(ctx) + other_actor.resolve(other_ctx)
        bus.apply_effects(effects, entities, voxels, spawns, removals, events)
    """

    def apply_effects(
        self,
        effects: list[Effect],
        entities: dict[str, dict],
        voxels: Any,  # VoxelManager — avoid circular import
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
        sorted_effects = sorted(
            effects, key=lambda e: EFFECT_PRIORITY.get(e.effect_type, 9)
        )

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
                            "type": "STATE_CHANGE",
                            "tick": effect.tick,
                            "source_id": effect.entity_id,
                            "target_id": None,
                            "position": entity["position"],
                            "prev_state": old_state,
                            "new_state": effect.new_state,
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

            elif isinstance(effect, SetEntityAttr):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    entity[effect.attr_name] = effect.value

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
                    "initial_attrs": effect.initial_attrs or {},
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

    def apply_batch(
        self,
        effects: list[Effect],
        entities: dict[str, dict],
        voxels: Any,
        spawns: list,
        removals: list,
        events: list,
    ) -> None:
        """Alias for ``apply_effects`` — same behavior."""
        self.apply_effects(effects, entities, voxels, spawns, removals, events)

    def apply_flow_batch(
        self,
        effects: list[Effect],
        entities: dict[str, dict],
        voxels: Any,
    ) -> None:
        """Apply flow-only effects (state vars + voxel changes).

        Flow actors only produce StateVarDelta, SetStateVar, and VoxelDelta.
        No entity lifecycle changes — those are handled by guard actors.

        Args:
            effects: Flow effects from all flow actors this tick.
            entities: Entity registry (mutated in place for state vars).
            voxels: Voxel grid manager (mutated in place for voxel deltas).
        """
        # Sort by priority — SetStateVar before StateVarDelta
        sorted_effects = sorted(
            effects, key=lambda e: EFFECT_PRIORITY.get(e.effect_type, 9)
        )

        for effect in sorted_effects:
            if isinstance(effect, SetStateVar):
                entity = entities.get(effect.entity_id)
                if entity is not None:
                    entity["state_vars"][effect.var_name] = effect.value

            elif isinstance(effect, StateVarDelta):
                entity = entities.get(effect.entity_id)
                if entity is not None:
                    sv = entity["state_vars"]
                    current = sv.get(effect.var_name, 0.0)
                    new_val = max(0.0, min(1.0, current + effect.delta))
                    sv[effect.var_name] = new_val

            elif isinstance(effect, VoxelDelta):
                voxels.add(effect.layer, effect.x, effect.y, effect.z, effect.delta)

    def apply_effects_with_om_deposit(
        self,
        effects: list[Effect],
        entities: dict[str, dict],
        voxels: Any,
        spawns: list,
        removals: list,
        events: list,
        deposit_fn: Any | None = None,
    ) -> None:
        """Apply effects with organic matter deposition on death.

        When a RemoveEntity effect is processed, if deposit_fn is provided,
        it will be called to handle the OM deposit. This bridges the gap
        between actor-based guards and engine-level OM handling.

        Args:
            effects: All effects collected from all actors this tick.
            entities: Entity registry (mutated in place).
            voxels: Voxel grid manager (mutated in place).
            spawns: Deferred spawn list.
            removals: Deferred removal list.
            events: Event log for client broadcast.
            deposit_fn: Callable(entity, params) -> None for OM deposition.
        """
        sorted_effects = sorted(
            effects, key=lambda e: EFFECT_PRIORITY.get(e.effect_type, 9)
        )

        removed_ids: set[str] = set()

        for effect in sorted_effects:
            if isinstance(effect, RemoveEntity):
                removals.append(effect.entity_id)
                removed_ids.add(effect.entity_id)
                # Deposit organic matter if handler provided
                if deposit_fn is not None:
                    entity = entities.get(effect.entity_id)
                    if entity is not None:
                        deposit_fn(entity, None)

            elif isinstance(effect, StateTransition):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    old_state = entity["state"]
                    entity["state"] = effect.new_state
                    if old_state != effect.new_state:
                        events.append({
                            "type": "STATE_CHANGE",
                            "tick": effect.tick,
                            "source_id": effect.entity_id,
                            "target_id": None,
                            "position": entity["position"],
                            "prev_state": old_state,
                            "new_state": effect.new_state,
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

            elif isinstance(effect, SetEntityAttr):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    entity[effect.attr_name] = effect.value

            elif isinstance(effect, SetTarget):
                entity = entities.get(effect.entity_id)
                if entity and effect.entity_id not in removed_ids:
                    entity["_target"] = effect.position

            elif isinstance(effect, VoxelDelta):
                voxels.add(effect.layer, effect.x, effect.y, effect.z, effect.delta)

            elif isinstance(effect, DepositOrganicMatter):
                if deposit_fn is not None:
                    # Create a temporary entity dict for the deposit handler
                    temp_entity = {
                        "position": effect.position,
                        "type": effect.type,
                        "species": effect.species,
                        "metadata": effect.metadata,
                    }
                    deposit_fn(temp_entity, effect.params)

            elif isinstance(effect, SpawnEntity):
                spawns.append({
                    "id": effect.entity_id,
                    "type": effect.type,
                    "species": effect.species,
                    "position": effect.position,
                    "metadata": effect.metadata,
                    "state_vars": effect.state_vars,
                    "skeleton_id": effect.skeleton_id,
                    "initial_attrs": effect.initial_attrs or {},
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
