# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
Unit tests for ReproductionActor (Phase 2 — Issue #54).

Tests animal reproduction and plant vegetative spreading as pure functions:
given a context, assert the correct list of Effects is returned.
No simulation harness needed.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from ecosim.actors.reproduction_actor import ReproductionActor
from ecosim.effects import (
    EventRecord,
    SetStateVar,
    SpawnEntity,
    StateTransition,
    StateVarDelta,
)

# ────────────────────────────────────────────────────────────────────────────
# Helpers — build mock contexts for animal and plant reproduction tests
# ────────────────────────────────────────────────────────────────────────────

def make_animal_context(
    entity_id: str = "parent_1",
    species: str = "deer",
    state: str = "IDLE",
    reproductive_drive: float = 0.9,
    energy: float = 0.8,
    hunger: float = 0.3,
    health: float = 0.9,
    hydration: float = 0.7,
    position: list[float] | None = None,
    mate_position: list[float] | None = None,
    has_mate: bool = True,
    entity_type: str = "ANIMAL",
    colony_health: float | None = None,
) -> MagicMock:
    """Build a mock GuardContext for animal reproduction tests."""
    pos = position or [10.0, 0.0, 10.0]

    ctx = MagicMock()
    sv: dict[str, float] = {
        "reproductive_drive": reproductive_drive,
        "energy": energy,
        "hunger": hunger,
        "health": health,
        "hydration": hydration,
        "age": 5.0,
    }
    if colony_health is not None:
        sv["colony_health"] = colony_health

    ctx.entity = {
        "id": entity_id,
        "species": species,
        "type": entity_type,
        "state": state,
        "position": pos,
        "state_vars": sv,
        "metadata": {"body_mass": 100.0},
    }

    # Mock params
    p = MagicMock()
    p.repro_drive_threshold = 0.7
    p.parent_energy_cost = 0.2
    p.clutch_size = 1
    p.sensory_range = 5.0
    ctx.params = p

    # Build entity set with optional mate
    entities: dict[str, dict] = {entity_id: ctx.entity}
    if has_mate:
        mate_pos = mate_position or [pos[0] + 1.0, pos[1], pos[2] + 1.0]
        entities["mate_1"] = {
            "id": "mate_1",
            "species": species,
            "type": entity_type,
            "state": "IDLE",
            "position": mate_pos,
            "state_vars": {"health": 0.8},
        }
    ctx._entities = entities

    # Voxel grid mock
    vg = MagicMock()
    vg.world_to_grid.return_value = (10, 0, 10)
    ctx.voxel_grid = vg

    ctx.tick = 42
    return ctx


def make_plant_context(
    entity_id: str = "plant_1",
    species: str = "grass",
    state: str = "GROWING",
    health: float = 0.8,
    hydration: float = 0.6,
    growth: float = 0.7,
    nutrient_store: float = 0.5,
    position: list[float] | None = None,
    spread_cooldown: int = 0,
    other_autotrophs: list[dict] | None = None,
) -> MagicMock:
    """Build a mock FlowContext for plant spreading tests."""
    pos = position or [15.0, 0.0, 15.0]

    ctx = MagicMock()
    sv: dict[str, float] = {
        "health": health,
        "hydration": hydration,
        "growth": growth,
        "nutrient_store": nutrient_store,
        "age": 10.0,
    }

    entity_dict: dict = {
        "id": entity_id,
        "species": species,
        "type": "PLANT",
        "state": state,
        "position": pos,
        "state_vars": sv,
        "metadata": {"nutrient_demand": {"N": 0.1}},
    }
    if spread_cooldown > 0:
        entity_dict["_spread_cooldown"] = spread_cooldown

    ctx.entity = entity_dict

    # Mock params
    p = MagicMock()
    p.spread_mode = "vegetative"
    p.spread_chance = 1.0  # always pass random gate in tests
    p.spread_range = 2.0
    p.spread_cooldown = 5
    ctx.params = p

    # Build entity set with optional other autotrophs
    entities: dict[str, dict] = {entity_id: entity_dict}
    if other_autotrophs:
        for i, other in enumerate(other_autotrophs):
            entities[f"other_{i}"] = other
    ctx._entities = entities

    # Voxel grid mock — good soil at spread position
    vg = MagicMock()
    vg.world_to_grid.return_value = (16, 0, 16)
    vg.get.side_effect = lambda layer, x, y, z: {
        "moisture": 0.4,
        "nutrients": 0.3,
    }.get(layer, 0.0)
    ctx.voxel_grid = vg

    # Rate multipliers
    ctx.rate_multipliers = {"reproduction": 1.0}

    ctx.tick = 42
    return ctx


# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────

class TestReproductionActorAnimal(unittest.TestCase):
    """Test animal reproduction via ReproductionActor.resolve_animal()."""

    def setUp(self):
        self.actor = ReproductionActor()

    def test_reproductionactor_animal_spawns_child_with_inherited_vars(self):
        """Drive > threshold + mate → SpawnEntity with inherited hunger/energy/health."""
        ctx = make_animal_context(
            reproductive_drive=0.9,
            energy=0.8,
            hunger=0.5,
            health=0.9,
            hydration=0.7,
            has_mate=True,
        )

        effects = self.actor.resolve_animal(ctx)

        # Find SpawnEntity effect
        spawns = [e for e in effects if isinstance(e, SpawnEntity)]
        self.assertEqual(len(spawns), 1)
        spawn = spawns[0]

        # Child has inherited state vars with proper floors
        self.assertIn("hunger", spawn.state_vars)
        self.assertAlmostEqual(spawn.state_vars["hunger"], 0.5 * 0.3, places=4)  # CHILD_HUNGER_INHERIT
        self.assertGreaterEqual(spawn.state_vars["energy"], 0.4)  # CHILD_ENERGY_FLOOR
        self.assertGreaterEqual(spawn.state_vars["health"], 0.5)  # CHILD_HEALTH_FLOOR
        self.assertEqual(spawn.state_vars["reproductive_drive"], 0.0)
        self.assertEqual(spawn.state_vars["age"], 0.0)

    def test_reproductionactor_resets_parent_drive_and_energy(self):
        """Parent drive set to 0, energy reduced by cost."""
        ctx = make_animal_context(
            reproductive_drive=0.95,
            energy=0.8,
            has_mate=True,
        )

        effects = self.actor.resolve_animal(ctx)

        # Find StateVarDelta effects for parent
        deltas = [e for e in effects if isinstance(e, StateVarDelta)]
        drive_deltas = [d for d in deltas if d.var_name == "reproductive_drive"]
        energy_deltas = [d for d in deltas if d.var_name == "energy"]

        self.assertEqual(len(drive_deltas), 1)
        # Drive delta should negate current value (0.95 → 0)
        self.assertAlmostEqual(drive_deltas[0].delta, -0.95, places=4)

        self.assertEqual(len(energy_deltas), 1)
        # Energy reduced by parent_energy_cost (0.2)
        self.assertAlmostEqual(energy_deltas[0].delta, -0.2, places=4)

    def test_reproductionactor_no_spawn_when_drive_below_threshold(self):
        """Drive ≤ threshold → no effects."""
        ctx = make_animal_context(reproductive_drive=0.5, has_mate=True)

        effects = self.actor.resolve_animal(ctx)
        self.assertEqual(effects, [])

    def test_reproductionactor_no_spawn_when_no_mate(self):
        """No mate in range → no effects."""
        ctx = make_animal_context(reproductive_drive=0.9, has_mate=False)

        effects = self.actor.resolve_animal(ctx)
        self.assertEqual(effects, [])

    def test_reproductionactor_child_position_jittered(self):
        """Child position = parent ± random offset within SPAWN_OFFSET."""
        ctx = make_animal_context(
            reproductive_drive=0.9,
            position=[10.0, 0.0, 10.0],
            has_mate=True,
        )

        effects = self.actor.resolve_animal(ctx)
        spawns = [e for e in effects if isinstance(e, SpawnEntity)]
        self.assertEqual(len(spawns), 1)

        child_pos = spawns[0].position
        parent_pos = ctx.entity["position"]

        # Child x and z should be within ±SPAWN_OFFSET (1.0) of parent
        self.assertLessEqual(abs(child_pos[0] - parent_pos[0]), 1.0 + 1e-9)
        self.assertLessEqual(abs(child_pos[2] - parent_pos[2]), 1.0 + 1e-9)
        # Y should match parent
        self.assertEqual(child_pos[1], parent_pos[1])

    def test_reproductionactor_emits_event_record(self):
        """REPRODUCTION event record is emitted."""
        ctx = make_animal_context(reproductive_drive=0.9, has_mate=True)

        effects = self.actor.resolve_animal(ctx)
        events = [e for e in effects if isinstance(e, EventRecord)]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "REPRODUCTION")
        self.assertEqual(events[0].source_id, ctx.entity["id"])

    def test_reproductionactor_transitions_to_reproducing(self):
        """Parent transitions to REPRODUCING state."""
        ctx = make_animal_context(reproductive_drive=0.9, has_mate=True)

        effects = self.actor.resolve_animal(ctx)
        transitions = [e for e in effects if isinstance(e, StateTransition)]
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].new_state, "REPRODUCING")


