# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā World-Process Handlers

Extracted from EcosystemEngine inline methods into pluggable handlers that
run at their own frequencies via the EffectBus world-process dispatch.

Each handler implements ``WorldProcessHandler`` and declares which effect
types it consumes. The engine emits intents (effects) and the bus routes
them to the appropriate handlers.

See Also:
- ``effects.py`` — WorldProcessHandler protocol, WorldProcessContext, effect types
- ``engine.py`` — Phase 4/5 emission of world-process effects
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .constants import (
    DISSOLUTION_RATE,
    MINERALIZATION_RATE,
    NUTRIENT_LEACH_RATE,
    OM_DEPOSIT_MAX,
    OM_DEPOSIT_MIN,
    OM_DEPOSIT_SCALE,
    SOIL_MOISTURE_FLOOR,
    WATER_SOURCE_MOISTURE_TARGET,
)
from .effects import (
    Effect,
    NutrientPoolDynamics,
    SoilDeposit,
    SoilDrain,
    SoilEvaporation,
    WaterReplenish,
    WorldProcessContext,
    WorldProcessHandler,
)

if TYPE_CHECKING:
    from .effects import EffectBus


# ═══════════════════════════════════════════════════════════════════════════════
# Soil Evaporation Handler
# ═══════════════════════════════════════════════════════════════════════════════

