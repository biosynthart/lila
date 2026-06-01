# līlā — BYOM Ecosystem Simulation Engine
# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
#
# tests/test_traits.py — Unit tests for the trait derivation layer
#
# Tests are organized in three groups:
#   1. Allometric derivation functions (individual scaling laws)
#   2. Interaction template matching (which species interact)
#   3. TraitCompiler integration (full pipeline)

import json
import math
import sys
import os

# Add parent dir so we can import ecosim
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ecosim.traits import (
    TraitVector,
    DerivedParams,
    derive_metabolic_rate,
    derive_speed,
    derive_sensory_range,
    derive_flow_rates,
    derive_guard_thresholds,
    derive_consumption_damage,
    derive_all,
    trait_vector_from_dict,
    parse_species_definitions,
    REFERENCE_METABOLIC_RATE,
    FLOOR_HUNGER_RATE,
)
from ecosim.interactions import (
    Herbivory,
    Predation,
    Pollination,
    Decomposition,
    InteractionParams,
    ALL_TEMPLATES,
)
from ecosim.trait_compiler import (
    TraitCompiler,
    CompiledEcology,
    compile_world,
)


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures — Trait vectors for all species
# ─────────────────────────────────────────────────────────────────────────────

def make_deer() -> TraitVector:
    return TraitVector(
        species_id="deer",
        functional_group="herbivore",
        entity_class="ANIMAL",
        body_mass_kg=80.0,
        locomotion="quadruped",
        skeleton_id="quadruped_medium",
        thermoregulation="endotherm",
        diet_type="herbivore",
        diet_breadth=["graminoid", "forb"],
        trophic_level=2.0,
        reproductive_strategy="K_selected",
        clutch_size=1,
        generation_time_ticks=5000,
        thermal_range=(0, 40),
        drought_tolerance=0.3,
        sensory_range_multiplier=1.0,
        movement_budget=0.4,
    )

def make_butterfly() -> TraitVector:
    return TraitVector(
        species_id="butterfly",
        functional_group="pollinator",
        entity_class="INSECT",
        body_mass_kg=0.0005,
        locomotion="flight_insect",
        skeleton_id="insect_wing",
        thermoregulation="ectotherm",
        diet_type="nectarivore",
        diet_breadth=["forb:fruiting"],
        trophic_level=2.0,
        reproductive_strategy="r_selected",
        clutch_size=2,
        generation_time_ticks=2000,
        thermal_range=(10, 35),
        drought_tolerance=0.1,
        sensory_range_multiplier=1.2,
        movement_budget=0.6,
        floral_affinity=["insect_generalist"],
    )

def make_oak() -> TraitVector:
    return TraitVector(
        species_id="oak",
        functional_group="producer",
        entity_class="TREE",
        body_mass_kg=5000.0,
        locomotion="rooted",
        thermoregulation="autotroph",
        diet_type="autotroph",
        diet_breadth=[],
        trophic_level=1.0,
        canopy_radius=3.0,
        root_persistence=True,
        resource_tags=["mast"],
    )

def make_grass() -> TraitVector:
    return TraitVector(
        species_id="meadow_grass",
        functional_group="producer",
        entity_class="PLANT",
        body_mass_kg=0.01,
        locomotion="sessile",
        thermoregulation="autotroph",
        diet_type="autotroph",
        diet_breadth=[],
        trophic_level=1.0,
        spread_mode="runner",
        spread_range=2.0,
        root_persistence=True,
        resource_tags=["graminoid"],
    )

def make_wildflower() -> TraitVector:
    return TraitVector(
        species_id="wildflower",
        functional_group="producer",
        entity_class="PLANT",
        body_mass_kg=0.05,
        locomotion="sessile",
        thermoregulation="autotroph",
        diet_type="autotroph",
        diet_breadth=[],
        trophic_level=1.0,
        spread_mode="runner",
        spread_range=3.5,
        root_persistence=True,
        resource_tags=["forb"],
        pollination_syndrome="insect_generalist",
    )

def make_wolf() -> TraitVector:
    return TraitVector(
        species_id="wolf",
        functional_group="predator",
        entity_class="ANIMAL",
        body_mass_kg=40.0,
        locomotion="quadruped",
        skeleton_id="quadruped_medium",
        thermoregulation="endotherm",
        diet_type="carnivore",
        diet_breadth=["herbivore"],
        trophic_level=3.0,
        reproductive_strategy="K_selected",
        clutch_size=3,
        generation_time_ticks=8000,
        sensory_range_multiplier=1.5,
        movement_budget=0.5,
    )

