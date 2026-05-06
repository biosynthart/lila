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
Static motor adapter for the Lila ecosystem engine.

Maps discrete entity states to fixed latent vectors. No ML, no math,
no dependencies. Useful for:

  - Running Lila without any model at all
  - Artists hand-tuning motion styles per state
  - Baseline comparison for trained models
  - Client development (predictable, deterministic latents)

The default mapping provides reasonable differentiation between states.
Override by passing a custom mapping dict to the constructor.
"""

from __future__ import annotations

from ..model_adapter import ContextField, ContextSpec

# Default latent vectors per discrete state.
# Tuned so that interpolation between states produces smooth transitions.
# Dimension semantics are arbitrary but consistent:
#   dim 0: energy/intensity (low=calm, high=vigorous)
#   dim 1: regularity (low=erratic, high=rhythmic)
#   dim 2: verticality (low=crouched, high=upright)
#   dim 3: alertness (low=relaxed, high=tense)

DEFAULT_STATE_MAP: dict[str, list[float]] = {
    "IDLE":         [ 0.1,  0.3,  0.4,  0.1],
    "RESTING":      [ 0.0,  0.5,  0.1,  0.0],
    "DRINKING":     [ 0.2,  0.4,  0.2,  0.2],
    "FORAGING":     [ 0.4,  0.3,  0.5,  0.3],
    "POLLINATING":  [ 0.3,  0.6,  0.5,  0.2],
    "HUNTING":      [ 0.7,  0.2,  0.4,  0.8],
    "REPRODUCING":  [ 0.5,  0.4,  0.4,  0.3],
    "SWARMING":     [ 0.8,  0.1,  0.5,  0.7],
    "FLEEING":      [ 1.0, -0.3,  0.3,  1.0],
    "DYING":        [ 0.0,  0.0, -0.2, -0.1],
}

_DEFAULT_LATENT = [0.0, 0.0, 0.0, 0.0]

# Minimal context spec — we only need the state code, but the protocol
# requires a ContextSpec. The engine will build the vector; we ignore it
# and read the entity state directly from the batch metadata.
_STATIC_SPEC = ContextSpec(
    fields=(
        ContextField("state_code", "derived"),
    ),
    latent_dim=4,
)


class StaticMotorAdapter:
    """
    Motor adapter that returns fixed latent vectors per entity state.

    Usage:
        adapter = StaticMotorAdapter()

        # Or with custom mapping:
        adapter = StaticMotorAdapter(state_map={
            "IDLE": [0.0, 0.0, 0.0, 0.0],
            "HUNTING": [1.0, 0.0, 0.5, 1.0],
        })
    """

    def __init__(
        self,
        state_map: dict[str, list[float]] | None = None,
        latent_dim: int = 4,
    ):
        self._state_map = state_map or DEFAULT_STATE_MAP
        self._latent_dim = latent_dim

    def context_spec(self) -> ContextSpec:
        return _STATIC_SPEC

    def context_spec_for(self, entity_type: str) -> ContextSpec:
        return self.context_spec()

    def infer(self, contexts: list[list[float]]) -> list[list[float]]:
        """
        Ignores context vectors entirely — returns latents based on
        state codes embedded in the context.

        Note: the engine passes state_code as the first (and only)
        context field for this adapter. We reverse-map it to a state
        name and look up the fixed latent.
        """
        results = []
        for ctx in contexts:
            state_code = ctx[0] if ctx else 0.0
            state_name = _code_to_state(state_code)
            latent = self._state_map.get(state_name, _DEFAULT_LATENT)
            results.append(list(latent))
        return results

    def infer_by_state(self, state: str) -> list[float]:
        """Direct lookup by state name (convenience for testing)."""
        return list(self._state_map.get(state, _DEFAULT_LATENT))


# Reverse mapping from state code float → state name
_CODE_TO_STATE: dict[float, str] = {
    0.0: "IDLE",
    0.1: "RESTING",
    0.2: "DRINKING",
    0.4: "FORAGING",   # also POLLINATING
    0.5: "REPRODUCING",
    0.7: "HUNTING",
    0.8: "SWARMING",
    1.0: "FLEEING",
}


def _code_to_state(code: float) -> str:
    """Find the closest matching state for a code value."""
    best_state = "IDLE"
    best_dist = float("inf")
    for c, s in _CODE_TO_STATE.items():
        d = abs(c - code)
        if d < best_dist:
            best_dist = d
            best_state = s
    return best_state
