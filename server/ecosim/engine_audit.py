# līlā — Engine Parameter Audit (Step 2.1)
#
# Every species-specific constant extracted from engine.py, organized by
# tick phase. These are the calibration targets for the trait derivation layer.
#
# IMPORTANT: All engine rates are multiplied by dt (default 0.1 at 10 Hz).
# "Per-tick" values below are the actual delta per step() call, i.e. rate × dt.
# Biome modifiers (hunger_rate_modifier, metabolic_scaling, etc.) assumed ≈ 1.0
# for TEMPERATE biome unless noted.

# ═══════════════════════════════════════════════════════════════════════════
# 1. FLOW PHASE — Continuous State Variable Updates
# ═══════════════════════════════════════════════════════════════════════════

FLOW_ANIMAL = {
    # _flow_animal() — lines 225-280
    # Applies to: ANIMAL, BIRD (line 216)

    "hunger_rate": {
        "formula": "0.015 * base_metabolism * biome_mod * rate_hunger * dt",
        "base_metabolism": "meta.get('metabolism_rate', 1.0)",
        "biome_mod": "biome.hunger_rate_modifier * biome.metabolic_scaling",
        "per_tick_deer": 0.0015,  # 0.015 * 1.0 * 1.0 * 1.0 * 0.1
        "line": 236,
    },
    "energy_drain_active": {
        "formula": "0.02 * biome.energy_drain_modifier * dt",
        "states": ["FORAGING", "HUNTING", "FLEEING"],
        "per_tick": 0.002,  # 0.02 * 1.0 * 0.1
        "line": 241,
    },
    "energy_recovery_resting": {
        "formula": "0.03 * dt",
        "states": ["RESTING", "IDLE"],
        "per_tick": 0.003,
        "line": 243,
    },
    "hydration_loss": {
        "formula": "biome.evaporation_rate * (temp / 30.0) * rate_thirst * dt",
        "note": "biome.evaporation_rate is the key biome-specific value",
        "line": 260,
    },
    "hydration_recovery_drinking": {
        "formula": "0.15 * soil_moisture * dt",
        "per_tick_wet_soil": 0.015,  # 0.15 * 1.0 * 0.1
        "line": 253,
    },
    "soil_drain_drinking": {
        "formula": "-0.01 * rate_thirst * dt",
        "per_tick": -0.001,
        "line": 256,
    },
    "water_source_drain_drinking": {
        "formula": "-0.003 * dt",
        "per_tick": -0.0003,
        "line": 258,
    },
    "repro_drive_build": {
        "formula": "0.005 * rate_reproduction * dt",
        "conditions": "energy > 0.5 AND hunger < 0.5 AND health > 0.5",
        "per_tick": 0.0005,
        "line": 267,
    },
    "repro_drive_decay": {
        "formula": "-0.002 * dt",
        "conditions": "hunger > 0.7 OR energy < 0.2",
        "per_tick": -0.0002,
        "line": 270,
    },
    "health_drain_starving": {
        "formula": "-0.01 * dt",
        "condition": "hunger > 0.8",
        "per_tick": -0.001,
        "line": 274,
    },
    "health_drain_dehydrated": {
        "formula": "-0.015 * dt",
        "condition": "hydration < 0.15",
        "per_tick": -0.0015,
        "line": 276,
    },
}

FLOW_INSECT = {
    # _flow_insect() — lines 353-397
    # Applies to: INSECT (line 220)

    "hunger_rate": {
        "formula": "0.01 * base_metabolism * biome_mod * rate_hunger * dt",
        "base_metabolism": "meta.get('metabolism_rate', 0.8)",
        "biome_mod": "biome.metabolic_scaling (NOT hunger_rate_modifier)",
        "per_tick_butterfly": 0.0008,  # 0.01 * 0.8 * 1.0 * 1.0 * 0.1
        "line": 363,
    },
    "water_hunger_relief": {
        "formula": "-0.005 * dt",
        "condition": "near water source",
        "per_tick": -0.0005,
        "line": 367,
    },
    "water_colony_recovery": {
        "formula": "0.002 * dt",
        "condition": "near water source",
        "per_tick": 0.0002,
        "line": 368,
    },
    "energy_recovery_resting": {
        "formula": "0.02 * dt",
        "states": ["RESTING", "lingering (_linger > 0)"],
        "per_tick": 0.002,
        "line": 372,
    },
    "energy_drain_active": {
        "formula": "0.005 * biome_mod * dt",
        "per_tick": 0.0005,  # 0.005 * 1.0 * 0.1
        "line": 374,
    },
    "colony_health_drain": {
        "formula": "(0.008 + hunger * 0.02) * dt",
        "condition": "hunger > 0.7 OR energy < 0.2",
        "per_tick_range": (0.0008, 0.0028),  # hunger 0.0 → 1.0
        "note": "Starvation ACCELERATES — scales with hunger level",
        "line": "378-379",
    },
    "repro_drive_build": {
        "formula": "0.012 * rate_reproduction * dt",
        "conditions": "energy > 0.4 AND hunger < 0.5 AND colony_health > 0.4",
        "per_tick": 0.0012,
        "note": "2.4× faster than animals — insects breed quickly",
        "line": 385,
    },
    "repro_drive_decay": {
        "formula": "-0.003 * dt",
        "conditions": "hunger > 0.7 OR colony_health < 0.2",
        "per_tick": -0.0003,
        "line": 388,
    },
}