def make_songbird() -> TraitVector:
    return TraitVector(
        species_id="songbird",
        functional_group="insectivore",
        entity_class="BIRD",
        body_mass_kg=0.025,
        locomotion="flight_bird",
        skeleton_id="bird_small",
        thermoregulation="endotherm",
        diet_type="omnivore",
        diet_breadth=["pollinator", "forb:fruiting"],
        trophic_level=2.5,
        reproductive_strategy="r_selected",
        clutch_size=4,
        generation_time_ticks=3000,
        sensory_range_multiplier=2.0,
    )

def make_mushroom() -> TraitVector:
    return TraitVector(
        species_id="mushroom",
        functional_group="decomposer",
        entity_class="MICROORGANISM",
        body_mass_kg=0.001,
        locomotion="sessile",
        thermoregulation="ectotherm",
        diet_type="decomposer",
        diet_breadth=["dead_organic_matter"],
        trophic_level=1.0,
        reproductive_strategy="r_selected",
        clutch_size=5,
        generation_time_ticks=300,
        spread_mode="spore",
        spread_range=4.0,
        root_persistence=False,
    )


ALL_5_SPECIES = [make_deer, make_butterfly, make_oak, make_grass, make_wildflower]
ALL_8_SPECIES = ALL_5_SPECIES + [make_wolf, make_songbird, make_mushroom]


# ═════════════════════════════════════════════════════════════════════════════
# 1. ALLOMETRIC DERIVATION TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestMetabolicRate:

    def test_deer_is_reference(self):
        """Deer (80 kg endotherm) should produce BMR ≈ 1.0 (reference)."""
        bmr = derive_metabolic_rate(make_deer())
        assert abs(bmr - REFERENCE_METABOLIC_RATE) < 0.01, \
            f"Deer BMR should be ~1.0 (reference), got {bmr}"

    def test_endotherm_exponent_075(self):
        """Endotherm BMR scales as M^0.75."""
        small = TraitVector("s", "", "ANIMAL", body_mass_kg=10.0,
                            locomotion="quadruped", thermoregulation="endotherm")
        big = TraitVector("b", "", "ANIMAL", body_mass_kg=100.0,
                          locomotion="quadruped", thermoregulation="endotherm")
        ratio = derive_metabolic_rate(big) / derive_metabolic_rate(small)
        expected = (100.0 / 10.0) ** 0.75  # 10^0.75 ≈ 5.623
        assert abs(ratio - expected) < 0.01, \
            f"Endotherm scaling: expected ratio {expected}, got {ratio}"

    def test_ectotherm_exponent_069(self):
        """Ectotherm BMR scales as M^0.69."""
        small = TraitVector("s", "", "INSECT", body_mass_kg=0.001,
                            locomotion="flight_insect", thermoregulation="ectotherm")
        big = TraitVector("b", "", "INSECT", body_mass_kg=0.01,
                          locomotion="flight_insect", thermoregulation="ectotherm")
        ratio = derive_metabolic_rate(big) / derive_metabolic_rate(small)
        expected = (0.01 / 0.001) ** 0.69  # 10^0.69 ≈ 4.898
        assert abs(ratio - expected) < 0.01

    def test_autotroph_reduced(self):
        """Autotrophs have reduced (not zero) BMR for threshold derivation."""
        oak_bmr = derive_metabolic_rate(make_oak())
        grass_bmr = derive_metabolic_rate(make_grass())
        deer_bmr = derive_metabolic_rate(make_deer())
        # Non-zero but much smaller than equivalent-mass animals
        assert oak_bmr > 0
        assert grass_bmr > 0
        assert grass_bmr < deer_bmr * 0.01

    def test_butterfly_much_lower_than_deer(self):
        """Butterfly (0.5 g ectotherm) << deer (80 kg endotherm)."""
        butterfly_bmr = derive_metabolic_rate(make_butterfly())
        deer_bmr = derive_metabolic_rate(make_deer())
        assert butterfly_bmr < deer_bmr * 0.01, \
            f"Butterfly BMR ({butterfly_bmr}) should be << deer ({deer_bmr})"


