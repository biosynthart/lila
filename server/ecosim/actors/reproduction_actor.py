# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Reproduction Actor — Entity spawning for animals and plants.

Extracted from inline reproduction logic in ConsumerGuardActor and
ProducerFlowActor to centralize all entity-spawning behavior into a
single actor that emits SpawnEntity effects.

Handles:
- Animal reproduction (mate search, offspring inheritance, parent costs)
- Plant vegetative spreading (density/soil checks, child spawn)

See Also:
- ``actors/guard_actors.py`` — ConsumerGuardActor calls resolve_animal()
- ``actors/flow_actors.py`` — ProducerFlowActor calls resolve_plant()
"""

from __future__ import annotations

import math
import random as _random
from typing import Any

from ..constants import (
    CHILD_COLONY_FLOOR,
    CHILD_COLONY_INHERIT,
    CHILD_ENERGY_FLOOR,
    CHILD_ENERGY_INHERIT,
    CHILD_HEALTH_FLOOR,
    CHILD_HEALTH_INHERIT,
    CHILD_HUNGER_INHERIT,
    POLLINATOR_POST_VISIT_COOLDOWN,
    SPAWN_OFFSET,
    SPREAD_DENSITY_RADIUS,
    SPREAD_MIN_GROWTH,
    SPREAD_MIN_HEALTH,
    SPREAD_MIN_HYDRATION,
    SPREAD_PARENT_GROWTH_COST,
    SPREAD_PARENT_NUTRIENT_COST,
    SPREAD_SOIL_MIN_MOISTURE,
    SPREAD_SOIL_MIN_NUTRIENTS,
)
from ..effects import (
    EventRecord,
    SetStateVar,
    SpawnEntity,
    StateTransition,
    StateVarDelta,
)
from ..entities import is_alive


class ReproductionActor:
    """Handles entity spawning for both animal reproduction and plant spreading.

    Emits SpawnEntity effects with inherited state variables, parent cost
    deductions, and event records. Called by ConsumerGuardActor (animals)
    and ProducerFlowActor (plants).
    """

    # ────────────────────────────────────────────────────────────────────────
    # Animal reproduction
    # ────────────────────────────────────────────────────────────────────────

    def resolve_animal(self, ctx: Any) -> list[Any]:
        """Evaluate animal reproduction for one entity this tick.

        Checks reproductive drive threshold and mate availability. If both
        pass, emits effects for parent cost deduction, offspring spawning
        with inherited state vars, and a state transition to REPRODUCING.

        Args:
            ctx: GuardContext with params, entities reference (_entities),
                 voxel_grid, biome, and rate_multipliers.

        Returns:
            List of Effect objects (SpawnEntity, StateVarDelta, EventRecord,
            StateTransition) or empty list if reproduction should not occur.
        """
        p = ctx.params
        sv = ctx.entity["state_vars"]

        # Drive must exceed threshold
        if sv.get("reproductive_drive", 0) <= p.repro_drive_threshold:
            return []

        # Skip entities in terminal states
        if ctx.entity["state"] in ("DYING", "REPRODUCING", "SWARMING"):
            return []

        # Mate search — iterate over all entities for proximity check
        mate_found = self._find_mate(ctx)
        if not mate_found:
            return []

        return self._animal_reproduction_effects(ctx, p, sv)

    @staticmethod
    def _find_mate(ctx: Any) -> bool:
        """Check if a living mate of the same species is within sensory range."""
        entity = ctx.entity
        params = ctx.params
        for other in ctx._entities.values():
            if not is_alive(other):
                continue
            if other["id"] == entity["id"]:
                continue
            if other.get("species") != entity.get("species"):
                continue
            dx = other["position"][0] - entity["position"][0]
            dz = other["position"][2] - entity["position"][2]
            dist = math.sqrt(dx * dx + dz * dz)
            if dist <= params.sensory_range:
                return True
        return False

    @staticmethod
    def _animal_reproduction_effects(
        ctx: Any, p: Any, sv: dict[str, float],
    ) -> list[Any]:
        """Generate reproduction effects for animal spawning.

        Returns:
            Effects for parent cost deduction, offspring SpawnEntity with
            inherited state vars, EventRecord, and StateTransition to REPRODUCING.
        """
        meta = ctx.entity["metadata"]
        pos = ctx.entity["position"]
        effects: list[Any] = []

        # ── Parent reproduction cost ──
        # Reset reproductive drive and pay energy cost
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="reproductive_drive",
            delta=-sv.get("reproductive_drive", 0.0), tick=ctx.tick,
        ))
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="energy",
            delta=-p.parent_energy_cost, tick=ctx.tick,
        ))

        # Colony health cost for insect-type entities (check type, not diet_type)
        if "colony_health" in sv and ctx.entity.get("type") == "INSECT":
            colony_cost = p.parent_energy_cost * 0.3
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="colony_health",
                delta=-colony_cost, tick=ctx.tick,
            ))

        # ── Spawn offspring (clutch_size from DerivedParams) ──
        clutch_size = p.clutch_size if p.clutch_size > 0 else 1
        for i in range(clutch_size):
            # Offspring position — near parent with small offset
            new_x = pos[0] + _random.uniform(-SPAWN_OFFSET, SPAWN_OFFSET)
            new_z = pos[2] + _random.uniform(-SPAWN_OFFSET, SPAWN_OFFSET)

            # Child inherits parent stress (generational decline) with floors
            offspring_sv: dict[str, float] = {
                "hunger": sv.get("hunger", 0.5) * CHILD_HUNGER_INHERIT,
                "energy": max(CHILD_ENERGY_FLOOR, sv.get("energy", 1.0) * CHILD_ENERGY_INHERIT),
                "hydration": sv.get("hydration", 1.0),
                "health": max(CHILD_HEALTH_FLOOR, sv.get("health", 1.0) * CHILD_HEALTH_INHERIT),
                "reproductive_drive": 0.0,
                "age": 0.0,
            }

            if "colony_health" in sv:
                offspring_sv["colony_health"] = max(
                    CHILD_COLONY_FLOOR, sv["colony_health"] * CHILD_COLONY_INHERIT)

            # Insect-specific state vars
            if ctx.entity.get("type") == "INSECT":
                offspring_sv.setdefault("activity", 1.0)
                offspring_sv.setdefault("population", 1.0)

            child_id = f"{ctx.entity['id']}_child_{ctx.tick}_{_random.randint(0, 999)}"
            effects.extend([
                SpawnEntity(
                    entity_id=child_id,
                    type=ctx.entity["type"],
                    species=ctx.entity.get("species"),
                    position=[new_x, pos[1], new_z],
                    metadata=dict(meta),
                    state_vars=offspring_sv,
                    skeleton_id=ctx.entity.get("skeleton_id"),
                    initial_attrs={
                        # Newborn pollinators start with a cooldown so they don't
                        # immediately re-pollinate the flower their parent was at.
                        "_pollination_cooldown": float(POLLINATOR_POST_VISIT_COOLDOWN),
                    },
                    tick=ctx.tick,
                ),
                EventRecord(
                    event_type="REPRODUCTION",
                    source_id=ctx.entity["id"],
                    target_id=child_id,
                    position=[new_x, pos[1], new_z],
                    tick=ctx.tick,
                ),
            ])

        # State transition to REPRODUCING (applied after spawns so parent stays alive)
        effects.append(StateTransition(
            entity_id=ctx.entity["id"], new_state="REPRODUCING", tick=ctx.tick,
        ))

        return effects

    # ────────────────────────────────────────────────────────────────────────
    # Plant vegetative spreading
    # ────────────────────────────────────────────────────────────────────────

    def resolve_plant(self, ctx: Any, sv: dict[str, Any], p: Any, dt: float) -> list[Any]:
        """Evaluate plant vegetative spreading for one entity this tick.

        Checks health/hydration/growth thresholds, cooldown, random chance,
        density constraints, and soil quality at target position. If all pass,
        emits effects for parent cost deduction and child SpawnEntity.

        Args:
            ctx: FlowContext with params, entities reference (_entities),
                 voxel_grid, biome, and rate_multipliers.
            sv: Entity state_vars dict (passed in to avoid redundant lookup).
            p: DerivedParams for the entity's species.
            dt: Time step in seconds.

        Returns:
            List of Effect objects or empty list if spreading should not occur.
        """
        # Threshold checks — must meet all three
        if (sv.get("health", 1.0) < SPREAD_MIN_HEALTH
                or sv.get("hydration", 1.0) < SPREAD_MIN_HYDRATION
                or sv.get("growth", 0.1) < SPREAD_MIN_GROWTH):
            return []

        # Cooldown check
        cooldown = ctx.entity.get("_spread_cooldown", 0)
        if cooldown > 0:
            return [SetStateVar(
                entity_id=ctx.entity["id"], var_name="_spread_cooldown",
                value=float(cooldown - 1), tick=ctx.tick,
            )]

        # Random chance gate (uses spread_chance from DerivedParams)
        rate_reproduction = ctx.rate_multipliers.get("reproduction", 1.0)
        if _random.random() > p.spread_chance * rate_reproduction:
            return []

        spread_range = p.spread_range or 2.0
        # Pick a random position within spread range
        pos = ctx.entity["position"]
        spread_pos = [
            pos[0] + _random.uniform(-spread_range, spread_range),
            0.0,
            pos[2] + _random.uniform(-spread_range, spread_range),
        ]

        # Density check — no other autotroph within SPREAD_DENSITY_RADIUS
        get_params = getattr(ctx, "_get_params", None)
        for ent in ctx._entities.values():
            if not is_alive(ent) or ent["state"] == "DORMANT":
                continue
            if ent["id"] == ctx.entity["id"]:
                continue
            # Check if autotroph via params or entity type
            ep = get_params(ent) if get_params else None
            is_autotroph = (
                (ep is not None and getattr(ep, "diet_type", "") == "autotroph")
                or ent.get("type") in ("PLANT", "TREE")
            )
            if not is_autotroph:
                continue
            dx = ent["position"][0] - spread_pos[0]
            dz = ent["position"][2] - spread_pos[2]
            dist = math.sqrt(dx * dx + dz * dz)
            if dist <= SPREAD_DENSITY_RADIUS:
                # Set cooldown and abort
                return [SetStateVar(
                    entity_id=ctx.entity["id"], var_name="_spread_cooldown",
                    value=float(p.spread_cooldown // 2), tick=ctx.tick,
                )]

        # Soil quality check at target position
        gx, gy, gz = ctx.voxel_grid.world_to_grid(*spread_pos)
        if (ctx.voxel_grid.get("moisture", gx, gy, gz) < SPREAD_SOIL_MIN_MOISTURE
                or ctx.voxel_grid.get("nutrients", gx, gy, gz) < SPREAD_SOIL_MIN_NUTRIENTS):
            return []

        # Offspring state vars (matches engine inline)
        offspring_sv = {
            "growth": 0.05,
            "hydration": ctx.voxel_grid.get("moisture", gx, gy, gz) * 0.8,
            "nutrient_store": 0.3,
            "health": 0.8,
            "age": 0.0,
        }

        return [
            # Parent pays fixed cost to spread
            StateVarDelta(
                entity_id=ctx.entity["id"], var_name="growth",
                delta=-SPREAD_PARENT_GROWTH_COST, tick=ctx.tick,
            ),
            StateVarDelta(
                entity_id=ctx.entity["id"], var_name="nutrient_store",
                delta=-SPREAD_PARENT_NUTRIENT_COST, tick=ctx.tick,
            ),
            # Set spread cooldown on parent
            SetStateVar(
                entity_id=ctx.entity["id"], var_name="_spread_cooldown",
                value=float(p.spread_cooldown), tick=ctx.tick,
            ),
            # Spawn child plant
            SpawnEntity(
                entity_id=f"{ctx.entity['id']}_s{ctx.tick}",
                type=ctx.entity["type"],
                species=ctx.entity.get("species"),
                position=spread_pos,
                metadata=dict(ctx.entity["metadata"]),
                state_vars=offspring_sv,
                tick=ctx.tick,
            ),
        ]