FLOW_PLANT = {
    # _flow_plant() — lines 282-351
    # Applies to: PLANT, TREE (line 218)

    "evapotranspiration": {
        "formula": "biome.evaporation_rate * (temp/30) * (1 - humidity*0.5) * rate_thirst * dt",
        "note": "Suppressed during rain (_rain_ticks_remaining > 0)",
        "line": 303,
    },
    "water_uptake": {
        "formula": "min(water_demand * dt, soil_moisture * 0.1 * dt)",
        "water_demand": "meta.get('water_demand', 0.03)",
        "per_tick_default": 0.003,  # min(0.03 * 0.1, soil * 0.01)
        "line": "309-311",
    },
    "growth": {
        "formula": "growth_rate * min(hydration, nutrients, light) * growth_mod * rate_growth * dt",
        "growth_rate": "meta.get('growth_rate', 0.02)",
        "note": "Liebig's law — limited by scarcest resource",
        "per_tick_optimal": 0.002,  # 0.02 * 1.0 * 1.0 * 1.0 * 0.1
        "line": "316-319",
    },
    "nutrient_uptake": {
        "formula": "total_demand * soil_nutrients * dt",
        "total_demand": "sum of meta.nutrient_demand or 0.01",
        "line": "322-324",
    },
    "health_drain_dry": {
        "formula": "-0.008 * dt",
        "condition": "hydration < 0.15",
        "per_tick": -0.0008,
        "line": 328,
    },
    "health_drain_starved": {
        "formula": "-0.005 * dt",
        "condition": "nutrient_store < 0.1",
        "per_tick": -0.0005,
        "line": 330,
    },
    "tree_collapse_health": {
        "formula": "-0.03 * dt",
        "condition": "TREE and support_count <= 2",
        "per_tick": -0.003,
        "line": 344,
    },
    "tree_collapse_hydration": {
        "formula": "-0.01 * dt",
        "condition": "TREE and support_count <= 2",
        "per_tick": -0.001,
        "line": 345,
    },
}

