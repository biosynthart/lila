# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""
līlā Distributed Engine — Message types for cross-tile communication.

Defines frozen dataclasses used as the protocol between tiles and the orchestrator.
All messages are immutable (frozen) to enable safe concurrent access in Phase 1
(shared memory) and clean serialization boundaries in Phase 2 (network transport).

See Also:
- ``docs/DISTRIBUTED_ENGINE_ARCHITECTURE.md`` — full architecture specification
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True, slots=True)
class MigrationMessage:
    """Entity crossing tile boundary.

    Emitted by a Tile when one of its entities moves past the grid edge.
    The orchestrator routes this to the target tile for insertion.

    Attributes:
        entity_id: Unique identifier of the migrating entity.
        source_tile: (row, col) position of the tile the entity is leaving.
        target_tile: (row, col) position of the tile the entity is entering.
        entity_data: Full entity dict for re-insertion into the target engine.
        global_position: Entity position in global world coordinates at time of migration.
    """

    entity_id: str
    source_tile: tuple[int, int]
    target_tile: tuple[int, int]
    entity_data: dict[str, Any]
    global_position: list[float]


@dataclasses.dataclass(frozen=True, slots=True)
class GhostUpdate:
    """Ghost entity state change from source tile.

    Emitted by a Tile when one of its boundary entities changes position or state.
    The orchestrator distributes this to all adjacent tiles that need the ghost replica.

    Attributes:
        source_tile: (row, col) of the tile owning the real entity.
        target_tiles: List of adjacent tile positions that should host a ghost replica.
        entity_id: Unique identifier of the source entity.
        position: Entity's local position within the source tile.
        state: Current discrete state (FORAGING, RESTING, etc.).
        state_vars: Current continuous state variables.
    """

    source_tile: tuple[int, int]
    target_tiles: list[tuple[int, int]]
    entity_id: str
    position: list[float]
    state: str
    state_vars: dict[str, float]


@dataclasses.dataclass(frozen=True, slots=True)
class GlobalEvent:
    """World-wide event broadcast to all tiles.

    Used for events that affect the entire ecosystem simultaneously, such as
    rainfall or seasonal changes. The orchestrator broadcasts these to every tile.

    Attributes:
        event_type: Event category (e.g., "RAIN", "SEASON_CHANGE").
        payload: Event-specific data dict (e.g., {"intensity": 0.8} for rain).
    """

    event_type: str
    payload: dict[str, Any]


@dataclasses.dataclass(frozen=True, slots=True)
class TileTickRequest:
    """Orchestrator → Tile: request a tick with neighbor ghosts.

    Sent by the orchestrator to each tile before stepping. Contains ghost
    entities from all adjacent tiles that should be visible during this tick.

    Attributes:
        dt: Time step in seconds.
        ghosts: Ghost entities keyed by source tile position. Each value is a
            list of entity dicts with positions already mirrored into the
            receiving tile's local coordinate space.
    """

    dt: float
    ghosts: dict[tuple[int, int], list[dict[str, Any]]]


@dataclasses.dataclass(frozen=True, slots=True)
class TileTickResult:
    """Tile → Orchestrator: result of one tick step.

    Returned by Tile.step() to the orchestrator. Contains the filtered tick
    packet (no ghosts), any migration messages for entities crossing boundaries,
    and ghost updates for neighbors.

    Attributes:
        tick_packet: Delta-encoded tick packet with entity updates, events, etc.
            Ghost entities are excluded from this packet.
        migrations: MigrationMessages for entities that crossed out of this tile.
        ghost_updates: GhostUpdates for boundary entities visible to neighbors.
    """

    tick_packet: dict[str, Any]
    migrations: list[MigrationMessage]
    ghost_updates: list[GhostUpdate]