class TestSpeed:

    def test_deer_speed_positive(self):
        """Deer should have a reasonable positive speed."""
        speed = derive_speed(make_deer())
        assert 0.05 < speed < 0.5, f"Deer speed {speed} out of expected range"

    def test_butterfly_speed_positive(self):
        """Butterfly (insect flight) should have positive speed."""
        speed = derive_speed(make_butterfly())
        assert speed > 0, f"Butterfly speed should be > 0, got {speed}"

    def test_sessile_zero_speed(self):
        """Sessile and rooted organisms have zero speed."""
        assert derive_speed(make_grass()) == 0.0
        assert derive_speed(make_oak()) == 0.0

    def test_wolf_slower_than_deer(self):
        """Wolf (40 kg) should be slightly slower than deer (80 kg) by M^0.25."""
        wolf_speed = derive_speed(make_wolf())
        deer_speed = derive_speed(make_deer())
        assert wolf_speed < deer_speed, \
            f"Wolf ({wolf_speed}) should be slower than deer ({deer_speed})"

    def test_terrestrial_scaling(self):
        """Terrestrial speed scales as M^0.25."""
        speed_deer = derive_speed(make_deer())    # 80 kg
        speed_wolf = derive_speed(make_wolf())    # 40 kg
        ratio = speed_deer / speed_wolf
        expected = (80.0 / 40.0) ** 0.25
        assert abs(ratio - expected) < 0.01


class TestSensoryRange:

    def test_deer_sensory_range(self):
        """Deer should detect at several grid units."""
        sr = derive_sensory_range(make_deer())
        assert 5.0 < sr < 15.0, f"Deer sensory range {sr} out of expected range"

    def test_wolf_enhanced_sensory(self):
        """Wolf has 1.5x sensory multiplier — larger range than mass alone."""
        wolf_sr = derive_sensory_range(make_wolf())
        # Wolf at 40 kg with 1.5x vs deer at 80 kg with 1.0x
        deer_sr = derive_sensory_range(make_deer())
        # Wolf has smaller mass but enhanced multiplier
        assert wolf_sr > 0, f"Wolf sensory range should be > 0"

    def test_sessile_zero_sensory(self):
        """Sessile organisms have no active sensing."""
        assert derive_sensory_range(make_grass()) == 0.0
        assert derive_sensory_range(make_oak()) == 0.0


class TestFlowRates:

    def test_deer_flow_rates_positive(self):
        """Deer should have positive hunger, thirst, energy, repro rates."""
        bmr = derive_metabolic_rate(make_deer())
        rates = derive_flow_rates(bmr, make_deer())
        assert rates["hunger_rate"] > 0
        assert rates["thirst_rate"] > 0
        assert rates["energy_drain"] > 0
        assert rates["repro_drive_build"] > 0

    def test_ectotherm_lower_thirst(self):
        """Ectotherms lose 30% as much water as endotherms at same BMR."""
        endo = TraitVector("e", "", "ANIMAL", body_mass_kg=1.0,
                           locomotion="quadruped", thermoregulation="endotherm")
        ecto = TraitVector("x", "", "ANIMAL", body_mass_kg=1.0,
                           locomotion="quadruped", thermoregulation="ectotherm")
        endo_rates = derive_flow_rates(1.0, endo)
        ecto_rates = derive_flow_rates(1.0, ecto)
        ratio = ecto_rates["thirst_rate"] / endo_rates["thirst_rate"]
        assert abs(ratio - 0.3) < 0.01

    def test_autotroph_zero_rates(self):
        """Autotrophs have zero hunger/thirst/energy rates."""
        bmr = derive_metabolic_rate(make_oak())
        rates = derive_flow_rates(bmr, make_oak())
        assert rates["hunger_rate"] == 0.0
        assert rates["thirst_rate"] == 0.0
        assert rates["energy_drain"] == 0.0


