# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""
līlā Distributed Engine — Tile: single-node simulation unit with boundary awareness.

A ``Tile`` wraps one ``EcosystemEngine`` instance and adds cross-tile communication
via ghost entity injection/removal and migration detection. Each tile owns a
32×32 voxel grid with up to 50 initial entities.

Ghost Protocol
──────────────
Before each tick, the orchestrator provides ghost entities from adjacent tiles.
The Tile injects these into ``engine.entities`` so spatial queries see neighbors
across boundaries. After the tick, ghosts are removed and a filtered packet is returned.

    Tick N:
      1. Inject neighbor ghosts → engine.entities (keyed as "ghost:{row}:{col}:{eid}")
      2. engine.step(dt) — standard 7-phase loop runs with ghosts in spatial index
      3. Remove ghosts from engine.entities
      4. Detect boundary crossings → MigrationMessages
      5. Build ghost updates for neighbors → GhostUpdates
      6. Return TileTickResult (packet filtered of ghosts, migrations + ghost updates)

Migration Detection
───────────────────
After each tick, entities whose positions exceed grid bounds are flagged for
migration to the adjacent tile in that direction. Migration is applied by the
orchestrator *after* all tiles complete their ticks, so entities finish their
current interaction before crossing.

See Also:
- ``docs/DISTRIBUTED_ENGINE_ARCHITECTURE.md`` — full architecture specification
"""

from __future__ import annotations

import logging
from typing import Any

from ..engine import EcosystemEngine
from .config import DistributedConfig
from .messages import GhostUpdate, MigrationMessage, TileTickResult

logger = logging.getLogger("lila.distributed.tile")

# Prefix for ghost entity IDs to avoid collisions with real entities.
_GHOST_PREFIX = "ghost:"


def _make_ghost_id(source_row: int, source_col: int, entity_id: str) -> str:
    """Create a unique ghost ID that won't collide with real entity IDs."""
    return f"{_GHOST_PREFIX}{source_row}:{source_col}:{entity_id}"


def _is_ghost(entity_id: str) -> bool:
    """Check if an entity ID is a ghost marker."""
    return entity_id.startswith(_GHOST_PREFIX)


