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

from ..constants import (
    CARNIVORE_HUNT_HUNGER,
    CHILD_COLONY_FLOOR,
    CHILD_COLONY_INHERIT,
    CHILD_ENERGY_FLOOR,
    CHILD_ENERGY_INHERIT,
    CHILD_HEALTH_FLOOR,
    CHILD_HEALTH_INHERIT,
    CHILD_HUNGER_INHERIT,
    DEHYDRATION_HYDRATION,
    DORMANCY_RECOVERY_EXIT_HEALTH,
    OM_DEPOSIT_MAX,
    OM_DEPOSIT_MIN,
    OM_DEPOSIT_SCALE,
    POLLINATOR_POST_VISIT_COOLDOWN,
    POLLINATOR_VISIT_LIMIT,
    POLLINATOR_WANDER_COOLDOWN,
    SPAWN_OFFSET,
)
from ..effects import (
    DepositOrganicMatter,
    EventRecord,
    RemoveEntity,
    SetEntityAttr,
    SetStateVar,
    SpawnEntity,
    StateTransition,
    StateVarDelta,
)
from ..entities import is_alive
from ..traits import DerivedParams


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

        # ── Dormant consumer recovery (rain-triggered wake-up) ──
        # Plants have explicit dormancy recovery in ProducerGuardActor that checks
        # soil moisture/nutrients. Consumers lacked this — they relied on slow
        # hunger accumulation alone to exit DORMANT, which could take hundreds of
        # ticks at low metabolic rates (e.g. butterflies: ~344 ticks from 0→0.27).
        # After rain, soil moisture is high everywhere, so we use it as a proxy for
        # "conditions have improved" and wake dormant consumers to IDLE immediately.
        if ctx.entity["state"] == "DORMANT":
            gx, gy, gz = ctx.voxel_grid.world_to_grid(*ctx.entity["position"])
            soil_moisture = ctx.voxel_grid.get("moisture", gx, gy, gz)
            # Use same moisture threshold as plant dormancy recovery for consistency
            if soil_moisture > 0.25:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
                # Skip further guard checks — just wake up this tick
                return effects

        # ── Colony swarming (highest behavioral priority) ──
        if "colony_health" in sv:
            if ctx.entity["state"] == "SWARMING":
                # Exit SWARMING when colony recovers above threshold.
                # The near-water bonus (WATER_PROXIMITY_COLONY_FACTOR) helps
                # rebuild colony_health once the insect reaches a water source,
                # allowing it to return to normal behavior.
                if sv["colony_health"] >= 0.35:
                    effects.append(StateTransition(
                        entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                    ))
            elif sv["colony_health"] < 0.3:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="SWARMING", tick=ctx.tick,
                ))

        # ── Pollinator forced WANDERING cooldown ──
        # When a pollinator has visited too many flowers, it is forced into
        # WANDERING for POLLINATOR_WANDER_COOLDOWN ticks to explore new areas.
        # During this time, decrement the countdown. When it reaches 0, reset
        # the visit counter and let normal guard logic decide next state.
        if p.floral_affinity and ctx.entity["state"] == "WANDERING":
            cooldown = ctx.entity.get("_wander_cooldown", 0)
            if cooldown > 0:
                new_cooldown = max(0, cooldown - 1)
                effects.append(SetEntityAttr(
                    entity_id=ctx.entity["id"], attr_name="_wander_cooldown",
                    value=float(new_cooldown), tick=ctx.tick,
                ))
                if new_cooldown == 0:
                    # Cooldown expired — reset visit counter and transition
                    # back to IDLE so normal guard logic (hunger-driven)
                    # can decide the next state.
                    effects.append(SetEntityAttr(
                        entity_id=ctx.entity["id"], attr_name="_pollination_visits",
                        value=0.0, tick=ctx.tick,
                    ))
                    effects.append(StateTransition(
                        entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                    ))
            elif cooldown <= 0:
                # Safety: pollinator in WANDERING with no active cooldown.
                # This can happen if the FORAGING→WANDERING transition fires
                # (hunger exit) without setting _wander_cooldown. Transition
                # back to IDLE immediately so normal guard logic takes over.
                effects.append(SetEntityAttr(
                    entity_id=ctx.entity["id"], attr_name="_pollination_visits",
                    value=0.0, tick=ctx.tick,
                ))
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))

        # ── Fleeing (managed by interaction resolver) ──
        elif ctx.entity["state"] == "FLEEING":
            if ctx.entity.get("_target") is None:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))

        # ── Reproduction exit — one-time event, then return to normal behavior. ──
        # After spawning offspring and resetting reproductive_drive to 0, the entity
        # must leave REPRODUCING so it can forage/drink/rest again. Since drive is
        # permanently at 0, it will never reproduce a second time.
        elif ctx.entity["state"] == "REPRODUCING":
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
            ))

        # ── Drinking (hysteresis) — only when NOT actively foraging/hunting.
        #    This prevents a critical bug where DRINKING overrides FORAGING:
        #    when both hunger > enter AND hydration < enter, the old code emitted
        #    two StateTransition effects and the last one (DRINKING) won. The animal
        #    would cycle: forage briefly → drink (hunger builds unchecked) →
        #    forage hungrier → drink sooner → ... → starvation death despite food.
        #    By gating entry on "not already FORAGING/HUNTING", animals finish their
        #    current feeding bout before switching to drink.
        #
        #    Emergency override: if hydration drops below DEHYDRATION_HYDRATION
        #    (the threshold where health starts draining), the animal abandons
        #    foraging/hunting/resting and seeks water immediately. This prevents
        #    death spirals during ecosystem collapse when plants go dormant/dead
        #    and herbivores would otherwise wander forever without drinking.
        elif ctx.entity["state"] == "DRINKING":
            if sv.get("hydration", 1.0) >= p.hydration_exit:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
        elif sv.get("hydration", 1.0) < DEHYDRATION_HYDRATION:
            # Critical dehydration — override any active behavior to drink
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="DRINKING", tick=ctx.tick,
            ))
        elif (sv.get("hydration", 1.0) < p.hydration_enter
              and ctx.entity["state"] not in ("FORAGING", "HUNTING")):
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="DRINKING", tick=ctx.tick,
            ))

        # ── Resting (hysteresis) — same gate: don't interrupt active behaviors.
        elif ctx.entity["state"] == "RESTING":
            if sv["energy"] >= p.energy_exit:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
        elif (sv["energy"] < p.energy_enter
              and ctx.entity["state"] not in ("FORAGING", "HUNTING")):
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="RESTING", tick=ctx.tick,
            ))

        # ── Foraging / Hunting (hysteresis) ──
        elif ctx.entity["state"] in ("FORAGING", "HUNTING"):
            if sv["hunger"] < p.hunger_exit:
                # Pollinators transition to WANDERING instead of IDLE when satiated.
                # This keeps them moving and searching for flowers rather than
                # sitting still, which prevents FORAGING↔IDLE chattering after
                # each pollination visit (relief drops hunger below exit threshold).
                if p.floral_affinity:
                    effects.append(StateTransition(
                        entity_id=ctx.entity["id"], new_state="WANDERING", tick=ctx.tick,
                    ))
                else:
                    effects.append(StateTransition(
                        entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                    ))
            elif p.diet_type in ("carnivore", "insectivore") and sv["hunger"] > CARNIVORE_HUNT_HUNGER:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="HUNTING", tick=ctx.tick,
                ))

        # ── Pollinator visit limit (state-independent check) ──
        # This must be outside the FORAGING/HUNTING block because pollinators
        # can pollinate while in IDLE state. If a butterfly has visited too many
        # flowers consecutively, force WANDERING regardless of current state.
        elif (p.floral_affinity
              and ctx.entity["state"] not in ("WANDERING", "DYING", "REPRODUCING")
              and ctx.entity.get("_pollination_visits", 0) >= POLLINATOR_VISIT_LIMIT):
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="WANDERING", tick=ctx.tick,
            ))
            effects.append(SetEntityAttr(
                entity_id=ctx.entity["id"], attr_name="_wander_cooldown",
                value=float(POLLINATOR_WANDER_COOLDOWN), tick=ctx.tick,
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
            if ctx.entity["state"] not in ("FORAGING", "HUNTING", "WANDERING", "FLEEING", "DRINKING",
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
        """Generate reproduction effects (offspring spawn + event).

        Matches engine inline behavior:
        - Parent pays energy cost from DerivedParams
        - Colony health cost for insect-type entities
        - Clutch size from DerivedParams (can be > 1)
        - Child inheritance with proper floors (generational decline)
        """
        import random as _random

        p = ctx.params
        sv = ctx.entity["state_vars"]
        meta = ctx.entity["metadata"]
        pos = ctx.entity["position"]
        effects: list[Any] = []

        # ── Parent reproduction cost ──
        # Reset reproductive drive and pay energy cost
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="reproductive_drive",
            delta=-sv.get("reproductive_drive", 0.0), tick=ctx.tick,
        ))
        effects.append(StateVarDelta(
            entity_id=ctx.entity["id"], var_name="energy",
            delta=-p.parent_energy_cost, tick=ctx.tick,
        ))

        # Colony health cost for insect-type entities (check type, not diet_type)
        if "colony_health" in sv and ctx.entity.get("type") == "INSECT":
            colony_cost = p.parent_energy_cost * 0.3
            effects.append(StateVarDelta(
                entity_id=ctx.entity["id"], var_name="colony_health",
                delta=-colony_cost, tick=ctx.tick,
            ))

        # ── Spawn offspring (clutch_size from DerivedParams) ──
        clutch_size = p.clutch_size if p.clutch_size > 0 else 1
        for i in range(clutch_size):
            # Offspring position — near parent with small offset
            new_x = pos[0] + _random.uniform(-SPAWN_OFFSET, SPAWN_OFFSET)
            new_z = pos[2] + _random.uniform(-SPAWN_OFFSET, SPAWN_OFFSET)

            # Child inherits parent stress (generational decline) with floors
            offspring_sv: dict[str, float] = {
                "hunger": sv.get("hunger", 0.5) * CHILD_HUNGER_INHERIT,
                "energy": max(CHILD_ENERGY_FLOOR, sv.get("energy", 1.0) * CHILD_ENERGY_INHERIT),
                "hydration": sv.get("hydration", 1.0),
                "health": max(CHILD_HEALTH_FLOOR, sv.get("health", 1.0) * CHILD_HEALTH_INHERIT),
                "reproductive_drive": 0.0,
                "age": 0.0,
            }

            if "colony_health" in sv:
                offspring_sv["colony_health"] = max(
                    CHILD_COLONY_FLOOR, sv["colony_health"] * CHILD_COLONY_INHERIT)

            # Insect-specific state vars
            if ctx.entity.get("type") == "INSECT":
                offspring_sv.setdefault("activity", 1.0)
                offspring_sv.setdefault("population", 1.0)

            child_id = f"{ctx.entity['id']}_child_{ctx.tick}_{_random.randint(0, 999)}"
            effects.extend([
                SpawnEntity(
                    entity_id=child_id,
                    type=ctx.entity["type"],
                    species=ctx.entity.get("species"),
                    position=[new_x, pos[1], new_z],
                    metadata=dict(meta),
                    state_vars=offspring_sv,
                    skeleton_id=ctx.entity.get("skeleton_id"),
                    initial_attrs={
                        # Newborn pollinators start with a cooldown so they don't
                        # immediately re-pollinate the flower their parent was at.
                        "_pollination_cooldown": float(POLLINATOR_POST_VISIT_COOLDOWN),
                    },
                    tick=ctx.tick,
                ),
                EventRecord(
                    event_type="REPRODUCTION",
                    source_id=ctx.entity["id"],
                    target_id=child_id,
                    position=[new_x, pos[1], new_z],
                    tick=ctx.tick,
                ),
            ])

        # State transition to REPRODUCING (applied after spawns so parent stays alive)
        effects.append(StateTransition(
            entity_id=ctx.entity["id"], new_state="REPRODUCING", tick=ctx.tick,
        ))

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
