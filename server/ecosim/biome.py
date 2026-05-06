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
Biome configuration for the līlā ecosystem simulation.

Maps biome identifiers to concrete simulation constants that drive the
hybrid automaton's flow equations. Each biome defines environmental
modifiers that affect metabolism, evaporation, growth rates, and
state transition thresholds.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BiomeConfig:
    """Immutable set of simulation constants derived from a biome type."""

    # Metabolism modifiers (multiplied against base entity rates)
    hunger_rate_modifier: float = 1.0
    energy_drain_modifier: float = 1.0

    # Water cycle
    evaporation_rate: float = 0.05
    rainfall_recharge: float = 0.02

    # Plant growth
    growth_rate_modifier: float = 1.0
    light_availability: float = 0.8

    # Soil dynamics
    decomposition_rate: float = 0.01
    nutrient_diffusion_rate: float = 0.005

    # Microorganism activity
    microbial_activity_modifier: float = 1.0

    # Temperature effect on all metabolic processes
    # Values > 1.0 speed things up, < 1.0 slow them down
    metabolic_scaling: float = 1.0


# Biome presets keyed by the biome string from the world definition.
# These are intentionally tuned to produce visibly different ecosystem
# behaviors — a tropical forest should feel lush and fast, an arctic
# one should feel slow and punishing.

BIOME_PRESETS: dict[str, BiomeConfig] = {
    "TROPICAL": BiomeConfig(
        hunger_rate_modifier=1.0,
        energy_drain_modifier=0.8,
        evaporation_rate=0.06,
        rainfall_recharge=0.04,
        growth_rate_modifier=1.5,
        light_availability=0.9,
        decomposition_rate=0.02,
        nutrient_diffusion_rate=0.008,
        microbial_activity_modifier=1.4,
        metabolic_scaling=1.2,
    ),
    "TEMPERATE": BiomeConfig(
        hunger_rate_modifier=1.0,
        energy_drain_modifier=1.0,
        evaporation_rate=0.04,
        rainfall_recharge=0.025,
        growth_rate_modifier=1.0,
        light_availability=0.7,
        decomposition_rate=0.01,
        nutrient_diffusion_rate=0.005,
        microbial_activity_modifier=1.0,
        metabolic_scaling=1.0,
    ),
    "ARCTIC": BiomeConfig(
        hunger_rate_modifier=1.6,
        energy_drain_modifier=1.5,
        evaporation_rate=0.015,
        rainfall_recharge=0.005,
        growth_rate_modifier=0.3,
        light_availability=0.4,
        decomposition_rate=0.003,
        nutrient_diffusion_rate=0.002,
        microbial_activity_modifier=0.3,
        metabolic_scaling=0.6,
    ),
    "DESERT": BiomeConfig(
        hunger_rate_modifier=1.3,
        energy_drain_modifier=1.2,
        evaporation_rate=0.12,
        rainfall_recharge=0.002,
        growth_rate_modifier=0.4,
        light_availability=1.0,
        decomposition_rate=0.005,
        nutrient_diffusion_rate=0.003,
        microbial_activity_modifier=0.4,
        metabolic_scaling=1.1,
    ),
}


def get_biome_config(biome: str) -> BiomeConfig:
    """Look up a biome preset, falling back to TEMPERATE for unknown biomes."""
    return BIOME_PRESETS.get(biome.upper(), BIOME_PRESETS["TEMPERATE"])
