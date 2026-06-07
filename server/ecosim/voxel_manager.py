# Copyright 2025 BioSynthArt Studios LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Sparse voxel manager for the līlā ecosystem.

Tracks five environmental layers
(nutrients_fast, nutrients_slow, moisture, temperature, organic_matter)
over a 3D grid. Only voxels that change beyond a
threshold are flagged as dirty and included in the next tick packet.

Grid coordinates are integer tuples (x, y, z). Layers are stored as
flat dicts keyed by coordinate tuple for O(1) access. The default
value for any unset voxel is 1.0 (fully saturated).

Multi-resolution protocol
─────────────────────────
The ``VoxelGrid`` protocol abstracts away the storage strategy so that
handlers and engine code can work with uniform grids today and swap in
an octree or quadtree later without changing call sites.

See Also:
- GitHub issue #64 — multi-resolution voxel grid interface (adaptive refinement)
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol, runtime_checkable

LAYERS = (
    "nutrients_fast",
    "nutrients_slow",
    "moisture",
    "temperature",
    "organic_matter",
)
# Backward-compat alias: old code referencing "nutrients" maps to fast pool.
LAYER_NUTRIENTS_ALIAS = {"nutrients": "nutrients_fast"}
DEFAULT_VALUE = 1.0
DIRTY_THRESHOLD = 0.05


