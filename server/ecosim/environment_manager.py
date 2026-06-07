# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Environment Manager — State and Logic for the World.

Encapsulates the physical environment of an ecosystem, including:
- Biome configuration (climate, growth rates, etc.)
- Climate state (temperature, humidity)
- Voxel grid (soil moisture, nutrients, organic matter)
- Water source management (replenishment, footprints)

This manager provides a unified interface for environmental updates like rain,
evaporation, and water distribution.
"""

from __future__ import annotations

from typing import Any

from .biome import BiomeConfig
from .constants import (
    RAIN_ANIMAL_HYDRATION,
    RAIN_MOISTURE_BOOST,
    RAIN_NUTRIENT_FAST_BOOST,
    RAIN_NUTRIENT_SLOW_BOOST,
    RAIN_PLANT_HEALTH,
    RAIN_PLANT_HYDRATION,
    RAIN_WATER_SOURCE_BOOST,
)
from .layout import LayoutManager, LayoutResult
from .voxel_manager import VoxelManager


class EnvironmentManager:
    """Manages the physical environment of a līlā ecosystem.

    Args:
        biome_name: The name of the biome (e.g., "TEMPERATE").
        climate: Initial climate parameters (temperature, humidity, etc.).
        voxel_grid_cfg: Configuration for the voxel grid (dimensions, cell_size).
        soil_cfg: Configuration for initial soil states.
    """

    def __init__(
        self,
        biome_name: str,
        climate: dict[str, float],
        voxel_grid_cfg: dict[str, Any],
        soil_cfg: dict[str, Any] | None = None,
    ) -> None:
        # ── Biome & Climate ──
        from .biome import get_biome_config
        self.biome_name: str = biome_name
        self.biome: BiomeConfig = get_biome_config(biome_name)
        self.climate: dict[str, float] = climate

        # ── Voxel Grid ──
        dims = tuple(voxel_grid_cfg.get("dimensions", [32, 32, 32]))
        cell = voxel_grid_cfg.get("cell_size", 1.0)
        self.voxels = VoxelManager(dimensions=dims, cell_size=cell)

        if soil_cfg:
            self.voxels.initialize_from_soil(soil_cfg)

        # ── Water Sources ──
        self.water_sources: list[dict] = []

    def load_layout(self, world_config: dict[str, Any]) -> LayoutResult:
        """Load entities and water sources from world config into this environment.

        Internally constructs a LayoutManager to parse the world JSON,
        applies optional randomization, seeds moisture footprints around
        water sources, and returns the result. The engine calls this once
        at init; it owns both entity placement and environment population.

        Args:
            world_config: World definition dict (from JSON). Must contain
                ``environment`` and optionally ``entities``.

        Returns:
            LayoutResult with initialized entities, water sources,
            and grid_max. Water sources are also stored on this manager.
        """
        layout = LayoutManager(world_config, self.voxels)
        result = layout.load()

        # Owner sets its own state — no back-mutation from the engine
        self.water_sources = result.water_sources

        # Apply optional randomization (mutates entities + water sources in-place)
        layout.randomize(result.entities, self.water_sources)

        # Seed moisture footprints after randomization (positions may have shifted)
        for source in self.water_sources:
            self._init_water_source_moisture(source)

        return LayoutResult(
            result.entities,
            self.water_sources,
            result.grid_max,
        )

    def add_water_source(self, source: dict) -> None:
        """Registers a water source and initializes its moisture footprint."""
        self.water_sources.append(source)
        self._init_water_source_moisture(source)

    def _init_water_source_moisture(self, source: dict) -> None:
        """Initialize soil moisture footprint around a water source.

        Uses ``query_overlap()`` to find all cells within the source radius
        and sets them to high initial moisture (0.95).
        """
        cx, _, cz = source["position"]
        r = source["radius"]
        for gx, gy, gz in self.voxels.query_overlap((cx, 0.0, cz), r):
            self.voxels.set("moisture", gx, gy, gz, 0.95)

    def apply_rain(
        self,
        intensity: float,
        entities: dict[str, dict],
        get_params_fn: Any | None,
        rate_multipliers: dict[str, float],
    ) -> dict[str, Any]:
        """Apply a rain event across the entire grid.

        Boosts soil moisture and nutrients, refills water sources,
        hydrates plants and animals, and suppresses evaporation.

        Args:
            intensity: Rain intensity (0.0–1.0).
            entities: Entity registry.
            get_params_fn: Callable(entity) → DerivedParams | None.
            rate_multipliers: Dictionary of global rate multipliers.

        Returns:
            A RainEvent record dict for the caller to log.
        """
        from .entities import is_alive  # avoid circular import at module level

        dims = self.voxels.dimensions

        # Soil moisture boost (walk_layer skips empty regions)
        self.voxels.walk_layer(
            "moisture",
            lambda x, y, z, val: self.voxels.set(
                "moisture", x, y, z,
                min(1.0, val + RAIN_MOISTURE_BOOST * intensity)),
        )

        # Soil nutrient boost — split between fast (immediately available)
        # and slow (long-term reserve) pools.
        self.voxels.walk_layer(
            "nutrients_fast",
            lambda x, y, z, val: self.voxels.set(
                "nutrients_fast", x, y, z,
                min(1.0, val + RAIN_NUTRIENT_FAST_BOOST * intensity)),
        )
        self.voxels.walk_layer(
            "nutrients_slow",
            lambda x, y, z, val: self.voxels.set(
                "nutrients_slow", x, y, z,
                min(1.0, val + RAIN_NUTRIENT_SLOW_BOOST * intensity)),
        )

        # Water source refill
        for source in self.water_sources:
            source["water_level"] = min(
                1.0, source["water_level"] + RAIN_WATER_SOURCE_BOOST * intensity)
            source["radius"] = source["max_radius"] * source["water_level"]

        # Direct entity hydration/health boost
        for ent in entities.values():
            if not is_alive(ent):
                continue
            sv = ent["state_vars"]
            params = get_params_fn(ent) if get_params_fn else None
            if params and getattr(params, "diet_type", "") == "autotroph":
                sv["hydration"] = min(1.0, sv["hydration"] + RAIN_PLANT_HYDRATION * intensity)
                sv["health"] = min(1.0, sv["health"] + RAIN_PLANT_HEALTH * intensity)
            elif params and getattr(params, "speed", 0) > 0:
                if "hydration" in sv:
                    # Scale boost inversely with current hydration: critically
                    # dehydrated animals get up to 2× the base, well-hydrated
                    # ones get only half. This prevents post-collapse death
                    # spirals where a single rain event can't save them.
                    current = sv["hydration"]
                    scale = max(0.5, min(2.0, 1.0 + (1.0 - current)))
                    sv["hydration"] = min(
                        1.0, current + RAIN_ANIMAL_HYDRATION * intensity * scale)

        return {
            "type": "RAIN",
            "intensity": intensity,
            "position": [dims[0] / 2, 0.0, dims[2] / 2],
        }
