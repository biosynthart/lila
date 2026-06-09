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

from ecosim.actors.guard_actors import ConsumerGuardActor
from ecosim.actors.interaction_actors import (
    FleeActor,
    HerbivoryActor,
    PollinationActor,
    PredationActor,
)
from ecosim.effects import (
    EffectType,
    EventRecord,
    RemoveEntity,
    SetEntityAttr,
    SetStateVar,
    StateTransition,
    StateVarDelta,
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
        """Flee triggers when predator is within sensory range."""
        p = MagicMock()
        p.species_id = "deer"
        p.diet_type = "herbivore"
        p.speed = 1.0
        p.sensory_range = 5.0
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

    def test_no_pollination_when_post_visit_cooldown_active(self):
        """No pollination when post-visit cooldown is still active.

        After lingering ends, the butterfly has a cooldown period during which
        it cannot re-pollinate — forcing it to fly away and explore before
        visiting another flower.
        """
        p = MagicMock()
        p.species_id = "butterfly"
        p.diet_type = "nectarivore"
        p.floral_affinity = True
        ctx = make_context(params=p)
        ctx.entity["_linger"] = 0  # Not lingering anymore
        ctx.entity["_pollination_cooldown"] = 10  # But cooldown still active
        actor = PollinationActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [])

    def test_pollination_sets_post_visit_cooldown_on_pollinator(self):
        """Pollination sets _pollination_cooldown on the pollinator entity.

        This cooldown prevents immediate re-pollination after lingering ends,
        forcing the butterfly to disperse across the field.
        """
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

        # Check that _pollination_cooldown is set on the pollinator (not just plant)
        # The actor uses SetEntityAttr for internal tracking vars like cooldowns
        attr_effects = [e for e in effects if isinstance(e, SetEntityAttr)]
        pollinator_cooldowns = [
            e for e in attr_effects
            if e.entity_id == "test_entity" and e.attr_name == "_pollination_cooldown"
        ]
        self.assertTrue(len(pollinator_cooldowns) >= 1,
                        "Pollinator should have _pollination_cooldown set")

    def test_no_pollination_when_flower_at_max_capacity(self):
        """No pollination when a flower already has max pollinators lingering.

        Enforces POLLINATOR_MAX_PER_FLOWER so butterflies disperse across the
        field instead of all clustering on one plant.
        """
        from ecosim.constants import POLLINATOR_MAX_PER_FLOWER

        p = MagicMock()
        p.species_id = "butterfly"
        p.diet_type = "nectarivore"
        p.floral_affinity = True
        p.pollination_relief = 0.15

        # Build a flower surrounded by max-capacity lingering pollinators
        nearby: list[dict] = [
            {
                "id": "wildflower_1",
                "species": "wildflower",
                "position": [6.0, 0.0, 5.0],
                "state": "FRUITING",
                "state_vars": {"health": 0.8},
                "_pollination_cooldown": 0,
            },
        ]
        # Add max-capacity pollinators lingering near the flower
        for i in range(POLLINATOR_MAX_PER_FLOWER):
            nearby.append({
                "id": f"butterfly_{i}",
                "species": "butterfly",
                "position": [6.1 + i * 0.1, 0.0, 5.1],  # within CROWD_RADIUS
                "state": "FORAGING",
                "state_vars": {"hunger": 0.5},
                "_linger": 10,  # actively lingering at the flower
            })

        ctx = make_context(params=p, nearby_entities=nearby)
        ctx.compiled.get_interactions.return_value = [
            MagicMock(
                interaction_type="pollination",
                linger_ticks=10,
                cooldown_ticks=30,
            )
        ]

        actor = PollinationActor()
        effects = actor.resolve(ctx)
        self.assertEqual(effects, [],
                         "Should not pollinate when flower is at max capacity")

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


