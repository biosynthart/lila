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
Smoke test: run the ecosystem engine for 50 ticks with a small
tropical forest and print key observations.
"""

import json

from ecosim.adapters import create_adapter
from ecosim.engine import EcosystemEngine

WORLD = {
    "version": "0.1",
    "session_id": "test-001",
    "species_definitions": [
        {
            "species_id": "jaguar",
            "functional_group": "predator",
            "entity_class": "ANIMAL",
            "body_mass_kg": 80.0,
            "locomotion": "quadruped",
            "skeleton_id": "quadruped_large",
            "thermoregulation": "endotherm",
            "diet_type": "carnivore",
            "diet_breadth": ["herbivore"],
            "trophic_level": 3.0,
            "reproductive_strategy": "K_selected",
            "clutch_size": 2,
            "generation_time_ticks": 8000,
            "thermal_range": [15, 40],
            "drought_tolerance": 0.4,
            "shade_tolerance": 0.5,
            "sensory_range_multiplier": 1.3,
            "movement_budget": 0.5,
        },
        {
            "species_id": "rabbit",
            "functional_group": "herbivore",
            "entity_class": "ANIMAL",
            "body_mass_kg": 3.0,
            "locomotion": "quadruped",
            "skeleton_id": "quadruped_small",
            "thermoregulation": "endotherm",
            "diet_type": "herbivore",
            "diet_breadth": ["graminoid", "forb"],
            "trophic_level": 2.0,
            "reproductive_strategy": "r_selected",
            "clutch_size": 4,
            "generation_time_ticks": 3000,
            "thermal_range": [5, 38],
            "drought_tolerance": 0.3,
            "shade_tolerance": 0.4,
            "sensory_range_multiplier": 1.2,
            "movement_budget": 0.6,
        },
        {
            "species_id": "fern",
            "functional_group": "producer",
            "entity_class": "PLANT",
            "body_mass_kg": 0.5,
            "locomotion": "sessile",
            "thermoregulation": "autotroph",
            "diet_type": "autotroph",
            "trophic_level": 1.0,
            "reproductive_strategy": "r_selected",
            "clutch_size": 2,
            "generation_time_ticks": 500,
            "thermal_range": [10, 40],
            "drought_tolerance": 0.2,
            "shade_tolerance": 0.7,
            "spread_mode": "spore",
            "spread_range": 3.0,
            "root_persistence": True,
            "resource_tags": ["forb"],
        },
        {
            "species_id": "tropical_oak",
            "functional_group": "producer",
            "entity_class": "TREE",
            "body_mass_kg": 5000.0,
            "locomotion": "rooted",
            "thermoregulation": "autotroph",
            "diet_type": "autotroph",
            "trophic_level": 1.0,
            "reproductive_strategy": "K_selected",
            "clutch_size": 1,
            "generation_time_ticks": 20000,
            "thermal_range": [5, 45],
            "drought_tolerance": 0.5,
            "shade_tolerance": 0.2,
            "canopy_radius": 3.0,
            "root_persistence": True,
            "resource_tags": ["mast"],
        },
        {
            "species_id": "honeybee",
            "functional_group": "pollinator",
            "entity_class": "INSECT",
            "body_mass_kg": 0.0001,
            "locomotion": "flight_insect",
            "thermoregulation": "ectotherm",
            "diet_type": "nectarivore",
            "diet_breadth": ["forb:fruiting"],
            "trophic_level": 2.0,
            "reproductive_strategy": "r_selected",
            "clutch_size": 5,
            "generation_time_ticks": 1000,
            "thermal_range": [15, 40],
            "drought_tolerance": 0.1,
            "shade_tolerance": 0.3,
            "sensory_range_multiplier": 1.5,
            "movement_budget": 0.7,
            "floral_affinity": ["insect_generalist"],
        },
        {
            "species_id": "mycorrhiza",
            "functional_group": "decomposer",
            "entity_class": "MICROORGANISM",
            "body_mass_kg": 0.001,
            "locomotion": "sessile",
            "thermoregulation": "ectotherm",
            "diet_type": "decomposer",
            "trophic_level": 1.0,
            "reproductive_strategy": "r_selected",
            "clutch_size": 5,
            "generation_time_ticks": 300,
            "thermal_range": [5, 40],
            "drought_tolerance": 0.1,
            "shade_tolerance": 0.9,
            "spread_mode": "spore",
            "spread_range": 2.0,
        },
    ],
    "environment": {
        "type": "FOREST_PATCH",
        "biome": "TROPICAL",
        "climate": {
            "temperature": 30.0,
            "humidity": 0.8,
            "rainfall": 0.6,
            "wind_speed": 0.2,
            "light_level": 0.9,
        },
        "soil": {
            "nitrogen": 0.9,
            "phosphorus": 0.7,
            "potassium": 0.6,
            "moisture": 0.8,
            "organic_matter": 0.5,
        },
        "voxel_grid": {"dimensions": [16, 16, 16], "cell_size": 2.0},
    },
    "entities": [
        {
            "id": "jaguar_01",
            "type": "ANIMAL",
            "species": "jaguar",
            "position": [10.0, 0.0, 10.0],
            "metadata": {
                "diet": "carnivore",
                "body_mass": 80.0,
                "metabolism_rate": 1.2,
                "sensory_range": 12.0,
                "movement_speed": 3.0,
                "lifespan": 500.0,
                "reproduction_threshold": 0.85,
            },
            "skeleton_id": "quadruped_large",
        },
        {
            "id": "rabbit_01",
            "type": "ANIMAL",
            "species": "rabbit",
            "position": [14.0, 0.0, 12.0],
            "metadata": {
                "diet": "herbivore",
                "body_mass": 3.0,
                "metabolism_rate": 1.5,
                "sensory_range": 8.0,
                "movement_speed": 4.0,
                "lifespan": 300.0,
                "reproduction_threshold": 0.7,
            },
            "skeleton_id": "quadruped_small",
        },
        {
            "id": "fern_01",
            "type": "PLANT",
            "species": "fern",
            "position": [8.0, 0.0, 6.0],
            "metadata": {
                "metabolism": "photosynthetic",
                "growth_rate": 0.04,
                "root_depth": 0.3,
                "canopy_radius": 0.0,
                "nutrient_demand": {"nitrogen": 0.01, "phosphorus": 0.005},
                "water_demand": 0.03,
            },
        },
        {
            "id": "oak_01",
            "type": "TREE",
            "species": "tropical_oak",
            "position": [4.0, 0.0, 4.0],
            "metadata": {
                "metabolism": "photosynthetic",
                "growth_rate": 0.01,
                "root_depth": 2.0,
                "canopy_radius": 3.0,
                "height_max": 20.0,
                "trunk_radius": 0.5,
                "shade_factor": 0.4,
                "nutrient_demand": {"nitrogen": 0.02, "phosphorus": 0.01},
                "water_demand": 0.05,
            },
        },
        {
            "id": "bee_colony_01",
            "type": "INSECT",
            "species": "honeybee",
            "position": [6.0, 0.0, 5.0],
            "metadata": {
                "diet": "herbivore",
                "colony_size": 200,
                "metabolism_rate": 0.8,
                "pollination_range": 5.0,
                "movement_speed": 2.0,
                "lifespan": 200.0,
            },
            "skeleton_id": None,
        },
        {
            "id": "fungi_01",
            "type": "MICROORGANISM",
            "species": "mycorrhiza",
            "position": [4.0, 0.0, 4.0],
            "metadata": {
                "function": "decomposer",
                "colony_density": 0.6,
                "activity_rate": 0.5,
                "optimal_ph": 6.5,
            },
        },
    ],
}


def main():
    adapter = create_adapter("mlp", seed=42)
    engine = EcosystemEngine(WORLD, adapters={"motor": adapter})
    print(f"Initialized engine: biome={engine.biome_name}, entities={len(engine.entities)}")
    print(f"Voxel grid: {engine.voxels.dimensions}")
    print(f"Motor adapter: {type(engine._motor_adapter).__name__}")
    print("=" * 70)

    events_seen = []
    removals_seen = []
    spawns_seen = []

    for i in range(50):
        packet = engine.step(dt=0.1)

        # Collect notable events
        for ev in packet.get("events", []):
            events_seen.append(ev)

        for rid in packet.get("entity_removals", []):
            removals_seen.append((packet["tick"], rid))

        for sp in packet.get("entity_spawns", []):
            spawns_seen.append((packet["tick"], sp["id"], sp["type"]))

        # Print summary every 10 ticks
        if (i + 1) % 10 == 0:
            print(f"\n--- Tick {packet['tick']} ---")
            for u in packet["entity_updates"]:
                state = u["state"]
                pos = u.get("ref_position", u.get("position", [0, 0, 0]))
                key_vars = {k: v for k, v in u.get("drive", u.get("state_vars", {})).items()
                           if k in ("hunger", "energy", "hydration", "growth",
                                    "health", "colony_health", "population")}
                ml = u.get("motion_latent")
                ml_str = f"  latent={[round(v, 3) for v in ml]}" if ml else ""
                print(f"  {u['id']:20s}  state={state:12s}  "
                      f"pos=({pos[0]:5.1f},{pos[2]:5.1f})  {key_vars}{ml_str}")

            vd = packet.get("voxel_deltas", {})
            if vd:
                total_dirty = sum(len(v) for v in vd.values())
                layers = list(vd.keys())
                print(f"  Voxel deltas: {total_dirty} voxels across {layers}")

    print("\n" + "=" * 70)
    print(f"Events fired: {len(events_seen)}")
    event_types = {}
    for ev in events_seen:
        event_types[ev["type"]] = event_types.get(ev["type"], 0) + 1
    for t, count in sorted(event_types.items()):
        print(f"  {t}: {count}")

    if removals_seen:
        print("\nEntities removed:")
        for tick, rid in removals_seen:
            print(f"  tick {tick}: {rid}")

    if spawns_seen:
        print("\nEntities spawned:")
        for tick, sid, stype in spawns_seen:
            print(f"  tick {tick}: {sid} ({stype})")

    # Print one full tick packet as JSON for schema validation
    final_packet = engine.step(dt=0.1)
    print(f"\n--- Sample tick packet (tick {final_packet['tick']}) ---")
    print(json.dumps(final_packet, indent=2))


if __name__ == "__main__":
    main()
