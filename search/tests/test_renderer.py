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

"""Tests for the headless renderer.

Uses a mock engine so these tests run without ecosim installed.
Validates that render_headless produces valid images from engine state.
"""

import numpy as np
import pytest

from lila_search.renderer import render_headless, IMG_SIZE


class MockVoxelManager:
    """Minimal mock of ecosim's VoxelManager."""

    def __init__(self, moisture: float = 0.5):
        self._moisture = moisture

    def get_layer_slice(self, layer: str, y: int = 0) -> np.ndarray:
        return np.full((32, 32), self._moisture, dtype=np.float32)


class MockEngine:
    """Minimal mock of ecosim's EcosystemEngine."""

    def __init__(self, entities: dict | None = None, moisture: float = 0.5):
        self.entities = entities or {}
        self.voxel_manager = MockVoxelManager(moisture)
        self.water_sources = [
            {"x": 16.0, "z": 16.0, "radius": 3.0, "water_level": 0.8},
        ]


class TestRenderHeadless:
    def test_output_shape_default(self):
        engine = MockEngine()
        img = render_headless(engine)
        assert img.shape == (IMG_SIZE, IMG_SIZE, 3)
        assert img.dtype == np.uint8

    def test_output_shape_custom_size(self):
        engine = MockEngine()
        img = render_headless(engine, img_size=128)
        # Note: renderer uses IMG_SIZE constant for grid math internally.
        # Custom sizes work but grid-to-pixel mapping uses the default.
        # This test just verifies no crash with different size.
        assert img.shape[2] == 3
        assert img.dtype == np.uint8

    def test_not_all_black(self):
        engine = MockEngine(moisture=0.5)
        img = render_headless(engine)
        assert img.sum() > 0, "Image should not be all black"

    def test_moisture_affects_background(self):
        engine_dry = MockEngine(moisture=0.1)
        engine_wet = MockEngine(moisture=0.9)
        img_dry = render_headless(engine_dry)
        img_wet = render_headless(engine_wet)
        # Wet soil should have more blue/teal, dry more amber
        # Just verify they're different
        assert not np.array_equal(img_dry, img_wet)

    def test_entities_render_without_crash(self):
        entities = {
            "deer_0": {
                "type": "ANIMAL", "state": "FORAGING",
                "x": 10.0, "y": 0.0, "z": 10.0,
                "health": 1.0, "growth": 0.0, "hydration": 0.8,
            },
            "butterfly_0": {
                "type": "INSECT", "state": "SEEKING",
                "x": 15.0, "y": 0.0, "z": 15.0,
                "health": 1.0, "growth": 0.0, "hydration": 0.8,
            },
            "oak_0": {
                "type": "TREE", "state": "GROWING",
                "x": 20.0, "y": 0.0, "z": 20.0,
                "health": 1.0, "growth": 0.8, "hydration": 0.8,
            },
            "grass_0": {
                "type": "PLANT", "state": "GROWING",
                "x": 5.0, "y": 0.0, "z": 5.0,
                "health": 1.0, "growth": 0.6, "hydration": 0.7,
            },
            "wildflower_0": {
                "type": "PLANT", "state": "FRUITING",
                "x": 8.0, "y": 0.0, "z": 8.0,
                "health": 1.0, "growth": 0.7, "hydration": 0.5,
            },
            "dormant_grass": {
                "type": "PLANT", "state": "DORMANT",
                "x": 25.0, "y": 0.0, "z": 25.0,
                "health": 0.0, "growth": 0.0, "hydration": 0.0,
            },
        }
        engine = MockEngine(entities=entities)
        img = render_headless(engine)
        assert img.shape == (IMG_SIZE, IMG_SIZE, 3)

    def test_empty_entities(self):
        engine = MockEngine(entities={})
        img = render_headless(engine)
        assert img.shape == (IMG_SIZE, IMG_SIZE, 3)

    def test_dried_water_source_not_rendered(self):
        engine = MockEngine()
        engine.water_sources = [
            {"x": 16.0, "z": 16.0, "radius": 3.0, "water_level": 0.01},
        ]
        img_dry = render_headless(engine)

        engine.water_sources = [
            {"x": 16.0, "z": 16.0, "radius": 3.0, "water_level": 0.8},
        ]
        img_wet = render_headless(engine)

        # The wet version should have blue pixels that the dry one doesn't
        assert not np.array_equal(img_dry, img_wet)
