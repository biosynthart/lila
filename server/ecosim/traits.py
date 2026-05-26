# līlā — BYOM Ecosystem Simulation Engine
# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
#
# ecosim/traits.py — Functional trait vectors and allometric derivation layer
#
# Every species is a point in trait space. This module derives ALL engine
# parameters from functional traits using allometric scaling laws. No
# entity-class special cases. No hard-coded per-species constants survive
# in the engine — everything flows through DerivedParams.
#
# stdlib only — no external dependencies.

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# Calibration Constants
# ─────────────────────────────────────────────────────────────────────────────

B0_ENDOTHERM = 0.03738
B0_ECTOTHERM = 0.03738
REFERENCE_METABOLIC_RATE = 1.0

# Flow rates (per-second — engine multiplies by dt each tick)
HUNGER_FRACTION = 0.015
THIRST_FRACTION = 0.020
ENERGY_DRAIN_FRACTION = 0.020
ENERGY_RECOVERY_FRACTION = 0.030
REPRO_BUILD_FRACTION = 0.005
REPRO_DECAY_FRACTION = 0.002
ECTOTHERM_WATER_FACTOR = 0.3

# Health drain (per-second)
HEALTH_DRAIN_STARVING_FRACTION = 0.010
HEALTH_DRAIN_DEHYDRATED_FRACTION = 0.015
HEALTH_DRAIN_NUTRIENT_FRACTION = 0.005

# Speed
SPEED_BASE_TERRESTRIAL = 0.0335
SPEED_BASE_INSECT = 0.60
SPEED_BASE_BIRD = 0.55

# Sensory
SENSORY_BASE = 0.895

# Interaction relief
HERBIVORY_RELIEF_FRACTION = 0.15
PREDATION_RELIEF_FRACTION = 0.67
POLLINATION_RELIEF_FRACTION = 0.52
PREDATION_ENERGY_FRACTION = 0.30
CONSUMPTION_GROWTH_DAMAGE_FRACTION = 0.10
CONSUMPTION_HEALTH_DAMAGE_FRACTION = 0.05

# Guard adjustment
GUARD_ADJUSTMENT_SCALE = 0.1
GUARD_ADJUSTMENT_MAX = 0.15
GUARD_ADJUSTMENT_MIN = -0.1

# Floors (per-second — effective per-tick at dt=0.1 is floor × 0.1)
FLOOR_HUNGER_RATE = 0.008
FLOOR_THIRST_RATE = 0.003
FLOOR_ENERGY_DRAIN = 0.005
FLOOR_ENERGY_RECOVERY = 0.010
FLOOR_REPRO_BUILD = 0.003
FLOOR_SENSORY_RANGE = 3.0
FLOOR_CONSUMPTION = 0.01
FLOOR_HERBIVORY_RELIEF = 0.05
FLOOR_PREDATION_RELIEF = 0.10
# Pollination relief floor must exceed hunger gained over a typical pollination
# cycle (travel + linger ≈ 30 ticks × hunger_rate_floor 0.008 × dt=0.1 = 0.024).
# Butterflies need net negative drift per cycle so their average hunger stays
# below REPRO_BUILD_MAX_HUNGER (0.5), allowing reproductive drive to build.
# With 0.12 relief and ~0.06 hunger gained between visits, net drift is −0.004/tick,
# keeping butterflies at ~0.3 average hunger — well within the reproduction window.
FLOOR_POLLINATION_RELIEF = 0.12
FLOOR_HEALTH_DRAIN = 0.003


