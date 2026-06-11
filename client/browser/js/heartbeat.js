// ═══════════════════════════════════════════════════════
// līlā — Heartbeat (Client → Server Upstream)
//
// Periodically sends client-reported positions and interaction
// events back to the server for absorption. This is how the
// nervous system hears from the body.
// ═══════════════════════════════════════════════════════

import { HEARTBEAT_INTERVAL_MS } from './constants.js';

export class HeartbeatSender {
  constructor(ws) {
    this.ws = ws;
    this.lastSend = 0;
    this.pendingEvents = []; // events accumulated between heartbeats
  }

  /** Queue an event for upstream transmission. */
  queueEvent(event) {
    this.pendingEvents.push(event);
  }

  /** Queue multiple events (from agency step). */
  queueEvents(events) {
    this.pendingEvents.push(...events);
  }

  /** Send heartbeat if interval has elapsed. Returns true if sent. */
  trySend(world, now) {
    if (now - this.lastSend < HEARTBEAT_INTERVAL_MS) return false;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;

    // Build position map for all mobile entities
    const positions = {};
    for (const ent of world.entities.values()) {
      if (ent.isMobileConsumer && ent.isAlive) {
        positions[ent.id] = [round(ent.x), 0, round(ent.z)];
      }
    }

    // Build heartbeat message
    const msg = { type: 'heartbeat', positions };
    if (this.pendingEvents.length > 0) {
      msg.events = [...this.pendingEvents];
      this.pendingEvents = [];
    } else if (Object.keys(positions).length > 0) {
      // Always send at least positions to keep reconciliation alive
    } else {
      return false; // nothing to report
    }

    try {
      this.ws.send(JSON.stringify(msg));
      this.lastSend = now;
      return true;
    } catch (e) {
      console.warn('Heartbeat send failed:', e);
      return false;
    }
  }
}

function round(v) {
  return Math.round(v * 10000) / 10000;
}
