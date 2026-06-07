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
Biome configuration for the l\u012bl\u0101 ecosystem simulation.

Maps biome identifiers to concrete simulation constants that drive the
hybrid automaton's flow equations. Each biome defines environmental
modifiers that affect metabolism, evaporation, growth rates, and
state transition thresholds.

Preset values are loaded from ``biomes.json`` alongside this module.
Custom biomes can be registered at runtime via ``register_biome()``.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass


@dataclass(frozen=True)
class BiomeConfig:
    """Immutable set of simulation constants derived from a biome type."""

    # ── Metabolism modifiers (multiplied against base entity rates) ────
    hunger_rate_modifier: float = 1.0
    energy_drain_modifier: float = 1.0

    # ── Water cycle ────────────────────────────────────────────────────
    evaporation_rate: float = 0.05
    rainfall_recharge: float = 0.02

    # ── Plant growth ───────────────────────────────────────────────────
    growth_rate_modifier: float = 1.0
    light_availability: float = 0.8

    # ── Soil dynamics ──────────────────────────────────────────────────
    decomposition_rate: float = 0.01
    nutrient_diffusion_rate: float = 0.005

    # ── Microorganism activity ─────────────────────────────────────────
    microbial_activity_modifier: float = 1.0

    # ── Temperature effect on all metabolic processes ──────────────────
    # Values > 1.0 speed things up, < 1.0 slow them down
    metabolic_scaling: float = 1.0

    # ═══════════════════════════════════════════════════════════════════
    # Environment-dependent thresholds (vary by biome)
    # ═══════════════════════════════════════════════════════════════════

    # Consumer dormancy: soil moisture level that wakes dormant consumers.
    # Desert organisms need more moisture to wake; arctic less.
    dormant_consumer_moisture_wake: float = 0.25

    # Plant dormancy recovery multipliers — how fast health/hydration rebuilds
    # when soil conditions improve. Tropical recovers quickly, arctic slowly.
    plant_dormancy_recovery_health_multiplier: float = 10.0
    plant_dormancy_recovery_hydration_multiplier: float = 13.0
    plant_dormancy_recovery_health_floor: float = 0.015

    # Decomposer dynamics — cold biomes have slower activity response and
    # lower population growth rates.
    decomposer_activity_smoothing_factor: float = 0.1
    decomposer_population_growth_rate: float = 0.005
    decomposer_population_decay_rate: float = 0.003

    # Water physics outside source footprint — desert soil dries much faster
    # and has a lower natural moisture floor.
    soil_dry_rate_outside_footprint: float = 0.02
    soil_moisture_floor_outside_water: float = 0.3


# ── Default fallback (TEMPERATE-equivalent) ────────────────────────────────
_DEFAULT_BIOME = BiomeConfig()

# Runtime registry — populated from biomes.json at module load time, plus any
# custom biomes registered programmatically.
_BIOME_REGISTRY: dict[str, BiomeConfig] = {}


def _load_builtin_biomes() -> None:
    """Load biome presets from biomes.json into the registry."""
    # Resolve relative to server/ root, not the ecosim package dir
    json_path = pathlib.Path(__file__).resolve().parent.parent / "config" / "biomes.json"
    if not json_path.is_file():
        # Fall back to hardcoded TEMPERATE default only.
        _BIOME_REGISTRY["TEMPERATE"] = _DEFAULT_BIOME
        return

    with open(json_path) as f:
        data = json.load(f)

    for name, values in data.items():
        if name.startswith("_"):
            continue  # skip comments / metadata keys
        _BIOME_REGISTRY[name.upper()] = BiomeConfig(**values)


# Populate registry at import time.
_load_builtin_biomes()


def register_biome(name: str, config: BiomeConfig) -> None:
    """Register a custom biome preset at runtime.

    Args:
        name: Biome identifier (case-insensitive).
        config: BiomeConfig instance with the desired parameters.
    """
    _BIOME_REGISTRY[name.upper()] = config


def get_biome_config(biome_name: str) -> BiomeConfig:
    """Look up a biome preset, falling back to defaults for unknown biomes."""
    return _BIOME_REGISTRY.get(biome_name.upper(), _DEFAULT_BIOME)
