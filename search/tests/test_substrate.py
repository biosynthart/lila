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

"""Integration tests for the substrate protocol.

These require ecosim to be installed/importable. Skip if not available.
Run with: pytest tests/test_substrate.py -v
"""

import numpy as np
import pytest

try:
    from ecosim.engine import EcosystemEngine
    HAS_ECOSIM = True
except ImportError:
    HAS_ECOSIM = False

from lila_search.theta import make_eco_rates_spec, theta_to_world_config


pytestmark = pytest.mark.skipif(not HAS_ECOSIM, reason="ecosim not installed")


class TestSubstrateIntegration:
    """Tests that require the actual ecosim engine."""

    def test_default_theta_creates_valid_engine(self):
        """Default θ → world config → engine init without crash."""
        spec = make_eco_rates_spec()
        config = theta_to_world_config(spec.defaults, seed=0)
        engine = EcosystemEngine(config)
        # If we get here, the config format matches what the engine expects

    def test_engine_steps_without_crash(self):
        """Engine can step for 50 ticks with a θ-generated config."""
        spec = make_eco_rates_spec()
        config = theta_to_world_config(spec.defaults, seed=0)
        engine = EcosystemEngine(config)
        for _ in range(50):
            engine.step()

    def test_random_theta_roundtrip(self):
        """10 random θ vectors all produce valid engines that step cleanly."""
        spec = make_eco_rates_spec()
        rng = np.random.default_rng(42)
        for i in range(10):
            theta = spec.sample_uniform(rng)
            config = theta_to_world_config(theta, seed=i)
            engine = EcosystemEngine(config)
            for _ in range(20):
                engine.step()

    def test_renderer_on_live_engine(self):
        """Headless renderer produces valid image from a running engine."""
        from lila_search.renderer import render_headless

        spec = make_eco_rates_spec()
        config = theta_to_world_config(spec.defaults, seed=0)
        engine = EcosystemEngine(config)
        for _ in range(100):
            engine.step()

        img = render_headless(engine)
        assert img.shape == (256, 256, 3)
        assert img.dtype == np.uint8
        assert img.sum() > 0

    def test_full_substrate_rollout(self):
        """Full substrate protocol: init → step → render cycle."""
        from lila_search.substrate import LilaSubstrate

        substrate = LilaSubstrate()
        spec = substrate.theta_spec()
        theta = spec.defaults

        frames = substrate.rollout(theta, n_steps=100, n_frames=5, seed=0)
        assert len(frames) == 5
        for frame in frames:
            assert frame.shape == (256, 256, 3)
            assert frame.dtype == np.uint8
