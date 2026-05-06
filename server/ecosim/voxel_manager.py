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

Tracks four environmental layers (nutrients, moisture, temperature,
organic_matter) over a 3D grid. Only voxels that change beyond a
threshold are flagged as dirty and included in the next tick packet.

Grid coordinates are integer tuples (x, y, z). Layers are stored as
flat dicts keyed by coordinate tuple for O(1) access. The default
value for any unset voxel is 1.0 (fully saturated).
"""

from __future__ import annotations

LAYERS = ("nutrients", "moisture", "temperature", "organic_matter")
DEFAULT_VALUE = 1.0
DIRTY_THRESHOLD = 0.05


class VoxelManager:
    """
    Manages a sparse 3D grid with multiple named layers.

    Each layer is a dict[(int,int,int) -> float]. Only coordinates
    that differ from the default or have been explicitly set are stored.
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
        """Read a voxel value. Returns DEFAULT_VALUE for unset voxels."""
        return self._data[layer].get((x, y, z), DEFAULT_VALUE)

    def set(
        self, layer: str, x: int, y: int, z: int, value: float,
    ) -> None:
        """
        Set a voxel value, clamped to [0.0, 1.0].
        Marks the voxel as dirty if the change exceeds DIRTY_THRESHOLD.
        """
        coord = (x, y, z)
        value = max(0.0, min(1.0, value))
        old = self._data[layer].get(coord, DEFAULT_VALUE)

        if abs(old - value) > DIRTY_THRESHOLD:
            self._data[layer][coord] = value
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

    def initialize_from_soil(self, soil_config: dict[str, float]) -> None:
        """
        Set uniform initial values across the grid from the world
        definition's soil parameters. Only sets non-default values
        to keep the sparse representation efficient.
        """
        dx, dy, dz = self.dimensions

        # Nutrients: average of N/P/K
        n = soil_config.get("nitrogen", DEFAULT_VALUE)
        p = soil_config.get("phosphorus", DEFAULT_VALUE)
        k = soil_config.get("potassium", DEFAULT_VALUE)
        nutrient_val = (n + p + k) / 3.0
        if abs(nutrient_val - DEFAULT_VALUE) > DIRTY_THRESHOLD:
            for x in range(dx):
                for y in range(dy):
                    for z in range(dz):
                        self._data["nutrients"][(x, y, z)] = nutrient_val

        # Moisture and organic_matter: direct from soil config
        for soil_key, layer in (("moisture", "moisture"), ("organic_matter", "organic_matter")):
            val = soil_config.get(soil_key)
            if val is not None and abs(val - DEFAULT_VALUE) > DIRTY_THRESHOLD:
                for x in range(dx):
                    for y in range(dy):
                        for z in range(dz):
                            self._data[layer][(x, y, z)] = val
