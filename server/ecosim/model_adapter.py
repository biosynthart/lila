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
Model adapter protocol for the Lila ecosystem engine.

Lila is a BYOM (Bring Your Own Model) framework. The simulation engine
handles physics, ecology, and state machines. Models handle intelligence.
This module defines the socket where models plug in.

## Model Levels

Models operate at one of three levels of abstraction. Each level has a
different tick cadence and a different I/O contract.

### MOTOR (implemented)
    Per-entity, every tick.
    Input:  entity context (state vars, metadata, biome)
    Output: motion latent vector (drives animation style)

    The client's motion retargeter maps latent vectors to bone transforms.
    Without a motor model, entities still move (the automaton handles
    position/velocity), they just animate with a fixed default pose blend.

### BEHAVIOR (not yet implemented)
    Per-entity, every tick.
    Input:  entity context + nearby entity summary
    Output: state bias vector (influences guard condition evaluation)

    Currently, guard conditions are deterministic (hunger >= 0.3 → FORAGING).
    A behavior model could learn softer decision boundaries from data —
    when SHOULD a deer forage, given its full context? This level would
    bias or override the hardcoded thresholds without replacing the
    automaton structure.

### NARRATIVE (not yet implemented)
    Per-ecosystem, every N ticks.
    Input:  whole ecosystem snapshot (populations, spatial distribution,
            resource levels, recent event history)
    Output: event injection, goal setting, or parameter modulation

    Operates at a slower cadence to shape emergent dynamics over longer
    time horizons. Could enforce ecological plausibility (populations
    shouldn't crash this fast), create spatial structure (predator
    territories should space out), or modulate biome parameters to
    produce natural-feeling rhythms. The key constraint: narrative
    intelligence should be invisible. It makes the world feel more
    alive, not more scripted.

## Adapter Registration

The engine discovers adapters by level. Multiple adapters can coexist
at different levels. Within a level, only one adapter is active.

    engine = EcosystemEngine(world_config, adapters={
        "motor": MlpMotorAdapter(weights="weights/motion_v0.json"),
    })

If no adapter is registered for a level, the engine uses a sensible
default (static latents for motor, deterministic guards for behavior,
no intervention for narrative).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Context specification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextField:
    """A single named input the model expects in its context vector."""
    name: str
    source: str       # "state_var", "metadata", "climate", "biome", "derived"
    normalize: float = 1.0  # divide raw value by this to get [0, 1] range
    default: float = 0.0    # value when source field is missing


@dataclass(frozen=True)
class ContextSpec:
    """
    Declares what inputs a model needs and in what order.

    The engine uses this to assemble context vectors from entity state,
    biome config, and climate. The model never touches engine internals
    directly — it receives a flat float vector built to its spec.
    """
    fields: tuple[ContextField, ...]
    latent_dim: int = 4

    @property
    def input_dim(self) -> int:
        return len(self.fields)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class MotorAdapter(Protocol):
    """
    Motor-level model adapter.

    Maps per-entity context vectors to motion latent vectors every tick.
    The latent encodes movement *style*, not direction or speed.
    """

    def context_spec(self) -> ContextSpec:
        """Declare the input features this model expects."""
        ...

    def context_spec_for(self, entity_type: str) -> ContextSpec:
        """
        Type-specific context spec. Override to provide different input
        features for different entity types (e.g., ANIMAL vs INSECT).

        Default behavior: delegates to context_spec(), ignoring entity_type.
        The engine checks for this method via hasattr and falls back to
        context_spec() if not present.
        """
        ...

    def infer(self, contexts: list[list[float]]) -> list[list[float]]:
        """
        Batch inference: list of context vectors → list of latent vectors.

        Batch size equals the number of skeleton entities alive this tick.
        Each inner list has length == context_spec().input_dim (input) or
        context_spec().latent_dim (output).
        """
        ...


# Placeholder protocols for future levels. Defined here so the hierarchy
# is visible in one place, but not yet used by the engine.

@runtime_checkable
class BehaviorAdapter(Protocol):
    """
    Behavior-level model adapter (NOT YET IMPLEMENTED).

    Per-entity, per-tick. Produces a bias vector that influences
    guard condition evaluation in the hybrid automaton.
    """

    def context_spec(self) -> ContextSpec:
        ...

    def infer_bias(
        self,
        contexts: list[list[float]],
        neighbor_summaries: list[dict[str, Any]],
    ) -> list[dict[str, float]]:
        """
        Returns per-entity bias dicts, e.g.:
        {"hunger_threshold": -0.05, "flee_sensitivity": 0.1}
        """
        ...


@runtime_checkable
class NarrativeAdapter(Protocol):
    """
    Narrative-level model adapter (NOT YET IMPLEMENTED).

    Per-ecosystem, periodic. Observes macro state and can inject
    events or modulate simulation parameters.
    """

    def cadence(self) -> int:
        """How many ticks between narrative evaluations."""
        ...

    def evaluate(
        self,
        ecosystem_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Returns intervention dict, e.g.:
        {"inject_events": [...], "modulate_climate": {"rainfall": +0.05}}
        """
        ...


# ---------------------------------------------------------------------------
# Context assembly (used by the engine)
# ---------------------------------------------------------------------------

def build_context(
    spec: ContextSpec,
    entity: dict[str, Any],
    biome_config: Any,
    climate: dict[str, float],
) -> list[float]:
    """
    Assemble a context vector for a single entity according to a
    model's ContextSpec.

    The engine calls this once per skeleton entity per tick. The model
    never needs to know about engine internals — it just gets a flat
    float vector built to its declared spec.
    """
    ctx: list[float] = []
    sv = entity.get("state_vars", {})
    meta = entity.get("metadata", {})

    for f in spec.fields:
        raw: float

        if f.source == "state_var":
            raw = sv.get(f.name, f.default)
        elif f.source == "metadata":
            raw = meta.get(f.name, f.default)
        elif f.source == "climate":
            raw = climate.get(f.name, f.default)
        elif f.source == "biome":
            raw = getattr(biome_config, f.name, f.default)
        elif f.source == "derived":
            raw = _resolve_derived(f.name, entity, f.default)
        else:
            raw = f.default

        # Normalize and clamp
        val = raw / f.normalize if f.normalize != 0 else 0.0
        ctx.append(max(0.0, min(1.0, val)))

    return ctx


def _resolve_derived(name: str, entity: dict[str, Any], default: float) -> float:
    """
    Compute derived features that don't map directly to a single field.
    Extend this as new derived features are needed.
    """
    if name == "state_code":
        return _STATE_CODES.get(entity.get("state", "IDLE"), 0.0)
    return default


# Discrete state → float encoding for motor context vectors.
# States implying more energetic movement get higher values.
_STATE_CODES: dict[str, float] = {
    "IDLE": 0.0,
    "RESTING": 0.1,
    "DRINKING": 0.2,
    "GROWING": 0.3,
    "FORAGING": 0.4,
    "POLLINATING": 0.4,
    "REPRODUCING": 0.5,
    "DORMANT": 0.6,
    "HUNTING": 0.7,
    "SWARMING": 0.8,
    "WILTING": 0.9,
    "FLEEING": 1.0,
}
