# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Layout Manager — World layout loading, transforms, and randomization.

Extracts all layout-related concerns from EcosystemEngine so the engine class
stays focused on its seven-phase tick loop. The LayoutManager owns:

  - Entity initialization from world JSON (raw entities → init_entity dict)
  - Water source parsing and moisture footprint seeding
  - Grid bounds calculation (_grid_max, margin clamping)
  - Randomization pipeline (D4 transforms, jitter, extra spawns, push-from-water)

The engine still owns spatial index helpers (_rebuild_spatial_index,
_entities_in_range) because they run on the hot path every tick.

Usage
─────
    layout = LayoutManager(world_config, voxels)
    entities, water_sources = layout.load()
    grid_max = layout.grid_max

    # Optional randomization (opt-in via world JSON "randomize" key)
    layout.randomize(entities, water_sources)
"""

from __future__ import annotations

import math
import random
import time as _time
from typing import Any

from .entities import init_entity

# ── Layout data types ────────────────────────────────────────────────────────

class LayoutResult:
    """Immutable result from loading a world layout.

    Attributes:
        entities: Dict of initialized entity dicts keyed by id.
        water_sources: List of water source dicts with position, radius, level.
        grid_max: World space max coordinate (derived from voxel dimensions).
    """
    __slots__ = ("entities", "water_sources", "grid_max")

    def __init__(
        self,
        entities: dict[str, dict[str, Any]],
        water_sources: list[dict[str, Any]],
        grid_max: float,
    ):
        self.entities = entities
        self.water_sources = water_sources
        self.grid_max = grid_max


# ── Layout Manager ───────────────────────────────────────────────────────────

class LayoutManager:
    """Handles world layout loading and optional randomization.

    Separates layout concerns (entity placement, water sources, transforms)
    from simulation concerns (tick loop, spatial queries, motor inference).

    Args:
        world_config: World definition dict (from JSON). Must contain
            ``environment`` and optionally ``entities``.
        voxels: The VoxelManager instance for grid bounds and moisture seeding.
    """

    def __init__(
        self,
        world_config: dict[str, Any],
        voxels: Any,  # VoxelManager — avoid circular import
    ):
        env = world_config["environment"]
        grid_cfg = env.get("voxel_grid", {})
        dims = tuple(grid_cfg.get("dimensions", [32, 32, 32]))

        self._world_config = world_config
        self._grid_max: float = (dims[0] - 1) * voxels.cell_size
        self._margin: float = 0.5
        self._voxels = voxels

        # Parse randomization config (opt-in via world JSON)
        rand_cfg = world_config.get("randomize")
        if rand_cfg is True:
            self._randomize_config: dict[str, Any] | None = {}
        elif isinstance(rand_cfg, dict):
            self._randomize_config = rand_cfg
        else:
            self._randomize_config = None

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def grid_max(self) -> float:
        """World space max coordinate (derived from voxel dimensions)."""
        return self._grid_max

    def load(self) -> LayoutResult:
        """Load entities and water sources from world config.

        Returns a LayoutResult with initialized entities, parsed water
        sources, and the computed grid_max. Water source moisture footprints
        are seeded into the voxel grid during this call.
        """
        env = self._world_config.get("environment", {})

        # Load entities from world JSON
        raw_entities: list[dict[str, Any]] = self._world_config.get("entities", [])
        entities: dict[str, dict[str, Any]] = {}
        for raw in raw_entities:
            e = init_entity(raw)
            entities[e["id"]] = e

        # Parse water sources from environment config
        water_sources = self._parse_water_sources(env)

        return LayoutResult(entities, water_sources, self._grid_max)

    def randomize(
        self,
        entities: dict[str, dict[str, Any]],
        water_sources: list[dict[str, Any]],
    ) -> None:
        """Apply optional randomization transforms to the layout.

        Mutates entities and water_sources in-place. Only runs if the world
        config includes a ``randomize`` key. Applies D4 symmetry transforms,
        position jitter, state variable noise, extra entity spawns, and
        pushes sessile entities out of water source footprints.

        Args:
            entities: Entity dict (mutated in-place).
            water_sources: Water source list (mutated in-place).
        """
        cfg = self._randomize_config
        if cfg is None or len(entities) < 5:
            return

        rng = random.Random(int(_time.time()))
        center = self._grid_max / 2.0

        # D4 symmetry transform (rotation + optional flip)
        do_transform = cfg.get("transform", True)
        if do_transform:
            rotation = rng.choice([0, 90, 180, 270])
            flip_x = rng.choice([True, False])
        else:
            rotation, flip_x = 0, False

        def transform_pos(pos: list[float]) -> list[float]:
            x, z = pos[0] - center, pos[2] - center
            if rotation == 90:
                x, z = -z, x
            elif rotation == 180:
                x, z = -x, -z
            elif rotation == 270:
                x, z = z, -x
            if flip_x:
                x = -x
            return self._clamp_to_grid([x + center, 0.0, z + center])

        # Apply transform to all entities and water sources
        for e in entities.values():
            e["position"][:] = transform_pos(e["position"])
        for source in water_sources:
            source["position"][:] = transform_pos(source["position"])

        # Water source position jitter
        for source in water_sources:
            pos = source["position"]
            pos[0] += rng.uniform(-3.0, 3.0)
            pos[2] += rng.uniform(-3.0, 3.0)
            pos[:] = self._clamp_to_grid(pos)
            source["max_radius"] = max(
                1.0, source["max_radius"] + rng.uniform(-0.5, 0.5)
            )
            source["radius"] = source["max_radius"]

        # Entity position jitter + state variable noise
        jitter = cfg.get("jitter", 1.5)
        for e in entities.values():
            pos = e["position"]
            pos[0] += rng.uniform(-jitter, jitter)
            pos[2] += rng.uniform(-jitter, jitter)
            pos[:] = self._clamp_to_grid(pos)

            sv = e["state_vars"]
            for key in ("hunger", "energy", "hydration", "health"):
                if key in sv:
                    sv[key] = max(0.0, min(1.0, sv[key] + rng.uniform(-0.05, 0.05)))

        # Extra grass and flower spawns
        self._spawn_extra_entities(entities, cfg, rng)

        # Push sessile entities out of water source footprints
        self._push_entities_from_water(entities, water_sources, rng)

    # ── Internal helpers ───────────────────────────────────────────────

    def _parse_water_sources(self, env: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse water sources from environment config."""
        water_sources: list[dict[str, Any]] = []
        for ws in env.get("water_sources", []):
            source = {
                "position": list(ws["position"]),
                "max_radius": ws.get("radius", 2.0),
                "radius": ws.get("radius", 2.0),
                "water_level": 1.0,
            }
            water_sources.append(source)
        return water_sources



    def _clamp_to_grid(self, pos: list[float]) -> list[float]:
        """Clamp position to grid bounds with margin."""
        lo, hi = self._margin, self._grid_max - self._margin
        return [max(lo, min(hi, pos[0])), pos[1], max(lo, min(hi, pos[2]))]

    def _spawn_extra_entities(
        self,
        entities: dict[str, dict[str, Any]],
        cfg: dict[str, Any],
        rng: random.Random,
    ) -> None:
        """Spawn extra grass and flower entities for visual density."""
        extra_grass_range = cfg.get("extra_grass", [0, 4])
        extra_flowers_range = cfg.get("extra_flowers", [0, 2])

        grass_tpl = flower_tpl = None
        for e in entities.values():
            if e.get("species") == "meadow_grass" and grass_tpl is None:
                grass_tpl = e
            if e.get("species") == "wildflower" and flower_tpl is None:
                flower_tpl = e

        if grass_tpl:
            for i in range(rng.randint(*extra_grass_range)):
                pos = self._clamp_to_grid([
                    rng.uniform(3.0, self._grid_max - 3.0), 0.0,
                    rng.uniform(3.0, self._grid_max - 3.0)])
                child = init_entity({
                    "id": f"grass_r{i}", "type": "PLANT",
                    "species": "meadow_grass", "position": pos,
                    "metadata": dict(grass_tpl["metadata"]),
                    "state_vars": {
                        "growth": rng.uniform(0.05, 0.3),
                        "hydration": rng.uniform(0.6, 1.0),
                        "nutrient_store": rng.uniform(0.3, 0.6),
                        "health": 1.0, "age": 0.0}})
                entities[child["id"]] = child

        if flower_tpl:
            for i in range(rng.randint(*extra_flowers_range)):
                pos = self._clamp_to_grid([
                    rng.uniform(3.0, self._grid_max - 3.0), 0.0,
                    rng.uniform(3.0, self._grid_max - 3.0)])
                child = init_entity({
                    "id": f"flower_r{i}", "type": "PLANT",
                    "species": "wildflower", "position": pos,
                    "metadata": dict(flower_tpl["metadata"]),
                    "state_vars": {
                        "growth": rng.uniform(0.05, 0.2),
                        "hydration": rng.uniform(0.6, 1.0),
                        "nutrient_store": rng.uniform(0.3, 0.6),
                        "health": 1.0, "age": 0.0}})
                entities[child["id"]] = child

    def _push_entities_from_water(
        self,
        entities: dict[str, dict[str, Any]],
        water_sources: list[dict[str, Any]],
        rng: random.Random,
    ) -> None:
        """Move sessile entities out of water source footprints."""
        for e in entities.values():
            metadata = e.get("metadata", {})
            # Check if entity is sessile/rooted via locomotion or type
            is_sessile = (
                e["type"] in ("PLANT", "TREE")
                or metadata.get("locomotion") in ("sessile", "rooted")
            )
            if not is_sessile:
                continue

            pos = e["position"]
            for source in water_sources:
                sx, _, sz = source["position"]
                dx, dz = pos[0] - sx, pos[2] - sz
                dist = math.sqrt(dx * dx + dz * dz)
                push_r = source["max_radius"] + 1.0
                if dist < push_r:
                    if dist < 0.1:
                        angle = rng.uniform(0, math.pi * 2)
                        dx, dz = math.cos(angle), math.sin(angle)
                        dist = 1.0
                    nx, nz = dx / dist, dz / dist
                    pos[0] = sx + nx * (push_r + 0.5)
                    pos[2] = sz + nz * (push_r + 0.5)
                    pos[:] = self._clamp_to_grid(pos)
