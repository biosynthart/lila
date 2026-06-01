# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Actor System — Base Classes and Interaction Context

Each actor implements a clean interface:
    resolve(ctx: InteractionContext) -> list[Effect]

Actors receive read-only context snapshots and return immutable Effect objects.
The engine's effect bus collects all effects from all actors, then applies them
atomically at the end of each phase.

See Also:
- ``effects.py`` — All Effect dataclasses + EffectBus
- ``actors/interaction_actors.py`` — FleeActor, PredationActor, HerbivoryActor, PollinationActor
- ``actors/flow_actors.py`` — ConsumerFlowActor, ProducerFlowActor, DecomposerFlowActor
- ``actors/guard_actors.py`` — ConsumerGuardActor, ProducerGuardActor, DecomposerGuardActor
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InteractionContext:
    """Read-only snapshot passed to actors.

    Actors receive this context and must not mutate it. All state access
    goes through read-only views or queries. The engine builds these contexts
    each tick from the current simulation state.

    Attributes:
        tick: Current simulation tick number.
        entity: Read-only view of the entity being evaluated.
        voxel_grid: VoxelManager instance — read-only access through context.
        biome: Biome configuration for this simulation.
        compiled: CompiledEcology from trait compiler (for interaction matrix lookups).
        params: Trait-derived parameters for the entity's species.
        nearby_entities: Entities within sensory range (from spatial index query).
        water_sources: Water sources in the world (read-only list of dicts).
        climate: Climate parameters (temperature, humidity, etc.).
        rate_multipliers: World-level rate multipliers from the engine config.
    """
    tick: int

    # The entity this actor is evaluating (read-only view)
    entity: dict[str, Any]

    # Read-only voxel access (no mutations through context)
    voxel_grid: Any  # VoxelManager — avoid circular import

    # Biome and climate configuration
    biome: Any  # BiomeConfig — avoid circular import

    # Compiled ecology from trait compiler (for interaction matrix lookups)
    compiled: Any = None  # CompiledEcology — avoid circular import

    # Trait-derived parameters for the entity's species
    params: Any = None  # DerivedParams | None — avoid circular import

    # Spatial query results — entities within sensory range
    nearby_entities: list[dict[str, Any]] = field(default_factory=list)

    # Water sources (read-only)
    water_sources: list[dict[str, Any]] = field(default_factory=list, repr=False)

    # Climate parameters
    climate: dict[str, float] = field(default_factory=dict)

    # Rate multipliers from engine config
    rate_multipliers: dict[str, float] = field(default_factory=lambda: {
        "consumption": 1.0,
        "hunger": 1.0,
        "thirst": 1.0,
        "growth": 1.0,
        "reproduction": 1.0,
    })