class TestGuardThresholds:

    def test_deer_reference_thresholds(self):
        """Deer guard thresholds should match v0.0.1-alpha hard-coded values.

        Target values from the project state doc:
          hunger_enter: 0.3, hunger_exit: 0.15
          hydration_enter: 0.2, hydration_exit: 0.6
          energy_enter: 0.2, energy_exit: 0.5
          repro_threshold: 0.8 (K-selected)
        """
        bmr = derive_metabolic_rate(make_deer())
        guards = derive_guard_thresholds(bmr, make_deer())

        # Deer is the reference species (BMR=1.0), so log(1.0)=0 → adjustment=0,
        # meaning thresholds should be exactly the base values.
        assert abs(guards["hunger_enter"] - 0.3) < 0.01, \
            f"hunger_enter: expected ~0.3, got {guards['hunger_enter']}"
        assert abs(guards["hunger_exit"] - 0.15) < 0.01
        assert guards["hydration_enter"] == 0.2
        assert guards["hydration_exit"] == 0.6
        assert abs(guards["energy_enter"] - 0.2) < 0.01
        assert abs(guards["energy_exit"] - 0.5) < 0.01
        assert guards["repro_drive_threshold"] == 0.8

    def test_r_selected_lower_repro_threshold(self):
        """r-selected species have lower reproductive threshold (0.7 vs 0.8)."""
        bmr = derive_metabolic_rate(make_butterfly())
        guards = derive_guard_thresholds(bmr, make_butterfly())
        assert guards["repro_drive_threshold"] == 0.7

    def test_autotroph_thresholds(self):
        """Autotrophs use the same universal guard formula as all species."""
        bmr = derive_metabolic_rate(make_grass())
        guards = derive_guard_thresholds(bmr, make_grass())
        # Universal formula: hunger_enter ~0.27 (small BMR → negative adjustment)
        assert 0.2 < guards["hunger_enter"] < 0.35
        assert guards["hydration_enter"] == 0.2
        # Plant-specific thresholds derived from drought_tolerance
        assert guards["wilting_hydration"] > 0
        assert guards["dormancy_recovery_moisture"] > 0


# ═════════════════════════════════════════════════════════════════════════════
# 2. INTERACTION TEMPLATE TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestHerbivory:

    def test_deer_eats_grass(self):
        """Deer (herbivore, diet=[graminoid,forb]) matches grass (graminoid)."""
        h = Herbivory()
        assert h.matches(make_deer(), make_grass())

    def test_deer_eats_wildflower(self):
        """Deer diet includes forb → matches wildflower."""
        h = Herbivory()
        assert h.matches(make_deer(), make_wildflower())

    def test_deer_prefers_grass_over_flower(self):
        """Grass (graminoid, index 0) preferred over wildflower (forb, index 1)."""
        h = Herbivory()
        grass_params = h.compute_rates(make_deer(), make_grass(), 1.0)
        flower_params = h.compute_rates(make_deer(), make_wildflower(), 1.0)
        assert grass_params.preference_order < flower_params.preference_order

    def test_butterfly_does_not_graze(self):
        """Butterfly (nectarivore) is not a herbivore — no herbivory match."""
        h = Herbivory()
        assert not h.matches(make_butterfly(), make_grass())

    def test_wolf_does_not_eat_grass(self):
        """Wolf (carnivore) has no herbivory interaction."""
        h = Herbivory()
        assert not h.matches(make_wolf(), make_grass())

    def test_no_self_herbivory(self):
        """Plants don't eat themselves."""
        h = Herbivory()
        assert not h.matches(make_grass(), make_grass())  # autotroph not herbivore


class TestPredation:

    def test_wolf_hunts_deer(self):
        """Wolf (carnivore, diet=[herbivore]) matches deer (herbivore).
        Mass ratio: 40/80 = 0.5, within carnivore window [0.1, 2.0]."""
        p = Predation()
        assert p.matches(make_wolf(), make_deer())

    def test_wolf_does_not_hunt_butterfly(self):
        """Wolf diet=[herbivore] doesn't match butterfly (pollinator)."""
        p = Predation()
        assert not p.matches(make_wolf(), make_butterfly())

    def test_predation_triggers_flee(self):
        """Predation interaction should set flee_trigger=True."""
        p = Predation()
        params = p.compute_rates(make_wolf(), make_deer(), 1.0)
        assert params.flee_trigger is True

    def test_songbird_hunts_butterfly(self):
        """Songbird (omnivore, diet=[pollinator,...]) matches butterfly.
        Mass ratio: 0.025/0.0005 = 50, within insectivory window [1, 1000]."""
        p = Predation()
        assert p.matches(make_songbird(), make_butterfly())

    def test_songbird_insectivory_mass_ratio(self):
        """Songbird→butterfly uses insectivory window (pollinator in diet_breadth)."""
        p = Predation()
        # The mass ratio is 50, which exceeds the carnivore window (max 2.0)
        # but fits the insectivory window (max 1000). The template should
        # detect "pollinator" in diet_breadth and use the insectivory window.
        assert p.matches(make_songbird(), make_butterfly())

    def test_no_predation_on_plants(self):
        """Predation doesn't apply to plant targets."""
        p = Predation()
        assert not p.matches(make_wolf(), make_grass())