FLOW_MICROORGANISM = {
    # _flow_microorganism() — lines 399-419

    "activity_approach": {
        "formula": "(optimal - activity) * 0.1 * dt",
        "optimal": "min(organic_matter, moisture) * biome.microbial_activity_modifier",
        "note": "Exponential approach to equilibrium",
        "line": "411-413",
    },
    "population_growth": {
        "formula": "0.005 * activity * dt",
        "condition": "activity > 0.3",
        "per_tick_max": 0.0005,
        "line": 417,
    },
    "population_decay": {
        "formula": "-0.003 * dt",
        "condition": "activity <= 0.3",
        "per_tick": -0.0003,
        "line": 419,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 2. GUARD PHASE — State Transition Thresholds
# ═══════════════════════════════════════════════════════════════════════════

GUARDS_ANIMAL = {
    # _guards_animal() — lines 880-950
    # Applies to: ANIMAL, BIRD

    "death_health": {"threshold": 0.0, "line": 891},
    "death_age": {"threshold": "meta.lifespan (default 1000)", "line": 895},
    "repro_drive_enter": {"threshold": 0.8, "extra": "AND mate available", "line": 906},
    "hydration_enter_drinking": {"threshold": 0.2, "op": "<", "line": 916},
    "hydration_exit_drinking": {"threshold": 0.6, "op": ">=", "line": 912},
    "energy_enter_resting": {"threshold": 0.2, "op": "<", "line": 928},
    "energy_exit_resting": {"threshold": 0.5, "op": ">=", "line": 925},
    "hunger_enter_foraging": {"threshold": 0.3, "op": ">=", "line": 938},
    "hunger_exit_foraging": {"threshold": 0.15, "op": "<", "line": 933},
    "carnivore_enter_hunting": {"threshold": 0.5, "op": ">", "note": "hunger", "line": 935},
}

GUARDS_INSECT = {
    # _guards_insect() — lines 1004-1035
    # Applies to: INSECT

    "death_colony_health": {"threshold": 0.0, "line": 1008},
    "swarming_colony_health": {"threshold": 0.3, "op": "<", "line": 1012},
    "energy_enter_resting": {"threshold": 0.15, "op": "<", "line": 1020},
    "energy_exit_resting": {"threshold": 0.4, "op": ">=", "line": 1017},
    "repro_drive_enter": {"threshold": 0.7, "extra": "AND mate available", "line": 1026},
    "default": "FORAGING",
}

GUARDS_PLANT = {
    # _guards_plant() — lines 952-1002
    # Applies to: PLANT, TREE

    "death_tree": {"condition": "health <= 0 AND type == TREE", "line": 957},
    "dormancy_enter": {"condition": "health <= 0 AND type == PLANT", "line": 963},
    "dormancy_recovery_moisture": {"threshold": 0.25, "op": ">", "line": 977},
    "dormancy_recovery_nutrients": {"threshold": 0.15, "op": ">", "line": 977},
    "dormancy_recovery_health_gain": 0.015,  # per tick while recovering (line 979)
    "dormancy_recovery_hydration_gain": 0.02,  # per tick while recovering (line 980)
    "dormancy_recovery_exit": {"condition": "health > 0.2", "line": 981},
    "dormancy_timeout": {"ticks": 2000, "line": 987},
    "wilting_hydration": {"threshold": 0.3, "op": "<=", "line": 994},
    "wilting_nutrients": {"threshold": 0.2, "op": "<=", "line": 994},
    "fruiting_growth": {"threshold": 0.5, "op": ">=", "line": 996},
    "fruiting_health": {"threshold": 0.4, "op": ">", "line": 996},
}

GUARDS_MICROORGANISM = {
    # _guards_microorganism() — lines 1037-1052

    "blooming": {"condition": "organic > 0.8 AND population > 0.7", "line": 1044},
    "dormant": {"condition": "activity < 0.2", "line": 1046},
    "default": "ACTIVE",
}


# ═══════════════════════════════════════════════════════════════════════════
# 3. INTERACTIONS — Species-Specific Event Parameters
# ═══════════════════════════════════════════════════════════════════════════

INTERACTION_PARAMS = {
    # _resolve_interactions() — lines 796-864
    # _predation_event() — lines 1123-1142
    # _consumption_event() — lines 1144-1158
    # _pollination_event() — lines 1160-1181

    "predation": {
        "flee_trigger_dist": 2.0,           # line 813
        "catch_dist": 1.5,                  # line 826
        "predator_hunger_relief": -0.4,     # line 1128
        "predator_energy_gain": 0.3,        # line 1129
        "line": "1123-1142",
    },
    "herbivory": {
        "consumption_dist": 2.0,            # line 837
        "hunger_trigger": 0.2,              # line 831: sv["hunger"] > 0.2
        "herbivore_hunger_relief": -0.15,   # line 1148
        "plant_growth_damage": -0.1,        # * rate_consumption, line 1149
        "plant_health_damage": -0.05,       # * rate_consumption, line 1150
        "note": "Prefers grass over wildflower (line 724-734, 839-843)",
    },
    "pollination": {
        "interaction_range": 3.0,           # meta.get("pollination_range", 3.0), line 854
        "movement_range": 6.0,              # meta.get("pollination_range", 6.0), line 684
        "movement_search_mult": 3.0,        # poll_range * 3 for flower search, line 685
        "plant_health_boost": 0.02,         # line 1165
        "insect_hunger_relief": -0.05,      # line 1166
        "linger_ticks": (15, 30),           # random.randint(15, 30), line 1169
        "cooldown_ticks": 50,               # on the flower, line 1173
        "target_conditions": "PLANT, FRUITING, not grass, no cooldown",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 4. MOVEMENT — Speed, Ranges, Distances
# ═══════════════════════════════════════════════════════════════════════════

MOVEMENT_PARAMS = {
    "movement_speed": {
        "source": "meta.get('movement_speed', 1.0)",
        "unit": "grid-units per second (× dt for per-tick step)",
        "deer_default": 1.0,
        "per_tick_deer": 0.1,  # 1.0 * 0.1
        "line": 619,
    },
    "sensory_range": {
        "source": "meta.get('sensory_range', 8.0)",
        "unit": "grid units (radius)",
        "deer_default": 8.0,
        "used_by": ["food search (676)", "mate search (1522)", "predator detection (803)"],
    },
    "pollination_range": {
        "source": "meta.get('pollination_range', ...)",
        "interaction_default": 3.0,   # line 854
        "movement_default": 6.0,      # line 684
        "search_radius": "poll_range * 3 = 18.0",
    },
    "flee_distance": {
        "value": 8.0,
        "note": "Hard-coded (not from metadata)",
        "line": 1565,
    },
    "arrival_threshold": 0.3,     # line 633
    "wander_range": 3.0,         # line 694-698, random ±3.0
}


# ═══════════════════════════════════════════════════════════════════════════
# 5. REPRODUCTION & SPREADING
# ═══════════════════════════════════════════════════════════════════════════

REPRODUCTION_PARAMS = {
    # _reproduction_event() — lines 1183-1225

    "parent_energy_cost": -0.3,          # line 1186
    "insect_colony_health_cost": -0.08,  # line 1190-1192
    "child_hunger_inherit": 0.3,         # parent * 0.3, line 1210
    "child_energy_inherit": "max(0.4, parent * 0.9)",  # line 1211
    "child_colony_inherit": "max(0.4, parent * 0.9)",  # line 1213
    "child_health_inherit": "max(0.5, parent * 0.95)", # line 1215
    "spawn_offset": 1.0,                # ±1.0 from parent, line 1199
}

PLANT_SPREAD = {
    # _try_plant_spread() — lines 1229-1318

    "requirements": {
        "health": 0.6,      # > 0.6, line 1239
        "hydration": 0.3,   # > 0.3
        "growth": 0.5,      # > 0.5
    },
    "soil_check": {
        "moisture": 0.15,   # > 0.15, line 1283
        "nutrients": 0.1,   # > 0.1
    },
    "density_check_radius": 1.5,  # line 1273
    "parent_cost_growth": -0.1,   # line 1306
    "parent_cost_nutrients": -0.05, # line 1307

    "meadow_grass": {
        "spread_range": 2.0,           # line 1251
        "spread_chance": 0.008,        # * rate_reproduction, line 1252
        "cooldown_ticks": 80,          # line 1253
    },
    "wildflower": {
        "spread_range": 3.5,           # line 1255
        "spread_chance": 0.005,        # * rate_reproduction, line 1256
        "cooldown_ticks": 120,         # line 1257
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 6. RAIN & WATER
# ═══════════════════════════════════════════════════════════════════════════

RAIN_PARAMS = {
    # apply_rain() — lines 1327-1387

    "soil_moisture_boost": "0.3 * intensity",       # line 1335
    "nutrient_boost": "0.03 * intensity",           # line 1347
    "water_source_boost": "0.4 * intensity",        # line 1360
    "evaporation_suppression_ticks": 80,             # line 1365
    "plant_hydration_boost": "0.2 * intensity",     # line 1373
    "plant_health_boost": "0.1 * intensity",        # line 1374
    "animal_hydration_boost": "0.1 * intensity",    # line 1379
}

WATER_SOURCES = {
    # _replenish_water_sources() — lines 1401-1446

    "evaporation_rate": "0.002 * rate_thirst * dt",   # per tick: 0.0002, line 1405
    "replenishment_rate": "0.003 * rate_water_replenish * dt",  # per tick: 0.0003, line 1407
    "net_per_tick": 0.0001,  # net gain at defaults: 0.0003 - 0.0002
}

SOIL_EVAPORATION = {
    # _evaporate_soil() — lines 1501-1518

    "formula": "0.001 * (temp/25) * (1 - humidity*0.6) * rate_thirst * dt",
    "per_tick_temperate": 0.00008,  # 0.001 * (20/25) * (1-0.3) * 1.0 * 0.1
    "suppressed_during": "_rain_ticks_remaining > 0",
    "floor": 0.05,  # moisture never drops below 0.05
}

ORGANIC_MATTER_DEPOSIT = {
    # _deposit_organic_matter() — lines 1320-1325

    "formula": "min(0.3, body_mass / 500.0)",
    "body_mass_source": "meta.get('body_mass', 10.0)",
    "deer_deposit": 0.02,   # 10.0 / 500 (default body_mass!)
    "note": "body_mass metadata default is 10.0, NOT the trait body_mass_kg",
}


# ═══════════════════════════════════════════════════════════════════════════
# 7. VOXEL EFFECTS — Entity Impact on Soil
# ═══════════════════════════════════════════════════════════════════════════

VOXEL_EFFECTS = {
    # _apply_voxel_effects() — lines 1056-1080

    "plant_nutrient_drain": {
        "formula": "-total_demand * dt",
        "total_demand": "sum of meta.nutrient_demand or 0.01",
        "line": 1066,
    },
    "plant_water_drain": {
        "formula": "-water_demand * size_factor * dt",
        "size_factor": "1.0 + canopy * 0.3 + root_depth * 0.2",
        "line": "1069-1073",
    },
    "microorganism_decomposition": {
        "formula": "biome.decomposition_rate * activity * dt",
        "organic_matter": "-rate",
        "nutrients": "+rate * 0.8",
        "note": "80% conversion efficiency",
        "line": "1078-1080",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 8. CALIBRATION COMPARISON — Current Derivation vs Engine Actual
# ═══════════════════════════════════════════════════════════════════════════

CALIBRATION_TARGETS = {
    "deer": {
        "type": "ANIMAL",
        "body_mass_kg": 80.0,
        "thermoregulation": "endotherm",
        "movement_speed": 1.0,     # metadata default, grid-units/sec
        "per_tick_speed": 0.1,     # 1.0 * dt
        "sensory_range": 8.0,
        "hunger_rate_per_tick": 0.0015,
        "energy_drain_per_tick": 0.002,
        "energy_recovery_per_tick": 0.003,
        "repro_build_per_tick": 0.0005,
        "repro_decay_per_tick": 0.0002,
        "guards": {
            "hunger_enter": 0.3,
            "hunger_exit": 0.15,
            "hydration_enter": 0.2,
            "hydration_exit": 0.6,
            "energy_enter": 0.2,
            "energy_exit": 0.5,
            "repro_threshold": 0.8,
        },
        "herbivory_hunger_relief": 0.15,
        "consumption_growth_damage": 0.1,
    },
    "butterfly": {
        "type": "INSECT",
        "body_mass_kg": 0.0005,
        "thermoregulation": "ectotherm",
        "base_metabolism_metadata": 0.8,
        "sensory_range": "N/A — uses pollination_range",
        "pollination_range_interaction": 3.0,
        "pollination_range_movement": 6.0,
        "hunger_rate_per_tick": 0.0008,
        "energy_drain_per_tick": 0.0005,
        "energy_recovery_per_tick": 0.002,
        "repro_build_per_tick": 0.0012,
        "repro_decay_per_tick": 0.0003,
        "guards": {
            "energy_enter": 0.15,
            "energy_exit": 0.4,
            "repro_threshold": 0.7,
            "colony_death": 0.0,
            "swarming": 0.3,
        },
        "pollination_linger": (15, 30),
        "pollination_cooldown": 50,
        "pollination_hunger_relief": 0.05,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 9. REQUIRED CALIBRATION CONSTANT UPDATES
# ═══════════════════════════════════════════════════════════════════════════
#
# The initial traits.py calibration assumed deer hunger_rate = 0.003/tick.
# The actual engine value is 0.0015/tick (half). All flow rate fractions
# need halving:
#
#   HUNGER_FRACTION:     0.003 → 0.0015
#   THIRST_FRACTION:     0.002 → TBD (depends on biome.evaporation_rate)
#   ENERGY_FRACTION:     0.0025 → 0.002 (energy drain when active)
#   REPRO_BUILD_FRACTION: 0.0008 → 0.0005
#
# Speed calibration:
#   movement_speed metadata = 1.0 (grid-units/sec)
#   per_tick = 1.0 * 0.1 = 0.1 grid-units/tick
#   Current derivation gives deer speed = 0.15 — needs to match 0.1
#   SPEED_BASE_TERRESTRIAL: 0.0502 → 0.0335  (0.1 / 80^0.25 = 0.1/2.99)
#
# Insect-specific rates need separate handling because the engine uses
# DIFFERENT base rates for insects vs animals (0.01 vs 0.015 for hunger,
# 0.005 vs 0.02 for energy drain). Pure allometric scaling can't reproduce
# this directly — the engine has two distinct rate "tiers".
#
# OPTIONS for the refactored engine:
#   A) Let allometric scaling + simulation floors handle it (current approach)
#   B) Use entity_class as a rate-tier selector alongside allometry
#   C) Store the raw rate multipliers in DerivedParams and let the engine
#      apply them with the same formulas it uses today
#
# Recommendation: Option C for Phase 1 (preserve exact dynamics), then
# migrate to Option A in Phase 2 once the trait system is validated.
#
# ═══════════════════════════════════════════════════════════════════════════
