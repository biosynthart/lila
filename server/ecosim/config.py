# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
l\u012bl\u0101 Simulation Configuration Loader

Loads tunable parameters from ``sim_config.json`` and exposes them as a
nested dict. This replaces hardcoded magic numbers scattered across flow
actors, guard actors, engine, interactions, movement, and world processes.

Usage::

    from .config import SIM_CONFIG

    # Access nested values:
    temp_norm = SIM_CONFIG["consumer_physiology"]["temperature_normalization"]
    swarm_exit = SIM_CONFIG["consumer_physiology"]["colony_swarm_exit_threshold"]

All keys have defaults baked in so the simulation runs without an external
JSON file. Drop a custom ``sim_config.json`` alongside this module to override.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

# ── Default configuration (global values only; biome-dependent values live in BiomeConfig) ──
_DEFAULT_CONFIG: dict[str, Any] = {
    "consumer_physiology": {
        "temperature_normalization": 30.0,
        "near_water_distance_buffer": 1.0,
        "water_drain_search_multiplier": 2.0,
        "colony_swarm_exit_threshold": 0.35,
        "colony_swarm_entry_threshold": 0.3,
    },
    "plant_physiology": {
        "spread_offspring_growth": 0.05,
        "spread_offspring_hydration_factor": 0.8,
        "spread_offspring_nutrient_store": 0.3,
        "spread_offspring_health": 0.8,
        "evapotranspiration_temp_normalization": 30.0,
        "slow_nutrient_weight_factor": 0.3,
        "dormancy_recovery_hydration_floor": 0.02,
    },
    "soil_dynamics": {
        "nutrient_diffusion_enabled": False,
    },
    "decomposer_physiology": {
        "active_population_threshold": 0.3,
        "blooming_organic_matter_threshold": 0.8,
        "blooming_population_threshold": 0.7,
        "dormant_activity_threshold": 0.2,
    },
    "movement": {
        "grid_max_default": 31.0,
        "mate_minimum_distance": 1.0,
        "food_growth_viability_threshold": 0.1,
        "water_source_min_distance": 0.1,
        "water_approach_radius_factor": 0.5,
        "wander_grid_margin": 0.5,
        "arrival_threshold_double": 2.0,
    },
    "reproduction": {
        "colony_health_repro_cost_factor": 0.3,
    },
    "interactions": {
        "mass_ratio_windows": {
            "carnivore": (0.1, 2.0),
            "insectivore": (1.0, 1000.0),
            "omnivore": (0.1, 100.0),
            "piscivore": (0.01, 10.0),
        },
        "pollination_linger_base": 20.0,
        "pollination_cooldown_ticks": 50,
        "herbivory_consumption_multiplier": 0.05,
        "predation_consumption_multiplier": 0.1,
        "capture_probability_cap": 0.95,
        "capture_probability_base_offset": 0.2,
        "capture_probability_fallback": 0.5,
        "pollination_metabolic_rate_floor": 0.001,
        "pollination_linger_exponent": 0.3,
        "decomposition_boost_min": 0.5,
        "decomposition_boost_scale": 5.0,
        "speed_coefficients": {
            "flight_insect": (0.60, 0.17),
            "flight_bird": (0.55, 0.17),
            "quadruped": (0.0502, 0.25),
        },
    },
    "engine_defaults": {
        "default_dt": 2.0,   # 0.5 Hz — server is state oracle; client handles behavioral continuity
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*."""
    merged = base.copy()
    for key, value in override.items():
        if key.startswith("_"):
            continue  # skip comments / metadata keys
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_sim_config(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """Load simulation config from JSON, falling back to defaults.

    Args:
        path: Path to a custom sim_config.json. If ``None``, looks for
              ``sim_config.json`` next to this module file.

    Returns:
        Merged configuration dict (defaults overridden by file values).
    """
    config = _DEFAULT_CONFIG.copy()

    if path is None:
        # Resolve relative to server/ root, not the ecosim package dir
        path = pathlib.Path(__file__).resolve().parent.parent / "config" / "sim_config.json"

    json_path = pathlib.Path(path)
    if json_path.is_file():
        with open(json_path) as f:
            file_config = json.load(f)
        config = _deep_merge(config, file_config)

    return config


# Module-level singleton — loaded once at import time.
SIM_CONFIG: dict[str, Any] = load_sim_config()