class InteractionActor(ABC):
    """Base protocol for all simulation actors.

    Each actor implements resolve() which takes a read-only context
    and returns a list of effects describing what should change.
    The engine's effect bus collects all effects from all actors,
    then applies them atomically.
    """

    @abstractmethod
    def resolve(self, ctx: InteractionContext) -> list[Any]:
        """Evaluate conditions and return effects to apply.

        Args:
            ctx: Read-only simulation context snapshot.

        Returns:
            List of immutable Effect objects describing state changes.
            Empty list if no action is needed.
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Flow Actor Protocol (Phase 2)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FlowContext(InteractionContext):
    """Extended context for flow actors.

    Adds dt, rain_ticks_remaining, and entities reference to the base
    InteractionContext (needed for tree collapse pressure check).
    """
    dt: float = 0.1
    rain_ticks_remaining: int = 0
    _entities: dict[str, Any] = field(default_factory=dict, repr=False)
    # Callable to look up DerivedParams for any entity by species_id.
    # Provided by engine so actors can query other entities' traits.
    _get_params: Any = None  # (entity: dict) -> DerivedParams | None


# ═══════════════════════════════════════════════════════════════════════════════
# Guard Actor Protocol (Phase 2)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class GuardContext(InteractionContext):
    """Extended context for guard actors.

    Adds entities reference (for mate search, support count) to the base
    InteractionContext.
    """
    # All entities in simulation — needed for mate search and tree collapse check
    _entities: dict[str, Any] = field(default_factory=dict, repr=False)
    # Callable to look up DerivedParams for any entity by species_id.
    # Provided by engine so actors can query other entities' traits.
    _get_params: Any = None  # (entity: dict) -> DerivedParams | None


class FlowActor(InteractionActor):
    """Subtype for continuous flow actors (Phase 2)."""

    @abstractmethod
    def resolve(self, ctx: FlowContext) -> list[Any]:
        raise NotImplementedError


class GuardActor(InteractionActor):
    """Subtype for discrete state transition actors (Phase 2)."""

    @abstractmethod
    def resolve(self, ctx: GuardContext) -> list[Any]:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════════════
# Actor Registry — Maps entity IDs to their responsible actor instances
# ═══════════════════════════════════════════════════════════════════════════════

from ..effects import Effect as Effect  # noqa: E402


class InteractionActorRegistry:
    """Maps species IDs to lists of interaction actor instances.

    The engine populates this at init time based on each entity's
    functional role and interaction capabilities (diet type, floral affinity).
    A single species can have multiple actors (e.g. FleeActor + HerbivoryActor),
    so we store a list per species_id.
    """

    def __init__(self) -> None:
        self._actors: dict[str, list[InteractionActor]] = {}

    def register(self, entity_id: str, actor: InteractionActor) -> None:
        self._actors.setdefault(entity_id, []).append(actor)

    def get(self, entity_id: str) -> list[InteractionActor]:
        return self._actors.get(entity_id, [])

    def clear(self) -> None:
        self._actors.clear()


class FlowActorRegistry:
    """Maps species IDs to their flow actor instances (Phase 2)."""

    def __init__(self) -> None:
        self._actors: dict[str, FlowActor] = {}

    def register(self, entity_id: str, actor: FlowActor) -> None:
        self._actors[entity_id] = actor

    def get(self, entity_id: str) -> FlowActor | None:
        return self._actors.get(entity_id)

    def clear(self) -> None:
        self._actors.clear()


class GuardActorRegistry:
    """Maps species IDs to their guard actor instances (Phase 2)."""

    def __init__(self) -> None:
        self._actors: dict[str, GuardActor] = {}

    def register(self, entity_id: str, actor: GuardActor) -> None:
        self._actors[entity_id] = actor

    def get(self, entity_id: str) -> GuardActor | None:
        return self._actors.get(entity_id)

    def clear(self) -> None:
        self._actors.clear()


class ActorRegistry:
    """Maps entity IDs to their responsible actor instances across all phases.

    The engine populates this at init time based on each entity's
    functional role (consumer/producer/decomposer) and interaction
    capabilities (has floral_affinity, is carnivore, etc.).
    """

    def __init__(self) -> None:
        self._interaction_actors: dict[str, InteractionActor] = {}
        self._flow_actors: dict[str, FlowActor] = {}  # Phase 2
        self._guard_actors: dict[str, GuardActor] = {}  # Phase 2

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


# ═══════════════════════════════════════════════════════════════════════════════
# Registry Builders — Called once at engine init
# ═══════════════════════════════════════════════════════════════════════════════

def build_interaction_registry(compiled) -> InteractionActorRegistry:
    """Build the interaction actor registry from compiled ecology.

    Called once at engine init. Each species gets registered with the
    appropriate actors based on its traits. The engine looks up actors
    per entity using the entity's species_id (actors are stateless).

    Args:
        compiled: CompiledEcology from trait_compiler.compile_world().

    Returns:
        Registry mapping species IDs to their interaction actors.
    """
    registry = InteractionActorRegistry()

    # Register by species — the engine will look up actors per entity
    # using the entity's species_id. Actors are stateless, so we reuse them.
    for species_id in compiled.derived_params:
        params = compiled.get_params(species_id)
        if params is None:
            continue

        actors_for_species: list[InteractionActor] = []

        # Flee actor — all mobile consumers flee from predators
        if params.diet_type not in ("autotroph", "decomposer"):
            actors_for_species.append(FleeActor())

        # Predation actor — carnivores/insectivores hunt prey
        if params.diet_type in ("carnivore", "insectivore", "omnivore"):
            actors_for_species.append(PredationActor())

        # Herbivory actor — herbivores/omnivores consume plants
        if params.diet_type in ("herbivore", "omnivore"):
            actors_for_species.append(HerbivoryActor())

        # Pollination actor — entities with floral affinity visit flowers
        if getattr(params, "floral_affinity", False):
            actors_for_species.append(PollinationActor())

        for actor in actors_for_species:
            registry.register(species_id, actor)

    return registry


def build_flow_registry(compiled) -> FlowActorRegistry:
    """Build the flow actor registry from compiled ecology (Phase 2).

    Called once at engine init. Each species gets registered with its
    appropriate flow actor based on diet_type.

    Args:
        compiled: CompiledEcology from trait_compiler.compile_world().

    Returns:
        Registry mapping species IDs to their flow actors.
    """
    registry = FlowActorRegistry()

    for species_id in compiled.derived_params:
        params = compiled.get_params(species_id)
        if params is None:
            continue

        actor: FlowActor | None = None
        if params.diet_type == "autotroph":
            actor = ProducerFlowActor()
        elif params.diet_type == "decomposer":
            actor = DecomposerFlowActor()
        else:
            # consumer (carnivore, herbivore, omnivore, insectivore)
            actor = ConsumerFlowActor()

        if actor is not None:
            registry.register(species_id, actor)

    return registry


def build_guard_registry(compiled) -> GuardActorRegistry:
    """Build the guard actor registry from compiled ecology (Phase 2).

    Called once at engine init. Each species gets registered with its
    appropriate guard actor based on diet_type.

    Args:
        compiled: CompiledEcology from trait_compiler.compile_world().

    Returns:
        Registry mapping species IDs to their guard actors.
    """
    registry = GuardActorRegistry()

    for species_id in compiled.derived_params:
        params = compiled.get_params(species_id)
        if params is None:
            continue

        actor: GuardActor | None = None
        if params.diet_type == "autotroph":
            actor = ProducerGuardActor()
        elif params.diet_type == "decomposer":
            actor = DecomposerGuardActor()
        else:
            # consumer (carnivore, herbivore, omnivore, insectivore)
            actor = ConsumerGuardActor()

        if actor is not None:
            registry.register(species_id, actor)

    return registry


# Import interaction actors here to avoid circular imports
# Import flow and guard actors for registry builders
from .flow_actors import (  # noqa: E402, F401
    ConsumerFlowActor,
    DecomposerFlowActor,
    ProducerFlowActor,
)
from .guard_actors import (  # noqa: E402, F401
    ConsumerGuardActor,
    DecomposerGuardActor,
    ProducerGuardActor,
)
from .interaction_actors import (  # noqa: E402, F401
    FleeActor,
    HerbivoryActor,
    PollinationActor,
    PredationActor,
)