@dataclass
class TraitVector:
    species_id: str
    functional_group: str
    entity_class: str
    body_mass_kg: float
    locomotion: str
    skeleton_id: str | None = None
    thermoregulation: str = "endotherm"
    mass_specific_bmr: float | None = None
    diet_type: str = "herbivore"
    diet_breadth: list[str] = field(default_factory=list)
    trophic_level: float = 2.0
    reproductive_strategy: str = "K_selected"
    clutch_size: int = 1
    generation_time_ticks: int = 5000
    thermal_range: tuple[float, float] = (0.0, 40.0)
    drought_tolerance: float = 0.3
    shade_tolerance: float = 0.3
    sensory_range_multiplier: float = 1.0
    movement_budget: float = 0.4
    spread_mode: str | None = None
    spread_range: float | None = None
    spread_chance: float | None = None
    spread_cooldown: int | None = None
    root_persistence: bool = False
    canopy_radius: float | None = None
    resource_tags: list[str] = field(default_factory=list)
    pollination_syndrome: str | None = None
    floral_affinity: list[str] = field(default_factory=list)

    @property
    def is_mobile(self) -> bool:
        return self.locomotion not in ("sessile", "rooted")

    @property
    def is_autotroph(self) -> bool:
        return self.thermoregulation == "autotroph"

    @property
    def is_plant(self) -> bool:
        return self.entity_class in ("PLANT", "TREE")


@dataclass
class DerivedParams:
    species_id: str
    entity_class: str
    metabolic_rate: float
    speed: float
    sensory_range: float
    hunger_rate: float
    thirst_rate: float
    energy_drain: float
    energy_recovery: float
    repro_drive_build: float
    repro_drive_decay: float
    health_drain_starving: float
    health_drain_dehydrated: float
    health_drain_nutrient: float
    hunger_enter: float
    hunger_exit: float
    hydration_enter: float
    hydration_exit: float
    energy_enter: float
    energy_exit: float
    repro_drive_threshold: float
    wilting_hydration: float
    wilting_nutrients: float
    fruiting_growth: float
    fruiting_health: float
    dormancy_recovery_moisture: float
    dormancy_recovery_nutrients: float
    dormancy_timeout: int
    herbivory_relief: float
    predation_relief: float
    predation_energy_gain: float
    pollination_relief: float
    consumption_damage_growth: float
    consumption_damage_health: float
    clutch_size: int
    generation_time_ticks: int
    parent_energy_cost: float
    spread_mode: str | None = None
    spread_range: float | None = None
    spread_chance: float = 0.0
    spread_cooldown: int = 0
    root_persistence: bool = False
    canopy_radius: float | None = None
    thermal_range: tuple[float, float] = (0.0, 40.0)
    drought_tolerance: float = 0.3
    functional_group: str = ""
    diet_type: str = ""
    diet_breadth: list[str] = field(default_factory=list)
    resource_tags: list[str] = field(default_factory=list)
    locomotion: str = ""
    skeleton_id: str | None = None
    trophic_level: float = 2.0
    pollination_syndrome: str | None = None
    floral_affinity: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Allometric Derivation Functions
# ─────────────────────────────────────────────────────────────────────────────

def derive_metabolic_rate(traits: TraitVector) -> float:
    if traits.mass_specific_bmr is not None:
        return traits.mass_specific_bmr
    if traits.thermoregulation == "endotherm":
        return B0_ENDOTHERM * (traits.body_mass_kg ** 0.75)
    elif traits.thermoregulation == "ectotherm":
        return B0_ECTOTHERM * (traits.body_mass_kg ** 0.69)
    else:  # autotroph — reduced ectotherm scaling for threshold derivation
        return B0_ECTOTHERM * (traits.body_mass_kg ** 0.69) * 0.1


def derive_speed(traits: TraitVector) -> float:
    m = traits.body_mass_kg
    if traits.locomotion in ("sessile", "rooted"):
        return 0.0
    elif traits.locomotion == "flight_insect":
        return SPEED_BASE_INSECT * (m ** 0.17)
    elif traits.locomotion == "flight_bird":
        return SPEED_BASE_BIRD * (m ** 0.17)
    elif traits.locomotion in ("quadruped", "biped"):
        return SPEED_BASE_TERRESTRIAL * (m ** 0.25)
    return 0.0


