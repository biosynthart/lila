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
Reference MLP motor adapter for the Lila ecosystem engine.

A tiny feedforward network (10 → 16 → 12 → 8 → 4) with tanh activations.
Pure-Python, stdlib only — ~500 parameters. Runs comfortably at 10 Hz
with <50 entities.

Random Xavier init by default. Supports save/load for trained weights.
The architecture is intentionally simple enough that training pipelines
can target it with PyTorch and export to the JSON weight format via
training/scripts/export_weights.py.
"""

from __future__ import annotations

import json
import math
import random as _random
from typing import Any

from ..model_adapter import ContextField, ContextSpec

# -- Context spec: what this model expects as input --------------------------

# ANIMAL/BIRD context (10 dims). The engine uses the ContextSpec to
# assemble these automatically — the adapter never touches entity dicts.
ANIMAL_CONTEXT = ContextSpec(
    fields=(
        ContextField("hunger",             "state_var"),
        ContextField("energy",             "state_var"),
        ContextField("hydration",          "state_var"),
        ContextField("health",             "state_var"),
        ContextField("reproductive_drive", "state_var"),
        ContextField("movement_speed",     "metadata",  normalize=10.0),
        ContextField("body_mass",          "metadata",  normalize=200.0),
        ContextField("temperature",        "climate",   normalize=50.0),
        ContextField("metabolic_scaling",  "biome"),
        ContextField("state_code",         "derived"),
    ),
    latent_dim=4,
)

# INSECT context (same structure, different features, padded to 10)
INSECT_CONTEXT = ContextSpec(
    fields=(
        ContextField("hunger",             "state_var"),
        ContextField("energy",             "state_var"),
        ContextField("colony_health",      "state_var"),
        ContextField("movement_speed",     "metadata",  normalize=10.0),
        ContextField("temperature",        "climate",   normalize=50.0),
        ContextField("metabolic_scaling",  "biome"),
        ContextField("state_code",         "derived"),
        ContextField("colony_size",        "metadata",  normalize=1000.0),
        # Padding to match ANIMAL_CONTEXT input_dim
        ContextField("_pad_8",             "derived",   default=0.0),
        ContextField("_pad_9",             "derived",   default=0.0),
    ),
    latent_dim=4,
)

# Map entity types to their context specs
CONTEXT_SPECS: dict[str, ContextSpec] = {
    "ANIMAL": ANIMAL_CONTEXT,
    "BIRD":   ANIMAL_CONTEXT,
    "INSECT": INSECT_CONTEXT,
}

# The primary spec (used by the engine for input_dim / latent_dim)
DEFAULT_SPEC = ANIMAL_CONTEXT


# -- Pure-Python micro-network ----------------------------------------------

def _xavier_init(fan_in: int, fan_out: int) -> list[list[float]]:
    """Xavier/Glorot uniform initialization."""
    limit = math.sqrt(6.0 / (fan_in + fan_out))
    return [
        [_random.uniform(-limit, limit) for _ in range(fan_in)]
        for _ in range(fan_out)
    ]


def _zero_bias(size: int) -> list[float]:
    return [0.0] * size


def _tanh(x: float) -> float:
    x = max(-10.0, min(10.0, x))
    e2x = math.exp(2.0 * x)
    return (e2x - 1.0) / (e2x + 1.0)


def _forward_layer(
    inp: list[float],
    weights: list[list[float]],
    biases: list[float],
    activation: bool = True,
) -> list[float]:
    """Single dense layer: out = activation(W @ inp + b)."""
    out = []
    for j in range(len(biases)):
        val = biases[j]
        for i in range(len(inp)):
            val += weights[j][i] * inp[i]
        if activation:
            val = _tanh(val)
        out.append(val)
    return out


class _MlpNetwork:
    """4-layer MLP: 10 → 16 → 12 → 8 → 4. ~500 parameters."""

    def __init__(self, input_dim: int = 10, latent_dim: int = 4, seed: int | None = None):
        if seed is not None:
            _random.seed(seed)

        self.w1 = _xavier_init(input_dim, 16)
        self.b1 = _zero_bias(16)
        self.w2 = _xavier_init(16, 12)
        self.b2 = _zero_bias(12)
        self.w3 = _xavier_init(12, 8)
        self.b3 = _zero_bias(8)
        self.w4 = _xavier_init(8, latent_dim)
        self.b4 = _zero_bias(latent_dim)

    def forward(self, context: list[float]) -> list[float]:
        h1 = _forward_layer(context, self.w1, self.b1)
        h2 = _forward_layer(h1, self.w2, self.b2)
        h3 = _forward_layer(h2, self.w3, self.b3)
        return _forward_layer(h3, self.w4, self.b4, activation=True)

    def get_weights(self) -> dict[str, Any]:
        return {
            "w1": self.w1, "b1": self.b1,
            "w2": self.w2, "b2": self.b2,
            "w3": self.w3, "b3": self.b3,
            "w4": self.w4, "b4": self.b4,
        }

    def set_weights(self, data: dict[str, Any]) -> None:
        self.w1 = data["w1"]
        self.b1 = data["b1"]
        self.w2 = data["w2"]
        self.b2 = data["b2"]
        self.w3 = data["w3"]
        self.b3 = data["b3"]
        self.w4 = data["w4"]
        self.b4 = data["b4"]


# -- Public adapter class ---------------------------------------------------

class MlpMotorAdapter:
    """
    Reference MLP motor adapter.

    Implements the MotorAdapter protocol. Plug into the engine via:

        engine = EcosystemEngine(world_config, adapters={
            "motor": MlpMotorAdapter(seed=42),
        })

    Or with pre-trained weights:

        adapter = MlpMotorAdapter(weights="weights/motion_v0.json")
    """

    def __init__(
        self,
        seed: int | None = None,
        weights: str | None = None,
        latent_dim: int = 4,
    ):
        self._spec = DEFAULT_SPEC
        self._network = _MlpNetwork(
            input_dim=self._spec.input_dim,
            latent_dim=latent_dim,
            seed=seed,
        )
        if weights is not None:
            self.load_weights(weights)

    def context_spec(self) -> ContextSpec:
        return self._spec

    def context_spec_for(self, entity_type: str) -> ContextSpec:
        """Return the context spec appropriate for an entity type."""
        return CONTEXT_SPECS.get(entity_type, DEFAULT_SPEC)

    def infer(self, contexts: list[list[float]]) -> list[list[float]]:
        """Batch inference: run each context through the network."""
        return [self._network.forward(ctx) for ctx in contexts]

    def save_weights(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self._network.get_weights(), f)

    def load_weights(self, path: str) -> None:
        with open(path) as f:
            self._network.set_weights(json.load(f))
