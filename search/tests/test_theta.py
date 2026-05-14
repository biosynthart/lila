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

"""Tests for theta parameterization and world config generation.

These tests validate that:
- θ spec has correct dimensionality and valid ranges
- Clipping keeps θ within bounds
- theta_to_world_config produces valid world configs for any θ in range
- Entity counts and water sources match θ values
"""

import numpy as np
import pytest

from lila_search.theta import ThetaSpec, ThetaDim, make_eco_rates_spec, theta_to_world_config


class TestThetaSpec:
    def test_eco_rates_spec_dimensions(self):
        spec = make_eco_rates_spec()
        assert spec.ndim == 17

    def test_bounds_shape(self):
        spec = make_eco_rates_spec()
        bounds = spec.bounds
        assert bounds.shape == (17, 2)
        assert (bounds[:, 0] < bounds[:, 1]).all(), "All lows must be < highs"

    def test_defaults_within_bounds(self):
        spec = make_eco_rates_spec()
        defaults = spec.defaults
        bounds = spec.bounds
        assert (defaults >= bounds[:, 0]).all()
        assert (defaults <= bounds[:, 1]).all()

    def test_clip_enforces_bounds(self):
        spec = make_eco_rates_spec()
        # Way out of range
        theta_low = np.full(spec.ndim, -100.0)
        theta_high = np.full(spec.ndim, 10000.0)

        clipped_low = spec.clip(theta_low)
        clipped_high = spec.clip(theta_high)

        bounds = spec.bounds
        np.testing.assert_array_equal(clipped_low, bounds[:, 0])
        np.testing.assert_array_equal(clipped_high, bounds[:, 1])

    def test_sample_uniform_within_bounds(self):
        spec = make_eco_rates_spec()
        rng = np.random.default_rng(42)
        for _ in range(100):
            theta = spec.sample_uniform(rng)
            bounds = spec.bounds
            assert (theta >= bounds[:, 0]).all()
            assert (theta <= bounds[:, 1]).all()


class TestThetaToWorldConfig:
    def test_default_config_structure(self):
        spec = make_eco_rates_spec()
        config = theta_to_world_config(spec.defaults)

        assert "version" in config
        assert "environment" in config
        assert "rates" in config
        assert "entities" in config
        assert "model" in config

        env = config["environment"]
        assert "climate" in env
        assert "soil" in env
        assert "voxel_grid" in env
        assert "water_sources" in env
        assert env["voxel_grid"]["dimensions"] == [32, 32, 32]

    def test_entity_counts_match_theta(self):
        spec = make_eco_rates_spec()
        theta = spec.defaults.copy()

        names = spec.names
        theta[names.index("deer_count")] = 3.0
        theta[names.index("butterfly_count")] = 5.0
        theta[names.index("oak_count")] = 2.0
        theta[names.index("grass_count")] = 7.0
        theta[names.index("wildflower_count")] = 4.0

        config = theta_to_world_config(theta)
        entities = config["entities"]

        deer = [e for e in entities if e["species"] == "deer"]
        butterflies = [e for e in entities if e["species"] == "monarch"]
        oaks = [e for e in entities if e["species"] == "meadow_oak"]
        grass = [e for e in entities if e["species"] == "meadow_grass"]
        flowers = [e for e in entities if e["species"] == "wildflower"]

        assert len(deer) == 3
        assert len(butterflies) == 5
        assert len(oaks) == 2
        assert len(grass) == 7
        assert len(flowers) == 4

    def test_water_count_matches_theta(self):
        spec = make_eco_rates_spec()
        theta = spec.defaults.copy()
        theta[spec.names.index("water_count")] = 3.0

        config = theta_to_world_config(theta)
        assert len(config["environment"]["water_sources"]) == 3

    def test_rate_multipliers_match_theta(self):
        spec = make_eco_rates_spec()
        theta = spec.defaults.copy()
        theta[spec.names.index("rate_hunger")] = 2.5

        config = theta_to_world_config(theta)
        assert config["rates"]["hunger"] == 2.5

    def test_deterministic_with_same_seed(self):
        spec = make_eco_rates_spec()
        theta = spec.sample_uniform(np.random.default_rng(99))

        config1 = theta_to_world_config(theta, seed=42)
        config2 = theta_to_world_config(theta, seed=42)

        for e1, e2 in zip(config1["entities"], config2["entities"]):
            assert e1["position"] == e2["position"]

    def test_different_seeds_produce_different_positions(self):
        spec = make_eco_rates_spec()
        theta = spec.defaults

        config1 = theta_to_world_config(theta, seed=0)
        config2 = theta_to_world_config(theta, seed=1)

        positions1 = [e["position"] for e in config1["entities"]]
        positions2 = [e["position"] for e in config2["entities"]]
        assert positions1 != positions2

    def test_entities_within_grid(self):
        spec = make_eco_rates_spec()
        rng = np.random.default_rng(0)
        for _ in range(20):
            theta = spec.sample_uniform(rng)
            config = theta_to_world_config(theta, seed=rng.integers(1000))
            for e in config["entities"]:
                pos = e["position"]
                assert 0 <= pos[0] <= 32, f"Entity {e['id']} x={pos[0]} out of grid"
                assert 0 <= pos[2] <= 32, f"Entity {e['id']} z={pos[2]} out of grid"

    def test_entities_have_position_arrays(self):
        spec = make_eco_rates_spec()
        config = theta_to_world_config(spec.defaults)
        for e in config["entities"]:
            assert isinstance(e["position"], list)
            assert len(e["position"]) == 3

    def test_entities_have_metadata(self):
        spec = make_eco_rates_spec()
        config = theta_to_world_config(spec.defaults)
        for e in config["entities"]:
            assert "metadata" in e

    def test_rain_config_zero_interval(self):
        spec = make_eco_rates_spec()
        theta = spec.defaults.copy()
        theta[spec.names.index("rain_interval")] = 0.0

        config = theta_to_world_config(theta)
        assert "rain" not in config

    def test_rain_config_nonzero_interval(self):
        spec = make_eco_rates_spec()
        theta = spec.defaults.copy()
        theta[spec.names.index("rain_interval")] = 500.0

        config = theta_to_world_config(theta)
        assert "rain" in config
        assert config["rain"]["interval"] == 500
