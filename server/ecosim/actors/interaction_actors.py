# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Interaction Actors — Phase 1 Refactoring

Extracts entity↔entity interaction logic from the monolithic engine into
pure-function actors that return immutable Effect objects instead of
mutating state directly.

Actors:
- FleeActor: Check for nearby predators and trigger flee response
- PredationActor: Carnivore/insectivore attempts to catch nearby prey
- HerbivoryActor: Herbivore/omnivore attempts to consume nearby plants
- PollinationActor: Pollinator visits a nearby FRUITING flower

Each actor implements ``resolve(ctx) -> list[Effect]`` — given the same
context, always produces the same effects. Deterministic by construction.

See Also:
- ``effects.py`` — All Effect dataclasses + EffectBus
- ``actors/__init__.py`` — InteractionActor base class + InteractionContext
"""

from __future__ import annotations

import math
import random
from typing import Any

from ..constants import (
    FLEE_ESCAPE_DISTANCE,
    FLEE_TRIGGER_DISTANCE,
    HERBIVORY_CONSUME_DISTANCE,
    HERBIVORY_MIN_HUNGER,
    OM_DEPOSIT_MAX,
    OM_DEPOSIT_MIN,
    OM_DEPOSIT_SCALE,
    POLLINATION_HEALTH_BOOST,
    POLLINATION_VISIT_DISTANCE,
    POLLINATOR_CROWD_RADIUS,
    POLLINATOR_MAX_PER_FLOWER,
    POLLINATOR_POST_VISIT_COOLDOWN,
    PREDATION_CATCH_DISTANCE,
)
from ..effects import (
    ClearTarget,
    Effect,
    EventRecord,
    LingerEffect,
    RemoveEntity,
    SetEntityAttr,
    SetStateVar,
    SetTarget,
    StateTransition,
    StateVarDelta,
    VoxelDelta,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FleeActor — Predator Proximity Detection + Escape Response
# ═══════════════════════════════════════════════════════════════════════════════

class FleeActor:
    """Check for nearby predators and trigger flee response.

    Detection: entity has flee targets from interaction matrix, predator
    within FLEE_TRIGGER_DISTANCE (2.0).

    Returns effects: StateTransition to FLEEING, SetTarget with escape position,
    and optionally an EventRecord if the state actually changed.
    """

    def resolve(self, ctx: Any) -> list[Effect]:
        """Evaluate flee conditions and return effects.

        Args:
            ctx: InteractionContext with entity, params, nearby_entities, etc.

        Returns:
            List of Effect objects (StateTransition + SetTarget + optional EventRecord).
        """
        if ctx.params is None:
            return []

        p = ctx.params
        if p.speed <= 0:
            return []

        # Get flee targets from compiled ecology
        flee_targets = self._get_flee_targets(p.species_id, ctx)
        if not flee_targets:
            return []

        # Check each nearby entity for predator match
        for other in ctx.nearby_entities:
            if other.get("species", "") in flee_targets:
                dist = self._distance(ctx.entity["position"], other["position"])
                if dist < FLEE_TRIGGER_DISTANCE:
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

    @staticmethod
    def _get_flee_targets(species_id: str, ctx: Any) -> list[str]:
        """Get species that this entity should flee from."""
        if hasattr(ctx.compiled, "get_flee_targets"):
            return ctx.compiled.get_flee_targets(species_id) or []
        return []

    @staticmethod
    def _distance(a: list[float], b: list[float]) -> float:
        """Euclidean distance in x-z plane (2D)."""
        dx = a[0] - b[0]
        dz = a[2] - b[2]
        return math.sqrt(dx * dx + dz * dz)

    @staticmethod
    def _flee_direction(pos: list[float], threat_pos: list[float]) -> list[float]:
        """Calculate escape target: run FLEE_ESCAPE_DISTANCE away from threat."""
        dx = pos[0] - threat_pos[0]
        dz = pos[2] - threat_pos[2]
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 0.01:
            dx, dz = random.uniform(-1, 1), random.uniform(-1, 1)
            dist = math.sqrt(dx * dx + dz * dz)

        # Clamp to grid bounds (default 32x32 grid)
        grid_max = 31.0
        escape_x = max(0.0, min(grid_max, pos[0] + (dx / dist) * FLEE_ESCAPE_DISTANCE))
        escape_z = max(0.0, min(grid_max, pos[2] + (dz / dist) * FLEE_ESCAPE_DISTANCE))
        return [escape_x, 0.0, escape_z]


# ═══════════════════════════════════════════════════════════════════════════════
# PredationActor — Carnivore/Insectivore Hunting + Consumption
# ═══════════════════════════════════════════════════════════════════════════════

class PredationActor:
    """Carnivore/insectivore attempts to catch nearby prey.

    Detection: predator in HUNTING state with hunger > 0.3, prey within
    PREDATION_CATCH_DISTANCE (1.5), and species match from interaction matrix.

    Returns effects for both predator (hunger/energy changes) and prey
    (health drain → DYING → removal + OM deposit).
    """

    def resolve(self, ctx: Any) -> list[Effect]:
        """Evaluate predation conditions and return effects."""
        if ctx.params is None:
            return []

        p = ctx.params
        if p.diet_type not in ("carnivore", "insectivore", "omnivore"):
            return []

        # Check hunting state and hunger threshold
        if ctx.entity["state"] != "HUNTING" or ctx.entity["state_vars"]["hunger"] <= 0.3:
            return []

        # Find catchable prey from interaction matrix
        prey_species = self._get_prey_species(p.species_id, ctx)
        if not prey_species:
            return []

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
            # Predator gains from predation
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
            # Prey is killed
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
            # Prey biomass deposited as organic matter
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
    def _get_prey_species(species_id: str, ctx: Any) -> list[str]:
        """Get prey species from the compiled ecology's diet order."""
        diet_order = getattr(ctx.compiled, "get_diet_order", lambda s: [])
        if not hasattr(ctx.compiled, "get_interactions"):
            return []

        result = []
        for target_species, _ in diet_order(species_id):
            interactions = ctx.compiled.get_interactions(species_id, target_species)
            if any(ix.interaction_type == "predation" for ix in interactions):
                result.append(target_species)
        return result

    @staticmethod
    def _distance(a: list[float], b: list[float]) -> float:
        dx = a[0] - b[0]
        dz = a[2] - b[2]
        return math.sqrt(dx * dx + dz * dz)

    @staticmethod
    def _compute_om_deposit(entity: dict, params: Any) -> float:
        """Compute organic matter deposit from entity biomass."""
        if params is not None and hasattr(params, "metabolic_rate"):
            deposit = min(OM_DEPOSIT_MAX, params.metabolic_rate * OM_DEPOSIT_SCALE)
            return max(deposit, OM_DEPOSIT_MIN)
        mass = entity.get("metadata", {}).get("body_mass", 10.0)
        return min(0.3, mass / 500.0)


