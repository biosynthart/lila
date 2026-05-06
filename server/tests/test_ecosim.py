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

"""Unit tests for the ecosim engine."""

from ecosim.engine import EcosystemEngine
from ecosim.entities import init_entity, is_alive, is_mobile
from ecosim.biome import get_biome_config
from ecosim.voxel_manager import VoxelManager
from ecosim.adapters import create_adapter


MINIMAL_WORLD = {
    "environment": {
        "biome": "TEMPERATE",
        "climate": {"temperature": 22.0, "humidity": 0.6},
        "soil": {"nitrogen": 0.7, "phosphorus": 0.6, "potassium": 0.5,
                 "moisture": 0.65, "organic_matter": 0.4},
        "voxel_grid": {"dimensions": [8, 8, 8], "cell_size": 1.0},
    },
    "entities": [
        {"id": "deer_01", "type": "ANIMAL", "species": "deer",
         "position": [4.0, 0.0, 4.0],
         "metadata": {"diet": "herbivore", "body_mass": 60.0,
                       "metabolism_rate": 1.0, "sensory_range": 8.0,
                       "movement_speed": 3.0}},
        {"id": "grass_01", "type": "PLANT", "species": "meadow_grass",
         "position": [3.0, 0.0, 3.0],
         "metadata": {"growth_rate": 0.06, "root_depth": 0.1,
                       "water_demand": 0.02,
                       "nutrient_demand": {"nitrogen": 0.005}}},
    ],
}


def test_engine_initializes():
    engine = EcosystemEngine(MINIMAL_WORLD)
    assert len(engine.entities) == 2
    assert engine.biome_name == "TEMPERATE"


def test_engine_steps():
    engine = EcosystemEngine(MINIMAL_WORLD)
    packet = engine.step(dt=0.1)
    assert packet["tick"] == 1
    assert "entity_updates" in packet


def test_engine_50_ticks():
    engine = EcosystemEngine(MINIMAL_WORLD)
    for _ in range(50):
        packet = engine.step(dt=0.1)
    assert packet["tick"] == 50
    assert len(packet["entity_updates"]) > 0


def test_adapters_create():
    mlp = create_adapter("mlp", seed=42)
    static = create_adapter("static")
    rand = create_adapter("random", seed=1)
    for adapter in (mlp, static, rand):
        spec = adapter.context_spec()
        assert spec.latent_dim == 4


def test_adapter_inference():
    adapter = create_adapter("mlp", seed=42)
    spec = adapter.context_spec()
    ctx = [0.5] * spec.input_dim
    results = adapter.infer([ctx, ctx])
    assert len(results) == 2
    assert len(results[0]) == spec.latent_dim


def test_entity_init():
    raw = {"id": "test", "type": "ANIMAL", "position": [0, 0, 0]}
    e = init_entity(raw)
    assert e["state"] == "IDLE"
    assert "hunger" in e["state_vars"]
    assert is_mobile(e)
    assert is_alive(e)


def test_biome_configs():
    for name in ("TEMPERATE", "TROPICAL", "ARCTIC", "DESERT"):
        config = get_biome_config(name)
        assert config.metabolic_scaling > 0


def test_voxel_manager():
    vm = VoxelManager(dimensions=(4, 4, 4))
    vm.set("moisture", 1, 0, 1, 0.8)
    assert vm.get("moisture", 1, 0, 1) == 0.8
    vm.add("moisture", 1, 0, 1, -0.3)
    assert abs(vm.get("moisture", 1, 0, 1) - 0.5) < 0.01


def test_voxel_initialize_from_soil():
    vm = VoxelManager(dimensions=(4, 4, 4))
    soil = {"nitrogen": 0.7, "phosphorus": 0.6, "potassium": 0.5,
            "moisture": 0.65, "organic_matter": 0.4}
    vm.initialize_from_soil(soil)
    assert abs(vm.get("moisture", 0, 0, 0) - 0.65) < 0.01
    assert abs(vm.get("organic_matter", 0, 0, 0) - 0.4) < 0.01
    assert abs(vm.get("nutrients", 0, 0, 0) - 0.6) < 0.01


def test_water_sources():
    world = {**MINIMAL_WORLD}
    world["environment"] = {
        **MINIMAL_WORLD["environment"],
        "water_sources": [{"position": [4.0, 0.0, 4.0], "radius": 2.0}],
    }
    engine = EcosystemEngine(world)
    assert len(engine.water_sources) == 1
    assert engine.water_sources[0]["water_level"] == 1.0


def test_rain():
    engine = EcosystemEngine(MINIMAL_WORLD)
    engine.apply_rain(0.8)
    gx, gy, gz = engine.voxels.world_to_grid(4.0, 0.0, 4.0)
    moisture = engine.voxels.get("moisture", gx, gy, gz)
    assert moisture > 0.8
