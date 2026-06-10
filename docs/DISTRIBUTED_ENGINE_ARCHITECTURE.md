<!--
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
-->

# Distributed Engine Architecture

## Goal

Scale the simulation beyond a single `EcosystemEngine` instance by partitioning
the world into spatially separate **tiles** that communicate via efficient message
passing. Start with multiple engines on one node (shared memory) before moving
to multi-node deployment.

**Target configuration:** 5×5 tile grid, each tile running a 32×32 voxel grid
with up to 50 initial entities. Total world: 160×160 cells across 25 engines.

---

## Core Concepts

### Tile

A **tile** is the fundamental unit of distributed simulation. Each tile owns:

- One `EcosystemEngine` instance running on a 32×32 voxel grid
- Up to 50 initial entities (configurable per species)
- A local coordinate space `[0, grid_max] × [0, grid_max]` where `grid_max = 31.0`
- A `(row, col)` position in the global tile grid

Tiles are **autonomous** — they run their own tick loop independently. Cross-tile
communication happens only at boundaries via ghost entities and migration messages.

### World Grid

The world is an `N×M` arrangement of tiles. For the target 5×5 configuration:

```
┌─────┬─────┬─────┬─────┬─────┐
│(0,0)│(0,1)│(0,2)│(0,3)│(0,4)│
├─────┼─────┼─────┼─────┼─────┤
│(1,0)│(1,1)│(1,2)│(1,3)│(1,4)│
├─────┼─────┼─────┼─────┼─────┤
│(2,0)│(2,1)│(2,2)│(2,3)│(2,4)│
├─────┼─────┼─────┼─────┼─────┤
│(3,0)│(3,1)│(3,2)│(3,3)│(3,4)│
├─────┼─────┼─────┼─────┼─────┤
│(4,0)│(4,1)│(4,2)│(4,3)│(4,4)│
└─────┴─────┴─────┴─────┴─────┘

Global world: 160×160 cells (5 tiles × 32 cells per tile)
```

### Global vs Local Coordinates

Entities use **local coordinates** within their tile `[0, grid_max]`. The
orchestrator maintains a mapping to **global coordinates**:

```
global_x = tile_col * grid_width + local_x
global_z = tile_row * grid_width + local_z
```

where `grid_width = 32.0` (the world-space width of one tile).

This mapping is used for:
- Client rendering (assembling the full world view)
- Migration routing (determining which tile an entity belongs to)
- Debug/telemetry (global entity tracking)

### Ghost Entities

Entities within a **boundary zone** near a tile edge are replicated as read-only
**ghost entities** in adjacent tiles. This allows interaction actors (predation,
herbivory, pollination, fleeing) to see neighbors across tile boundaries without
modifying the core engine's spatial query logic.

```
Tile A (32×32)                    Tile B (32×32)
┌──────────────────────┐         ┌──────────────────────┐
│                      │         │  ghost of deer_A  ◄──│
│   deer_A ●           │         │                       │
│              boundary│    ──►  │boundary               │
│      zone = 5.0      │         │   zone = 5.0          │
│                      │         │                       │
└──────────────────────┘         └──────────────────────┘

deer_A is at local x=29 (within 5.0 of right edge).
A read-only ghost appears in Tile B at local x=1.0 (mirrored position).
```

Ghost entities:
- Are marked with `_ghost: true` in metadata
- Have a `source_tile` reference `(row, col)` for migration routing
- Are **not** processed by flow/guard actors (no state evolution)
- **Are** included in spatial index queries (visible to interaction actors)
- Are excluded from tick packets sent to clients

### Entity Migration

When an entity moves past a tile boundary, ownership transfers to the adjacent
tile:

```
1. Tile detects entity position exceeds boundary threshold during step()
2. Orchestrator receives MigrationMessage(entity, direction, target_tile)
3. Entity is removed from source tile's engine.entities
4. Entity is inserted into target tile with remapped local coordinates
5. Ghost replicas are cleaned up across all affected tiles
```

Migration happens **after** the tick completes (Phase 7: Spawn/Kill), so entities
finish their current interaction before crossing. This avoids mid-tick state
inconsistency.

### Voxel Boundaries

Each tile maintains its own voxel grid independently. Boundary voxels are **not**
shared in Phase 1 — this means soil moisture/nutrient gradients don't cross tile
boundaries. This is acceptable for Phase 1 because:

- Entities spend limited time at boundaries (they move through)
- The boundary zone is small relative to the full grid
- Environmental effects are local (footprint-based drain/deposit)

**Phase 2 extension:** Shared boundary voxel strips where adjacent tiles sync
edge layer values each tick. This enables continuous moisture/nutrient gradients
across tile seams.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Orchestrator                             │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                    │
│  │ Tile(0,0) │   │ Tile(0,1) │   │ Tile(0,2) │   ...            │
│  │          │   │          │   │          │                    │
│  │ Engine   │   │ Engine   │   │ Engine   │                    │
│  │ 32×32    │   │ 32×32    │   │ 32×32    │                    │
│  │ ≤50 ents │   │ ≤50 ents │   │ ≤50 ents │                    │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘                    │
│       │              │              │                           │
│       └──────────┬───┴──────────────┘                           │
│                  │  Cross-tile messages (shared memory)          │
│                  ▼                                              │
│           Message Bus                                           │
│    MigrationMessage, GhostUpdate, GlobalEvent                   │
└─────────────────────────────────────────────────────────────────┘

Phase 1: All tiles + orchestrator in single process (asyncio tasks)
Phase 2: Tiles on separate nodes (TCP/UDP transport)
```

---

## Module Structure

```
server/ecosim/distributed/
├── __init__.py              # Public API exports
├── tile.py                  # Tile class — wraps EcosystemEngine with boundary awareness
├── orchestrator.py          # WorldOrchestrator — coordinates N×M tiles, syncs ticks
├── messages.py              # Message types (MigrationMessage, GhostUpdate, etc.)
├── config.py                # DistributedConfig — tile grid size, boundary zone, etc.
└── world_layout.py          # TileWorldLayout — generates per-tile world configs from master spec
```

---

## Data Types

### Messages (`messages.py`)

```python
@dataclass(frozen=True)
class MigrationMessage:
    """Entity crossing tile boundary."""
    entity_id: str
    source_tile: tuple[int, int]   # (row, col)
    target_tile: tuple[int, int]
    entity_data: dict              # full entity dict for re-insertion
    global_position: list[float]  # position in global coordinates

@dataclass(frozen=True)
class GhostUpdate:
    """Ghost entity state change from source tile."""
    source_tile: tuple[int, int]
    target_tiles: list[tuple[int, int]]  # adjacent tiles that need this ghost
    entity_id: str
    position: list[float]       # local position in source tile
    state: str
    state_vars: dict[str, float]

@dataclass(frozen=True)
class GlobalEvent:
    """World-wide event broadcast to all tiles."""
    event_type: str             # "RAIN", "SEASON_CHANGE", etc.
    payload: dict               # event-specific data (e.g., intensity for rain)
```

### Configuration (`config.py`)

```python
@dataclass
class DistributedConfig:
    """Configuration for the distributed simulation."""
    tile_rows: int = 5          # number of tile rows
    tile_cols: int = 5          # number of tile columns
    grid_size: int = 32         # voxel grid dimension per tile (32×32×32)
    cell_size: float = 1.0      # world units per voxel cell
    boundary_zone: float = 5.0  # distance from edge where ghosts are created
    max_entities_per_tile: int = 50  # initial population cap per tile
    tick_rate: float = 0.1      # seconds between ticks (10 Hz)

    @property
    def grid_max(self) -> float:
        return (self.grid_size - 1) * self.cell_size  # 31.0

    @property
    def world_width(self) -> float:
        return self.tile_cols * self.grid_max + self.cell_size  # ~160.0

    @property
    def world_height(self) -> float:
        return self.tile_rows * self.grid_max + self.cell_size  # ~160.0

    @property
    def total_tiles(self) -> int:
        return self.tile_rows * self.tile_cols
