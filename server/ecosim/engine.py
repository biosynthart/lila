# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Ecosystem Engine — Hybrid Automaton with Trait-Based Species Architecture.

Architecture
────────────
The engine runs a seven-phase **hybrid automaton** at 10 Hz (configurable).
Each tick advances continuous state variables (flow), evaluates discrete state
transitions (guards), resolves entity interactions, applies voxel-layer soil
effects, manages water sources, runs motor inference (BYOM), and handles
entity spawning/removal.

    ┌──────────────────────────────────────────────────────────┐
    │ step(dt)                                                 │
    │  1. Flow       — continuous variable updates (hunger,    │
    │                   hydration, energy, growth, ...)        │
    │  2. Interactions — entity↔entity events (grazing,        │
    │                   predation, pollination)                │
    │  3. Guards     — discrete state transitions (FORAGING    │
    │                   → RESTING, GROWING → FRUITING, ...)    │
    │  4. Voxel FX   — entity impact on soil grid (nutrient    │
    │                   uptake, moisture drain, decomposition) │
    │  5. Water      — source evaporation & replenishment      │
    │  6. Motor      — BYOM adapter inference (latent vectors) │
    │  7. Spawn/Kill — deferred entity creation and removal    │
    └──────────────────────────────────────────────────────────┘

Trait-Based Dispatch
────────────────────
Species are defined as functional trait vectors in the world JSON. At init,
the TraitCompiler derives all engine parameters (DerivedParams) from body mass
and traits using allometric scaling laws. The engine dispatches on
**functional role** (consumer / producer / decomposer), never on entity class:

    if params.diet_type == "autotroph":     → _flow_producer
    elif params.diet_type == "decomposer":  → _flow_decomposer
    else:                                   → _flow_consumer

Every numeric constant the tick loop uses comes from DerivedParams. No hard-
coded per-species values remain in this file. The remaining named constants
below are universal simulation physics.

Backward Compatibility
──────────────────────
Worlds without a ``species_definitions`` key in the JSON fall back to
LegacyParams, which signals the engine to use its original per-species
code paths (not included in this file).

