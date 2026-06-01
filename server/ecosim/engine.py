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
    DECOMP_NUTRIENT_EFFICIENCY,
    OM_DEPOSIT_MAX,
    OM_DEPOSIT_MIN,
    OM_DEPOSIT_SCALE,
    PLANT_BASE_WATER_DEMAND,
    PLANT_DEFAULT_NUTRIENT_DEMAND,
    RAIN_ANIMAL_HYDRATION,
    RAIN_MOISTURE_BOOST,
    RAIN_NUTRIENT_BOOST,
    RAIN_PLANT_HEALTH,
    RAIN_PLANT_HYDRATION,
    RAIN_SUPPRESSION_TICKS,
    RAIN_WATER_SOURCE_BOOST,
    SOIL_EVAP_BASE_RATE,
    SOIL_EVAP_HUMIDITY_FACTOR,
    SOIL_EVAP_TEMP_SCALE,
    SOIL_MOISTURE_FLOOR,
    WATER_EVAPORATION_RATE,
    WATER_REFILL_RATE,
    WATER_REPLENISH_RATE,
    WATER_SOURCE_MOISTURE_TARGET,
)
from .effects import EffectBus
from .entities import init_entity, is_alive
from .layout import LayoutManager
from .movement_system import MovementSystem
from .spatial_index import SpatialQuery
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

        # ── Trait compilation ──
        # Converts species_definitions into DerivedParams + interaction matrix.
        biome_dict = {"name": self.biome_name}
        self.compiled: CompiledEcology = compile_world(world_config, biome_dict)

        self.tick: int = 0

        # ── Spatial index (neighbor queries for interaction actors) ──
        self._spatial = SpatialQuery()


        # ── BYOM motor adapter ──
        adapters = adapters or {}
        motor = adapters.get("motor")
        if motor is not None:
            self._motor_adapter: MotorAdapter = motor
        else:
            from .adapters.static import StaticMotorAdapter
            self._motor_adapter = StaticMotorAdapter()

        # ── Layout loading (entities + water sources + randomization) ──
        layout = LayoutManager(world_config, self.voxels)
        result = layout.load()
        self.entities: dict[str, dict[str, Any]] = result.entities
        self.water_sources: list[dict[str, Any]] = result.water_sources
        self._grid_max: float = result.grid_max
        layout.randomize(self.entities, self.water_sources)

        # ── Seed water source moisture footprints (after randomization) ──
        for source in self.water_sources:
            self._init_water_source_moisture(source)

        # ── Movement system (gate + kinematics for mobile entities) ──
        self._movement = MovementSystem(self._grid_max)

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

        # ── Effect bus (Phase 1 refactoring) ──
        self.effect_bus = EffectBus()

        # ── Actor registries (Phase 1 + Phase 2 refactoring) ──
        self.actor_registry = build_interaction_registry(self.compiled)
        self.flow_actor_registry = build_flow_registry(self.compiled)
        self.guard_actor_registry = build_guard_registry(self.compiled)



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
        self._spatial.rebuild(self.entities)

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
            if params and params.diet_type not in ("autotroph", "decomposer"):
                self._movement.step(entity, params, dt)

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
        nearby = self._spatial.query(
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

    def _init_water_source_moisture(self, source: dict[str, Any]) -> None:
        """Initialize soil moisture footprint around a water source.

        Called during engine init after randomization so the footprint
        matches the final (possibly transformed) water source position.
        """
        cx, _, cz = source["position"]
        r = source["radius"]
        for ix in range(int(cx - r), int(cx + r) + 1):
            for iz in range(int(cz - r), int(cz + r) + 1):
                if (ix - cx) ** 2 + (iz - cz) ** 2 <= r * r:
                    gx, gy, gz = self.voxels.world_to_grid(float(ix), 0.0, float(iz))
                    self.voxels.set("moisture", gx, gy, gz, 0.95)

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


