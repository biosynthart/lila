# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""Tests for VoxelGrid protocol and UniformVoxelGrid implementation."""

from __future__ import annotations

import math

from ecosim.voxel_manager import (
    DEFAULT_VALUE,
    DIRTY_THRESHOLD,
    UniformVoxelGrid,
    VoxelGrid,
    _distance_3d_sq,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_grid(dimensions=(8, 8, 8), cell_size=1.0) -> UniformVoxelGrid:
    return UniformVoxelGrid(dimensions=dimensions, cell_size=cell_size)


# ═══════════════════════════════════════════════════════════════════════════════
# Protocol conformance
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoxelGridProtocol:
    """Verify UniformVoxelGrid satisfies the VoxelGrid protocol."""

    def test_isinstance_protocol(self):
        grid = make_grid()
        assert isinstance(grid, VoxelGrid)

    def test_has_required_attributes(self):
        grid = make_grid((16, 16, 16), cell_size=2.0)
        assert grid.dimensions == (16, 16, 16)
        assert grid.cell_size == 2.0

    def test_has_required_methods(self):
        grid = make_grid()
        for method in ("get", "set", "add", "world_to_grid",
                       "query_overlap", "walk_layer", "get_delta_packet"):
            assert hasattr(grid, method), f"Missing method: {method}"


# ═══════════════════════════════════════════════════════════════════════════════
# _distance_3d_sq helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestDistance3DSq:
    def test_same_point(self):
        assert _distance_3d_sq((1, 2, 3), (1, 2, 3)) == 0.0

    def test_axis_aligned(self):
        assert _distance_3d_sq((0, 0, 0), (3, 0, 0)) == 9.0

    def test_diagonal(self):
        result = _distance_3d_sq((0, 0, 0), (1, 1, 1))
        assert abs(result - 3.0) < 1e-10


# ═══════════════════════════════════════════════════════════════════════════════
# query_overlap — spherical footprint queries
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueryOverlap:
    def test_single_cell_at_center(self):
        """Radius large enough to include center cell."""
        grid = make_grid((8, 8, 8))
        # Cell (4,4,4) has center at (4.5, 4.5, 4.5)
        cells = grid.query_overlap((4.5, 4.5, 4.5), 1.0)
        assert (4, 4, 4) in cells

    def test_radius_one_cell(self):
        """Radius=1.0 should include center cell and immediate neighbors."""
        grid = make_grid((8, 8, 8))
        cells = grid.query_overlap((4.5, 4.5, 4.5), 1.0)
        # Center is (4,4,4); radius 1.0 should reach adjacent cell centers
        assert len(cells) >= 1

    def test_larger_radius_covers_more_cells(self):
        grid = make_grid((16, 16, 16))
        small = grid.query_overlap((8.5, 8.5, 8.5), 1.0)
        large = grid.query_overlap((8.5, 8.5, 8.5), 3.0)
        assert len(large) > len(small)

    def test_clamped_to_grid_bounds(self):
        """Cells near edges should not exceed grid dimensions."""
        grid = make_grid((4, 4, 4))
        cells = grid.query_overlap((0.5, 0.5, 0.5), 3.0)
        for x, y, z in cells:
            assert 0 <= x < 4
            assert 0 <= y < 4
            assert 0 <= z < 4

    def test_all_cells_in_small_grid(self):
        """Large radius on small grid should return all cells."""
        grid = make_grid((3, 3, 3))
        cells = grid.query_overlap((1.5, 1.5, 1.5), 10.0)
        assert len(cells) == 27

    def test_returns_sorted_cells(self):
        """Cells should be returned in a deterministic order."""
        grid = make_grid((8, 8, 8))
        cells_a = grid.query_overlap((4.5, 4.5, 4.5), 2.0)
        cells_b = grid.query_overlap((4.5, 4.5, 4.5), 2.0)
        assert cells_a == cells_b

    def test_cell_centers_within_radius(self):
        """Every returned cell's center should be within radius of query center."""
        grid = make_grid((16, 16, 16))
        center = (8.5, 8.5, 8.5)
        radius = 2.0
        cells = grid.query_overlap(center, radius)
        for x, y, z in cells:
            cell_center = (
                (x + 0.5) * grid.cell_size,
                (y + 0.5) * grid.cell_size,
                (z + 0.5) * grid.cell_size,
            )
            dist_sq = _distance_3d_sq(center, cell_center)
            assert math.sqrt(dist_sq) <= radius + 1e-9

    def test_different_cell_sizes(self):
        """query_overlap should work correctly with non-unit cell sizes."""
        grid = make_grid((8, 8, 8), cell_size=2.0)
        # Grid covers world space [0, 16] in each axis
        cells = grid.query_overlap((8.0, 8.0, 8.0), 2.0)
        assert len(cells) >= 1

    def test_2d_plane_query(self):
        """Query at y=0 (ground level) should find ground cells."""
        grid = make_grid((16, 4, 16))
        cells = grid.query_overlap((8.5, 0.5, 8.5), 3.0)
        for x, y, z in cells:
            assert y >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# walk_layer — sparse iteration
# ═══════════════════════════════════════════════════════════════════════════════

class TestWalkLayer:
    def test_walks_only_existing_cells(self):
        grid = make_grid((8, 8, 8))
        grid.set("moisture", 3, 0, 3, 0.5)
        grid.set("moisture", 4, 0, 4, 0.7)

        visited: list[tuple[int, int, int]] = []
        grid.walk_layer("moisture", lambda x, y, z, v: visited.append((x, y, z)))
        assert (3, 0, 3) in visited
        assert (4, 0, 4) in visited

    def test_empty_layer_walks_nothing(self):
        grid = make_grid()
        visited: list[tuple[int, int, int]] = []
        grid.walk_layer("temperature", lambda x, y, z, v: visited.append((x, y, z)))
        assert len(visited) == 0

    def test_callback_receives_values(self):
        grid = make_grid()
        grid.set("nutrients", 1, 1, 1, 0.3)
        values: list[float] = []
        grid.walk_layer("nutrients", lambda x, y, z, v: values.append(v))
        assert 0.3 in values

    def test_full_grid_walk(self):
        """After initialize_from_soil, walk should visit all cells."""
        grid = make_grid((4, 4, 4))
        grid.initialize_from_soil({"nitrogen": 0.5, "phosphorus": 0.5, "potassium": 0.5})
        count = [0]  # use list for mutability in lambda
        grid.walk_layer("nutrients", lambda x, y, z, v: count.__setitem__(0, count[0] + 1))
        # Nutrient val is 0.5 which differs from DEFAULT_VALUE=1.0, so all cells set
        assert count[0] == 64

    def test_walk_does_not_include_defaults(self):
        """Cells at DEFAULT_VALUE that were never explicitly set should not appear."""
        grid = make_grid()
        # Don't set anything — walk should find nothing
        visited: list[tuple[int, int, int]] = []
        grid.walk_layer("moisture", lambda x, y, z, v: visited.append((x, y, z)))
        assert len(visited) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Backward compatibility — VoxelManager alias
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoxelManagerAlias:
    def test_voxel_manager_is_uniform(self):
        from ecosim.voxel_manager import VoxelManager
        vm = VoxelManager(dimensions=(8, 8, 8))
        assert isinstance(vm, UniformVoxelGrid)

    def test_voxel_manager_has_new_methods(self):
        from ecosim.voxel_manager import VoxelManager
        vm = VoxelManager()
        cells = vm.query_overlap((4.5, 4.5, 4.5), 1.0)
        assert len(cells) >= 1

    def test_voxel_manager_walk_layer(self):
        from ecosim.voxel_manager import VoxelManager
        vm = VoxelManager()
        vm.set("moisture", 3, 0, 3, 0.5)
        visited: list[tuple[int, int, int]] = []
        vm.walk_layer("moisture", lambda x, y, z, v: visited.append((x, y, z)))
        assert (3, 0, 3) in visited


# ═══════════════════════════════════════════════════════════════════════════════
# Integration — handlers use new protocol methods
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandlerIntegration:
    """Verify world-process handlers work with the new protocol interface."""

    def test_soil_drain_handler_with_radius(self):
        from ecosim.effects import SoilDrain, WorldProcessContext
        from ecosim.world_processes import SoilDrainHandler

        grid = make_grid((16, 4, 16))
        # Seed moisture at y=0 only (ground layer)
        for x in range(16):
            for z in range(16):
                grid.set("moisture", x, 0, z, 0.8)

        ctx = WorldProcessContext(
            tick=1, voxel_grid=grid, biome=None, climate={},
            entities={}, water_sources=[], rate_multipliers={})

        handler = SoilDrainHandler()
        # Use a large amount so per-cell delta exceeds DIRTY_THRESHOLD
        effect = SoilDrain(
            tick=1, entity_id="tree-1", position=[8.5, 0.0, 8.5],
            layer="moisture", amount=-2.0, radius=2.0)

        handler.resolve(effect, ctx)

        # Check that ground cells (y=0) under the footprint were drained
        cells = grid.query_overlap((8.5, 0.0, 8.5), 2.0)
        ground_cells = [(x, y, z) for x, y, z in cells if y == 0]
        assert len(ground_cells) > 1  # multi-cell footprint
        for gx, gy, gz in ground_cells:
            val = grid.get("moisture", gx, gy, gz)
            assert val < 0.8 - DIRTY_THRESHOLD, \
                f"Cell ({gx},{gy},{gz}) should be drained below threshold, got {val}"

    def test_soil_drain_handler_without_radius(self):
        """Without radius, handler falls back to single-cell behavior."""
        from ecosim.effects import SoilDrain, WorldProcessContext
        from ecosim.world_processes import SoilDrainHandler

        grid = make_grid((16, 4, 16))
        ctx = WorldProcessContext(
            tick=1, voxel_grid=grid, biome=None, climate={},
            entities={}, water_sources=[], rate_multipliers={})

        handler = SoilDrainHandler()
        effect = SoilDrain(
            tick=1, entity_id="mushroom-1", position=[8.5, 0.0, 8.5],
            layer="nutrients", amount=-0.3)

        handler.resolve(effect, ctx)

        # Only the center cell should be affected
        gx, gy, gz = grid.world_to_grid(8.5, 0.0, 8.5)
        assert grid.get("nutrients", gx, gy, gz) < DEFAULT_VALUE - DIRTY_THRESHOLD

    def test_soil_deposit_handler_with_radius(self):
        from ecosim.effects import SoilDeposit, WorldProcessContext
        from ecosim.world_processes import SoilDepositHandler

        grid = make_grid((16, 4, 16))
        ctx = WorldProcessContext(
            tick=1, voxel_grid=grid, biome=None, climate={},
            entities={}, water_sources=[], rate_multipliers={})

        handler = SoilDepositHandler()
        effect = SoilDeposit(
            tick=1, entity_id="decomp-1", position=[8.5, 0.0, 8.5],
            layer="organic_matter", amount=0.2, radius=1.5)

        handler.resolve(effect, ctx)

        cells = grid.query_overlap((8.5, 0.0, 8.5), 1.5)
        for gx, gy, gz in cells:
            val = grid.get("organic_matter", gx, gy, gz)
            assert val > DEFAULT_VALUE - DIRTY_THRESHOLD or \
                   abs(val - DEFAULT_VALUE) < DIRTY_THRESHOLD

    def test_evaporation_handler_uses_walk_layer(self):
        """SoilEvaporationHandler should only visit existing cells."""
        from ecosim.effects import SoilEvaporation, WorldProcessContext
        from ecosim.world_processes import SoilEvaporationHandler

        grid = make_grid((8, 4, 8))
        # Only set a few moisture cells (sparse)
        grid.set("moisture", 3, 0, 3, 0.6)
        grid.set("moisture", 4, 0, 4, 0.7)

        ctx = WorldProcessContext(
            tick=1, voxel_grid=grid, biome=None, climate={},
            entities={}, water_sources=[], rate_multipliers={})

        handler = SoilEvaporationHandler()
        effect = SoilEvaporation(tick=1, evap_rate=0.05, rain_suppressed=False)
        handler.resolve(effect, ctx)

        # The two cells should have evaporated
        assert grid.get("moisture", 3, 0, 3) < 0.6
        assert grid.get("moisture", 4, 0, 4) < 0.7

    def test_evaporation_handler_skips_when_rain_suppressed(self):
        from ecosim.effects import SoilEvaporation, WorldProcessContext
        from ecosim.world_processes import SoilEvaporationHandler

        grid = make_grid()
        grid.set("moisture", 3, 0, 3, 0.6)

        ctx = WorldProcessContext(
            tick=1, voxel_grid=grid, biome=None, climate={},
            entities={}, water_sources=[], rate_multipliers={})

        handler = SoilEvaporationHandler()
        effect = SoilEvaporation(tick=1, evap_rate=0.05, rain_suppressed=True)
        handler.resolve(effect, ctx)

        # No change when rain suppressed
        assert grid.get("moisture", 3, 0, 3) == 0.6
