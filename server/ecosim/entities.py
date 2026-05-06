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
Entity schema definitions for the līlā ecosystem.

Handles initialization of state variables from entity metadata and
provides type-safe access patterns. Each entity is a plain dict at
the protocol level (JSON-serializable), but this module provides
factory functions that guarantee correct initial state for each type.
"""

from __future__ import annotations

from typing import Any

# -- Discrete states by entity type ------------------------------------------

ANIMAL_STATES = frozenset({
    "IDLE", "FORAGING", "HUNTING", "FLEEING",
    "RESTING", "DRINKING", "REPRODUCING", "DYING",
})

BIRD_STATES = ANIMAL_STATES  # Same behavioral repertoire for now

PLANT_STATES = frozenset({
    "GROWING", "WILTING", "DORMANT", "FRUITING", "DEAD",
})

TREE_STATES = PLANT_STATES

INSECT_STATES = frozenset({
    "IDLE", "FORAGING", "RESTING", "POLLINATING",
    "SWARMING", "REPRODUCING", "DORMANT", "DEAD",
})

MICROORGANISM_STATES = frozenset({
    "ACTIVE", "DORMANT", "BLOOMING",
})

VALID_STATES: dict[str, frozenset[str]] = {
    "ANIMAL": ANIMAL_STATES,
    "BIRD": BIRD_STATES,
    "PLANT": PLANT_STATES,
    "TREE": TREE_STATES,
    "INSECT": INSECT_STATES,
    "MICROORGANISM": MICROORGANISM_STATES,
}


# -- Default state variables per entity type ---------------------------------

_ANIMAL_DEFAULTS: dict[str, float] = {
    "hunger": 0.0,
    "energy": 1.0,
    "hydration": 1.0,
    "age": 0.0,
    "health": 1.0,
    "reproductive_drive": 0.0,
}

_PLANT_DEFAULTS: dict[str, float] = {
    "hydration": 1.0,
    "growth": 0.1,
    "nutrient_store": 0.5,
    "health": 1.0,
    "age": 0.0,
}

_INSECT_DEFAULTS: dict[str, float] = {
    "hunger": 0.0,
    "energy": 1.0,
    "colony_health": 1.0,
    "age": 0.0,
    "reproductive_drive": 0.0,
}

_MICROORGANISM_DEFAULTS: dict[str, float] = {
    "population": 0.5,
    "activity": 0.5,
}

_STATE_VAR_DEFAULTS: dict[str, dict[str, float]] = {
    "ANIMAL": _ANIMAL_DEFAULTS,
    "BIRD": _ANIMAL_DEFAULTS,
    "PLANT": _PLANT_DEFAULTS,
    "TREE": _PLANT_DEFAULTS,
    "INSECT": _INSECT_DEFAULTS,
    "MICROORGANISM": _MICROORGANISM_DEFAULTS,
}

_INITIAL_STATES: dict[str, str] = {
    "ANIMAL": "IDLE",
    "BIRD": "IDLE",
    "PLANT": "GROWING",
    "TREE": "GROWING",
    "INSECT": "IDLE",
    "MICROORGANISM": "ACTIVE",
}


# -- Entity initialization ---------------------------------------------------

def init_entity(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Take a raw entity dict from the world definition and ensure it has
    all required runtime fields: state, state_vars, velocity, and any
    missing metadata defaults.

    Mutates and returns the same dict (no copy) for efficiency.
    """
    entity_type = raw["type"]

    # Set initial discrete state if not provided
    if "state" not in raw:
        raw["state"] = _INITIAL_STATES.get(entity_type, "IDLE")

    # Merge default state variables with any overrides from the world def
    defaults = _STATE_VAR_DEFAULTS.get(entity_type, {})
    existing = raw.get("state_vars", {})
    raw["state_vars"] = {**defaults, **existing}

    # Ensure position and velocity exist
    if "position" not in raw:
        raw["position"] = [0.0, 0.0, 0.0]
    if "velocity" not in raw:
        raw["velocity"] = [0.0, 0.0, 0.0]

    # Ensure metadata exists
    if "metadata" not in raw:
        raw["metadata"] = {}

    return raw


def is_mobile(entity: dict[str, Any]) -> bool:
    """Whether this entity type can move through the world."""
    return entity["type"] in ("ANIMAL", "BIRD", "INSECT")


def is_alive(entity: dict[str, Any]) -> bool:
    """Whether this entity is still participating in the simulation."""
    return entity["state"] not in ("DEAD", "DYING")