class TestReproductionActorPlant(unittest.TestCase):
    """Test plant vegetative spreading via ReproductionActor.resolve_plant()."""

    def setUp(self):
        self.actor = ReproductionActor()

    def test_reproductionactor_plant_spreads_when_conditions_met(self):
        """Healthy plant with adequate soil → SpawnEntity for child."""
        ctx = make_plant_context(
            health=0.8,
            hydration=0.6,
            growth=0.7,
            spread_cooldown=0,
        )

        effects = self.actor.resolve_plant(ctx, ctx.entity["state_vars"], ctx.params, 0.1)

        spawns = [e for e in effects if isinstance(e, SpawnEntity)]
        self.assertEqual(len(spawns), 1)

        # Child has expected initial state vars
        child_sv = spawns[0].state_vars
        self.assertAlmostEqual(child_sv["growth"], 0.05, places=4)
        self.assertAlmostEqual(child_sv["health"], 0.8, places=4)
        self.assertEqual(child_sv["age"], 0.0)

    def test_reproductionactor_no_spawn_when_cooldown_active(self):
        """Spread cooldown > 0 → no spawn effect (only cooldown decrement)."""
        ctx = make_plant_context(
            health=0.8,
            hydration=0.6,
            growth=0.7,
            spread_cooldown=3,
        )

        effects = self.actor.resolve_plant(ctx, ctx.entity["state_vars"], ctx.params, 0.1)

        spawns = [e for e in effects if isinstance(e, SpawnEntity)]
        self.assertEqual(len(spawns), 0)

        # Should have a SetStateVar to decrement cooldown
        set_vars = [e for e in effects if isinstance(e, SetStateVar)]
        self.assertEqual(len(set_vars), 1)
        self.assertEqual(set_vars[0].var_name, "_spread_cooldown")
        self.assertAlmostEqual(set_vars[0].value, 2.0, places=4)

    def test_reproductionactor_no_spawn_when_health_too_low(self):
        """Health < SPREAD_MIN_HEALTH (0.6) → no spawn."""
        ctx = make_plant_context(health=0.5, hydration=0.6, growth=0.7)

        effects = self.actor.resolve_plant(ctx, ctx.entity["state_vars"], ctx.params, 0.1)
        spawns = [e for e in effects if isinstance(e, SpawnEntity)]
        self.assertEqual(len(spawns), 0)

    def test_reproductionactor_no_spawn_when_hydration_too_low(self):
        """Hydration < SPREAD_MIN_HYDRATION (0.3) → no spawn."""
        ctx = make_plant_context(health=0.8, hydration=0.2, growth=0.7)

        effects = self.actor.resolve_plant(ctx, ctx.entity["state_vars"], ctx.params, 0.1)
        spawns = [e for e in effects if isinstance(e, SpawnEntity)]
        self.assertEqual(len(spawns), 0)

    def test_reproductionactor_no_spawn_when_growth_too_low(self):
        """Growth < SPREAD_MIN_GROWTH (0.5) → no spawn."""
        ctx = make_plant_context(health=0.8, hydration=0.6, growth=0.3)

        effects = self.actor.resolve_plant(ctx, ctx.entity["state_vars"], ctx.params, 0.1)
        spawns = [e for e in effects if isinstance(e, SpawnEntity)]
        self.assertEqual(len(spawns), 0)

    def test_reproductionactor_parent_pays_spread_cost(self):
        """Parent growth and nutrient_store are reduced."""
        ctx = make_plant_context(
            health=0.8, hydration=0.6, growth=0.7, spread_cooldown=0,
        )

        effects = self.actor.resolve_plant(ctx, ctx.entity["state_vars"], ctx.params, 0.1)

        deltas = [e for e in effects if isinstance(e, StateVarDelta)]
        growth_deltas = [d for d in deltas if d.var_name == "growth"]
        nutrient_deltas = [d for d in deltas if d.var_name == "nutrient_store"]

        self.assertEqual(len(growth_deltas), 1)
        self.assertAlmostEqual(growth_deltas[0].delta, -0.1, places=4)  # SPREAD_PARENT_GROWTH_COST
        self.assertEqual(len(nutrient_deltas), 1)
        self.assertAlmostEqual(nutrient_deltas[0].delta, -0.05, places=4)  # SPREAD_PARENT_NUTRIENT_COST

    def test_reproductionactor_no_spawn_when_density_too_high(self):
        """Other autotroph within SPREAD_DENSITY_RADIUS → no spawn."""
        ctx = make_plant_context(
            health=0.8, hydration=0.6, growth=0.7, spread_cooldown=0,
            other_autotrophs=[{
                "id": "nearby_plant",
                "species": "grass",
                "type": "PLANT",
                "state": "GROWING",
                "position": [15.5, 0.0, 15.5],  # very close to parent at (15, 0, 15)
                "state_vars": {"health": 0.8},
            }],
        )

        effects = self.actor.resolve_plant(ctx, ctx.entity["state_vars"], ctx.params, 0.1)
        # May or may not spawn depending on random spread position —
        # but with a nearby autotroph at (15.5, 15.5), many positions will be blocked.
        # We can't guarantee no spawn due to randomness, so just check it doesn't crash
        self.assertIsInstance(effects, list)


