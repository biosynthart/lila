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
Hybrid Automaton Engine for the Lila Ecosystem.

The engine runs a fixed-timestep simulation loop with two phases per tick:

  1. FLOW  — continuous state variable updates (dx/dt = f(x, env))
  2. GUARD — discrete state transition checks (if condition → new_state)

Entity-entity interactions (predation, consumption, pollination) are
resolved between flow and guard evaluation. Events are collected
during the tick and returned in the tick packet.

The engine is BYOM (Bring Your Own Model). Motor-level ML adapters
plug in via the adapters dict at construction time. If no adapter is
provided, a static fallback is used — the simulation runs fine, entities
just animate with fixed per-state latent vectors.

Architecture note: this module is deliberately free of any networking
or serialization logic. It takes a world config dict, advances state,
and returns a tick packet dict. The worker module handles I/O.
"""

from __future__ import annotations

import math
import random
from typing import Any

from .biome import BiomeConfig, get_biome_config
from .entities import init_entity, is_alive, is_mobile
from .model_adapter import MotorAdapter, build_context
from .voxel_manager import VoxelManager


class EcosystemEngine:
    """
    Stateful simulation engine for a single ecosystem session.

    Constructed from a world definition dict (the JSON payload the
    client sends on "Become Alive!"). Call step(dt) to advance the
    simulation by one tick.
    """

    def __init__(
        self,
        world_config: dict[str, Any],
        adapters: dict[str, Any] | None = None,
    ):
        env = world_config["environment"]
        self.biome_name: str = env.get("biome", "TEMPERATE")
        self.biome: BiomeConfig = get_biome_config(self.biome_name)
        self.climate: dict[str, float] = dict(env.get("climate", {}))

        # Voxel grid
        grid_cfg = env.get("voxel_grid", {})
        dims = tuple(grid_cfg.get("dimensions", [32, 32, 32]))
        cell = grid_cfg.get("cell_size", 1.0)
        self.voxels = VoxelManager(dimensions=dims, cell_size=cell)

        soil = env.get("soil", {})
        if soil:
            self.voxels.initialize_from_soil(soil)

        # Entities
        raw_entities = world_config.get("entities", [])
        self.entities: dict[str, dict[str, Any]] = {}
        for raw in raw_entities:
            e = init_entity(raw)
            self.entities[e["id"]] = e

        # Tick counter
        self.tick: int = 0

        # Motor adapter (BYOM — bring your own model)
        # Falls back to static adapter if none provided.
        adapters = adapters or {}
        motor = adapters.get("motor")
        if motor is not None:
            self._motor_adapter: MotorAdapter = motor
        else:
            from .adapters.static import StaticMotorAdapter
            self._motor_adapter = StaticMotorAdapter()

        # Grid bounds (for clamping positions)
        self._grid_max: float = (
            (self.voxels.dimensions[0] - 1) * self.voxels.cell_size
        )

        # Water sources (positions, no moisture yet — randomize first)
        self.water_sources: list[dict[str, Any]] = []
        for ws in env.get("water_sources", []):
            source = {
                "position": list(ws["position"]),
                "max_radius": ws.get("radius", 2.0),
                "radius": ws.get("radius", 2.0),
                "water_level": 1.0,
            }
            self.water_sources.append(source)

        # Tunable rate multipliers (from world config, all default to 1.0)
        rates = world_config.get("rates", {})
        self.rate_consumption: float = rates.get("consumption", 1.0)
        self.rate_hunger: float = rates.get("hunger", 1.0)
        self.rate_thirst: float = rates.get("thirst", 1.0)
        self.rate_growth: float = rates.get("growth", 1.0)
        self.rate_reproduction: float = rates.get("reproduction", 1.0)
        self.rate_water_replenish: float = rates.get("water_replenishment", 1.0)

        # Per-tick event accumulator (cleared each step)
        self._events: list[dict[str, Any]] = []

        # Rain suppresses evaporation temporarily
        self._rain_ticks_remaining: int = 0

        # Randomization config (None = no randomization)
        rand_cfg = world_config.get("randomize")
        if rand_cfg is True:
            self._randomize_config: dict[str, Any] | None = {}
        elif isinstance(rand_cfg, dict):
            self._randomize_config = rand_cfg
        else:
            self._randomize_config = None

        # Randomize starting conditions if configured
        self._randomize_world()

        # Now initialize water moisture at final positions
        for source in self.water_sources:
            self._init_water_source(source)

        # Per-tick spawn/removal accumulators
        self._spawns: list[dict[str, Any]] = []
        self._removals: list[str] = []

        # Simple spatial index: rebuilt each tick (brute force for POC)
        self._positions: dict[str, list[float]] = {}

    # -- Public API ----------------------------------------------------------

    def step(self, dt: float = 0.1) -> dict[str, Any]:
        """
        Advance the simulation by one tick.

        Returns a tick packet dict ready for JSON serialization.
        """
        self.tick += 1
        self._events.clear()
        self._spawns.clear()
        self._removals.clear()

        # Rebuild spatial index
        self._rebuild_spatial_index()

        # Phase 1: continuous flow
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._apply_flow(entity, dt)

        # Phase 2: entity interactions (spatial)
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._resolve_interactions(entity)

        # Phase 3: guard conditions (discrete transitions)
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._evaluate_guards(entity)

        # Phase 4: voxel updates from entity activity
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._apply_voxel_effects(entity, dt)

        # Phase 4b: replenish water sources
        if self.water_sources:
            self._replenish_water_sources(dt)

        # Phase 4c: background soil evaporation (drought pressure)
        self._evaporate_soil(dt)

        # Phase 5: motor adapter inference (skeleton entities only)
        self._apply_motor_inference()

        # Phase 6: process removals
        for eid in self._removals:
            self.entities.pop(eid, None)

        # Phase 7: process spawns
        for spawn in self._spawns:
            self.entities[spawn["id"]] = spawn

        return self._build_tick_packet(dt)

    # -- Flow equations ------------------------------------------------------

    def _apply_flow(self, e: dict[str, Any], dt: float) -> None:
        """Apply continuous state variable changes based on entity type."""
        sv = e["state_vars"]
        meta = e["metadata"]
        etype = e["type"]

        if etype in ("ANIMAL", "BIRD"):
            self._flow_animal(e, sv, meta, dt)
        elif etype in ("PLANT", "TREE"):
            self._flow_plant(e, sv, meta, dt)
        elif etype == "INSECT":
            self._flow_insect(e, sv, meta, dt)
        elif etype == "MICROORGANISM":
            self._flow_microorganism(e, sv, meta, dt)

    def _flow_animal(
        self,
        e: dict[str, Any],
        sv: dict[str, float],
        meta: dict[str, Any],
        dt: float,
    ) -> None:
        base_metabolism = meta.get("metabolism_rate", 1.0)
        biome_mod = self.biome.hunger_rate_modifier * self.biome.metabolic_scaling

        # Hunger increases with metabolism
        sv["hunger"] = min(1.0, sv["hunger"] + 0.015 * base_metabolism * biome_mod * self.rate_hunger * dt)

        # Energy drains while moving, recovers while resting/idle
        if e["state"] in ("FORAGING", "HUNTING", "FLEEING"):
            drain = 0.02 * self.biome.energy_drain_modifier * dt
            sv["energy"] = max(0.0, sv["energy"] - drain)
        elif e["state"] in ("RESTING", "IDLE"):
            sv["energy"] = min(1.0, sv["energy"] + 0.03 * dt)

        # Hydration: decreases with temperature, recovers while DRINKING
        temp = self.climate.get("temperature", 20.0)
        evap = self.biome.evaporation_rate * (temp / 30.0) * self.rate_thirst

        if e["state"] == "DRINKING":
            # Recover hydration from soil moisture at current position
            gx, gy, gz = self.voxels.world_to_grid(*e["position"])
            soil_moisture = self.voxels.get("moisture", gx, gy, gz)
            recovery = 0.15 * soil_moisture * dt
            sv["hydration"] = min(1.0, sv["hydration"] + recovery)
            # Drain some soil moisture in the process
            self.voxels.add("moisture", gx, gy, gz, -0.01 * self.rate_thirst * dt)
            # Drain the nearest water source
            self._drain_nearest_water(e["position"], 0.003 * dt)
        else:
            sv["hydration"] = max(0.0, sv["hydration"] - evap * dt)

        # Age always increases
        sv["age"] += dt

        # Reproductive drive builds when reasonably fed and energetic
        if sv["energy"] > 0.5 and sv["hunger"] < 0.5 and sv["health"] > 0.5:
            sv["reproductive_drive"] = min(1.0, sv["reproductive_drive"] + 0.005 * self.rate_reproduction * dt)
        elif sv["hunger"] > 0.7 or sv["energy"] < 0.2:
            # Only decay drive when truly struggling
            sv["reproductive_drive"] = max(0.0, sv["reproductive_drive"] - 0.002 * dt)

        # Health degrades under starvation or dehydration
        if sv["hunger"] > 0.8:
            sv["health"] = max(0.0, sv["health"] - 0.01 * dt)
        if sv["hydration"] < 0.15:
            sv["health"] = max(0.0, sv["health"] - 0.015 * dt)

        # Movement toward current target (if any)
        if is_mobile(e) and e["state"] in ("FORAGING", "HUNTING", "FLEEING", "DRINKING"):
            self._move_toward_target(e, meta, dt)

    def _flow_plant(
        self,
        e: dict[str, Any],
        sv: dict[str, float],
        meta: dict[str, Any],
        dt: float,
    ) -> None:
        # Dormant plants are inactive — roots persist but no metabolism
        if e["state"] == "DORMANT":
            sv["age"] += dt
            return

        # Decrement pollination cooldown
        if e.get("_pollination_cooldown", 0) > 0:
            e["_pollination_cooldown"] -= 1

        temp = self.climate.get("temperature", 20.0)
        humidity = self.climate.get("humidity", 0.5)

        # Hydration loss from evapotranspiration (suppressed during rain)
        if self._rain_ticks_remaining <= 0:
            evap = self.biome.evaporation_rate * (temp / 30.0) * (1.0 - humidity * 0.5) * self.rate_thirst
            sv["hydration"] = max(0.0, sv["hydration"] - evap * dt)

        # Hydration recovery from soil moisture
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        soil_moisture = self.voxels.get("moisture", gx, gy, gz)
        water_demand = meta.get("water_demand", 0.03)
        uptake = min(water_demand * dt, soil_moisture * 0.1 * dt)
        sv["hydration"] = min(1.0, sv["hydration"] + uptake)

        # Growth: limited by the scarcest resource (Liebig's law)
        light = self.biome.light_availability
        soil_nutrients = self.voxels.get("nutrients", gx, gy, gz)
        growth_potential = min(sv["hydration"], soil_nutrients, light)
        base_growth = meta.get("growth_rate", 0.02)
        growth_inc = base_growth * growth_potential * self.biome.growth_rate_modifier * self.rate_growth * dt
        sv["growth"] = min(1.0, sv["growth"] + growth_inc)

        # Nutrient uptake from soil
        n_demand = meta.get("nutrient_demand", {})
        total_demand = sum(n_demand.values()) if isinstance(n_demand, dict) else 0.01
        sv["nutrient_store"] = min(1.0, sv["nutrient_store"] + total_demand * soil_nutrients * dt)

        # Health
        if sv["hydration"] < 0.15:
            sv["health"] = max(0.0, sv["health"] - 0.008 * dt)
        if sv["nutrient_store"] < 0.1:
            sv["health"] = max(0.0, sv["health"] - 0.005 * dt)

        # Ecosystem collapse pressure — trees depend on the living ecosystem
        # (nutrient cycling, mycorrhizal networks, soil microbiome)
        # Insect swarms alone can't sustain a tree
        if e["type"] == "TREE":
            support_count = sum(
                1 for ent in self.entities.values()
                if is_alive(ent)
                and ent["state"] != "DORMANT"
                and ent["type"] not in ("TREE", "INSECT")
            )
            if support_count <= 2:
                # Ecosystem has collapsed — accelerate decline
                sv["health"] = max(0.0, sv["health"] - 0.03 * dt)
                sv["hydration"] = max(0.0, sv["hydration"] - 0.01 * dt)

        sv["age"] += dt

        # Vegetative spreading (grass and flowers only, not trees)
        if e["type"] == "PLANT":
            self._try_plant_spread(e, sv, meta, dt)

    def _flow_insect(
        self,
        e: dict[str, Any],
        sv: dict[str, float],
        meta: dict[str, Any],
        dt: float,
    ) -> None:
        base_metabolism = meta.get("metabolism_rate", 0.8)
        biome_mod = self.biome.metabolic_scaling

        sv["hunger"] = min(1.0, sv["hunger"] + 0.01 * base_metabolism * biome_mod * self.rate_hunger * dt)

        # Insects can drink from water sources to survive without nectar
        if self._is_near_water(e["position"]):
            sv["hunger"] = max(0.0, sv["hunger"] - 0.005 * dt)
            sv["colony_health"] = min(1.0, sv["colony_health"] + 0.002 * dt)

        # Energy: drains while flying, recovers while resting or lingering
        if e["state"] == "RESTING" or e.get("_linger", 0) > 0:
            sv["energy"] = min(1.0, sv["energy"] + 0.02 * dt)
        else:
            sv["energy"] = max(0.0, sv["energy"] - 0.005 * biome_mod * dt)

        if sv["hunger"] > 0.7 or sv["energy"] < 0.2:
            # Drain scales with hunger — starvation accelerates
            drain = 0.008 + sv["hunger"] * 0.02
            sv["colony_health"] = max(0.0, sv["colony_health"] - drain * dt)

        sv["age"] += dt

        # Reproductive drive builds faster than deer (insects breed quickly)
        if sv["energy"] > 0.4 and sv["hunger"] < 0.5 and sv["colony_health"] > 0.4:
            sv["reproductive_drive"] = min(1.0, sv["reproductive_drive"] + 0.012 * self.rate_reproduction * dt)
        elif sv["hunger"] > 0.7 or sv["colony_health"] < 0.2:
            # Only decay drive when truly struggling
            sv["reproductive_drive"] = max(0.0, sv["reproductive_drive"] - 0.003 * dt)

        if is_mobile(e) and e["state"] == "FORAGING":
            # Linger timer: pause at flowers after pollination
            linger = e.get("_linger", 0)
            if linger > 0:
                e["_linger"] = linger - 1
                e["velocity"] = [0.0, 0.0, 0.0]
            else:
                self._move_toward_target(e, meta, dt)

    def _flow_microorganism(
        self,
        e: dict[str, Any],
        sv: dict[str, float],
        meta: dict[str, Any],
        dt: float,
    ) -> None:
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        organic = self.voxels.get("organic_matter", gx, gy, gz)
        moisture = self.voxels.get("moisture", gx, gy, gz)

        # Activity scales with organic matter and moisture
        optimal_activity = min(organic, moisture) * self.biome.microbial_activity_modifier
        sv["activity"] += (optimal_activity - sv["activity"]) * 0.1 * dt
        sv["activity"] = max(0.0, min(1.0, sv["activity"]))

        # Population grows when active, shrinks when dormant
        if sv["activity"] > 0.3:
            sv["population"] = min(1.0, sv["population"] + 0.005 * sv["activity"] * dt)
        else:
            sv["population"] = max(0.0, sv["population"] - 0.003 * dt)

    def _randomize_world(self) -> None:
        """Randomize starting conditions based on world config.

        Only runs if the world config contains a "randomize" key.
        If "randomize": true, uses defaults.
        If "randomize": { ... }, uses provided values.

        Transforms: applies a random rotation (0/90/180/270) and
        optional axis flip to the entire layout, then jitters positions
        and varies plant counts.
        """
        cfg = self._randomize_config
        if cfg is None:
            return

        if len(self.entities) < 5:
            return

        import time as _time

        rng = random.Random(int(_time.time()))
        jitter = cfg.get("jitter", 1.5)
        extra_grass_range = cfg.get("extra_grass", [0, 4])
        extra_flowers_range = cfg.get("extra_flowers", [0, 2])
        do_transform = cfg.get("transform", True)

        # Grid center for rotation/flip
        center = self._grid_max / 2.0

        # Pick a random symmetry (D4 group: 4 rotations × 2 flips = 8)
        if do_transform:
            rotation = rng.choice([0, 90, 180, 270])
            flip_x = rng.choice([True, False])
        else:
            rotation = 0
            flip_x = False

        def transform_pos(pos: list[float]) -> list[float]:
            """Apply rotation and flip relative to grid center."""
            x = pos[0] - center
            z = pos[2] - center

            # Rotate
            if rotation == 90:
                x, z = -z, x
            elif rotation == 180:
                x, z = -x, -z
            elif rotation == 270:
                x, z = z, -x

            # Flip
            if flip_x:
                x = -x

            return self._clamp_to_grid([x + center, 0.0, z + center])

        # Transform all entity positions
        for e in self.entities.values():
            pos = transform_pos(e["position"])
            e["position"][0], e["position"][1], e["position"][2] = pos[0], pos[1], pos[2]

        # Transform water source positions
        for source in self.water_sources:
            pos = transform_pos(source["position"])
            source["position"][0] = pos[0]
            source["position"][1] = pos[1]
            source["position"][2] = pos[2]

        # Jitter water source positions
        for source in self.water_sources:
            pos = source["position"]
            pos[0] += rng.uniform(-3.0, 3.0)
            pos[2] += rng.uniform(-3.0, 3.0)
            clamped = self._clamp_to_grid(pos)
            pos[0], pos[1], pos[2] = clamped[0], clamped[1], clamped[2]
            source["max_radius"] += rng.uniform(-0.5, 0.5)
            source["max_radius"] = max(1.0, source["max_radius"])
            source["radius"] = source["max_radius"]

        # Jitter entity positions
        for e in self.entities.values():
            pos = e["position"]
            pos[0] += rng.uniform(-jitter, jitter)
            pos[2] += rng.uniform(-jitter, jitter)
            clamped = self._clamp_to_grid(pos)
            pos[0], pos[1], pos[2] = clamped[0], clamped[1], clamped[2]

            # Slight variance in starting state
            sv = e["state_vars"]
            for key in ("hunger", "energy", "hydration", "health"):
                if key in sv:
                    sv[key] = max(0.0, min(1.0, sv[key] + rng.uniform(-0.05, 0.05)))

        # Spawn extra plants from templates
        grass_template = None
        flower_template = None
        for e in self.entities.values():
            if e.get("species") == "meadow_grass" and grass_template is None:
                grass_template = e
            if e.get("species") == "wildflower" and flower_template is None:
                flower_template = e

        if grass_template:
            n = rng.randint(extra_grass_range[0], extra_grass_range[1])
            for i in range(n):
                pos = self._clamp_to_grid([
                    rng.uniform(3.0, self._grid_max - 3.0),
                    0.0,
                    rng.uniform(3.0, self._grid_max - 3.0),
                ])
                child = init_entity({
                    "id": f"grass_r{i}",
                    "type": "PLANT",
                    "species": "meadow_grass",
                    "position": pos,
                    "metadata": dict(grass_template["metadata"]),
                    "state_vars": {
                        "growth": rng.uniform(0.05, 0.3),
                        "hydration": rng.uniform(0.6, 1.0),
                        "nutrient_store": rng.uniform(0.3, 0.6),
                        "health": 1.0,
                        "age": 0.0,
                    },
                })
                self.entities[child["id"]] = child

        if flower_template:
            n = rng.randint(extra_flowers_range[0], extra_flowers_range[1])
            for i in range(n):
                pos = self._clamp_to_grid([
                    rng.uniform(3.0, self._grid_max - 3.0),
                    0.0,
                    rng.uniform(3.0, self._grid_max - 3.0),
                ])
                child = init_entity({
                    "id": f"flower_r{i}",
                    "type": "PLANT",
                    "species": "wildflower",
                    "position": pos,
                    "metadata": dict(flower_template["metadata"]),
                    "state_vars": {
                        "growth": rng.uniform(0.05, 0.2),
                        "hydration": rng.uniform(0.6, 1.0),
                        "nutrient_store": rng.uniform(0.3, 0.6),
                        "health": 1.0,
                        "age": 0.0,
                    },
                })
                self.entities[child["id"]] = child

        # Push plants out of water
        self._push_entities_from_water(rng)

    def _push_entities_from_water(self, rng: random.Random) -> None:
        """Move any entity sitting inside a water source to just outside it."""
        for e in self.entities.values():
            if e["type"] in ("PLANT", "TREE"):
                pos = e["position"]
                for source in self.water_sources:
                    sx, _, sz = source["position"]
                    dx = pos[0] - sx
                    dz = pos[2] - sz
                    dist = math.sqrt(dx * dx + dz * dz)
                    push_r = source["max_radius"] + 1.0
                    if dist < push_r:
                        # Push outward to just past the edge
                        if dist < 0.1:
                            angle = rng.uniform(0, math.pi * 2)
                            dx, dz = math.cos(angle), math.sin(angle)
                            dist = 1.0
                        nx, nz = dx / dist, dz / dist
                        pos[0] = sx + nx * (push_r + 0.5)
                        pos[2] = sz + nz * (push_r + 0.5)
                        clamped = self._clamp_to_grid(pos)
                        pos[0], pos[1], pos[2] = clamped[0], clamped[1], clamped[2]

    # -- Movement ------------------------------------------------------------

    def _clamp_to_grid(self, pos: list[float]) -> list[float]:
        """Clamp a position to within the voxel grid bounds."""
        margin = 0.5  # keep entities slightly inside the edge
        lo = margin
        hi = self._grid_max - margin
        return [
            max(lo, min(hi, pos[0])),
            pos[1],
            max(lo, min(hi, pos[2])),
        ]

    def _move_toward_target(
        self, e: dict[str, Any], meta: dict[str, Any], dt: float,
    ) -> None:
        """
        Move an entity toward its current target position.
        If no target is set, pick one based on entity state and type.
        """
        target = e.get("_target")
        pos = e["position"]
        speed = meta.get("movement_speed", 1.0)

        if target is None:
            target = self._pick_movement_target(e, meta)
            if target is None:
                # Nothing to move toward — stay put
                e["velocity"] = [0.0, 0.0, 0.0]
                return
            e["_target"] = target

        dx = target[0] - pos[0]
        dz = target[2] - pos[2]
        dist = math.sqrt(dx * dx + dz * dz)

        if dist < 0.3:
            # Arrived at target — clear it
            e["_target"] = None
            e["velocity"] = [0.0, 0.0, 0.0]
            return

        # Normalize direction and apply speed
        step = min(speed * dt, dist)
        nx = dx / dist
        nz = dz / dist
        pos[0] += nx * step
        pos[2] += nz * step
        e["velocity"] = [nx * speed, 0.0, nz * speed]

        # Clamp to grid bounds
        pos[0] = max(0.0, min(self._grid_max, pos[0]))
        pos[2] = max(0.0, min(self._grid_max, pos[2]))

    def _pick_movement_target(
        self, e: dict[str, Any], meta: dict[str, Any],
    ) -> list[float] | None:
        """
        Pick a contextually appropriate movement target based on
        entity type and current state. Returns None if the entity
        should stay in place.
        """
        etype = e["type"]
        state = e["state"]
        pos = e["position"]

        # DRINKING — stay at water source (target set on state entry)
        if state == "DRINKING":
            return None

        # High reproductive drive — seek a mate
        if etype in ("ANIMAL", "BIRD", "INSECT"):
            drive = e["state_vars"].get("reproductive_drive", 0)
            if drive > 0.5:
                mate_pos = self._find_nearest_mate_pos(e)
                if mate_pos:
                    return mate_pos

        # Herbivore FORAGING — seek nearest edible plant
        if etype in ("ANIMAL", "BIRD") and state == "FORAGING":
            sensory = meta.get("sensory_range", 8.0)
            food = self._find_nearest_food(pos, sensory)
            if food:
                return food

        # Insect FORAGING — seek nearest flower (prefer FRUITING), fall back to water
        if etype == "INSECT" and state == "FORAGING":
            poll_range = meta.get("pollination_range", 6.0)
            flower = self._find_nearest_flower(pos, poll_range * 3)
            if flower:
                return flower
            # No flowers — seek water for survival
            water = self._find_nearest_water(pos)
            if water:
                return water

        # Default: wander randomly
        raw_target = [
            pos[0] + random.uniform(-3.0, 3.0),
            0.0,
            pos[2] + random.uniform(-3.0, 3.0),
        ]
        return self._clamp_to_grid(raw_target)

    def _find_nearest_food(
        self, pos: list[float], search_range: float,
    ) -> list[float] | None:
        """Find nearest edible plant. Prefers grass, falls back to flowers."""
        nearby = self._entities_in_range(pos, search_range)

        best_grass = None
        best_grass_dist = float("inf")
        best_flower = None
        best_flower_dist = float("inf")

        for other in nearby:
            if (
                other["type"] != "PLANT"
                or other["state"] in ("DEAD", "DYING", "DORMANT")
            ):
                continue
            if other["state_vars"].get("growth", 0) <= 0.1:
                continue
            d = self._distance(pos, other["position"])
            if d < 1.0:
                continue  # Skip plants we're already at

            if other.get("species") != "wildflower":
                if d < best_grass_dist:
                    best_grass_dist = d
                    best_grass = list(other["position"])
            else:
                if d < best_flower_dist:
                    best_flower_dist = d
                    best_flower = list(other["position"])

        # Prefer grass, fall back to flowers
        return best_grass or best_flower

    def _find_nearest_flower(
        self, pos: list[float], search_range: float,
    ) -> list[float] | None:
        """Find nearest flower for an insect, preferring FRUITING plants.
        Excludes grass — butterflies pollinate flowers, not grass."""
        nearby = self._entities_in_range(pos, search_range)
        best_fruiting = None
        best_fruiting_dist = float("inf")
        best_any_flower = None
        best_any_dist = float("inf")

        for other in nearby:
            if other["type"] != "PLANT":
                continue  # Skip trees — oaks are wind-pollinated
            if other["state"] in ("DEAD", "DYING", "DORMANT", "WILTING"):
                continue
            if other.get("species") == "meadow_grass":
                continue  # Butterflies don't visit grass
            d = self._distance(pos, other["position"])
            if d < 1.0:
                continue  # Skip flowers we're already at
            if other["state"] == "FRUITING":
                if d < best_fruiting_dist:
                    best_fruiting_dist = d
                    best_fruiting = list(other["position"])
            elif other.get("species") == "wildflower":
                if d < best_any_dist:
                    best_any_dist = d
                    best_any_flower = list(other["position"])

        # Prefer FRUITING plants, fall back to any wildflower
        return best_fruiting or best_any_flower

    # -- Entity interactions -------------------------------------------------

    def _rebuild_spatial_index(self) -> None:
        """Rebuild the flat spatial lookup. POC brute-force approach."""
        self._positions = {
            eid: list(e["position"])
            for eid, e in self.entities.items()
            if is_alive(e)
        }

    def _entities_in_range(
        self, pos: list[float], radius: float, exclude_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find all living entities within radius of a position."""
        results = []
        r2 = radius * radius
        for eid, epos in self._positions.items():
            if eid == exclude_id:
                continue
            dx = pos[0] - epos[0]
            dz = pos[2] - epos[2]
            if dx * dx + dz * dz <= r2:
                entity = self.entities.get(eid)
                if entity and is_alive(entity):
                    results.append(entity)
        return results

    def _resolve_interactions(self, e: dict[str, Any]) -> None:
        """Check for and resolve entity-entity interactions."""
        etype = e["type"]
        meta = e["metadata"]
        sv = e["state_vars"]

        if etype in ("ANIMAL", "BIRD"):
            sensory = meta.get("sensory_range", 8.0)
            diet = meta.get("diet", "herbivore")
            nearby = self._entities_in_range(e["position"], sensory, e["id"])

            # Check for predators (triggers FLEEING)
            for other in nearby:
                other_diet = other.get("metadata", {}).get("diet", "")
                if other_diet == "carnivore" and other["id"] != e["id"]:
                    if diet != "carnivore":
                        # This entity is potential prey — check proximity
                        if self._distance(e["position"], other["position"]) < 2.0:
                            old_state = e["state"]
                            e["state"] = "FLEEING"
                            e["_target"] = self._flee_direction(e["position"], other["position"])
                            if old_state != "FLEEING":
                                self._emit_state_change(e, old_state, "FLEEING")

            # Predation: carnivore catches prey
            if diet == "carnivore" and e["state"] == "HUNTING" and sv["hunger"] > 0.3:
                for other in nearby:
                    if other["type"] in ("ANIMAL", "BIRD", "INSECT") and other["id"] != e["id"]:
                        other_diet = other.get("metadata", {}).get("diet", "")
                        if other_diet != "carnivore":
                            if self._distance(e["position"], other["position"]) < 1.5:
                                self._predation_event(e, other)
                                break

            # Herbivore consumption (prefer grass, fall back to flowers)
            if diet == "herbivore" and e["state"] == "FORAGING" and sv["hunger"] > 0.2:
                grass_target = None
                flower_target = None
                for other in nearby:
                    if other["type"] != "PLANT" or other["state"] in ("DEAD", "DYING", "DORMANT"):
                        continue
                    if self._distance(e["position"], other["position"]) >= 2.0:
                        continue
                    if other.get("species") != "wildflower":
                        grass_target = other
                        break
                    elif flower_target is None:
                        flower_target = other

                target = grass_target or flower_target
                if target:
                    self._consumption_event(e, target)

        elif etype == "INSECT":
            # Don't re-pollinate while lingering at a flower
            if e.get("_linger", 0) > 0:
                pass
            else:
                poll_range = meta.get("pollination_range", 3.0)
                nearby = self._entities_in_range(e["position"], poll_range, e["id"])
                for other in nearby:
                    if (
                        other["type"] == "PLANT"
                        and other["state"] == "FRUITING"
                        and other.get("species") != "meadow_grass"
                        and other.get("_pollination_cooldown", 0) <= 0
                    ):
                        self._pollination_event(e, other)
                        break

    # -- Guard conditions ----------------------------------------------------

    def _evaluate_guards(self, e: dict[str, Any]) -> None:
        """Evaluate discrete state transition guards for an entity."""
        etype = e["type"]
        if etype in ("ANIMAL", "BIRD"):
            self._guards_animal(e)
        elif etype in ("PLANT", "TREE"):
            self._guards_plant(e)
        elif etype == "INSECT":
            self._guards_insect(e)
        elif etype == "MICROORGANISM":
            self._guards_microorganism(e)

    def _guards_animal(self, e: dict[str, Any]) -> None:
        sv = e["state_vars"]
        meta = e["metadata"]
        old_state = e["state"]

        # Priority-ordered guard checks (highest priority first)
        # Hysteresis: use lower thresholds to ENTER a need-state,
        # higher thresholds to EXIT (prevents oscillation at boundaries)

        # Death
        lifespan = meta.get("lifespan", 1000.0)
        if sv["health"] <= 0.0:
            e["state"] = "DYING"
            self._emit_event("DEATH_STARVE", e)
            self._schedule_removal(e)
        elif sv["age"] >= lifespan:
            e["state"] = "DYING"
            self._emit_event("DEATH_NATURAL", e)
            self._schedule_removal(e)

        # FLEEING is set by interaction resolver — don't override it here
        elif e["state"] == "FLEEING":
            if e.get("_target") is None:
                e["state"] = "IDLE"

        # Reproduction — only enters this branch if mate is actually available
        elif sv["reproductive_drive"] > 0.8 and self._find_mate(e):
            e["state"] = "REPRODUCING"
            self._reproduction_event(e)

        # Dehydration → drinking (enter at 0.2, stay until 0.6)
        elif e["state"] == "DRINKING":
            if sv["hydration"] >= 0.6:
                e["state"] = "IDLE"  # Will fall through to hunger/idle below next tick
                e["_target"] = None
            # else: stay DRINKING
        elif sv["hydration"] < 0.2:
            e["state"] = "DRINKING"
            # Seek nearest water source
            water_pos = self._find_nearest_water(e["position"])
            if water_pos:
                e["_target"] = water_pos

        # Exhaustion → resting (enter at 0.2, stay until 0.5)
        elif e["state"] == "RESTING":
            if sv["energy"] >= 0.5:
                e["state"] = "IDLE"
            # else: stay RESTING
        elif sv["energy"] < 0.2:
            e["state"] = "RESTING"

        # Hunger → foraging/hunting (enter at 0.3, stay until hunger < 0.15)
        elif e["state"] in ("FORAGING", "HUNTING"):
            if sv["hunger"] < 0.15:
                e["state"] = "IDLE"
            elif meta.get("diet") == "carnivore" and sv["hunger"] > 0.5:
                e["state"] = "HUNTING"
            # else: stay in current foraging/hunting state
        elif sv["hunger"] >= 0.3:
            diet = meta.get("diet", "herbivore")
            if diet == "carnivore" and sv["hunger"] > 0.5:
                e["state"] = "HUNTING"
            else:
                e["state"] = "FORAGING"

        # Default
        else:
            e["state"] = "IDLE"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    def _guards_plant(self, e: dict[str, Any]) -> None:
        sv = e["state_vars"]
        old_state = e["state"]

        if sv["health"] <= 0.0:
            if e["type"] == "TREE":
                # Trees die permanently
                e["state"] = "DEAD"
                self._emit_event("DEATH_NATURAL", e)
                self._schedule_removal(e)
                self._deposit_organic_matter(e)
            elif e["state"] != "DORMANT":
                # Grass and flowers go dormant — roots survive
                e["state"] = "DORMANT"
                sv["growth"] = 0.0
                e["_dormant_ticks"] = 0

        elif e["state"] == "DORMANT":
            # Check if conditions allow recovery
            gx, gy, gz = self.voxels.world_to_grid(*e["position"])
            soil_moisture = self.voxels.get("moisture", gx, gy, gz)
            soil_nutrients = self.voxels.get("nutrients", gx, gy, gz)

            e["_dormant_ticks"] = e.get("_dormant_ticks", 0) + 1

            if soil_moisture > 0.25 and soil_nutrients > 0.15:
                # Conditions improved — begin recovery
                sv["health"] = min(1.0, sv["health"] + 0.015)
                sv["hydration"] = min(1.0, sv["hydration"] + 0.02)
                if sv["health"] > 0.2:
                    e["state"] = "GROWING"
                    sv["growth"] = 0.05
                    sv["nutrient_store"] = max(sv["nutrient_store"], 0.2)
                    e["_dormant_ticks"] = 0

            elif e.get("_dormant_ticks", 0) > 2000:
                # Dormant too long with no recovery — roots die
                e["state"] = "DEAD"
                self._emit_event("DEATH_NATURAL", e)
                self._schedule_removal(e)
                self._deposit_organic_matter(e)

        elif sv["hydration"] <= 0.3 or sv["nutrient_store"] <= 0.2:
            e["state"] = "WILTING"
        elif sv["growth"] >= 0.5 and sv["health"] > 0.4:
            e["state"] = "FRUITING"
        else:
            e["state"] = "GROWING"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    def _guards_insect(self, e: dict[str, Any]) -> None:
        sv = e["state_vars"]
        old_state = e["state"]

        if sv["colony_health"] <= 0.0:
            e["state"] = "DEAD"
            self._emit_event("DEATH_NATURAL", e)
            self._schedule_removal(e)
        elif sv["colony_health"] < 0.3:
            e["state"] = "SWARMING"

        # Exhaustion → resting (enter at 0.15, stay until 0.4)
        elif e["state"] == "RESTING":
            if sv["energy"] >= 0.4:
                e["state"] = "FORAGING"
            # else: stay RESTING — recovering energy
        elif sv["energy"] < 0.15:
            e["state"] = "RESTING"
            e["velocity"] = [0.0, 0.0, 0.0]
            e["_target"] = None

        # Reproduction — only enters if mate is actually available
        elif sv.get("reproductive_drive", 0) > 0.7 and self._find_mate(e):
            e["state"] = "REPRODUCING"
            self._reproduction_event(e)

        # Default: active foraging (visiting flowers)
        else:
            e["state"] = "FORAGING"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    def _guards_microorganism(self, e: dict[str, Any]) -> None:
        sv = e["state_vars"]
        old_state = e["state"]

        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        organic = self.voxels.get("organic_matter", gx, gy, gz)

        if organic > 0.8 and sv["population"] > 0.7:
            e["state"] = "BLOOMING"
        elif sv["activity"] < 0.2:
            e["state"] = "DORMANT"
        else:
            e["state"] = "ACTIVE"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    # -- Voxel effects -------------------------------------------------------

    def _apply_voxel_effects(self, e: dict[str, Any], dt: float) -> None:
        """Update voxel layers based on entity activity."""
        pos = e["position"]
        gx, gy, gz = self.voxels.world_to_grid(*pos)
        etype = e["type"]

        if etype in ("PLANT", "TREE"):
            # Plants drain nutrients and moisture from their root voxel
            n_demand = e["metadata"].get("nutrient_demand", {})
            total_demand = sum(n_demand.values()) if isinstance(n_demand, dict) else 0.01
            self.voxels.add("nutrients", gx, gy, gz, -total_demand * dt)

            # Trees drain more water proportional to canopy and root depth
            base_demand = e["metadata"].get("water_demand", 0.03)
            canopy = e["metadata"].get("canopy_radius", 0.0)
            root_depth = e["metadata"].get("root_depth", 0.1)
            size_factor = 1.0 + canopy * 0.3 + root_depth * 0.2
            self.voxels.add("moisture", gx, gy, gz, -base_demand * size_factor * dt)

        elif etype == "MICROORGANISM":
            # Microorganisms decompose organic matter into nutrients
            activity = e["state_vars"]["activity"]
            rate = self.biome.decomposition_rate * activity * dt
            self.voxels.add("organic_matter", gx, gy, gz, -rate)
            self.voxels.add("nutrients", gx, gy, gz, rate * 0.8)  # Not 100% efficient

    # -- Motor inference -----------------------------------------------------

    def _apply_motor_inference(self) -> None:
        """
        Run the motor adapter on all skeleton entities this tick.

        Batches entities, builds context vectors per the adapter's spec,
        calls infer(), and writes latent vectors back to entities.
        """
        # Collect skeleton entities
        skeleton_entities: list[dict[str, Any]] = [
            e for e in self.entities.values()
            if is_alive(e) and e.get("skeleton_id")
        ]
        if not skeleton_entities:
            return

        # Build context vectors using the adapter's declared spec.
        # If the adapter provides type-specific specs (context_spec_for),
        # use those; otherwise use the single spec for all entities.
        adapter = self._motor_adapter
        has_type_specs = hasattr(adapter, "context_spec_for")

        contexts: list[list[float]] = []
        for entity in skeleton_entities:
            if has_type_specs:
                spec = adapter.context_spec_for(entity["type"])
            else:
                spec = adapter.context_spec()
            ctx = build_context(spec, entity, self.biome, self.climate)
            contexts.append(ctx)

        # Batch inference
        latents = adapter.infer(contexts)

        # Write results back to entities
        for entity, latent in zip(skeleton_entities, latents):
            entity["motion_latent"] = latent

    # -- Interaction events --------------------------------------------------

    def _predation_event(
        self, predator: dict[str, Any], prey: dict[str, Any],
    ) -> None:
        """Predator catches prey."""
        # Predator feeds
        predator["state_vars"]["hunger"] = max(0.0, predator["state_vars"]["hunger"] - 0.4)
        predator["state_vars"]["energy"] = min(1.0, predator["state_vars"]["energy"] + 0.3)

        # Prey dies
        prey["state"] = "DYING"
        self._schedule_removal(prey)
        self._deposit_organic_matter(prey)

        self._events.append({
            "type": "PREDATION",
            "tick": self.tick,
            "source_id": predator["id"],
            "target_id": prey["id"],
            "position": list(prey["position"]),
        })

    def _consumption_event(
        self, herbivore: dict[str, Any], plant: dict[str, Any],
    ) -> None:
        """Herbivore eats part of a plant."""
        herbivore["state_vars"]["hunger"] = max(0.0, herbivore["state_vars"]["hunger"] - 0.15)
        plant["state_vars"]["growth"] = max(0.0, plant["state_vars"]["growth"] - 0.1 * self.rate_consumption)
        plant["state_vars"]["health"] = max(0.0, plant["state_vars"]["health"] - 0.05 * self.rate_consumption)

        self._events.append({
            "type": "CONSUMPTION",
            "tick": self.tick,
            "source_id": herbivore["id"],
            "target_id": plant["id"],
            "position": list(plant["position"]),
        })

    def _pollination_event(
        self, insect: dict[str, Any], plant: dict[str, Any],
    ) -> None:
        """Insect pollinates a fruiting plant."""
        # Boost plant health slightly
        plant["state_vars"]["health"] = min(1.0, plant["state_vars"]["health"] + 0.02)
        insect["state_vars"]["hunger"] = max(0.0, insect["state_vars"]["hunger"] - 0.05)

        # Linger at the flower (1.5–3 seconds at 10Hz)
        insect["_linger"] = random.randint(15, 30)
        insect["_target"] = None  # Clear target so it stays put

        # Cooldown on the flower prevents re-pollination for ~5 seconds
        plant["_pollination_cooldown"] = 50

        self._events.append({
            "type": "POLLINATION",
            "tick": self.tick,
            "source_id": insect["id"],
            "target_id": plant["id"],
            "position": list(plant["position"]),
        })

    def _reproduction_event(self, parent: dict[str, Any]) -> None:
        """Entity reproduces — spawns a new entity nearby."""
        parent["state_vars"]["reproductive_drive"] = 0.0
        parent["state_vars"]["energy"] -= 0.3

        # Reproduction costs colony_health for insects
        if parent["type"] == "INSECT":
            parent["state_vars"]["colony_health"] = max(
                0.0, parent["state_vars"].get("colony_health", 1.0) - 0.08,
            )

        child = init_entity({
            "id": f"{parent['id']}_child_{self.tick}",
            "type": parent["type"],
            "species": parent.get("species", "unknown"),
            "position": self._clamp_to_grid([
                parent["position"][0] + random.uniform(-1.0, 1.0),
                0.0,
                parent["position"][2] + random.uniform(-1.0, 1.0),
            ]),
            "metadata": dict(parent["metadata"]),
            "skeleton_id": parent.get("skeleton_id"),
        })

        # Children inherit some parent stress — weak parents produce weaker offspring
        psv = parent["state_vars"]
        csv = child["state_vars"]
        csv["hunger"] = psv["hunger"] * 0.3
        csv["energy"] = max(0.4, psv["energy"] * 0.9)
        if "colony_health" in csv:
            csv["colony_health"] = max(0.4, psv.get("colony_health", 1.0) * 0.9)
        if "health" in csv:
            csv["health"] = max(0.5, psv.get("health", 1.0) * 0.95)

        self._spawns.append(child)

        self._events.append({
            "type": "REPRODUCTION",
            "tick": self.tick,
            "source_id": parent["id"],
            "target_id": child["id"],
            "position": list(child["position"]),
        })

    # -- Helpers -------------------------------------------------------------

    def _try_plant_spread(
        self, e: dict[str, Any], sv: dict[str, float],
        meta: dict[str, Any], dt: float,
    ) -> None:
        """Vegetative spreading for grass and flowers.
        Grass spreads via runners (short range, frequent).
        Flowers spread via seeds (longer range, less frequent).
        Only spreads when healthy with adequate resources."""

        # Must be healthy enough to spread
        if sv["health"] < 0.6 or sv["hydration"] < 0.3 or sv["growth"] < 0.5:
            return

        # Spread cooldown — check and decrement
        cooldown = e.get("_spread_cooldown", 0)
        if cooldown > 0:
            e["_spread_cooldown"] = cooldown - 1
            return

        # Spread chance per tick (scaled by reproduction rate)
        species = e.get("species", "")
        if species == "meadow_grass":
            spread_range = 2.0
            spread_chance = 0.008 * self.rate_reproduction
            cooldown_ticks = 80
        elif species == "wildflower":
            spread_range = 3.5
            spread_chance = 0.005 * self.rate_reproduction
            cooldown_ticks = 120
        else:
            return  # Unknown plant species — don't spread

        if random.random() > spread_chance:
            return

        # Pick a spread position
        pos = e["position"]
        spread_pos = self._clamp_to_grid([
            pos[0] + random.uniform(-spread_range, spread_range),
            0.0,
            pos[2] + random.uniform(-spread_range, spread_range),
        ])

        # Density check — don't spread if another plant is too close
        nearby = self._entities_in_range(spread_pos, 1.5)
        for other in nearby:
            if other["type"] in ("PLANT", "TREE") and other["id"] != e["id"]:
                e["_spread_cooldown"] = cooldown_ticks // 2
                return

        # Check soil quality at target
        gx, gy, gz = self.voxels.world_to_grid(*spread_pos)
        soil_moisture = self.voxels.get("moisture", gx, gy, gz)
        soil_nutrients = self.voxels.get("nutrients", gx, gy, gz)
        if soil_moisture < 0.15 or soil_nutrients < 0.1:
            return  # Too dry or barren to establish

        # Spawn the new plant (smaller, younger)
        child_meta = dict(meta)
        child = init_entity({
            "id": f"{e['id']}_s{self.tick}",
            "type": "PLANT",
            "species": species,
            "position": spread_pos,
            "metadata": child_meta,
            "state_vars": {
                "growth": 0.05,
                "hydration": soil_moisture * 0.8,
                "nutrient_store": 0.3,
                "health": 0.8,
                "age": 0.0,
            },
        })

        self._spawns.append(child)

        # Cost to parent — spreading takes resources
        sv["growth"] -= 0.1
        sv["nutrient_store"] = max(0.0, sv["nutrient_store"] - 0.05)

        # Set cooldown
        e["_spread_cooldown"] = cooldown_ticks

        self._events.append({
            "type": "REPRODUCTION",
            "tick": self.tick,
            "source_id": e["id"],
            "target_id": child["id"],
            "position": list(spread_pos),
        })

    def _deposit_organic_matter(self, e: dict[str, Any]) -> None:
        """When an entity dies, deposit organic matter at its voxel."""
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        mass = e.get("metadata", {}).get("body_mass", 10.0)
        deposit = min(0.3, mass / 500.0)
        self.voxels.add("organic_matter", gx, gy, gz, deposit)

    def apply_rain(self, intensity: float = 0.5) -> None:
        """Apply rainfall across the ecosystem.

        Args:
            intensity: 0.0–1.0 controlling moisture boost.
                0.2 = light drizzle, 0.5 = steady rain, 1.0 = downpour.
        """
        dims = self.voxels.dimensions
        moisture_boost = 0.3 * intensity

        # Global soil moisture boost
        for x in range(dims[0]):
            for z in range(dims[2]):
                current = self.voxels.get("moisture", x, 0, z)
                self.voxels.set(
                    "moisture", x, 0, z,
                    min(1.0, current + moisture_boost),
                )

        # Small nutrient boost (rain carries atmospheric nitrogen)
        nutrient_boost = 0.03 * intensity
        for x in range(dims[0]):
            for z in range(dims[2]):
                current = self.voxels.get("nutrients", x, 0, z)
                self.voxels.set(
                    "nutrients", x, 0, z,
                    min(1.0, current + nutrient_boost),
                )

        # Replenish water sources
        for source in self.water_sources:
            source["water_level"] = min(
                1.0,
                source["water_level"] + 0.4 * intensity,
            )
            source["radius"] = source["max_radius"] * source["water_level"]

        # Suppress evaporation for a period (wet ground stays wet)
        self._rain_ticks_remaining = 80

        # Rain falls on plants too — boost hydration and health directly
        for ent in self.entities.values():
            if not is_alive(ent):
                continue
            if ent["type"] in ("PLANT", "TREE"):
                sv = ent["state_vars"]
                sv["hydration"] = min(1.0, sv["hydration"] + 0.2 * intensity)
                sv["health"] = min(1.0, sv["health"] + 0.1 * intensity)
            elif ent["type"] in ("ANIMAL", "BIRD", "INSECT"):
                # Animals drink rainwater too
                sv = ent["state_vars"]
                if "hydration" in sv:
                    sv["hydration"] = min(1.0, sv["hydration"] + 0.1 * intensity)

        # Emit event so viz can show it
        self._events.append({
            "type": "RAIN",
            "tick": self.tick,
            "intensity": intensity,
            "position": [dims[0] / 2, 0.0, dims[2] / 2],
        })

    def _init_water_source(self, source: dict[str, Any]) -> None:
        """Initialize voxel moisture for a water source."""
        cx, cy, cz = source["position"]
        r = source["radius"]
        for ix in range(int(cx - r), int(cx + r) + 1):
            for iz in range(int(cz - r), int(cz + r) + 1):
                dx = ix - cx
                dz = iz - cz
                if dx * dx + dz * dz <= r * r:
                    gx, gy, gz = self.voxels.world_to_grid(float(ix), 0.0, float(iz))
                    self.voxels.set("moisture", gx, gy, gz, 0.95)

    def _replenish_water_sources(self, dt: float) -> None:
        """Update water source levels: evaporate, replenish, update radius."""
        for source in self.water_sources:
            # Evaporation drains the water level
            evap_loss = 0.002 * self.rate_thirst * dt
            # Groundwater replenishment recovers it
            replenish = 0.003 * self.rate_water_replenish * dt

            source["water_level"] = max(
                0.0, min(1.0, source["water_level"] - evap_loss + replenish),
            )

            # Effective radius shrinks with water level
            source["radius"] = source["max_radius"] * source["water_level"]

            # Update voxel moisture based on current level
            cx, cy, cz = source["position"]
            max_r = source["max_radius"]
            effective_r = source["radius"]

            for ix in range(int(cx - max_r), int(cx + max_r) + 1):
                for iz in range(int(cz - max_r), int(cz + max_r) + 1):
                    dx = ix - cx
                    dz = iz - cz
                    dist_sq = dx * dx + dz * dz
                    gx, gy, gz = self.voxels.world_to_grid(
                        float(ix), 0.0, float(iz),
                    )
                    if dist_sq <= effective_r * effective_r:
                        # Inside current water body — maintain moisture
                        target = 0.9 * source["water_level"]
                        current = self.voxels.get("moisture", gx, gy, gz)
                        if current < target:
                            refill = 0.05 * self.rate_water_replenish * dt
                            self.voxels.set(
                                "moisture", gx, gy, gz,
                                min(target, current + refill),
                            )
                    elif dist_sq <= max_r * max_r:
                        # Was water, now dried up — moisture decays
                        current = self.voxels.get("moisture", gx, gy, gz)
                        if current > 0.3:
                            self.voxels.set(
                                "moisture", gx, gy, gz,
                                max(0.3, current - 0.02 * dt),
                            )

    def _find_nearest_water(self, pos: list[float]) -> list[float] | None:
        """Find the nearest water source position, or None."""
        if not self.water_sources:
            return None
        best = None
        best_dist = float("inf")
        for source in self.water_sources:
            if source["water_level"] < 0.05:
                continue  # Skip dried-up sources
            d = self._distance(pos, source["position"])
            if d < best_dist:
                best_dist = d
                best = source
        if best is None:
            return None
        # Target the edge of the water source (don't walk to center)
        dx = best["position"][0] - pos[0]
        dz = best["position"][2] - pos[2]
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 0.1:
            return list(best["position"])
        # Aim for a point at the edge of the source radius
        r = best["radius"] * 0.5
        return [
            best["position"][0] - (dx / dist) * r,
            0.0,
            best["position"][2] - (dz / dist) * r,
        ]

    def _drain_nearest_water(self, pos: list[float], amount: float) -> None:
        """Drain water level from the nearest water source."""
        if not self.water_sources:
            return
        best = None
        best_dist = float("inf")
        for source in self.water_sources:
            d = self._distance(pos, source["position"])
            if d < source["max_radius"] * 2 and d < best_dist:
                best_dist = d
                best = source
        if best is not None:
            best["water_level"] = max(0.0, best["water_level"] - amount)

    def _is_near_water(self, pos: list[float]) -> bool:
        """Check if a position is within a water source's effective radius."""
        for source in self.water_sources:
            if source["water_level"] < 0.05:
                continue
            d = self._distance(pos, source["position"])
            if d <= source["radius"] + 1.0:
                return True
        return False

    def _evaporate_soil(self, dt: float) -> None:
        """Background soil moisture evaporation across the ground plane.
        Without water sources, the entire grid dries out over time.
        Suppressed temporarily after rainfall."""
        if self._rain_ticks_remaining > 0:
            self._rain_ticks_remaining -= 1
            return  # Ground is still wet from rain

        temp = self.climate.get("temperature", 20.0)
        humidity = self.climate.get("humidity", 0.5)
        evap = 0.001 * (temp / 25.0) * (1.0 - humidity * 0.6) * self.rate_thirst * dt

        dims = self.voxels.dimensions
        for x in range(dims[0]):
            for z in range(dims[2]):
                current = self.voxels.get("moisture", x, 0, z)
                if current > 0.05:
                    self.voxels.set("moisture", x, 0, z, max(0.05, current - evap))

    def _find_mate(self, e: dict[str, Any]) -> bool:
        """Check if a compatible mate is nearby (within sensory range)."""
        sensory = e["metadata"].get("sensory_range", 8.0)
        nearby = self._entities_in_range(e["position"], sensory, e["id"])
        for other in nearby:
            if (
                other["type"] == e["type"]
                and other.get("species") == e.get("species")
                and other["state_vars"].get("reproductive_drive", 0) > 0.3
            ):
                return True
        return False

    def _find_nearest_mate_pos(self, e: dict[str, Any]) -> list[float] | None:
        """Find position of nearest compatible mate across the full grid.
        Animals can detect mates at longer range (scent, calls)."""
        best_dist = float("inf")
        best_pos = None
        for other in self.entities.values():
            if other["id"] == e["id"]:
                continue
            if not is_alive(other):
                continue
            if other["type"] != e["type"]:
                continue
            if other.get("species") != e.get("species"):
                continue
            d = self._distance(e["position"], other["position"])
            if d < 1.0:
                continue  # Already next to them
            if d < best_dist:
                best_dist = d
                best_pos = list(other["position"])
        return best_pos

    def _flee_direction(
        self, pos: list[float], threat_pos: list[float],
    ) -> list[float]:
        """Compute a target position away from a threat."""
        dx = pos[0] - threat_pos[0]
        dz = pos[2] - threat_pos[2]
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 0.01:
            dx, dz = random.uniform(-1, 1), random.uniform(-1, 1)
            dist = math.sqrt(dx * dx + dz * dz)
        flee_dist = 8.0
        raw = [
            pos[0] + (dx / dist) * flee_dist,
            0.0,
            pos[2] + (dz / dist) * flee_dist,
        ]
        return self._clamp_to_grid(raw)

    @staticmethod
    def _distance(a: list[float], b: list[float]) -> float:
        dx = a[0] - b[0]
        dz = a[2] - b[2]
        return math.sqrt(dx * dx + dz * dz)

    def _emit_event(
        self,
        event_type: str,
        entity: dict[str, Any],
        target: dict[str, Any] | None = None,
    ) -> None:
        self._events.append({
            "type": event_type,
            "tick": self.tick,
            "source_id": entity["id"],
            "target_id": target["id"] if target else None,
            "position": list(entity["position"]),
        })

    def _emit_state_change(
        self, entity: dict[str, Any], old_state: str, new_state: str,
    ) -> None:
        self._events.append({
            "type": "STATE_CHANGE",
            "tick": self.tick,
            "source_id": entity["id"],
            "target_id": None,
            "position": list(entity["position"]),
            "prev_state": old_state,
            "new_state": new_state,
        })

    def _schedule_removal(self, entity: dict[str, Any]) -> None:
        if entity["id"] not in self._removals:
            self._removals.append(entity["id"])

    # -- Tick packet assembly ------------------------------------------------

    def _build_tick_packet(self, dt: float) -> dict[str, Any]:
        """Assemble the tick packet from current engine state."""
        packet: dict[str, Any] = {
            "tick": self.tick,
            "dt": dt,
        }

        # Entity updates (all living entities for now; delta-encode in v2)
        updates = []
        for e in self.entities.values():
            update: dict[str, Any] = {
                "id": e["id"],
                "state": e["state"],
                "position": [round(v, 4) for v in e["position"]],
                "velocity": [round(v, 4) for v in e.get("velocity", [0, 0, 0])],
                "state_vars": {k: round(v, 4) for k, v in e["state_vars"].items()},
            }
            # Only include motion_latent for entities with skeletons
            if e.get("skeleton_id"):
                update["motion_latent"] = e.get("motion_latent", [0.0, 0.0, 0.0, 0.0])
            updates.append(update)
        packet["entity_updates"] = updates

        # Spawns
        if self._spawns:
            packet["entity_spawns"] = [
                {
                    "id": s["id"],
                    "type": s["type"],
                    "species": s.get("species"),
                    "position": [round(v, 4) for v in s["position"]],
                    "skeleton_id": s.get("skeleton_id"),
                    "state": s["state"],
                    "state_vars": {k: round(v, 4) for k, v in s["state_vars"].items()},
                    "motion_latent": [0.0, 0.0, 0.0, 0.0],
                }
                for s in self._spawns
            ]

        # Removals
        if self._removals:
            packet["entity_removals"] = list(self._removals)

        # Events
        if self._events:
            packet["events"] = self._events

        # Voxel deltas
        voxel_packet = self.voxels.get_delta_packet()
        if voxel_packet:
            packet["voxel_deltas"] = voxel_packet

        # Water sources (static, but viz needs them for rendering)
        if self.water_sources:
            packet["water_sources"] = [
                {
                    "position": ws["position"],
                    "radius": ws["radius"],
                    "water_level": ws["water_level"],
                }
                for ws in self.water_sources
            ]

        return packet