class SoilEvaporationHandler(WorldProcessHandler):
    """Handles soil moisture evaporation.

    Runs every tick (period=1). Can be tuned to lower frequency once
    validated — natural cadence is ~2 Hz at 10 Hz tick rate (period=5).
    """

    handles = (SoilEvaporation,)
    period: int = 1

    def resolve(self, effect: Effect, ctx: WorldProcessContext) -> None:
        evap_effect = effect
        if not isinstance(evap_effect, SoilEvaporation):
            return

        if evap_effect.rain_suppressed:
            return

        evap = evap_effect.evap_rate  # pre-computed by engine (includes dt)

        ctx.voxel_grid.walk_layer(
            "moisture",
            lambda x, y, z, val: (
                ctx.voxel_grid.set("moisture", x, y, z,
                                   max(SOIL_MOISTURE_FLOOR, val - evap))
                if val > SOIL_MOISTURE_FLOOR
                else None),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Water Replenishment Handler
# ═══════════════════════════════════════════════════════════════════════════════

class WaterReplenishHandler(WorldProcessHandler):
    """Handles water source level updates and soil moisture footprints.

    Runs every tick (period=1). Natural cadence is ~1 Hz at 10 Hz tick rate
    (period=10), but starts at period=1 for behavior-preserving migration.
    """

    handles = (WaterReplenish,)
    period: int = 1

    def resolve(self, effect: Effect, ctx: WorldProcessContext) -> None:
        replenish_effect = effect
        if not isinstance(replenish_effect, WaterReplenish):
            return

        for source in replenish_effect.sources:
            source["water_level"] = max(0.0, min(1.0,
                source["water_level"] - replenish_effect.evap_loss
                + replenish_effect.replenish_gain))
            source["radius"] = source["max_radius"] * source["water_level"]

            # Update soil moisture in water footprint using query_overlap.
            cx, _, cz = source["position"]
            eff_r = source["radius"]
            max_r = source["max_radius"]
            target = WATER_SOURCE_MOISTURE_TARGET * source["water_level"]

            # Cells within effective radius: refill toward target moisture.
            for gx, gy, gz in ctx.voxel_grid.query_overlap(
                (cx, 0.0, cz), eff_r):
                current = ctx.voxel_grid.get("moisture", gx, gy, gz)
                if current < target:
                    ctx.voxel_grid.set(
                        "moisture", gx, gy, gz,
                        min(target, current + replenish_effect.soil_refill_rate),
                    )

            # Cells between effective and max radius: dry toward floor.
            for gx, gy, gz in ctx.voxel_grid.query_overlap(
                (cx, 0.0, cz), max_r):
                # Skip cells already handled by the inner footprint.
                dist_sq = ((gx + 0.5) * ctx.voxel_grid.cell_size - cx) ** 2 + \
                          ((gz + 0.5) * ctx.voxel_grid.cell_size - cz) ** 2
                if dist_sq <= eff_r * eff_r:
                    continue
                current = ctx.voxel_grid.get("moisture", gx, gy, gz)
                floor_val = ctx.biome.soil_moisture_floor_outside_water
                if current > floor_val:
                    ctx.voxel_grid.set(
                        "moisture", gx, gy, gz,
                        max(floor_val, current - replenish_effect.soil_dry_rate),
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# Soil Drain Handler (entity → soil nutrient/moisture uptake)
# ═══════════════════════════════════════════════════════════════════════════════

class SoilDrainHandler(WorldProcessHandler):
    """Handles entity-driven soil drain (nutrient/moisture uptake).

    Autotrophs drain nutrients and moisture from cells under their footprint.
    When *radius* is set on the effect, drain is distributed evenly across
    all overlapping cells via ``query_overlap()``.  Without a radius,
    falls back to single-cell behavior for backward compatibility.
    """

    handles = (SoilDrain,)
    period: int = 1

    def resolve(self, effect: Effect, ctx: WorldProcessContext) -> None:
        drain_effect = effect
        if not isinstance(drain_effect, SoilDrain):
            return

        if drain_effect.radius is not None and drain_effect.radius > 0:
            cells = ctx.voxel_grid.query_overlap(
                tuple(drain_effect.position), drain_effect.radius)
            per_cell = drain_effect.amount / max(1, len(cells))
            for gx, gy, gz in cells:
                ctx.voxel_grid.add(drain_effect.layer, gx, gy, gz, per_cell)
        else:
            gx, gy, gz = ctx.voxel_grid.world_to_grid(
                *drain_effect.position)
            ctx.voxel_grid.add(
                drain_effect.layer, gx, gy, gz, drain_effect.amount)


# ═══════════════════════════════════════════════════════════════════════════════
# Soil Deposit Handler (decomposer OM → nutrient conversion + death deposits)
# ═══════════════════════════════════════════════════════════════════════════════

class SoilDepositHandler(WorldProcessHandler):
    """Handles entity-driven soil deposit (organic matter, decomposition).

    Decomposers convert organic matter into nutrients. Entity deaths deposit
    biomass as organic matter. When *radius* is set on the effect, deposit
    is distributed evenly across all overlapping cells via ``query_overlap()``.
    Without a radius, falls back to single-cell behavior for backward compatibility.
    """

    handles = (SoilDeposit,)
    period: int = 1

    def resolve(self, effect: Effect, ctx: WorldProcessContext) -> None:
        deposit_effect = effect
        if not isinstance(deposit_effect, SoilDeposit):
            return

        if deposit_effect.radius is not None and deposit_effect.radius > 0:
            cells = ctx.voxel_grid.query_overlap(
                tuple(deposit_effect.position), deposit_effect.radius)
            per_cell = deposit_effect.amount / max(1, len(cells))
            for gx, gy, gz in cells:
                ctx.voxel_grid.add(deposit_effect.layer, gx, gy, gz, per_cell)
        else:
            gx, gy, gz = ctx.voxel_grid.world_to_grid(
                *deposit_effect.position)
            ctx.voxel_grid.add(
                deposit_effect.layer, gx, gy, gz, deposit_effect.amount)


# ═══════════════════════════════════════════════════════════════════════════════
# Nutrient Pool Dynamics Handler (two-pool nutrient model)
# ═══════════════════════════════════════════════════════════════════════════════

class NutrientPoolDynamicsHandler(WorldProcessHandler):
    """Handles per-tick two-pool nutrient fluxes.

    Runs every tick (period=1). Three processes:
    1. Mineralization: organic_matter → nutrients_slow
    2. Dissolution: nutrients_slow → nutrients_fast
    3. Leaching: nutrients_fast drains slowly

    Rate multipliers from context scale each process independently.
    """

    handles = (NutrientPoolDynamics,)
    period: int = 1

    def resolve(self, effect: Effect, ctx: WorldProcessContext) -> None:
        pool_effect = effect
        if not isinstance(pool_effect, NutrientPoolDynamics):
            return

        vg = ctx.voxel_grid
        rates = ctx.rate_multipliers
        mineral_mult = rates.get("mineralization", 1.0)
        dissolution_mult = rates.get("dissolution", 1.0)
        leach_mult = rates.get("nutrient_leaching", 1.0)
        dt = pool_effect.dt

        # Process each layer independently to avoid reading DEFAULT_VALUE
        # for layers that don't have the cell explicitly set.
        # walk_layer only visits cells in _data[layer], so we process per-layer.

        # Track which cells we've already handled this tick (a cell may appear
        # in multiple layers — we need to apply all three fluxes together).
        processed: dict[tuple[int, int, int], dict[str, float]] = {}

        def _gather(layer: str) -> None:
            vg.walk_layer(
                layer,
                lambda x, y, z, v: (
                    processed.setdefault((x, y, z), {})
                ).__setitem__(layer, v),
            )

        _gather("organic_matter")
        _gather("nutrients_slow")
        _gather("nutrients_fast")

        for (gx, gy, gz), vals in processed.items():
            om = vals.get("organic_matter", 0.0)
            slow = vals.get("nutrients_slow", 0.0)
            fast = vals.get("nutrients_fast", 0.0)

            # 1. Mineralization: organic_matter → nutrients_slow
            mineralized = om * MINERALIZATION_RATE * mineral_mult * dt
            if mineralized > 0:
                om -= mineralized
                slow += mineralized

            # 2. Dissolution: nutrients_slow → nutrients_fast
            dissolved = slow * DISSOLUTION_RATE * dissolution_mult * dt
            if dissolved > 0:
                slow -= dissolved
                fast += dissolved

            # 3. Leaching: nutrients_fast drains
            leached = fast * NUTRIENT_LEACH_RATE * leach_mult * dt
            if leached > 0:
                fast -= leached

            # Clamp and write back.
            vg.set("organic_matter", gx, gy, gz, max(0.0, min(1.0, om)))
            vg.set("nutrients_slow", gx, gy, gz, max(0.0, min(1.0, slow)))
            vg.set("nutrients_fast", gx, gy, gz, max(0.0, min(1.0, fast)))

def deposit_organic_matter(
    entity: dict,
    params: dict | None,
    voxel_grid: Any,  # VoxelManager — avoid circular import
) -> None:
    """Deposit entity biomass into the organic matter voxel layer on death.

    Args:
        entity: Entity dict with position and metadata.
        params: DerivedParams or dict with metabolic_rate (or None).
        voxel_grid: VoxelManager for grid operations.
    """
    gx, gy, gz = voxel_grid.world_to_grid(*entity["position"])
    if params is not None:
        mr = getattr(params, "metabolic_rate", None) or (
            params.get("metabolic_rate") if isinstance(params, dict) else None
        )
        if mr is not None:
            deposit = min(OM_DEPOSIT_MAX, mr * OM_DEPOSIT_SCALE)
            deposit = max(deposit, OM_DEPOSIT_MIN)
        else:
            mass = entity.get("metadata", {}).get("body_mass", 10.0)
            deposit = min(0.3, mass / 500.0)
    else:
        mass = entity.get("metadata", {}).get("body_mass", 10.0)
        deposit = min(0.3, mass / 500.0)
    voxel_grid.add("organic_matter", gx, gy, gz, deposit)


# ═══════════════════════════════════════════════════════════════════════════════
# Registration Helper
# ═══════════════════════════════════════════════════════════════════════════════

def register_default_world_handlers(bus: EffectBus) -> None:  # noqa: F821
    """Register all default world-process handlers on an EffectBus.

    Call this from EcosystemEngine.__init__ after creating the bus.
    """
    bus.register_world_handler(SoilEvaporationHandler())
    bus.register_world_handler(WaterReplenishHandler())
    bus.register_world_handler(SoilDrainHandler())
    bus.register_world_handler(SoilDepositHandler())
    bus.register_world_handler(NutrientPoolDynamicsHandler())
