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

from ..biome import BiomeConfig
from ..effects import (
    ClearTarget,
    EffectType,
    EventRecord,
    LingerEffect,
    SetStateVar,
    SpawnEntity,
    StateTransition,
    StateVarDelta,
    VoxelDelta,
)
from ..entities import is_alive
from ..traits import DerivedParams

# ── Consumer flow constants ────────────────────────────────────────────────
ACTIVE_ENERGY_DRAIN_STATES = {"FORAGING", "HUNTING", "FLEEING"}
ENERGY_RECOVERY_STATES = {"IDLE", "RESTING", "REPRODUCING", "SWARMING"}
ACTIVE_MOVEMENT_STATES = {"FORAGING", "HUNTING", "FLEEING", "DRINKING", "REPRODUCING"}

DRINK_RECOVERY_RATE = 0.15      # hydration gained per tick × local soil moisture
DRINK_SOIL_DRAIN = 0.01         # soil moisture removed per drink tick
DRINK_WATER_DRAIN = 0.02        # water source level removed per drink tick

WATER_PROXIMITY_HUNGER_FACTOR = 0.5   # hunger relief = hunger_rate × this
WATER_PROXIMITY_COLONY_FACTOR = 0.2   # colony_health recovery = energy_recovery × this

REPRO_BUILD_MIN_ENERGY = 0.5    # energy must exceed this to build drive
REPRO_BUILD_MAX_HUNGER = 0.6    # hunger below this to build drive
REPRO_BUILD_MIN_HEALTH = 0.3    # health above this to build drive
REPRO_DECAY_HUNGER = 0.7        # hunger above this → decay reproductive drive
REPRO_DECAY_ENERGY = 0.3        # energy below this → decay reproductive drive

STARVATION_HUNGER = 0.8         # hunger above this → health drain
DEHYDRATION_HYDRATION = 0.15    # hydration below this → health drain

COLONY_STRESS_HUNGER = 0.7      # colony_health starts draining under stress
COLONY_STRESS_ENERGY = 0.2      # colony_health starts draining under stress


# ── Producer flow constants ────────────────────────────────────────────────
PLANT_BASE_WATER_DEMAND = 0.03  # base water uptake rate from soil
PLANT_SOIL_UPTAKE_RATE = 0.1    # fraction of soil moisture available per tick
PLANT_BASE_GROWTH_RATE = 0.05   # base growth rate (× resource availability)

PLANT_HEALTH_CRITICAL_HYDRATION = 0.15  # below this, plant health degrades
PLANT_HEALTH_CRITICAL_NUTRIENTS = 0.1   # below this, plant health degrades

COLLAPSE_SUPPORT_THRESHOLD = 2
COLLAPSE_HEALTH_MULTIPLIER = 3.0
COLLAPSE_HYDRATION_MULTIPLIER = 2.0


# ── Plant spreading constants ──────────────────────────────────────────────
SPREAD_MIN_MOISTURE = 0.15
SPREAD_MIN_NUTRIENTS = 0.10
SPREAD_MAX_PARENT_COST = 0.3
SPREAD_DENSITY_THRESHOLD = 8
SPREAD_DISTANCE_TOLERANCE = 0.5


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
        WATER_DRY_THRESHOLD = 0.05
        for source in ctx.water_sources:
            if source.get("water_level", 1.0) < WATER_DRY_THRESHOLD:
                continue
            dx = pos[0] - source["position"][0]
            dz = pos[2] - source["position"][2]
            dist = math.sqrt(dx * dx + dz * dz)
            if dist <= source.get("radius", 1.0) + 1.0:
                return True
        return False


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
        """Count non-structural, non-decomposer living entities."""
        count = 0
        for ent in ctx._entities.values():
            if not is_alive(ent) or ent["state"] == "DORMANT":
                continue
            ep = getattr(ctx, "_get_params", lambda e: None)(ent)
            if ep is None:
                continue
            if ep.canopy_radius or ep.diet_type == "decomposer":
                continue
            count += 1
        return count

    def _try_spread(self, ctx: Any, sv: dict[str, Any], p: DerivedParams, dt: float) -> list[Any]:
        """Vegetative spreading — returns effects for new offspring."""
        if sv["growth"] < 0.3 or sv["health"] < 0.2:
            return []

        gx, gy, gz = ctx.voxel_grid.world_to_grid(*ctx.entity["position"])
        soil_moisture = ctx.voxel_grid.get("moisture", gx, gy, gz)
        soil_nutrients = ctx.voxel_grid.get("nutrients", gx, gy, gz)

        if soil_moisture < SPREAD_MIN_MOISTURE or soil_nutrients < SPREAD_MIN_NUTRIENTS:
            return []

        # Check density in spread range
        spread_range = p.spread_range or 2.0
        count_in_range = 0
        for ent in ctx._entities.values():
            if not is_alive(ent) or ent["state"] == "DORMANT":
                continue
            dx = ent["position"][0] - ctx.entity["position"][0]
            dz = ent["position"][2] - ctx.entity["position"][2]
            dist = math.sqrt(dx * dx + dz * dz)
            if dist <= spread_range + SPREAD_DISTANCE_TOLERANCE:
                count_in_range += 1

        if count_in_range >= SPREAD_DENSITY_THRESHOLD:
            return []

        # Parent resource cost
        parent_cost = min(sv["growth"] * 0.3, SPREAD_MAX_PARENT_COST)
        effects: list[Any] = [SetStateVar(
            entity_id=ctx.entity["id"], var_name="growth",
            value=max(0.0, sv["growth"] - parent_cost), tick=ctx.tick,
        )]

        # Create offspring at nearby position
        import random as _random
        angle = _random.uniform(0, 2 * math.pi)
        offset = spread_range * 0.5 * _random.uniform(0.5, 1.0)
        new_x = ctx.entity["position"][0] + math.cos(angle) * offset
        new_z = ctx.entity["position"][2] + math.sin(angle) * offset

        # Offspring inherits parent state with reduced growth/health
        offspring_sv = {
            "hydration": sv.get("hydration", 1.0),
            "growth": max(0.05, sv["growth"] - parent_cost) * 0.5,
            "health": sv.get("health", 1.0) * 0.8,
            "nutrient_store": sv.get("nutrient_store", 0.0),
            "age": 0.0,
        }

        effects.append(SpawnEntity(
            entity_id=f"{ctx.entity['species']}_spread_{ctx.tick}",
            type=ctx.entity["type"],
            species=ctx.entity.get("species"),
            position=[new_x, ctx.entity["position"][1], new_z],
            metadata=dict(ctx.entity["metadata"]),
            state_vars=offspring_sv,
        ))

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
        sv.setdefault("activity", 0.0)
        sv.setdefault("population", 1.0)