```

---

## Tile Design (`tile.py`)

The `Tile` class wraps an `EcosystemEngine` and adds boundary awareness:

### Responsibilities

1. **Ghost management** — inject ghost entities from neighbors before tick,
   remove after tick
2. **Boundary detection** — detect entities that crossed tile boundaries during
   the tick
3. **Migration emission** — emit `MigrationMessage` for crossing entities
4. **Ghost emission** — emit `GhostUpdate` when boundary entities change state

### Lifecycle per Tick

```
Tile.step(dt):
    1. Inject ghost entities from neighbors into engine.entities
       (marked with _ghost: true, source_tile reference)
    2. Call self.engine.step(dt)  ← standard tick loop runs normally
    3. Remove ghost entities from engine.entities
    4. Detect boundary crossings → emit MigrationMessages
    5. Update ghost replicas for neighbors → emit GhostUpdates
    6. Return filtered tick packet (ghosts excluded)
```

### Key Methods

```python
class Tile:
    def __init__(self, row: int, col: int, world_config: dict, config: DistributedConfig):
        self.row = row
        self.col = col
        self.config = config
        self.engine = EcosystemEngine(world_config)
        self._ghosts: dict[str, dict] = {}  # ghost entities injected this tick

    def step(self, dt: float, neighbor_ghosts: dict[tuple[int,int], list[dict]]) -> TickResult:
        """Run one simulation tick with boundary awareness.

        Args:
            dt: Time step in seconds.
            neighbor_ghosts: Ghost entities from adjacent tiles, keyed by tile position.

        Returns:
            TickResult with tick packet, migration messages, and ghost updates.
        """
        ...

    def _inject_ghosts(self, neighbor_ghosts) -> None:
        """Insert ghost entities into engine.entities for spatial queries."""
        ...

    def _remove_ghosts(self) -> None:
        """Remove previously injected ghost entities."""
        ...

    def _detect_migrations(self) -> list[MigrationMessage]:
        """Find entities that crossed tile boundaries this tick."""
        ...

    def _build_ghost_updates(self) -> list[GhostUpdate]:
        """Build ghost updates for boundary entities to send to neighbors."""
        ...

    def insert_entity(self, entity: dict, local_position: list[float]) -> None:
        """Insert a migrating entity at the given local position."""
        ...

    def remove_entity(self, entity_id: str) -> None:
        """Remove an entity (migration out or death)."""
        ...

    def apply_global_event(self, event: GlobalEvent) -> None:
        """Apply a world-wide event (e.g., rain) to this tile."""
        ...
```

### TickResult

```python
@dataclass
class TickResult:
    """Result from one Tile.step() call."""
    tick_packet: dict              # filtered packet (no ghosts)
    migrations: list[MigrationMessage]  # entities crossing out
    ghost_updates: list[GhostUpdate]    # updates for neighbors
```

---

## Orchestrator Design (`orchestrator.py`)

The `WorldOrchestrator` coordinates all tiles and handles cross-tile messaging.

### Responsibilities

1. **Tile lifecycle** — create, initialize, and manage N×M tiles
2. **Tick synchronization** — run all tiles in lockstep (Phase 1) or with
   bounded drift tolerance (Phase 2)
3. **Message routing** — deliver migration messages and ghost updates to correct tiles
4. **Global events** — broadcast rain, season changes, etc. to all tiles
5. **World state assembly** — merge tick packets from all tiles for client rendering

### Tick Loop

```
Orchestrator.step(dt):
    1. Collect ghost entities from each tile's boundary zone
       → build neighbor_ghosts map: {tile_pos: [ghost_entities]}
    2. Run all tiles in parallel (asyncio.gather for Phase 1):
       results = await gather(tile.step(dt, neighbor_ghosts[tile_pos]) for tile)
    3. Process migration messages:
       for msg in all_migrations:
           source_tile.remove_entity(msg.entity_id)
           target_tile.insert_entity(msg.entity_data, remap_to_local(msg.global_position))
    4. Update ghost replicas across tiles based on GhostUpdates
    5. Assemble combined tick packet (merge entity updates from all tiles)
    6. Return combined packet