# ═══════════════════════════════════════════════════════════════════════════════
# HerbivoryActor — Herbivore/Omnivore Grazing + Consumption
# ═══════════════════════════════════════════════════════════════════════════════

class HerbivoryActor:
    """Herbivore/omnivore attempts to consume nearby plants.

    Detection: entity in FORAGING state with hunger > HERBIVORY_MIN_HUNGER,
    plant within HERBIVORY_CONSUME_DISTANCE (2.0), species match from diet order.

    Returns effects for both herbivore (hunger relief) and plant (growth/health damage).
    """

    def resolve(self, ctx: Any) -> list[Effect]:
        """Evaluate herbivory conditions and return effects."""
        if ctx.params is None:
            return []

        p = ctx.params
        if ctx.entity["state"] != "FORAGING" or ctx.entity["state_vars"]["hunger"] <= HERBIVORY_MIN_HUNGER:
            return []

        diet_order = self._get_diet_order(p.species_id, ctx)
        if not diet_order:
            return []

        # Find best target by preference ordering (lowest preference number = highest priority)
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
                    interactions = ctx.compiled.get_interactions(p.species_id, other_species)
                    for ix in interactions:
                        if (ix.interaction_type == "herbivory"
                                and other.get("state_vars", {}).get("growth", 0) > 0.1
                                and pref < best_pref):
                            best_pref = pref
                            best_target = other
                    break

        if best_target is None:
            return []

        plant = best_target
        rate_consumption = ctx.rate_multipliers.get("consumption", 1.0)

        effects: list[Effect] = [
            # Herbivore gains hunger relief
            StateVarDelta(
                entity_id=ctx.entity["id"],
                var_name="hunger",
                delta=-p.herbivory_relief,
                tick=ctx.tick,
            ),
            # Plant takes growth damage
            SetStateVar(
                entity_id=plant["id"],
                var_name="growth",
                value=max(0.0, plant["state_vars"]["growth"] - p.consumption_damage_growth * rate_consumption),
                tick=ctx.tick,
            ),
            # Plant takes health damage
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

    @staticmethod
    def _get_diet_order(species_id: str, ctx: Any) -> list[tuple[str, int]]:
        """Get diet preference ordering from compiled ecology."""
        if hasattr(ctx.compiled, "get_diet_order"):
            return ctx.compiled.get_diet_order(species_id) or []
        return []

    @staticmethod
    def _distance(a: list[float], b: list[float]) -> float:
        dx = a[0] - b[0]
        dz = a[2] - b[2]
        return math.sqrt(dx * dx + dz * dz)


# ═══════════════════════════════════════════════════════════════════════════════
# PollinationActor — Pollinator Visits Flowers for Nectar
# ═══════════════════════════════════════════════════════════════════════════════

class PollinationActor:
    """Pollinator visits a nearby FRUITING flower.

    Detection: entity has floral_affinity, plant is in FRUITING state,
    not on pollination cooldown, species match from interaction matrix.

    Returns effects for both pollinator (hunger/hydration relief + linger)
    and plant (health boost + cooldown).
    """

    def resolve(self, ctx: Any) -> list[Effect]:
        """Evaluate pollination conditions and return effects."""
        if ctx.params is None or not getattr(ctx.params, "floral_affinity", False):
            return []

        # Skip if already lingering at a flower
        if ctx.entity.get("_linger", 0) > 0:
            return []

        # Skip if post-visit cooldown is active — prevents immediate re-pollination
        # after lingering ends. Forces the butterfly to actually fly away and explore
        # before it can visit another flower.
        if ctx.entity.get("_pollination_cooldown", 0) > 0:
            return []

        # Skip if in WANDERING state — during forced exploration cooldown,
        # butterflies should wander randomly across the field, not pollinate.
        if ctx.entity["state"] == "WANDERING":
            return []

        for other in ctx.nearby_entities:
            other_species = other.get("species", "")
            interactions = ctx.compiled.get_interactions(ctx.params.species_id, other_species)

            for ix in interactions:
                if (ix.interaction_type == "pollination"
                        and other["state"] == "FRUITING"
                        and other.get("_pollination_cooldown", 0) <= 0):

                    # Pollinator must physically arrive at the flower.
                    # With global chemical sensing, nearby_entities includes
                    # all flowers in the field — but actual pollination only
                    # happens when the butterfly is close enough to land.
                    if self._distance(ctx.entity["position"], other["position"]) > POLLINATION_VISIT_DISTANCE:
                        continue

                    plant = other

                    # Enforce per-flower visitor cap — count how many other
                    # pollinators are already lingering near this flower.
                    if self._count_pollinators_at_flower(
                            plant["position"], ctx.nearby_entities) >= POLLINATOR_MAX_PER_FLOWER:
                        continue  # Flower is full, try the next one

                    # Build visited flowers tracking effect
                    expiry_tick = ctx.tick + ix.linger_ticks + ix.cooldown_ticks

                    effects: list[Effect] = [
                        # Plant gets health boost from pollination
                        SetStateVar(
                            entity_id=plant["id"],
                            var_name="health",
                            value=min(1.0, plant["state_vars"]["health"] + POLLINATION_HEALTH_BOOST),
                            tick=ctx.tick,
                        ),
                        # Pollinator gets hunger relief from nectar
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

                    # Linger at flower (stop moving)
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
                        # Post-visit cooldown on the pollinator — prevents immediate
                        # re-pollination after lingering ends. The butterfly must fly
                        # away and explore for POLLINATOR_POST_VISIT_COOLDOWN ticks
                        # before it can visit another flower.
                        SetEntityAttr(
                            entity_id=ctx.entity["id"],
                            attr_name="_pollination_cooldown",
                            value=float(POLLINATOR_POST_VISIT_COOLDOWN),
                            tick=ctx.tick,
                        ),
                    ])

                    # Track consecutive pollination visits — incremented here in the
                    # interaction actor so ALL pollinations are counted (not just
                    # those during FORAGING state). Used by guard actor to force
                    # WANDERING after N visits so butterflies explore the field.
                    current_visits = ctx.entity.get("_pollination_visits", 0.0)
                    effects.append(SetEntityAttr(
                        entity_id=ctx.entity["id"],
                        attr_name="_pollination_visits",
                        value=current_visits + 1.0,
                        tick=ctx.tick,
                    ))

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

    @staticmethod
    def _count_pollinators_at_flower(
            flower_pos: list[float], nearby_entities: list) -> int:
        """Count other pollinators already lingering near a given flower.

        Used to enforce POLLINATOR_MAX_PER_FLOWER so butterflies disperse
        across the field instead of all clustering on one plant.
        Counts entities within POLLINATOR_CROWD_RADIUS that have _linger > 0
        (actively visiting) or have a movement target set near this flower
        (en route to visit).
        """
        count = 0
        r2 = POLLINATOR_CROWD_RADIUS ** 2
        for entity in nearby_entities:
            if not entity.get("_linger", 0):
                continue
            dx = entity["position"][0] - flower_pos[0]
            dz = entity["position"][2] - flower_pos[2]
            if dx * dx + dz * dz <= r2:
                count += 1
        return count

    @staticmethod
    def _distance(a: list[float], b: list[float]) -> float:
        """Euclidean distance in x-z plane (2D)."""
        dx = a[0] - b[0]
        dz = a[2] - b[2]
        return math.sqrt(dx * dx + dz * dz)
