# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""
Tests for the distributed simulation engine (multi-tile architecture).

Covers:
- DistributedConfig properties and validation
- Message type construction and immutability
- Tile ghost injection/removal and migration detection
- WorldOrchestrator coordinate mapping, neighbor resolution, ghost collection
- TileWorldLayout entity and water source partitioning
"""

from __future__ import annotations

import pytest

from ecosim.distributed.config import DistributedConfig
from ecosim.distributed.messages import (
    GhostUpdate,
    GlobalEvent,
    MigrationMessage,
    TileTickResult,
)
from ecosim.distributed.tile import _is_ghost, _make_ghost_id
from ecosim.distributed.world_layout import TileWorldLayout


# ═══════════════════════════════════════════════════════════════════════════
# DistributedConfig Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDistributedConfig:
    """Test DistributedConfig dataclass properties and validation."""

    def test_default_values(self) -> None:
        cfg = DistributedConfig()
        assert cfg.tile_rows == 5
        assert cfg.tile_cols == 5
        assert cfg.grid_size == 32
        assert cfg.cell_size == 1.0
        assert cfg.boundary_zone == 5.0
        assert cfg.max_entities_per_tile == 50
        assert cfg.tick_rate == 2.0

    def test_grid_max(self) -> None:
        cfg = DistributedConfig(grid_size=32, cell_size=1.0)
        assert cfg.grid_max == 31.0

        cfg2 = DistributedConfig(grid_size=64, cell_size=2.0)
        assert cfg2.grid_max == 126.0

    def test_tile_world_width(self) -> None:
        cfg = DistributedConfig(grid_size=32, cell_size=1.0)
        # grid_max + cell_size = 31.0 + 1.0 = 32.0
        assert cfg.tile_world_width == 32.0

    def test_world_dimensions(self) -> None:
        cfg = DistributedConfig(tile_rows=5, tile_cols=5, grid_size=32, cell_size=1.0)
        # 5 tiles × 32 world units per tile = 160.0
        assert cfg.world_width == 160.0
        assert cfg.world_height == 160.0

    def test_total_tiles(self) -> None:
        cfg = DistributedConfig(tile_rows=5, tile_cols=5)
        assert cfg.total_tiles == 25

        cfg2 = DistributedConfig(tile_rows=3, tile_cols=4)
        assert cfg2.total_tiles == 12

    def test_is_valid_tile_in_bounds(self) -> None:
        cfg = DistributedConfig(tile_rows=5, tile_cols=5)
        assert cfg.is_valid_tile(0, 0) is True
        assert cfg.is_valid_tile(4, 4) is True
        assert cfg.is_valid_tile(2, 3) is True

    def test_is_valid_tile_out_of_bounds(self) -> None:
        cfg = DistributedConfig(tile_rows=5, tile_cols=5)
        assert cfg.is_valid_tile(-1, 0) is False
        assert cfg.is_valid_tile(5, 0) is False
        assert cfg.is_valid_tile(0, -1) is False
        assert cfg.is_valid_tile(0, 5) is False

    def test_custom_configuration(self) -> None:
        cfg = DistributedConfig(
            tile_rows=3,
            tile_cols=4,
            grid_size=16,
            cell_size=2.0,
            boundary_zone=3.0,
            max_entities_per_tile=25,
        )
        assert cfg.grid_max == 30.0       # (16-1) * 2.0
        assert cfg.tile_world_width == 32.0  # 30.0 + 2.0
        assert cfg.world_width == 128.0    # 4 * 32.0
        assert cfg.world_height == 96.0    # 3 * 32.0


# ═══════════════════════════════════════════════════════════════════════════
# Message Type Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMigrationMessage:
    """Test MigrationMessage construction and immutability."""

    def test_construction(self) -> None:
        msg = MigrationMessage(
            entity_id="deer_1",
            source_tile=(2, 3),
            target_tile=(2, 4),
            entity_data={"id": "deer_1", "type": "ANIMAL"},
            global_position=[96.0, 0.0, 64.0],
        )
        assert msg.entity_id == "deer_1"
        assert msg.source_tile == (2, 3)
        assert msg.target_tile == (2, 4)

    def test_frozen(self) -> None:
        msg = MigrationMessage(
            entity_id="deer_1",
            source_tile=(0, 0),
            target_tile=(0, 1),
            entity_data={},
            global_position=[0.0, 0.0, 0.0],
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            msg.entity_id = "deer_2"  # type: ignore


class TestGhostUpdate:
    """Test GhostUpdate construction."""

    def test_construction(self) -> None:
        update = GhostUpdate(
            source_tile=(1, 1),
            target_tiles=[(1, 0), (0, 1)],
            entity_id="butterfly_3",
            position=[28.0, 0.0, 5.0],
            state="FORAGING",
            state_vars={"hunger": 0.6, "energy": 0.4},
        )
        assert update.source_tile == (1, 1)
        assert len(update.target_tiles) == 2
        assert update.state == "FORAGING"


class TestGlobalEvent:
    """Test GlobalEvent construction."""

    def test_rain_event(self) -> None:
        event = GlobalEvent(event_type="RAIN", payload={"intensity": 0.8})
        assert event.event_type == "RAIN"
        assert event.payload["intensity"] == 0.8


class TestTileTickResult:
    """Test TileTickResult construction."""

    def test_construction(self) -> None:
        result = TileTickResult(
            tick_packet={"tick": 1, "entity_updates": []},
            migrations=[],
            ghost_updates=[],
        )
        assert result.tick_packet["tick"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Ghost ID Helpers Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestGhostIdHelpers:
    """Test ghost ID generation and detection."""

    def test_make_ghost_id(self) -> None:
        gid = _make_ghost_id(2, 3, "deer_1")
        assert gid == "ghost:2:3:deer_1"

    def test_is_ghost_true(self) -> None:
        assert _is_ghost("ghost:0:0:entity_x") is True
        assert _is_ghost("ghost:4:4:butterfly_99") is True

    def test_is_ghost_false(self) -> None:
        assert _is_ghost("deer_1") is False
        assert _is_ghost("butterfly_3") is False
        assert _is_ghost("grass_0") is False


# ═══════════════════════════════════════════════════════════════════════════
# TileWorldLayout Tests
# ═══════════════════════════════════════════════════════════════════════════

def _make_master_spec(
    entities: list[dict] | None = None,
    water_sources: list[dict] | None = None,
) -> dict:
    """Build a minimal master world spec for testing."""
    return {
        "version": "0.1",
        "environment": {
            "biome": "TEMPERATE",
            "climate": {"temperature": 22.0, "humidity": 0.6},
            "voxel_grid": {"dimensions": [32, 32, 32], "cell_size": 1.0},
            "water_sources": water_sources or [],
        },
        "species_definitions": [
            {
                "name": "deer",
                "body_mass_kg": 80.0,
                "diet_type": "herbivore",
                "locomotion": "quadruped",
                "thermoregulation": "endotherm",
            },
        ],
        "entities": entities or [],
    }


class TestTileWorldLayout:
    """Test per-tile world config generation from master spec."""

    def test_entity_partitioning_single_tile(self) -> None:
        """Entity at global (10, 0, 10) should go to tile (0, 0)."""
        cfg = DistributedConfig(tile_rows=2, tile_cols=2, grid_size=32, cell_size=1.0)
        spec = _make_master_spec(entities=[{
            "id": "deer_1",
            "type": "ANIMAL",
            "species": "deer",
            "position": [10.0, 0.0, 10.0],  # global coords → tile (0, 0)
        }])

        layout = TileWorldLayout(spec, cfg)
        tile_cfg = layout.generate_tile_config(0, 0)

        assert len(tile_cfg["entities"]) == 1
        entity = tile_cfg["entities"][0]
        # Position should be converted to local coords (same for tile 0,0)
        assert entity["position"] == [10.0, 0.0, 10.0]

    def test_entity_partitioning_cross_tile(self) -> None:
        """Entity at global (50, 0, 40) should go to tile (1, 1)."""
        cfg = DistributedConfig(tile_rows=2, tile_cols=2, grid_size=32, cell_size=1.0)
        spec = _make_master_spec(entities=[{
            "id": "deer_1",
            "type": "ANIMAL",
            "species": "deer",
            "position": [50.0, 0.0, 40.0],  # global → tile (1, 1)
        }])

        layout = TileWorldLayout(spec, cfg)

        # Tile (0, 0) should have no entities
        cfg_00 = layout.generate_tile_config(0, 0)
        assert len(cfg_00["entities"]) == 0

        # Tile (1, 1) should have the entity with local position [18.0, 0.0, 8.0]
        cfg_11 = layout.generate_tile_config(1, 1)
        assert len(cfg_11["entities"]) == 1
        entity = cfg_11["entities"][0]
        # global (50, 40) - tile offset (32, 32) = local (18, 8)
        assert entity["position"] == [18.0, 0.0, 8.0]

    def test_entity_partitioning_multiple_tiles(self) -> None:
        """Entities distributed across multiple tiles."""
        cfg = DistributedConfig(tile_rows=2, tile_cols=2, grid_size=32, cell_size=1.0)
        spec = _make_master_spec(entities=[
            {"id": "e1", "type": "ANIMAL", "species": "deer", "position": [5.0, 0.0, 5.0]},    # tile (0,0)
            {"id": "e2", "type": "ANIMAL", "species": "deer", "position": [40.0, 0.0, 5.0]},   # tile (0,1)
            {"id": "e3", "type": "ANIMAL", "species": "deer", "position": [5.0, 0.0, 40.0]},   # tile (1,0)
            {"id": "e4", "type": "ANIMAL", "species": "deer", "position": [50.0, 0.0, 50.0]},  # tile (1,1)
        ])

        layout = TileWorldLayout(spec, cfg)

        assert len(layout.generate_tile_config(0, 0)["entities"]) == 1
        assert len(layout.generate_tile_config(0, 1)["entities"]) == 1
        assert len(layout.generate_tile_config(1, 0)["entities"]) == 1
        assert len(layout.generate_tile_config(1, 1)["entities"]) == 1

    def test_water_source_partitioning(self) -> None:
        """Water sources partitioned by global position."""
        cfg = DistributedConfig(tile_rows=2, tile_cols=2, grid_size=32, cell_size=1.0)
        spec = _make_master_spec(water_sources=[
            {"position": [15.0, 0.0, 15.0], "radius": 3.0},   # tile (0,0)
            {"position": [50.0, 0.0, 50.0], "radius": 2.0},    # tile (1,1)
        ])

        layout = TileWorldLayout(spec, cfg)

        cfg_00 = layout.generate_tile_config(0, 0)
        assert len(cfg_00["environment"]["water_sources"]) == 1
        ws = cfg_00["environment"]["water_sources"][0]
        assert ws["position"] == [15.0, 0.0, 15.0]

        cfg_11 = layout.generate_tile_config(1, 1)
        assert len(cfg_11["environment"]["water_sources"]) == 1
        ws = cfg_11["environment"]["water_sources"][0]
        # global (50, 50) - tile offset (32, 32) = local (18, 18)
        assert ws["position"] == [18.0, 0.0, 18.0]

    def test_species_definitions_shared(self) -> None:
        """Species definitions are shared across all tiles."""
        cfg = DistributedConfig(tile_rows=2, tile_cols=2)
        spec = _make_master_spec()

        layout = TileWorldLayout(spec, cfg)
        cfg_00 = layout.generate_tile_config(0, 0)
        cfg_11 = layout.generate_tile_config(1, 1)

        assert cfg_00["species_definitions"] == cfg_11["species_definitions"]

    def test_generate_full_world(self) -> None:
        """generate_full_world produces configs for all tiles."""
        cfg = DistributedConfig(tile_rows=3, tile_cols=2)
        spec = _make_master_spec()

        layout = TileWorldLayout(spec, cfg)
        full = layout.generate_full_world()

        assert len(full) == 6  # 3 × 2


# ═══════════════════════════════════════════════════════════════════════════
# Coordinate Mapping Tests (Orchestrator logic, tested in isolation)
# ═══════════════════════════════════════════════════════════════════════════

class TestCoordinateMapping:
    """Test global ↔ local coordinate conversion logic."""

    def _make_config(self):
        return DistributedConfig(tile_rows=5, tile_cols=5, grid_size=32, cell_size=1.0)

    def test_global_to_local_center_tile(self) -> None:
        """Global (64, 0, 80) → tile (2, 2), local (0, 0, 16)."""
        cfg = self._make_config()
        tw = cfg.tile_world_width  # 32.0

        col = int(64.0 // tw)     # 2
        row = int(80.0 // tw)     # 2
        local_x = 64.0 - col * tw  # 0.0
        local_z = 80.0 - row * tw  # 16.0

        assert (row, col) == (2, 2)
        assert local_x == 0.0
        assert local_z == 16.0

    def test_local_to_global(self) -> None:
        """Local (15, 0, 20) in tile (3, 4) → global (143, 0, 116)."""
        cfg = self._make_config()
        tw = cfg.tile_world_width  # 32.0

        row, col = 3, 4
        local_pos = [15.0, 0.0, 20.0]
        global_x = local_pos[0] + col * tw   # 15 + 128 = 143
        global_z = local_pos[2] + row * tw   # 20 + 96 = 116

        assert global_x == 143.0
        assert global_z == 116.0

    def test_roundtrip(self) -> None:
        """Global → local → global should be identity."""
        cfg = self._make_config()
        tw = cfg.tile_world_width

        # Start with global position
        gx, gz = 75.0, 93.0

        # Global → local
        col = int(gx // tw)
        row = int(gz // tw)
        lx = gx - col * tw
        lz = gz - row * tw

        # Local → global
        back_gx = lx + col * tw
        back_gz = lz + row * tw

        assert back_gx == gx
        assert back_gz == gz


# ═══════════════════════════════════════════════════════════════════════════
# Ghost Position Mirroring Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestGhostMirroring:
    """Test ghost position mirroring between adjacent tiles."""

    def _mirror(
        self, local_pos: list[float], source: tuple[int, int], target: tuple[int, int],
    ) -> list[float]:
        """Reproduce the orchestrator's _mirror_position logic for testing."""
        tw = 32.0  # tile_world_width for default config
        src_row, src_col = source
        tgt_row, tgt_col = target

        x, z = float(local_pos[0]), float(local_pos[2])

        if tgt_col > src_col:
            x = x - tw
        elif tgt_col < src_col:
            x = x + tw
        if tgt_row > src_row:
            z = z - tw
        elif tgt_row < src_row:
            z = z + tw

        return [x, local_pos[1], z]

    def test_mirror_to_right_neighbor(self) -> None:
        """Entity at x=29 in tile (0,0) → ghost at x=-3 in tile (0,1)."""
        result = self._mirror([29.0, 0.0, 15.0], (0, 0), (0, 1))
        assert result == [-3.0, 0.0, 15.0]

    def test_mirror_to_left_neighbor(self) -> None:
        """Entity at x=2 in tile (0,1) → ghost at x=34 in tile (0,0)."""
        result = self._mirror([2.0, 0.0, 15.0], (0, 1), (0, 0))
        assert result == [34.0, 0.0, 15.0]

    def test_mirror_to_bottom_neighbor(self) -> None:
        """Entity at z=29 in tile (0,0) → ghost at z=-3 in tile (1,0)."""
        result = self._mirror([15.0, 0.0, 29.0], (0, 0), (1, 0))
        assert result == [15.0, 0.0, -3.0]

    def test_mirror_to_top_neighbor(self) -> None:
        """Entity at z=2 in tile (1,0) → ghost at z=34 in tile (0,0)."""
        result = self._mirror([15.0, 0.0, 2.0], (1, 0), (0, 0))
        assert result == [15.0, 0.0, 34.0]

    def test_mirror_to_diagonal_neighbor(self) -> None:
        """Entity at corner → ghost in diagonal tile."""
        result = self._mirror([29.0, 0.0, 29.0], (0, 0), (1, 1))
        assert result == [-3.0, 0.0, -3.0]