```

### Key Methods

```python
class WorldOrchestrator:
    def __init__(self, config: DistributedConfig, master_world_spec: dict):
        self.config = config
        self.tiles: dict[tuple[int,int], Tile] = {}
        self._tick: int = 0
        # Build tiles from master world spec
        layout = TileWorldLayout(master_world_spec, config)
        for row in range(config.tile_rows):
            for col in range(config.tile_cols):
                tile_config = layout.generate_tile_config(row, col)
                self.tiles[(row, col)] = Tile(row, col, tile_config, config)

    async def step(self, dt: float) -> dict:
        """Run one synchronized tick across all tiles."""
        ...

    def apply_rain(self, intensity: float) -> None:
        """Broadcast rain event to all tiles."""
        for tile in self.tiles.values():
            tile.apply_global_event(GlobalEvent("RAIN", {"intensity": intensity}))

    @property
    def neighbors(self) -> dict[tuple[int,int], list[tuple[int,int]]]:
        """Map each tile to its adjacent tiles (up/down/left/right)."""
        ...

    def global_to_local(self, global_pos: list[float]) -> tuple[tuple[int,int], list[float]]:
        """Convert global coordinates to (tile_position, local_coordinates)."""
        ...

    def local_to_global(self, tile_pos: tuple[int,int], local_pos: list[float]) -> list[float]:
        """Convert local coordinates to global world coordinates."""
        ...
```

---

## World Layout Generation (`world_layout.py`)

The `TileWorldLayout` generates per-tile world configs from a master specification.
Each tile gets its own subset of entities, water sources, and soil patches.

### Strategy

1. **Master spec** defines the full world (species definitions, biome, climate)
2. **Partitioning** distributes entities across tiles based on global positions
3. **Water sources** are placed at tile boundaries or within tiles as specified
4. **Soil patches** can vary per tile for environmental diversity

```python
class TileWorldLayout:
    def __init__(self, master_spec: dict, config: DistributedConfig):
        self.master_spec = master_spec
        self.config = config

    def generate_tile_config(self, row: int, col: int) -> dict:
        """Generate a complete world_config for one tile.

        Extracts the subset of entities whose global positions fall within
        this tile's bounds. Places water sources and soil patches according
        to the master spec partitioning rules.
        """
        ...
```

### Entity Distribution

Entities from the master spec have **global positions**. The layout generator:
1. Maps each entity to its target tile via `global_to_local()`
2. Converts global position to local coordinates for that tile
3. Ensures no tile exceeds `max_entities_per_tile` (rejects or redistributes)
4. Generates unique entity IDs scoped to the tile

### Water Source Placement

Water sources can be:
- **Internal** — fully within one tile (owned by that tile)
- **Boundary** — centered on a tile edge (replicated as ghost water source in adjacent tile)

For Phase 1, boundary water sources are duplicated in both tiles with shared
state managed by the orchestrator.

---

## Migration Protocol

### When Migration Happens

Migration is detected at the end of each tick, after all phases complete:

```python
def _detect_migrations(self) -> list[MigrationMessage]:
    migrations = []
    grid_max = self.config.grid_max
    boundary = self.config.boundary_zone  # not used for migration threshold

    for entity in self.engine.entities.values():
        if entity.get("_ghost"):
            continue
        pos = entity["position"]

        target_tile = None
        if pos[0] >= grid_max:          # crossed right edge
            target_tile = (self.row, self.col + 1)
        elif pos[0] < 0:                # crossed left edge
            target_tile = (self.row, self.col - 1)
        elif pos[2] >= grid_max:        # crossed bottom edge
            target_tile = (self.row + 1, self.col)
        elif pos[2] < 0:                # crossed top edge
            target_tile = (self.row - 1, self.col)

        if target_tile and self._is_valid_tile(target_tile):
            global_pos = self.orchestrator.local_to_global(
                (self.row, self.col), pos
            )
            migrations.append(MigrationMessage(
                entity_id=entity["id"],
                source_tile=(self.row, self.col),
                target_tile=target_tile,
                entity_data=dict(entity),  # shallow copy for transport
                global_position=global_pos,
            ))

    return migrations
```

### Migration Application (Orchestrator)

```python
def _apply_migrations(self, all_migrations: list[MigrationMessage]) -> None:
    for msg in all_migrations:
        # Remove from source tile
        source = self.tiles[msg.source_tile]
        source.remove_entity(msg.entity_id)

        # Insert into target tile with remapped local position
        target = self.tiles[msg.target_tile]
        _, local_pos = self.global_to_local(msg.global_position)
        msg.entity_data["position"] = local_pos
        target.insert_entity(msg.entity_data, local_pos)
