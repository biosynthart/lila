"""līlā Python Client — Client-side agency engine.

Between server ticks, each mobile entity decides what to do based on:
  - Server intent (state + drives + eligibility flags)
  - Local perception (nearest food, water, threats from world model)
  - Motion latent (modulates speed, hesitation, path curvature)

This is the "body" in "server is nervous system, client is body."
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

from .constants import GRID_SIZE
from .world_model import WorldEntity


def step_agency(world, dt: float) -> list[dict]:
    """Run one frame of local agency for all mobile entities.

    Called every render frame (~60 Hz). Returns client-reported events
    to send upstream via heartbeat.
    """
    events: list[dict] = []

    for ent in world.entities.values():
        if not ent.is_mobile_consumer or not ent.is_alive:
            continue

        # Update speed from species definition (once)
        if not getattr(ent, "_speed_set", False):
            defn = world.species_defs.get(ent.species, {})
            ent.speed = defn.get("movement_speed", 2.0)
            ent._speed_set = True

        # Evaluate behavior
        action = evaluate_behavior(ent, world)

        # Execute the action
        execute_action(ent, action, world, dt, events)

    return events


# ─── Behavior Evaluators ──────────────────────────────────────────────────


def evaluate_behavior(ent: WorldEntity, world) -> dict[str, Any]:
    """Evaluate what an entity should do based on its intent and local perception."""
    state = ent.state
    drive = ent.drive or {}
    species_def = world.species_defs.get(ent.species, {})

    # ── Fleeing (highest priority) ──
    if state == "FLEEING":
        return evaluate_fleeing(ent, world, species_def)

    # ── Drinking ──
    if state == "DRINKING" or (ent.can_drink and drive.get("hydration", 1.0) < 0.3):
        return evaluate_drinking(ent, world)

    # ── Reproduction seeking ──
    if ent.repro_eligible and drive.get("reproductive_drive", 0) > 0.5:
        mate_action = evaluate_mate_seeking(ent, world)
        if mate_action:
            return mate_action

    # ── Foraging / Herbivory ──
    if state == "FORAGING" and ent.can_consume:
        return evaluate_foraging(ent, world, species_def)

    # ── Hunting / Predation ──
    if (state in ("HUNTING", "FORAGING")) and ent.can_predate:
        return evaluate_hunting(ent, world, species_def)

    # ── Pollination ──
    if ent.can_pollinate and species_def.get("is_pollinator"):
        return evaluate_pollination(ent, world, species_def)

    # ── Resting / Idle — wander with latent-modulated style ──
    return evaluate_wandering(ent, world)


def evaluate_fleeing(ent: WorldEntity, world, species_def: dict) -> dict:
    """Flee from nearest threat."""
    flee_targets = species_def.get("flee_targets", [])
    if not flee_targets:
        return evaluate_wandering(ent, world)

    nearest_threat: WorldEntity | None = None
    best_dist_sq = float("inf")
    for other in world.entities.values():
        if not other.is_alive:
            continue
        other_def = world.species_defs.get(other.species, {})
        if not other_def or other.species not in flee_targets:
            continue
        d2 = ent.dist_sq_to(other)
        if d2 < best_dist_sq:
            best_dist_sq = d2
            nearest_threat = other

    if nearest_threat and best_dist_sq < 400:  # ~20 world units sensory range²
        dx = ent.x - nearest_threat.x
        dz = ent.z - nearest_threat.z
        dist = math.sqrt(dx * dx + dz * dz) or 1
        return {
            "type": "flee",
            "target_x": clamp(ent.x + (dx / dist) * 8, GRID_SIZE),
            "target_z": clamp(ent.z + (dz / dist) * 8, GRID_SIZE),
        }

    return evaluate_wandering(ent, world)


def evaluate_drinking(ent: WorldEntity, world) -> dict:
    """Approach nearest water source."""
    water = world.find_nearest_water(ent.x, ent.z)
    if water:
        wx, _, wz = water["position"]
        r = water.get("radius", 1)
        dx = wx - ent.x
        dz = wz - ent.z
        dist = math.sqrt(dx * dx + dz * dz) or 1
        approach_r = max(r - 0.5, 0.3)
        return {
            "type": "drink",
            "target_x": clamp(wx - (dx / dist) * approach_r, GRID_SIZE),
            "target_z": clamp(wz - (dz / dist) * approach_r, GRID_SIZE),
        }
    return evaluate_wandering(ent, world)


def evaluate_mate_seeking(ent: WorldEntity, world) -> dict | None:
    """Seek a mate of the same species."""
    best: WorldEntity | None = None
    best_dist = 15.0  # sensory range
    for other in world.entities.values():
        if other.id == ent.id:
            continue
        if other.species != ent.species:
            continue
        if not other.is_alive:
            continue
        d = ent.distance_to(other)
        if d < best_dist:
            best_dist = d
            best = other

    if best:
        return {
            "type": "seek_mate",
            "target_x": best.x,
            "target_z": best.z,
            "target_id": best.id,
        }
    return None


def evaluate_foraging(ent: WorldEntity, world, species_def: dict) -> dict:
    """Find nearest food item to approach."""
    diet_order = species_def.get("diet_order", [])

    for food_species in diet_order:
        if isinstance(food_species, (list, tuple)):
            food_species = food_species[0]
        food = world.find_nearest_species(ent.x, ent.z, [food_species], ent.id)
        if food and ent.distance_to(food) < 15:
            return {
                "type": "forage",
                "target_x": food.x,
                "target_z": food.z,
                "target_id": food.id,
            }

    return evaluate_wandering(ent, world)


def evaluate_hunting(ent: WorldEntity, world, species_def: dict) -> dict:
    """Find nearest prey to hunt."""
    diet_order = species_def.get("diet_order", [])
    prey_species = [s[0] if isinstance(s, (list, tuple)) else s for s in diet_order]

    if prey_species:
        prey = world.find_nearest_species(ent.x, ent.z, prey_species, ent.id)
        if prey and ent.distance_to(prey) < 15:
            return {
                "type": "hunt",
                "target_x": prey.x,
                "target_z": prey.z,
                "target_id": prey.id,
            }

    return evaluate_wandering(ent, world)


def evaluate_pollination(ent: WorldEntity, world, species_def: dict) -> dict:
    """Find nearest fruiting flower to pollinate."""
    poll_targets = species_def.get("pollination_targets", [])
    if not poll_targets:
        return evaluate_wandering(ent, world)

    for flower in world.entities.values():
        if not flower.is_alive:
            continue
        if flower.species not in poll_targets:
            continue
        if flower.state != "FRUITING":
            continue
        dist = ent.distance_to(flower)
        if 0.5 < dist < 15:
            return {
                "type": "pollinate",
                "target_x": flower.x,
                "target_z": flower.z,
                "target_id": flower.id,
            }

    return evaluate_wandering(ent, world)


def evaluate_wandering(ent: WorldEntity, world) -> dict:
    """Pick a wander target near the entity, modulated by motion latent."""
    # Reuse existing wander target if still valid and not reached
    if getattr(ent, "has_target", False) and getattr(ent, "_last_action_type", "") == "wander":
        dx = ent.target_x - ent.x
        dz = ent.target_z - ent.z
        if math.sqrt(dx * dx + dz * dz) > 0.5:
            return {"type": "wander", "target_x": ent.target_x, "target_z": ent.target_z}

    ml = ent.motion_latent or [0, 0, 0, 0]
    urgency = abs(ml[0])  # dim 0 = pace/urgency
    wander_range = 2 + (1 - urgency) * 4

    return {
        "type": "wander",
        "target_x": clamp(ent.x + (random.random() - 0.5) * wander_range * 2, GRID_SIZE),
        "target_z": clamp(ent.z + (random.random() - 0.5) * wander_range * 2, GRID_SIZE),
    }


# ─── Action Execution ─────────────────────────────────────────────────────


def execute_action(
    ent: WorldEntity,
    action: dict[str, Any],
    world,
    dt: float,
    events: list[dict],
) -> None:
    """Execute an action for one entity over one frame."""
    if not action or ("target_x" not in action and action.get("type") != "wander"):
        return

    ml = ent.motion_latent or [0, 0, 0, 0]
    urgency = (ml[0] + 1) * 0.5  # normalize to 0..1
    caution = abs(ml[1])  # dim 1 = alertness

    base_speed = ent.speed or 2.0

    # Modulate speed by action type
    speed_mod = {
        "flee": 1.5 + urgency * 0.5,
        "hunt": 1.2 + urgency * 0.3,
        "forage": 0.8 + urgency * 0.4,
        "drink": 0.7,
        "seek_mate": 0.6 + urgency * 0.3,
        "pollinate": 0.5 + urgency * 0.3,
    }.get(action.get("type", "wander"), 0.3 + (1 - caution) * 0.4)

    base_speed *= speed_mod

    target_x = action.get("target_x", ent.x)
    target_z = action.get("target_z", ent.z)

    # Move toward target
    dx = target_x - ent.x
    dz = target_z - ent.z
    dist = math.sqrt(dx * dx + dz * dz)

    if dist > 0.1:
        step = min(base_speed * dt, dist)
        # Add slight curvature based on caution — high caution = more wobble
        wobble = caution * math.sin(time.monotonic() * 3 + ent.x) * 0.3

        ent.x += (dx / dist) * step + wobble * dt
        ent.z += (dz / dist) * step - wobble * dt
        ent.x = clamp(ent.x, GRID_SIZE)
        ent.z = clamp(ent.z, GRID_SIZE)

        ent.velocity_x = (dx / dist) * base_speed
        ent.velocity_z = (dz / dist) * base_speed

        # Lerp facing angle toward travel direction (smooth rotation)
        target_angle = math.atan2(dz, dx)
        ent.facing_angle = _lerp_angle(ent.facing_angle, target_angle, 0.15)
    else:
        # Arrived at target
        ent.velocity_x = 0
        ent.velocity_z = 0

        # Check for interaction triggers on arrival
        _check_interaction(ent, action, world, events)

        # Reset wander target so next frame picks a new one
        if action.get("type") == "wander":
            ent.has_target = False

    ent.target_x = target_x
    ent.target_z = target_z
    ent._last_action_type = action.get("type", "wander")
    ent.has_target = True


def _check_interaction(
    ent: WorldEntity,
    action: dict[str, Any],
    world,
    events: list[dict],
) -> None:
    """Check if an entity should trigger an interaction on target arrival."""
    target_id = action.get("target_id")
    if not target_id:
        return

    target_ent = world.entities.get(target_id)
    if not target_ent or not target_ent.is_alive:
        return

    dist = ent.distance_to(target_ent)
    if dist > 3.0:
        return

    # Cooldown: don't re-interact with same target within 2 seconds
    now = time.monotonic()
    cooldowns = getattr(ent, "_interaction_cooldowns", {})
    key = f"{ent.id}:{target_id}"
    if cooldowns.get(key, 0) > now - 2.0:
        return

    action_type = action.get("type", "")

    if action_type == "forage" and dist < 2.0 and ent.can_consume:
        events.append({
            "type": "consumption",
            "source_id": ent.id,
            "target_id": target_id,
            "position": [target_ent.x, 0, target_ent.z],
        })
        cooldowns[key] = now

    elif action_type == "hunt" and dist < 1.5 and ent.can_predate:
        events.append({
            "type": "predation",
            "source_id": ent.id,
            "target_id": target_id,
            "kill_position": [target_ent.x, 0, target_ent.z],
        })
        cooldowns[key] = now

    elif action_type == "pollinate" and dist < 1.5 and ent.can_pollinate:
        events.append({
            "type": "pollination",
            "source_id": ent.id,
            "target_id": target_id,
            "position": [target_ent.x, 0, target_ent.z],
        })
        cooldowns[key] = now

    elif action_type == "seek_mate" and dist < 3.0 and ent.repro_eligible:
        events.append({
            "type": "repro",
            "parent_id": ent.id,
            "offspring_count": 1,
            "client_position": [ent.x, 0, ent.z],
        })
        cooldowns[key] = now

    ent._interaction_cooldowns = cooldowns


# ─── Helpers ────────────────────────────────────────────────────────────────


def clamp(v: float, max_val: int) -> float:
    return max(0.5, min(max_val - 0.5, v))


def _lerp_angle(a: float, b: float, t: float) -> float:
    """Spherical lerp between two angles (handles wrapping at ±π)."""
    # Normalize difference to [-π, π]
    diff = math.atan2(math.sin(b - a), math.cos(b - a))
    return a + diff * t
