# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0

"""
līlā Distributed Engine — World Orchestrator for multi-tile simulation.

The ``WorldOrchestrator`` coordinates N×M tiles running in a single process.
It manages tick synchronization, cross-tile message routing (migrations and
ghost updates), global event broadcasting, and combined tick packet assembly.

Tick Synchronization (Phase 1: Single-Node)
────────────────────────────────────────────
All tiles step in lockstep via ``asyncio.gather()``. The orchestrator:

    1. Collects boundary entities from all tiles → builds ghost map
    2. Runs all tiles concurrently with their neighbor ghosts
    3. Processes migration messages (remove from source, insert into target)
    4. Updates ghost replicas across tiles based on GhostUpdates
    5. Assembles combined tick packet for client rendering

The orchestrator maintains a global tick counter and maps between local tile
coordinates and global world coordinates.

See Also:
- ``docs/DISTRIBUTED_ENGINE_ARCHITECTURE.md`` — full architecture specification
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from .config import DistributedConfig
from .messages import GlobalEvent, MigrationMessage, TileTickResult
from .tile import Tile, _is_ghost
from .world_layout import TileWorldLayout

logger = logging.getLogger("lila.distributed.orchestrator")


class WorldOrchestrator:
    """Coordinates N×M tiles for distributed ecosystem simulation.

    Manages tile lifecycle, tick synchronization, cross-tile messaging,
    and global event broadcasting. In Phase 1, all tiles run in a single
    process coordinated via asyncio tasks.

    Args:
        config: DistributedConfig controlling grid layout and parameters.
        master_world_spec: Master world definition dict containing species
            definitions, biome, climate, and entity placement rules. The
            TileWorldLayout partitions this into per-tile configs.

    Attributes:
        config: The distributed configuration (read-only reference).
        tiles: Map from (row, col) to Tile instances.
        tick: Global tick counter (incremented each step).
    """

    def __init__(
        self,
        config: DistributedConfig,
        master_world_spec: dict[str, Any],
    ) -> None:
        self.config = config
        self.tiles: dict[tuple[int, int], Tile] = {}
        self.tick: int = 0

        # Build tiles from master world spec
        layout = TileWorldLayout(master_world_spec, config)

        for row in range(config.tile_rows):
            for col in range(config.tile_cols):
                tile_config = layout.generate_tile_config(row, col)
                self.tiles[(row, col)] = Tile(row, col, tile_config, config)

        logger.info(
            "WorldOrchestrator initialized: %d×%d = %d tiles, "
            "%dx%d grid each, world %.0f×%.0f",
            config.tile_rows, config.tile_cols, config.total_tiles,
            config.grid_size, config.grid_size,
            config.world_width, config.world_height,
        )

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def neighbors(self) -> dict[tuple[int, int], list[tuple[int, int]]]:
        """Map each tile to its adjacent tiles (up/down/left/right)."""
        result: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for row in range(self.config.tile_rows):
            for col in range(self.config.tile_cols):
                adj: list[tuple[int, int]] = []
                if row > 0:
                    adj.append((row - 1, col))       # top
                if row < self.config.tile_rows - 1:
                    adj.append((row + 1, col))        # bottom
                if col > 0:
                    adj.append((row, col - 1))        # left
                if col < self.config.tile_cols - 1:
                    adj.append((row, col + 1))        # right
                result[(row, col)] = adj
        return result

    @property
    def total_entities(self) -> int:
        """Total real (non-ghost) entities across all tiles."""
        return sum(tile.entity_count for tile in self.tiles.values())

    # ── Coordinate Mapping ────────────────────────────────────────────────

    def global_to_local(
        self,
        global_pos: list[float],
    ) -> tuple[tuple[int, int], list[float]]:
        """Convert global world coordinates to (tile_position, local_coordinates).

        Args:
            global_pos: [x, y, z] in global world space.

        Returns:
            Tuple of ((row, col), [local_x, local_y, local_z]).
        """
        tw = self.config.tile_world_width
        col = int(global_pos[0] // tw)
        row = int(global_pos[2] // tw)

        # Clamp to valid tile range
        row = max(0, min(self.config.tile_rows - 1, row))
        col = max(0, min(self.config.tile_cols - 1, col))

        local_x = global_pos[0] - col * tw
        local_z = global_pos[2] - row * tw

        return (row, col), [local_x, global_pos[1], local_z]

    def local_to_global(
        self,
        tile_pos: tuple[int, int],
        local_pos: list[float],
    ) -> list[float]:
        """Convert local tile coordinates to global world coordinates.

        Args:
            tile_pos: (row, col) of the source tile.
            local_pos: [x, y, z] in the tile's local coordinate space.

        Returns:
            [global_x, global_y, global_z].
        """
        row, col = tile_pos
        tw = self.config.tile_world_width
        return [
            local_pos[0] + col * tw,
            local_pos[1],
            local_pos[2] + row * tw,
        ]

    # ── Tick Execution ────────────────────────────────────────────────────

    async def step(self, dt: float | None = None) -> dict[str, Any]:
        """Run one synchronized tick across all tiles.

        Orchestrates the full cross-tile tick cycle:
        1. Collect boundary entities → build ghost map per tile
        2. Run all tiles concurrently with neighbor ghosts (asyncio.gather)
        3. Process migration messages (entity ownership transfer)
        4. Update ghost replicas based on GhostUpdates
        5. Assemble combined tick packet for client rendering

        Args:
            dt: Time step in seconds. Defaults to config.tick_rate.

        Returns:
            Combined tick packet with entity updates from all tiles,
            events aggregated across the world, and global coordinate mapping.
        """
        if dt is None:
            dt = self.config.tick_rate

        self.tick += 1

        # Step 1: Collect ghosts — build neighbor_ghosts map for each tile
        ghost_map = self._collect_ghost_map()

        # Step 2: Run all tiles concurrently
        tasks: list[asyncio.Task[TileTickResult]] = []
        for (row, col), tile in self.tiles.items():
            neighbor_ghosts = ghost_map.get((row, col), {})
            task = asyncio.create_task(
                self._run_tile_step(tile, dt, neighbor_ghosts)
            )
            tasks.append(task)

        results: list[TileTickResult] = await asyncio.gather(*tasks)

        # Step 3: Process migrations (entity ownership transfer)
        all_migrations: list[MigrationMessage] = []
        for result in results:
            all_migrations.extend(result.migrations)
        self._apply_migrations(all_migrations)

        # Step 4: Assemble combined tick packet
        combined_packet = self._assemble_combined_packet(results, dt)

        return combined_packet

    async def _run_tile_step(
        self,
        tile: Tile,
        dt: float,
        neighbor_ghosts: dict[tuple[int, int], list[dict[str, Any]]],
    ) -> TileTickResult:
        """Run a single tile's step in an asyncio task.

        Wraps the synchronous Tile.step() call so it can be gathered
        concurrently with other tiles.

        Args:
            tile: The tile to step.
            dt: Time step in seconds.
            neighbor_ghosts: Ghost entities from adjacent tiles.

        Returns:
            TileTickResult from the tile's step.
        """
        # Run in executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: tile.step(dt, neighbor_ghosts),
        )

    # ── Ghost Collection ─────────────────────────────────────────────────

    def _collect_ghost_map(self) -> dict[tuple[int, int], dict[tuple[int, int], list[dict[str, Any]]]]:
        """Collect boundary entities from all tiles and build ghost injection map.

        For each tile, determines which neighbor tiles need ghost replicas of
        its boundary entities. Positions are mirrored into the receiving tile's
        local coordinate space.

        Returns:
            Nested dict: {receiving_tile_pos: {source_tile_pos: [ghost_entities]}}
        """
        # ghosts_for[receiving_tile][(source_row, source_col)] = [entity_dicts]
        ghosts_for: dict[tuple[int, int], dict[tuple[int, int], list[dict[str, Any]]]] = defaultdict(dict)

        for (row, col), tile in self.tiles.items():
            boundary_entities = tile.get_boundary_entities()
            for entity in boundary_entities:
                target_tiles = self._get_affected_neighbors(tile, entity)
                for target_pos in target_tiles:
                    mirrored = self._mirror_position(
                        entity["position"], (row, col), target_pos,
                    )
                    ghost_entity = {
                        "id": entity["id"],
                        "type": entity.get("type", "ANIMAL"),
                        "species": entity.get("species", ""),
                        "state": entity.get("state", "IDLE"),
                        "position": mirrored,
                        "velocity": list(entity.get("velocity", [0.0, 0.0, 0.0])),
                        "state_vars": dict(entity.get("state_vars", {})),
                        "metadata": dict(entity.get("metadata", {})),
                    }

                    if (row, col) not in ghosts_for[target_pos]:
                        ghosts_for[target_pos][(row, col)] = []
                    ghosts_for[target_pos][(row, col)].append(ghost_entity)

        return dict(ghosts_for)

    def _get_affected_neighbors(
        self,
        tile: Tile,
        entity: dict[str, Any],
    ) -> list[tuple[int, int]]:
        """Determine which adjacent tiles can see this boundary entity.

        Checks each of the four cardinal directions to see if the entity is
        within the boundary zone of that edge and if a neighbor tile exists there.

        Args:
            tile: The source tile containing the entity.
            entity: Entity dict with position in local coordinates.

        Returns:
            List of (row, col) positions for tiles that need this ghost.
        """
        bz = self.config.boundary_zone
        grid_max = tile.grid_max
        pos = entity["position"]
        neighbors: list[tuple[int, int]] = []

        # Right edge → neighbor to the east (col + 1)
        if pos[0] >= grid_max - bz and self.config.is_valid_tile(tile.row, tile.col + 1):
            neighbors.append((tile.row, tile.col + 1))
        # Left edge → neighbor to the west (col - 1)
        if pos[0] <= bz and self.config.is_valid_tile(tile.row, tile.col - 1):
            neighbors.append((tile.row, tile.col - 1))
        # Bottom edge → neighbor to the south (row + 1)
        if pos[2] >= grid_max - bz and self.config.is_valid_tile(tile.row + 1, tile.col):
            neighbors.append((tile.row + 1, tile.col))
        # Top edge → neighbor to the north (row - 1)
        if pos[2] <= bz and self.config.is_valid_tile(tile.row - 1, tile.col):
            neighbors.append((tile.row - 1, tile.col))

        return neighbors

    def _mirror_position(
        self,
        local_pos: list[float],
        source_tile: tuple[int, int],
        target_tile: tuple[int, int],
    ) -> list[float]:
        """Mirror entity position from source tile to target tile's coordinate space.

        Converts the entity's local position in the source tile to a local
        position in the target tile by subtracting/adding the tile world width.

        Args:
            local_pos: [x, y, z] in source tile's local coordinates.
            source_tile: (row, col) of the source tile.
            target_tile: (row, cl) of the receiving tile.

        Returns:
            [mirrored_x, y, mirrored_z] in target tile's local coordinates.
        """
        src_row, src_col = source_tile
        tgt_row, tgt_col = target_tile
        tw = self.config.tile_world_width

        x, z = float(local_pos[0]), float(local_pos[2])

        if tgt_col > src_col:   # ghost goes to right neighbor
            x = x - tw
        elif tgt_col < src_col:  # ghost goes to left neighbor
            x = x + tw

        if tgt_row > src_row:   # ghost goes to bottom neighbor
            z = z - tw
        elif tgt_row < src_row:  # ghost goes to top neighbor
            z = z + tw

        return [x, local_pos[1], z]

    # ── Migration Application ─────────────────────────────────────────────

    def _apply_migrations(self, migrations: list[MigrationMessage]) -> None:
        """Apply migration messages by transferring entity ownership.

        For each migration:
        1. Remove entity from source tile's engine.entities
        2. Convert global position to target tile's local coordinates
        3. Insert entity into target tile at the remapped position

        Args:
            migrations: List of MigrationMessage objects from all tiles this tick.
        """
        for msg in migrations:
            # Remove from source tile
            source_tile = self.tiles.get(msg.source_tile)
            if source_tile is not None:
                source_tile.remove_entity(msg.entity_id)

            # Insert into target tile with remapped local position
            target_tile = self.tiles.get(msg.target_tile)
            if target_tile is None:
                logger.warning(
                    "Migration target %s out of bounds for entity %s, dropping",
                    msg.target_tile, msg.entity_id,
                )
                continue

            _, local_pos = self.global_to_local(msg.global_position)
            # Clamp to valid range within target tile
            grid_max = target_tile.grid_max
            local_pos[0] = max(0.0, min(grid_max, local_pos[0]))
            local_pos[2] = max(0.0, min(grid_max, local_pos[2]))

            target_tile.insert_entity(msg.entity_data, local_pos)

    # ── Combined Tick Packet Assembly ─────────────────────────────────────

    def _assemble_combined_packet(
        self,
        results: list[TileTickResult],
        dt: float,
    ) -> dict[str, Any]:
        """Merge tick packets from all tiles into a single client-facing packet.

        Entity positions are converted to global coordinates. Events from all
        tiles are aggregated. Voxel deltas and water sources are included per-tile
        with tile position metadata for the visualizer to place them correctly.

        Args:
            results: List of TileTickResult from each tile's step.
            dt: Time step in seconds.

        Returns:
            Combined tick packet dict suitable for WebSocket transmission.
        """
        all_entity_updates: list[dict[str, Any]] = []
        all_spawns: list[dict[str, Any]] = []
        all_removals: list[str] = []
        all_events: list[dict[str, Any]] = []
        tile_voxel_deltas: dict[str, Any] = {}
        tile_water_sources: dict[str, Any] = {}

        for (row, col), result in zip(self.tiles.keys(), results):
            packet = result.tick_packet
            tile_key = f"{row},{col}"

            # Entity updates — convert positions to global coordinates
            for update in packet.get("entity_updates", []):
                if _is_ghost(update.get("id", "")):
                    continue
                local_pos = update["position"]
                global_pos = self.local_to_global((row, col), local_pos)
                all_entity_updates.append({
                    **update,
                    "position": [round(v, 4) for v in global_pos],
                    "tile": tile_key,
                })

            # Spawns
            for spawn in packet.get("entity_spawns", []):
                local_pos = spawn["position"]
                global_pos = self.local_to_global((row, col), local_pos)
                all_spawns.append({
                    **spawn,
                    "position": [round(v, 4) for v in global_pos],
                    "tile": tile_key,
                })

            # Removals
            all_removals.extend(packet.get("entity_removals", []))

            # Events — add tile metadata
            for event in packet.get("events", []):
                all_events.append({**event, "tile": tile_key})

            # Voxel deltas (per-tile)
            if "voxel_deltas" in packet:
                tile_voxel_deltas[tile_key] = packet["voxel_deltas"]

            # Water sources — convert positions to global
            if "water_sources" in packet:
                tile_water_sources[tile_key] = [
                    {
                        **ws,
                        "position": [
                            round(v, 4) for v in self.local_to_global(
                                (row, col), ws["position"]
                            )
                        ],
                    }
                    for ws in packet["water_sources"]
                ]

        combined: dict[str, Any] = {
            "tick": self.tick,
            "dt": dt,
            "entity_updates": all_entity_updates,
            "world_size": {
                "tile_rows": self.config.tile_rows,
                "tile_cols": self.config.tile_cols,
                "grid_width": self.config.world_width,
                "grid_height": self.config.world_height,
            },
        }

        if all_spawns:
            combined["entity_spawns"] = all_spawns
        if all_removals:
            combined["entity_removals"] = all_removals
        if all_events:
            combined["events"] = all_events
        if tile_voxel_deltas:
            combined["voxel_deltas"] = tile_voxel_deltas
        if tile_water_sources:
            combined["water_sources"] = tile_water_sources

        return combined

    # ── Global Events ─────────────────────────────────────────────────────

    def apply_rain(self, intensity: float = 0.5) -> None:
        """Broadcast rain event to all tiles simultaneously.

        Args:
            intensity: Rain intensity in [0.0, 1.0].
        """
        for tile in self.tiles.values():
            tile.apply_rain(intensity)

    def apply_global_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast a custom global event to all tiles.

        Args:
            event_type: Event category (e.g., "RAIN", "SEASON_CHANGE").
            payload: Event-specific data dict.
        """
        if event_type == "RAIN":
            intensity = payload.get("intensity", 0.5)
            self.apply_rain(intensity)
        # Extend with additional event types as needed

    # ── Utility ───────────────────────────────────────────────────────────

    def get_tile(self, row: int, col: int) -> Tile | None:
        """Get a tile by its grid position.

        Args:
            row: Tile row (0-indexed).
            col: Tile column (0-indexed).

        Returns:
            Tile instance or None if out of bounds.
        """
        return self.tiles.get((row, col))
