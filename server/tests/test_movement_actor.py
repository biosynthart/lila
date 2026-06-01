# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
Unit tests for MovementActor (Issue #53 — Phase 1/2 refactoring).

Tests MovementActor as a pure function: given an InteractionContext with
nearby entities and water sources, assert the correct SetTarget/ClearTarget
effects are returned. No simulation harness needed.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from ecosim.actors.movement_actors import MovementActor
from ecosim.effects import ClearTarget, EffectType, SetTarget


def make_movement_context(
    entity_id: str = "test_entity",
    species: str = "deer",
    state: str = "FORAGING",
    position: list[float] | None = None,
    target: list[float] | None = None,
    hunger: float = 0.5,
    hydration: float = 1.0,
    energy: float = 0.8,
    health: float = 1.0,
    reproductive_drive: float = 0.0,
    params: Any | None = None,
    nearby_entities: list[dict] | None = None,
    water_sources: list[dict] | None = None,
    compiled: Any | None = None,
    all_entities: dict | None = None,
    entity_type: str = "ANIMAL",
) -> MagicMock:
    """Build a mock context for MovementActor testing.

    Mimics the FlowContext produced by ConsumerFlowActor._build_movement_context().
    """
    from typing import Any

    pos = position or [5.0, 0.0, 5.0]
    entity = {
        "id": entity_id,
        "type": entity_type,
        "species": species,
        "state": state,
        "position": pos,
        "velocity": [0.0, 0.0, 0.0],
        "state_vars": {
            "hunger": hunger,
            "hydration": hydration,
            "energy": energy,
            "health": health,
            "reproductive_drive": reproductive_drive,
        },
    }
    if target is not None:
        entity["_target"] = target

    ctx = MagicMock()
    ctx.entity = entity
    ctx.tick = 1
    ctx.nearby_entities = nearby_entities or []
    ctx.water_sources = water_sources or []
    ctx._entities = all_entities or {}
    ctx._grid_max = 31.0  # grid bounds for wander clamping

    if params is not None:
        ctx.params = params
    else:
        p = MagicMock()
        p.species_id = species
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 5.0
        p.floral_affinity = False
        ctx.params = p

    if compiled is not None:
        ctx.compiled = compiled
    else:
        c = MagicMock()
        c.get_diet_order.return_value = []
        c.get_interactions.return_value = []
        ctx.compiled = c

    return ctx


class TestMovementActorForagingHerbivore(unittest.TestCase):
    """Test 1: FORAGING herbivore seeks food by diet preference."""

    def test_foraging_herbivore_seeks_food(self):
        """FORAGING herbivore → SetTarget to nearest food by preference."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 8.0
        p.floral_affinity = False

        nearby = [
            {
                "id": "grass_1",
                "species": "meadow_grass",
                "state": "GROWING",
                "position": [7.0, 0.0, 6.0],
                "state_vars": {"growth": 0.8},
            },
        ]

        compiled = MagicMock()
        compiled.get_diet_order.return_value = [("meadow_grass", 1)]

        ctx = make_movement_context(
            species="deer", state="FORAGING", params=p,
            nearby_entities=nearby, compiled=compiled,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        self.assertEqual(effects[0].entity_id, "test_entity")
        # Target should be the grass position
        self.assertAlmostEqual(effects[0].position[0], 7.0)
        self.assertAlmostEqual(effects[0].position[2], 6.0)

    def test_foraging_herbivore_prefers_higher_priority_food(self):
        """Herbivore picks nearest food from highest preference tier."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 8.0
        p.floral_affinity = False

        nearby = [
            {
                "id": "fern_1",
                "species": "bracken_fern",
                "state": "GROWING",
                "position": [6.0, 0.0, 5.5],  # closer but lower preference
                "state_vars": {"growth": 0.8},
            },
            {
                "id": "grass_1",
                "species": "meadow_grass",
                "state": "GROWING",
                "position": [8.0, 0.0, 7.0],  # farther but higher preference
                "state_vars": {"growth": 0.9},
            },
        ]

        compiled = MagicMock()
        # meadow_grass is pref=1 (higher), bracken_fern is pref=2 (lower)
        compiled.get_diet_order.return_value = [
            ("meadow_grass", 1), ("bracken_fern", 2)]

        ctx = make_movement_context(
            species="deer", state="FORAGING", params=p,
            nearby_entities=nearby, compiled=compiled,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        # Should target meadow_grass (higher preference) even though fern is closer
        self.assertAlmostEqual(effects[0].position[0], 8.0)


class TestMovementActorPollinator(unittest.TestCase):
    """Test 2: Pollinator seeks FRUITING flowers."""

    def test_pollinator_seeks_fruiting_flower(self):
        """Pollinator with floral_affinity → SetTarget to nearest FRUITING flower."""
        p = MagicMock()
        p.species_id = "butterfly"
        p.diet_type = "nectarivore"
        p.speed = 1.5
        p.sensory_range = 31.0
        p.floral_affinity = True

        nearby = [
            {
                "id": "flower_1",
                "species": "wildflower",
                "state": "FRUITING",
                "position": [10.0, 0.0, 12.0],
                "state_vars": {"health": 0.8},
                "_pollination_cooldown": 0,
            },
        ]

        compiled = MagicMock()
        compiled.get_diet_order.return_value = []
        compiled.get_interactions.return_value = [
            MagicMock(interaction_type="pollination", linger_ticks=15, cooldown_ticks=30)
        ]

        ctx = make_movement_context(
            species="butterfly", state="FORAGING", params=p,
            nearby_entities=nearby, compiled=compiled,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        self.assertAlmostEqual(effects[0].position[0], 10.0)
        self.assertAlmostEqual(effects[0].position[2], 12.0)

    def test_pollinator_skips_flower_on_cooldown(self):
        """Pollinator skips flowers that are on pollination cooldown."""
        p = MagicMock()
        p.species_id = "butterfly"
        p.diet_type = "nectarivore"
        p.speed = 1.5
        p.sensory_range = 31.0
        p.floral_affinity = True

        nearby = [
            {
                "id": "flower_1",
                "species": "wildflower",
                "state": "FRUITING",
                "position": [6.0, 0.0, 6.0],
                "state_vars": {"health": 0.8},
                "_pollination_cooldown": 20,  # On cooldown!
            },
            {
                "id": "flower_2",
                "species": "wildflower",
                "state": "FRUITING",
                "position": [15.0, 0.0, 18.0],
                "state_vars": {"health": 0.9},
                "_pollination_cooldown": 0,  # Available
            },
        ]

        compiled = MagicMock()
        compiled.get_diet_order.return_value = []
        compiled.get_interactions.return_value = [
            MagicMock(interaction_type="pollination", linger_ticks=15, cooldown_ticks=30)
        ]

        ctx = make_movement_context(
            species="butterfly", state="FORAGING", params=p,
            nearby_entities=nearby, compiled=compiled,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        # Should skip flower_1 (on cooldown) and target flower_2
        self.assertAlmostEqual(effects[0].position[0], 15.0)


class TestMovementActorFlowerExclusion(unittest.TestCase):
    """Test 3: Pollinator excludes recently visited flowers."""

    def test_excludes_recently_visited_flowers(self):
        """Visited flowers excluded during linger + cooldown ticks.

        Flowers with _pollination_cooldown > 0 are treated as recently visited
        and skipped by the pollinator's target selection. This prevents the
        butterfly from immediately re-targeting a flower it just left.
        """
        p = MagicMock()
        p.species_id = "butterfly"
        p.diet_type = "nectarivore"
        p.speed = 1.5
        p.sensory_range = 31.0
        p.floral_affinity = True

        nearby = [
            {
                "id": "flower_visited",
                "species": "wildflower",
                "state": "FRUITING",
                "position": [6.0, 0.0, 6.0],
                "state_vars": {"health": 0.8},
                "_pollination_cooldown": 45,  # Recently visited (linger + cooldown)
            },
            {
                "id": "flower_fresh",
                "species": "wildflower",
                "state": "FRUITING",
                "position": [20.0, 0.0, 22.0],
                "state_vars": {"health": 0.9},
                "_pollination_cooldown": 0,  # Not visited yet
            },
        ]

        compiled = MagicMock()
        compiled.get_diet_order.return_value = []
        compiled.get_interactions.return_value = [
            MagicMock(interaction_type="pollination", linger_ticks=15, cooldown_ticks=30)
        ]

        ctx = make_movement_context(
            species="butterfly", state="FORAGING", params=p,
            nearby_entities=nearby, compiled=compiled,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        # Should skip the visited flower and target the fresh one
        self.assertAlmostEqual(effects[0].position[0], 20.0)


class TestMovementActorHunting(unittest.TestCase):
    """Test 4: HUNTING carnivore seeks prey."""

    def test_hunting_carnivore_seeks_prey(self):
        """HUNTING carnivore → SetTarget to nearest prey."""
        p = MagicMock()
        p.species_id = "wolf"
        p.diet_type = "carnivore"
        p.speed = 1.2
        p.sensory_range = 10.0
        p.floral_affinity = False

        nearby = [
            {
                "id": "deer_1",
                "species": "deer",
                "state": "FORAGING",
                "position": [8.0, 0.0, 9.0],
                "state_vars": {"health": 1.0},
            },
        ]

        compiled = MagicMock()
        compiled.get_diet_order.return_value = [("deer", 1)]

        ctx = make_movement_context(
            species="wolf", state="HUNTING", hunger=0.7, params=p,
            nearby_entities=nearby, compiled=compiled,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        self.assertAlmostEqual(effects[0].position[0], 8.0)
        self.assertAlmostEqual(effects[0].position[2], 9.0)


class TestMovementActorDrinking(unittest.TestCase):
    """Test 5: DRINKING state — no target when at water."""

    def test_no_target_when_drinking_at_water(self):
        """DRINKING entity near water → ClearTarget (stay put and drink)."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 5.0
        p.floral_affinity = False

        water_sources = [
            {
                "position": [5.0, 0.0, 5.0],  # Entity is right at the water
                "radius": 2.0,
                "water_level": 0.8,
            },
        ]

        ctx = make_movement_context(
            species="deer", state="DRINKING", position=[5.5, 0.0, 5.5],
            params=p, water_sources=water_sources,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        # Should clear target — entity is at water, should stay and drink
        self.assertIsInstance(effects[0], ClearTarget)

    def test_drinking_navigates_to_water(self):
        """DRINKING entity far from water → SetTarget to nearest water."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 5.0
        p.floral_affinity = False

        water_sources = [
            {
                "position": [20.0, 0.0, 22.0],
                "radius": 3.0,
                "water_level": 0.9,
            },
        ]

        ctx = make_movement_context(
            species="deer", state="DRINKING", position=[5.0, 0.0, 5.0],
            params=p, water_sources=water_sources,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        # Target should be near the water source (approach point at radius*0.5)


class TestMovementActorWandering(unittest.TestCase):
    """Test 6: Wander when no targets found."""

    def test_wanders_when_no_targets_found(self):
        """No food/flowers/prey in range → random wander target."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 5.0
        p.floral_affinity = False

        compiled = MagicMock()
        compiled.get_diet_order.return_value = [("meadow_grass", 1)]

        ctx = make_movement_context(
            species="deer", state="FORAGING", position=[16.0, 0.0, 16.0],
            params=p, nearby_entities=[], compiled=compiled,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        # Wander target should be within grid bounds (margin to grid_max - margin)
        pos = effects[0].position
        self.assertGreaterEqual(pos[0], 0.5)
        self.assertLessEqual(pos[0], 30.5)
        self.assertGreaterEqual(pos[2], 0.5)
        self.assertLessEqual(pos[2], 30.5)

    def test_no_effect_when_target_already_set(self):
        """Entity with existing _target → no new effects (don't override)."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 5.0
        p.floral_affinity = False

        ctx = make_movement_context(
            species="deer", state="FORAGING", target=[10.0, 0.0, 12.0],
            params=p,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(effects, [],
                         "Should not emit effects when _target is already set")


class TestMovementActorMateSeeking(unittest.TestCase):
    """Bonus: High reproductive drive triggers mate seeking."""

    def test_seeks_mate_when_drive_high(self):
        """Reproductive drive > 0.5 → seek nearest conspecific mate."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 5.0
        p.floral_affinity = False

        all_entities = {
            "test_entity": {
                "id": "test_entity",
                "type": "ANIMAL",
                "species": "deer",
                "state": "IDLE",
                "position": [5.0, 0.0, 5.0],
                "state_vars": {"health": 1.0},
            },
            "mate_1": {
                "id": "mate_1",
                "type": "ANIMAL",
                "species": "deer",
                "state": "FORAGING",
                "position": [15.0, 0.0, 18.0],
                "state_vars": {"health": 0.9},
            },
        }

        ctx = make_movement_context(
            species="deer", state="IDLE", reproductive_drive=0.7,
            params=p, all_entities=all_entities,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)
        # Should target the mate's position
        self.assertAlmostEqual(effects[0].position[0], 15.0)
        self.assertAlmostEqual(effects[0].position[2], 18.0)


class TestMovementActorSwarming(unittest.TestCase):
    """Bonus: SWARMING state seeks water for colony survival."""

    def test_swarming_seeks_water(self):
        """SWARMING entity → SetTarget to nearest water source."""
        p = MagicMock()
        p.species_id = "bee"
        p.diet_type = "nectarivore"
        p.speed = 1.5
        p.sensory_range = 8.0
        p.floral_affinity = True

        water_sources = [
            {
                "position": [25.0, 0.0, 28.0],
                "radius": 2.0,
                "water_level": 0.7,
            },
        ]

        ctx = make_movement_context(
            species="bee", state="SWARMING", entity_type="INSECT",
            params=p, water_sources=water_sources,
        )

        actor = MovementActor()
        effects = actor.resolve(ctx)

        self.assertEqual(len(effects), 1)
        self.assertIsInstance(effects[0], SetTarget)


if __name__ == "__main__":
    unittest.main()
