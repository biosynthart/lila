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
Random motor adapter for the Lila ecosystem engine.

Produces random latent vectors each tick. Useful for:

  - Testing that the client's interpolation pipeline handles
    arbitrary inputs without crashing or producing visual artifacts
  - Stress-testing the motion retargeter with full latent range
  - Verifying the engine doesn't depend on specific latent values
"""

from __future__ import annotations

import random as _random

from ..model_adapter import ContextField, ContextSpec

_RANDOM_SPEC = ContextSpec(
    fields=(
        ContextField("state_code", "derived"),
    ),
    latent_dim=4,
)


class RandomMotorAdapter:
    """
    Motor adapter that returns random latent vectors.

    Latents are uniform random in [-1, 1] (matching tanh output range
    of the MLP adapter). Use a fixed seed for reproducible behavior.

    Usage:
        adapter = RandomMotorAdapter(seed=123)
    """

    def __init__(
        self,
        seed: int | None = None,
        latent_dim: int = 4,
    ):
        self._rng = _random.Random(seed)
        self._latent_dim = latent_dim

    def context_spec(self) -> ContextSpec:
        return _RANDOM_SPEC

    def context_spec_for(self, entity_type: str) -> ContextSpec:
        return self.context_spec()

    def infer(self, contexts: list[list[float]]) -> list[list[float]]:
        """Ignore contexts, return random latents."""
        return [
            [self._rng.uniform(-1.0, 1.0) for _ in range(self._latent_dim)]
            for _ in contexts
        ]
