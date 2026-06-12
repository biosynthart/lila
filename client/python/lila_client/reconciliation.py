"""līlā Python Client — Reconciliation (Client ↔ Server Position Sync).

When a new tick packet arrives, reconcile client-agency positions
with server reference positions. Trust the client within bounds;
gently correct when divergence exceeds expected travel distance.
"""

from __future__ import annotations

import math

from .constants import SERVER_TICK_RATE, RECONCILE_SNAP_THRESHOLD_MULT, RECONCILE_NUDGE_FACTOR


def reconcile(world) -> None:
    """Reconcile all entities after receiving a new tick packet.

    Called once per server tick, not every frame.
    """
    for ent in world.entities.values():
        if not ent.is_mobile_consumer or not ent.is_alive:
            continue

        # If server acknowledged our deviation, trust it fully — no correction needed
        if ent.ack_received:
            # Server snapped to our position. Sync client x/z to ref.
            ent.x = ent.ref_x
            ent.z = ent.ref_z
            continue

        dx = ent.x - ent.ref_x
        dz = ent.z - ent.ref_z
        divergence = math.sqrt(dx * dx + dz * dz)

        if divergence < 0.1:
            continue  # negligible drift

        # Expected max travel per server tick interval
        speed = ent.speed or 2.0
        expected_travel = speed * SERVER_TICK_RATE

        if divergence <= expected_travel * RECONCILE_SNAP_THRESHOLD_MULT:
            # Within bounds — soft nudge toward reference (gravity well)
            ent.x -= dx * RECONCILE_NUDGE_FACTOR
            ent.z -= dz * RECONCILE_NUDGE_FACTOR
        else:
            # Significant divergence — lerp more aggressively toward reference.
            # The server will likely send _ack on next tick if this persists.
            snap_factor = 0.5
            ent.x -= dx * snap_factor
            ent.z -= dz * snap_factor