class TestPollination:

    def test_butterfly_pollinates_wildflower(self):
        """Butterfly (nectarivore, affinity=[insect_generalist]) matches
        wildflower (syndrome=insect_generalist)."""
        p = Pollination()
        assert p.matches(make_butterfly(), make_wildflower())

    def test_butterfly_does_not_pollinate_grass(self):
        """Grass has no pollination_syndrome — no match."""
        p = Pollination()
        assert not p.matches(make_butterfly(), make_grass())

    def test_deer_does_not_pollinate(self):
        """Deer (herbivore) has no floral_affinity — no pollination."""
        p = Pollination()
        assert not p.matches(make_deer(), make_wildflower())

    def test_pollination_has_linger_and_cooldown(self):
        """Pollination params include linger ticks and flower cooldown."""
        p = Pollination()
        bmr = derive_metabolic_rate(make_butterfly())
        params = p.compute_rates(make_butterfly(), make_wildflower(), bmr)
        assert params.linger_ticks > 0
        assert params.cooldown_ticks > 0

    def test_pollination_linger_capped(self):
        """Pollination linger ticks are capped by POLLINATION_MAX_LINGER."""
        from ecosim.constants import POLLINATION_MAX_LINGER
        p = Pollination()
        bmr = derive_metabolic_rate(make_butterfly())
        params = p.compute_rates(make_butterfly(), make_wildflower(), bmr)
        assert params.linger_ticks <= POLLINATION_MAX_LINGER, \
            f"Linger ({params.linger_ticks}) should be capped at {POLLINATION_MAX_LINGER}"


class TestDecomposition:

    def test_mushroom_is_decomposer(self):
        """Mushroom (diet_type=decomposer) is detected by matches_voxel."""
        d = Decomposition()
        assert d.matches_voxel(make_mushroom())

    def test_deer_is_not_decomposer(self):
        """Non-decomposers don't match."""
        d = Decomposition()
        assert not d.matches_voxel(make_deer())

    def test_decomposition_pairwise_never_matches(self):
        """Decomposition never matches in the pair-wise interaction check
        (it's entity↔voxel, not entity↔entity)."""
        d = Decomposition()
        assert not d.matches(make_mushroom(), make_deer())
        assert not d.matches(make_mushroom(), make_grass())

    def test_decomposer_has_mineralization_boost(self):
        """Decomposer voxel params include a mineralization boost."""
        d = Decomposition()
        bmr = derive_metabolic_rate(make_mushroom())
        params = d.compute_voxel_rates(make_mushroom(), bmr)
        assert params.mineralization_boost > 0