def _distance_3d_sq(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    """Squared Euclidean distance between two world-space points."""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


# ═══════════════════════════════════════════════════════════════════════════════
# VoxelGrid Protocol — abstracts away storage strategy
# ═══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class VoxelGrid(Protocol):
    """Abstract interface for multi-resolution voxel grids.

    Implementations can range from uniform flat grids to octrees/quadtrees.
    The engine and world-process handlers only depend on this protocol.
    """

    dimensions: tuple[int, int, int]  # logical grid bounds (coarse level)
    cell_size: float  # base cell size at coarsest resolution

    def get(self, layer: str, x: int, y: int, z: int) -> float:
        """Read a voxel value. Returns DEFAULT_VALUE for unset voxels."""
        ...

    def set(self, layer: str, x: int, y: int, z: int, value: float) -> None:
        """Set a voxel value, clamped to [0.0, 1.0]."""
        ...

    def add(self, layer: str, x: int, y: int, z: int, delta: float) -> float:
        """Add a delta to a voxel value. Returns new clamped value."""
        ...

    def world_to_grid(
        self, wx: float, wy: float, wz: float,
    ) -> tuple[int, int, int]:
        """Convert world-space position to nearest grid coordinate."""
        ...

    def query_overlap(
        self,
        center: tuple[float, float, float],
        radius: float,
    ) -> list[tuple[int, int, int]]:
        """Find all grid cells overlapping a spherical region.

        Args:
            center: World-space center of the sphere.
            radius: Footprint radius in world units.

        Returns:
            List of (x, y, z) cell coordinates whose centers fall within
            *radius* of *center*.  A tree with canopy_radius=4.0 centered
            at (16, 2, 16) might return 16-64 cells depending on local
            refinement level.
        """
        ...

    def walk_layer(
        self,
        layer: str,
        callback: Callable[[int, int, int, float], None],
    ) -> None:
        """Walk all existing cells in a layer, calling *callback* for each.

        Skips empty regions entirely.  Replaces O(grid²) full walks in
        evaporation and rainfall handlers.
        """
        ...

    def get_delta_packet(self) -> dict[str, dict[str, float]]:
        """Return all dirty voxels grouped by layer and clear buffer."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# UniformVoxelGrid — current flat uniform grid (protocol-compliant)
# ═══════════════════════════════════════════════════════════════════════════════


class UniformVoxelGrid:
    """Flat uniform grid — implements the ``VoxelGrid`` protocol.

    No behavioral changes from the original VoxelManager.  Adds
    ``query_overlap()`` and ``walk_layer()`` on top of existing sparse
    dict storage, enabling handlers to use the protocol interface today
    and swap in an octree later without changing call sites.
    """

    def __init__(
        self,
        dimensions: tuple[int, int, int] = (32, 32, 32),
        cell_size: float = 1.0,
    ):
        self.dimensions = dimensions
        self.cell_size = cell_size

        # Current state per layer: coord -> value
        self._data: dict[str, dict[tuple[int, int, int], float]] = {
            layer: {} for layer in LAYERS
        }

        # Dirty buffer per layer: coord_str -> value (ready for JSON)
        self._dirty: dict[str, dict[str, float]] = {
            layer: {} for layer in LAYERS
        }

    def get(
        self, layer: str, x: int, y: int, z: int,
    ) -> float:
        """Read a voxel value. Returns DEFAULT_VALUE for unset voxels.

        Accepts legacy alias ``"nutrients"`` → ``"nutrients_fast"``.
        """
        layer = LAYER_NUTRIENTS_ALIAS.get(layer, layer)
        return self._data[layer].get((x, y, z), DEFAULT_VALUE)

    def set(
        self, layer: str, x: int, y: int, z: int, value: float,
    ) -> None:
        """
        Set a voxel value, clamped to [0.0, 1.0].

        Always persists the value in ``_data`` so that subsequent reads
        (e.g. from ``walk_layer``) see the updated state.  Marks the voxel
        as dirty for client delta packets only when the change exceeds
        DIRTY_THRESHOLD.

        Accepts legacy alias ``"nutrients"`` → ``"nutrients_fast"``.
        """
        layer = LAYER_NUTRIENTS_ALIAS.get(layer, layer)
        coord = (x, y, z)
        value = max(0.0, min(1.0, value))
        old = self._data[layer].get(coord, DEFAULT_VALUE)

        # Always persist — small per-tick fluxes must accumulate correctly.
        self._data[layer][coord] = value

        # Only mark dirty for client delta packets when change is significant.
        if abs(old - value) > DIRTY_THRESHOLD:
            self._dirty[layer][f"{x},{y},{z}"] = round(value, 4)

    def add(
        self, layer: str, x: int, y: int, z: int, delta: float,
    ) -> float:
        """
        Add a delta to a voxel value (can be negative). Returns the
        new clamped value. Convenience for flow-equation updates.
        """
        current = self.get(layer, x, y, z)
        new_val = current + delta
        self.set(layer, x, y, z, new_val)
        return max(0.0, min(1.0, new_val))

    def get_delta_packet(self) -> dict[str, dict[str, float]]:
        """
        Return all dirty voxels grouped by layer and clear the buffer.
        Returns an empty dict if nothing changed (caller should omit
        from tick packet).
        """
        packet = {}
        for layer in LAYERS:
            if self._dirty[layer]:
                packet[layer] = self._dirty[layer]
                self._dirty[layer] = {}
        return packet

    def world_to_grid(self, wx: float, wy: float, wz: float) -> tuple[int, int, int]:
        """Convert a world-space position to the nearest grid coordinate."""
        gx = int(max(0, min(self.dimensions[0] - 1, wx / self.cell_size)))
        gy = int(max(0, min(self.dimensions[1] - 1, wy / self.cell_size)))
        gz = int(max(0, min(self.dimensions[2] - 1, wz / self.cell_size)))
        return (gx, gy, gz)

    def query_overlap(
        self,
        center: tuple[float, float, float],
        radius: float,
    ) -> list[tuple[int, int, int]]:
        """Find all grid cells whose centers fall within *radius* of *center*.

        Uses a bounding-box early-out: iterates only over the integer cell
        range that could possibly overlap the sphere, then filters by exact
        distance.  Returns at least one cell (the center cell) even for
        very small radii.
        """
        cx, cy, cz = self.world_to_grid(*center)
        r_cells = max(1, int(math.ceil(radius / self.cell_size)))
        radius_sq = radius * radius
        dx, dy, dz = self.dimensions
        results: list[tuple[int, int, int]] = []
        for x in range(max(0, cx - r_cells), min(dx, cx + r_cells + 1)):
            wx = (x + 0.5) * self.cell_size
            dx_sq = (wx - center[0]) ** 2
            if dx_sq > radius_sq:
                continue
            for y in range(max(0, cy - r_cells), min(dy, cy + r_cells + 1)):
                wy = (y + 0.5) * self.cell_size
                if dx_sq + (wy - center[1]) ** 2 > radius_sq:
                    continue
                for z in range(max(0, cz - r_cells), min(dz, cz + r_cells + 1)):
                    wz = (z + 0.5) * self.cell_size
                    if dx_sq + (wy - center[1]) ** 2 + (wz - center[2]) ** 2 <= radius_sq:
                        results.append((x, y, z))
        return results

    def walk_layer(
        self,
        layer: str,
        callback: Callable[[int, int, int, float], None],
    ) -> None:
        """Walk all existing cells in *layer*, calling *callback* for each.

        Only visits cells that exist in the sparse dict — empty regions are
        skipped entirely.  This replaces O(grid²) full walks in evaporation
        and rainfall handlers.
        """
        for (x, y, z), value in self._data[layer].items():
            callback(x, y, z, value)

    def initialize_from_soil(self, soil_config: dict[str, float]) -> None:
        """
        Set uniform initial values across the grid from the world
        definition's soil parameters. Only sets non-default values
        to keep the sparse representation efficient.

        Nutrients are split into fast (40%, immediately available) and
        slow (60%, long-term reserve) pools per the two-pool nutrient model.
        """
        dx, dy, dz = self.dimensions

        # Nutrients: average of N/P/K, then split 40/60 into fast/slow pools
        n = soil_config.get("nitrogen", DEFAULT_VALUE)
        p = soil_config.get("phosphorus", DEFAULT_VALUE)
        k = soil_config.get("potassium", DEFAULT_VALUE)
        base_nutrients = (n + p + k) / 3.0
        fast_val = base_nutrients * 0.4   # immediately available
        slow_val = base_nutrients * 0.6   # long-term reserve
        for layer, val in (("nutrients_fast", fast_val), ("nutrients_slow", slow_val)):
            if abs(val - DEFAULT_VALUE) > DIRTY_THRESHOLD:
                for x in range(dx):
                    for y in range(dy):
                        for z in range(dz):
                            self._data[layer][(x, y, z)] = val

        # Moisture and organic_matter: direct from soil config
        for soil_key, layer in (("moisture", "moisture"), ("organic_matter", "organic_matter")):
            val = soil_config.get(soil_key)
            if val is not None and abs(val - DEFAULT_VALUE) > DIRTY_THRESHOLD:
                for x in range(dx):
                    for y in range(dy):
                        for z in range(dz):
                            self._data[layer][(x, y, z)] = val


# Backward-compatible alias so existing imports still work.
class VoxelManager(UniformVoxelGrid):  # noqa: F821
    """Alias for UniformVoxelGrid (backward compatibility)."""
    pass
