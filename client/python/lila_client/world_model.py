"""līlā Python Client — World Model (local scene graph).

Mirrors the browser client's WorldModel for entity tracking and spatial queries.
Used by both the ImGui renderer and the local agency system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorldEntity:
    """Client-side entity record with server reference and local position."""

    id: str
    type: str = ""          # ANIMAL, PLANT, TREE, INSECT, BIRD, MICROORGANISM
    species: str = ""

    # Server reference position (gravity well for reconciliation)
    ref_x: float = 0.0
    ref_z: float = 0.0

    # Client-agency position (where the entity actually is in local sim)
    x: float = 0.0
    z: float = 0.0

    # Discrete state from server
    state: str = "IDLE"

    # Drive values from server intent packet
    drive: dict[str, float] = field(default_factory=dict)

    # Motion latent vector (4D, from motor model)
    motion_latent: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    # Eligibility flags from server
    can_consume: bool = False
    can_predate: bool = False
    can_pollinate: bool = False
    repro_eligible: bool = False
    can_drink: bool = False
    spread_eligible: bool = False

    # Skeleton ID (for future 3D client)
    skeleton_id: Optional[str] = None

    # Local agency state
    target_x: float = 0.0
    target_z: float = 0.0
    has_target: bool = False
    velocity_x: float = 0.0
    velocity_z: float = 0.0
    speed: float = 1.0

    # Facing angle (radians, smoothed via lerp toward velocity direction)
    facing_angle: float = 0.0

    # Acknowledgment tracking
    ack_received: bool = False

    @property
    def is_alive(self) -> bool:
        return self.state not in ("DEAD", "DYING", "DORMANT")

    @property
    def is_mobile_consumer(self) -> bool:
        return self.type in ("ANIMAL", "BIRD", "INSECT")

    def distance_to(self, other: WorldEntity) -> float:
        dx = self.x - other.x
        dz = self.z - other.z
        return math.sqrt(dx * dx + dz * dz)

    def dist_sq_to(self, other: WorldEntity) -> float:
        dx = self.x - other.x
        dz = self.z - other.z
        return dx * dx + dz * dz


class WorldModel:
    """Client-side world model — local scene graph with spatial queries."""

    def __init__(self):
        self.entities: dict[str, WorldEntity] = {}
        self.species_defs: dict[str, dict] = {}
        self.water_sources: list[dict] = []
        # Moisture grid for heatmap rendering (GRID_SIZE × GRID_SIZE)
        from .constants import GRID_SIZE
        self.moisture = [0.65] * (GRID_SIZE * GRID_SIZE)
        self._grid_size = GRID_SIZE

    # ─── Entity Management ──────────────────────────────────────────────

    def apply_update(self, u: dict) -> WorldEntity:
        """Add or update an entity from a server tick packet."""
        eid = u["id"]
        ent = self.entities.get(eid)

        if not ent:
            info = _infer_entity_type_from_id(eid)
            ent = WorldEntity(
                id=eid, type=info["type"], species=info["species"],
            )
            ref_pos = u.get("ref_position", [0, 0, 0])
            ent.ref_x = ref_pos[0]
            ent.ref_z = ref_pos[2]
            ent.x = ref_pos[0]
            ent.z = ref_pos[2]
            self.entities[eid] = ent

        # Update server reference position (gravity well)
        if "ref_position" in u:
            pos = u["ref_position"]
            ent.ref_x = pos[0]
            ent.ref_z = pos[2]

        # State and drives
        if "state" in u:
            ent.state = u["state"]
        if "drive" in u:
            ent.drive.update(u["drive"])
        if "motion_latent" in u:
            ent.motion_latent = list(u["motion_latent"])

        # Eligibility flags
        ent.can_consume = bool(u.get("_can_consume", False))
        ent.can_predate = bool(u.get("_can_predate", False))
        ent.can_pollinate = bool(u.get("_can_pollinate", False))
        ent.repro_eligible = bool(u.get("_repro_eligible", False))
        ent.can_drink = bool(u.get("_can_drink", False))
        ent.spread_eligible = bool(u.get("_spread_eligible", False))

        # Acknowledgment tracking
        ent.ack_received = bool(u.get("_ack", False))

        return ent

    def apply_spawn(self, s: dict) -> WorldEntity:
        """Spawn a new entity from server spawn packet."""
        ref_pos = s["ref_position"]
        ent = WorldEntity(
            id=s["id"],
            type=s.get("type", ""),
            species=s.get("species", ""),
            ref_x=ref_pos[0], ref_z=ref_pos[2],
            x=ref_pos[0], z=ref_pos[2],
        )
        if "state" in s:
            ent.state = s["state"]
        if "drive" in s:
            ent.drive.update(s["drive"])
        if "skeleton_id" in s:
            ent.skeleton_id = s["skeleton_id"]
        self.entities[ent.id] = ent
        return ent

    def apply_removal(self, eid: str) -> Optional[WorldEntity]:
        """Remove an entity. Returns the removed entity or None."""
        return self.entities.pop(eid, None)

    # ─── Spatial Queries (for agency logic) ──────────────────────────────

    def find_nearest(
        self, x: float, z: float, types: list[str], exclude_id: str | None = None,
    ) -> Optional[WorldEntity]:
        """Find nearest alive entity of given type(s)."""
        best_dist_sq = float("inf")
        best_ent = None
        for ent in self.entities.values():
            if not ent.is_alive:
                continue
            if exclude_id and ent.id == exclude_id:
                continue
            if ent.type not in types:
                continue
            d2 = (x - ent.x) ** 2 + (z - ent.z) ** 2
            if d2 < best_dist_sq:
                best_dist_sq = d2
                best_ent = ent
        return best_ent

    def find_nearest_species(
        self, x: float, z: float, species_list: list[str], exclude_id: str | None = None,
    ) -> Optional[WorldEntity]:
        """Find nearest alive entity of given species."""
        best_dist_sq = float("inf")
        best_ent: WorldEntity | None = None
        for ent in self.entities.values():
            if not ent.is_alive:
                continue
            if exclude_id and ent.id == exclude_id:
                continue
            if ent.species not in species_list:
                continue
            d2 = (x - ent.x) ** 2 + (z - ent.z) ** 2
            if d2 < best_dist_sq:
                best_dist_sq = d2
                best_ent = ent
        return best_ent

    def find_nearest_mate(self, ent: WorldEntity) -> Optional[WorldEntity]:
        """Find nearest alive entity of the same species."""
        best_dist_sq = float("inf")
        best_ent: WorldEntity | None = None
        for other in self.entities.values():
            if not other.is_alive:
                continue
            if other.id == ent.id:
                continue
            if other.species != ent.species:
                continue
            d2 = ent.dist_sq_to(other)
            if d2 < best_dist_sq:
                best_dist_sq = d2
                best_ent = other
        return best_ent

    def get_species_def(self, species_id: str) -> dict:
        """Get species definition by ID."""
        return self.species_defs.get(species_id, {})

    def find_nearest_water(self, x: float, z: float) -> Optional[dict]:
        """Find nearest non-dry water source."""
        best_dist_sq = float("inf")
        best_source = None
        for ws in self.water_sources:
            if (ws.get("water_level", 1.0) or 1.0) < 0.05:
                continue
            d2 = (x - ws["position"][0]) ** 2 + (z - ws["position"][2]) ** 2
            if d2 < best_dist_sq:
                best_dist_sq = d2
                best_source = ws
        return best_source

    def get_alive_of_type(self, entity_type: str) -> list[WorldEntity]:
        """Get all alive entities of a given type."""
        return [e for e in self.entities.values() if e.is_alive and e.type == entity_type]

    # ─── Voxel / Environment ──────────────────────────────────────────────

    def apply_voxel_deltas(self, deltas: dict) -> None:
        """Apply moisture voxel deltas from server."""
        if not deltas or "moisture" not in deltas:
            return
        for coord_str, val in deltas["moisture"].items():
            parts = list(map(int, coord_str.split(",")))
            gx, gz = parts[0], parts[2]
            if 0 <= gx < self._grid_size and 0 <= gz < self._grid_size:
                self.moisture[gz * self._grid_size + gx] = val

    def apply_water_sources(self, sources: list[dict]) -> None:
        """Update water source positions."""
        self.water_sources = sources or []


def _infer_entity_type_from_id(eid: str) -> dict[str, str]:
    """Infer entity type from ID prefix (fallback when server data is sparse)."""
    if eid.startswith("deer"):
        return {"type": "ANIMAL", "species": "deer"}
    if eid.startswith("wolf"):
        return {"type": "ANIMAL", "species": "wolf"}
    if eid.startswith("bird") or eid.startswith("songbird"):
        return {"type": "BIRD", "species": "songbird"}
    if eid.startswith("butterfly"):
        return {"type": "INSECT", "species": "monarch"}
    if eid.startswith("oak"):
        return {"type": "TREE", "species": "meadow_oak"}
    if eid.startswith("grass"):
        return {"type": "PLANT", "species": "meadow_grass"}
    if eid.startswith("flower"):
        return {"type": "PLANT", "species": "wildflower"}
    if eid.startswith("mushroom") or eid.startswith("fungus"):
        return {"type": "MICROORGANISM", "species": "mushroom"}
    return {"type": "ANIMAL", "species": "unknown"}
