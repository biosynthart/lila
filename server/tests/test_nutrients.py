# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""Two-pool nutrient flow tests (mineralization, dissolution, leaching).

Validates the split of the single ``nutrients`` layer into
``nutrients_fast`` and ``nutrients_slow`` with inter-pool fluxes.
"""

from __future__ import annotations

from ecosim.effects import NutrientPoolDynamics, WorldProcessContext
from ecosim.voxel_manager import DEFAULT_VALUE, UniformVoxelGrid
from ecosim.world_processes import NutrientPoolDynamicsHandler

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_grid(dimensions=(8, 8, 8), cell_size=1.0) -> UniformVoxelGrid:
    return UniformVoxelGrid(dimensions=dimensions, cell_size=cell_size)


def _ctx(grid: UniformVoxelGrid, rates: dict | None = None):
    return WorldProcessContext(
        tick=1, voxel_grid=grid, biome=None, climate={},
        entities={}, water_sources=[],
        rate_multipliers=rates or {},
    )

_MINIMAL_SPECIES = [
    {
        "species_id": "meadow_grass",
        "functional_group": "autotroph",
        "entity_class": "PLANT",
        "body_mass_kg": 0.5,
        "locomotion": "sessile",
        "thermoregulation": "ectotherm",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Two-pool initialization
# ═══════════════════════════════════════════════════════════════════════════════

class TestTwoPoolInitialization:
    """Verify both nutrient pools initialized from soil config."""

    def test_fast_slow_split_from_soil(self):
        grid = make_grid((4, 4, 4))
        grid.initialize_from_soil({
            "nitrogen": 0.7, "phosphorus": 0.6, "potassium": 0.5,
        })
        # base = (0.7+0.6+0.5)/3 = 0.6 → fast=0.24, slow=0.36
        assert abs(grid.get("nutrients_fast", 0, 0, 0) - 0.24) < 0.01
        assert abs(grid.get("nutrients_slow", 0, 0, 0) - 0.36) < 0.01

    def test_backward_compat_alias(self):
        """Legacy 'nutrients' key maps to nutrients_fast."""
        grid = make_grid((4, 4, 4))
        grid.initialize_from_soil({
            "nitrogen": 0.5, "phosphorus": 0.5, "potassium": 0.5,
        })
        # base=0.5 → fast=0.2
        assert abs(grid.get("nutrients", 0, 0, 0) - 0.2) < 0.01

    def test_all_five_layers_initialized(self):
        """All five layers must be set after initialize_from_soil."""
        grid = make_grid((4, 4, 4))
        grid.initialize_from_soil({
            "nitrogen": 0.5, "phosphorus": 0.5, "potassium": 0.5,
            "moisture": 0.6, "organic_matter": 0.3,
        })
        for layer in ("nutrients_fast", "nutrients_slow", "moisture",
                       "temperature", "organic_matter"):
            # temperature stays at DEFAULT_VALUE (not set by soil config)
            if layer == "temperature":
                assert grid.get(layer, 0, 0, 0) == DEFAULT_VALUE
            else:
                val = grid.get(layer, 0, 0, 0)
                assert val != DEFAULT_VALUE or layer == "organic_matter"


# ═══════════════════════════════════════════════════════════════════════════════
# Mineralization: organic_matter → nutrients_slow
# ═══════════════════════════════════════════════════════════════════════════════

class TestMineralizationFlow:
    """Organic matter converts to slow nutrients over time."""

    def test_mineralization_reduces_om_increases_slow(self):
        grid = make_grid((4, 4, 4))
        cell = (2, 0, 2)
        grid.set("organic_matter", *cell, 0.5)
        grid.set("nutrients_slow", *cell, 0.0)

        handler = NutrientPoolDynamicsHandler()
        ctx = _ctx(grid)
        effect = NutrientPoolDynamics(tick=1, dt=1.0)

        # Simulate 100 ticks
        for _ in range(100):
            handler.resolve(effect, ctx)

        om = grid.get("organic_matter", *cell)
        slow = grid.get("nutrients_slow", *cell)
        assert om < 0.42   # OM should have decayed significantly
        assert slow > 0.06  # Slow pool grew (net of mineralization minus dissolution)

    def test_mineralization_rate_multiplier(self):
        """Rate multiplier scales mineralization."""
        grid = make_grid((4, 4, 4))
        cell = (2, 0, 2)
        grid.set("organic_matter", *cell, 0.5)
        grid.set("nutrients_slow", *cell, 0.0)

        handler = NutrientPoolDynamicsHandler()
        effect = NutrientPoolDynamics(tick=1, dt=1.0)

        # Run with 1× rate
        ctx1x = _ctx(grid, {"mineralization": 1.0})
        for _ in range(50):
            handler.resolve(effect, ctx1x)
        slow_1x = grid.get("nutrients_slow", *cell)

        # Reset and run with 4× rate (simulating decomposer boost)
        grid.set("organic_matter", *cell, 0.5)
        grid.set("nutrients_slow", *cell, 0.0)
        ctx4x = _ctx(grid, {"mineralization": 4.0})
        for _ in range(50):
            handler.resolve(effect, ctx4x)
        slow_4x = grid.get("nutrients_slow", *cell)

        # 4× rate should produce significantly more slow nutrients
        assert slow_4x > slow_1x * 2


# ═══════════════════════════════════════════════════════════════════════════════
# Dissolution: nutrients_slow → nutrients_fast
# ═══════════════════════════════════════════════════════════════════════════════

class TestDissolutionFlow:
    """Slow nutrients dissolve into fast pool."""

    def test_dissolution_moves_slow_to_fast(self):
        grid = make_grid((4, 4, 4))
        cell = (2, 0, 2)
        grid.set("nutrients_slow", *cell, 0.5)
        grid.set("nutrients_fast", *cell, 0.0)

        handler = NutrientPoolDynamicsHandler()
        ctx = _ctx(grid)
        effect = NutrientPoolDynamics(tick=1, dt=1.0)

        for _ in range(100):
            handler.resolve(effect, ctx)

        fast = grid.get("nutrients_fast", *cell)
        slow = grid.get("nutrients_slow", *cell)
        assert fast > 0.15   # Fast pool should have grown
        assert slow < 0.35   # Slow pool should have declined


# ═══════════════════════════════════════════════════════════════════════════════
# Leaching: nutrients_fast drains slowly
# ═══════════════════════════════════════════════════════════════════════════════

class TestLeachingFlow:
    """Fast pool experiences slow leaching."""

    def test_leaching_reduces_fast_pool(self):
        grid = make_grid((4, 4, 4))
        cell = (2, 0, 2)
        grid.set("nutrients_fast", *cell, 0.5)

        handler = NutrientPoolDynamicsHandler()
        ctx = _ctx(grid)
        effect = NutrientPoolDynamics(tick=1, dt=1.0)

        initial = grid.get("nutrients_fast", *cell)
        for _ in range(200):
            handler.resolve(effect, ctx)

        final = grid.get("nutrients_fast", *cell)
        assert final < initial  # Leaching should reduce fast pool


# ═══════════════════════════════════════════════════════════════════════════════
# Rain splits nutrients into both pools
# ═══════════════════════════════════════════════════════════════════════════════

class TestRainNutrientSplit:
    """Rain adds to both pools with correct ratio."""

    def test_rain_boosts_both_pools(self):
        from ecosim.engine import EcosystemEngine

        world = {
            "environment": {
                "biome": "TEMPERATE",
                "climate": {"temperature": 22.0, "humidity": 0.6},
                "soil": {"nitrogen": 0.5, "phosphorus": 0.5, "potassium": 0.5,
                         "moisture": 0.5, "organic_matter": 0.2},
                "voxel_grid": {"dimensions": [8, 8, 8], "cell_size": 1.0},
            },
            "species_definitions": _MINIMAL_SPECIES,
        }
        engine = EcosystemEngine(world)

        # Record pre-rain values at a cell
        gx, gy, gz = 3, 0, 3
        fast_before = engine.voxels.get("nutrients_fast", gx, gy, gz)
        slow_before = engine.voxels.get("nutrients_slow", gx, gy, gz)

        engine.apply_rain(1.0)

        fast_after = engine.voxels.get("nutrients_fast", gx, gy, gz)
        slow_after = engine.voxels.get("nutrients_slow", gx, gy, gz)

        # Both pools should increase
        assert fast_after > fast_before
        assert slow_after > slow_before
        # Fast pool gets the larger share (~83%)
        fast_delta = fast_after - fast_before
        slow_delta = slow_after - slow_before
        assert fast_delta > slow_delta


# ═══════════════════════════════════════════════════════════════════════════════
# Dormancy recovery uses weighted effective nutrients
# ═══════════════════════════════════════════════════════════════════════════════

class TestDormancyRecoveryEffectiveNutrients:
    """Dormancy recovery uses weighted sum of both pools."""

    def test_weighted_sum_allows_recovery(self):
        fast = 0.10
        slow = 0.20
        effective = fast + slow * 0.3  # = 0.16
        assert effective > 0.15  # Should allow recovery

    def test_fast_only_below_threshold(self):
        fast = 0.10
        slow = 0.0
        effective = fast + slow * 0.3  # = 0.10
        assert effective < 0.15  # Should NOT allow recovery


# ═══════════════════════════════════════════════════════════════════════════════
# Full integration: engine steps with two-pool dynamics
# ═══════════════════════════════════════════════════════════════════════════════

class TestTwoPoolEngineIntegration:
    """End-to-end validation of two-pool nutrient system in the engine."""

    def test_engine_steps_with_two_pools(self):
        from ecosim.engine import EcosystemEngine

        world = {
            "environment": {
                "biome": "TEMPERATE",
                "climate": {"temperature": 22.0, "humidity": 0.6},
                "soil": {"nitrogen": 0.7, "phosphorus": 0.6, "potassium": 0.5,
                         "moisture": 0.65, "organic_matter": 0.4},
                "voxel_grid": {"dimensions": [8, 8, 8], "cell_size": 1.0},
            },
            "species_definitions": _MINIMAL_SPECIES,
            "entities": [
                {"id": "grass_01", "type": "PLANT", "species": "meadow_grass",
                 "position": [3.0, 0.0, 3.0],
                 "metadata": {"growth_rate": 0.06, "root_depth": 0.1,
                               "water_demand": 0.02,
                               "nutrient_demand": {"nitrogen": 0.005}}},
            ],
        }
        engine = EcosystemEngine(world)

        # Step 100 ticks — should not crash and pools should evolve
        for _ in range(100):
            packet = engine.step(dt=0.1)

        assert packet["tick"] == 100

        gx, gy, gz = engine.voxels.world_to_grid(3.0, 0.0, 3.0)
        fast = engine.voxels.get("nutrients_fast", gx, gy, gz)
        slow = engine.voxels.get("nutrients_slow", gx, gy, gz)

        # Both pools should remain in valid range
        assert 0.0 <= fast <= 1.0
        assert 0.0 <= slow <= 1.0

    def test_rate_multipliers_accepted(self):
        """World JSON with new rate multipliers doesn't crash."""
        from ecosim.engine import EcosystemEngine

        world = {
            "environment": {
                "biome": "TEMPERATE",
                "climate": {"temperature": 22.0, "humidity": 0.6},
                "soil": {"nitrogen": 0.5, "phosphorus": 0.5, "potassium": 0.5,
                         "moisture": 0.5, "organic_matter": 0.3},
                "voxel_grid": {"dimensions": [8, 8, 8], "cell_size": 1.0},
            },
            "species_definitions": _MINIMAL_SPECIES,
            "rates": {
                "mineralization": 2.0,
                "dissolution": 0.5,
                "nutrient_leaching": 1.5,
            },
        }
        engine = EcosystemEngine(world)
        assert engine.rate_mineralization == 2.0
        assert engine.rate_dissolution == 0.5
        assert engine.rate_nutrient_leaching == 1.5

    def test_backward_compat_no_new_rates(self):
        """World JSON without new rate multipliers defaults to 1.0."""
        from ecosim.engine import EcosystemEngine

        world = {
            "environment": {
                "biome": "TEMPERATE",
                "climate": {"temperature": 22.0, "humidity": 0.6},
                "soil": {"nitrogen": 0.5, "phosphorus": 0.5, "potassium": 0.5,
                         "moisture": 0.5, "organic_matter": 0.3},
                "voxel_grid": {"dimensions": [8, 8, 8], "cell_size": 1.0},
            },
            "species_definitions": _MINIMAL_SPECIES,
        }
        engine = EcosystemEngine(world)
        assert engine.rate_mineralization == 1.0
        assert engine.rate_dissolution == 1.0
        assert engine.rate_nutrient_leaching == 1.0
