# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Guard Actors — Discrete state transitions as effect-emitting actors

Each guard actor implements resolve(ctx) → list[Effect] for discrete
state machine transitions (death, dormancy, wilting, etc.). The engine
collects effects and applies them via the EffectBus.

See Also:
- ``effects.py`` — Effect dataclasses + EffectBus
- ``actors/flow_actors.py`` — Continuous state evolution actors
"""

from __future__ import annotations

import math
from typing import Any

from ..effects import (
    DepositOrganicMatter,
    EventRecord,
    RemoveEntity,
    SetStateVar,
    SpawnEntity,
    StateTransition,
)
from ..entities import is_alive
from ..traits import DerivedParams


# ── Guard constants ────────────────────────────────────────────────────────
CARNIVORE_HUNT_HUNGER = 0.5     # hunger above this → HUNTING instead of FORAGING
DORMANCY_RECOVERY_EXIT_HEALTH = 0.2  # health above this exits dormancy

# Organic matter deposit on death
OM_DEPOSIT_SCALE = 0.15         # body mass → organic matter conversion
OM_DEPOSIT_MIN = 0.002          # minimum deposit for any entity
OM_DEPOSIT_MAX = 0.5            # maximum deposit per cell


class ConsumerGuardActor:
    """Guard evaluation for consumers (animals, birds, insects).

    State machine priority (highest to lowest):
    1. Death (health ≤ 0 or age ≥ lifespan)
    2. Colony swarming (colony_health < 0.3)
    3. Fleeing (set by interaction resolver, cleared when target reached)
    4. Reproduction (drive > threshold AND mate available)
    5. Drinking (hydration hysteresis: enter < p.hydration_enter, exit ≥ p.hydration_exit)
    6. Resting (energy hysteresis: enter < p.energy_enter, exit ≥ p.energy_exit)
    7. Foraging/Hunting (hunger hysteresis: enter ≥ p.hunger_enter, exit < p.hunger_exit)
    8. Idle (default)

    Returns StateTransition effects for each state change detected.
    Death triggers RemoveEntity + EventRecord.
    Reproduction triggers SpawnEntity (offspring) + EventRecord.
    """

    def resolve(self, ctx: Any) -> list[Any]:
        """Evaluate consumer guards for one entity this tick.

        Args:
            ctx: InteractionContext with params, voxel_grid, biome, entities,
                 and rate_multipliers.

        Returns:
            List of Effect objects describing state changes.
        """
        p = ctx.params
        if p is None:
            return []

        sv = ctx.entity["state_vars"]
        old_state = ctx.entity["state"]
        meta = ctx.entity["metadata"]
        effects: list[Any] = []

        # ── Death ──
        if p.generation_time_ticks > 0:
            lifespan = p.generation_time_ticks
        else:
            lifespan = meta.get("lifespan", 1000.0)

        health_key = "colony_health" if "colony_health" in sv else "health"
        # Build OM deposit data for death handling
        params_dict = None
        if p is not None:
            params_dict = {
                "metabolic_rate": p.metabolic_rate,
            }
        om_effect = DepositOrganicMatter(
            entity_id=ctx.entity["id"],
            type=ctx.entity["type"],
            species=ctx.entity.get("species"),
            position=list(ctx.entity["position"]),
            metadata=dict(ctx.entity["metadata"]),
            params=params_dict,
            tick=ctx.tick,
        )

        if sv.get(health_key, 1.0) <= 0.0:
            effects.extend([
                StateTransition(entity_id=ctx.entity["id"], new_state="DYING", tick=ctx.tick),
                RemoveEntity(entity_id=ctx.entity["id"], tick=ctx.tick),
                om_effect,
                EventRecord(event_type="DEATH_STARVE", source_id=ctx.entity["id"],
                            target_id=None, position=list(ctx.entity["position"]),
                            tick=ctx.tick),
            ])
            return effects

        elif sv["age"] >= lifespan:
            effects.extend([
                StateTransition(entity_id=ctx.entity["id"], new_state="DYING", tick=ctx.tick),
                RemoveEntity(entity_id=ctx.entity["id"], tick=ctx.tick),
                om_effect,
                EventRecord(event_type="DEATH_NATURAL", source_id=ctx.entity["id"],
                            target_id=None, position=list(ctx.entity["position"]),
                            tick=ctx.tick),
            ])
            return effects

        # ── Colony swarming ──
        if "colony_health" in sv and sv["colony_health"] < 0.3:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="SWARMING", tick=ctx.tick,
            ))

        # ── Fleeing (managed by interaction resolver) ──
        elif ctx.entity["state"] == "FLEEING":
            if ctx.entity.get("_target") is None:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))

        # ── Drinking (hysteresis) ──
        elif ctx.entity["state"] == "DRINKING":
            if sv.get("hydration", 1.0) >= p.hydration_exit:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
        elif sv.get("hydration", 1.0) < p.hydration_enter:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="DRINKING", tick=ctx.tick,
            ))

        # ── Resting (hysteresis) ──
        elif ctx.entity["state"] == "RESTING":
            if sv["energy"] >= p.energy_exit:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
        elif sv["energy"] < p.energy_enter:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="RESTING", tick=ctx.tick,
            ))

        # ── Foraging / Hunting (hysteresis) ──
        elif ctx.entity["state"] in ("FORAGING", "HUNTING"):
            if sv["hunger"] < p.hunger_exit:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
            elif p.diet_type in ("carnivore", "insectivore") and sv["hunger"] > CARNIVORE_HUNT_HUNGER:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="HUNTING", tick=ctx.tick,
                ))
        elif sv["hunger"] >= p.hunger_enter:
            if p.diet_type in ("carnivore", "insectivore") and sv["hunger"] > CARNIVORE_HUNT_HUNGER:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="HUNTING", tick=ctx.tick,
                ))
            else:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="FORAGING", tick=ctx.tick,
                ))

        # ── Default ──
        else:
            if ctx.entity["state"] not in ("FORAGING", "HUNTING", "FLEEING", "DRINKING",
                                           "RESTING", "REPRODUCING", "SWARMING"):
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))

        # ── Reproduction (checked independently, can interrupt any state) ──
        if self._should_reproduce(ctx):
            effects.extend(self._reproduction_effects(ctx))

        return effects

    def _should_reproduce(self, ctx: Any) -> bool:
        """Check if entity should reproduce this tick."""
        sv = ctx.entity["state_vars"]
        p = ctx.params
        if sv.get("reproductive_drive", 0) <= p.repro_drive_threshold:
            return False
        if ctx.entity["state"] in ("DYING", "REPRODUCING", "SWARMING"):
            return False

        # Mate search — iterate over all entities for proximity check
        for other in ctx._entities.values():
            if not is_alive(other):
                continue
            if other.get("species") != ctx.entity.get("species"):
                continue
            dx = other["position"][0] - ctx.entity["position"][0]
            dz = other["position"][2] - ctx.entity["position"][2]
            dist = math.sqrt(dx * dx + dz * dz)
            if dist <= p.sensory_range:
                return True
        return False

    def _reproduction_effects(self, ctx: Any) -> list[Any]:
        """Generate reproduction effects (offspring spawn + event)."""
        p = ctx.params
        sv = ctx.entity["state_vars"]
        meta = ctx.entity["metadata"]
        pos = ctx.entity["position"]

        # Offspring position — near parent with small offset
        import random as _random
        angle = _random.uniform(0, 2 * math.pi)
        offset = p.sensory_range * 0.3 * _random.uniform(0.5, 1.0)
        new_x = pos[0] + math.cos(angle) * offset
        new_z = pos[2] + math.sin(angle) * offset

        # Offspring inherits parent state with reduced values (generational decline)
        offspring_sv = {
            "hunger": sv.get("hunger", 0.5) * 0.3,
            "energy": min(1.0, sv.get("energy", 1.0) * 0.9),
            "hydration": sv.get("hydration", 1.0),
            "health": sv.get("health", 1.0),
            "reproductive_drive": 0.0,
            "age": 0.0,
        }

        if "colony_health" in sv:
            offspring_sv["colony_health"] = sv["colony_health"] * 0.9

        # Reproduction cost to parent colony health (insects)
        parent_effects: list[Any] = []
        if "colony_health" in sv and p.diet_type == "insect":
            colony_cost = min(sv["colony_health"], 0.3)
            parent_effects.append(SetStateVar(
                entity_id=ctx.entity["id"], var_name="colony_health",
                value=max(0.0, sv["colony_health"] - colony_cost), tick=ctx.tick,
            ))

        # Offspring state vars depend on type
        if ctx.entity.get("type") == "INSECT":
            offspring_sv.setdefault("activity", 1.0)
            offspring_sv.setdefault("population", 1.0)

        effects = parent_effects + [
            StateTransition(entity_id=ctx.entity["id"], new_state="REPRODUCING", tick=ctx.tick),
            SpawnEntity(
                entity_id=f"{ctx.entity['species']}_child_{ctx.tick}_{_random.randint(0, 999)}",
                type=ctx.entity["type"],
                species=ctx.entity.get("species"),
                position=[new_x, pos[1], new_z],
                metadata=dict(meta),
                state_vars=offspring_sv,
            ),
            EventRecord(event_type="REPRODUCTION", source_id=ctx.entity["id"],
                        target_id=None, position=list(pos), tick=ctx.tick),
        ]

        return effects


class ProducerGuardActor:
    """Guard evaluation for autotroph sessile entities (plants, trees).

    State machine:
    - health ≤ 0 + root_persistence → DORMANT (roots survive)
    - health ≤ 0 + no persistence → DEAD
    - DORMANT + soil recovery → GROWING (if health rebuilds past threshold)
    - DORMANT + timeout → DEAD (roots die after too long)
    - low hydration or nutrients → WILTING
    - high growth + good health → FRUITING (available for pollination)
    - otherwise → GROWING
    """

    def resolve(self, ctx: Any) -> list[Any]:
        """Evaluate producer guards for one entity this tick.

        Args:
            ctx: InteractionContext with params, voxel_grid, biome.

        Returns:
            List of Effect objects describing state changes.
        """
        p = ctx.params
        if p is None:
            return []

        sv = ctx.entity["state_vars"]
        old_state = ctx.entity["state"]
        effects: list[Any] = []

        # ── Death (health ≤ 0) ──
        params_dict = None
        if p is not None:
            params_dict = {
                "metabolic_rate": p.metabolic_rate,
            }
        om_effect = DepositOrganicMatter(
            entity_id=ctx.entity["id"],
            type=ctx.entity["type"],
            species=ctx.entity.get("species"),
            position=list(ctx.entity["position"]),
            metadata=dict(ctx.entity["metadata"]),
            params=params_dict,
            tick=ctx.tick,
        )

        if sv["health"] <= 0.0:
            if p.root_persistence:
                if ctx.entity["state"] != "DORMANT":
                    effects.append(StateTransition(
                        entity_id=ctx.entity["id"], new_state="DORMANT", tick=ctx.tick,
                    ))
                    effects.append(SetStateVar(
                        entity_id=ctx.entity["id"], var_name="growth",
                        value=0.0, tick=ctx.tick,
                    ))
                    effects.append(SetStateVar(
                        entity_id=ctx.entity["id"], var_name="_dormant_ticks",
                        value=0.0, tick=ctx.tick,
                    ))
            else:
                effects.extend([
                    StateTransition(entity_id=ctx.entity["id"], new_state="DEAD", tick=ctx.tick),
                    RemoveEntity(entity_id=ctx.entity["id"], tick=ctx.tick),
                    om_effect,
                    EventRecord(event_type="DEATH_NATURAL", source_id=ctx.entity["id"],
                                target_id=None, position=list(ctx.entity["position"]),
                                tick=ctx.tick),
                ])
                return effects

        # ── Dormant state ──
        elif ctx.entity["state"] == "DORMANT":
            gx, gy, gz = ctx.voxel_grid.world_to_grid(*ctx.entity["position"])
            soil_moisture = ctx.voxel_grid.get("moisture", gx, gy, gz)
            soil_nutrients = ctx.voxel_grid.get("nutrients", gx, gy, gz)

            # Increment dormant ticks counter
            current_dormant_ticks = sv.get("_dormant_ticks", 0) + 1
            effects.append(SetStateVar(
                entity_id=ctx.entity["id"], var_name="_dormant_ticks",
                value=float(current_dormant_ticks), tick=ctx.tick,
            ))

            # Recovery check
            if (soil_moisture > p.dormancy_recovery_moisture
                    and soil_nutrients > p.dormancy_recovery_nutrients):
                recovery_health = max(0.015, p.health_drain_dehydrated * 10.0)
                recovery_hydration = max(0.02, p.health_drain_dehydrated * 13.0)
                effects.append(SetStateVar(
                    entity_id=ctx.entity["id"], var_name="health",
                    value=min(1.0, sv["health"] + recovery_health), tick=ctx.tick,
                ))
                effects.append(SetStateVar(
                    entity_id=ctx.entity["id"], var_name="hydration",
                    value=min(1.0, sv.get("hydration", 0.0) + recovery_hydration), tick=ctx.tick,
                ))
                if sv["health"] + recovery_health > DORMANCY_RECOVERY_EXIT_HEALTH:
                    effects.append(StateTransition(
                        entity_id=ctx.entity["id"], new_state="GROWING", tick=ctx.tick,
                    ))
                    effects.append(SetStateVar(
                        entity_id=ctx.entity["id"], var_name="growth",
                        value=0.05, tick=ctx.tick,
                    ))
                    effects.append(SetStateVar(
                        entity_id=ctx.entity["id"], var_name="nutrient_store",
                        value=max(sv.get("nutrient_store", 0.0), 0.2), tick=ctx.tick,
                    ))
                    effects.append(SetStateVar(
                        entity_id=ctx.entity["id"], var_name="_dormant_ticks",
                        value=0.0, tick=ctx.tick,
                    ))

            # Dormancy timeout → permanent death
            elif current_dormant_ticks > p.dormancy_timeout:
                effects.extend([
                    StateTransition(entity_id=ctx.entity["id"], new_state="DEAD", tick=ctx.tick),
                    RemoveEntity(entity_id=ctx.entity["id"], tick=ctx.tick),
                    EventRecord(event_type="DEATH_NATURAL", source_id=ctx.entity["id"],
                                target_id=None, position=list(ctx.entity["position"]),
                                tick=ctx.tick),
                ])

        # ── Wilting (low hydration or nutrients) ──
        elif sv["hydration"] <= p.wilting_hydration or sv["nutrient_store"] <= p.wilting_nutrients:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="WILTING", tick=ctx.tick,
            ))

        # ── Fruiting (high growth + good health) ──
        elif sv["growth"] >= p.fruiting_growth and sv["health"] > p.fruiting_health:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="FRUITING", tick=ctx.tick,
            ))

        # ── Growing (default) ──
        else:
            if ctx.entity["state"] not in ("GROWING", "WILTING", "FRUITING"):
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="GROWING", tick=ctx.tick,
                ))

        return effects


class DecomposerGuardActor:
    """Guard evaluation for decomposer entities (fungi, microorganisms).

    State machine:
    - high organic matter + high population → BLOOMING
    - low activity → DORMANT
    - otherwise → ACTIVE
    """

    def resolve(self, ctx: Any) -> list[Any]:
        """Evaluate decomposer guards for one entity this tick.

        Args:
            ctx: InteractionContext with params, voxel_grid, biome.

        Returns:
            List of Effect objects describing state changes.
        """
        p = ctx.params
        if p is None:
            return []

        sv = ctx.entity["state_vars"]
        effects: list[Any] = []

        gx, gy, gz = ctx.voxel_grid.world_to_grid(*ctx.entity["position"])
        organic = ctx.voxel_grid.get("organic_matter", gx, gy, gz)

        if organic > 0.8 and sv["population"] > 0.7:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="BLOOMING", tick=ctx.tick,
            ))
        elif sv["activity"] < 0.2:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="DORMANT", tick=ctx.tick,
            ))
        else:
            if ctx.entity["state"] not in ("ACTIVE", "BLOOMING", "DORMANT"):
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="ACTIVE", tick=ctx.tick,
                ))

        return effects