See Also
────────
- ``traits.py``          — TraitVector, allometric derivation functions
- ``interactions.py``    — Parameterized interaction templates
- ``trait_compiler.py``  — Compiles traits into DerivedParams + interaction matrix
- ``voxel_manager.py``   — Sparse 3D soil grid (nutrients, moisture, temperature, OM)
- ``model_adapter.py``   — BYOM motor adapter protocol
"""

from __future__ import annotations

import math
import random
from typing import Any

from .actors import (
    FlowActor,
    FlowContext,
    GuardActor,
    GuardContext,
    InteractionContext,
    build_flow_registry,
    build_guard_registry,
    build_interaction_registry,
)
from .biome import BiomeConfig, get_biome_config
from .effects import EffectBus
from .entities import init_entity, is_alive, is_mobile
from .model_adapter import MotorAdapter, build_context
from .trait_compiler import LegacyParams, compile_world
from .traits import DerivedParams
from .voxel_manager import VoxelManager

# ═══════════════════════════════════════════════════════════════════════════════
# Universal Simulation Constants
# ═══════════════════════════════════════════════════════════════════════════════
# These are world-level physics constants, not species-specific values.
# Species-specific parameters come from DerivedParams via the trait compiler.

# ── Drinking & hydration ──────────────────────────────────────────────────────
DRINK_RECOVERY_RATE = 0.15      # hydration gained per tick × local soil moisture
DRINK_SOIL_DRAIN = 0.01         # soil moisture removed per drink tick
DRINK_WATER_DRAIN = 0.003       # water source level removed per drink tick

# ── Near-water survival bonus ─────────────────────────────────────────────────
# Entities near water receive reduced hunger (can sip/browse at water's edge).
# This is a biome-level benefit, not a species interaction.
WATER_PROXIMITY_HUNGER_FACTOR = 0.5   # hunger relief = hunger_rate × this
WATER_PROXIMITY_COLONY_FACTOR = 0.2   # colony_health recovery = energy_recovery × this

# ── Reproductive drive conditions ─────────────────────────────────────────────
# Universal thresholds for when reproductive drive builds vs decays.
# The actual drive rate comes from DerivedParams.
REPRO_BUILD_MIN_ENERGY = 0.5    # energy must exceed this to build drive
REPRO_BUILD_MAX_HUNGER = 0.5    # hunger must be below this to build drive
REPRO_BUILD_MIN_HEALTH = 0.5    # health must exceed this to build drive
REPRO_DECAY_HUNGER = 0.7        # drive decays when hunger exceeds this
REPRO_DECAY_ENERGY = 0.2        # drive decays when energy falls below this
REPRO_MATE_SEEK_DRIVE = 0.5     # drive above this triggers mate-seeking movement

# ── Critical stress thresholds ────────────────────────────────────────────────
# When state variables cross these, health begins to drain.
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
SPREAD_DENSITY_RADIUS = 1.5     # no other plant within this radius
SPREAD_PARENT_GROWTH_COST = 0.1 # growth deducted from parent
SPREAD_PARENT_NUTRIENT_COST = 0.05  # nutrients deducted from parent

# ── Dormancy recovery ─────────────────────────────────────────────────────────
DORMANCY_RECOVERY_EXIT_HEALTH = 0.2  # health above this exits dormancy

# ── Ecosystem collapse ────────────────────────────────────────────────────────
# Trees collapse when non-structural species count drops below this.
COLLAPSE_SUPPORT_THRESHOLD = 2
COLLAPSE_HEALTH_MULTIPLIER = 3.0    # health drain = base_drain × this
COLLAPSE_HYDRATION_MULTIPLIER = 0.7 # hydration drain = base_drain × this

# ── Pollination ───────────────────────────────────────────────────────────────
POLLINATION_HEALTH_BOOST = 0.02  # health boost to pollinated plant

# ── Predation & herbivory distances ──────────────────────────────────────────
FLEE_TRIGGER_DISTANCE = 2.0     # predator must be this close to trigger flee
PREDATION_CATCH_DISTANCE = 1.5  # predator must be this close to catch
HERBIVORY_CONSUME_DISTANCE = 2.0  # herbivore must be this close to eat
HERBIVORY_MIN_HUNGER = 0.2      # minimum hunger to trigger consumption
FLEE_ESCAPE_DISTANCE = 8.0      # how far prey runs from predator
CARNIVORE_HUNT_HUNGER = 0.5     # hunger above this → HUNTING instead of FORAGING

# ── Movement ──────────────────────────────────────────────────────────────────
ARRIVAL_THRESHOLD = 0.3         # close enough to target to stop
WANDER_RANGE = 3.0              # random wander distance when no target
POLLINATOR_CRITICAL_HUNGER = 0.7  # pollinators seek water only above this

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
RAIN_NUTRIENT_BOOST = 0.03      # soil nutrient increase × intensity
RAIN_WATER_SOURCE_BOOST = 0.4   # water source level increase × intensity
RAIN_SUPPRESSION_TICKS = 80     # ticks of suppressed evaporation after rain
RAIN_PLANT_HYDRATION = 0.2      # direct plant hydration boost × intensity
RAIN_PLANT_HEALTH = 0.1         # direct plant health boost × intensity
RAIN_ANIMAL_HYDRATION = 0.1     # direct animal hydration boost × intensity

# ── Soil evaporation ──────────────────────────────────────────────────────────
SOIL_EVAP_BASE_RATE = 0.001     # base soil moisture loss per tick
SOIL_EVAP_TEMP_SCALE = 25.0     # temperature divisor for evaporation rate
SOIL_EVAP_HUMIDITY_FACTOR = 0.6 # humidity dampening factor
SOIL_MOISTURE_FLOOR = 0.05      # soil moisture never drops below this

# ── Organic matter deposit ────────────────────────────────────────────────────
OM_DEPOSIT_SCALE = 0.15         # body mass → organic matter conversion
OM_DEPOSIT_MIN = 0.002          # minimum deposit for any entity
OM_DEPOSIT_MAX = 0.5            # maximum deposit per cell

# ── Decomposition ─────────────────────────────────────────────────────────────
DECOMP_NUTRIENT_EFFICIENCY = 0.8  # fraction of organic matter → nutrients

# ── Active states (entity moves toward targets in these) ──────────────────────
ACTIVE_MOVEMENT_STATES = frozenset({"FORAGING", "HUNTING", "FLEEING", "DRINKING"})
ACTIVE_ENERGY_DRAIN_STATES = frozenset({"FORAGING", "HUNTING", "FLEEING"})
ENERGY_RECOVERY_STATES = frozenset({"RESTING", "IDLE"})


# ═══════════════════════════════════════════════════════════════════════════════
# Engine
# ═══════════════════════════════════════════════════════════════════════════════

class EcosystemEngine:
    """Hybrid automaton engine for a single līlā ecosystem session.

    The engine owns the full simulation state: entities, voxel grid, water
    sources, and climate. Each call to ``step(dt)`` advances the simulation
    by one tick and returns a delta-encoded packet for client rendering.

    Species behavior is derived entirely from functional traits compiled at
    init. The engine dispatches on functional role (consumer / producer /
    decomposer) and reads all constants from ``DerivedParams``.

    Args:
        world_config: World definition dict (from JSON). Must contain
            ``environment``, ``entities``, and optionally
            ``species_definitions`` and ``rates``.
        adapters: Optional dict of BYOM adapters. Key ``"motor"`` maps to
            a ``MotorAdapter`` instance for skeletal animation inference.
    """

    def __init__(
        self,
        world_config: dict[str, Any],
        adapters: dict[str, Any] | None = None,
    ):
        # ── Environment setup ──
        env = world_config["environment"]
        self.biome_name: str = env.get("biome", "TEMPERATE")
        self.biome: BiomeConfig = get_biome_config(self.biome_name)
        self.climate: dict[str, float] = dict(env.get("climate", {}))

        # ── Voxel grid (soil layers: nutrients, moisture, temperature, OM) ──
        grid_cfg = env.get("voxel_grid", {})
        dims = tuple(grid_cfg.get("dimensions", [32, 32, 32]))
        cell = grid_cfg.get("cell_size", 1.0)
        self.voxels = VoxelManager(dimensions=dims, cell_size=cell)

        soil = env.get("soil", {})
        if soil:
            self.voxels.initialize_from_soil(soil)

        # ── Entities ──
        raw_entities = world_config.get("entities", [])
        self.entities: dict[str, dict[str, Any]] = {}
        for raw in raw_entities:
            e = init_entity(raw)
            self.entities[e["id"]] = e

        self.tick: int = 0

        # ── Trait compilation ──
        # Converts species_definitions into DerivedParams + interaction matrix.
        # If no species_definitions key, returns LegacyParams for backward compat.
        biome_dict = {"name": self.biome_name}
        self.compiled = compile_world(world_config, biome_dict)


        # ── BYOM motor adapter ──
        adapters = adapters or {}
        motor = adapters.get("motor")
        if motor is not None:
            self._motor_adapter: MotorAdapter = motor
        else:
            from .adapters.static import StaticMotorAdapter
            self._motor_adapter = StaticMotorAdapter()

        # ── Grid bounds ──
        self._grid_max: float = (
            (self.voxels.dimensions[0] - 1) * self.voxels.cell_size
        )

        # ── Water sources ──
        self.water_sources: list[dict[str, Any]] = []
        for ws in env.get("water_sources", []):
            source = {
                "position": list(ws["position"]),
                "max_radius": ws.get("radius", 2.0),
                "radius": ws.get("radius", 2.0),
                "water_level": 1.0,
            }
            self.water_sources.append(source)

        # ── Rate multipliers (from world JSON, all default 1.0) ──
        rates = world_config.get("rates", {})
        self.rate_consumption: float = rates.get("consumption", 1.0)
        self.rate_hunger: float = rates.get("hunger", 1.0)
        self.rate_thirst: float = rates.get("thirst", 1.0)
        self.rate_growth: float = rates.get("growth", 1.0)
        self.rate_reproduction: float = rates.get("reproduction", 1.0)
        self.rate_water_replenish: float = rates.get("water_replenishment", 1.0)

        # ── Internal bookkeeping ──
        self._events: list[dict[str, Any]] = []
        self._rain_ticks_remaining: int = 0
        self._spawns: list[dict[str, Any]] = []
        self._removals: list[str] = []
        self._positions: dict[str, list[float]] = {}

        # ── Effect bus (Phase 1 refactoring) ──
        self.effect_bus = EffectBus()

        # ── Actor registries (Phase 1 + Phase 2 refactoring) ──
        self.actor_registry = build_interaction_registry(self.compiled)
        self.flow_actor_registry = build_flow_registry(self.compiled)
        self.guard_actor_registry = build_guard_registry(self.compiled)
        self._is_legacy = isinstance(self.compiled, LegacyParams)

        # ── Randomization (opt-in via JSON) ──
        rand_cfg = world_config.get("randomize")
        if rand_cfg is True:
            self._randomize_config: dict[str, Any] | None = {}
        elif isinstance(rand_cfg, dict):
            self._randomize_config = rand_cfg
        else:
            self._randomize_config = None
        self._randomize_world()

        # Initialize water source moisture footprints
        for source in self.water_sources:
            self._init_water_source(source)

    # ───────────────────────────────────────────────────────────────────────
    # Param Lookup
    # ───────────────────────────────────────────────────────────────────────

    def _get_params(self, entity: dict[str, Any]) -> DerivedParams | None:
        """Look up DerivedParams for an entity by its species_id.

        Returns None for entities without a species field or when running
        in legacy mode (no species_definitions in world config).
        """
        species = entity.get("species")
        if species and not self._is_legacy:
            return self.compiled.get_params(species)
        return None

    @staticmethod
    def _ensure_consumer_vars(sv: dict[str, Any]) -> None:
        """Ensure all consumer state_vars keys exist.

        init_entity creates different keys per entity type (insects lack
        ``hydration``, animals lack ``colony_health``). The unified consumer
        flow needs all keys present. Uses setdefault so existing values
        from init_entity are preserved.
        """
        sv.setdefault("hunger", 0.0)
        sv.setdefault("energy", 1.0)
        sv.setdefault("hydration", 1.0)
        sv.setdefault("health", 1.0)
        sv.setdefault("reproductive_drive", 0.0)
        sv.setdefault("age", 0.0)

    @staticmethod
    def _ensure_producer_vars(sv: dict[str, Any]) -> None:
        """Ensure all producer state_vars keys exist."""
        sv.setdefault("growth", 0.1)
        sv.setdefault("hydration", 0.8)
        sv.setdefault("nutrient_store", 0.5)
        sv.setdefault("health", 1.0)
        sv.setdefault("age", 0.0)

    @staticmethod
    def _ensure_decomposer_vars(sv: dict[str, Any]) -> None:
        """Ensure all decomposer state_vars keys exist."""
        sv.setdefault("activity", 0.5)
        sv.setdefault("population", 0.5)
        sv.setdefault("age", 0.0)

    # ═══════════════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════════════

    def step(self, dt: float = 0.1) -> dict[str, Any]:
        """Advance the simulation by one tick.

        Executes the seven-phase hybrid automaton and returns a delta-encoded
        tick packet for client rendering via WebSocket.

        Args:
            dt: Time step in seconds. Default 0.1 (10 Hz).

        Returns:
            Tick packet dict containing entity updates, spawns, removals,
            events, voxel deltas, and water source states.
        """
        self.tick += 1
        self._events.clear()
        self._spawns.clear()
        self._removals.clear()
        self._rebuild_spatial_index()

        # Phase 1: Flow — continuous state variable updates
        if self._is_legacy:
            for entity in list(self.entities.values()):
                if is_alive(entity):
                    self._apply_flow(entity, dt)
        else:
            flow_effects = []
            for entity in list(self.entities.values()):
                if not is_alive(entity):
                    continue
                actor = self.flow_actor_registry.get(entity.get("species"))
                if actor:
                    ctx = self._build_flow_context(entity, dt)
                    effects = actor.resolve(ctx)
                    flow_effects.extend(effects)
            # Apply flow effects atomically (StateVarDelta only — no entity lifecycle)
            self.effect_bus.apply_flow_batch(
                flow_effects,
                self.entities,
                self.voxels,
            )

            # Movement — move consumers toward targets after state vars are updated
            for entity in list(self.entities.values()):
                if not is_alive(entity):
                    continue
                params = self._get_params(entity)
                if params is None or params.diet_type in ("autotroph", "decomposer"):
                    continue
                if params.speed > 0 and entity["state"] in ACTIVE_MOVEMENT_STATES:
                    if entity.get("_linger", 0) <= 0:
                        self._move_toward_target(entity, params, dt)

        # Phase 2: Interactions — entity↔entity events (actor-based)
        if self._is_legacy:
            # Legacy mode: use inline interaction resolution
            for entity in list(self.entities.values()):
                if is_alive(entity):
                    self._resolve_interactions(entity)
        else:
            # Actor-based: collect effects, apply atomically
            interaction_effects = []
            for entity in list(self.entities.values()):
                if not is_alive(entity):
                    continue
                actor = self.actor_registry.get(entity.get("species"))
                if actor:
                    ctx = self._build_interaction_context(entity, dt)
                    effects = actor.resolve(ctx)
                    interaction_effects.extend(effects)
            # Apply interaction effects atomically
            self.effect_bus.apply_batch(
                interaction_effects,
                self.entities,
                self.voxels,
                self._spawns,
                self._removals,
                self._events,
            )

        # Phase 3: Guards — discrete state transitions
        if self._is_legacy:
            for entity in list(self.entities.values()):
                if is_alive(entity):
                    self._evaluate_guards(entity)
        else:
            guard_effects = []
            for entity in list(self.entities.values()):
                if not is_alive(entity):
                    continue
                actor = self.guard_actor_registry.get(entity.get("species"))
                if actor:
                    ctx = self._build_guard_context(entity)
                    effects = actor.resolve(ctx)
                    guard_effects.extend(effects)
            # Apply guard effects atomically (includes lifecycle + OM deposit)
            self.effect_bus.apply_effects_with_om_deposit(
                guard_effects,
                self.entities,
                self.voxels,
                self._spawns,
                self._removals,
                self._events,
                deposit_fn=self._deposit_organic_matter,
            )

        # Phase 4: Voxel effects — entity impact on soil
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._apply_voxel_effects(entity, dt)

        # Phase 5: Water — source evaporation & replenishment
        if self.water_sources:
            self._replenish_water_sources(dt)
        self._evaporate_soil(dt)

        # Phase 6: Motor — BYOM adapter inference
        self._apply_motor_inference()

        # Phase 7: Spawn/Kill — deferred entity creation and removal
        for eid in self._removals:
            self.entities.pop(eid, None)
        for spawn in self._spawns:
            init_entity(spawn)
            self.entities[spawn["id"]] = spawn

        return self._build_tick_packet(dt)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2: Interactions — Entity↔Entity Events (Legacy Path)
    # ═══════════════════════════════════════════════════════════════════════
    # This method is kept for backward compatibility with legacy worlds
    # that don't have species_definitions. New worlds use the actor-based
    # path in step() instead.

    def _resolve_interactions(self, e: dict[str, Any]) -> None:
        """Evaluate and resolve all interactions for one entity (legacy).

        Checks flee triggers (predator proximity), predation (catch prey),
        herbivory (consume plants), and pollination (visit flowers).
        All interaction parameters come from the compiled interaction matrix.
        """
        params = self._get_params(e)
        if params is None:
            return

        pos = e["position"]

        # ── Consumer interactions (flee, hunt, graze) ──
        if params.diet_type not in ("autotroph", "decomposer"):
            self._resolve_flee(e, params, pos)
            self._resolve_predation(e, params, pos)
            self._resolve_herbivory(e, params, pos)

        # ── Pollinator interactions (visit flowers) ──
        if params.floral_affinity:
            self._resolve_pollination(e, params, pos)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 1: Flow — Continuous State Variable Updates
    # ═══════════════════════════════════════════════════════════════════════
    # Each entity's state variables (hunger, energy, hydration, growth, etc.)
    # evolve continuously. The flow dispatcher routes to one of three
    # functions based on the entity's functional role.

    def _apply_flow(self, e: dict[str, Any], dt: float) -> None:
        """Route entity to the appropriate flow function by functional role.

        In legacy mode (no species_definitions), routes by entity type using
        hardcoded metadata-based flow functions. In trait mode, routes by
        diet_type using DerivedParams-based flow functions.
        """
        if self._is_legacy:
            sv = e["state_vars"]
            meta = e.get("metadata", {})
            etype = e.get("type", "")

            if etype in ("ANIMAL", "BIRD"):
                self._flow_animal(e, sv, meta, dt)
            elif etype in ("PLANT", "TREE"):
                self._flow_plant(e, sv, meta, dt)
            elif etype == "INSECT":
                self._flow_insect(e, sv, meta, dt)
            elif etype == "MICROORGANISM":
                self._flow_microorganism(e, sv, meta, dt)
        else:
            params = self._get_params(e)
            if params is None:
                return

            if params.diet_type == "autotroph":
                self._flow_producer(e, params, dt)
            elif params.diet_type == "decomposer":
                self._flow_decomposer(e, params, dt)
            else:
                self._flow_consumer(e, params, dt)

    # ═══════════════════════════════════════════════════════════════════════
    # Legacy Flow Functions — Entity-Type-Based (no traits required)
    # Used when world has no species_definitions.
    # ═══════════════════════════════════════════════════════════════════════

    def _flow_animal(
        self,
        e: dict[str, Any],
        sv: dict[str, float],
        meta: dict[str, Any],
        dt: float,
    ) -> None:
        """Continuous flow for animals (legacy path)."""
        base_metabolism = meta.get("metabolism_rate", 1.0)
        biome_mod = self.biome.hunger_rate_modifier * self.biome.metabolic_scaling

        sv["hunger"] = min(1.0, sv["hunger"] + 0.015 * base_metabolism * biome_mod * self.rate_hunger * dt)

        if e["state"] in ("FORAGING", "HUNTING", "FLEEING"):
            drain = 0.02 * self.biome.energy_drain_modifier * dt
            sv["energy"] = max(0.0, sv["energy"] - drain)
        elif e["state"] in ("RESTING", "IDLE"):
            sv["energy"] = min(1.0, sv["energy"] + 0.03 * dt)

        temp = self.climate.get("temperature", 20.0)
        evap = self.biome.evaporation_rate * (temp / 30.0) * self.rate_thirst

        if e["state"] == "DRINKING":
            gx, gy, gz = self.voxels.world_to_grid(*e["position"])
            soil_moisture = self.voxels.get("moisture", gx, gy, gz)
            recovery = 0.15 * soil_moisture * dt
            sv["hydration"] = min(1.0, sv["hydration"] + recovery)
            self.voxels.add("moisture", gx, gy, gz, -0.01 * self.rate_thirst * dt)
            self._drain_nearest_water(e["position"], 0.003 * dt)
        else:
            sv["hydration"] = max(0.0, sv["hydration"] - evap * dt)

        sv["age"] += dt

        if sv["energy"] > 0.5 and sv["hunger"] < 0.5 and sv.get("health", 1.0) > 0.5:
            sv["reproductive_drive"] = min(1.0, sv["reproductive_drive"] + 0.005 * self.rate_reproduction * dt)
        elif sv["hunger"] > 0.7 or sv["energy"] < 0.2:
            sv["reproductive_drive"] = max(0.0, sv["reproductive_drive"] - 0.002 * dt)

        if sv["hunger"] > 0.8:
            sv["health"] = max(0.0, sv["health"] - 0.01 * dt)
        if sv.get("hydration", 1.0) < 0.15:
            sv["health"] = max(0.0, sv["health"] - 0.015 * dt)

        if is_mobile(e) and e["state"] in ("FORAGING", "HUNTING", "FLEEING", "DRINKING"):
            self._move_toward_target_legacy(e, meta, dt)

    def _flow_plant(
        self,
        e: dict[str, Any],
        sv: dict[str, float],
        meta: dict[str, Any],
        dt: float,
    ) -> None:
        """Continuous flow for plants and trees (legacy path)."""
        if e["state"] == "DORMANT":
            sv["age"] += dt
            return

        if e.get("_pollination_cooldown", 0) > 0:
            e["_pollination_cooldown"] -= 1

        temp = self.climate.get("temperature", 20.0)
        humidity = self.climate.get("humidity", 0.5)

        if self._rain_ticks_remaining <= 0:
            evap = self.biome.evaporation_rate * (temp / 30.0) * (1.0 - humidity * 0.5) * self.rate_thirst
            sv["hydration"] = max(0.0, sv["hydration"] - evap * dt)

        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        soil_moisture = self.voxels.get("moisture", gx, gy, gz)
        water_demand = meta.get("water_demand", 0.03)
        uptake = min(water_demand * dt, soil_moisture * 0.1 * dt)
        sv["hydration"] = min(1.0, sv["hydration"] + uptake)

        light = self.biome.light_availability
        soil_nutrients = self.voxels.get("nutrients", gx, gy, gz)
        growth_potential = min(sv["hydration"], soil_nutrients, light)
        base_growth = meta.get("growth_rate", 0.02)
        growth_inc = base_growth * growth_potential * self.biome.growth_rate_modifier * self.rate_growth * dt
        sv["growth"] = min(1.0, sv["growth"] + growth_inc)

        n_demand = meta.get("nutrient_demand", {})
        total_demand = sum(n_demand.values()) if isinstance(n_demand, dict) else 0.01
        sv["nutrient_store"] = min(1.0, sv["nutrient_store"] + total_demand * soil_nutrients * dt)

        if sv.get("hydration", 1.0) < 0.15:
            sv["health"] = max(0.0, sv["health"] - 0.008 * dt)
        if sv.get("nutrient_store", 0.5) < 0.1:
            sv["health"] = max(0.0, sv["health"] - 0.005 * dt)

        if e["type"] == "TREE":
            support_count = sum(
                1 for ent in self.entities.values()
                if is_alive(ent)
                and ent.get("state") != "DORMANT"
                and ent.get("type") not in ("TREE", "INSECT")
            )
            if support_count <= 2:
                sv["health"] = max(0.0, sv["health"] - 0.03 * dt)
                sv["hydration"] = max(0.0, sv["hydration"] - 0.01 * dt)

        sv["age"] += dt

        if e["type"] == "PLANT":
            self._try_plant_spread_legacy(e, sv, meta, dt)

    def _flow_insect(
        self,
        e: dict[str, Any],
        sv: dict[str, float],
        meta: dict[str, Any],
        dt: float,
    ) -> None:
        """Continuous flow for insects (legacy path)."""
        base_metabolism = meta.get("metabolism_rate", 0.8)
        biome_mod = self.biome.metabolic_scaling

        sv["hunger"] = min(1.0, sv["hunger"] + 0.01 * base_metabolism * biome_mod * self.rate_hunger * dt)

        if self._is_near_water(e["position"]):
            sv["hunger"] = max(0.0, sv["hunger"] - 0.005 * dt)
            sv["colony_health"] = min(1.0, sv["colony_health"] + 0.002 * dt)

        if e["state"] == "RESTING" or e.get("_linger", 0) > 0:
            sv["energy"] = min(1.0, sv["energy"] + 0.02 * dt)
        else:
            sv["energy"] = max(0.0, sv["energy"] - 0.005 * biome_mod * dt)

        if sv.get("hunger", 0) > 0.7 or sv.get("energy", 1.0) < 0.2:
            drain = 0.008 + sv.get("hunger", 0) * 0.02
            sv["colony_health"] = max(0.0, sv["colony_health"] - drain * dt)

        sv["age"] += dt

        if sv.get("energy", 1.0) > 0.4 and sv.get("hunger", 0) < 0.5 and sv.get("colony_health", 1.0) > 0.4:
            sv["reproductive_drive"] = min(1.0, sv["reproductive_drive"] + 0.012 * self.rate_reproduction * dt)
        elif sv.get("hunger", 0) > 0.7 or sv.get("colony_health", 1.0) < 0.2:
            sv["reproductive_drive"] = max(0.0, sv["reproductive_drive"] - 0.003 * dt)

        if is_mobile(e) and e["state"] == "FORAGING":
            linger = e.get("_linger", 0)
            if linger > 0:
                e["_linger"] = linger - 1
                e["velocity"] = [0.0, 0.0, 0.0]
            else:
                self._move_toward_target_legacy(e, meta, dt)

    def _flow_microorganism(
        self,
        e: dict[str, Any],
        sv: dict[str, float],
        meta: dict[str, Any],
        dt: float,
    ) -> None:
        """Continuous flow for microorganisms/decomposers (legacy path)."""
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        organic = self.voxels.get("organic_matter", gx, gy, gz)
        moisture = self.voxels.get("moisture", gx, gy, gz)

        optimal_activity = min(organic, moisture) * self.biome.microbial_activity_modifier
        sv["activity"] += (optimal_activity - sv["activity"]) * 0.1 * dt
        sv["activity"] = max(0.0, min(1.0, sv["activity"]))

        if sv.get("activity", 0) > 0.3:
            sv["population"] = min(1.0, sv["population"] + 0.005 * sv["activity"] * dt)
        else:
            sv["population"] = max(0.0, sv["population"] - 0.003 * dt)

    def _move_toward_target_legacy(self, e: dict[str, Any], meta: dict[str, Any], dt: float) -> None:
        """Legacy movement — uses metadata speed instead of DerivedParams."""
        speed = meta.get("movement_speed", 1.0)
        if speed <= 0 or not e.get("_target"):
            return

        target = e["_target"]
        pos = e["position"]
        dx, dz = target[0] - pos[0], target[2] - pos[2]
        dist = (dx**2 + dz**2) ** 0.5
        if dist < 0.1:
            del e["_target"]
            return

        move_amount = min(speed * dt, dist)
        e["position"][0] += dx / dist * move_amount
        e["position"][2] += dz / dist * move_amount
        e["velocity"] = [dx / dist * speed, 0.0, dz / dist * speed]

    def _try_plant_spread_legacy(self, e: dict, sv: dict, meta: dict, dt: float) -> None:
        """Legacy plant spreading — no traits required."""
        if (sv.get("health", 1.0) < SPREAD_MIN_HEALTH
                or sv.get("hydration", 1.0) < SPREAD_MIN_HYDRATION
                or sv.get("growth", 0.1) < SPREAD_MIN_GROWTH):
            return

        cooldown = e.get("_spread_cooldown", 0)
        if cooldown > 0:
            e["_spread_cooldown"] = cooldown - 1
            return

        if random.random() > 0.3 * self.rate_reproduction:
            return

        spread_range = meta.get("canopy_radius", 2.0) or 2.0
        spread_pos = self._clamp_to_grid([
            e["position"][0] + random.uniform(-spread_range, spread_range), 0.0,
            e["position"][2] + random.uniform(-spread_range, spread_range),
        ])

        for other in self._entities_in_range(spread_pos, SPREAD_DENSITY_RADIUS):
            if (other.get("type") in ("PLANT", "TREE") and other["id"] != e["id"]):
                e["_spread_cooldown"] = 10 // 2
                return

        gx, gy, gz = self.voxels.world_to_grid(spread_pos)
        if (self.voxels.get("moisture", gx, gy, gz) < SPREAD_SOIL_MIN_MOISTURE
                or self.voxels.get("nutrients", gx, gy, gz) < SPREAD_SOIL_MIN_NUTRIENTS):
            return

        child = init_entity({
            "id": f"{e['id']}_s{self.tick}",
            "type": e["type"],
            "species": e.get("species"),
            "position": spread_pos,
            "metadata": dict(e["metadata"]),
        })
        self._spawns.append(child)

    # ═══════════════════════════════════════════════════════════════════════
    # Trait-Based Flow Functions — require DerivedParams
    # ═══════════════════════════════════════════════════════════════════════

    def _flow_consumer(self, e: dict, p: DerivedParams, dt: float) -> None:
        """Continuous flow for all mobile consumers.

        Handles: hunger buildup, energy drain/recovery, hydration loss,
        drinking recovery, near-water bonus, reproductive drive, health
        degradation under starvation/dehydration, colony health, and
        movement toward targets.

        All rate constants come from ``p`` (DerivedParams). Biome modifiers
        and world rate multipliers are applied on top.
        """
        sv = e["state_vars"]
        self._ensure_consumer_vars(sv)
        biome_mod = self.biome.hunger_rate_modifier * self.biome.metabolic_scaling

        # ── Hunger — increases with metabolism (dt-dependent) ──
        sv["hunger"] = min(1.0, sv["hunger"] + p.hunger_rate * biome_mod * self.rate_hunger * dt)

        # ── Energy — drains during activity, recovers at rest (dt-dependent) ──
        if e["state"] in ACTIVE_ENERGY_DRAIN_STATES:
            sv["energy"] = max(0.0, sv["energy"] - p.energy_drain * self.biome.energy_drain_modifier * dt)
        elif e["state"] in ENERGY_RECOVERY_STATES:
            sv["energy"] = min(1.0, sv["energy"] + p.energy_recovery * dt)

        # Lingering at a resource (e.g. pollination visit) also recovers energy
        if e.get("_linger", 0) > 0:
            sv["energy"] = min(1.0, sv["energy"] + p.energy_recovery * dt)
            e["_linger"] -= 1
            e["velocity"] = [0.0, 0.0, 0.0]

        # ── Hydration — temperature-driven loss, soil-based recovery when drinking ──
        temp = self.climate.get("temperature", 20.0)

        if e["state"] == "DRINKING":
            gx, gy, gz = self.voxels.world_to_grid(*e["position"])
            soil_moisture = self.voxels.get("moisture", gx, gy, gz)
            recovery = DRINK_RECOVERY_RATE * soil_moisture * dt
            sv["hydration"] = min(1.0, sv["hydration"] + recovery)
            # Drinking depletes local soil moisture and water source
            self.voxels.add("moisture", gx, gy, gz, -DRINK_SOIL_DRAIN * self.rate_thirst * dt)
            self._drain_nearest_water(e["position"], DRINK_WATER_DRAIN * dt)
        else:
            sv["hydration"] = max(0.0, sv["hydration"] - p.thirst_rate * (temp / 30.0) * dt)

        # ── Near-water bonus — reduced hunger from browse/sip at water's edge (dt-dependent) ──
        if self._is_near_water(e["position"]):
            sv["hunger"] = max(0.0, sv["hunger"] - p.hunger_rate * WATER_PROXIMITY_HUNGER_FACTOR * dt)
            if "colony_health" in sv:
                sv["colony_health"] = min(
                    1.0, sv["colony_health"] + p.energy_recovery * WATER_PROXIMITY_COLONY_FACTOR * dt)

        sv["age"] += dt

        # ── Reproductive drive — builds when healthy, decays under stress (dt-dependent) ──
        if (sv["energy"] > REPRO_BUILD_MIN_ENERGY
                and sv["hunger"] < REPRO_BUILD_MAX_HUNGER
                and sv.get("health", 1.0) > REPRO_BUILD_MIN_HEALTH):
            sv["reproductive_drive"] = min(
                1.0, sv["reproductive_drive"] + p.repro_drive_build * self.rate_reproduction * dt)
        elif sv["hunger"] > REPRO_DECAY_HUNGER or sv["energy"] < REPRO_DECAY_ENERGY:
            sv["reproductive_drive"] = max(
                0.0, sv["reproductive_drive"] - p.repro_drive_decay * dt)

        # ── Health — degrades under critical starvation or dehydration (dt-dependent) ──
        if sv["hunger"] > STARVATION_HUNGER:
            sv["health"] = max(0.0, sv["health"] - p.health_drain_starving * dt)
        if sv["hydration"] < DEHYDRATION_HYDRATION:
            sv["health"] = max(0.0, sv["health"] - p.health_drain_dehydrated * dt)

        # ── Colony health — accelerated drain under stress (hunger-scaled, dt-dependent) ──
        if "colony_health" in sv:
            if sv["hunger"] > COLONY_STRESS_HUNGER or sv["energy"] < COLONY_STRESS_ENERGY:
                drain = p.health_drain_starving * (1.0 + sv["hunger"] * 2.0) * dt
                sv["colony_health"] = max(0.0, sv["colony_health"] - drain)

        # ── Movement — move toward current target if in an active state ──
        if p.speed > 0 and e["state"] in ACTIVE_MOVEMENT_STATES:
            if e.get("_linger", 0) <= 0:
                self._move_toward_target(e, p, dt)

    def _flow_producer(self, e: dict, p: DerivedParams, dt: float) -> None:
        """Continuous flow for autotroph sessile entities (plants, trees).

        Handles: evapotranspiration, water uptake from soil, growth via
        Liebig's law (limited by scarcest resource), nutrient uptake,
        health degradation, tree collapse pressure, and vegetative spreading.
        """
        sv = e["state_vars"]
        self._ensure_producer_vars(sv)

        # Dormant plants have no active metabolism — roots persist
        if e["state"] == "DORMANT":
            sv["age"] += dt
            return

        # Tick down pollination cooldown
        if e.get("_pollination_cooldown", 0) > 0:
            e["_pollination_cooldown"] -= 1

        temp = self.climate.get("temperature", 20.0)
        humidity = self.climate.get("humidity", 0.5)

        # ── Evapotranspiration — hydration loss (suppressed during rain) ──
        if self._rain_ticks_remaining <= 0:
            evap = (self.biome.evaporation_rate * (temp / 30.0)
                    * (1.0 - humidity * 0.5) * self.rate_thirst)
            sv["hydration"] = max(0.0, sv["hydration"] - evap * dt)

        # ── Water uptake from soil ──
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        soil_moisture = self.voxels.get("moisture", gx, gy, gz)
        uptake = min(PLANT_BASE_WATER_DEMAND * dt,
                     soil_moisture * PLANT_SOIL_UPTAKE_RATE * dt)
        sv["hydration"] = min(1.0, sv["hydration"] + uptake)

        # ── Growth — Liebig's law: limited by scarcest resource ──
        light = self.biome.light_availability
        soil_nutrients = self.voxels.get("nutrients", gx, gy, gz)
        growth_potential = min(sv["hydration"], soil_nutrients, light)
        growth_inc = (PLANT_BASE_GROWTH_RATE * growth_potential
                      * self.biome.growth_rate_modifier * self.rate_growth * dt)
        sv["growth"] = min(1.0, sv["growth"] + growth_inc)

        # ── Nutrient uptake from soil ──
        n_demand = e["metadata"].get("nutrient_demand", {})
        total_demand = (sum(n_demand.values())
                        if isinstance(n_demand, dict)
                        else PLANT_DEFAULT_NUTRIENT_DEMAND)
        sv["nutrient_store"] = min(
            1.0, sv["nutrient_store"] + total_demand * soil_nutrients * dt)

        # ── Health degradation under resource stress ──
        if sv["hydration"] < PLANT_HEALTH_CRITICAL_HYDRATION:
            sv["health"] = max(0.0, sv["health"] - p.health_drain_dehydrated)
        if sv["nutrient_store"] < PLANT_HEALTH_CRITICAL_NUTRIENTS:
            sv["health"] = max(0.0, sv["health"] - p.health_drain_nutrient)

        # ── Tree collapse pressure ──
        # Large canopy species collapse when ecosystem support drops too low.
        # "Support" = non-structural, non-decomposer living entities.
        if p.canopy_radius and p.canopy_radius > 0:
            support_count = 0
            for ent in self.entities.values():
                if not is_alive(ent) or ent["state"] == "DORMANT":
                    continue
                ep = self._get_params(ent)
                if ep is None:
                    continue
                if ep.canopy_radius or ep.diet_type == "decomposer":
                    continue
                support_count += 1
            if support_count <= COLLAPSE_SUPPORT_THRESHOLD:
                sv["health"] = max(
                    0.0, sv["health"] - p.health_drain_starving * COLLAPSE_HEALTH_MULTIPLIER)
                sv["hydration"] = max(
                    0.0, sv["hydration"] - p.health_drain_dehydrated * COLLAPSE_HYDRATION_MULTIPLIER)

        sv["age"] += dt

        # ── Vegetative spreading ──
        if p.spread_mode is not None:
            self._try_plant_spread(e, sv, p, dt)

    def _flow_decomposer(self, e: dict, p: DerivedParams, dt: float) -> None:
        """Continuous flow for decomposer entities (fungi, microorganisms).

        Activity approaches an equilibrium set by local organic matter and
        moisture. Population grows when active, decays when dormant.
        """
        sv = e["state_vars"]
        self._ensure_decomposer_vars(sv)
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        organic = self.voxels.get("organic_matter", gx, gy, gz)
        moisture = self.voxels.get("moisture", gx, gy, gz)

        # Activity approaches equilibrium (exponential smoothing)
        optimal_activity = min(organic, moisture) * self.biome.microbial_activity_modifier
        sv["activity"] += (optimal_activity - sv["activity"]) * 0.1 * dt
        sv["activity"] = max(0.0, min(1.0, sv["activity"]))

        # Population dynamics
        if sv["activity"] > 0.3:
            sv["population"] = min(1.0, sv["population"] + 0.005 * sv["activity"] * dt)
        else:
            sv["population"] = max(0.0, sv["population"] - 0.003 * dt)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2: Interactions — Entity↔Entity Events
    # ═══════════════════════════════════════════════════════════════════════
    # Interactions are resolved using the trait compiler's interaction matrix.
    # The matrix maps (actor_species, target_species) → interaction type and
    # parameters. No per-species interaction code exists in this file.

    def _build_interaction_context(self, entity: dict[str, Any], dt: float) -> InteractionContext:
        """Build a read-only interaction context for one entity.

        Populates nearby_entities from the spatial index using the entity's
        sensory range. Returns an InteractionContext suitable for actor.resolve().
        """
        params = self._get_params(entity)
        rate_multipliers = {
            "consumption": self.rate_consumption,
            "hunger": self.rate_hunger,
            "thirst": self.rate_thirst,
            "growth": self.rate_growth,
            "reproduction": self.rate_reproduction,
        }

        if params is None:
            return InteractionContext(
                tick=self.tick,
                entity=entity,
                voxel_grid=self.voxels,
                biome=self.biome,
                compiled=self.compiled,
                params=None,
                nearby_entities=[],
                water_sources=self.water_sources,
                climate=self.climate,
                rate_multipliers=rate_multipliers,
            )

        # Query nearby entities using spatial index
        nearby = self._entities_in_range(
            entity["position"], params.sensory_range, entity["id"]
        )

        return InteractionContext(
            tick=self.tick,
            entity=entity,
            voxel_grid=self.voxels,
            biome=self.biome,
            compiled=self.compiled,
            params=params,
            nearby_entities=nearby,
            water_sources=self.water_sources,
            climate=self.climate,
            rate_multipliers=rate_multipliers,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2 Context Builders — Flow + Guard actors (Phase 2)
    # ═══════════════════════════════════════════════════════════════════════

    def _build_flow_context(self, entity: dict[str, Any], dt: float) -> FlowContext:
        """Build a flow context for one entity (Phase 2).

        Extends InteractionContext with dt and rain_ticks_remaining.
        """
        params = self._get_params(entity)
        rate_multipliers = {
            "consumption": self.rate_consumption,
            "hunger": self.rate_hunger,
            "thirst": self.rate_thirst,
            "growth": self.rate_growth,
            "reproduction": self.rate_reproduction,
        }

        return FlowContext(
            tick=self.tick,
            entity=entity,
            voxel_grid=self.voxels,
            biome=self.biome,
            compiled=self.compiled,
            params=params,
            nearby_entities=[],  # flow actors don't need spatial queries
            water_sources=self.water_sources,
            climate=self.climate,
            rate_multipliers=rate_multipliers,
            dt=dt,
            rain_ticks_remaining=self._rain_ticks_remaining,
            _entities=self.entities,
        )

    def _build_guard_context(self, entity: dict[str, Any]) -> GuardContext:
        """Build a guard context for one entity (Phase 2).

        Extends InteractionContext with entities reference (for mate search,
        support count). No nearby_entities needed — guards use full entity list.
        """
        params = self._get_params(entity)
        rate_multipliers = {
            "consumption": self.rate_consumption,
            "hunger": self.rate_hunger,
            "thirst": self.rate_thirst,
            "growth": self.rate_growth,
            "reproduction": self.rate_reproduction,
        }

        return GuardContext(
            tick=self.tick,
            entity=entity,
            voxel_grid=self.voxels,
            biome=self.biome,
            compiled=self.compiled,
            params=params,
            nearby_entities=[],
            water_sources=self.water_sources,
            climate=self.climate,
            rate_multipliers=rate_multipliers,
            _entities=self.entities,
        )

    def _resolve_flee(self, e: dict, p: DerivedParams, pos: list[float]) -> None:
        """Check for nearby predators and trigger flee response."""
        flee_from = self.compiled.get_flee_targets(p.species_id)
        if not flee_from or p.speed <= 0:
            return
        nearby = self._entities_in_range(pos, p.sensory_range, e["id"])
        for other in nearby:
            if other.get("species", "") in flee_from:
                if self._distance(pos, other["position"]) < FLEE_TRIGGER_DISTANCE:
                    old_state = e["state"]
                    e["state"] = "FLEEING"
                    e["_target"] = self._flee_direction(pos, other["position"])
                    if old_state != "FLEEING":
                        self._emit_state_change(e, old_state, "FLEEING")

    def _resolve_predation(self, e: dict, p: DerivedParams, pos: list[float]) -> None:
        """Carnivore/insectivore attempts to catch nearby prey."""
        if p.diet_type not in ("carnivore", "insectivore", "omnivore"):
            return
        if e["state"] != "HUNTING" or e["state_vars"]["hunger"] <= 0.3:
            return
        prey_species = [
            s for s, _ in self.compiled.get_diet_order(p.species_id)
            if any(ix.interaction_type == "predation"
                   for ix in self.compiled.get_interactions(p.species_id, s))
        ]
        for other in self._entities_in_range(pos, p.sensory_range, e["id"]):
            if other.get("species") in prey_species:
                if self._distance(pos, other["position"]) < PREDATION_CATCH_DISTANCE:
                    self._predation_event(e, other, p)
                    break

    def _resolve_herbivory(self, e: dict, p: DerivedParams, pos: list[float]) -> None:
        """Herbivore/omnivore attempts to consume nearby plants."""
        if e["state"] != "FORAGING" or e["state_vars"]["hunger"] <= HERBIVORY_MIN_HUNGER:
            return
        diet_order = self.compiled.get_diet_order(p.species_id)
        if not diet_order:
            return

        best_target = None
        best_pref = 999
        for other in self._entities_in_range(pos, p.sensory_range, e["id"]):
            if other["state"] in ("DEAD", "DYING", "DORMANT"):
                continue
            if self._distance(pos, other["position"]) >= HERBIVORY_CONSUME_DISTANCE:
                continue
            other_species = other.get("species", "")
            for target_species, pref in diet_order:
                if other_species == target_species:
                    ixns = self.compiled.get_interactions(p.species_id, other_species)
                    for ix in ixns:
                        if (ix.interaction_type == "herbivory"
                                and other.get("state_vars", {}).get("growth", 0) > 0.1
                                and pref < best_pref):
                            best_pref = pref
                            best_target = other
                    break

        if best_target is not None:
            self._consumption_event(e, best_target, p)

    def _resolve_pollination(self, e: dict, p: DerivedParams, pos: list[float]) -> None:
        """Pollinator visits a nearby FRUITING flower."""
        if e.get("_linger", 0) > 0:
            return
        for other in self._entities_in_range(pos, p.sensory_range, e["id"]):
            other_species = other.get("species", "")
            ixns = self.compiled.get_interactions(p.species_id, other_species)
            for ix in ixns:
                if (ix.interaction_type == "pollination"
                        and other["state"] == "FRUITING"
                        and other.get("_pollination_cooldown", 0) <= 0):
                    self._pollination_event(e, other, p, ix)
                    return

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 3: Guards — Discrete State Transitions
    # ═══════════════════════════════════════════════════════════════════════
    # Guards evaluate conditions for state machine transitions. Each entity
    # has a state (IDLE, FORAGING, DRINKING, etc.) that changes when
    # thresholds are crossed. Hysteresis bands prevent oscillation.

    def _evaluate_guards(self, e: dict[str, Any]) -> None:
        """Evaluate discrete state transition guards for an entity.

        In legacy mode (no species_definitions), routes by entity type using
        hardcoded metadata-based guard functions. In trait mode, routes by
        diet_type using DerivedParams-based guard functions.
        """
        if self._is_legacy:
            etype = e.get("type", "")
            if etype in ("ANIMAL", "BIRD"):
                self._guards_animal(e)
            elif etype in ("PLANT", "TREE"):
                self._guards_plant(e)
            elif etype == "INSECT":
                self._guards_insect(e)
            elif etype == "MICROORGANISM":
                self._guards_microorganism(e)
        else:
            params = self._get_params(e)
            if params is None:
                return
            if params.diet_type == "autotroph":
                self._guards_producer(e, params)
            elif params.diet_type == "decomposer":
                self._guards_decomposer(e, params)
            else:
                self._guards_consumer(e, params)

    # ═══════════════════════════════════════════════════════════════════════
    # Legacy Guard Functions — Entity-Type-Based (no traits required)
    # Used when world has no species_definitions.
    # ═══════════════════════════════════════════════════════════════════════

    def _guards_animal(self, e: dict[str, Any]) -> None:
        """Guard evaluation for animals (legacy path)."""
        sv = e["state_vars"]
        meta = e.get("metadata", {})
        old_state = e["state"]

        lifespan = meta.get("lifespan", 1000.0)
        if sv.get("health", 1.0) <= 0.0:
            e["state"] = "DYING"
            self._emit_event("DEATH_STARVE", e)
            self._schedule_removal(e)
        elif sv.get("age", 0) >= lifespan:
            e["state"] = "DYING"
            self._emit_event("DEATH_NATURAL", e)
            self._schedule_removal(e)

        elif e["state"] == "FLEEING":
            if e.get("_target") is None:
                e["state"] = "IDLE"

        elif sv.get("reproductive_drive", 0) > 0.8 and self._find_mate_legacy(e):
            e["state"] = "REPRODUCING"
            self._reproduction_event_legacy(e, meta)

        elif e["state"] == "DRINKING":
            if sv.get("hydration", 1.0) >= 0.6:
                e["state"] = "IDLE"
                e["_target"] = None
        elif sv.get("hydration", 1.0) < 0.2:
            e["state"] = "DRINKING"
            water_pos = self._find_nearest_water(e["position"])
            if water_pos:
                e["_target"] = water_pos

        elif e["state"] == "RESTING":
            if sv.get("energy", 1.0) >= 0.5:
                e["state"] = "IDLE"
        elif sv.get("energy", 1.0) < 0.2:
            e["state"] = "RESTING"

        elif e["state"] in ("FORAGING", "HUNTING"):
            if sv.get("hunger", 0) < 0.15:
                e["state"] = "IDLE"
            elif meta.get("diet") == "carnivore" and sv.get("hunger", 0) > 0.5:
                e["state"] = "HUNTING"
        elif sv.get("hunger", 0) >= 0.3:
            diet = meta.get("diet", "herbivore")
            if diet == "carnivore" and sv.get("hunger", 0) > 0.5:
                e["state"] = "HUNTING"
            else:
                e["state"] = "FORAGING"

        else:
            e["state"] = "IDLE"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    def _guards_plant(self, e: dict[str, Any]) -> None:
        """Guard evaluation for plants and trees (legacy path)."""
        sv = e["state_vars"]
        old_state = e["state"]

        if sv.get("health", 1.0) <= 0.0:
            if e["type"] == "TREE":
                e["state"] = "DEAD"
                self._emit_event("DEATH_NATURAL", e)
                self._schedule_removal(e)
                self._deposit_organic_matter_legacy(e)
            elif e["state"] != "DORMANT":
                e["state"] = "DORMANT"
                sv["growth"] = 0.0
                e["_dormant_ticks"] = 0

        elif e["state"] == "DORMANT":
            gx, gy, gz = self.voxels.world_to_grid(*e["position"])
            soil_moisture = self.voxels.get("moisture", gx, gy, gz)
            soil_nutrients = self.voxels.get("nutrients", gx, gy, gz)

            e["_dormant_ticks"] = e.get("_dormant_ticks", 0) + 1

            if soil_moisture > 0.25 and soil_nutrients > 0.15:
                sv["health"] = min(1.0, sv["health"] + 0.015)
                sv["hydration"] = min(1.0, sv["hydration"] + 0.02)
                if sv["health"] > 0.2:
                    e["state"] = "GROWING"
                    sv["growth"] = 0.05
                    sv["nutrient_store"] = max(sv.get("nutrient_store", 0.5), 0.2)
                    e["_dormant_ticks"] = 0

            elif e.get("_dormant_ticks", 0) > 2000:
                e["state"] = "DEAD"
                self._emit_event("DEATH_NATURAL", e)
                self._schedule_removal(e)
                self._deposit_organic_matter_legacy(e)

        elif sv.get("hydration", 1.0) <= 0.3 or sv.get("nutrient_store", 0.5) <= 0.2:
            e["state"] = "WILTING"
        elif sv.get("growth", 0) >= 0.5 and sv.get("health", 1.0) > 0.4:
            e["state"] = "FRUITING"
        else:
            e["state"] = "GROWING"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    def _guards_insect(self, e: dict[str, Any]) -> None:
        """Guard evaluation for insects (legacy path)."""
        sv = e["state_vars"]
        old_state = e["state"]

        if sv.get("colony_health", 1.0) <= 0.0:
            e["state"] = "DEAD"
            self._emit_event("DEATH_NATURAL", e)
            self._schedule_removal(e)
        elif sv.get("colony_health", 1.0) < 0.3:
            e["state"] = "SWARMING"

        elif e["state"] == "RESTING":
            if sv.get("energy", 1.0) >= 0.4:
                e["state"] = "FORAGING"
        elif sv.get("energy", 1.0) < 0.15:
            e["state"] = "RESTING"
            e["velocity"] = [0.0, 0.0, 0.0]
            e["_target"] = None

        elif sv.get("reproductive_drive", 0) > 0.7 and self._find_mate_legacy(e):
            e["state"] = "REPRODUCING"
            self._reproduction_event_legacy(e, {})

        else:
            e["state"] = "FORAGING"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    def _guards_microorganism(self, e: dict[str, Any]) -> None:
        """Guard evaluation for microorganisms (legacy path)."""
        sv = e["state_vars"]
        old_state = e["state"]

        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        organic = self.voxels.get("organic_matter", gx, gy, gz)

        if organic > 0.8 and sv.get("population", 0) > 0.7:
            e["state"] = "BLOOMING"
        elif sv.get("activity", 0.5) < 0.2:
            e["state"] = "DORMANT"
        else:
            e["state"] = "ACTIVE"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    # ═══════════════════════════════════════════════════════════════════════
    # Trait-Based Guard Functions — require DerivedParams
    # ═══════════════════════════════════════════════════════════════════════

    def _guards_consumer(self, e: dict, p: DerivedParams) -> None:
        """Guard evaluation for consumers.

        State machine priority (highest to lowest):
        1. Death (health ≤ 0 or age ≥ lifespan)
        2. Colony swarming (colony_health < 0.3)
        3. Fleeing (set by interaction resolver, cleared when target reached)
        4. Reproduction (drive > threshold AND mate available)
        5. Drinking (hydration hysteresis: enter < 0.2, exit ≥ 0.6)
        6. Resting (energy hysteresis: enter < 0.2, exit ≥ 0.5)
        7. Foraging/Hunting (hunger hysteresis: enter ≥ 0.3, exit < 0.15)
        8. Idle (default)
        """
        sv = e["state_vars"]
        old_state = e["state"]
        meta = e["metadata"]

        # ── Death ──
        # Use trait-derived generation_time_ticks as lifespan when available.
        # This ensures entities live long enough to reproduce (r-selected
        # species like butterflies need many ticks to build reproductive drive).
        # Fall back to entity metadata for legacy worlds without traits.
        if p is not None and p.generation_time_ticks > 0:
            lifespan = p.generation_time_ticks
        else:
            lifespan = meta.get("lifespan", 1000.0)
        health_key = "colony_health" if "colony_health" in sv else "health"
        if sv.get(health_key, 1.0) <= 0.0:
            e["state"] = "DYING"
            self._emit_event("DEATH_STARVE", e)
            self._schedule_removal(e)
            self._deposit_organic_matter(e, p)
        elif sv["age"] >= lifespan:
            e["state"] = "DYING"
            self._emit_event("DEATH_NATURAL", e)
            self._schedule_removal(e)
            self._deposit_organic_matter(e, p)

        # ── Colony swarming ──
        elif "colony_health" in sv and sv["colony_health"] < 0.3:
            e["state"] = "SWARMING"

        # ── Fleeing (managed by interaction resolver) ──
        elif e["state"] == "FLEEING":
            if e.get("_target") is None:
                e["state"] = "IDLE"

        # ── Drinking (hysteresis) ──
        elif e["state"] == "DRINKING":
            if sv.get("hydration", 1.0) >= p.hydration_exit:
                e["state"] = "IDLE"
                e["_target"] = None
        elif sv.get("hydration", 1.0) < p.hydration_enter:
            e["state"] = "DRINKING"
            water_pos = self._find_nearest_water(e["position"])
            if water_pos:
                e["_target"] = water_pos

        # ── Resting (hysteresis) ──
        elif e["state"] == "RESTING":
            if sv["energy"] >= p.energy_exit:
                e["state"] = "IDLE"
        elif sv["energy"] < p.energy_enter:
            e["state"] = "RESTING"
            e["velocity"] = [0.0, 0.0, 0.0]
            e["_target"] = None

        # ── Foraging / Hunting (hysteresis) ──
        elif e["state"] in ("FORAGING", "HUNTING"):
            if sv["hunger"] < p.hunger_exit:
                e["state"] = "IDLE"
            elif p.diet_type in ("carnivore", "insectivore") and sv["hunger"] > CARNIVORE_HUNT_HUNGER:
                e["state"] = "HUNTING"
        elif sv["hunger"] >= p.hunger_enter:
            if p.diet_type in ("carnivore", "insectivore") and sv["hunger"] > CARNIVORE_HUNT_HUNGER:
                e["state"] = "HUNTING"
            else:
                e["state"] = "FORAGING"

        # ── Default ──
        else:
            if e["state"] not in ("FORAGING", "HUNTING", "FLEEING", "DRINKING",
                                  "RESTING", "REPRODUCING", "SWARMING"):
                e["state"] = "IDLE"

        # ── Reproduction (checked independently, can interrupt any state) ──
        # This check is separate from the hysteresis logic above so that reproduction
        # can happen even if the entity is in FORAGING or other states. The reproduction
        # check uses 'if' not 'elif' so it's not blocked by previous state logic.
        if (sv.get("reproductive_drive", 0) > p.repro_drive_threshold 
                and self._find_mate(e) 
                and e["state"] not in ("DYING", "REPRODUCING", "SWARMING")):
            e["state"] = "REPRODUCING"
            self._reproduction_event(e, p)

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    def _guards_producer(self, e: dict, p: DerivedParams) -> None:
        """Guard evaluation for autotroph sessile entities.

        State machine:
        - health ≤ 0 + root_persistence → DORMANT (roots survive)
        - health ≤ 0 + no persistence → DEAD
        - DORMANT + soil recovery → GROWING (if health rebuilds past threshold)
        - DORMANT + timeout → DEAD (roots die after too long)
        - low hydration or nutrients → WILTING
        - high growth + good health → FRUITING (available for pollination)
        - otherwise → GROWING
        """
        sv = e["state_vars"]
        old_state = e["state"]

        if sv["health"] <= 0.0:
            if p.root_persistence:
                if e["state"] != "DORMANT":
                    e["state"] = "DORMANT"
                    sv["growth"] = 0.0
                    e["_dormant_ticks"] = 0
            else:
                e["state"] = "DEAD"
                self._emit_event("DEATH_NATURAL", e)
                self._schedule_removal(e)
                self._deposit_organic_matter(e, p)

        elif e["state"] == "DORMANT":
            gx, gy, gz = self.voxels.world_to_grid(*e["position"])
            soil_moisture = self.voxels.get("moisture", gx, gy, gz)
            soil_nutrients = self.voxels.get("nutrients", gx, gy, gz)
            e["_dormant_ticks"] = e.get("_dormant_ticks", 0) + 1

            if (soil_moisture > p.dormancy_recovery_moisture
                    and soil_nutrients > p.dormancy_recovery_nutrients):
                recovery_health = max(0.015, p.health_drain_dehydrated * 10.0)
                recovery_hydration = max(0.02, p.health_drain_dehydrated * 13.0)
                sv["health"] = min(1.0, sv["health"] + recovery_health)
                sv["hydration"] = min(1.0, sv["hydration"] + recovery_hydration)
                if sv["health"] > DORMANCY_RECOVERY_EXIT_HEALTH:
                    e["state"] = "GROWING"
                    sv["growth"] = 0.05
                    sv["nutrient_store"] = max(sv["nutrient_store"], 0.2)
                    e["_dormant_ticks"] = 0

            elif e.get("_dormant_ticks", 0) > p.dormancy_timeout:
                e["state"] = "DEAD"
                self._emit_event("DEATH_NATURAL", e)
                self._schedule_removal(e)
                self._deposit_organic_matter(e, p)

        elif sv["hydration"] <= p.wilting_hydration or sv["nutrient_store"] <= p.wilting_nutrients:
            e["state"] = "WILTING"
        elif sv["growth"] >= p.fruiting_growth and sv["health"] > p.fruiting_health:
            e["state"] = "FRUITING"
        else:
            e["state"] = "GROWING"

        if e["state"] != old_state:
            self._emit_state_change(e, old_state, e["state"])

    def _guards_decomposer(self, e: dict, p: DerivedParams) -> None:
        """Guard evaluation for decomposer entities."""
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

    # ═══════════════════════════════════════════════════════════════════════
    # Movement — Trait-Driven Target Selection
    # ═══════════════════════════════════════════════════════════════════════

    def _move_toward_target(self, e: dict, p: DerivedParams, dt: float) -> None:
        """Move entity toward its current target at species-derived speed.

        If no target is set, calls _pick_movement_target to select one.
        Entities stop when they arrive within ARRIVAL_THRESHOLD of target.
        """
        target = e.get("_target")
        pos = e["position"]

        if target is None:
            target = self._pick_movement_target(e, p)
            if target is None:
                e["velocity"] = [0.0, 0.0, 0.0]
                return
            e["_target"] = target

        dx = target[0] - pos[0]
        dz = target[2] - pos[2]
        dist = math.sqrt(dx * dx + dz * dz)

        if dist < ARRIVAL_THRESHOLD:
            e["_target"] = None
            e["velocity"] = [0.0, 0.0, 0.0]
            return

        step = min(p.speed, dist)
        nx, nz = dx / dist, dz / dist
        pos[0] = max(0.0, min(self._grid_max, pos[0] + nx * step))
        pos[2] = max(0.0, min(self._grid_max, pos[2] + nz * step))
        e["velocity"] = [nx * p.speed / dt, 0.0, nz * p.speed / dt]

    def _pick_movement_target(self, e: dict, p: DerivedParams) -> list[float] | None:
        """Select a movement target based on entity state and traits.

        Priority:
        1. DRINKING → no target (guard already set the water position)
        2. High reproductive drive → seek nearest mate
        3. FORAGING herbivore → seek nearest food by diet preference
        4. FORAGING pollinator → seek FRUITING flower → any flower → wander → water
        5. HUNTING → seek nearest prey
        6. Default → wander randomly
        """
        state = e["state"]
        pos = e["position"]

        if state == "DRINKING":
            return None

        # Seek mates when reproductive drive is high
        drive = e["state_vars"].get("reproductive_drive", 0)
        if drive > REPRO_MATE_SEEK_DRIVE:
            mate_pos = self._find_nearest_mate_pos(e)
            if mate_pos:
                return mate_pos

        if state == "FORAGING":
            # Herbivores/omnivores: seek food by diet preference
            diet_order = self.compiled.get_diet_order(p.species_id)
            if diet_order:
                food = self._find_nearest_food_by_preference(pos, p.sensory_range, diet_order)
                if food:
                    return food

            # Pollinators: seek flowers (FRUITING first, then water if hungry, then wait)
            if p.floral_affinity:
                # Priority 1: FRUITING flowers — actual nectar available
                flower = self._find_nearest_flower(pos, self._grid_max, p)
                if flower:
                    return flower

                # No FRUITING flowers anywhere. If critically hungry, head to water:
                # the near-water bonus slows hunger drain, and this matches the
                # documented "stress cascade" behavior — butterflies cluster at
                # ponds when flowers are gone. This MUST come before the dormant-
                # flower fallback, because flowers go DORMANT (not dead) under
                # stress, so _find_nearest_flower_any_state would otherwise always
                # return something and the water branch would be unreachable.
                if e["state_vars"].get("hunger", 0) > POLLINATOR_CRITICAL_HUNGER:
                    water = self._find_nearest_water(pos)
                    if water:
                        return water

                # Priority 3: drift toward any non-dead flower and wait for bloom
                any_flower = self._find_nearest_flower_any_state(pos, self._grid_max, p)
                if any_flower:
                    return any_flower

        if state == "HUNTING":
            prey_species = [s for s, _ in self.compiled.get_diet_order(p.species_id)]
            target = self._find_nearest_prey(pos, p.sensory_range, prey_species)
            if target:
                return target

        # Default: wander randomly
        return self._clamp_to_grid([
            pos[0] + random.uniform(-WANDER_RANGE, WANDER_RANGE), 0.0,
            pos[2] + random.uniform(-WANDER_RANGE, WANDER_RANGE),
        ])

    # ── Target search helpers ──

    def _find_nearest_food_by_preference(
        self, pos: list[float], search_range: float,
        diet_order: list[tuple[str, int]],
    ) -> list[float] | None:
        """Find nearest food source, respecting diet preference ordering.

        Returns the position of the nearest entity whose species matches
        the first (most preferred) diet tag. Skips dead, dying, dormant,
        and low-growth entities.
        """
        nearby = self._entities_in_range(pos, search_range * 2)
        best_by_pref: dict[int, tuple[float, list[float]]] = {}

        for other in nearby:
            if other["state"] in ("DEAD", "DYING", "DORMANT"):
                continue
            if other.get("state_vars", {}).get("growth", 1.0) <= 0.1:
                continue
            d = self._distance(pos, other["position"])
            if d < 1.0:
                continue
            other_species = other.get("species", "")
            for target_species, pref in diet_order:
                if other_species == target_species:
                    if pref not in best_by_pref or d < best_by_pref[pref][0]:
                        best_by_pref[pref] = (d, list(other["position"]))
                    break

        if not best_by_pref:
            return None
        return best_by_pref[min(best_by_pref.keys())][1]

    def _find_nearest_prey(
        self, pos: list[float], search_range: float, prey_species: list[str],
    ) -> list[float] | None:
        """Find nearest living prey entity from the given species list."""
        best_dist, best_pos = float("inf"), None
        for other in self._entities_in_range(pos, search_range):
            if other.get("species") in prey_species and is_alive(other):
                d = self._distance(pos, other["position"])
                if d < best_dist:
                    best_dist, best_pos = d, list(other["position"])
        return best_pos

    def _find_nearest_flower(
        self, pos: list[float], search_range: float, p: DerivedParams,
    ) -> list[float] | None:
        """Find nearest FRUITING flower matching pollinator's floral affinity.

        Only returns flowers that are FRUITING, have a matching pollination
        syndrome (via the compiled interaction matrix), and are not on
        pollination cooldown.
        """
        best_dist, best_pos = float("inf"), None
        for other in self._entities_in_range(pos, search_range):
            if other["state"] != "FRUITING":
                continue
            if other.get("_pollination_cooldown", 0) > 0:
                continue
            ixns = self.compiled.get_interactions(p.species_id, other.get("species", ""))
            if not any(ix.interaction_type == "pollination" for ix in ixns):
                continue
            d = self._distance(pos, other["position"])
            if d < best_dist:
                best_dist, best_pos = d, list(other["position"])
        return best_pos

    def _find_nearest_flower_any_state(
        self, pos: list[float], search_range: float, p: DerivedParams,
    ) -> list[float] | None:
        """Find nearest flower the pollinator can visit, regardless of state.

        Used as a waypoint when no FRUITING flowers exist — the pollinator
        flies to the flower cluster and waits for blooms instead of sitting
        at water indefinitely.
        """
        best_dist, best_pos = float("inf"), None
        for other in self._entities_in_range(pos, search_range):
            if other["state"] in ("DEAD", "DYING"):
                continue
            ixns = self.compiled.get_interactions(p.species_id, other.get("species", ""))
            if not any(ix.interaction_type == "pollination" for ix in ixns):
                continue
            d = self._distance(pos, other["position"])
            if d < best_dist:
                best_dist, best_pos = d, list(other["position"])
        return best_pos

    # ═══════════════════════════════════════════════════════════════════════
    # Interaction Events — All Values from DerivedParams
    # ═══════════════════════════════════════════════════════════════════════

    def _predation_event(self, predator: dict, prey: dict, p: DerivedParams) -> None:
        """Execute a predation event: predator kills and consumes prey."""
        predator["state_vars"]["hunger"] = max(
            0.0, predator["state_vars"]["hunger"] - p.predation_relief)
        predator["state_vars"]["energy"] = min(
            1.0, predator["state_vars"]["energy"] + p.predation_energy_gain)
        prey["state"] = "DYING"
        self._schedule_removal(prey)
        self._deposit_organic_matter(prey, self._get_params(prey))
        self._events.append({
            "type": "PREDATION", "tick": self.tick,
            "source_id": predator["id"], "target_id": prey["id"],
            "position": list(prey["position"]),
        })

    def _consumption_event(self, herbivore: dict, plant: dict, p: DerivedParams) -> None:
        """Execute an herbivory event: herbivore grazes on plant."""
        herbivore["state_vars"]["hunger"] = max(
            0.0, herbivore["state_vars"]["hunger"] - p.herbivory_relief)
        plant["state_vars"]["growth"] = max(
            0.0, plant["state_vars"]["growth"] - p.consumption_damage_growth * self.rate_consumption)
        plant["state_vars"]["health"] = max(
            0.0, plant["state_vars"]["health"] - p.consumption_damage_health * self.rate_consumption)
        self._events.append({
            "type": "CONSUMPTION", "tick": self.tick,
            "source_id": herbivore["id"], "target_id": plant["id"],
            "position": list(plant["position"]),
        })

    def _pollination_event(self, pollinator: dict, plant: dict,
                           p: DerivedParams, ix_params) -> None:
        """Execute a pollination event: pollinator visits flower."""
        plant["state_vars"]["health"] = min(
            1.0, plant["state_vars"]["health"] + POLLINATION_HEALTH_BOOST)
        pollinator["state_vars"]["hunger"] = max(
            0.0, pollinator["state_vars"]["hunger"] - p.pollination_relief)
        pollinator["_linger"] = ix_params.linger_ticks
        pollinator["_target"] = None
        plant["_pollination_cooldown"] = ix_params.cooldown_ticks
        self._events.append({
            "type": "POLLINATION", "tick": self.tick,
            "source_id": pollinator["id"], "target_id": plant["id"],
            "position": list(plant["position"]),
        })

    def _reproduction_event(self, parent: dict, p: DerivedParams) -> None:
        """Execute a reproduction event: parent spawns offspring."""
        parent["state_vars"]["reproductive_drive"] = 0.0
        parent["state_vars"]["energy"] = max(
            0.0, parent["state_vars"]["energy"] - p.parent_energy_cost)

        if "colony_health" in parent["state_vars"]:
            parent["state_vars"]["colony_health"] = max(
                0.0, parent["state_vars"]["colony_health"] - p.parent_energy_cost * 0.3)

        for _ in range(p.clutch_size):
            child = init_entity({
                "id": f"{parent['id']}_child_{self.tick}_{random.randint(0, 999)}",
                "type": parent["type"],
                "species": parent.get("species", "unknown"),
                "position": self._clamp_to_grid([
                    parent["position"][0] + random.uniform(-SPAWN_OFFSET, SPAWN_OFFSET), 0.0,
                    parent["position"][2] + random.uniform(-SPAWN_OFFSET, SPAWN_OFFSET),
                ]),
                "metadata": dict(parent["metadata"]),
                "skeleton_id": parent.get("skeleton_id"),
            })
            # Children inherit parent stress (generational decline)
            psv = parent["state_vars"]
            csv = child["state_vars"]
            csv["hunger"] = psv["hunger"] * CHILD_HUNGER_INHERIT
            csv["energy"] = max(CHILD_ENERGY_FLOOR, psv["energy"] * CHILD_ENERGY_INHERIT)
            if "colony_health" in csv:
                csv["colony_health"] = max(CHILD_COLONY_FLOOR,
                                           psv.get("colony_health", 1.0) * CHILD_COLONY_INHERIT)
            if "health" in csv:
                csv["health"] = max(CHILD_HEALTH_FLOOR,
                                    psv.get("health", 1.0) * CHILD_HEALTH_INHERIT)
            self._spawns.append(child)
            self._events.append({
                "type": "REPRODUCTION", "tick": self.tick,
                "source_id": parent["id"], "target_id": child["id"],
                "position": list(child["position"]),
            })

    # ═══════════════════════════════════════════════════════════════════════
    # Plant Spreading
    # ═══════════════════════════════════════════════════════════════════════

    def _try_plant_spread(self, e: dict, sv: dict, p: DerivedParams, dt: float) -> None:
        """Attempt vegetative spreading for a plant entity.

        Requirements: parent must be healthy, hydrated, and grown.
        Target cell must have adequate soil moisture/nutrients and no
        existing plant within the density check radius.
        """
        if (sv["health"] < SPREAD_MIN_HEALTH
                or sv["hydration"] < SPREAD_MIN_HYDRATION
                or sv["growth"] < SPREAD_MIN_GROWTH):
            return

        cooldown = e.get("_spread_cooldown", 0)
        if cooldown > 0:
            e["_spread_cooldown"] = cooldown - 1
            return

        if random.random() > p.spread_chance * self.rate_reproduction:
            return

        spread_range = p.spread_range or 2.0
        spread_pos = self._clamp_to_grid([
            e["position"][0] + random.uniform(-spread_range, spread_range), 0.0,
            e["position"][2] + random.uniform(-spread_range, spread_range),
        ])

        # Density check — no other autotroph within radius
        for other in self._entities_in_range(spread_pos, SPREAD_DENSITY_RADIUS):
            op = self._get_params(other)
            if op and op.diet_type == "autotroph" and other["id"] != e["id"]:
                e["_spread_cooldown"] = p.spread_cooldown // 2
                return

        # Soil quality check
        gx, gy, gz = self.voxels.world_to_grid(*spread_pos)
        if (self.voxels.get("moisture", gx, gy, gz) < SPREAD_SOIL_MIN_MOISTURE
                or self.voxels.get("nutrients", gx, gy, gz) < SPREAD_SOIL_MIN_NUTRIENTS):
            return

        # Spawn child plant
        child = init_entity({
            "id": f"{e['id']}_s{self.tick}",
            "type": e["type"],
            "species": e.get("species"),
            "position": spread_pos,
            "metadata": dict(e["metadata"]),
            "state_vars": {
                "growth": 0.05,
                "hydration": self.voxels.get("moisture", gx, gy, gz) * 0.8,
                "nutrient_store": 0.3, "health": 0.8, "age": 0.0,
            },
        })
        self._spawns.append(child)

        # Parent pays a cost to spread
        sv["growth"] -= SPREAD_PARENT_GROWTH_COST
        sv["nutrient_store"] = max(0.0, sv["nutrient_store"] - SPREAD_PARENT_NUTRIENT_COST)
        e["_spread_cooldown"] = p.spread_cooldown

        self._events.append({
            "type": "REPRODUCTION", "tick": self.tick,
            "source_id": e["id"], "target_id": child["id"],
            "position": list(spread_pos),
        })

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 4: Voxel Effects — Entity Impact on Soil
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_voxel_effects(self, e: dict[str, Any], dt: float) -> None:
        """Apply entity-driven changes to the voxel soil grid.

        Autotrophs drain nutrients and moisture. Decomposers convert
        organic matter into nutrients.
        """
        params = self._get_params(e)
        if params is None:
            return
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])

        if params.diet_type == "autotroph":
            n_demand = e["metadata"].get("nutrient_demand", {})
            total_demand = (sum(n_demand.values())
                           if isinstance(n_demand, dict)
                           else PLANT_DEFAULT_NUTRIENT_DEMAND)
            self.voxels.add("nutrients", gx, gy, gz, -total_demand * dt)
            base_demand = PLANT_BASE_WATER_DEMAND
            size_factor = 1.0 + (params.canopy_radius or 0.0) * 0.3
            self.voxels.add("moisture", gx, gy, gz, -base_demand * size_factor * dt)

        elif params.diet_type == "decomposer":
            activity = e["state_vars"].get("activity", 0)
            rate = self.biome.decomposition_rate * activity * dt
            self.voxels.add("organic_matter", gx, gy, gz, -rate)
            self.voxels.add("nutrients", gx, gy, gz, rate * DECOMP_NUTRIENT_EFFICIENCY)

    def _deposit_organic_matter(self, e: dict, p: DerivedParams | dict | None) -> None:
        """Deposit entity biomass into the organic matter voxel layer on death."""
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        if p is not None:
            mr = getattr(p, "metabolic_rate", None) or (p.get("metabolic_rate") if isinstance(p, dict) else None)
            if mr is not None:
                deposit = min(OM_DEPOSIT_MAX, mr * OM_DEPOSIT_SCALE)
                deposit = max(deposit, OM_DEPOSIT_MIN)
            else:
                mass = e.get("metadata", {}).get("body_mass", 10.0)
                deposit = min(0.3, mass / 500.0)
        else:
            mass = e.get("metadata", {}).get("body_mass", 10.0)
            deposit = min(0.3, mass / 500.0)
        self.voxels.add("organic_matter", gx, gy, gz, deposit)

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 5: Water & Soil — World-Level Processes
    # ═══════════════════════════════════════════════════════════════════════

    def _evaporate_soil(self, dt: float) -> None:
        """Background soil moisture evaporation (suppressed during rain)."""
        if self._rain_ticks_remaining > 0:
            self._rain_ticks_remaining -= 1
            return
        temp = self.climate.get("temperature", 20.0)
        humidity = self.climate.get("humidity", 0.5)
        evap = (SOIL_EVAP_BASE_RATE * (temp / SOIL_EVAP_TEMP_SCALE)
                * (1.0 - humidity * SOIL_EVAP_HUMIDITY_FACTOR) * self.rate_thirst * dt)
        dims = self.voxels.dimensions
        for x in range(dims[0]):
            for z in range(dims[2]):
                current = self.voxels.get("moisture", x, 0, z)
                if current > SOIL_MOISTURE_FLOOR:
                    self.voxels.set("moisture", x, 0, z,
                                    max(SOIL_MOISTURE_FLOOR, current - evap))

    def _init_water_source(self, source: dict[str, Any]) -> None:
        """Initialize soil moisture footprint around a water source."""
        cx, _, cz = source["position"]
        r = source["radius"]
        for ix in range(int(cx - r), int(cx + r) + 1):
            for iz in range(int(cz - r), int(cz + r) + 1):
                if (ix - cx) ** 2 + (iz - cz) ** 2 <= r * r:
                    gx, gy, gz = self.voxels.world_to_grid(float(ix), 0.0, float(iz))
                    self.voxels.set("moisture", gx, gy, gz, 0.95)

    def _replenish_water_sources(self, dt: float) -> None:
        """Evaporate and replenish water sources; update soil moisture footprint."""
        for source in self.water_sources:
            evap_loss = WATER_EVAPORATION_RATE * self.rate_thirst * dt
            replenish = WATER_REPLENISH_RATE * self.rate_water_replenish * dt
            source["water_level"] = max(0.0, min(1.0,
                source["water_level"] - evap_loss + replenish))
            source["radius"] = source["max_radius"] * source["water_level"]

            # Update soil moisture in water footprint
            cx, _, cz = source["position"]
            max_r = source["max_radius"]
            eff_r = source["radius"]
            for ix in range(int(cx - max_r), int(cx + max_r) + 1):
                for iz in range(int(cz - max_r), int(cz + max_r) + 1):
                    dist_sq = (ix - cx) ** 2 + (iz - cz) ** 2
                    gx, gy, gz = self.voxels.world_to_grid(float(ix), 0.0, float(iz))
                    if dist_sq <= eff_r * eff_r:
                        target = WATER_SOURCE_MOISTURE_TARGET * source["water_level"]
                        current = self.voxels.get("moisture", gx, gy, gz)
                        if current < target:
                            refill = WATER_REFILL_RATE * self.rate_water_replenish * dt
                            self.voxels.set("moisture", gx, gy, gz,
                                            min(target, current + refill))
                    elif dist_sq <= max_r * max_r:
                        current = self.voxels.get("moisture", gx, gy, gz)
                        if current > 0.3:
                            self.voxels.set("moisture", gx, gy, gz,
                                            max(0.3, current - 0.02 * dt))

    def apply_rain(self, intensity: float = 0.5) -> None:
        """Apply a rain event across the entire grid.

        Boosts soil moisture and nutrients, refills water sources,
        hydrates plants and animals, and suppresses evaporation.
        Triggered via WebSocket control message or programmatic API.
        """
        dims = self.voxels.dimensions

        # Soil moisture boost
        for x in range(dims[0]):
            for z in range(dims[2]):
                current = self.voxels.get("moisture", x, 0, z)
                self.voxels.set("moisture", x, 0, z,
                                min(1.0, current + RAIN_MOISTURE_BOOST * intensity))

        # Soil nutrient boost (dissolved minerals in rainwater)
        for x in range(dims[0]):
            for z in range(dims[2]):
                current = self.voxels.get("nutrients", x, 0, z)
                self.voxels.set("nutrients", x, 0, z,
                                min(1.0, current + RAIN_NUTRIENT_BOOST * intensity))

        # Water source refill
        for source in self.water_sources:
            source["water_level"] = min(
                1.0, source["water_level"] + RAIN_WATER_SOURCE_BOOST * intensity)
            source["radius"] = source["max_radius"] * source["water_level"]

        # Suppress evaporation
        self._rain_ticks_remaining = RAIN_SUPPRESSION_TICKS

        # Direct entity hydration/health boost
        for ent in self.entities.values():
            if not is_alive(ent):
                continue
            sv = ent["state_vars"]
            params = self._get_params(ent)
            if params and params.diet_type == "autotroph":
                sv["hydration"] = min(1.0, sv["hydration"] + RAIN_PLANT_HYDRATION * intensity)
                sv["health"] = min(1.0, sv["health"] + RAIN_PLANT_HEALTH * intensity)
            elif params and params.speed > 0:
                if "hydration" in sv:
                    sv["hydration"] = min(
                        1.0, sv["hydration"] + RAIN_ANIMAL_HYDRATION * intensity)

        self._events.append({
            "type": "RAIN", "tick": self.tick, "intensity": intensity,
            "position": [dims[0] / 2, 0.0, dims[2] / 2],
        })

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 6: Motor Inference (BYOM)
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_motor_inference(self) -> None:
        """Run BYOM motor adapter inference for entities with skeletons.

        The adapter receives context vectors (state, environment) and returns
        4D motion latent vectors that drive skeletal animation on the client.
        """
        skeleton_entities = [
            e for e in self.entities.values()
            if is_alive(e) and e.get("skeleton_id")
        ]
        if not skeleton_entities:
            return
        adapter = self._motor_adapter
        has_type_specs = hasattr(adapter, "context_spec_for")
        contexts = []
        for entity in skeleton_entities:
            spec = (adapter.context_spec_for(entity["type"])
                    if has_type_specs else adapter.context_spec())
            ctx = build_context(spec, entity, self.biome, self.climate)
            contexts.append(ctx)
        latents = adapter.infer(contexts)
        for entity, latent in zip(skeleton_entities, latents):
            entity["motion_latent"] = latent

    # ═══════════════════════════════════════════════════════════════════════
    # Spatial Helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _rebuild_spatial_index(self) -> None:
        """Rebuild the brute-force spatial index (O(n) per query).

        TODO: Replace with spatial hash for O(1) neighbor queries when
        entity count exceeds ~100.
        """
        self._positions = {
            eid: list(e["position"])
            for eid, e in self.entities.items()
            if is_alive(e)
        }

    def _entities_in_range(
        self, pos: list[float], radius: float, exclude_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find all living entities within radius of pos (brute force)."""
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

    @staticmethod
    def _distance(a: list[float], b: list[float]) -> float:
        """2D Euclidean distance (XZ plane, Y is vertical)."""
        dx = a[0] - b[0]
        dz = a[2] - b[2]
        return math.sqrt(dx * dx + dz * dz)

    def _clamp_to_grid(self, pos: list[float]) -> list[float]:
        """Clamp position to grid bounds with margin."""
        margin = 0.5
        lo, hi = margin, self._grid_max - margin
        return [max(lo, min(hi, pos[0])), pos[1], max(lo, min(hi, pos[2]))]

    # ═══════════════════════════════════════════════════════════════════════
    # Water & Mate Helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _find_nearest_water(self, pos: list[float]) -> list[float] | None:
        """Find the nearest non-dry water source and return an approach point."""
        best, best_dist = None, float("inf")
        for source in self.water_sources:
            if source["water_level"] < WATER_DRY_THRESHOLD:
                continue
            d = self._distance(pos, source["position"])
            if d < best_dist:
                best_dist, best = d, source
        if best is None:
            return None
        dx = best["position"][0] - pos[0]
        dz = best["position"][2] - pos[2]
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 0.1:
            return list(best["position"])
        r = best["radius"] * 0.5
        return [best["position"][0] - (dx / dist) * r, 0.0,
                best["position"][2] - (dz / dist) * r]

    def _drain_nearest_water(self, pos: list[float], amount: float) -> None:
        """Drain water from the nearest source (called during drinking)."""
        best, best_dist = None, float("inf")
        for source in self.water_sources:
            d = self._distance(pos, source["position"])
            if d < source["max_radius"] * 2 and d < best_dist:
                best_dist, best = d, source
        if best is not None:
            best["water_level"] = max(0.0, best["water_level"] - amount)

    def _is_near_water(self, pos: list[float]) -> bool:
        """Check if position is within effective radius of any water source."""
        for source in self.water_sources:
            if source["water_level"] < WATER_DRY_THRESHOLD:
                continue
            if self._distance(pos, source["position"]) <= source["radius"] + 1.0:
                return True
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Legacy Helper Functions — no traits required
    # ═══════════════════════════════════════════════════════════════════════

    def _find_mate_legacy(self, e: dict[str, Any]) -> bool:
        """Legacy mate finding — uses metadata sensory_range or default 8.0."""
        meta = e.get("metadata", {})
        sensory = meta.get("sensory_range", 8.0)
        for other in self._entities_in_range(e["position"], sensory, e["id"]):
            if (other.get("species") == e.get("species")
                    and other["state_vars"].get("reproductive_drive", 0) > 0.3):
                return True
        return False

    def _reproduction_event_legacy(self, parent: dict, meta: dict) -> None:
        """Legacy reproduction — uses metadata for costs/sizes."""
        parent["state_vars"]["reproductive_drive"] = 0.0
        parent["state_vars"]["energy"] = max(
            0.0, parent["state_vars"]["energy"] - 0.15)

        if "colony_health" in parent["state_vars"]:
            parent["state_vars"]["colony_health"] = max(
                0.0, parent["state_vars"]["colony_health"] - 0.045)

        clutch_size = meta.get("clutch_size", 2) if "clutch_size" in meta else (3 if parent["type"] == "INSECT" else 1)
        for _ in range(clutch_size):
            child = init_entity({
                "id": f"{parent['id']}_child_{self.tick}_{random.randint(0, 999)}",
                "type": parent["type"],
                "species": parent.get("species", "unknown"),
                "position": self._clamp_to_grid([
                    parent["position"][0] + random.uniform(-1.0, 1.0), 0.0,
                    parent["position"][2] + random.uniform(-1.0, 1.0),
                ]),
                "metadata": dict(parent.get("metadata", {})),
                "skeleton_id": parent.get("skeleton_id"),
            })
            psv = parent["state_vars"]
            csv = child["state_vars"]
            csv["hunger"] = psv.get("hunger", 0) * 0.5
            csv["energy"] = max(0.3, psv.get("energy", 1.0) * 0.7)
            if "colony_health" in csv:
                csv["colony_health"] = max(0.4, psv.get("colony_health", 1.0) * 0.7)
            if "health" in csv:
                csv["health"] = max(0.6, psv.get("health", 1.0) * 0.8)
            self._spawns.append(child)
            self._events.append({
                "type": "REPRODUCTION", "tick": self.tick,
                "source_id": parent["id"], "target_id": child["id"],
                "position": list(child["position"]),
            })

    def _deposit_organic_matter_legacy(self, e: dict) -> None:
        """Legacy organic matter deposit — uses metadata body_mass."""
        gx, gy, gz = self.voxels.world_to_grid(*e["position"])
        mass = e.get("metadata", {}).get("body_mass", 10.0)
        deposit = min(0.3, mass / 500.0)
        self.voxels.add("organic_matter", gx, gy, gz, deposit)

    # ═══════════════════════════════════════════════════════════════════════

    def _find_mate(self, e: dict[str, Any]) -> bool:
        """Check if a compatible mate is within sensory range."""
        params = self._get_params(e)
        sensory = params.sensory_range if params else 8.0
        for other in self._entities_in_range(e["position"], sensory, e["id"]):
            if (other.get("species") == e.get("species")
                    and other["state_vars"].get("reproductive_drive", 0) > 0.3):
                return True
        return False

    def _find_nearest_mate_pos(self, e: dict[str, Any]) -> list[float] | None:
        """Find position of nearest conspecific for mating approach."""
        best_dist, best_pos = float("inf"), None
        for other in self.entities.values():
            if other["id"] == e["id"] or not is_alive(other):
                continue
            if other.get("species") != e.get("species"):
                continue
            d = self._distance(e["position"], other["position"])
            if 1.0 < d < best_dist:
                best_dist, best_pos = d, list(other["position"])
        return best_pos

    def _flee_direction(self, pos: list[float], threat_pos: list[float]) -> list[float]:
        """Calculate escape target: run FLEE_ESCAPE_DISTANCE away from threat."""
        dx = pos[0] - threat_pos[0]
        dz = pos[2] - threat_pos[2]
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 0.01:
            dx, dz = random.uniform(-1, 1), random.uniform(-1, 1)
            dist = math.sqrt(dx * dx + dz * dz)
        return self._clamp_to_grid([
            pos[0] + (dx / dist) * FLEE_ESCAPE_DISTANCE, 0.0,
            pos[2] + (dz / dist) * FLEE_ESCAPE_DISTANCE,
        ])

    # ═══════════════════════════════════════════════════════════════════════
    # Event Emission
    # ═══════════════════════════════════════════════════════════════════════

    def _emit_event(self, event_type: str, entity: dict,
                    target: dict | None = None) -> None:
        self._events.append({
            "type": event_type, "tick": self.tick,
            "source_id": entity["id"],
            "target_id": target["id"] if target else None,
            "position": list(entity["position"]),
        })

    def _emit_state_change(self, entity: dict, old_state: str, new_state: str) -> None:
        self._events.append({
            "type": "STATE_CHANGE", "tick": self.tick,
            "source_id": entity["id"], "target_id": None,
            "position": list(entity["position"]),
            "prev_state": old_state, "new_state": new_state,
        })

    def _schedule_removal(self, entity: dict) -> None:
        if entity["id"] not in self._removals:
            self._removals.append(entity["id"])

    # ═══════════════════════════════════════════════════════════════════════
    # Tick Packet Assembly
    # ═══════════════════════════════════════════════════════════════════════

    def _build_tick_packet(self, dt: float) -> dict[str, Any]:
        """Build delta-encoded tick packet for WebSocket transmission."""
        packet: dict[str, Any] = {"tick": self.tick, "dt": dt}
        updates = []
        for e in self.entities.values():
            update: dict[str, Any] = {
                "id": e["id"], "state": e["state"],
                "position": [round(v, 4) for v in e["position"]],
                "velocity": [round(v, 4) for v in e.get("velocity", [0, 0, 0])],
                "state_vars": {k: round(v, 4) for k, v in e["state_vars"].items()},
            }
            if e.get("skeleton_id"):
                update["motion_latent"] = e.get("motion_latent", [0.0, 0.0, 0.0, 0.0])
            updates.append(update)
        packet["entity_updates"] = updates
        if self._spawns:
            packet["entity_spawns"] = [
                {"id": s["id"], "type": s["type"], "species": s.get("species"),
                 "position": [round(v, 4) for v in s["position"]],
                 "skeleton_id": s.get("skeleton_id"), "state": s["state"],
                 "state_vars": {k: round(v, 4) for k, v in s["state_vars"].items()},
                 "motion_latent": [0.0, 0.0, 0.0, 0.0]}
                for s in self._spawns
            ]
        if self._removals:
            packet["entity_removals"] = list(self._removals)
        if self._events:
            packet["events"] = self._events
        voxel_packet = self.voxels.get_delta_packet()
        if voxel_packet:
            packet["voxel_deltas"] = voxel_packet
        if self.water_sources:
            packet["water_sources"] = [
                {"position": ws["position"], "radius": ws["radius"],
                 "water_level": ws["water_level"]}
                for ws in self.water_sources
            ]
        return packet

    # ═══════════════════════════════════════════════════════════════════════
    # World Randomization (Opt-In)
    # ═══════════════════════════════════════════════════════════════════════

    def _randomize_world(self) -> None:
        """Apply D4 symmetry transforms, jitter, and extra entities.

        Only runs if the world config includes a ``randomize`` key.
        See README for randomization options.
        """
        cfg = self._randomize_config
        if cfg is None or len(self.entities) < 5:
            return
        import time as _time
        rng = random.Random(int(_time.time()))
        jitter = cfg.get("jitter", 1.5)
        extra_grass_range = cfg.get("extra_grass", [0, 4])
        extra_flowers_range = cfg.get("extra_flowers", [0, 2])
        do_transform = cfg.get("transform", True)
        center = self._grid_max / 2.0

        # D4 symmetry transform (rotation + optional flip)
        if do_transform:
            rotation = rng.choice([0, 90, 180, 270])
            flip_x = rng.choice([True, False])
        else:
            rotation, flip_x = 0, False

        def transform_pos(pos):
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

        for e in self.entities.values():
            e["position"][:] = transform_pos(e["position"])
        for source in self.water_sources:
            source["position"][:] = transform_pos(source["position"])

        # Water source position jitter
        for source in self.water_sources:
            pos = source["position"]
            pos[0] += rng.uniform(-3.0, 3.0)
            pos[2] += rng.uniform(-3.0, 3.0)
            pos[:] = self._clamp_to_grid(pos)
            source["max_radius"] = max(1.0, source["max_radius"] + rng.uniform(-0.5, 0.5))
            source["radius"] = source["max_radius"]

        # Entity position jitter + state variable noise
        for e in self.entities.values():
            pos = e["position"]
            pos[0] += rng.uniform(-jitter, jitter)
            pos[2] += rng.uniform(-jitter, jitter)
            pos[:] = self._clamp_to_grid(pos)
            sv = e["state_vars"]
            for key in ("hunger", "energy", "hydration", "health"):
                if key in sv:
                    sv[key] = max(0.0, min(1.0, sv[key] + rng.uniform(-0.05, 0.05)))

        # Extra grass and flower spawns
        grass_tpl = flower_tpl = None
        for e in self.entities.values():
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
                    "id": f"grass_r{i}", "type": "PLANT", "species": "meadow_grass",
                    "position": pos, "metadata": dict(grass_tpl["metadata"]),
                    "state_vars": {"growth": rng.uniform(0.05, 0.3),
                                   "hydration": rng.uniform(0.6, 1.0),
                                   "nutrient_store": rng.uniform(0.3, 0.6),
                                   "health": 1.0, "age": 0.0}})
                self.entities[child["id"]] = child

        if flower_tpl:
            for i in range(rng.randint(*extra_flowers_range)):
                pos = self._clamp_to_grid([
                    rng.uniform(3.0, self._grid_max - 3.0), 0.0,
                    rng.uniform(3.0, self._grid_max - 3.0)])
                child = init_entity({
                    "id": f"flower_r{i}", "type": "PLANT", "species": "wildflower",
                    "position": pos, "metadata": dict(flower_tpl["metadata"]),
                    "state_vars": {"growth": rng.uniform(0.05, 0.2),
                                   "hydration": rng.uniform(0.6, 1.0),
                                   "nutrient_store": rng.uniform(0.3, 0.6),
                                   "health": 1.0, "age": 0.0}})
                self.entities[child["id"]] = child

        # Push plants out of water sources
        self._push_entities_from_water(rng)

    def _push_entities_from_water(self, rng) -> None:
        """Move sessile entities out of water source footprints."""
        for e in self.entities.values():
            ep = self._get_params(e)
            if ep and ep.locomotion in ("sessile", "rooted"):
                pos = e["position"]
                for source in self.water_sources:
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
