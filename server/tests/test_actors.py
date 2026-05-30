# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
Unit tests for the Interaction Actor System (Phase 1 refactoring).

Tests each actor as a pure function: given an InteractionContext,
assert the correct list of Effects is returned. No simulation harness needed.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from ecosim.actors.interaction_actors import (
    FleeActor,
    HerbivoryActor,
    PollinationActor,
    PredationActor,
)
from ecosim.effects import (
    ClearTarget,
    EffectType,
    EventRecord,
    LingerEffect,
    RemoveEntity,
    SetStateVar,
    SetTarget,
    StateTransition,
    StateVarDelta,
    VoxelDelta,
)


def make_context(
    entity_id: str = "test_entity",
    species: str = "deer",
    state: str = "IDLE",
    hunger: float = 0.5,
    params=None,
    nearby_entities: list | None = None,
    voxel_grid=None,
    water_sources: list | None = None,
    biome=None,
    climate: dict | None = None,
) -> MagicMock:
    """Build a mock InteractionContext for testing."""
    ctx = MagicMock()
    ctx.entity = {
        "id": entity_id,
        "species": species,
        "state": state,
        "position": [5.0, 0.0, 5.0],
        "state_vars": {"hunger": hunger},
    }
    if params is not None:
        ctx.params = params
    else:
        # Default mock params
        p = MagicMock()
        p.species_id = species
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.hunger_rate = 0.01
        p.sensory_range = 5.0
        ctx.params = p

    ctx.nearby_entities = nearby_entities or []

    # Set up compiled ecology mock for interaction lookups
    if not hasattr(ctx, 'compiled'):
        ctx.compiled = MagicMock()

    # Set up voxel_grid mock to return (x, y, z) for world_to_grid calls
    if voxel_grid is None:
        vg = MagicMock()
        vg.world_to_grid.return_value = (5, 0, 5)
        ctx.voxel_grid = vg
    else:
        ctx.voxel_grid = voxel_grid

    ctx.water_sources = water_sources or []
    ctx.biome = biome or MagicMock()
    ctx.climate = climate or {"temperature": 20.0, "humidity": 0.5}
    ctx.rate_multipliers = {
        "consumption": 1.0,
        "hunger": 1.0,
        "thirst": 1.0,
        "growth": 1.0,
        "reproduction": 1.0,
    }
    return ctx


class TestFleeActor(unittest.TestCase):
    """Tests for FleeActor."""

    def test_no_flee_when_no_params(self):
        """No effects when params is None."""
        ctx = make_context(params=None)
        actor = FleeActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_no_flee_when_speed_zero(self):
        """No flee when entity speed is 0."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 0.0
        ctx = make_context(params=p)
        actor = FleeActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_no_flee_when_no_predators_nearby(self):
        """No flee when no predators in nearby entities."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        ctx = make_context(params=p, nearby_entities=[{"species": "grass"}])
        actor = FleeActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_flee_when_predator_nearby(self):
        """Flee triggers when predator is within trigger distance."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        # Mock flee targets from compiled ecology
        ctx = make_context(
            params=p,
            nearby_entities=[{"id": "wolf_1", "species": "wolf", "position": [6.0, 0.0, 5.0]}],
        )
        ctx.compiled.get_flee_targets.return_value = ["wolf"]

        actor = FleeActor()
        effects = actor.resolve(ctx)

        # Should have StateTransition to FLEEING and SetTarget
        effect_types = [e.effect_type for e in effects]
        self.assertIn(EffectType.STATE_TRANSITION, effect_types)
        self.assertIn(EffectType.SET_TARGET, effect_types)

        # Check state transition target
        transitions = [e for e in effects if isinstance(e, StateTransition)]
        self.assertTrue(len(transitions) >= 1)
        self.assertEqual(transitions[0].new_state, "FLEEING")


class TestPredationActor(unittest.TestCase):
    """Tests for PredationActor."""

    def test_no_predation_when_not_carnivore(self):
        """No predation effects for non-carnivores."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        ctx = make_context(params=p, state="HUNTING")
        actor = PredationActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_no_predation_when_not_hunting(self):
        """No predation when entity is not in HUNTING state."""
        p = MagicMock()
        p.species_id = "wolf"
        p.diet_type = "carnivore"
        ctx = make_context(params=p, state="FORAGING")
        actor = PredationActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_no_predation_when_hunger_too_low(self):
        """No predation when hunger <= 0.3."""
        p = MagicMock()
        p.species_id = "wolf"
        p.diet_type = "carnivore"
        ctx = make_context(params=p, state="HUNTING", hunger=0.2)
        actor = PredationActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_predation_when_prey_nearby(self):
        """Predation produces correct effects when prey is within catch distance."""
        p = MagicMock()
        p.species_id = "wolf"
        p.diet_type = "carnivore"
        p.predation_relief = 0.3
        p.predation_energy_gain = 0.4
        p.metabolic_rate = 1.5

        # Mock diet order and interactions
        ctx = make_context(
            params=p,
            state="HUNTING",
            nearby_entities=[{"id": "deer_1", "species": "deer", "position": [6.0, 0.0, 5.0]}],
        )
        ctx.compiled.get_diet_order.return_value = [("deer", 1)]
        ctx.compiled.get_interactions.return_value = [
            MagicMock(interaction_type="predation")
        ]

        actor = PredationActor()
        effects = actor.resolve(ctx)

        # Should have multiple effects: predator gains, prey killed, OM deposit, event
        self.assertTrue(len(effects) > 0)

        effect_types = [e.effect_type for e in effects]
        # Predator hunger relief
        self.assertIn(EffectType.STATE_VAR_DELTA, effect_types)
        # Prey health set to 0
        self.assertIn(EffectType.SET_STATE_VAR, effect_types)
        # Prey state transition to DYING
        self.assertIn(EffectType.STATE_TRANSITION, effect_types)
        # Prey removal
        self.assertIn(EffectType.REMOVE_ENTITY, effect_types)
        # OM deposit
        self.assertIn(EffectType.VOXEL_DELTA, effect_types)
        # Event record
        self.assertIn(EffectType.EVENT_RECORD, effect_types)

        # Verify predation event type
        events = [e for e in effects if isinstance(e, EventRecord)]
        self.assertTrue(len(events) >= 1)
        self.assertEqual(events[0].event_type, "PREDATION")


