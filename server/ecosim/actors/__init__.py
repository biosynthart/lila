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
        params: Trait-derived parameters for the entity's species (None in legacy mode).
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
    compiled: Any = None  # CompiledEcology | LegacyParams — avoid circular import

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


# ═══════════════════════════════════════════════════════════════════════════════
# Actor Registry — Maps entity IDs to their responsible actor instances
# ═══════════════════════════════════════════════════════════════════════════════

from ..effects import Effect  # noqa: E402


class InteractionActorRegistry:
    """Maps entity IDs to their interaction actor instances.

    The engine populates this at init time based on each entity's
    functional role and interaction capabilities (diet type, floral affinity).
    """

    def __init__(self) -> None:
        self._actors: dict[str, InteractionActor] = {}

    def register(self, entity_id: str, actor: InteractionActor) -> None:
        self._actors[entity_id] = actor

    def get(self, entity_id: str) -> InteractionActor | None:
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


def build_interaction_registry(compiled: Any) -> InteractionActorRegistry:
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


# Import interaction actors here to avoid circular imports
from .interaction_actors import (  # noqa: E402, F401
    FleeActor,
    HerbivoryActor,
    PollinationActor,
    PredationActor,
)