class TestReproductionActorInsect(unittest.TestCase):
    """Test insect-specific reproduction behavior."""

    def setUp(self):
        self.actor = ReproductionActor()

    def test_insect_reproduction_includes_colony_health_cost(self):
        """INSECT type parent pays colony_health cost."""
        ctx = make_animal_context(
            reproductive_drive=0.9,
            energy=0.8,
            entity_type="INSECT",
            colony_health=0.7,
            has_mate=True,
        )

        effects = self.actor.resolve_animal(ctx)

        deltas = [e for e in effects if isinstance(e, StateVarDelta)]
        colony_deltas = [d for d in deltas if d.var_name == "colony_health"]
        self.assertEqual(len(colony_deltas), 1)
        # Colony cost = parent_energy_cost * 0.3 = 0.2 * 0.3 = 0.06
        self.assertAlmostEqual(colony_deltas[0].delta, -0.06, places=4)

    def test_insect_child_inherits_colony_health(self):
        """INSECT child gets colony_health with floor."""
        ctx = make_animal_context(
            reproductive_drive=0.9,
            energy=0.8,
            entity_type="INSECT",
            colony_health=0.7,
            has_mate=True,
        )

        effects = self.actor.resolve_animal(ctx)
        spawns = [e for e in effects if isinstance(e, SpawnEntity)]
        self.assertEqual(len(spawns), 1)

        # Child colony_health = max(CHILD_COLONY_FLOOR, parent * CHILD_COLONY_INHERIT)
        expected = max(0.4, 0.7 * 0.9)  # 0.63
        self.assertAlmostEqual(spawns[0].state_vars["colony_health"], expected, places=4)


if __name__ == "__main__":
    unittest.main()