class Tile:
    """Single simulation tile wrapping one EcosystemEngine with boundary awareness.

    Args:
        row: Tile row position in the world grid (0-indexed).
        col: Tile column position in the world grid (0-indexed).
        world_config: World definition dict for this tile's engine. Must contain
            ``environment``, ``entities``, and ``species_definitions`` keys.
            Entity positions should be in local coordinates [0, grid_max].
        config: DistributedConfig controlling grid size, boundary zone, etc.

    Attributes:
        row: Tile row in world grid.
        col: Tile column in world grid.
        engine: The wrapped EcosystemEngine instance.
        config: The distributed configuration (read-only reference).
    """

    def __init__(
        self,
        row: int,
        col: int,
        world_config: dict[str, Any],
        config: DistributedConfig,
    ) -> None:
        self.row = row
        self.col = col
        self.config = config
        self.engine = EcosystemEngine(world_config)

        # Ghost bookkeeping — tracks which ghost IDs were injected this tick.
        self._injected_ghost_ids: list[str] = []

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def grid_max(self) -> float:
        """World-space max coordinate within this tile."""
        return self.config.grid_max

    @property
    def entity_count(self) -> int:
        """Number of real (non-ghost) entities in this tile."""
        return sum(1 for eid in self.engine.entities if not _is_ghost(eid))

    # ── Public API ────────────────────────────────────────────────────────

    def step(
        self,
        dt: float,
        neighbor_ghosts: dict[tuple[int, int], list[dict[str, Any]]],
    ) -> TileTickResult:
        """Run one simulation tick with boundary awareness.

        Injects ghost entities from neighbors, runs the engine's standard
        7-phase tick loop, then removes ghosts and detects migrations.

        Args:
            dt: Time step in seconds.
            neighbor_ghosts: Ghost entities from adjacent tiles, keyed by
                source tile (row, col). Positions are already mirrored into
                this tile's local coordinate space.

        Returns:
            TileTickResult with filtered tick packet, migration messages,
            and ghost updates for neighbors.
        """
        # Phase A: Inject ghosts from neighbors
        self._inject_ghosts(neighbor_ghosts)

        # Phase B: Run standard engine tick (ghosts are in spatial index)
        raw_packet = self.engine.step(dt)

        # Phase C: Remove injected ghosts
        self._remove_ghosts()

        # Phase D: Detect boundary crossings → migrations
        migrations = self._detect_migrations()

        # Phase E: Build ghost updates for neighbors
        ghost_updates = self._build_ghost_updates()

        # Phase F: Filter tick packet (exclude ghosts from entity_updates)
        filtered_packet = self._filter_tick_packet(raw_packet)

        return TileTickResult(
            tick_packet=filtered_packet,
            migrations=migrations,
            ghost_updates=ghost_updates,
        )

    def insert_entity(self, entity: dict[str, Any], local_position: list[float]) -> None:
        """Insert a migrating entity at the given local position.

        Called by the orchestrator when an entity migrates into this tile.
        The entity is added directly to the engine's entity registry.

        Args:
            entity: Full entity dict (as produced by init_entity or migration copy).
            local_position: Position in this tile's local coordinate space [0, grid_max].
        """
        entity["position"] = list(local_position)
        self.engine.entities[entity["id"]] = entity

    def remove_entity(self, entity_id: str) -> None:
        """Remove an entity from this tile.

        Called by the orchestrator when an entity migrates out of this tile.

        Args:
            entity_id: The entity's unique identifier.
        """
        self.engine.entities.pop(entity_id, None)

    def apply_rain(self, intensity: float = 0.5) -> None:
        """Apply a rain event to this tile's engine.

        Args:
            intensity: Rain intensity in [0.0, 1.0].
        """
        self.engine.apply_rain(intensity)

    def get_boundary_entities(self) -> list[dict[str, Any]]:
        """Get all real entities within the boundary zone of this tile.

        These are candidates for ghost replication to adjacent tiles.

        Returns:
            List of entity dicts (real entities only, no ghosts).
        """
        bz = self.config.boundary_zone
        grid_max = self.grid_max
        boundary_entities: list[dict[str, Any]] = []

        for eid, entity in self.engine.entities.items():
            if _is_ghost(eid):
                continue
            pos = entity["position"]
            # Check if within boundary zone of any edge
            if (pos[0] <= bz or pos[0] >= grid_max - bz
                    or pos[2] <= bz or pos[2] >= grid_max - bz):
                boundary_entities.append(entity)

        return boundary_entities

    # ── Ghost Injection / Removal ────────────────────────────────────────

    def _inject_ghosts(
        self,
        neighbor_ghosts: dict[tuple[int, int], list[dict[str, Any]]],
    ) -> None:
        """Insert ghost entities into engine.entities for spatial queries.

        Ghost IDs use the format "ghost:{row}:{col}:{original_id}" to avoid
        collisions with real entity IDs in this tile.

        Args:
            neighbor_ghosts: Map from source tile position to list of entity dicts.
                Each entity dict has positions already mirrored into this tile's
                local coordinate space.
        """
        self._injected_ghost_ids.clear()

        for (src_row, src_col), ghosts in neighbor_ghosts.items():
            for ghost_entity in ghosts:
                original_id = ghost_entity["id"]
                ghost_id = _make_ghost_id(src_row, src_col, original_id)

                # Don't re-inject if we already have this ghost from another source
                if ghost_id in self.engine.entities:
                    continue

                # Mark as ghost and store source tile reference
                ghost_entity["_ghost"] = True
                ghost_entity["_source_tile"] = (src_row, src_col)
                self.engine.entities[ghost_id] = ghost_entity
                self._injected_ghost_ids.append(ghost_id)

    def _remove_ghosts(self) -> None:
        """Remove all previously injected ghost entities from engine.entities."""
        for ghost_id in self._injected_ghost_ids:
            self.engine.entities.pop(ghost_id, None)
        self._injected_ghost_ids.clear()

    # ── Migration Detection ──────────────────────────────────────────────

    def _detect_migrations(self) -> list[MigrationMessage]:
        """Find entities that crossed tile boundaries this tick.

        Checks each real entity's position against grid bounds. Entities
        past the edge are flagged for migration to the adjacent tile.

        Returns:
            List of MigrationMessage objects for crossing entities.
        """
        migrations: list[MigrationMessage] = []
        grid_max = self.grid_max

        for eid, entity in self.engine.entities.items():
            if _is_ghost(eid):
                continue

            pos = entity["position"]
            target_tile: tuple[int, int] | None = None

            # Check each boundary direction
            if pos[0] >= grid_max:
                target_tile = (self.row, self.col + 1)
            elif pos[0] < 0:
                target_tile = (self.row, self.col - 1)
            elif pos[2] >= grid_max:
                target_tile = (self.row + 1, self.col)
            elif pos[2] < 0:
                target_tile = (self.row - 1, self.col)

            if target_tile is not None and self.config.is_valid_tile(*target_tile):
                # Compute global position for routing
                global_x = self.col * self.config.tile_world_width + max(0, pos[0])
                global_z = self.row * self.config.tile_world_width + max(0, pos[2])

                migrations.append(MigrationMessage(
                    entity_id=eid,
                    source_tile=(self.row, self.col),
                    target_tile=target_tile,
                    entity_data=dict(entity),  # shallow copy for transport
                    global_position=[global_x, pos[1], global_z],
                ))

        return migrations

    # ── Ghost Update Building ────────────────────────────────────────────

    def _build_ghost_updates(self) -> list[GhostUpdate]:
        """Build ghost updates for boundary entities visible to neighbors.

        For each real entity within the boundary zone, determine which
        adjacent tiles can see it and emit a GhostUpdate.

        Returns:
            List of GhostUpdate objects for distribution by the orchestrator.
        """
        updates: list[GhostUpdate] = []
        bz = self.config.boundary_zone
        grid_max = self.grid_max

        for eid, entity in self.engine.entities.items():
            if _is_ghost(eid):
                continue

            pos = entity["position"]

            # Determine which edges this entity is near
            target_tiles: list[tuple[int, int]] = []
            if pos[0] >= grid_max - bz and self.config.is_valid_tile(self.row, self.col + 1):
                target_tiles.append((self.row, self.col + 1))
            if pos[0] <= bz and self.config.is_valid_tile(self.row, self.col - 1):
                target_tiles.append((self.row, self.col - 1))
            if pos[2] >= grid_max - bz and self.config.is_valid_tile(self.row + 1, self.col):
                target_tiles.append((self.row + 1, self.col))
            if pos[2] <= bz and self.config.is_valid_tile(self.row - 1, self.col):
                target_tiles.append((self.row - 1, self.col))

            if target_tiles:
                updates.append(GhostUpdate(
                    source_tile=(self.row, self.col),
                    target_tiles=target_tiles,
                    entity_id=eid,
                    position=list(pos),
                    state=entity.get("state", "IDLE"),
                    state_vars=dict(entity.get("state_vars", {})),
                ))

        return updates

    # ── Tick Packet Filtering ────────────────────────────────────────────

    def _filter_tick_packet(self, packet: dict[str, Any]) -> dict[str, Any]:
        """Remove ghost entities from the tick packet.

        Ghost entities should not appear in client-facing packets since they
        are replicas owned by other tiles.

        Args:
            packet: Raw tick packet from engine.step().

        Returns:
            Filtered packet with only real entity updates.
        """
        filtered = dict(packet)

        # Filter entity_updates to exclude ghosts
        if "entity_updates" in filtered:
            filtered["entity_updates"] = [
                u for u in filtered["entity_updates"]
                if not _is_ghost(u.get("id", ""))
            ]

        return filtered
