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

from typing import TYPE_CHECKING

from .constants import (
    SOIL_MOISTURE_FLOOR,
    WATER_SOURCE_MOISTURE_TARGET,
)
from .effects import (
    Effect,
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

        dims = ctx.voxel_grid.dimensions
        for x in range(dims[0]):
            for z in range(dims[2]):
                current = ctx.voxel_grid.get("moisture", x, 0, z)
                if current > SOIL_MOISTURE_FLOOR:
                    ctx.voxel_grid.set(
                        "moisture", x, 0, z,
                        max(SOIL_MOISTURE_FLOOR, current - evap),
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

            # Update soil moisture in water footprint
            cx, _, cz = source["position"]
            max_r = source["max_radius"]
            eff_r = source["radius"]
            for ix in range(int(cx - max_r), int(cx + max_r) + 1):
                for iz in range(int(cz - max_r), int(cz + max_r) + 1):
                    dist_sq = (ix - cx) ** 2 + (iz - cz) ** 2
                    gx, gy, gz = ctx.voxel_grid.world_to_grid(
                        float(ix), 0.0, float(iz))
                    if dist_sq <= eff_r * eff_r:
                        target = WATER_SOURCE_MOISTURE_TARGET * source["water_level"]
                        current = ctx.voxel_grid.get("moisture", gx, gy, gz)
                        if current < target:
                            ctx.voxel_grid.set(
                                "moisture", gx, gy, gz,
                                min(target, current + replenish_effect.soil_refill_rate),
                            )
                    elif dist_sq <= max_r * max_r:
                        current = ctx.voxel_grid.get("moisture", gx, gy, gz)
                        if current > 0.3:
                            ctx.voxel_grid.set(
                                "moisture", gx, gy, gz,
                                max(0.3, current - replenish_effect.soil_dry_rate),
                            )


# ═══════════════════════════════════════════════════════════════════════════════
# Soil Drain Handler (entity → soil nutrient/moisture uptake)
# ═══════════════════════════════════════════════════════════════════════════════

class SoilDrainHandler(WorldProcessHandler):
    """Handles entity-driven soil drain (nutrient/moisture uptake).

    Autotrophs drain nutrients and moisture from the cell at their position.
    With multi-resolution grids (#64), this handler will use query_overlap()
    to distribute drain across all cells under an entity's footprint.
    """

    handles = (SoilDrain,)
    period: int = 1

    def resolve(self, effect: Effect, ctx: WorldProcessContext) -> None:
        drain_effect = effect
        if not isinstance(drain_effect, SoilDrain):
            return

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
    biomass as organic matter. With multi-resolution grids (#64), this handler
    will use query_overlap() for footprint-aware deposits.
    """

    handles = (SoilDeposit,)
    period: int = 1

    def resolve(self, effect: Effect, ctx: WorldProcessContext) -> None:
        deposit_effect = effect
        if not isinstance(deposit_effect, SoilDeposit):
            return

        gx, gy, gz = ctx.voxel_grid.world_to_grid(
            *deposit_effect.position)
        ctx.voxel_grid.add(
            deposit_effect.layer, gx, gy, gz, deposit_effect.amount)


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
