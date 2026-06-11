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

from .config import SIM_CONFIG

DEFAULT_DT = SIM_CONFIG["engine_defaults"]["default_dt"]

# noqa: E402 — imports after config init to avoid circular dependencies
from .actors import (  # noqa: E402
    FlowContext,
    GuardContext,
    InteractionContext,
    build_flow_registry,
    build_guard_registry,
    build_interaction_registry,
)
from .constants import (  # noqa: E402
    DECOMP_NUTRIENT_EFFICIENCY,
    PLANT_BASE_WATER_DEMAND,
    PLANT_DEFAULT_NUTRIENT_DEMAND,
    RAIN_REPRO_RECOVERY_TICKS,
    RAIN_SUPPRESSION_TICKS,
    SOIL_EVAP_BASE_RATE,
    SOIL_EVAP_HUMIDITY_FACTOR,
    SOIL_EVAP_TEMP_SCALE,
    WATER_EVAPORATION_RATE,
    WATER_REFILL_RATE,
    WATER_REPLENISH_RATE,
)
from .effects import (  # noqa: E402
    EffectBus,
    NutrientPoolDynamics,
    SoilDeposit,
    SoilDrain,
    SoilEvaporation,
    WaterReplenish,
    WorldProcessContext,
)
from .entities import init_entity, is_alive  # noqa: E402
from .environment_manager import EnvironmentManager  # noqa: E402
from .model_adapter import MotorAdapter, build_context  # noqa: E402
from .movement_system import MovementSystem  # noqa: E402
from .spatial_index import SpatialQuery  # noqa: E402
from .trait_compiler import CompiledEcology, compile_world  # noqa: E402
from .traits import DerivedParams  # noqa: E402
from .world_processes import (  # noqa: E402
    deposit_organic_matter,
    register_default_world_handlers,
)

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
        # ── Environment setup (wrapped in EnvironmentManager) ──
        env_cfg = world_config["environment"]
        self.env = EnvironmentManager(
            biome_name=env_cfg.get("biome", "TEMPERATE"),
            climate=dict(env_cfg.get("climate", {})),
            voxel_grid_cfg=env_cfg.get("voxel_grid", {}),
            soil_cfg=env_cfg.get("soil")
        )

        # ── Trait compilation ──
        # Converts species_definitions into DerivedParams + interaction matrix.
        biome_dict = {"name": self.env.biome_name}
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
        result = self.env.load_layout(world_config)
        self.entities: dict[str, dict[str, Any]] = result.entities
        self._grid_max: float = result.grid_max

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
        # Two-pool nutrient rate multipliers (default 1.0 for backward compat)
        self.rate_mineralization: float = rates.get("mineralization", 1.0)
        self.rate_dissolution: float = rates.get("dissolution", 1.0)
        self.rate_nutrient_leaching: float = rates.get("nutrient_leaching", 1.0)

        # ── Internal bookkeeping ──
        self._events: list[dict[str, Any]] = []
        self._rain_ticks_remaining: int = 0
        # Ticks remaining for post-rain reproduction rebound boost.
        # Set when rain occurs; decrements each tick. Flow actors use this
        # to temporarily boost repro_drive_build after environmental recovery.
        self._recent_rain_recovery_ticks: int = 0
        self._spawns: list[dict[str, Any]] = []
        self._removals: list[str] = []

        # ── Client agency reconciliation ──
        # Entities whose client-reported positions were absorbed this tick.
        # Next packet will include _ack: true for these entities so the
        # client knows the server heard it and adjusted its expectation.
        self._pending_acks: set[str] = set()
        # Client-reported reproduction events pending absorption.
        self._pending_client_repro: list[dict[str, Any]] = []
        # Client-reported interaction events (consumption, predation, pollination).
        self._pending_client_events: list[dict[str, Any]] = []

        # ── Effect bus + world-process handlers ──
        self.effect_bus = EffectBus()
        register_default_world_handlers(self.effect_bus)

        # ── Actor registries ──
        self.actor_registry = build_interaction_registry(self.compiled)
        self.flow_actor_registry = build_flow_registry(self.compiled)
        self.guard_actor_registry = build_guard_registry(self.compiled)

    # ───────────────────────────────────────────────────────────────────────
    # Environment Properties (backward compatibility)
    # ───────────────────────────────────────────────────────────────────────

    @property
    def biome(self):
        return self.env.biome

    @property
    def biome_name(self):
        return self.env.biome_name

    @property
    def climate(self):
        return self.env.climate

    @property
    def voxels(self):
        return self.env.voxels

    @property
    def water_sources(self):
        return self.env.water_sources

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

    def step(self, dt: float | None = None) -> dict[str, Any]:
        """Advance the simulation by one tick.

        Executes the seven-phase hybrid automaton and returns a delta-encoded
        tick packet for client rendering via WebSocket.

        Args:
            dt: Time step in seconds. Defaults to ``engine_defaults.default_dt``
                from sim_config.json (0.1 = 10 Hz).

        Returns:
            Tick packet dict containing entity updates, spawns, removals,
            events, voxel deltas, and water source states.
        """
        if dt is None:
            dt = DEFAULT_DT
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
            self.env.voxels,
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
            self.env.voxels,
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
            self.env.voxels,
            self._spawns,
            self._removals,
            self._events,
            deposit_fn=lambda e, p: deposit_organic_matter(e, p, self.env.voxels),
        )

        # Phase 4: Voxel effects — entity impact on soil (effect-based)
        voxel_effects = []
        for entity in list(self.entities.values()):
            if is_alive(entity):
                self._build_voxel_effects(entity, dt, voxel_effects)
        world_ctx = WorldProcessContext(
            tick=self.tick,
            voxel_grid=self.env.voxels,
            biome=self.env.biome,
            climate=self.env.climate,
            entities=self.entities,
            water_sources=self.env.water_sources,
            rate_multipliers={
                "consumption": self.rate_consumption,
                "hunger": self.rate_hunger,
                "thirst": self.rate_thirst,
                "growth": self.rate_growth,
                "reproduction": self.rate_reproduction,
                "water_replenishment": self.rate_water_replenish,
                "mineralization": self.rate_mineralization,
                "dissolution": self.rate_dissolution,
                "nutrient_leaching": self.rate_nutrient_leaching,
            },
        )
        self.effect_bus.apply_world_batch(voxel_effects, self.tick, world_ctx)

        # Phase 5: Water & soil — world-level processes (effect-based)
        # Rain suppression counter is managed here (not in handler) so it
        # decrements every tick regardless of handler frequency.
        rain_suppressed = self._rain_ticks_remaining > 0
        if self._rain_ticks_remaining > 0:
            self._rain_ticks_remaining -= 1

        # Post-rain reproduction recovery window — decrement each tick.
        if self._recent_rain_recovery_ticks > 0:
            self._recent_rain_recovery_ticks -= 1

        world_effects: list[Any] = [
            SoilEvaporation(
                tick=self.tick,
                evap_rate=(
                    SOIL_EVAP_BASE_RATE
                    * (self.env.climate.get("temperature", 20.0) / SOIL_EVAP_TEMP_SCALE)
                    * (1.0 - self.env.climate.get("humidity", 0.5) * SOIL_EVAP_HUMIDITY_FACTOR)
                    * self.rate_thirst * dt
                ),
                rain_suppressed=rain_suppressed,
                rainfall_recharge=self.env.biome.rainfall_recharge * dt,
            ),
            NutrientPoolDynamics(tick=self.tick, dt=dt),
        ]
        if self.env.water_sources:
            world_effects.append(WaterReplenish(
                tick=self.tick,
                sources=self.env.water_sources,
                evap_loss=WATER_EVAPORATION_RATE * self.rate_thirst * dt,
                replenish_gain=(WATER_REPLENISH_RATE + self.env.biome.rainfall_recharge)
                    * self.rate_water_replenish * dt,
                soil_refill_rate=WATER_REFILL_RATE * self.rate_water_replenish * dt,
                soil_dry_rate=self.env.biome.soil_dry_rate_outside_footprint * dt,
            ))
        self.effect_bus.apply_world_batch(world_effects, self.tick, world_ctx)

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
                voxel_grid=self.env.voxels,
                biome=self.env.biome,
                compiled=self.compiled,
                params=None,
                nearby_entities=[],
                water_sources=self.env.water_sources,
                climate=self.env.climate,
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
            voxel_grid=self.env.voxels,
            biome=self.env.biome,
            compiled=self.compiled,
            params=params,
            nearby_entities=nearby,
            water_sources=self.env.water_sources,
            climate=self.env.climate,
            rate_multipliers=rate_multipliers,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 2 Context Builders — Flow + Guard actors (Phase 2)
    # ═══════════════════════════════════════════════════════════════════════

    def _build_flow_context(self, entity: dict[str, Any], dt: float) -> FlowContext:
        """Build a flow context for one entity (Phase 2).

        Extends InteractionContext with dt, rain_ticks_remaining, and
        recent_rain_recovery_ticks.
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
            voxel_grid=self.env.voxels,
            biome=self.env.biome,
            compiled=self.compiled,
            params=params,
            nearby_entities=[],  # flow actors don't need spatial queries
            water_sources=self.env.water_sources,
            climate=self.env.climate,
            rate_multipliers=rate_multipliers,
            dt=dt,
            rain_ticks_remaining=self._rain_ticks_remaining,
            recent_rain_recovery_ticks=self._recent_rain_recovery_ticks,
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
            voxel_grid=self.env.voxels,
            biome=self.env.biome,
            compiled=self.compiled,
            params=params,
            nearby_entities=[],
            water_sources=self.env.water_sources,
            climate=self.env.climate,
            rate_multipliers=rate_multipliers,
            _entities=self.entities,
            _get_params=self._get_params,  # for querying other entities' traits
        )

    # ═══════════════════════════════════════════════════════════════════════
    def _build_voxel_effects(
        self,
        e: dict[str, Any],
        dt: float,
        effects: list,
    ) -> None:
        """Build world-process effects for entity-driven soil changes.

        Autotrophs emit SoilDrain (nutrients + moisture). Decomposers emit
        SoilDeposit (organic matter consumption) and SoilDrain with negative
        amount (nutrient release from decomposition).

        Args:
            e: Entity dict.
            dt: Time step.
            effects: List to append world-process effects into.
        """
        params = self._get_params(e)
        if params is None:
            return

        if params.diet_type == "autotroph":
            n_demand = e["metadata"].get("nutrient_demand", {})
            total_demand = (sum(n_demand.values())
                           if isinstance(n_demand, dict)
                           else PLANT_DEFAULT_NUTRIENT_DEMAND)
            footprint_r = params.canopy_radius or 1.0
            effects.append(SoilDrain(
                tick=self.tick,
                entity_id=e["id"],
                position=e["position"],
                layer="nutrients_fast",
                amount=-total_demand * dt,
                radius=footprint_r,
            ))
            base_demand = PLANT_BASE_WATER_DEMAND
            size_factor = 1.0 + (params.canopy_radius or 0.0) * 0.3
            effects.append(SoilDrain(
                tick=self.tick,
                entity_id=e["id"],
                position=e["position"],
                layer="moisture",
                amount=-base_demand * size_factor * dt,
                radius=footprint_r,
            ))

        elif params.diet_type == "decomposer":
            activity = e["state_vars"].get("activity", 0)
            rate = self.env.biome.decomposition_rate * activity * dt
            effects.append(SoilDeposit(
                tick=self.tick,
                entity_id=e["id"],
                position=e["position"],
                layer="organic_matter",
                amount=-rate,
            ))
            # Decomposers mineralize OM into the slow nutrient pool
            effects.append(SoilDeposit(
                tick=self.tick,
                entity_id=e["id"],
                position=e["position"],
                layer="nutrients_slow",
                amount=rate * DECOMP_NUTRIENT_EFFICIENCY,
            ))

    def apply_rain(self, intensity: float = 0.5) -> None:
        """Apply a rain event across the entire grid.

        Boosts soil moisture and nutrients, refills water sources,
        hydrates plants and animals, and suppresses evaporation.
        Triggered via WebSocket control message or programmatic API.
        """
        # Delegate core effects to EnvironmentManager
        rate_multipliers = {
            "consumption": self.rate_consumption,
            "hunger": self.rate_hunger,
            "thirst": self.rate_thirst,
            "growth": self.rate_growth,
            "reproduction": self.rate_reproduction,
            "water_replenishment": self.rate_water_replenish,
        }
        event = self.env.apply_rain(
            intensity=intensity,
            entities=self.entities,
            get_params_fn=self._get_params,
            rate_multipliers=rate_multipliers,
        )

        # Engine-level bookkeeping (tick counters, event log)
        self._rain_ticks_remaining = RAIN_SUPPRESSION_TICKS
        # Only set if not already active — prevents spam-clicking rain
        # from extending the recovery window indefinitely.
        if self._recent_rain_recovery_ticks <= 0:
            self._recent_rain_recovery_ticks = RAIN_REPRO_RECOVERY_TICKS
        self._events.append({"type": "RAIN", "tick": self.tick, **event})

    # ═══════════════════════════════════════════════════════════════════════
    # Client Agency Reconciliation — Absorb client-reported state
    # ═══════════════════════════════════════════════════════════════════════

    def absorb_client_positions(
        self, positions: dict[str, list[float]], divergence_multiplier: float = 2.5,
    ) -> None:
        """Absorb client-reported entity positions into server state.

        For each entity, compare reported position against simulated position.
        If divergence < threshold (species speed × dt_server_tick × multiplier):
            soft-nudge internal position toward reported over a few ticks.
        If divergence > threshold:
            snap to reported and mark for _ack on next packet.

        The server never abandons its goals — state vars, discrete states,
        and ecological relationships remain authoritative. Only positions shift.

        Args:
            positions: Mapping of entity_id → [x, y, z] from client heartbeat.
            divergence_multiplier: How many "expected travel distances" before snap.
                Default 2.5 means an entity can deviate up to 2.5× its max
                travel distance before the server snaps to client truth.
        """
        import math as _math

        for eid, reported_pos in positions.items():
            entity = self.entities.get(eid)
            if entity is None or not is_alive(entity):
                continue  # Entity may have been removed server-side

            params = self._get_params(entity)
            sim_pos = entity["position"]

            dx = reported_pos[0] - sim_pos[0]
            dz = reported_pos[2] - sim_pos[2]
            divergence = _math.sqrt(dx * dx + dz * dz)

            if divergence < 0.1:
                continue  # Negligible drift — no action needed

            # Compute threshold from species speed × effective tick interval.
            # At 0.5 Hz server tick, dt ≈ 2.0s. Speed is in world units/sec.
            if params and hasattr(params, "speed"):
                max_travel = params.speed * (1.0 / self._effective_hz()) * divergence_multiplier
            else:
                max_travel = 10.0 * divergence_multiplier  # generous default for sessile

            if divergence <= max_travel:
                # Soft nudge — move server position partway toward client.
                # This keeps the two simulations loosely coupled without snapping.
                nudge_factor = 0.3  # absorb 30% of deviation per heartbeat
                sim_pos[0] += dx * nudge_factor
                sim_pos[2] += dz * nudge_factor
            else:
                # Snap — client deviated significantly. Absorb its position
                # and acknowledge on next packet so the client knows we heard it.
                sim_pos[0] = reported_pos[0]
                sim_pos[2] = reported_pos[2]
                self._pending_acks.add(eid)

    def absorb_client_events(self, events: list[dict[str, Any]]) -> None:
        """Absorb client-reported interaction and lifecycle events.

        The client may report reproduction, consumption, predation,
        or pollination events that occurred through its local agency.
        The server validates basic sanity (entities exist, are alive)
        and applies the ecological effects. Detailed proximity checks
        are skipped — the client's perception is trusted within bounds.

        Event types:
            - "repro": Client-side reproduction. Spawns offspring,
              applies parent costs. Rate-capped per species.
            - "consumption": Herbivory event. Applies hunger relief
              to consumer, growth/health damage to plant.
            - "predation": Predation event. Removes prey, feeds predator,
              deposits organic matter at kill site. Rate-capped.
            - "pollination": Pollinator visited a flower. Boosts plant
              health, relieves pollinator hunger/hydration.

        Args:
            events: List of event dicts from client heartbeat.
        """
        import random as _random

        for ev in events:
            etype = ev.get("type", "")

            if etype == "repro":
                self._absorb_reproduction(ev)
            elif etype == "consumption":
                self._absorb_consumption(ev)
            elif etype == "predation":
                self._absorb_predation(ev)
            elif etype == "pollination":
                self._absorb_pollination(ev)

    def _absorb_reproduction(self, ev: dict[str, Any]) -> None:
        """Absorb a client-reported reproduction event."""
        parent_id = ev.get("parent_id", "")
        parent = self.entities.get(parent_id)
        if not parent or not is_alive(parent):
            return

        params = self._get_params(parent)
        if params is None:
            return

        # Sanity: parent must have high reproductive drive (server-side truth)
        sv = parent["state_vars"]
        if sv.get("reproductive_drive", 0) <= params.repro_drive_threshold:
            return  # Server doesn't think parent is ready — defer

        # Rate cap: track repro events per species to prevent runaway spawning
        species_id = parent.get("species", "unknown")
        recent_repros = sum(
            1 for e in self._events
            if e.get("type") == "REPRODUCTION"
            and e.get("source_id", "").startswith(species_id)
            and abs(e.get("tick", 0) - self.tick) < 50
        )
        max_repros = max(1, params.clutch_size * 3) if params.clutch_size else 3
        if recent_repros >= max_repros:
            return  # Rate capped — try again later

        # Apply parent costs
        sv["reproductive_drive"] = 0.0
        sv["energy"] = max(0.0, sv.get("energy", 1.0) - params.parent_energy_cost)

        # Spawn offspring at client-reported position
        pos = ev.get("client_position", list(parent["position"]))
        clutch_size = ev.get("offspring_count", params.clutch_size or 1)
        meta = parent.get("metadata", {})

        for i in range(clutch_size):
            new_x = pos[0] + _random.uniform(-1.0, 1.0)
            new_z = pos[2] + _random.uniform(-1.0, 1.0)
            child_id = f"{parent_id}_child_{self.tick}_{_random.randint(0, 999)}"

            offspring_sv: dict[str, float] = {
                "hunger": sv.get("hunger", 0.5) * 0.6,
                "energy": max(0.3, sv.get("energy", 1.0) * 0.8),
                "hydration": sv.get("hydration", 1.0),
                "health": max(0.3, sv.get("health", 1.0) * 0.9),
                "reproductive_drive": 0.0,
                "age": 0.0,
            }

            self._spawns.append({
                "id": child_id,
                "type": parent["type"],
                "species": parent.get("species"),
                "position": [new_x, pos[1] or 0.0, new_z],
                "metadata": dict(meta),
                "state_vars": offspring_sv,
                "skeleton_id": parent.get("skeleton_id"),
                "sex": _random.choice(("male", "female")),
                "initial_attrs": {},
            })

        self._events.append({
            "type": "REPRODUCTION",
            "tick": self.tick,
            "source_id": parent_id,
            "target_id": child_id if clutch_size == 1 else None,
            "position": pos,
            "client_reported": True,
        })

    def _absorb_consumption(self, ev: dict[str, Any]) -> None:
        """Absorb a client-reported herbivory event."""
        source_id = ev.get("source_id", "")
        target_id = ev.get("target_id", "")

        consumer = self.entities.get(source_id)
        plant = self.entities.get(target_id)
        if not consumer or not plant:
            return
        if not is_alive(consumer) or not is_alive(plant):
            return

        params = self._get_params(consumer)
        if params is None:
            return

        # Apply hunger relief to consumer
        sv = consumer["state_vars"]
        sv["hunger"] = max(0.0, sv.get("hunger", 0) - params.herbivory_relief)

        # Apply growth/health damage to plant
        psv = plant["state_vars"]
        rate = self.rate_consumption
        psv["growth"] = max(
            0.0, psv.get("growth", 1.0) - params.consumption_damage_growth * rate,
        )
        psv["health"] = max(
            0.0, psv.get("health", 1.0) - params.consumption_damage_health * rate,
        )

        self._events.append({
            "type": "CONSUMPTION",
            "tick": self.tick,
            "source_id": source_id,
            "target_id": target_id,
            "position": ev.get("position", list(plant["position"])),
            "client_reported": True,
        })

    def _absorb_predation(self, ev: dict[str, Any]) -> None:
        """Absorb a client-reported predation event."""
        source_id = ev.get("source_id", "")
        target_id = ev.get("target_id", "")

        predator = self.entities.get(source_id)
        prey = self.entities.get(target_id)
        if not predator or not prey:
            return
        if not is_alive(predator) or not is_alive(prey):
            return

        params = self._get_params(predator)
        if params is None:
            return

        # Rate cap: max kills per predator per N ticks
        recent_kills = sum(
            1 for e in self._events
            if e.get("type") == "PREDATION"
            and e.get("source_id") == source_id
            and abs(e.get("tick", 0) - self.tick) < 100
        )
        if recent_kills >= 3:
            return  # Rate capped

        # Predator gains
        psv = predator["state_vars"]
        psv["hunger"] = max(0.0, psv.get("hunger", 0) - params.predation_relief)
        psv["energy"] = min(1.0, psv.get("energy", 0) + params.predation_energy_gain)

        # Prey removed
        kill_pos = ev.get("kill_position", list(prey["position"]))
        self._removals.append(target_id)

        # Deposit organic matter at kill site
        deposit_amount = min(
            0.3, (params.metabolic_rate * 0.1 if hasattr(params, "metabolic_rate") else 0.05),
        )
        gx, gy, gz = self.env.voxels.world_to_grid(*kill_pos)
        self.env.voxels.add("organic_matter", gx, gy, gz, deposit_amount)

        self._events.append({
            "type": "PREDATION",
            "tick": self.tick,
            "source_id": source_id,
            "target_id": target_id,
            "position": kill_pos,
            "client_reported": True,
        })

    def _absorb_pollination(self, ev: dict[str, Any]) -> None:
        """Absorb a client-reported pollination event."""
        source_id = ev.get("source_id", "")
        target_id = ev.get("target_id", "")

        pollinator = self.entities.get(source_id)
        plant = self.entities.get(target_id)
        if not pollinator or not plant:
            return
        if not is_alive(pollinator) or not is_alive(plant):
            return

        params = self._get_params(pollinator)
        if params is None or not getattr(params, "floral_affinity", False):
            return

        # Plant health boost
        psv = plant["state_vars"]
        psv["health"] = min(1.0, psv.get("health", 0) + 0.05)

        # Pollinator hunger/hydration relief
        psv2 = pollinator["state_vars"]
        relief = getattr(params, "pollination_relief", 0.08)
        psv2["hunger"] = max(0.0, psv2.get("hunger", 0) - relief)
        if "hydration" in psv2:
            psv2["hydration"] = min(1.0, psv2.get("hydration", 0) + relief * 0.5)

        self._events.append({
            "type": "POLLINATION",
            "tick": self.tick,
            "source_id": source_id,
            "target_id": target_id,
            "position": ev.get("position", list(plant["position"])),
            "client_reported": True,
        })

    def get_species_definitions(self) -> dict[str, Any]:
        """Build lightweight species reference for client-side agency.

        Derived from CompiledEcology at server init. Gives the client
        everything it needs for local target selection without exposing
        full simulation internals (allometric scaling, interaction matrix,
        guard thresholds).

        Returns:
            Dict mapping species_id → { type, diet_order, flee_targets,
                is_pollinator, pollination_targets, has_roost_affinity,
                mating_radius }.
        """
        result: dict[str, Any] = {}

        for species_id, params in self.compiled.derived_params.items():
            entry: dict[str, Any] = {
                "type": params.entity_class,
                "movement_speed": round(params.speed, 4),
                "diet_order": [],
                "flee_targets": [],
                "is_pollinator": bool(getattr(params, "floral_affinity", False)),
                "pollination_targets": [],
                "has_roost_affinity": bool(getattr(params, "roost_affinity", False)),
                "mating_radius": params.sensory_range,
            }

            # Diet order: list of [species_id, preference_rank]
            diet_order = self.compiled.get_diet_order(species_id)
            if diet_order:
                entry["diet_order"] = [[s, p] for s, p in diet_order]

            # Flee targets from interaction matrix
            flee_targets = self.compiled.get_flee_targets(species_id)
            if flee_targets:
                entry["flee_targets"] = list(flee_targets)

            # Pollination targets (species this pollinator can visit)
            if entry["is_pollinator"]:
                for other_species in self.compiled.derived_params:
                    interactions = self.compiled.get_interactions(
                        species_id, other_species,
                    )
                    if interactions and any(
                        ix.interaction_type == "pollination"
                        for ix in interactions
                    ):
                        entry["pollination_targets"].append(other_species)

            result[species_id] = entry

        return result

    def _effective_hz(self) -> float:
        """Return effective server tick rate in Hz.

        Used by absorption to compute expected travel distance thresholds.
        Defaults to 0.5 (2-second ticks) for intent-based mode.
        """
        return 0.5

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
            ctx = build_context(spec, entity, self.env.biome, self.env.climate)
            contexts.append(ctx)
        latents = adapter.infer(contexts)
        for entity, latent in zip(skeleton_entities, latents):
            entity["motion_latent"] = latent

    # ═══════════════════════════════════════════════════════════════════════
    # Tick Packet Assembly
    # ═══════════════════════════════════════════════════════════════════════

    def _build_tick_packet(self, dt: float) -> dict[str, Any]:
        """Build intent-based tick packet for client agency.

        Unlike the old position-authoritative format, this packet sends:
        - **State** (discrete state machine value)
        - **Drives** (continuous desire variables: hunger, thirst, energy,
          reproductive_drive) — these are *intent*, not commands
        - **Motion latent** (4D vector from motor model encoding how the
          entity should feel moving right now)
        - **Reference position** (where server expects entity to be — used
          for reconciliation gravity, not as authoritative command)
        - **Eligibility flags** (_can_consume, _can_predate, etc.) —
          permission gates derived from server-side state checks
        - **_ack** flag if server absorbed client deviation this tick

        The client uses drives + latent + eligibility to generate local
        movement and interaction behavior. It reports outcomes back via
        heartbeat messages.
        """
        packet: dict[str, Any] = {"tick": self.tick, "dt": dt}
        updates = []
        for e in self.entities.values():
            if not is_alive(e):
                continue  # Don't send dead/dying entities

            eid = e["id"]
            sv = e["state_vars"]
            params = self._get_params(e)

            update: dict[str, Any] = {
                "id": eid,
                "state": e["state"],
                # Reference position — gravity well for reconciliation,
                # not an authoritative command. Client may deviate.
                "ref_position": [round(v, 4) for v in e["position"]],
                # Drives — continuous desire variables that encode intent
                "drive": {
                    "hunger": round(sv.get("hunger", 0.0), 4),
                    "energy": round(sv.get("energy", 1.0), 4),
                    "hydration": round(sv.get("hydration", 1.0), 4),
                    "health": round(sv.get("health", 1.0), 4),
                },
            }

            # Include reproductive drive for mobile consumers
            if params and params.diet_type not in ("autotroph", "decomposer"):
                update["drive"]["reproductive_drive"] = round(
                    sv.get("reproductive_drive", 0.0), 4,
                )

            # Include plant-specific state vars for producers
            if params and params.diet_type == "autotroph":
                update["drive"]["growth"] = round(sv.get("growth", 0.0), 4)
                update["drive"]["nutrient_store"] = round(
                    sv.get("nutrient_store", 0.0), 4,
                )

            # Motion latent — 4D vector encoding movement disposition
            if e.get("skeleton_id") or (params and params.speed > 0):
                update["motion_latent"] = e.get(
                    "motion_latent", [0.0, 0.0, 0.0, 0.0],
                )

            # Eligibility flags — server-side permission gates.
            # These tell the client what interactions are *possible* given
            # current state vars and discrete state. The client decides
            # whether conditions are met locally (proximity, etc.).
            if params:
                diet = params.diet_type
                state = e["state"]

                # Herbivory: FORAGING consumer with sufficient hunger
                if diet in ("herbivore", "omnivore"):
                    update["_can_consume"] = (
                        state == "FORAGING"
                        and sv.get("hunger", 0) > 0.2
                    )

                # Predation: HUNTING carnivore/insectivore with hunger
                if diet in ("carnivore", "insectivore"):
                    update["_can_predate"] = (
                        state == "HUNTING"
                        and sv.get("hunger", 0) > 0.3
                    )
                elif diet == "omnivore":
                    # Omnivores can opportunistically predate while FORAGING
                    update["_can_predate"] = state in ("HUNTING", "FORAGING")

                # Pollination: pollinator not lingering, not on cooldown
                if getattr(params, "floral_affinity", False):
                    update["_can_pollinate"] = (
                        e.get("_linger", 0) <= 0
                        and e.get("_pollination_cooldown", 0) <= 0
                        and state != "WANDERING"
                    )

                # Reproduction: female with drive above threshold
                if diet not in ("autotroph", "decomposer"):
                    update["_repro_eligible"] = (
                        e.get("sex") == "female"
                        and sv.get("reproductive_drive", 0) > params.repro_drive_threshold
                        and state not in ("DYING", "REPRODUCING", "SWARMING")
                    )

                # Drinking: entity can drink when near water
                if diet not in ("autotroph", "decomposer"):
                    update["_can_drink"] = (
                        sv.get("hydration", 1.0) < 0.7
                        or state == "DRINKING"
                    )

            # Plant spreading eligibility
            if params and params.diet_type == "autotroph":
                update["_spread_eligible"] = (
                    sv.get("health", 1.0) > 0.5
                    and sv.get("hydration", 1.0) > 0.3
                    and sv.get("growth", 0.0) > 0.4
                    and e.get("_spread_cooldown", 0) <= 0
                )

            # Acknowledgment flag — server absorbed client deviation
            if eid in self._pending_acks:
                update["_ack"] = True

            updates.append(update)

        packet["entity_updates"] = updates

        # Clear pending acks after including them in this packet
        self._pending_acks.clear()

        # Spawns — new entities entering the simulation.
        # Client renders these immediately; they start with full intent data.
        if self._spawns:
            packet["entity_spawns"] = [
                {
                    "id": s["id"],
                    "type": s["type"],
                    "species": s.get("species"),
                    "ref_position": [round(v, 4) for v in s["position"]],
                    "skeleton_id": s.get("skeleton_id"),
                    "state": s["state"],
                    "drive": {k: round(v, 4) for k, v in s["state_vars"].items()},
                    "motion_latent": [0.0, 0.0, 0.0, 0.0],
                }
                for s in self._spawns
            ]

        if self._removals:
            packet["entity_removals"] = list(self._removals)
        if self._events:
            packet["events"] = self._events

        # Voxel deltas — moisture layer for client heatmap rendering.
        # Client doesn't need full 5-layer voxel data; just moisture for visuals.
        voxel_packet = self.env.voxels.get_delta_packet()
        if voxel_packet:
            packet["voxel_deltas"] = voxel_packet

        if self.env.water_sources:
            packet["water_sources"] = [
                {"position": ws["position"], "radius": ws["radius"],
                 "water_level": ws["water_level"]}
                for ws in self.env.water_sources
            ]

        return packet