# ═════════════════════════════════════════════════════════════════════════════
# 3. TRAIT COMPILER INTEGRATION TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestTraitCompiler:

    def _compile_5(self) -> CompiledEcology:
        species = [f() for f in ALL_5_SPECIES]
        return TraitCompiler(species).compile()

    def _compile_8(self) -> CompiledEcology:
        species = [f() for f in ALL_8_SPECIES]
        return TraitCompiler(species).compile()

    def test_all_species_have_derived_params(self):
        """Every species should produce a DerivedParams entry."""
        compiled = self._compile_5()
        for f in ALL_5_SPECIES:
            sid = f().species_id
            assert sid in compiled.derived_params, f"Missing params for {sid}"

    def test_interaction_matrix_populated(self):
        """Interaction matrix should contain expected interactions."""
        compiled = self._compile_5()
        # deer→grass herbivory should exist
        ixn = compiled.get_interactions("deer", "meadow_grass")
        types = [i.interaction_type for i in ixn]
        assert "herbivory" in types, f"Expected deer→grass herbivory, got {types}"

    def test_butterfly_wildflower_pollination(self):
        """Butterfly→wildflower pollination should be in the matrix."""
        compiled = self._compile_5()
        ixn = compiled.get_interactions("butterfly", "wildflower")
        types = [i.interaction_type for i in ixn]
        assert "pollination" in types

    def test_flee_index_empty_for_5_species(self):
        """With 5 species (no predators), no flee relationships exist."""
        compiled = self._compile_5()
        assert len(compiled.flee_from) == 0

    def test_flee_index_with_wolf(self):
        """With wolf, deer should flee from wolf."""
        compiled = self._compile_8()
        assert "deer" in compiled.flee_from
        assert "wolf" in compiled.flee_from["deer"]

    def test_diet_preferences_deer(self):
        """Deer prefers grass (graminoid) over wildflower (forb)."""
        compiled = self._compile_5()
        prefs = compiled.get_diet_order("deer")
        species_order = [s for s, _ in prefs]
        assert "meadow_grass" in species_order
        assert "wildflower" in species_order
        assert species_order.index("meadow_grass") < species_order.index("wildflower")

    def test_decomposer_registry(self):
        """Mushroom should be registered as a decomposer."""
        compiled = self._compile_8()
        assert compiled.is_decomposer("mushroom")
        assert not compiled.is_decomposer("deer")

    def test_wolf_deer_predation_in_8sp(self):
        """8-species world: wolf→deer predation is in the matrix."""
        compiled = self._compile_8()
        ixn = compiled.get_interactions("wolf", "deer")
        types = [i.interaction_type for i in ixn]
        assert "predation" in types

    def test_songbird_butterfly_predation(self):
        """8-species world: songbird→butterfly predation (insectivory)."""
        compiled = self._compile_8()
        ixn = compiled.get_interactions("songbird", "butterfly")
        types = [i.interaction_type for i in ixn]
        assert "predation" in types

    def test_no_wolf_butterfly_interaction(self):
        """Wolf doesn't interact with butterfly (no matching diet)."""
        compiled = self._compile_8()
        ixn = compiled.get_interactions("wolf", "butterfly")
        assert len(ixn) == 0

    def test_trait_vectors_preserved(self):
        """Original trait vectors should be accessible in compiled output."""
        compiled = self._compile_5()
        assert "deer" in compiled.traits
        assert compiled.traits["deer"].body_mass_kg == 80.0


class TestCompileWorld:

    def test_missing_species_definitions_raises(self):
        """World without species_definitions raises ValueError."""
        config = {"biome": "temperate", "entities": []}
        try:
            compile_world(config)
            assert False, "Expected ValueError"
        except ValueError as exc:
            assert "species_definitions" in str(exc)

    def test_trait_world_returns_compiled(self):
        """World with species_definitions returns CompiledEcology."""
        config = {
            "species_definitions": [
                {
                    "species_id": "deer",
                    "functional_group": "herbivore",
                    "entity_class": "ANIMAL",
                    "body_mass_kg": 80.0,
                    "locomotion": "quadruped",
                    "thermoregulation": "endotherm",
                }
            ]
        }
        result = compile_world(config)
        assert isinstance(result, CompiledEcology)
        assert "deer" in result.derived_params


class TestJSONParsing:

    def test_parse_species_from_json(self):
        """Round-trip: JSON dict → TraitVector → DerivedParams."""
        d = {
            "species_id": "test_animal",
            "functional_group": "herbivore",
            "entity_class": "ANIMAL",
            "body_mass_kg": 50.0,
            "locomotion": "quadruped",
            "thermoregulation": "endotherm",
            "diet_type": "herbivore",
            "diet_breadth": ["graminoid"],
            "thermal_range": [-5, 38],
        }
        tv = trait_vector_from_dict(d)
        assert tv.species_id == "test_animal"
        assert tv.body_mass_kg == 50.0
        assert tv.thermal_range == (-5, 38)

        params = derive_all(tv)
        assert params.speed > 0
        assert params.metabolic_rate > 0

    def test_parse_species_definitions_missing_key(self):
        """World without species_definitions returns empty list."""
        assert parse_species_definitions({}) == []

    def test_parse_full_definitions_file(self):
        """Parse the full species_definitions.json file."""
        # Load the example file
        json_path = os.path.join(os.path.dirname(__file__), "..",
                                 "examples", "species_definitions.json")
        if os.path.exists(json_path):
            with open(json_path) as f:
                data = json.load(f)
            traits = parse_species_definitions(data)
            assert len(traits) == 8
            ids = {t.species_id for t in traits}
            assert "deer" in ids
            assert "wolf" in ids
            assert "mushroom" in ids


