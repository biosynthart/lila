# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Movement Actor — Target selection as an effect-emitting actor

Extracts ``_pick_movement_target()`` from the engine into a pure-function
actor that emits SetTarget/ClearTarget effects. This resolves "Open Question #1"
from the architecture doc.

The actor receives read-only context (entity state, nearby entities, water sources,
compiled ecology) and returns deterministic movement target effects. Given the same
context, it always produces the same effects.

See Also:
- ``effects.py`` — SetTarget, ClearTarget effect dataclasses
- ``actors/flow_actors.py`` — ConsumerFlowActor (calls MovementActor during flow)
"""

from __future__ import annotations

import math
import random
from typing import Any

from ..config import SIM_CONFIG
from ..constants import (
    ARRIVAL_THRESHOLD,
    DEHYDRATION_HYDRATION,
    POLLINATOR_CROWD_RADIUS,
    POLLINATOR_MAX_PER_FLOWER,
    REPRO_MATE_SEEK_DRIVE,
    WANDER_RANGE,
    WATER_DRY_THRESHOLD,
)
from ..effects import ClearTarget, Effect, SetTarget

# ═══════════════════════════════════════════════════════════════════════════════
# MovementActor — Target Selection
# ═══════════════════════════════════════════════════════════════════════════════

class MovementActor:
    """Select movement targets based on entity state and traits.

    Priority order (matches engine inline behavior):
    1. SWARMING → seek nearest water, else wander
    2. DRINKING → stay put if at water, else navigate to nearest water
    3. High reproductive drive (>0.5) → seek nearest mate
    4. FORAGING herbivore → seek nearest food by diet preference
    5. FORAGING pollinator → FRUITING flower → any flower → wander
    6. HUNTING → seek nearest prey
    7. IDLE pollinator → seek flowers (active exploration)
    8. Default → wander randomly

    Only emits effects when entity has no current target (``_target is None``).
    The engine handles arrival detection and re-targeting by clearing ``_target``
    and calling the actor again.
    """

    def resolve(self, ctx: Any) -> list[Effect]:
        """Evaluate movement target for one entity this tick.

        Args:
            ctx: FlowContext (or compatible) with entity, params, nearby_entities,
                 water_sources, compiled ecology, and _entities (for mate search).

        Returns:
            List containing at most one effect: SetTarget or ClearTarget.
            Empty list if entity already has a target or no action is needed.
        """
        # If entity already has a target, don't override it.
        # The engine clears _target on arrival and calls us again.
        if ctx.entity.get("_target") is not None:
            return []

        p = ctx.params
        if p is None:
            return []

        state = ctx.entity["state"]
        pos = ctx.entity["position"]
        # grid_max: prefer explicit context field, fall back to config default
        grid_max_raw = getattr(ctx, "_grid_max", None)
        grid_max = (
            grid_max_raw
            if isinstance(grid_max_raw, (int, float))
            else SIM_CONFIG["movement"]["grid_max_default"]
        )

        # ── SWARMING — colony under stress, seek water for survival ──
        if state == "SWARMING":
            water = self._find_nearest_water(pos, ctx.water_sources)
            if water:
                return [SetTarget(
                    entity_id=ctx.entity["id"], position=water, tick=ctx.tick)]
            # No water found — wander to search
            return [SetTarget(
                entity_id=ctx.entity["id"],
                position=self._clamp_to_grid(pos, grid_max),
                tick=ctx.tick)]

        # ── DRINKING — stay put if at water, else navigate toward it ──
        if state == "DRINKING":
            if self._is_near_water(pos, ctx.water_sources):
                return [ClearTarget(entity_id=ctx.entity["id"], tick=ctx.tick)]
            water = self._find_nearest_water(pos, ctx.water_sources)
            if water:
                return [SetTarget(
                    entity_id=ctx.entity["id"], position=water, tick=ctx.tick)]
            # No water available — stay put (clear target to stop moving)
            return [ClearTarget(entity_id=ctx.entity["id"], tick=ctx.tick)]

        # ── Reproductive drive — seek mates when drive is high ──
        drive = ctx.entity["state_vars"].get("reproductive_drive", 0)
        if drive > REPRO_MATE_SEEK_DRIVE:
            mate_pos = self._find_nearest_mate_pos(ctx)
            if mate_pos:
                return [SetTarget(
                    entity_id=ctx.entity["id"], position=mate_pos, tick=ctx.tick)]

        # ── FORAGING ──
        if state == "FORAGING":
            target = self._resolve_foraging_target(ctx, pos, p, grid_max)
            if target is not None:
                return [SetTarget(
                    entity_id=ctx.entity["id"], position=target, tick=ctx.tick)]

        # ── HUNTING — seek nearest prey ──
        if state == "HUNTING":
            target = self._resolve_hunting_target(ctx, pos, p)
            if target is not None:
                return [SetTarget(
                    entity_id=ctx.entity["id"], position=target, tick=ctx.tick)]

        # ── IDLE pollinators — actively explore for flowers ──
        # WANDERING is excluded — during forced exploration cooldown, butterflies
        # should wander randomly to disperse, not fly back to nearby flowers.
        if p.floral_affinity and state == "IDLE":
            target = self._resolve_pollinator_idle_target(ctx, pos, p, grid_max)
            if target is not None:
                return [SetTarget(
                    entity_id=ctx.entity["id"], position=target, tick=ctx.tick)]

        # ── Default — wander randomly ──
        return [SetTarget(
            entity_id=ctx.entity["id"],
            position=self._clamp_to_grid(pos, grid_max),
            tick=ctx.tick)]

    # ── State-specific target resolution ──────────────────────────────────────

    def _resolve_foraging_target(
        self, ctx: Any, pos: list[float], p: Any, grid_max: float,
    ) -> list[float] | None:
        """Resolve FORAGING state target: food → emergency water → flowers → wander."""
        # Herbivores/omnivores: seek food by diet preference
        diet_order = self._get_diet_order(p.species_id, ctx)
        if diet_order:
            food = self._find_nearest_food_by_preference(
                pos, p.sensory_range, diet_order, ctx.nearby_entities)
            if food:
                return food

        # Emergency: critically dehydrated forager with no food nearby.
        hydration = ctx.entity["state_vars"].get("hydration", 1.0)
        if hydration < DEHYDRATION_HYDRATION:
            water = self._find_nearest_water(pos, ctx.water_sources)
            if water:
                return water

        # Pollinators: seek flowers (FRUITING first, then any flower, wander last)
        if p.floral_affinity:
            flower = self._find_nearest_flower(
                pos, grid_max, p, ctx.nearby_entities, ctx.entity["id"],
                compiled=ctx.compiled)
            if flower:
                return flower

            any_flower = self._find_nearest_flower_any_state(
                pos, grid_max, p, ctx.nearby_entities, ctx.entity["id"],
                compiled=ctx.compiled)
            if any_flower:
                return any_flower

            # No flowers found — wander randomly across the field
            return self._clamp_to_grid(pos, grid_max)

        # Non-pollinator forager with no food and not dehydrated — wander
        return self._clamp_to_grid(pos, grid_max)

    def _resolve_hunting_target(
        self, ctx: Any, pos: list[float], p: Any,
    ) -> list[float] | None:
        """Resolve HUNTING state target: prey → emergency water."""
        prey_species = [s for s, _ in self._get_diet_order(p.species_id, ctx)]
        if prey_species:
            target = self._find_nearest_prey(
                pos, p.sensory_range, prey_species, ctx.nearby_entities)
            if target:
                return target

        # Emergency: critically dehydrated hunter with no prey nearby
        hydration = ctx.entity["state_vars"].get("hydration", 1.0)
        if hydration < DEHYDRATION_HYDRATION:
            water = self._find_nearest_water(pos, ctx.water_sources)
            if water:
                return water

        # No prey found — fall through to default (wander) in resolve()
        return None

    def _resolve_pollinator_idle_target(
        self, ctx: Any, pos: list[float], p: Any, grid_max: float,
    ) -> list[float] | None:
        """Resolve IDLE pollinator target: flowers → wander."""
        flower = self._find_nearest_flower(
            pos, grid_max, p, ctx.nearby_entities, ctx.entity["id"],
            compiled=ctx.compiled)
        if flower:
            return flower

        any_flower = self._find_nearest_flower_any_state(
            pos, grid_max, p, ctx.nearby_entities, ctx.entity["id"],
            compiled=ctx.compiled)
        if any_flower:
            return any_flower

        # No flowers in range — wander randomly
        return self._clamp_to_grid(pos, grid_max)

    # ── Target search helpers ─────────────────────────────────────────────────

    @staticmethod
    def _find_nearest_food_by_preference(
        pos: list[float], search_range: float,
        diet_order: list[tuple[str, int]],
        nearby_entities: list[dict],
    ) -> list[float] | None:
        """Find nearest food source, respecting diet preference ordering.

        Returns the position of the nearest entity whose species matches
        the first (most preferred) diet tag. Skips dead, dying, dormant,
        and low-growth entities.
        """
        best_by_pref: dict[int, tuple[float, list[float]]] = {}

        for other in nearby_entities:
            if other["state"] in ("DEAD", "DYING", "DORMANT"):
                continue
            growth_threshold = SIM_CONFIG["movement"]["food_growth_viability_threshold"]
            if other.get("state_vars", {}).get("growth", 1.0) <= growth_threshold:
                continue
            d = _distance(pos, other["position"])
            min_dist = SIM_CONFIG["movement"]["mate_minimum_distance"]
            if d < min_dist or d > search_range * 2:
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

    @staticmethod
    def _find_nearest_prey(
        pos: list[float], search_range: float, prey_species: list[str],
        nearby_entities: list[dict],
    ) -> list[float] | None:
        """Find nearest living prey entity from the given species list."""
        best_dist: float = float("inf")
        best_pos: list[float] | None = None
        for other in nearby_entities:
            if other.get("species") not in prey_species:
                continue
            if _is_alive(other):
                d = _distance(pos, other["position"])
                if d < search_range and d < best_dist:
                    best_dist, best_pos = d, list(other["position"])
        return best_pos

    @staticmethod
    def _find_nearest_flower(
        pos: list[float], search_range: float, p: Any,
        nearby_entities: list[dict], entity_id: str,
        compiled: Any = None,
    ) -> list[float] | None:
        """Find nearest FRUITING flower matching pollinator's floral affinity.

        Only returns flowers that are FRUITING, have a matching pollination
        syndrome (via the compiled interaction matrix), are not on
        pollination cooldown, and haven't reached max visitor capacity.
        """
        best_dist: float = float("inf")
        best_pos: list[float] | None = None

        for other in nearby_entities:
            if other["id"] == entity_id:
                continue
            if other["state"] != "FRUITING":
                continue
            if other.get("_pollination_cooldown", 0) > 0:
                continue
            # Check pollination interaction via compiled ecology
            if not _has_pollination_interaction(
                    p.species_id, other.get("species", ""), compiled):
                continue
            # Skip flowers at max pollinator capacity — forces dispersal
            if _count_pollinators_at_flower(other["position"], nearby_entities) >= POLLINATOR_MAX_PER_FLOWER:
                continue
            # Skip flowers too close — prevents re-targeting the same flower after arrival
            arrive_mult = SIM_CONFIG["movement"]["arrival_threshold_double"]
            d = _distance(pos, other["position"])
            if d < ARRIVAL_THRESHOLD * arrive_mult or d > search_range:
                continue
            if d < best_dist:
                best_dist, best_pos = d, list(other["position"])
        return best_pos

    @staticmethod
    def _find_nearest_flower_any_state(
        pos: list[float], search_range: float, p: Any,
        nearby_entities: list[dict], entity_id: str,
        compiled: Any = None,
    ) -> list[float] | None:
        """Find nearest flower the pollinator can visit, regardless of state.

        Used as a waypoint when no FRUITING flowers exist — the pollinator
        flies to the flower cluster and waits for blooms instead of sitting
        at water indefinitely. Respects per-flower visitor cap.

        Excludes DORMANT plants: they have no nectar and targeting them causes
        butterflies to shuttle between dormant plants endlessly instead of
        wandering across the field searching for active blooms.
        """
        best_dist: float = float("inf")
        best_pos: list[float] | None = None

        for other in nearby_entities:
            if other["id"] == entity_id:
                continue
            if other["state"] in ("DEAD", "DYING", "DORMANT"):
                continue
            if not _has_pollination_interaction(
                    p.species_id, other.get("species", ""), compiled):
                continue
            # Skip flowers at max pollinator capacity
            if _count_pollinators_at_flower(other["position"], nearby_entities) >= POLLINATOR_MAX_PER_FLOWER:
                continue
            # Skip flowers too close — prevents re-targeting after arrival
            arrive_mult = SIM_CONFIG["movement"]["arrival_threshold_double"]
            d = _distance(pos, other["position"])
            if d < ARRIVAL_THRESHOLD * arrive_mult or d > search_range:
                continue
            if d < best_dist:
                best_dist, best_pos = d, list(other["position"])
        return best_pos

    @staticmethod
    def _find_nearest_mate_pos(ctx: Any) -> list[float] | None:
        """Find position of nearest compatible mate across the full grid.

        Animals can detect mates at longer range (scent, calls).
        Uses ctx._entities for global search.
        """
        entity = ctx.entity
        best_dist: float = float("inf")
        best_pos: list[float] | None = None

        all_entities = getattr(ctx, "_entities", {})
        min_dist = SIM_CONFIG["movement"]["mate_minimum_distance"]
        for other in all_entities.values():
            if other["id"] == entity["id"]:
                continue
            if not _is_alive(other):
                continue
            if other.get("type") != entity.get("type"):
                continue
            if other.get("species") != entity.get("species"):
                continue
            d = _distance(entity["position"], other["position"])
            if d < min_dist:
                continue  # Already next to them
            if d < best_dist:
                best_dist, best_pos = d, list(other["position"])
        return best_pos

    @staticmethod
    def _find_nearest_water(
        pos: list[float], water_sources: list[dict],
    ) -> list[float] | None:
        """Find the nearest non-dry water source and return an approach point."""
        best: dict | None = None
        best_dist: float = float("inf")

        for source in water_sources:
            if source.get("water_level", 1.0) < WATER_DRY_THRESHOLD:
                continue
            d = _distance(pos, source["position"])
            if d < best_dist:
                best_dist, best = d, source

        if best is None:
            return None

        dx = best["position"][0] - pos[0]
        dz = best["position"][2] - pos[2]
        dist = math.sqrt(dx * dx + dz * dz)
        min_water_dist = SIM_CONFIG["movement"]["water_source_min_distance"]
        if dist < min_water_dist:
            return list(best["position"])

        approach_factor = SIM_CONFIG["movement"]["water_approach_radius_factor"]
        r = best.get("radius", 1.0) * approach_factor
        return [best["position"][0] - (dx / dist) * r, 0.0,
                best["position"][2] - (dz / dist) * r]

    @staticmethod
    def _is_near_water(pos: list[float], water_sources: list[dict]) -> bool:
        """Check if position is within effective radius of any water source."""
        for source in water_sources:
            if source.get("water_level", 1.0) < WATER_DRY_THRESHOLD:
                continue
            near_buffer = SIM_CONFIG["consumer_physiology"]["near_water_distance_buffer"]
            if _distance(pos, source["position"]) <= source.get("radius", 1.0) + near_buffer:
                return True
        return False

    @staticmethod
    def _clamp_to_grid(pos: list[float], grid_max: float) -> list[float]:
        """Generate a random wander target clamped to grid bounds with margin."""
        margin = SIM_CONFIG["movement"]["wander_grid_margin"]
        lo, hi = margin, grid_max - margin
        return [
            max(lo, min(hi, pos[0] + random.uniform(-WANDER_RANGE, WANDER_RANGE))),
            pos[1],
            max(lo, min(hi, pos[2] + random.uniform(-WANDER_RANGE, WANDER_RANGE))),
        ]

    @staticmethod
    def _get_diet_order(species_id: str, ctx: Any) -> list[tuple[str, int]]:
        """Get diet preference ordering from compiled ecology."""
        if ctx.compiled is None:
            return []
        return ctx.compiled.get_diet_order(species_id) or []


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level helper functions (no self dependency)
# ═══════════════════════════════════════════════════════════════════════════════

def _distance(a: list[float], b: list[float]) -> float:
    """2D Euclidean distance (XZ plane, Y is vertical)."""
    dx = a[0] - b[0]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dz * dz)


def _is_alive(entity: dict) -> bool:
    """Check if entity is alive (not DEAD, DYING, or DORMANT)."""
    return entity["state"] not in ("DEAD", "DYING", "DORMANT")


def _has_pollination_interaction(
    species_a: str, species_b: str, compiled: Any,
) -> bool:
    """Check if two species have a pollination interaction.

    Returns True if the compiled ecology reports a pollination-type
    interaction between the given species pair. Falls back to False
    if compiled ecology is unavailable.
    """
    if compiled is None:
        return False
    try:
        interactions = compiled.get_interactions(species_a, species_b)
        if not interactions:
            return False
        return any(ix.interaction_type == "pollination" for ix in interactions)
    except Exception:
        return False


def _count_pollinators_at_flower(
    flower_pos: list[float], nearby_entities: list[dict],
) -> int:
    """Count pollinators currently lingering at or near a flower.

    Used to enforce per-flower visitor cap so butterflies disperse
    across the field instead of all clustering on one plant.
    Counts entities within POLLINATOR_CROWD_RADIUS that have _linger > 0.
    """
    count = 0
    r2 = POLLINATOR_CROWD_RADIUS ** 2
    for entity in nearby_entities:
        if not entity.get("_linger", 0):
            continue
        dx = entity["position"][0] - flower_pos[0]
        dz = entity["position"][2] - flower_pos[2]
        if dx * dx + dz * dz <= r2:
            count += 1
    return count
