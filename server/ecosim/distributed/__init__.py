# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""
līlā Distributed Engine — Multi-tile ecosystem simulation.

Partitions the world into an N×M grid of tiles, each running its own
EcosystemEngine instance on a 32×32 voxel grid with up to 50 entities.
Tiles communicate via ghost entity replication and migration messages.

Quick Start
───────────
    from ecosim.distributed import (
        DistributedConfig,
        WorldOrchestrator,
    )

    config = DistributedConfig(tile_rows=5, tile_cols=5)
    orchestrator = WorldOrchestrator(config, master_world_spec)
    packet = await orchestrator.step(dt=0.1)

See Also:
- ``docs/DISTRIBUTED_ENGINE_ARCHITECTURE.md`` — full architecture specification
"""

from .config import DistributedConfig
from .messages import (
    GhostUpdate,
    GlobalEvent,
    MigrationMessage,
    TileTickRequest,
    TileTickResult,
)
from .orchestrator import WorldOrchestrator
from .tile import Tile
from .world_layout import TileWorldLayout

__all__ = [
    "DistributedConfig",
    "GhostUpdate",
    "GlobalEvent",
    "MigrationMessage",
    "Tile",
    "TileTickRequest",
    "TileTickResult",
    "TileWorldLayout",
    "WorldOrchestrator",
]
