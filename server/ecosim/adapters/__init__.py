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
Built-in motor adapters for the Lila ecosystem engine.

Three adapters ship with the framework:

  mlp     — Reference 4-layer MLP. Pure-Python, stdlib only.
             Supports save/load weights for trained model swap-in.

  static  — Hand-tuned latent mapping per discrete state.
             No ML, no dependencies. Artists can tune motion styles
             without touching Python.

  random  — Random latents each tick. Useful for testing that the
             client's interpolation and retargeting pipeline handles
             arbitrary inputs gracefully.

Usage:
    from ecosim.adapters import create_adapter

    adapter = create_adapter("mlp", seed=42)
    adapter = create_adapter("static")
    adapter = create_adapter("mlp", weights="weights/motion_v0.json")
"""

from __future__ import annotations

from typing import Any

from ..model_adapter import MotorAdapter


def create_adapter(name: str, **kwargs: Any) -> MotorAdapter:
    """
    Factory for built-in motor adapters.

    Args:
        name: one of "mlp", "static", "random"
        **kwargs: passed to the adapter constructor

    Raises:
        ValueError: if the adapter name is not recognized
    """
    if name == "mlp":
        from .mlp import MlpMotorAdapter
        return MlpMotorAdapter(**kwargs)
    elif name == "static":
        from .static import StaticMotorAdapter
        return StaticMotorAdapter(**kwargs)
    elif name == "random":
        from .random import RandomMotorAdapter
        return RandomMotorAdapter(**kwargs)
    else:
        raise ValueError(
            f"Unknown adapter '{name}'. "
            f"Built-in adapters: mlp, static, random"
        )