def derive_sensory_range(traits: TraitVector) -> float:
    if not traits.is_mobile:
        return 0.0
    raw = SENSORY_BASE * (traits.body_mass_kg ** 0.5) * traits.sensory_range_multiplier
    return max(raw, FLOOR_SENSORY_RANGE)


def derive_flow_rates(metabolic_rate: float, traits: TraitVector) -> dict[str, float]:
    if traits.is_autotroph:
        return {
            "hunger_rate": 0.0, "thirst_rate": 0.0,
            "energy_drain": 0.0, "energy_recovery": 0.0,
            "repro_drive_build": 0.0, "repro_drive_decay": 0.0,
        }

    hunger = max(metabolic_rate * HUNGER_FRACTION, FLOOR_HUNGER_RATE)
    thirst = max(metabolic_rate * THIRST_FRACTION, FLOOR_THIRST_RATE)
    energy_drain = max(metabolic_rate * ENERGY_DRAIN_FRACTION, FLOOR_ENERGY_DRAIN)
    energy_recovery = max(metabolic_rate * ENERGY_RECOVERY_FRACTION, FLOOR_ENERGY_RECOVERY)
    # r-selected species reproduce eagerly — boost both the metabolic-scaled
    # value and the floor so small r-selected pollinators (butterflies) actually
    # reach the reproductive threshold within a useful timeframe. Without this
    # boost butterflies need ~2300 ticks of continuous favorable conditions to
    # reach drive=0.7; with the boost they reach it in ~778 ticks.
    repro_boost = 3.0 if traits.reproductive_strategy == "r_selected" else 1.0
    repro_build = max(metabolic_rate * REPRO_BUILD_FRACTION * repro_boost,
                      FLOOR_REPRO_BUILD * repro_boost)
    repro_decay = max(metabolic_rate * REPRO_DECAY_FRACTION, FLOOR_REPRO_BUILD * 0.5)

    if traits.thermoregulation == "ectotherm":
        thirst *= ECTOTHERM_WATER_FACTOR

    return {
        "hunger_rate": hunger, "thirst_rate": thirst,
        "energy_drain": energy_drain, "energy_recovery": energy_recovery,
        "repro_drive_build": repro_build, "repro_drive_decay": repro_decay,
    }


def derive_health_drain_rates(metabolic_rate: float) -> dict[str, float]:
    return {
        "health_drain_starving": max(metabolic_rate * HEALTH_DRAIN_STARVING_FRACTION,
                                     FLOOR_HEALTH_DRAIN),
        "health_drain_dehydrated": max(metabolic_rate * HEALTH_DRAIN_DEHYDRATED_FRACTION,
                                       FLOOR_HEALTH_DRAIN),
        "health_drain_nutrient": max(metabolic_rate * HEALTH_DRAIN_NUTRIENT_FRACTION,
                                     FLOOR_HEALTH_DRAIN * 0.5),
    }


def derive_guard_thresholds(metabolic_rate: float, traits: TraitVector) -> dict[str, float]:
    if metabolic_rate > 0:
        m_norm = metabolic_rate / REFERENCE_METABOLIC_RATE
        adjustment = GUARD_ADJUSTMENT_SCALE * math.log(max(m_norm, 1e-10))
        adjustment = max(GUARD_ADJUSTMENT_MIN, min(GUARD_ADJUSTMENT_MAX, adjustment))
    else:
        adjustment = 0.0

    repro_threshold = 0.7 if traits.reproductive_strategy == "r_selected" else 0.8

    wilting_hydration = 0.3 * (1.0 - traits.drought_tolerance * 0.5)
    wilting_nutrients = 0.2 * (1.0 - traits.drought_tolerance * 0.3)
    dormancy_moisture = 0.25 * (1.0 - traits.drought_tolerance * 0.4)
    dormancy_nutrients = 0.15 * (1.0 - traits.drought_tolerance * 0.3)

    return {
        "hunger_enter": 0.3 * (1.0 + adjustment),
        "hunger_exit": 0.15 * (1.0 - adjustment),
        "hydration_enter": 0.2,
        "hydration_exit": 0.6,
        "energy_enter": 0.2 * (1.0 + adjustment),
        "energy_exit": 0.5 * (1.0 - adjustment),
        "repro_drive_threshold": repro_threshold,
        "wilting_hydration": wilting_hydration,
        "wilting_nutrients": wilting_nutrients,
        "fruiting_growth": 0.5,
        "fruiting_health": 0.4,
        "dormancy_recovery_moisture": dormancy_moisture,
        "dormancy_recovery_nutrients": dormancy_nutrients,
        "dormancy_timeout": 2000,
    }