class TestHerbivoryActor(unittest.TestCase):
    """Tests for HerbivoryActor."""

    def test_no_herbivory_when_not_foraging(self):
        """No herbivory when entity is not FORAGING."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        ctx = make_context(params=p, state="IDLE")
        actor = HerbivoryActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_no_herbivory_when_hunger_too_low(self):
        """No herbivory when hunger <= 0.2."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        ctx = make_context(params=p, state="FORAGING", hunger=0.1)
        actor = HerbivoryActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_herbivory_when_plant_nearby(self):
        """Herbivory produces correct effects when plant is within consume distance."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.herbivory_relief = 0.2
        p.consumption_damage_growth = 0.15
        p.consumption_damage_health = 0.1

        ctx = make_context(
            params=p,
            state="FORAGING",
            nearby_entities=[{
                "id": "grass_1",
                "species": "meadow_grass",
                "position": [6.0, 0.0, 5.0],
                "state": "GROWING",
                "state_vars": {"growth": 0.8, "health": 1.0},
            }],
        )
        ctx.compiled.get_diet_order.return_value = [("meadow_grass", 1)]
        ctx.compiled.get_interactions.return_value = [
            MagicMock(interaction_type="herbivory")
        ]

        actor = HerbivoryActor()
        effects = actor.resolve(ctx)

        self.assertTrue(len(effects) > 0)

        effect_types = [e.effect_type for e in effects]
        # Hunger relief
        self.assertIn(EffectType.STATE_VAR_DELTA, effect_types)
        # Plant growth damage (SetStateVar because it's an absolute value calculation)
        self.assertIn(EffectType.SET_STATE_VAR, effect_types)
        # Consumption event
        events = [e for e in effects if isinstance(e, EventRecord)]
        self.assertTrue(len(events) >= 1)
        self.assertEqual(events[0].event_type, "CONSUMPTION")


class TestPollinationActor(unittest.TestCase):
    """Tests for PollinationActor."""

    def test_no_pollination_when_no_floral_affinity(self):
        """No pollination when entity has no floral affinity."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.floral_affinity = False
        ctx = make_context(params=p)
        actor = PollinationActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_no_pollination_when_already_lingering(self):
        """No pollination when entity is already lingering at a flower."""
        p = MagicMock()
        p.species_id = "butterfly"
        p.diet_type = "nectarivore"
        p.floral_affinity = True
        ctx = make_context(params=p)
        ctx.entity["_linger"] = 5  # Already lingering
        actor = PollinationActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_pollination_when_flower_nearby(self):
        """Pollination produces correct effects when FRUITING flower is nearby."""
        p = MagicMock()
        p.species_id = "butterfly"
        p.diet_type = "nectarivore"
        p.floral_affinity = True
        p.pollination_relief = 0.15

        ctx = make_context(
            params=p,
            nearby_entities=[{
                "id": "wildflower_1",
                "species": "wildflower",
                "position": [6.0, 0.0, 5.0],
                "state": "FRUITING",
                "state_vars": {"health": 0.8},
                "_pollination_cooldown": 0,
            }],
        )
        ctx.compiled.get_interactions.return_value = [
            MagicMock(
                interaction_type="pollination",
                linger_ticks=15,
                cooldown_ticks=30,
            )
        ]

        actor = PollinationActor()
        effects = actor.resolve(ctx)

        self.assertTrue(len(effects) > 0)

        effect_types = [e.effect_type for e in effects]
        # Plant health boost (SetStateVar)
        self.assertIn(EffectType.SET_STATE_VAR, effect_types)
        # Pollinator hunger relief
        self.assertIn(EffectType.STATE_VAR_DELTA, effect_types)
        # Linger at flower
        self.assertIn(EffectType.LINGER_EFFECT, effect_types)
        # Clear movement target
        self.assertIn(EffectType.CLEAR_TARGET, effect_types)
        # Set pollination cooldown on plant
        set_vars = [e for e in effects if isinstance(e, SetStateVar)]
        self.assertTrue(any(e.var_name == "_pollination_cooldown" for e in set_vars))
        # Pollination event
        events = [e for e in effects if isinstance(e, EventRecord)]
        self.assertTrue(len(events) >= 1)
        self.assertEqual(events[0].event_type, "POLLINATION")


