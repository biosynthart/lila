# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Flow Actors — Continuous state evolution as effect-emitting actors

Each flow actor implements resolve(ctx) → list[Effect] for continuous
state variable changes (hunger buildup, energy drain, growth, etc.).
The engine collects effects and applies them via the EffectBus.

See Also:
- ``effects.py`` — Effect dataclasses + EffectBus
- ``actors/guard_actors.py`` — Discrete state transition actors
"""

from __future__ import annotations

import math
from typing import Any

from ..constants import (
    ACTIVE_ENERGY_DRAIN_STATES,
    COLLAPSE_HEALTH_MULTIPLIER,
    COLLAPSE_HYDRATION_MULTIPLIER,
    COLLAPSE_SUPPORT_THRESHOLD,
    COLONY_STRESS_ENERGY,
    COLONY_STRESS_HUNGER,
    DEHYDRATION_HYDRATION,
    DRINK_RECOVERY_RATE,
    DRINK_SOIL_DRAIN,
    DRINK_WATER_DRAIN,
    ENERGY_RECOVERY_STATES,
    PLANT_BASE_GROWTH_RATE,
    PLANT_BASE_WATER_DEMAND,
    PLANT_HEALTH_CRITICAL_HYDRATION,
    PLANT_HEALTH_CRITICAL_NUTRIENTS,
    PLANT_SOIL_UPTAKE_RATE,
    REPRO_BUILD_MAX_HUNGER,
    REPRO_BUILD_MIN_ENERGY,
    REPRO_BUILD_MIN_HEALTH,
    REPRO_DECAY_ENERGY,
    REPRO_DECAY_HUNGER,
    SPREAD_DENSITY_RADIUS,
    SPREAD_MIN_GROWTH,
    SPREAD_MIN_HEALTH,
    SPREAD_MIN_HYDRATION,
    SPREAD_PARENT_GROWTH_COST,
    SPREAD_PARENT_NUTRIENT_COST,
    SPREAD_SOIL_MIN_MOISTURE,
    SPREAD_SOIL_MIN_NUTRIENTS,
    STARVATION_HUNGER,
    WATER_DRY_THRESHOLD,
    WATER_PROXIMITY_COLONY_FACTOR,
    WATER_PROXIMITY_HUNGER_FACTOR,
)
from ..effects import (
    SetStateVar,
    SpawnEntity,
    StateVarDelta,
    VoxelDelta,
)
from ..entities import is_alive
from ..traits import DerivedParams


class ConsumerFlowActor:
    """Continuous flow for all mobile consumers (animals, birds, insects).

    Handles: hunger buildup, energy drain/recovery, hydration loss,
    drinking recovery, near-water bonus, reproductive drive, health
    degradation under starvation/dehydration, colony health, and
    movement toward targets.

    All rate constants come from DerivedParams. Biome modifiers and world
    rate multipliers are applied on top.
    """

    def resolve(self, ctx: Any) -> list[Any]:
        """Evaluate consumer flow for one entity this tick.

        Args:
            ctx: InteractionContext with dt, params, voxel_grid, biome,
                 climate, and rate_multipliers.

        Returns:
            List of Effect objects describing state changes.
        """
        p = ctx.params
        if p is None:
            return []

        sv = ctx.entity["state_vars"]
        self._ensure_consumer_vars(sv)
        biome_mod = ctx.biome.hunger_rate_modifier * ctx.biome.metabolic_scaling
        dt = ctx.dt
        effects: list[Any] = []

        # ── Hunger — increases with metabolism (dt-dependent) ──
        hunger_delta = p.hunger_rate * biome_mod * ctx.rate_multipliers.get("hunger", 1.0) * dt
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="hunger",
            delta=hunger_delta, tick=ctx.tick,
        ))

        # ── Energy — drains during activity, recovers at rest (dt-dependent) ──
        if ctx.entity["state"] in ACTIVE_ENERGY_DRAIN_STATES:
            drain = p.energy_drain * ctx.biome.energy_drain_modifier * dt
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="energy",
                delta=-drain, tick=ctx.tick,
            ))
        elif ctx.entity["state"] in ENERGY_RECOVERY_STATES:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="energy",
                delta=p.energy_recovery * dt, tick=ctx.tick,
            ))

        # Lingering at a resource (e.g. pollination visit) also recovers energy
        if ctx.entity.get("_linger", 0) > 0:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="energy",
                delta=p.energy_recovery * dt, tick=ctx.tick,
            ))
            # Linger countdown and velocity reset are handled by engine
            # after effect application (side-effect on entity dict)

        # ── Hydration — temperature-driven loss, soil-based recovery when drinking ──
        temp = ctx.climate.get("temperature", 20.0)

        if ctx.entity["state"] == "DRINKING":
            gx, gy, gz = ctx.voxel_grid.world_to_grid(*ctx.entity["position"])
            soil_moisture = ctx.voxel_grid.get("moisture", gx, gy, gz)
            recovery = DRINK_RECOVERY_RATE * soil_moisture * dt
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="hydration",
                delta=recovery, tick=ctx.tick,
            ))
            # Drinking depletes local soil moisture and water source
            effects.append(VoxelDelta(
                layer="moisture", x=gx, y=gy, z=gz,
                delta=-DRINK_SOIL_DRAIN * ctx.rate_multipliers.get("thirst", 1.0) * dt,
                tick=ctx.tick,
            ))
            # Drain nearest water source (in-place mutation on context list)
            self._drain_nearest_water(ctx, DRINK_WATER_DRAIN * dt)

        else:
            thirst = p.thirst_rate * (temp / 30.0) * dt
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="hydration",
                delta=-thirst, tick=ctx.tick,
            ))

        # ── Near-water bonus — reduced hunger from browse/sip at water's edge (dt-dependent) ──
        if self._is_near_water(ctx):
            hunger_relief = p.hunger_rate * WATER_PROXIMITY_HUNGER_FACTOR * dt
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="hunger",
                delta=-hunger_relief, tick=ctx.tick,
            ))
            if "colony_health" in sv:
                colony_recovery = p.energy_recovery * WATER_PROXIMITY_COLONY_FACTOR * dt
                effects.append(StateVarDelta(
                    entity_id=ctx.entity["id"], var_name="colony_health",
                    delta=colony_recovery, tick=ctx.tick,
                ))

        # ── Age — always increments (dt-dependent) ──
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="age",
            delta=dt, tick=ctx.tick,
        ))

        # ── Reproductive drive — builds when healthy, decays under stress (dt-dependent) ──
        rate_repro = ctx.rate_multipliers.get("reproduction", 1.0)
        if (sv["energy"] > REPRO_BUILD_MIN_ENERGY
                and sv["hunger"] < REPRO_BUILD_MAX_HUNGER
                and sv.get("health", 1.0) > REPRO_BUILD_MIN_HEALTH):
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="reproductive_drive",
                delta=p.repro_drive_build * rate_repro * dt, tick=ctx.tick,
            ))
        elif sv["hunger"] > REPRO_DECAY_HUNGER or sv["energy"] < REPRO_DECAY_ENERGY:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="reproductive_drive",
                delta=-p.repro_drive_decay * dt, tick=ctx.tick,
            ))

        # ── Health — degrades under critical starvation or dehydration (dt-dependent) ──
        if sv["hunger"] > STARVATION_HUNGER:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="health",
                delta=-p.health_drain_starving * dt, tick=ctx.tick,
            ))
        if sv["hydration"] < DEHYDRATION_HYDRATION:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="health",
                delta=-p.health_drain_dehydrated * dt, tick=ctx.tick,
            ))

        # ── Colony health — accelerated drain under stress (hunger-scaled, dt-dependent) ──
        if "colony_health" in sv:
            if sv["hunger"] > COLONY_STRESS_HUNGER or sv["energy"] < COLONY_STRESS_ENERGY:
                drain = p.health_drain_starving * (1.0 + sv["hunger"] * 2.0) * dt
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

    def _is_near_water(self, ctx: Any) -> bool:
        """Check if entity is near any water source."""
        pos = ctx.entity["position"]
        for source in ctx.water_sources:
            if source.get("water_level", 1.0) < WATER_DRY_THRESHOLD:
                continue
            dx = pos[0] - source["position"][0]
            dz = pos[2] - source["position"][2]
            dist = math.sqrt(dx * dx + dz * dz)
            if dist <= source.get("radius", 1.0) + 1.0:
                return True
        return False

    @staticmethod
    def _drain_nearest_water(ctx: Any, amount: float) -> None:
        """Drain water from the nearest source (called during drinking)."""
        pos = ctx.entity["position"]
        best, best_dist = None, float("inf")
        for source in ctx.water_sources:
            d = math.sqrt(
                (pos[0] - source["position"][0]) ** 2 +
                (pos[2] - source["position"][2]) ** 2
            )
            if d < source.get("max_radius", 2.0) * 2 and d < best_dist:
                best_dist, best = d, source
        if best is not None:
            best["water_level"] = max(0.0, best["water_level"] - amount)


class ProducerFlowActor:
    """Continuous flow for autotroph sessile entities (plants, trees).

    Handles: evapotranspiration, water uptake from soil, growth via
    Liebig's law (limited by scarcest resource), nutrient uptake,
    health degradation, tree collapse pressure, and vegetative spreading.
    """

    def resolve(self, ctx: Any) -> list[Any]:
        """Evaluate producer flow for one entity this tick.

        Args:
            ctx: InteractionContext with dt, params, voxel_grid, biome,
                 climate, entities (for support count), and rate_multipliers.

        Returns:
            List of Effect objects describing state changes.
        """
        p = ctx.params
        if p is None:
            return []

        sv = ctx.entity["state_vars"]
        self._ensure_producer_vars(sv)
        dt = ctx.dt
        effects: list[Any] = []

        # Dormant plants have no active metabolism — roots persist
        if ctx.entity["state"] == "DORMANT":
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="age",
                delta=dt, tick=ctx.tick,
            ))
            return effects

        # Tick down pollination cooldown (handled as state var update)
        if ctx.entity.get("_pollination_cooldown", 0) > 0:
            new_cd = ctx.entity["_pollination_cooldown"] - 1
            effects.append(SetStateVar(
                entity_id=ctx.entity["id"], var_name="_pollination_cooldown",
                value=float(new_cd), tick=ctx.tick,
            ))

        temp = ctx.climate.get("temperature", 20.0)
        humidity = ctx.climate.get("humidity", 0.5)
        rain_ticks = getattr(ctx, "rain_ticks_remaining", 0) or 0

        # ── Evapotranspiration — hydration loss (suppressed during rain) ──
        if rain_ticks <= 0:
            evap = (ctx.biome.evaporation_rate * (temp / 30.0)
                    * (1.0 - humidity * 0.5) * ctx.rate_multipliers.get("thirst", 1.0))
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="hydration",
                delta=-evap * dt, tick=ctx.tick,
            ))

        # ── Water uptake from soil ──
        gx, gy, gz = ctx.voxel_grid.world_to_grid(*ctx.entity["position"])
        soil_moisture = ctx.voxel_grid.get("moisture", gx, gy, gz)
        uptake = min(PLANT_BASE_WATER_DEMAND * dt,
                     soil_moisture * PLANT_SOIL_UPTAKE_RATE * dt)
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="hydration",
            delta=uptake, tick=ctx.tick,
        ))

        # ── Growth — Liebig's law: limited by scarcest resource ──
        light = ctx.biome.light_availability
        soil_nutrients = ctx.voxel_grid.get("nutrients", gx, gy, gz)
        growth_potential = min(sv["hydration"], soil_nutrients, light)
        rate_growth = ctx.rate_multipliers.get("growth", 1.0)
        growth_inc = (PLANT_BASE_GROWTH_RATE * growth_potential
                      * ctx.biome.growth_rate_modifier * rate_growth * dt)
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="growth",
            delta=growth_inc, tick=ctx.tick,
        ))

        # ── Nutrient uptake from soil ──
        n_demand = ctx.entity["metadata"].get("nutrient_demand", {})
        total_demand = (sum(n_demand.values())
                        if isinstance(n_demand, dict)
                        else 1.0)  # PLANT_DEFAULT_NUTRIENT_DEMAND fallback
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="nutrient_store",
            delta=total_demand * soil_nutrients * dt, tick=ctx.tick,
        ))

        # ── Health degradation under resource stress ──
        if sv["hydration"] < PLANT_HEALTH_CRITICAL_HYDRATION:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="health",
                delta=-p.health_drain_dehydrated, tick=ctx.tick,
            ))
        if sv["nutrient_store"] < PLANT_HEALTH_CRITICAL_NUTRIENTS:
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="health",
                delta=-p.health_drain_nutrient, tick=ctx.tick,
            ))

        # ── Tree collapse pressure ──
        if p.canopy_radius and p.canopy_radius > 0:
            support_count = self._count_support(ctx)
            if support_count <= COLLAPSE_SUPPORT_THRESHOLD:
                effects.append(StateVarDelta(
                    entity_id=ctx.entity["id"], var_name="health",
                    delta=-p.health_drain_starving * COLLAPSE_HEALTH_MULTIPLIER,
                    tick=ctx.tick,
                ))
                effects.append(StateVarDelta(
                    entity_id=ctx.entity["id"], var_name="hydration",
                    delta=-p.health_drain_dehydrated * COLLAPSE_HYDRATION_MULTIPLIER,
                    tick=ctx.tick,
                ))

        # ── Age — always increments (dt-dependent) ──
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="age",
            delta=dt, tick=ctx.tick,
        ))

        # ── Vegetative spreading ──
        if p.spread_mode is not None:
            spread_effects = self._try_spread(ctx, sv, p, dt)
            effects.extend(spread_effects)

        return effects

    @staticmethod
    def _ensure_producer_vars(sv: dict[str, Any]) -> None:
        sv.setdefault("hydration", 1.0)
        sv.setdefault("growth", 0.0)
        sv.setdefault("health", 1.0)
        sv.setdefault("nutrient_store", 0.0)
        sv.setdefault("age", 0.0)

    @staticmethod
    def _count_support(ctx: Any) -> int:
        """Count non-structural, non-decomposer living entities.

        Uses ctx._get_params (callable provided by engine) to look up
        DerivedParams for each entity. Entities with canopy_radius > 0
        or diet_type == 'decomposer' are excluded from the count.
        """
        get_params = getattr(ctx, "_get_params", None)
        if get_params is None:
            return 0  # Can't determine support without param lookup
        count = 0
        for ent in ctx._entities.values():
            if not is_alive(ent) or ent["state"] == "DORMANT":
                continue
            ep = get_params(ent)
            if ep is None:
                continue
            if (getattr(ep, "canopy_radius", None) and ep.canopy_radius > 0):
                continue
            if getattr(ep, "diet_type", "") == "decomposer":
                continue
            count += 1
        return count

    def _try_spread(self, ctx: Any, sv: dict[str, Any], p: DerivedParams, dt: float) -> list[Any]:
        """Vegetative spreading — returns effects for new offspring.

        Matches engine inline behavior:
        - Parent must meet health/hydration/growth thresholds
        - Random chance gate using spread_chance × rate_reproduction
        - Density check: no other autotroph within SPREAD_DENSITY_RADIUS
        - Soil quality check at target position
        - Fixed parent cost (growth -0.1, nutrients -0.05)
        """
        import random as _random

        # Threshold checks — must meet all three
        if (sv.get("health", 1.0) < SPREAD_MIN_HEALTH
                or sv.get("hydration", 1.0) < SPREAD_MIN_HYDRATION
                or sv.get("growth", 0.1) < SPREAD_MIN_GROWTH):
            return []

        # Cooldown check
        cooldown = ctx.entity.get("_spread_cooldown", 0)
        if cooldown > 0:
            effects: list[Any] = [SetStateVar(
                entity_id=ctx.entity["id"], var_name="_spread_cooldown",
                value=float(cooldown - 1), tick=ctx.tick,
            )]
            return effects

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

        effects = [
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

        return effects


class DecomposerFlowActor:
    """Continuous flow for decomposer entities (fungi, microorganisms).

    Activity approaches an equilibrium set by local organic matter and
    moisture. Population grows when active, decays when dormant.
    """

    def resolve(self, ctx: Any) -> list[Any]:
        """Evaluate decomposer flow for one entity this tick.

        Args:
            ctx: InteractionContext with dt, params, voxel_grid, biome.

        Returns:
            List of Effect objects describing state changes.
        """
        p = ctx.params
        if p is None:
            return []

        sv = ctx.entity["state_vars"]
        self._ensure_decomposer_vars(sv)
        dt = ctx.dt
        effects: list[Any] = []

        gx, gy, gz = ctx.voxel_grid.world_to_grid(*ctx.entity["position"])
        organic = ctx.voxel_grid.get("organic_matter", gx, gy, gz)
        moisture = ctx.voxel_grid.get("moisture", gx, gy, gz)

        # Activity approaches equilibrium (exponential smoothing)
        optimal_activity = min(organic, moisture) * ctx.biome.microbial_activity_modifier
        activity_delta = (optimal_activity - sv["activity"]) * 0.1 * dt
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="activity",
            delta=activity_delta, tick=ctx.tick,
        ))

        # Population dynamics
        if sv["activity"] > 0.3:
            pop_growth = 0.005 * sv["activity"] * dt
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="population",
                delta=pop_growth, tick=ctx.tick,
            ))
        else:
            pop_decay = -0.003 * dt
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="population",
                delta=pop_decay, tick=ctx.tick,
            ))

        return effects

    @staticmethod
    def _ensure_decomposer_vars(sv: dict[str, Any]) -> None:
        sv.setdefault("activity", 0.5)
        sv.setdefault("population", 0.5)
