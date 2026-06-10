// ═══════════════════════════════════════════════════════
// līlā Client Behavioral Model — State Store + Reconciliation
// 
// Fills the gap between server ticks (0.5Hz = 2s intervals) with
// procedural behavioral prediction. When new server truth arrives,
// reconciles smoothly over 30-60 frames.
// ═══════════════════════════════════════════════════════

const BehavioralModel = (() => {
  'use strict';

  // ─── Configuration ──────────────────────────────────────
  const CONFIG = {
    serverTickRate: 2.0,           // seconds between server ticks (0.5Hz)
    correctionFrames: 45,          // frames to blend back to truth on divergence
    divergenceThreshold: 3.0,      // world units — when to trigger correction snap
    maxPredictionDrift: 8.0,       // world units — hard cap on prediction distance
    stateTransitionFrames: 12,     // frames for animated state transitions
  };

  // ─── Entity Behavioral State ────────────────────────────
  class EntityBehavior {
    constructor(id) {
      this.id = id;
      
      // Server truth (last confirmed from tick packet)
      this.server = {
        x: 0, z: 0,
        state: 'IDLE',
        stateVars: {},
        tickTime: performance.now(),
      };

      // Client prediction (updated each frame by behavioral model)
      this.predicted = {
        x: 0, z: 0,
        vx: 0, vz: 0,       // predicted velocity
        state: 'IDLE',
        microBehaviors: {},  // head_bob, ear_twitch, etc.
      };

      // Reconciliation state
      this.blendFactor = 1.0;     // 0.0 = trust server, 1.0 = trust prediction
      this.correctionTimer = 0;   // frames remaining in correction phase
      this.divergenceHistory = []; // recent divergence measurements
      this.lastDivergence = 0;    // last measured divergence distance

      // State transition animation
      this.prevState = 'IDLE';
      this.transitionProgress = 1.0; // 0→1 during transition animation
    }

    /** Initialize from server tick packet */
    initFromServer(x, z, state, stateVars) {
      this.server.x = x;
      this.server.z = z;
      this.server.state = state;
      this.server.stateVars = { ...stateVars };
      this.server.tickTime = performance.now();

      // Seed prediction from server truth
      this.predicted.x = x;
      this.predicted.z = z;
      this.predicted.state = state;
      
      this.prevState = state;
      this.transitionProgress = 1.0;
      this.blendFactor = 1.0;
    }

    /** Reconcile with new server tick — detect divergence and adjust blend */
    reconcile(x, z, state, stateVars) {
      const dx = x - this.predicted.x;
      const dz = z - this.predicted.z;
      const divergence = Math.sqrt(dx * dx + dz * dz);

      this.divergenceHistory.push(divergence);
      if (this.divergenceHistory.length > 10) this.divergenceHistory.shift();
      this.lastDivergence = divergence;

      // State change detection
      const stateChanged = state !== this.predicted.state;

      if (divergence < CONFIG.divergenceThreshold && !stateChanged) {
        // Prediction was accurate — increase trust in model
        this.blendFactor = Math.min(1.0, this.blendFactor + 0.1);
      } else if (divergence > CONFIG.divergenceThreshold * 2 || stateChanged) {
        // Significant divergence or unexpected state change — snap back
        this.blendFactor = Math.max(0.0, this.blendFactor - 0.3);
        this.correctionTimer = CONFIG.correctionFrames;
      } else {
        // Minor divergence — gentle correction
        this.blendFactor = Math.max(0.2, this.blendFactor - 0.05);
        this.correctionTimer = Math.min(this.correctionTimer, 15);
      }

      // Update server truth
      this.prevState = this.server.state;
      this.server.x = x;
      this.server.z = z;
      this.server.state = state;
      this.server.stateVars = { ...stateVars };
      this.server.tickTime = performance.now();

      // Track state transitions for animation
      if (stateChanged) {
        this.transitionProgress = 0;
      }
    }

    /** Run one frame of behavioral prediction */
    predictFrame(dt) {
      const sv = this.server.stateVars;
      const state = this.predicted.state;

      // ─── State-based velocity prediction ──────────────
      let targetSpeed = 0;
      let targetDx = this.predicted.vx;
      let targetDz = this.predicted.vz;

      switch (state) {
        case 'FORAGING':
          targetSpeed = 1.5; // moderate wandering speed
          // Bias toward existing direction with slight random walk
          if (Math.abs(this.predicted.vx) < 0.1 && Math.abs(this.predicted.vz) < 0.1) {
            const angle = Math.random() * Math.PI * 2;
            targetDx = Math.cos(angle);
            targetDz = Math.sin(angle);
          }
          break;

        case 'FLEEING':
          targetSpeed = 4.0; // fast escape
          // Continue in current direction (set by server when fleeing starts)
          if (Math.abs(this.predicted.vx) < 0.1 && Math.abs(this.predicted.vz) < 0.1) {
            const angle = Math.random() * Math.PI * 2;
            targetDx = Math.cos(angle);
            targetDz = Math.sin(angle);
          }
          break;

        case 'DRINKING':
          targetSpeed = 0.5; // slow approach to water
          break;

        case 'RESTING':
          targetSpeed = 0;
          targetDx = 0;
          targetDz = 0;
          break;

        case 'HUNTING':
          targetSpeed = 2.5;
          if (Math.abs(this.predicted.vx) < 0.1 && Math.abs(this.predicted.vz) < 0.1) {
            const angle = Math.random() * Math.PI * 2;
            targetDx = Math.cos(angle);
            targetDz = Math.sin(angle);
          }
          break;

        case 'REPRODUCING':
          targetSpeed = 0.3; // slow, deliberate movement
          break;

        default: // IDLE, WANDERING, etc.
          targetSpeed = 0.8;
          if (Math.abs(this.predicted.vx) < 0.1 && Math.abs(this.predicted.vz) < 0.1) {
            const angle = Math.random() * Math.PI * 2;
            targetDx = Math.cos(angle);
            targetDz = Math.sin(angle);
          }
      }

      // ─── Micro-behaviors (procedural, state-driven) ──
      this.predicted.microBehaviors = this._computeMicroBehaviors(state, sv);

      // ─── Apply predicted movement ─────────────────────
      if (targetSpeed > 0 && (Math.abs(targetDx) > 0.01 || Math.abs(targetDz) > 0.01)) {
        const len = Math.sqrt(targetDx * targetDx + targetDz * targetDz);
        if (len > 0) {
          targetDx /= len;
          targetDz /= len;
        }

        this.predicted.vx += (targetDx - this.predicted.vx) * 0.1; // smooth direction changes
        this.predicted.vz += (targetDz - this.predicted.vz) * 0.1;

        const moveX = this.predicted.vx * targetSpeed * dt;
        const moveZ = this.predicted.vz * targetSpeed * dt;

        this.predicted.x += moveX;
        this.predicted.z += moveZ;
      } else {
        // Decelerate when not moving
        this.predicted.vx *= 0.9;
        this.predicted.vz *= 0.9;
      }

      // ─── Clamp prediction drift ───────────────────────
      const dxFromServer = this.predicted.x - this.server.x;
      const dzFromServer = this.predicted.z - this.server.z;
      const drift = Math.sqrt(dxFromServer * dxFromServer + dzFromServer * dzFromServer);

      if (drift > CONFIG.maxPredictionDrift) {
        // Pull prediction back toward server truth to prevent runaway drift
        const pullFactor = 0.05;
        this.predicted.x -= dxFromServer * pullFactor;
        this.predicted.z -= dzFromServer * pullFactor;
      }

      // ─── State transition animation progress ──────────
      if (this.transitionProgress < 1.0) {
        this.transitionProgress = Math.min(1.0, this.transitionProgress + dt / (CONFIG.stateTransitionFrames / 60));
      }

      // ─── Correction timer countdown ───────────────────
      if (this.correctionTimer > 0) {
        this.correctionTimer--;
      }
    }

    /** Get the blended position for rendering */
    getRenderPosition() {
      const blend = this.blendFactor;
      return {
        x: lerp(this.server.x, this.predicted.x, blend),
        z: lerp(this.server.z, this.predicted.z, blend),
      };
    }

    /** Get the effective state (with transition blending) */
    getEffectiveState() {
      if (this.transitionProgress >= 1.0) return this.predicted.state;
      // During transition, show previous state fading to new state
      return this.transitionProgress < 0.5 ? this.prevState : this.predicted.state;
    }

    /** Compute procedural micro-behaviors based on state and variables */
    _computeMicroBehaviors(state, sv) {
      const now = performance.now() * 0.001;
      const behaviors = {};

      // Breathing rate (all entities)
      const health = sv.health || 1.0;
      behaviors.breathingRate = 0.5 + (1 - health) * 2.0; // faster when stressed
      behaviors.breathingAmp = 0.3 + health * 0.2;

      // Head scanning (FORAGING, HUNTING states)
      if (state === 'FORAGING' || state === 'HUNTING') {
        behaviors.headScan = Math.sin(now * 1.5) * 0.3;
        behaviors.alertness = sv.hunger > 0.5 ? 0.8 : 0.4;
      }

      // Ear twitching (near predators or when FLEEING)
      if (state === 'FLEEING') {
        behaviors.earTwitch = Math.sin(now * 12) * 0.5;
        behaviors.alertness = 1.0;
      } else if (Math.random() < 0.02) {
        // Random occasional twitch
        behaviors.earTwitch = Math.sin(now * 8) * 0.3;
      }

      // Tail wagging (REPRODUCING, IDLE states)
      if (state === 'REPRODUCING' || state === 'IDLE') {
        behaviors.tailWag = Math.sin(now * 2.5) * 0.4;
      }

      // Weight shifting (RESTING state)
      if (state === 'RESTING') {
        behaviors.weightShift = Math.sin(now * 0.3) * 0.15;
      }

      return behaviors;
    }

    /** Get prediction accuracy metric (for debugging/telemetry) */
    getAccuracy() {
      if (this.divergenceHistory.length === 0) return null;
      const avg = this.divergenceHistory.reduce((a, b) => a + b, 0) / this.divergenceHistory.length;
      return {
        avgDivergence: avg.toFixed(2),
        lastDivergence: this.lastDivergence.toFixed(2),
        blendFactor: this.blendFactor.toFixed(2),
        historyLength: this.divergenceHistory.length,
      };
    }
  }

  // ─── State Store (manages all entity behaviors) ────────
  class StateStore {
    constructor() {
      this.behaviors = new Map(); // entityId → EntityBehavior
    }

    /** Process a server tick packet, reconcile all entities */
    processTickPacket(packet) {
      if (!packet.entity_updates) return;

      for (const update of packet.entity_updates) {
        const x = update.position ? update.position[0] : 0;
        const z = update.position ? update.position[2] : 0;
        const state = update.state || 'IDLE';
        const sv = update.state_vars || {};

        let behavior = this.behaviors.get(update.id);
        if (!behavior) {
          behavior = new EntityBehavior(update.id);
          this.behaviors.set(update.id, behavior);
          behavior.initFromServer(x, z, state, sv);
        } else {
          behavior.reconcile(x, z, state, sv);
        }
      }

      // Handle spawns
      if (packet.entity_spawns) {
        for (const spawn of packet.entity_spawns) {
          const x = spawn.position[0];
          const z = spawn.position[2];
          const behavior = new EntityBehavior(spawn.id);
          behavior.initFromServer(x, z, spawn.state || 'IDLE', spawn.state_vars || {});
          this.behaviors.set(spawn.id, behavior);
        }
      }

      // Handle removals
      if (packet.entity_removals) {
        for (const id of packet.entity_removals) {
          this.behaviors.delete(id);
        }
      }
    }

    /** Run one prediction frame for all entities */
    predictAll(dt) {
      for (const behavior of this.behaviors.values()) {
        behavior.predictFrame(dt);
      }
    }

    /** Get blended render position for an entity */
    getRenderPosition(entityId) {
      const behavior = this.behaviors.get(entityId);
      if (!behavior) return null;
      return behavior.getRenderPosition();
    }

    /** Get micro-behaviors for an entity */
    getMicroBehaviors(entityId) {
      const behavior = this.behaviors.get(entityId);
      if (!behavior) return {};
      return behavior.predicted.microBehaviors;
    }

    /** Get prediction accuracy stats (for debug overlay) */
    getAccuracyStats() {
      const stats = [];
      for (const [id, behavior] of this.behaviors.entries()) {
        const acc = behavior.getAccuracy();
        if (acc) stats.push({ id, ...acc });
      }
      return stats;
    }

    /** Get entity count */
    get size() {
      return this.behaviors.size;
    }
  }

  // ─── Utility ──────────────────────────────────────────
  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  // ─── Public API ───────────────────────────────────────
  return {
    StateStore,
    EntityBehavior,
    CONFIG,
  };
})();