class TestEffectBus(unittest.TestCase):
    """Tests for EffectBus.apply_effects."""

    def test_remove_entity_blocks_further_effects(self):
        """Effects on a removed entity should be silently dropped."""
        from ecosim.effects import EffectBus

        bus = EffectBus()
        entities = {
            "prey": {"id": "prey", "state": "GROWING", "position": [5.0, 0.0, 5.0],
                     "state_vars": {"health": 1.0}},
        }
        voxels = MagicMock()
        spawns = []
        removals = []
        events = []

        effects = [
            RemoveEntity(entity_id="prey", tick=1),
            # These should be dropped because prey is removed
            SetStateVar(entity_id="prey", var_name="health", value=0.0, tick=1),
            StateTransition(entity_id="prey", new_state="DYING", tick=1),
        ]

        bus.apply_effects(effects, entities, voxels, spawns, removals, events)

        self.assertIn("prey", removals)
        # Entity should still exist in dict (removal is deferred to Phase 7)
        self.assertIn("prey", entities)
        # But health should NOT have been set to 0 (dropped due to removal)
        self.assertEqual(entities["prey"]["state_vars"]["health"], 1.0)

    def test_state_transition_emits_event(self):
        """State transitions emit STATE_CHANGE events."""
        from ecosim.effects import EffectBus

        bus = EffectBus()
        entities = {
            "deer": {"id": "deer", "state": "IDLE", "position": [5.0, 0.0, 5.0],
                     "state_vars": {}},
        }
        voxels = MagicMock()
        spawns = []
        removals = []
        events = []

        effects = [
            StateTransition(entity_id="deer", new_state="FLEEING", tick=1),
        ]

        bus.apply_effects(effects, entities, voxels, spawns, removals, events)

        self.assertEqual(entities["deer"]["state"], "FLEEING")
        state_events = [e for e in events if e.get("type") == "STATE_CHANGE"]
        self.assertTrue(len(state_events) >= 1)
        self.assertEqual(state_events[0]["prev_state"], "IDLE")
        self.assertEqual(state_events[0]["new_state"], "FLEEING")

    def test_set_state_var_applies_absolute_value(self):
        """SetStateVar sets the absolute value."""
        from ecosim.effects import EffectBus

        bus = EffectBus()
        entities = {
            "plant": {"id": "plant", "state": "GROWING", "position": [5.0, 0.0, 5.0],
                      "state_vars": {"growth": 0.8}},
        }
        voxels = MagicMock()
        spawns = []
        removals = []
        events = []

        effects = [
            SetStateVar(entity_id="plant", var_name="growth", value=0.3, tick=1),
        ]

        bus.apply_effects(effects, entities, voxels, spawns, removals, events)

        self.assertEqual(entities["plant"]["state_vars"]["growth"], 0.3)

    def test_state_var_delta_clamps_to_0_1(self):
        """StateVarDelta clamps values to [0, 1]."""
        from ecosim.effects import EffectBus

        bus = EffectBus()
        entities = {
            "deer": {"id": "deer", "state": "IDLE", "position": [5.0, 0.0, 5.0],
                     "state_vars": {"hunger": 0.9}},
        }
        voxels = MagicMock()
        spawns = []
        removals = []
        events = []

        effects = [
            StateVarDelta(entity_id="deer", var_name="hunger", delta=0.2, tick=1),
        ]

        bus.apply_effects(effects, entities, voxels, spawns, removals, events)

        # 0.9 + 0.2 = 1.1 → clamped to 1.0
        self.assertEqual(entities["deer"]["state_vars"]["hunger"], 1.0)


if __name__ == "__main__":
    unittest.main()