def derive_interaction_relief(metabolic_rate: float) -> dict[str, float]:
    return {
        "herbivory_relief": max(metabolic_rate * HERBIVORY_RELIEF_FRACTION,
                                FLOOR_HERBIVORY_RELIEF),
        "predation_relief": max(metabolic_rate * PREDATION_RELIEF_FRACTION,
                                FLOOR_PREDATION_RELIEF),
        "predation_energy_gain": max(metabolic_rate * PREDATION_ENERGY_FRACTION,
                                     FLOOR_PREDATION_RELIEF * 0.5),
        "pollination_relief": max(metabolic_rate * POLLINATION_RELIEF_FRACTION,
                                  FLOOR_POLLINATION_RELIEF),
    }


def derive_consumption_damage(metabolic_rate: float) -> dict[str, float]:
    return {
        "consumption_damage_growth": max(metabolic_rate * CONSUMPTION_GROWTH_DAMAGE_FRACTION,
                                         0.02),
        "consumption_damage_health": max(metabolic_rate * CONSUMPTION_HEALTH_DAMAGE_FRACTION,
                                         0.01),
    }


def derive_reproduction_cost(metabolic_rate: float) -> float:
    return max(metabolic_rate * 0.3, 0.1)


def derive_spread_params(traits: TraitVector) -> dict[str, float]:
    if traits.spread_mode is None:
        return {"spread_chance": 0.0, "spread_cooldown": 0}
    if traits.spread_chance is not None and traits.spread_cooldown is not None:
        return {"spread_chance": traits.spread_chance, "spread_cooldown": traits.spread_cooldown}
    if traits.reproductive_strategy == "r_selected":
        base_chance, base_cooldown = 0.008, 80
    else:
        base_chance, base_cooldown = 0.003, 200
    mass_factor = max(0.3, min(2.0, 0.05 / max(traits.body_mass_kg, 0.001)))
    return {
        "spread_chance": base_chance * mass_factor,
        "spread_cooldown": int(base_cooldown / mass_factor),
    }


