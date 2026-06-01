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

Requirements
─────────────
All worlds must include a ``species_definitions`` key. Worlds without it
will fail at init with a clear error — there is no fallback.

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
    FlowContext,
    GuardContext,
    InteractionContext,
    build_flow_registry,
    build_guard_registry,
    build_interaction_registry,
)
from .biome import BiomeConfig, get_biome_config
from .constants import (
    ACTIVE_MOVEMENT_STATES,
    ARRIVAL_THRESHOLD,
    DECOMP_NUTRIENT_EFFICIENCY,
    DEHYDRATION_HYDRATION,
    OM_DEPOSIT_MAX,
    OM_DEPOSIT_MIN,
    OM_DEPOSIT_SCALE,
    PLANT_BASE_WATER_DEMAND,
    PLANT_DEFAULT_NUTRIENT_DEMAND,
    POLLINATOR_CROWD_RADIUS,
    POLLINATOR_MAX_PER_FLOWER,
    RAIN_ANIMAL_HYDRATION,
    RAIN_MOISTURE_BOOST,
    RAIN_NUTRIENT_BOOST,
    RAIN_PLANT_HEALTH,
    RAIN_PLANT_HYDRATION,
    RAIN_SUPPRESSION_TICKS,
    RAIN_WATER_SOURCE_BOOST,
    REPRO_MATE_SEEK_DRIVE,
    SOIL_EVAP_BASE_RATE,
    SOIL_EVAP_HUMIDITY_FACTOR,
    SOIL_EVAP_TEMP_SCALE,
    SOIL_MOISTURE_FLOOR,
    WANDER_RANGE,
    WATER_DRY_THRESHOLD,
    WATER_EVAPORATION_RATE,
    WATER_REFILL_RATE,
    WATER_REPLENISH_RATE,
    WATER_SOURCE_MOISTURE_TARGET,
)
from .effects import EffectBus
from .entities import init_entity, is_alive
from .model_adapter import MotorAdapter, build_context
from .trait_compiler import CompiledEcology, compile_world
from .traits import DerivedParams
from .voxel_manager import VoxelManager

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
        biome_dict = {"name": self.biome_name}
        self.compiled: CompiledEcology = compile_world(world_config, biome_dict)


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
        """Look up DerivedParams for an entity by its species_id."""
        species = entity.get("species")
        if species:
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

            # Decrement linger counter (set by pollination visits, etc.)
            linger = entity.get("_linger", 0)
            if linger > 0:
                entity["_linger"] = max(0, linger - 1)
                entity["velocity"] = [0.0, 0.0, 0.0]
                continue

            # Decrement post-visit cooldown (prevents immediate re-pollination
            # after lingering ends — forces butterfly to actually fly away before
            # it can visit another flower).
            poll_cooldown = entity.get("_pollination_cooldown", 0)
            if poll_cooldown > 0:
                entity["_pollination_cooldown"] = max(0, poll_cooldown - 1)

            # Pollinators move even when IDLE/WANDERING.
            # IDLE: actively seek and discover flowers across the field.
            # WANDERING: wander randomly (no flower-seeking) to disperse
            # after pollination bouts, exploring new areas.
            can_move = (
                entity["state"] in ACTIVE_MOVEMENT_STATES
                or (params.floral_affinity and entity["state"] in ("IDLE", "WANDERING"))
            )
            if params.speed > 0 and can_move:
                self._move_toward_target(entity, params, dt)

        # Phase 2: Interactions — entity↔entity events (actor-based)
        interaction_effects = []
        for entity in list(self.entities.values()):
            if not is_alive(entity):
                continue
            actors = self.actor_registry.get(entity.get("species"))
            if actors:
                ctx = self._build_interaction_context(entity, dt)
                # Each species can have multiple interaction actors
                # (e.g. FleeActor + HerbivoryActor for deer).
                # Run all of them — they produce independent effects.
                for actor in actors:
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

        # Query nearby entities using spatial index.
        # Pollinators (butterflies, etc.) sense chemical gradients across the
        # entire meadow — they can detect floral volatiles and nectar signals
        # from anywhere in the field. This lets them make informed dispersal
        # decisions (e.g., avoid crowded flowers, seek distant blooms) rather
        # than reacting only to what's immediately nearby.
        if params.floral_affinity:
            search_radius = self._grid_max
        else:
            search_radius = params.sensory_range
        nearby = self._entities_in_range(
            entity["position"], search_radius, entity["id"]
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
            _get_params=self._get_params,  # for querying other entities' traits
            _grid_max=self._grid_max,
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
            _get_params=self._get_params,  # for querying other entities' traits
        )

    def _move_toward_target(self, e: dict, p: DerivedParams, dt: float) -> None:
        """Move entity toward its current target at species-derived speed.

        Target selection is now handled by MovementActor (actor-based),
        which emits SetTarget/ClearTarget effects during the flow phase.
        This method only performs the physical movement step.

        When no target is set, the entity stops. The next tick's flow
        phase will have MovementActor select a new target via effects.
        On arrival within ARRIVAL_THRESHOLD, the target is cleared and
        the entity stops — MovementActor picks a new one on the next tick.
        """
        pos = e["position"]
        target = e.get("_target")

        if target is None:
            e["velocity"] = [0.0, 0.0, 0.0]
            return

        dx = target[0] - pos[0]
        dz = target[2] - pos[2]
        dist = math.sqrt(dx * dx + dz * dz)

        if dist < ARRIVAL_THRESHOLD:
            # Arrived — clear target. MovementActor will pick a new one
            # on the next tick's flow phase.
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

        .. deprecated:: Superseded by MovementActor (actors/movement_actors.py).
           Target selection is now handled by the actor-based system which emits
           SetTarget/ClearTarget effects during the flow phase. This method is
           kept for backward compatibility but is no longer called by the engine.

        Priority:
        1. DRINKING → seek nearest water if not already there, else stay put
        2. High reproductive drive → seek nearest mate
        3. FORAGING herbivore → seek nearest food by diet preference
        4. FORAGING pollinator → seek FRUITING flower → any flower → wander → water
        5. HUNTING → seek nearest prey
        6. Default → wander randomly
        """
        state = e["state"]
        pos = e["position"]

        # SWARMING — colony under stress, seek water for survival.
        # When ecosystem collapses and colony_health drops below 0.3,
        # insects enter SWARMING. They must navigate to water sources
        # where the near-water bonus slows hunger/colony drain while
        # conditions recover (e.g. rain revives plants).
        if state == "SWARMING":
            water = self._find_nearest_water(pos)
            if water:
                return water
            # No water found — wander to search
            return self._clamp_to_grid([
                pos[0] + random.uniform(-WANDER_RANGE, WANDER_RANGE), 0.0,
                pos[2] + random.uniform(-WANDER_RANGE, WANDER_RANGE),
            ])

        if state == "DRINKING":
            # If already at water, stay put and drink in place.
            # Otherwise navigate toward the nearest water source.
            if self._is_near_water(pos):
                return None
            water = self._find_nearest_water(pos)
            if water:
                return water
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

            # Emergency: critically dehydrated forager with no food nearby.
            # During ecosystem collapse (plants dormant/dead), herbivores would
            # otherwise wander randomly over dead vegetation and die of dehydration.
            # Seek water to survive until conditions improve (e.g. rain revives plants).
            hydration = e["state_vars"].get("hydration", 1.0)
            if hydration < DEHYDRATION_HYDRATION:
                water = self._find_nearest_water(pos)
                if water:
                    return water

            # Pollinators: seek flowers (FRUITING first, then any flower, water last)
            if p.floral_affinity:
                # Priority 1: FRUITING flowers — actual nectar available
                flower = self._find_nearest_flower(pos, self._grid_max, p)
                if flower:
                    return flower

                # Priority 2: drift toward any non-dead flower and wait for bloom.
                # This MUST come before the water fallback. The old ordering put
                # water first (for "stress cascade" clustering), but that created a
                # trap: once at water, the near-water hunger bonus kept pollinators
                # below POLLINATOR_CRITICAL_HUNGER, so they never reached the
                # threshold needed to trigger the any-flower fallback. Result:
                # butterflies stuck at ponds forever even with flowers blooming.
                any_flower = self._find_nearest_flower_any_state(pos, self._grid_max, p)
                if any_flower:
                    return any_flower

                # Priority 3: no flowers found — wander randomly across the field.
                # This is the endgame behavior when all plants are dormant/dead:
                # butterflies fly around searching for nectar. If they become
                # critically dehydrated, the guard actor forces DRINKING state
                # which overrides this and sends them to water as last resort.
                return self._clamp_to_grid([
                    pos[0] + random.uniform(-WANDER_RANGE, WANDER_RANGE), 0.0,
                    pos[2] + random.uniform(-WANDER_RANGE, WANDER_RANGE),
                ])

        if state == "HUNTING":
            prey_species = [s for s, _ in self.compiled.get_diet_order(p.species_id)]
            target = self._find_nearest_prey(pos, p.sensory_range, prey_species)
            if target:
                return target

            # Emergency: critically dehydrated hunter with no prey nearby.
            hydration = e["state_vars"].get("hydration", 1.0)
            if hydration < DEHYDRATION_HYDRATION:
                water = self._find_nearest_water(pos)
                if water:
                    return water

        # Pollinators seek flowers when IDLE so they actively explore and
        # discover flowers instead of sitting still waiting for hunger to build.
        # WANDERING is excluded — during forced exploration cooldown, butterflies
        # should wander randomly across the field to disperse, not fly back to
        # nearby flowers (which would defeat the purpose of dispersal).
        if p.floral_affinity and state == "IDLE":
            flower = self._find_nearest_flower(pos, self._grid_max, p)
            if flower:
                return flower
            any_flower = self._find_nearest_flower_any_state(pos, self._grid_max, p)
            if any_flower:
                return any_flower
            # No flowers in range (all dormant/dead or none exist) — wander randomly.
            # When all plants are dormant, pollinators should fly across the field
            # searching for nectar rather than heading straight to water. If they
            # become critically dehydrated, the guard actor forces DRINKING state.
            return self._clamp_to_grid([
                pos[0] + random.uniform(-WANDER_RANGE, WANDER_RANGE), 0.0,
                pos[2] + random.uniform(-WANDER_RANGE, WANDER_RANGE),
            ])

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

    def _count_pollinators_at_flower(self, flower_pos: list[float]) -> int:
        """Count pollinators currently lingering at or near a flower.

        Used to enforce per-flower visitor cap so butterflies disperse
        across the field instead of all clustering on one plant.
        """
        count = 0
        r2 = POLLINATOR_CROWD_RADIUS ** 2
        for eid, epos in self._positions.items():
            entity = self.entities.get(eid)
            if not entity or not is_alive(entity):
                continue
            params = self._get_params(entity)
            if not params or not params.floral_affinity:
                continue
            dx = epos[0] - flower_pos[0]
            dz = epos[2] - flower_pos[2]
            if dx * dx + dz * dz <= r2:
                # Count pollinators actively lingering (from pollination visit)
                # or very close with a target set (en route to the flower)
                if entity.get("_linger", 0) > 0:
                    count += 1
        return count

    def _find_nearest_flower(
        self, pos: list[float], search_range: float, p: DerivedParams,
    ) -> list[float] | None:
        """Find nearest FRUITING flower matching pollinator's floral affinity.

        Only returns flowers that are FRUITING, have a matching pollination
        syndrome (via the compiled interaction matrix), are not on
        pollination cooldown, and haven't reached max visitor capacity.
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
            # Skip flowers at max pollinator capacity — forces dispersal
            if self._count_pollinators_at_flower(other["position"]) >= POLLINATOR_MAX_PER_FLOWER:
                continue
            # Skip flowers too close — prevents re-targeting the same flower
            # after arriving. Forces butterfly to fly to a different flower or
            # wander away, breaking the infinite pollination loop.
            d = self._distance(pos, other["position"])
            if d < ARRIVAL_THRESHOLD * 2:
                continue
            if d < best_dist:
                best_dist, best_pos = d, list(other["position"])
        return best_pos

    def _find_nearest_flower_any_state(
        self, pos: list[float], search_range: float, p: DerivedParams,
    ) -> list[float] | None:
        """Find nearest flower the pollinator can visit, regardless of state.

        Used as a waypoint when no FRUITING flowers exist — the pollinator
        flies to the flower cluster and waits for blooms instead of sitting
        at water indefinitely. Respects per-flower visitor cap.

        Excludes DORMANT plants: they have no nectar and targeting them causes
        butterflies to shuttle between dormant plants endlessly instead of
        wandering across the field searching for active blooms.
        """
        best_dist, best_pos = float("inf"), None
        for other in self._entities_in_range(pos, search_range):
            if other["state"] in ("DEAD", "DYING", "DORMANT"):
                continue
            ixns = self.compiled.get_interactions(p.species_id, other.get("species", ""))
            if not any(ix.interaction_type == "pollination" for ix in ixns):
                continue
            # Skip flowers at max pollinator capacity
            if self._count_pollinators_at_flower(other["position"]) >= POLLINATOR_MAX_PER_FLOWER:
                continue
            # Skip flowers too close — prevents re-targeting after arrival
            d = self._distance(pos, other["position"])
            if d < ARRIVAL_THRESHOLD * 2:
                continue
            if d < best_dist:
                best_dist, best_pos = d, list(other["position"])
        return best_pos

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

    def _find_nearest_mate_pos(self, e: dict[str, Any]) -> list[float] | None:
        """Find position of nearest compatible mate across the full grid.
        Animals can detect mates at longer range (scent, calls)."""
        best_dist = float("inf")
        best_pos = None
        for other in self.entities.values():
            if other["id"] == e["id"]:
                continue
            if not is_alive(other):
                continue
            if other["type"] != e["type"]:
                continue
            if other.get("species") != e.get("species"):
                continue
            d = self._distance(e["position"], other["position"])
            if d < 1.0:
                continue  # Already next to them
            if d < best_dist:
                best_dist = d
                best_pos = list(other["position"])
        return best_pos

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