```

### Edge Cases

- **Corner crossing** — entity crosses both X and Z boundaries simultaneously.
  Route to diagonal tile. If diagonal tile doesn't exist (world edge), clamp
  position and keep in current tile.
- **Bounce** — if target tile is out of bounds, clamp entity position to grid
  edge and do not migrate (entity stays at boundary).
- **Sessile entities** — plants/trees don't migrate. If a plant spawns near
  a boundary via vegetative spreading, it stays in its birth tile.

---

## Ghost Entity Protocol

### Injection

Before each tick, the orchestrator collects boundary entities from all tiles and
builds a ghost map:

```python
def _collect_ghosts(self) -> dict[tuple[int,int], list[dict]]:
    """Collect ghost entities from all tile boundaries."""
    ghosts_for_tile: dict[tuple[int,int], list[dict]] = defaultdict(list)

    for (row, col), tile in self.tiles.items():
        boundary_entities = tile._get_boundary_entities()
        for entity in boundary_entities:
            # Determine which neighbor tiles need this ghost
            neighbors = self._affected_neighbors(tile, entity)
            for neighbor_pos in neighbors:
                # Mirror position into neighbor's local coordinate space
                mirrored = self._mirror_position(entity["position"], tile, neighbor_pos)
                ghost = {
                    **entity,
                    "_ghost": True,
                    "source_tile": (row, col),
                    "position": mirrored,  # in neighbor's local coords
                }
                ghosts_for_tile[neighbor_pos].append(ghost)

    return ghosts_for_tile
```

### Position Mirroring

When a ghost is injected into a neighbor tile, its position is mirrored:

```python
def _mirror_position(self, local_pos: list[float], source_tile: Tile,
                     target_pos: tuple[int,int]) -> list[float]:
    """Mirror entity position from source tile to target tile's coordinate space."""
    src_row, src_col = source_tile.row, source_tile.col
    tgt_row, tgt_col = target_pos
    grid_max = self.config.grid_max

    x, z = local_pos[0], local_pos[2]

    if tgt_col == src_col + 1:   # ghost goes to right neighbor
        x = x - grid_max          # e.g., 29 → -2 (clamped to 0 by target tile)
    elif tgt_col == src_col - 1: # ghost goes to left neighbor
        x = x + grid_max          # e.g., 2 → 33 (clamped to grid_max)
    if tgt_row == src_row + 1:   # ghost goes to bottom neighbor
        z = z - grid_max
    elif tgt_row == src_row - 1: # ghost goes to top neighbor
        z = z + grid_max

    return [max(0, x), local_pos[1], max(0, z)]
```

### Ghost Lifecycle

```
Tick N:
  1. Orchestrator collects boundary entities → builds ghost map
  2. Each tile injects its ghosts into engine.entities (keyed by source_tile:entity_id)
  3. Tile.step() runs — spatial index includes ghosts for interaction queries
  4. Tile removes ghosts from engine.entities

Tick N+1:
  5. If boundary entity moved, new GhostUpdate is emitted
  6. Neighbor tiles update their ghost replica position/state
