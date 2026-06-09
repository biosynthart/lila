# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā — Universal Simulation Constants

All numeric constants used by the engine and actors live here. This is the
single source of truth — no module should define its own copies.

These are world-level physics constants, not species-specific values.
Species-specific parameters come from DerivedParams via the trait compiler.
"""

from __future__ import annotations

# ── Drinking & hydration ──────────────────────────────────────────────────────
DRINK_RECOVERY_RATE = 0.15      # hydration gained per tick × local soil moisture
DRINK_SOIL_DRAIN = 0.01         # soil moisture removed per drink tick
DRINK_WATER_DRAIN = 0.003       # water source level removed per drink tick

# ── Near-water survival bonus ─────────────────────────────────────────────────
WATER_PROXIMITY_HUNGER_FACTOR = 0.5   # hunger relief = hunger_rate × this
WATER_PROXIMITY_COLONY_FACTOR = 0.2   # colony_health recovery = energy_recovery × this

# ── Reproductive drive conditions ─────────────────────────────────────────────
REPRO_BUILD_MIN_ENERGY = 0.5    # energy must exceed this to build drive
REPRO_BUILD_MAX_HUNGER = 0.5    # hunger must be below this to build drive
REPRO_BUILD_MIN_HEALTH = 0.5    # health must exceed this to build drive
REPRO_DECAY_HUNGER = 0.7        # drive decays when hunger exceeds this
REPRO_DECAY_ENERGY = 0.2        # drive decays when energy falls below this
REPRO_MATE_SEEK_DRIVE = 0.5     # drive above this triggers mate-seeking movement

# ── Critical stress thresholds ────────────────────────────────────────────────
STARVATION_HUNGER = 0.8         # hunger above this → health drain
DEHYDRATION_HYDRATION = 0.15    # hydration below this → health drain
COLONY_STRESS_HUNGER = 0.7      # colony_health starts draining
COLONY_STRESS_ENERGY = 0.2      # colony_health starts draining

# ── Plant physiology ──────────────────────────────────────────────────────────
PLANT_BASE_WATER_DEMAND = 0.03  # base water uptake rate from soil
PLANT_SOIL_UPTAKE_RATE = 0.1    # fraction of soil moisture available per tick
PLANT_BASE_GROWTH_RATE = 0.05   # base growth rate (× resource availability)
PLANT_DEFAULT_NUTRIENT_DEMAND = 0.01  # fallback if metadata lacks nutrient_demand
PLANT_HEALTH_CRITICAL_HYDRATION = 0.15  # below this, plant health degrades
PLANT_HEALTH_CRITICAL_NUTRIENTS = 0.1   # below this, plant health degrades

# ── Plant spreading requirements ──────────────────────────────────────────────
SPREAD_MIN_HEALTH = 0.6         # parent must be this healthy to spread
SPREAD_MIN_HYDRATION = 0.3      # parent must be this hydrated
SPREAD_MIN_GROWTH = 0.5         # parent must have this much growth
SPREAD_SOIL_MIN_MOISTURE = 0.15 # target cell needs this much soil moisture
SPREAD_SOIL_MIN_NUTRIENTS = 0.1 # target cell needs this much nutrients
SPREAD_DENSITY_RADIUS = 1.5     # no other autotroph within this radius
SPREAD_PARENT_GROWTH_COST = 0.1 # growth deducted from parent
SPREAD_PARENT_NUTRIENT_COST = 0.05  # nutrients deducted from parent

# ── Dormancy recovery ─────────────────────────────────────────────────────────
DORMANCY_RECOVERY_EXIT_HEALTH = 0.2  # health above this exits dormancy

# ── Ecosystem collapse ────────────────────────────────────────────────────────
COLLAPSE_SUPPORT_THRESHOLD = 2
COLLAPSE_HEALTH_MULTIPLIER = 3.0    # health drain = base_drain × this
COLLAPSE_HYDRATION_MULTIPLIER = 0.7 # hydration drain = base_drain × this

# ── Pollination ───────────────────────────────────────────────────────────────
POLLINATION_HEALTH_BOOST = 0.02  # health boost to pollinated plant
POLLINATION_MAX_LINGER = 10      # hard cap on linger ticks per visit

# ── Predation & herbivory distances ───────────────────────────────────────────
PREDATION_CATCH_DISTANCE = 1.5  # predator must be this close to catch
HERBIVORY_CONSUME_DISTANCE = 2.0  # herbivore must be this close to eat
POLLINATION_VISIT_DISTANCE = 2.0  # pollinator must be this close to visit a flower
HERBIVORY_MIN_HUNGER = 0.2      # minimum hunger to trigger consumption
FLEE_ESCAPE_DISTANCE = 8.0      # how far prey runs from predator
CARNIVORE_HUNT_HUNGER = 0.5     # hunger above this → HUNTING instead of FORAGING

# ── Movement ──────────────────────────────────────────────────────────────────
ARRIVAL_THRESHOLD = 0.3         # close enough to target to stop
WANDER_RANGE = 8.0              # random wander distance when no target
POLLINATOR_CRITICAL_HUNGER = 0.7  # pollinators seek water only above this

# ── Pollinator dispersal ─────────────────────────────────────────────────────
POLLINATOR_MAX_PER_FLOWER = 5     # max pollinators lingering at one flower
POLLINATOR_VISIT_LIMIT = 4        # visits before forced WANDERING exploration
POLLINATOR_WANDER_COOLDOWN = 30   # ticks to wander before re-entering FORAGING
POLLINATOR_CROWD_RADIUS = 2.5     # radius to count "at flower" pollinators
POLLINATOR_POST_VISIT_COOLDOWN = 15  # ticks after linger ends before re-pollination

# ── Child entity inheritance ──────────────────────────────────────────────────
CHILD_HUNGER_INHERIT = 0.3      # child hunger = parent × this
CHILD_ENERGY_FLOOR = 0.4        # child energy ≥ this
CHILD_ENERGY_INHERIT = 0.9      # child energy = max(floor, parent × this)
CHILD_COLONY_FLOOR = 0.4        # colony_health floor
CHILD_COLONY_INHERIT = 0.9      # colony_health = max(floor, parent × this)
CHILD_HEALTH_FLOOR = 0.5        # health floor
CHILD_HEALTH_INHERIT = 0.95     # health = max(floor, parent × this)
SPAWN_OFFSET = 1.0              # ±offset from parent position

# ── Water source physics ──────────────────────────────────────────────────────
WATER_EVAPORATION_RATE = 0.002  # per tick water level loss
WATER_REPLENISH_RATE = 0.003    # per tick water level gain (groundwater)
WATER_SOURCE_MOISTURE_TARGET = 0.9  # soil moisture level in water cells
WATER_REFILL_RATE = 0.05        # soil moisture refill rate in water cells
WATER_DRY_THRESHOLD = 0.05      # sources below this are considered dry

# ── Rain ──────────────────────────────────────────────────────────────────────
RAIN_MOISTURE_BOOST = 0.3       # soil moisture increase × intensity
# Nutrient rain is split between fast (immediately available) and slow pools.
# Total preserved: RAIN_NUTRIENT_FAST_BOOST + RAIN_NUTRIENT_SLOW_BOOST ≈ old RAIN_NUTRIENT_BOOST
RAIN_NUTRIENT_FAST_BOOST = 0.025   # ~83% — dissolved mineral input to fast pool
RAIN_NUTRIENT_SLOW_BOOST = 0.005   # ~17% — particulate deposition to slow pool
# Kept for backward-compat calculation (sum of the two above)
RAIN_NUTRIENT_BOOST = 0.03      # soil nutrient increase × intensity (legacy total)
RAIN_WATER_SOURCE_BOOST = 0.4   # water source level increase × intensity
RAIN_SUPPRESSION_TICKS = 80     # ticks of suppressed evaporation after rain
RAIN_PLANT_HYDRATION = 0.2      # direct plant hydration boost × intensity
RAIN_PLANT_HEALTH = 0.1         # direct plant health boost × intensity
# Animal rain hydration: base boost scaled inversely by current hydration.
# Critically dehydrated animals (hydration ≈ 0) get up to 2× the base,
# well-hydrated animals (hydration > 0.7) get only half. This prevents
# post-collapse death spirals where a single rain event can't save them.
# Animal rain hydration: base boost scaled inversely by current hydration.
# Critically dehydrated animals (hydration ≈ 0) get up to 2× the base,
# well-hydrated animals (hydration > 0.7) get only half. This prevents
# post-collapse death spirals where a single rain event can't save them.
RAIN_ANIMAL_HYDRATION = 0.15     # direct animal hydration boost × intensity

# Post-rain reproduction rebound: after environmental recovery (rain),
# surviving animals get a temporary boost to reproductive drive build rate
# so populations can recover before individuals die of old age/starvation.
RAIN_REPRO_RECOVERY_TICKS = 800   # ticks the boost remains active (~80s at 10Hz)
RAIN_REPRO_BOOST_MULTIPLIER = 3.0  # multiplier on repro_drive_build during recovery

# ── Soil evaporation ──────────────────────────────────────────────────────────
SOIL_EVAP_BASE_RATE = 0.001     # base soil moisture loss per tick
SOIL_EVAP_TEMP_SCALE = 25.0     # temperature divisor for evaporation rate
SOIL_EVAP_HUMIDITY_FACTOR = 0.6 # humidity dampening factor
SOIL_MOISTURE_FLOOR = 0.05      # soil moisture never drops below this

# ── Organic matter deposit ────────────────────────────────────────────────────
OM_DEPOSIT_SCALE = 0.15         # body mass → organic matter conversion
OM_DEPOSIT_MIN = 0.002          # minimum deposit for any entity
OM_DEPOSIT_MAX = 0.5            # maximum deposit per cell

# ── Two-pool nutrient dynamics ────────────────────────────────────────────────
# Mineralization: organic_matter → nutrients_slow (background microbial)
MINERALIZATION_RATE = 0.002   # per tick (half-life ~350 ticks without decomposers)
# Dissolution: nutrients_slow → nutrients_fast (background fertility release)
DISSOLUTION_RATE = 0.005      # per tick (slow pool half-life ~140 ticks)
# Leaching: nutrients_fast drains slowly (nutrients wash deeper / are lost)
NUTRIENT_LEACH_RATE = 0.001   # per tick
# Decomposition efficiency: fraction of organic matter → slow nutrients
DECOMP_NUTRIENT_EFFICIENCY = 0.8

# ── Active states (entity moves toward targets in these) ──────────────────────
ACTIVE_MOVEMENT_STATES = frozenset({"FORAGING", "HUNTING", "FLEEING", "DRINKING", "SWARMING"})
ACTIVE_ENERGY_DRAIN_STATES = frozenset({"FORAGING", "HUNTING", "FLEEING", "SWARMING"})
ENERGY_RECOVERY_STATES = frozenset({"RESTING", "IDLE"})
