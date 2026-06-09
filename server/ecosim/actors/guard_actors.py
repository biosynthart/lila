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

from typing import Any

from ..config import SIM_CONFIG
from ..constants import (
    CARNIVORE_HUNT_HUNGER,
    DEHYDRATION_HYDRATION,
    DORMANCY_RECOVERY_EXIT_HEALTH,
    POLLINATOR_VISIT_LIMIT,
    POLLINATOR_WANDER_COOLDOWN,
)
from ..effects import (
    DepositOrganicMatter,
    EventRecord,
    RemoveEntity,
    SetEntityAttr,
    SetStateVar,
    StateTransition,
)


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
        ctx.entity["state"]
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
            # Biome-dependent: desert organisms need more moisture to wake
            if soil_moisture > ctx.biome.dormant_consumer_moisture_wake:
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                ))
                # Skip further guard checks — just wake up this tick
                return effects

        # ── Colony swarming (highest behavioral priority) ──
        if "colony_health" in sv:
            swarm_entry = SIM_CONFIG["consumer_physiology"]["colony_swarm_entry_threshold"]
            if ctx.entity["state"] == "SWARMING":
                # Exit SWARMING when colony recovers above threshold.
                # The near-water bonus (WATER_PROXIMITY_COLONY_FACTOR) helps
                # rebuild colony_health once the insect reaches a water source,
                # allowing it to return to normal behavior.
                swarm_exit = SIM_CONFIG["consumer_physiology"]["colony_swarm_exit_threshold"]
                if sv["colony_health"] >= swarm_exit:
                    effects.append(StateTransition(
                        entity_id=ctx.entity["id"], new_state="IDLE", tick=ctx.tick,
                    ))
            elif sv["colony_health"] < swarm_entry:
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
        # must leave REPRODUCING so it can forage/drink/rest again. Drive will rebuild
        # over time via consumer flow if conditions are good (low hunger, high energy).
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
            elif self._should_hunt(ctx, p, sv):
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
            if self._should_hunt(ctx, p, sv):
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
        effects.extend(ReproductionActor().resolve_animal(ctx))

        return effects

    @staticmethod
    def _should_hunt(ctx: Any, p: Any, sv: dict[str, float]) -> bool:
        """Determine if an entity should transition to HUNTING state.

        Obligate carnivores/insectivores hunt when hunger > CARNIVORE_HUNT_HUNGER.
        Omnivores also hunt at that threshold, but additionally escalate earlier
        when prey populations are high relative to plant food sources. This models
        the ecological dynamic where predators switch to hunting when their primary
        prey becomes abundant (e.g., songbirds eating butterflies during explosions).
        """
        if p.diet_type in ("carnivore", "insectivore"):
            return sv["hunger"] > CARNIVORE_HUNT_HUNGER

        if p.diet_type == "omnivore":
            # Always hunt at critical hunger levels
            if sv["hunger"] > CARNIVORE_HUNT_HUNGER:
                return True

            # Population-based escalation: check prey-to-plant ratio.
            # When pollinators outstrip flowers, omnivores switch to hunting
            # even at moderate hunger. Threshold: 3+ living pollinators per
            # viable flower (GROWING or FRUITING state).
            all_entities = getattr(ctx, "_entities", {})
            if not all_entities:
                return False

            prey_species = []
            plant_species = []
            diet_order = ctx.compiled.get_diet_order(p.species_id) if ctx.compiled else []
            for target_species, _ in diet_order:
                interactions = ctx.compiled.get_interactions(p.species_id, target_species)
                for ix in interactions:
                    if ix.interaction_type == "predation":
                        prey_species.append(target_species)
                    elif ix.interaction_type == "herbivory":
                        plant_species.append(target_species)

            if not prey_species or not plant_species:
                return False

            living_prey = sum(
                1 for e in all_entities.values()
                if e.get("species") in prey_species and _is_alive_guard(e)
            )
            viable_plants = sum(
                1 for e in all_entities.values()
                if e.get("species") in plant_species
                and e["state"] in ("GROWING", "FRUITING")
            )

            # Ratio threshold: hunt when prey outnumber plants by 3:1
            return viable_plants > 0 and living_prey / max(viable_plants, 1) >= 3.0

        return False


def _is_alive_guard(entity: dict[str, Any]) -> bool:
    """Check if entity is alive for guard actor population counting."""
    return entity["state"] not in ("DEAD", "DYING", "DORMANT")


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
        ctx.entity["state"]
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
            # Weighted effective nutrients: fast pool dominates,
            # slow pool contributes as a buffer (soil health memory).
            n_fast = ctx.voxel_grid.get("nutrients_fast", gx, gy, gz)
            n_slow = ctx.voxel_grid.get("nutrients_slow", gx, gy, gz)
            soil_nutrients = n_fast + n_slow * 0.3

            # Increment dormant ticks counter
            current_dormant_ticks = sv.get("_dormant_ticks", 0) + 1
            effects.append(SetStateVar(
                entity_id=ctx.entity["id"], var_name="_dormant_ticks",
                value=float(current_dormant_ticks), tick=ctx.tick,
            ))

            # Recovery check
            if (soil_moisture > p.dormancy_recovery_moisture
                    and soil_nutrients > p.dormancy_recovery_nutrients):
                # Biome-dependent recovery rates
                recovery_health = max(
                    ctx.biome.plant_dormancy_recovery_health_floor,
                    p.health_drain_dehydrated * ctx.biome.plant_dormancy_recovery_health_multiplier,
                )
                hyd_floor = SIM_CONFIG["plant_physiology"]["dormancy_recovery_hydration_floor"]
                recovery_hydration = max(
                    hyd_floor,
                    p.health_drain_dehydrated * ctx.biome.plant_dormancy_recovery_hydration_multiplier,
                )
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

        decomp_cfg = SIM_CONFIG["decomposer_physiology"]
        if (organic > decomp_cfg["blooming_organic_matter_threshold"]
                and sv["population"] > decomp_cfg["blooming_population_threshold"]):
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="BLOOMING", tick=ctx.tick,
            ))
        elif sv["activity"] < decomp_cfg["dormant_activity_threshold"]:
            effects.append(StateTransition(
                entity_id=ctx.entity["id"], new_state="DORMANT", tick=ctx.tick,
            ))
        else:
            if ctx.entity["state"] not in ("ACTIVE", "BLOOMING", "DORMANT"):
                effects.append(StateTransition(
                    entity_id=ctx.entity["id"], new_state="ACTIVE", tick=ctx.tick,
                ))

        return effects

# Import reproduction actor here to avoid circular imports with __init__.py
from .reproduction_actor import ReproductionActor  # noqa: E402, F401

