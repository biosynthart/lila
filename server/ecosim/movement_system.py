# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Movement System — Gate and kinematics for mobile entities.

Owns the full movement pipeline that runs between flow actors (which select
targets via SetTarget/ClearTarget effects) and interaction actors (which need
updated positions). The system has two responsibilities:

  1. **Movement gate** — decides whether an entity should move this tick.
     Checks ACTIVE_MOVEMENT_STATES, pollinator exceptions for IDLE/WANDERING,
     and decrements linger/cooldown side-effects from pollination visits.

  2. **Kinematics** — moves the entity toward its current target at species-
     derived speed, clamping to grid bounds and clearing on arrival.

Target selection itself is handled by MovementActor (actor-based), which emits
SetTarget/ClearTarget effects during the flow phase. This system only performs
the physical movement step.

Usage
─────
    movement = MovementSystem(grid_max=31.0)
    for entity in mobile_entities:
        params = get_params(entity)
        if params and params.diet_type not in ("autotroph", "decomposer"):
            movement.step(entity, params, dt)

See Also:
- ``actors/movement_actors.py`` — MovementActor (target selection via effects)
"""

from __future__ import annotations

import math
from typing import Any

from .constants import ACTIVE_MOVEMENT_STATES, ARRIVAL_THRESHOLD

# ── Movement System ──────────────────────────────────────────────────────────

class MovementSystem:
    """Gate and kinematics for mobile entities.

    Separates movement concerns from the engine's tick orchestration so that:
    - Target selection lives in MovementActor (actor-based, effect-emitting)
    - Gate policy + physics live here (unit-testable without full engine)
    - Engine step() stays focused on phase coordination

    Args:
        grid_max: World space max coordinate for position clamping.
    """

    def __init__(self, grid_max: float) -> None:
        self._grid_max = grid_max

    # ── Public API ───────────────────────────────────────────────────────

    def step(self, entity: dict[str, Any], params: Any, dt: float) -> None:
        """Advance one mobile entity for one tick.

        Handles linger/cooldown decrements, checks the movement gate, and
        performs kinematics (move toward target at species-derived speed).

        Args:
            entity: Entity dict (mutated in-place: position, velocity,
                    _linger, _pollination_cooldown, _target).
            params: DerivedParams for this entity's species.
            dt: Time step in seconds.
        """
        # Decrement linger counter (set by pollination visits, etc.)
        linger = entity.get("_linger", 0)
        if linger > 0:
            entity["_linger"] = max(0, linger - 1)
            entity["velocity"] = [0.0, 0.0, 0.0]
            return

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
        else:
            entity["velocity"] = [0.0, 0.0, 0.0]

    # ── Kinematics ───────────────────────────────────────────────────────

    def _move_toward_target(
        self, e: dict[str, Any], p: Any, dt: float,
    ) -> None:
        """Move entity toward its current target at species-derived speed.

        Target selection is handled by MovementActor (actor-based), which
        emits SetTarget/ClearTarget effects during the flow phase. This
        method only performs the physical movement step.

        When no target is set, the entity stops. The next tick's flow phase
        will have MovementActor select a new target via effects. On arrival
        within ARRIVAL_THRESHOLD, the target is cleared and the entity stops —
        MovementActor picks a new one on the next tick.
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
