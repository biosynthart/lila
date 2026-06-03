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

"""θ parameterization for ASAL search over līlā simulations.

Defines the searchable parameter space and provides theta_to_world_config()
which converts a flat numpy vector into a valid world JSON dict that
EcosystemEngine can load.

Current scope (Track A): ~18 dimensions covering rate multipliers, biome
base values, water source configuration, and entity count scaling.
This searches over "interesting tunings of the same five species."

When the trait system lands, this module expands to encode trait vectors
(body mass, diet, thermal tolerance) without changing the substrate protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ThetaDim:
    """A single dimension of the search space."""
    name: str
    low: float
    high: float
    default: float


@dataclass
class ThetaSpec:
    """Describes the full parameter space: names, ranges, defaults."""
    dims: list[ThetaDim] = field(default_factory=list)

    @property
    def ndim(self) -> int:
        return len(self.dims)

    @property
    def names(self) -> list[str]:
        return [d.name for d in self.dims]

    @property
    def bounds(self) -> np.ndarray:
        """Shape (ndim, 2) — lower and upper bounds."""
        return np.array([[d.low, d.high] for d in self.dims])

    @property
    def defaults(self) -> np.ndarray:
        return np.array([d.default for d in self.dims])

    def clip(self, theta: np.ndarray) -> np.ndarray:
        """Clip theta to valid bounds."""
        b = self.bounds
        return np.clip(theta, b[:, 0], b[:, 1])

    def sample_uniform(self, rng: np.random.Generator | None = None) -> np.ndarray:
        """Sample a random theta uniformly within bounds."""
        if rng is None:
            rng = np.random.default_rng()
        b = self.bounds
        return rng.uniform(b[:, 0], b[:, 1])


# ---------------------------------------------------------------------------
# EcoRates θ spec — the Track A parameter space
# ---------------------------------------------------------------------------

def make_eco_rates_spec() -> ThetaSpec:
    """~18-dimensional parameter space over rate multipliers, biome, and water.

    Dimensions:
        0–5:   Rate multipliers (consumption, hunger, thirst, growth,
               reproduction, water_replenishment). Range [0.2, 5.0].
        6–8:   Biome base values (soil_nutrients, soil_moisture,
               soil_temperature). Range [0.05, 1.0].
        9:     Water source count. Range [1, 4] (rounded to int).
        10:    Water source mean radius. Range [1.0, 5.0].
        11:    Water source mean water_level. Range [0.3, 1.0].
        12:    Deer count. Range [2, 8] (rounded to int).
        13:    Butterfly count. Range [2, 8] (rounded to int).
        14:    Oak count. Range [1, 5] (rounded to int).
        15:    Grass count. Range [3, 15] (rounded to int).
        16:    Wildflower count. Range [2, 10] (rounded to int).
        17:    Rain interval (ticks between rain events, 0=no rain).
               Range [0, 2000].
    """
    dims = [
        # Rate multipliers
        ThetaDim("rate_consumption",       0.2, 5.0, 1.0),
        ThetaDim("rate_hunger",            0.2, 5.0, 1.0),
        ThetaDim("rate_thirst",            0.2, 5.0, 1.0),
        ThetaDim("rate_growth",            0.2, 5.0, 1.0),
        ThetaDim("rate_reproduction",      0.2, 5.0, 1.0),
        ThetaDim("rate_water_replenishment", 0.2, 5.0, 1.0),
        # Biome base values
        ThetaDim("soil_nitrogen",          0.1, 1.0, 0.7),
        ThetaDim("soil_moisture",          0.1, 1.0, 0.65),
        ThetaDim("climate_temperature",    10.0, 40.0, 22.0),
        # Water sources
        ThetaDim("water_count",            1.0,  4.0, 2.0),
        ThetaDim("water_radius",           1.0,  5.0, 3.0),
        # Entity counts
        ThetaDim("deer_count",             2.0,  8.0, 4.0),
        ThetaDim("butterfly_count",        2.0,  8.0, 4.0),
        ThetaDim("oak_count",              1.0,  5.0, 2.0),
        ThetaDim("grass_count",            3.0, 15.0, 8.0),
        ThetaDim("wildflower_count",       2.0, 10.0, 5.0),
        # Rain
        ThetaDim("rain_interval",          0.0, 2000.0, 500.0),
    ]
    return ThetaSpec(dims=dims)


# ---------------------------------------------------------------------------
# θ → world config conversion
# ---------------------------------------------------------------------------

# Default grid size and species templates
_GRID = 32


def _spread_positions(count: int, grid_size: int, rng: np.random.Generator) -> list[list[float]]:
    """Generate spread-out positions for entities on the grid.

    Returns list of [x, y, z] arrays matching demo_world.json format.
    """
    positions = []
    for _ in range(count):
        x = float(rng.uniform(1, grid_size - 1))
        z = float(rng.uniform(1, grid_size - 1))
        positions.append([x, 0.0, z])
    return positions


def _water_positions(count: int, grid_size: int, rng: np.random.Generator) -> list[list[float]]:
    """Generate water source positions spread across the grid."""
    positions = []
    quadrants = [
        (0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75),
    ]
    for i in range(count):
        qx, qz = quadrants[i % len(quadrants)]
        jitter_x = rng.uniform(-0.15, 0.15)
        jitter_z = rng.uniform(-0.15, 0.15)
        x = float(np.clip((qx + jitter_x) * grid_size, 2, grid_size - 2))
        z = float(np.clip((qz + jitter_z) * grid_size, 2, grid_size - 2))
        positions.append([x, 0.0, z])
    return positions


# ---------------------------------------------------------------------------
# Species trait definitions (from demo_world.json)
# ---------------------------------------------------------------------------

_SPECIES_DEFINITIONS = [
    {
        "species_id": "deer",
        "functional_group": "herbivore",
        "entity_class": "ANIMAL",
        "body_mass_kg": 80.0,
        "locomotion": "quadruped",
        "skeleton_id": "quadruped_medium",
        "thermoregulation": "endotherm",
        "diet_type": "herbivore",
        "diet_breadth": ["graminoid", "forb"],
        "trophic_level": 2.0,
        "reproductive_strategy": "K_selected",
        "clutch_size": 1,
        "generation_time_ticks": 5000,
        "thermal_range": [0, 40],
        "drought_tolerance": 0.3,
        "shade_tolerance": 0.3,
        "sensory_range_multiplier": 1.0,
        "movement_budget": 0.4,
        "resource_tags": [],
    },
    {
        "species_id": "monarch",
        "functional_group": "pollinator",
        "entity_class": "INSECT",
        "body_mass_kg": 0.0005,
        "locomotion": "flight_insect",
        "skeleton_id": "insect_wing",
        "thermoregulation": "ectotherm",
        "diet_type": "nectarivore",
        "diet_breadth": ["forb:fruiting"],
        "trophic_level": 2.0,
        "reproductive_strategy": "r_selected",
        "clutch_size": 2,
        "generation_time_ticks": 2000,
        "thermal_range": [10, 35],
        "drought_tolerance": 0.1,
        "shade_tolerance": 0.5,
        "sensory_range_multiplier": 1.2,
        "movement_budget": 0.6,
        "resource_tags": [],
        "floral_affinity": ["insect_generalist"],
    },
    {
        "species_id": "meadow_oak",
        "functional_group": "producer",
        "entity_class": "TREE",
        "body_mass_kg": 5000.0,
        "locomotion": "rooted",
        "skeleton_id": None,
        "thermoregulation": "autotroph",
        "diet_type": "autotroph",
        "diet_breadth": [],
        "trophic_level": 1.0,
        "reproductive_strategy": "K_selected",
        "clutch_size": 1,
        "generation_time_ticks": 20000,
        "thermal_range": [-10, 40],
        "drought_tolerance": 0.5,
        "shade_tolerance": 0.2,
        "sensory_range_multiplier": 0.0,
        "movement_budget": 0.0,
        "canopy_radius": 3.0,
        "root_persistence": True,
        "resource_tags": ["mast"],
    },
    {
        "species_id": "meadow_grass",
        "functional_group": "producer",
        "entity_class": "PLANT",
        "body_mass_kg": 0.01,
        "locomotion": "sessile",
        "skeleton_id": None,
        "thermoregulation": "autotroph",
        "diet_type": "autotroph",
        "diet_breadth": [],
        "trophic_level": 1.0,
        "reproductive_strategy": "r_selected",
        "clutch_size": 2,
        "generation_time_ticks": 500,
        "thermal_range": [5, 35],
        "drought_tolerance": 0.2,
        "shade_tolerance": 0.4,
        "sensory_range_multiplier": 0.0,
        "movement_budget": 0.0,
        "spread_mode": "runner",
        "spread_range": 2.0,
        "spread_chance": 0.008,
        "spread_cooldown": 80,
        "root_persistence": True,
        "resource_tags": ["graminoid"],
    },
    {
        "species_id": "wildflower",
        "functional_group": "producer",
        "entity_class": "PLANT",
        "body_mass_kg": 0.05,
        "locomotion": "sessile",
        "skeleton_id": None,
        "thermoregulation": "autotroph",
        "diet_type": "autotroph",
        "diet_breadth": [],
        "trophic_level": 1.0,
        "reproductive_strategy": "r_selected",
        "clutch_size": 1,
        "generation_time_ticks": 800,
        "thermal_range": [5, 35],
        "drought_tolerance": 0.15,
        "shade_tolerance": 0.3,
        "sensory_range_multiplier": 0.0,
        "movement_budget": 0.0,
        "spread_mode": "runner",
        "spread_range": 3.5,
        "spread_chance": 0.005,
        "spread_cooldown": 120,
        "root_persistence": True,
        "resource_tags": ["forb"],
        "pollination_syndrome": "insect_generalist",
    },
]

# Legacy entity metadata (kept for backward compat with renderer)
_DEER_METADATA = {
    "diet": "herbivore", "body_mass": 60.0, "metabolism_rate": 1.0,
    "sensory_range": 12.0, "movement_speed": 3.0,
    "lifespan": 800.0, "reproduction_threshold": 0.8,
}

_BUTTERFLY_METADATA = {
    "diet": "herbivore", "colony_size": 1, "metabolism_rate": 0.6,
    "pollination_range": 6.0, "movement_speed": 2.0, "lifespan": 150.0,
}

_OAK_METADATA = {
    "metabolism": "photosynthetic", "growth_rate": 0.005, "root_depth": 2.0,
    "canopy_radius": 4.0, "height_max": 12.0, "trunk_radius": 0.6,
    "shade_factor": 0.35,
    "nutrient_demand": {"nitrogen": 0.02, "phosphorus": 0.01},
    "water_demand": 0.05,
}

_GRASS_METADATA = {
    "metabolism": "photosynthetic", "growth_rate": 0.06, "root_depth": 0.1,
    "canopy_radius": 0.0,
    "nutrient_demand": {"nitrogen": 0.005, "phosphorus": 0.002},
    "water_demand": 0.02,
}

_WILDFLOWER_METADATA = {
    "metabolism": "photosynthetic", "growth_rate": 0.09, "root_depth": 0.15,
    "canopy_radius": 0.0,
    "nutrient_demand": {"nitrogen": 0.008, "phosphorus": 0.004},
    "water_demand": 0.025,
}


def theta_to_world_config(theta: np.ndarray, seed: int = 0) -> dict:
    """Convert a flat θ vector into a valid world config dict.

    The output format matches demo_world.json — the dict that
    EcosystemEngine.__init__() expects.

    Parameters
    ----------
    theta : np.ndarray
        Parameter vector of length matching make_eco_rates_spec().ndim.
    seed : int
        Random seed for entity/water placement.

    Returns
    -------
    dict
        World configuration ready for EcosystemEngine(config).
    """
    rng = np.random.default_rng(seed)

    # Unpack theta
    t = dict(zip(make_eco_rates_spec().names, theta))

    # Integer counts
    n_water = int(round(t["water_count"]))
    n_deer = int(round(t["deer_count"]))
    n_butterfly = int(round(t["butterfly_count"]))
    n_oak = int(round(t["oak_count"]))
    n_grass = int(round(t["grass_count"]))
    n_wildflower = int(round(t["wildflower_count"]))
    rain_interval = int(round(t["rain_interval"]))

    # Water sources — position as [x, y, z] array
    water_sources = []
    for pos in _water_positions(n_water, _GRID, rng):
        water_sources.append({
            "position": pos,
            "radius": float(t["water_radius"]),
        })

    # Build entity list
    entities = []

    # Deer
    for i, pos in enumerate(_spread_positions(n_deer, _GRID, rng)):
        entities.append({
            "id": f"deer_{i:02d}",
            "type": "ANIMAL",
            "species": "deer",
            "position": pos,
            "metadata": {**_DEER_METADATA},
            "skeleton_id": "quadruped_medium",
        })

    # Butterflies
    for i, pos in enumerate(_spread_positions(n_butterfly, _GRID, rng)):
        entities.append({
            "id": f"butterfly_{i:02d}",
            "type": "INSECT",
            "species": "monarch",
            "position": pos,
            "metadata": {**_BUTTERFLY_METADATA},
            "skeleton_id": "insect_wing",
        })

    # Oaks
    for i, pos in enumerate(_spread_positions(n_oak, _GRID, rng)):
        entities.append({
            "id": f"oak_{i:02d}",
            "type": "TREE",
            "species": "meadow_oak",
            "position": pos,
            "metadata": {**_OAK_METADATA},
        })

    # Grass
    for i, pos in enumerate(_spread_positions(n_grass, _GRID, rng)):
        entities.append({
            "id": f"grass_{i:02d}",
            "type": "PLANT",
            "species": "meadow_grass",
            "position": pos,
            "metadata": {**_GRASS_METADATA},
        })

    # Wildflowers
    for i, pos in enumerate(_spread_positions(n_wildflower, _GRID, rng)):
        entities.append({
            "id": f"flower_{i:02d}",
            "type": "PLANT",
            "species": "wildflower",
            "position": pos,
            "metadata": {**_WILDFLOWER_METADATA},
        })

    config = {
        "version": "0.1",
        "session_id": f"search-{seed:06d}",

        "environment": {
            "type": "MEADOW",
            "biome": "TEMPERATE",
            "climate": {
                "temperature": float(t["climate_temperature"]),
                "humidity": 0.6,
                "rainfall": 0.4,
                "wind_speed": 0.15,
                "light_level": 0.85,
            },
            "soil": {
                "nitrogen": float(t["soil_nitrogen"]),
                "phosphorus": 0.6,
                "potassium": 0.5,
                "moisture": float(t["soil_moisture"]),
                "organic_matter": 0.4,
                "ph": 6.8,
            },
            "voxel_grid": {
                "dimensions": [_GRID, _GRID, _GRID],
                "cell_size": 1.0,
            },
            "water_sources": water_sources,
        },

        "model": {
            "adapter": "static",
        },

        "rates": {
            "consumption": float(t["rate_consumption"]),
            "hunger": float(t["rate_hunger"]),
            "thirst": float(t["rate_thirst"]),
            "growth": float(t["rate_growth"]),
            "reproduction": float(t["rate_reproduction"]),
            "water_replenishment": float(t["rate_water_replenishment"]),
        },

        "entities": entities,

        "species_definitions": _SPECIES_DEFINITIONS,
    }

    # Randomization — opt-in, use for position jitter
    config["randomize"] = {
        "jitter": 1.5,
        "extra_grass": [0, 2],
        "extra_flowers": [0, 1],
        "transform": True,
    }

    # Rain config — read by substrate protocol, not part of engine init
    # 0 means no rain
    if rain_interval > 0:
        config["rain"] = {
            "interval": rain_interval,
            "intensity": 0.8,
        }

    return config