def derive_all(traits: TraitVector) -> DerivedParams:
    metabolic = derive_metabolic_rate(traits)
    flow = derive_flow_rates(metabolic, traits)
    guards = derive_guard_thresholds(metabolic, traits)
    health = derive_health_drain_rates(metabolic)
    relief = derive_interaction_relief(metabolic)
    damage = derive_consumption_damage(metabolic)
    spread = derive_spread_params(traits)

    return DerivedParams(
        species_id=traits.species_id, entity_class=traits.entity_class,
        metabolic_rate=metabolic,
        speed=derive_speed(traits), sensory_range=derive_sensory_range(traits),
        hunger_rate=flow["hunger_rate"], thirst_rate=flow["thirst_rate"],
        energy_drain=flow["energy_drain"], energy_recovery=flow["energy_recovery"],
        repro_drive_build=flow["repro_drive_build"], repro_drive_decay=flow["repro_drive_decay"],
        health_drain_starving=health["health_drain_starving"],
        health_drain_dehydrated=health["health_drain_dehydrated"],
        health_drain_nutrient=health["health_drain_nutrient"],
        hunger_enter=guards["hunger_enter"], hunger_exit=guards["hunger_exit"],
        hydration_enter=guards["hydration_enter"], hydration_exit=guards["hydration_exit"],
        energy_enter=guards["energy_enter"], energy_exit=guards["energy_exit"],
        repro_drive_threshold=guards["repro_drive_threshold"],
        wilting_hydration=guards["wilting_hydration"], wilting_nutrients=guards["wilting_nutrients"],
        fruiting_growth=guards["fruiting_growth"], fruiting_health=guards["fruiting_health"],
        dormancy_recovery_moisture=guards["dormancy_recovery_moisture"],
        dormancy_recovery_nutrients=guards["dormancy_recovery_nutrients"],
        dormancy_timeout=guards["dormancy_timeout"],
        herbivory_relief=relief["herbivory_relief"], predation_relief=relief["predation_relief"],
        predation_energy_gain=relief["predation_energy_gain"],
        pollination_relief=relief["pollination_relief"],
        consumption_damage_growth=damage["consumption_damage_growth"],
        consumption_damage_health=damage["consumption_damage_health"],
        clutch_size=traits.clutch_size, generation_time_ticks=traits.generation_time_ticks,
        parent_energy_cost=derive_reproduction_cost(metabolic),
        spread_mode=traits.spread_mode, spread_range=traits.spread_range,
        spread_chance=spread["spread_chance"], spread_cooldown=int(spread["spread_cooldown"]),
        root_persistence=traits.root_persistence, canopy_radius=traits.canopy_radius,
        thermal_range=traits.thermal_range, drought_tolerance=traits.drought_tolerance,
        functional_group=traits.functional_group, diet_type=traits.diet_type,
        diet_breadth=list(traits.diet_breadth), resource_tags=list(traits.resource_tags),
        locomotion=traits.locomotion, skeleton_id=traits.skeleton_id,
        trophic_level=traits.trophic_level, pollination_syndrome=traits.pollination_syndrome,
        floral_affinity=list(traits.floral_affinity),
    )


def trait_vector_from_dict(d: dict) -> TraitVector:
    thermal = d.get("thermal_range", [0.0, 40.0])
    if isinstance(thermal, list):
        thermal = tuple(thermal)
    return TraitVector(
        species_id=d["species_id"], functional_group=d["functional_group"],
        entity_class=d["entity_class"], body_mass_kg=d["body_mass_kg"],
        locomotion=d["locomotion"], skeleton_id=d.get("skeleton_id"),
        thermoregulation=d.get("thermoregulation", "endotherm"),
        mass_specific_bmr=d.get("mass_specific_bmr"),
        diet_type=d.get("diet_type", "herbivore"),
        diet_breadth=d.get("diet_breadth", []),
        trophic_level=d.get("trophic_level", 2.0),
        reproductive_strategy=d.get("reproductive_strategy", "K_selected"),
        clutch_size=d.get("clutch_size", 1),
        generation_time_ticks=d.get("generation_time_ticks", 5000),
        thermal_range=thermal,
        drought_tolerance=d.get("drought_tolerance", 0.3),
        shade_tolerance=d.get("shade_tolerance", 0.3),
        sensory_range_multiplier=d.get("sensory_range_multiplier", 1.0),
        movement_budget=d.get("movement_budget", 0.4),
        spread_mode=d.get("spread_mode"), spread_range=d.get("spread_range"),
        spread_chance=d.get("spread_chance"), spread_cooldown=d.get("spread_cooldown"),
        root_persistence=d.get("root_persistence", False),
        canopy_radius=d.get("canopy_radius"),
        resource_tags=d.get("resource_tags", []),
        pollination_syndrome=d.get("pollination_syndrome"),
        floral_affinity=d.get("floral_affinity", []),
    )


def parse_species_definitions(world_config: dict) -> list[TraitVector]:
    defs = world_config.get("species_definitions", [])
    return [trait_vector_from_dict(d) for d in defs]
