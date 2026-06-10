# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""
līlā Distributed Engine — World Layout Generator for tile partitioning.

The ``TileWorldLayout`` generates per-tile world configs from a master world
specification. It partitions entities across tiles based on their global
positions, distributes water sources, and configures soil patches per tile.

Partitioning Strategy
─────────────────────
1. **Species definitions** — shared across all tiles (same trait vectors)
2. **Entities** — distributed by global position to the appropriate tile
3. **Water sources** — placed at specified global positions, owned by one tile
4. **Soil patches** — can vary per tile for environmental diversity

The master spec defines entities with ``global_position`` keys (or regular
``position`` keys that are interpreted as global coordinates). The layout
generator maps each entity to its target tile and converts to local coordinates.

See Also:
- ``docs/DISTRIBUTED_ENGINE_ARCHITECTURE.md`` — full architecture specification
"""

from __future__ import annotations

import logging
import random
from typing import Any

from .config import DistributedConfig

logger = logging.getLogger("lila.distributed.world_layout")


class TileWorldLayout:
    """Generates per-tile world configs from a master world specification.

    Partitions entities, water sources, and soil patches across an N×M tile grid.
    Each tile gets a complete ``world_config`` dict suitable for constructing
    an EcosystemEngine instance.

    Args:
        master_spec: Master world definition dict. Must contain:
            - ``environment`` — biome, climate, voxel_grid config (shared)
            - ``species_definitions`` — trait vectors (shared across tiles)
            - ``entities`` — list of entity definitions with global positions
            - Optionally: ``water_sources``, ``soil_patches``, ``rates``, ``model``
        config: DistributedConfig controlling tile grid layout.

    Attributes:
        master_spec: The master world specification (read-only reference).
        config: The distributed configuration (read-only reference).
    """

    def __init__(
        self,
        master_spec: dict[str, Any],
        config: DistributedConfig,
    ) -> None:
        self.master_spec = master_spec
        self.config = config

    def generate_tile_config(self, row: int, col: int) -> dict[str, Any]:
        """Generate a complete world_config for one tile.

        Extracts the subset of entities whose global positions fall within
        this tile's bounds. Converts their positions to local coordinates.
        Includes shared species definitions and environment config.

        Args:
            row: Tile row position (0-indexed).
            col: Tile column position (0-indexed).

        Returns:
            World config dict suitable for EcosystemEngine(world_config).
        """
        tw = self.config.tile_world_width
        tile_min_x = col * tw
        tile_max_x = tile_min_x + tw
        tile_min_z = row * tw
        tile_max_z = tile_min_z + tw

        # Base environment config (shared across tiles)
        env = dict(self.master_spec.get("environment", {}))

        # Override voxel grid dimensions for this tile's local grid
        env["voxel_grid"] = {
            "dimensions": [self.config.grid_size] * 3,
            "cell_size": self.config.cell_size,
        }

        # Partition entities for this tile
        tile_entities = self._partition_entities(
            tile_min_x, tile_max_x, tile_min_z, tile_max_z, row, col,
        )

        # Partition water sources for this tile
        tile_water_sources = self._partition_water_sources(
            env.get("water_sources", []),
            tile_min_x, tile_max_x, tile_min_z, tile_max_z,
            row, col,
        )
        env["water_sources"] = tile_water_sources

        # Build the tile config
        tile_config: dict[str, Any] = {
            "version": self.master_spec.get("version", "0.1"),
            "session_id": f"tile-{row}-{col}",
            "environment": env,
            "species_definitions": self.master_spec.get("species_definitions", []),
            "entities": tile_entities,
        }

        # Include shared config sections
        if "rates" in self.master_spec:
            tile_config["rates"] = dict(self.master_spec["rates"])
        if "model" in self.master_spec:
            tile_config["model"] = dict(self.master_spec["model"])

        return tile_config

    def _partition_entities(
        self,
        tile_min_x: float,
        tile_max_x: float,
        tile_min_z: float,
        tile_max_z: float,
        row: int,
        col: int,
    ) -> list[dict[str, Any]]:
        """Partition entities falling within this tile's bounds.

        Entities from the master spec have global positions (either in
        ``global_position`` or ``position`` key). This method filters to
        those within the tile and converts to local coordinates.

        If the master spec has fewer entities than needed, generates random
        filler entities up to max_entities_per_tile using species definitions.

        Args:
            tile_min_x, tile_max_x: X bounds of this tile in global coords.
            tile_min_z, tile_max_z: Z bounds of this tile in global coords.
            row, col: Tile position for logging and ID scoping.

        Returns:
            List of entity dicts with local positions.
        """
        raw_entities = self.master_spec.get("entities", [])
        tile_entities: list[dict[str, Any]] = []
        rng = random.Random(row * 1000 + col)  # deterministic per tile

        for i, raw in enumerate(raw_entities):
            pos = raw.get("global_position") or raw.get("position", [0.0, 0.0, 0.0])
            gx, gz = pos[0], pos[2]

            if tile_min_x <= gx < tile_max_x and tile_min_z <= gz < tile_max_z:
                # Convert to local coordinates
                local_x = gx - col * self.config.tile_world_width
                local_z = gz - row * self.config.tile_world_width

                entity = dict(raw)
                entity["position"] = [local_x, pos[1], local_z]
                # Remove global_position key (not used by engine)
                entity.pop("global_position", None)
                tile_entities.append(entity)

        # Entity count is controlled by the master spec partitioning.
        # The max_entities_per_tile config value is a soft cap for initial
        # population; migrating entities can exceed it.

        return tile_entities

    def _partition_water_sources(
        self,
        all_sources: list[dict[str, Any]],
        tile_min_x: float,
        tile_max_x: float,
        tile_min_z: float,
        tile_max_z: float,
        row: int,
        col: int,
    ) -> list[dict[str, Any]]:
        """Partition water sources for this tile.

        Water sources whose center falls within the tile bounds are owned by
        that tile. Sources near boundaries may be duplicated in adjacent tiles
        (handled by the orchestrator's ghost protocol for Phase 2).

        Args:
            all_sources: All water source definitions from master spec.
            tile_min_x, tile_max_x: X bounds of this tile.
            tile_min_z, tile_max_z: Z bounds of this tile.
            row, col: Tile position.

        Returns:
            List of water source dicts with local positions.
        """
        tile_sources: list[dict[str, Any]] = []

        for ws in all_sources:
            pos = ws.get("position", [0.0, 0.0, 0.0])
            gx, gz = pos[0], pos[2]

            if tile_min_x <= gx < tile_max_x and tile_min_z <= gz < tile_max_z:
                local_x = gx - col * self.config.tile_world_width
                local_z = gz - row * self.config.tile_world_width

                source = dict(ws)
                source["position"] = [local_x, pos[1], local_z]
                tile_sources.append(source)

        return tile_sources

    def generate_full_world(self) -> dict[tuple[int, int], dict[str, Any]]:
        """Generate world configs for all tiles in the grid.

        Returns:
            Dict mapping (row, col) to complete tile world_config dicts.
        """
        configs: dict[tuple[int, int], dict[str, Any]] = {}
        for row in range(self.config.tile_rows):
            for col in range(self.config.tile_cols):
                configs[(row, col)] = self.generate_tile_config(row, col)

        total_entities = sum(
            len(cfg.get("entities", [])) for cfg in configs.values()
        )
        logger.info(
            "Generated %d tile configs with %d total entities",
            len(configs), total_entities,
        )

        return configs
