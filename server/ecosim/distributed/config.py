# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""
līlā Distributed Engine — Configuration dataclasses.

Defines ``DistributedConfig`` which controls tile grid dimensions, boundary zone
size, entity limits, and tick synchronization parameters for the distributed engine.

See Also:
- ``docs/DISTRIBUTED_ENGINE_ARCHITECTURE.md`` — full architecture specification
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class DistributedConfig:
    """Configuration for the distributed simulation engine.

    Controls tile grid layout, per-tile parameters, boundary behavior, and
    tick synchronization. All values are immutable after construction.

    Attributes:
        tile_rows: Number of tile rows in the world grid.
        tile_cols: Number of tile columns in the world grid.
        grid_size: Voxel grid dimension per tile (creates a grid_size×grid_size×grid_size grid).
        cell_size: World units per voxel cell.
        boundary_zone: Distance from tile edge (in world units) where entities
            are replicated as ghosts to adjacent tiles.
        max_entities_per_tile: Maximum initial population per tile. Entities
            migrating in beyond this limit are still accepted (soft cap).
        tick_rate: Seconds between ticks (default 2.0 = 0.5 Hz).

    Example::

        config = DistributedConfig(tile_rows=5, tile_cols=5)
        # → 25 tiles, each 32×32 grid, world is ~160×160 cells
    """

    tile_rows: int = 5
    tile_cols: int = 5
    grid_size: int = 32
    cell_size: float = 1.0
    boundary_zone: float = 5.0
    max_entities_per_tile: int = 50
    tick_rate: float = 2.0

    @property
    def grid_max(self) -> float:
        """World-space maximum coordinate within a single tile.

        For a 32-cell grid with cell_size=1.0, this is 31.0 (cells are indexed
        0..31, and the max coordinate is (grid_size - 1) * cell_size).
        """
        return (self.grid_size - 1) * self.cell_size

    @property
    def tile_world_width(self) -> float:
        """World-space width of one tile including its last cell.

        This is the stride used when computing global coordinates from local
        tile positions. For grid_max=31.0 and cell_size=1.0, this is 32.0.
        """
        return self.grid_max + self.cell_size

    @property
    def world_width(self) -> float:
        """Total world width in world units (all columns)."""
        return self.tile_cols * self.tile_world_width

    @property
    def world_height(self) -> float:
        """Total world height in world units (all rows)."""
        return self.tile_rows * self.tile_world_width

    @property
    def total_tiles(self) -> int:
        """Total number of tiles in the grid."""
        return self.tile_rows * self.tile_cols

    def is_valid_tile(self, row: int, col: int) -> bool:
        """Check if (row, col) is within the world grid bounds."""
        return 0 <= row < self.tile_rows and 0 <= col < self.tile_cols