```

Ghost entities use composite IDs like `ghost:(row,col):entity_id` to avoid
colliding with real entity IDs in the target tile.

---

## Phase Plan

### Phase 1: Single-Node Multi-Engine (Current Priority)

**Goal:** 5×5 tiles running in a single process, synchronized via asyncio.

| Step | Description | Deliverable |
|------|-------------|-------------|
| 1.1 | Define message types and config dataclasses | `distributed/messages.py`, `distributed/config.py` |
| 1.2 | Implement Tile class with ghost injection/removal | `distributed/tile.py` |
| 1.3 | Implement WorldOrchestrator tick sync + message routing | `distributed/orchestrator.py` |
| 1.4 | Implement TileWorldLayout for per-tile config generation | `distributed/world_layout.py` |
| 1.5 | Migration detection and application | Integrated into tile.py + orchestrator.py |
| 1.6 | Global event broadcasting (rain) | Orchestrator method + Tile handler |
| 1.7 | Combined tick packet assembly for client rendering | Orchestrator.step() return value |
| 1.8 | Test suite: ghost injection, migration, boundary interactions | `tests/test_distributed.py` (~40 tests) |
| 1.9 | Demo: 5×5 tile world with entity migration visualization | Script + updated browser visualizer |

**Validation criteria:**
- Entity migrates from one tile to adjacent tile when crossing boundary
- Predator in Tile A can detect prey ghost in Tile B (and vice versa)
- 5×5 world runs at target tick rate (10 Hz) on single node
- No entity is lost during migration
- Tick packets correctly exclude ghosts

### Phase 2: Multi-Node Preparation

**Goal:** Abstract transport layer so tiles can run on separate nodes.

| Step | Description | Deliverable |
|------|-------------|-------------|
| 2.1 | Message serialization (compact binary protocol) | `distributed/serialization.py` |
| 2.2 | Transport abstraction interface | `distributed/transport.py` (Protocol) |
| 2.3 | Shared-memory transport (Phase 1 implementation) | `distributed/transports/shared_memory.py` |
| 2.4 | TCP transport for multi-node | `distributed/transports/tcp.py` |
| 2.5 | Latency compensation — speculative execution at boundaries | Orchestrator extension |

### Phase 3: Multi-Node Deployment

**Goal:** Run tiles across multiple machines with fault tolerance.

| Step | Description | Deliverable |
|------|-------------|-------------|
| 3.1 | Distributed orchestrator with node assignment | `distributed/node_orchestrator.py` |
| 3.2 | Fault tolerance — engine restart with state replay from effect log | Effect log persistence + replay |
| 3.3 | Dynamic rebalancing — migrate tiles when population shifts | Load balancer in orchestrator |

---

## Integration Points

### With Existing Engine

The distributed layer wraps `EcosystemEngine` without modifying it:

- **Ghost injection** — adds/removes entities from `engine.entities` dict
  (no engine code changes needed)
- **Migration** — uses existing `init_entity()` for inserting into target tile
- **Tick packets** — post-processes engine output to filter ghosts and add
  global coordinate mapping

### With Worker / WebSocket

The worker currently runs one `SimulationSession` with one engine. For
distributed mode, the worker runs a `WorldOrchestrator`:

```python
# Current (single engine):
session = SimulationSession(world_config)
packet = session.engine.step(dt)

# Distributed (multi-tile):
orchestrator = WorldOrchestrator(config, master_world_spec)
packet = await orchestrator.step(dt)  # merged packet from all tiles
```

The WebSocket protocol remains the same — clients receive a unified tick packet.
The visualizer needs to handle global coordinates for rendering across tiles.

### With Browser Visualizer

The visualizer receives entity positions in **global coordinates** (mapped by
the orchestrator). Tile boundaries can be rendered as subtle grid lines for
debugging. No protocol changes needed — just coordinate scaling.

---

## Performance Considerations

### Per-Tick Overhead (per tile)

| Operation | Cost | Notes |
|-----------|------|-------|
| Ghost injection | O(G) where G = boundary entities | ~5-10% of entity count |
| Ghost removal | O(G) | Same as injection |
| Migration detection | O(E) where E = tile entities | Single pass over entities |
| Ghost update emission | O(G) | Only for changed boundary entities |
| Message routing (orchestrator) | O(T × G) where T = total tiles | Batched per tick |

### Target Throughput

- 25 tiles × 50 entities = 1,250 total entities
- At 10 Hz: ~12,500 entity-ticks/second
- Ghost overhead: ~5-10% additional (62-125 ghost entities per tile at boundaries)
- Expected: single-node 5×5 runs comfortably on modern hardware

### Future Scaling

For larger worlds (>10×10 tiles), consider:
- **Spatial hash** swap in `SpatialIndex` for O(1) neighbor queries (reduces
  ghost overhead by narrowing boundary zone)
- **OctreeVoxelGrid** for sparse voxel storage at boundaries
- **Async tile stepping** with bounded drift tolerance instead of strict sync

---

## Open Questions

1. **Boundary water sources** — should water sources on tile edges be shared
   state or duplicated? (Phase 1: duplicate, Phase 2: shared via orchestrator)
2. **Reproduction near boundaries** — if a child spawns within the boundary zone,
   does it start as a ghost in the neighbor tile? (Yes — ghost protocol handles this)
3. **Voxel continuity** — should soil moisture/nutrients be continuous across
   tiles? (Phase 1: no, Phase 2: shared boundary strips)
4. **Tick synchronization** — strict lockstep or bounded drift? (Phase 1: lockstep
   via asyncio.gather, Phase 2: configurable tolerance)