# ═════════════════════════════════════════════════════════════════════════════
# 4. CALIBRATION REPORT (run as a diagnostic, not a strict pass/fail)
# ═════════════════════════════════════════════════════════════════════════════

def print_calibration_report():
    """Print derived values for all species — used for manual calibration
    against the v0.0.1-alpha hard-coded constants."""

    print("\n" + "=" * 72)
    print("CALIBRATION REPORT — Derived values for all species")
    print("=" * 72)

    for factory in ALL_8_SPECIES:
        tv = factory()
        params = derive_all(tv)
        print(f"\n── {tv.species_id} ({tv.body_mass_kg} kg, {tv.thermoregulation}) ──")
        print(f"  Metabolic rate:    {params.metabolic_rate:.6f}")
        print(f"  Speed:             {params.speed:.4f} grid-units/tick")
        print(f"  Sensory range:     {params.sensory_range:.2f} grid-units")
        print(f"  Hunger rate:       {params.hunger_rate:.6f} /tick")
        print(f"  Thirst rate:       {params.thirst_rate:.6f} /tick")
        print(f"  Energy decay:      {params.energy_drain:.6f} /tick")
        print(f"  Repro drive build: {params.repro_drive_build:.6f} /tick")
        print(f"  Consumption rate:  {params.consumption_rate:.6f} /event")
        print(f"  Guards: hunger     {params.hunger_enter:.3f} / {params.hunger_exit:.3f}")
        print(f"  Guards: hydration  {params.hydration_enter:.3f} / {params.hydration_exit:.3f}")
        print(f"  Guards: energy     {params.energy_enter:.3f} / {params.energy_exit:.3f}")
        print(f"  Guards: repro      {params.repro_drive_threshold:.2f}")
        if params.spread_mode:
            print(f"  Spread: {params.spread_mode}, range {params.spread_range}")
        if params.canopy_radius:
            print(f"  Canopy radius: {params.canopy_radius}")

    # Interaction matrix
    species = [f() for f in ALL_8_SPECIES]
    compiled = TraitCompiler(species).compile()
    print(f"\n── Interaction Matrix ──")
    for (a, t), ixns in sorted(compiled.interaction_matrix.items()):
        for ix in ixns:
            extras = []
            if ix.flee_trigger:
                extras.append("flee")
            if ix.capture_probability < 1.0:
                extras.append(f"p={ix.capture_probability:.2f}")
            if ix.linger_ticks:
                extras.append(f"linger={ix.linger_ticks}")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            print(f"  {a:15s} → {t:15s} : {ix.interaction_type}{extra_str}")

    print(f"\n── Flee Index ──")
    for prey, predators in sorted(compiled.flee_from.items()):
        print(f"  {prey} flees from: {predators}")

    print(f"\n── Diet Preferences ──")
    for species_id, prefs in sorted(compiled.diet_preferences.items()):
        ordered = [f"{s} (pref={p})" for s, p in prefs]
        print(f"  {species_id}: {', '.join(ordered)}")

    print(f"\n── Decomposers ──")
    for sid, params in compiled.decomposers.items():
        print(f"  {sid}: mineralization_boost={params.mineralization_boost:.3f}")

    print()


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run tests manually (or use pytest)
    import traceback

    test_classes = [
        TestMetabolicRate,
        TestSpeed,
        TestSensoryRange,
        TestFlowRates,
        TestGuardThresholds,
        TestHerbivory,
        TestPredation,
        TestPollination,
        TestDecomposition,
        TestTraitCompiler,
        TestCompileWorld,
        TestJSONParsing,
    ]

    total = 0
    passed = 0
    failed = 0
    errors = []

    for cls in test_classes:
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for method_name in sorted(methods):
            total += 1
            try:
                getattr(instance, method_name)()
                passed += 1
                print(f"  ✓ {cls.__name__}.{method_name}")
            except Exception as e:
                failed += 1
                errors.append((cls.__name__, method_name, e))
                print(f"  ✗ {cls.__name__}.{method_name}: {e}")

    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{total} passed, {failed} failed")

    if errors:
        print(f"\nFailures:")
        for cls_name, method, err in errors:
            print(f"  {cls_name}.{method}: {err}")

    print()
    print_calibration_report()