class TestConsumerGuardActor(unittest.TestCase):
    """Tests for ConsumerGuardActor state transitions."""

    def _make_guard_context(self, **kwargs):
        """Build a mock GuardContext for testing."""
        entity_id = kwargs.pop("entity_id", "test_deer")
        species = kwargs.pop("species", "deer")
        state = kwargs.pop("state", "IDLE")
        hunger = kwargs.pop("hunger", 0.5)
        hydration = kwargs.pop("hydration", 1.0)
        energy = kwargs.pop("energy", 0.8)
        health = kwargs.pop("health", 1.0)
        age = kwargs.pop("age", 0.0)

        entity = {
            "id": entity_id,
            "type": kwargs.pop("entity_type", "ANIMAL"),
            "species": species,
            "state": state,
            "position": [5.0, 0.0, 5.0],
            "velocity": [0.0, 0.0, 0.0],
            "state_vars": {
                "hunger": hunger,
                "hydration": hydration,
                "energy": energy,
                "health": health,
                "age": age,
                "reproductive_drive": 0.0,
            },
            "metadata": {},
        }

        p = MagicMock()
        p.species_id = species
        p.diet_type = kwargs.pop("diet_type", "herbivore")
        p.speed = kwargs.pop("speed", 1.0)
        p.hunger_rate = 0.01
        p.thirst_rate = 0.02
        p.energy_drain = 0.02
        p.energy_recovery = 0.03
        p.health_drain_starving = 0.01
        p.health_drain_dehydrated = 0.015
        p.hunger_enter = kwargs.pop("hunger_enter", 0.3)
        p.hunger_exit = kwargs.pop("hunger_exit", 0.15)
        p.hydration_enter = kwargs.pop("hydration_enter", 0.2)
        p.hydration_exit = kwargs.pop("hydration_exit", 0.6)
        p.energy_enter = kwargs.pop("energy_enter", 0.2)
        p.energy_exit = kwargs.pop("energy_exit", 0.5)
        p.repro_drive_threshold = kwargs.pop("repro_drive_threshold", 0.8)
        p.generation_time_ticks = kwargs.pop("generation_time_ticks", 5000)
        p.floral_affinity = kwargs.pop("floral_affinity", False)

        ctx = MagicMock()
        ctx.entity = entity
        ctx.params = p
        ctx.tick = 1
        ctx._entities = {}
        ctx.voxel_grid = MagicMock()
        ctx.voxel_grid.world_to_grid.return_value = (5, 0, 5)
        ctx.voxel_grid.get.return_value = 0.5
        ctx.biome = MagicMock()
        ctx.climate = {"temperature": 20.0}
        ctx.rate_multipliers = {
            "consumption": 1.0,
            "hunger": 1.0,
            "thirst": 1.0,
            "growth": 1.0,
            "reproduction": 1.0,
        }

        return ctx, entity

    def test_normal_drinking_transition(self):
        """Normal drinking transition when hydration < enter and not foraging."""
        ctx, entity = self._make_guard_context(
            state="IDLE",
            hydration=0.15,
        )
        actor = ConsumerGuardActor()
        effects = actor.resolve(ctx)

        transitions = [e for e in effects if isinstance(e, StateTransition)]
        drinking_transitions = [t for t in transitions if t.new_state == "DRINKING"]
        self.assertTrue(len(drinking_transitions) >= 1,
                        "Should transition to DRINKING when hydration is low and not foraging")

    def test_drinking_blocked_while_foraging_normal_hydration(self):
        """Drinking should NOT trigger while FORAGING at normal hydration levels."""
        ctx, entity = self._make_guard_context(
            state="FORAGING",
            hunger=0.5,
            hydration=0.18,  # Below hydration_enter (0.2) but above DEHYDRATION_HYDRATION (0.15)
        )
        actor = ConsumerGuardActor()
        effects = actor.resolve(ctx)

        transitions = [e for e in effects if isinstance(e, StateTransition)]
        drinking_transitions = [t for t in transitions if t.new_state == "DRINKING"]
        self.assertEqual(len(drinking_transitions), 0,
                         "Should NOT transition to DRINKING while FORAGING at non-critical hydration")

    def test_critical_dehydration_overrides_foraging(self):
        """Critical dehydration (< DEHYDRATION_HYDRATION) should override FORAGING state.

        This is the key fix for ecosystem collapse: when plants go dormant/dead,
        herbivores stay in FORAGING but can't find food. Without this override,
        they'd wander forever and die of dehydration without ever drinking.
        """
        ctx, entity = self._make_guard_context(
            state="FORAGING",
            hunger=0.7,
            hydration=0.12,  # Below DEHYDRATION_HYDRATION (0.15)
        )
        actor = ConsumerGuardActor()
        effects = actor.resolve(ctx)

        transitions = [e for e in effects if isinstance(e, StateTransition)]
        drinking_transitions = [t for t in transitions if t.new_state == "DRINKING"]
        self.assertTrue(len(drinking_transitions) >= 1,
                        "Critical dehydration should override FORAGING and trigger DRINKING")

    def test_critical_dehydration_overrides_hunting(self):
        """Critical dehydration should also override HUNTING state."""
        ctx, entity = self._make_guard_context(
            state="HUNTING",
            diet_type="carnivore",
            hunger=0.8,
            hydration=0.10,
        )
        actor = ConsumerGuardActor()
        effects = actor.resolve(ctx)

        transitions = [e for e in effects if isinstance(e, StateTransition)]
        drinking_transitions = [t for t in transitions if t.new_state == "DRINKING"]
        self.assertTrue(len(drinking_transitions) >= 1,
                        "Critical dehydration should override HUNTING and trigger DRINKING")

    def test_drinking_exits_to_idle_when_hydrated(self):
        """Entity exits DRINKING to IDLE when hydration reaches exit threshold."""
        ctx, entity = self._make_guard_context(
            state="DRINKING",
            hydration=0.7,  # Above hydration_exit (0.6)
        )
        actor = ConsumerGuardActor()
        effects = actor.resolve(ctx)

        transitions = [e for e in effects if isinstance(e, StateTransition)]
        idle_transitions = [t for t in transitions if t.new_state == "IDLE"]
        self.assertTrue(len(idle_transitions) >= 1,
                        "Should exit DRINKING to IDLE when hydrated")

    def test_death_still_has_highest_priority(self):
        """Death should still override everything, including critical dehydration."""
        ctx, entity = self._make_guard_context(
            state="FORAGING",
            health=0.0,
            hydration=0.10,
        )
        actor = ConsumerGuardActor()
        effects = actor.resolve(ctx)

        transitions = [e for e in effects if isinstance(e, StateTransition)]
        dying_transitions = [t for t in transitions if t.new_state == "DYING"]
        self.assertTrue(len(dying_transitions) >= 1,
                        "Death should have highest priority over dehydration")


if __name__ == "__main__":
    unittest.main()
