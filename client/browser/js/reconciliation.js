// ═══════════════════════════════════════════════════════
// līlā — Reconciliation (Client ↔ Server Position Sync)
//
// When a new tick packet arrives, reconcile client-agency positions
// with server reference positions. Trust the client within bounds;
// gently correct when divergence exceeds expected travel distance.
// ═══════════════════════════════════════════════════════

import { SERVER_TICK_RATE } from './constants.js';

/**
 * Reconcile all entities after receiving a new tick packet.
 * Called once per server tick, not every frame.
 */
export function reconcile(world) {
  for (const ent of world.entities.values()) {
    if (!ent.isMobileConsumer || !ent.isAlive) continue;

    // If server acknowledged our deviation, trust it fully — no correction needed
    if (ent.ackReceived) {
      // Server snapped to our position. Sync client x/z to ref.
      ent.x = ent.refX;
      ent.z = ent.refZ;
      continue;
    }

    const dx = ent.x - ent.refX;
    const dz = ent.z - ent.refZ;
    const divergence = Math.sqrt(dx * dx + dz * dz);

    if (divergence < 0.1) continue; // negligible drift

    // Expected max travel per server tick interval
    const speed = ent.speed || 2.0;
    const expectedTravel = speed * SERVER_TICK_RATE;

    if (divergence <= expectedTravel * 2.5) {
      // Within bounds — soft nudge toward reference (gravity well)
      const nudgeFactor = 0.15; // gentle pull, not a snap
      ent.x -= dx * nudgeFactor;
      ent.z -= dz * nudgeFactor;
    } else {
      // Significant divergence — lerp more aggressively toward reference.
      // The server will likely send _ack on next tick if this persists.
      const snapFactor = 0.5;
      ent.x -= dx * snapFactor;
      ent.z -= dz * snapFactor;
    }
  }
}
