// ═══════════════════════════════════════════════════════
// līlā — Client-Side Agency Engine
//
// Between server ticks, each mobile entity decides what to do based on:
//   - Server intent (state + drives + eligibility flags)
//   - Local perception (nearest food, water, threats from world model)
//   - Motion latent (modulates speed, hesitation, path curvature)
//
// This is the "body" in "server is nervous system, client is body."
// ═══════════════════════════════════════════════════════

import { GRID_SIZE } from './constants.js';

/**
 * Run one frame of local agency for all mobile entities.
 * Called every render frame (~60 Hz).
 */
export function stepAgency(world, dt) {
  const events = []; // client-reported events to send upstream

  for (const ent of world.entities.values()) {
    if (!ent.isMobileConsumer || !ent.isAlive) continue;

    // Update speed from species definition (once)
    if (!ent._speedSet) {
      const def = world.getSpeciesDef(ent.species);
      ent.speed = def?.movement_speed ?? 2.0;
      ent._speedSet = true;
    }

    // Evaluate behavior based on state + drives + eligibility
    const action = evaluateBehavior(ent, world);

    // Execute the action (move toward target, interact, etc.)
    executeAction(ent, action, world, dt, events);
  }

  return events;
}

/**
 * Evaluate what an entity should do based on its intent and local perception.
 * Returns { type, target? } describing the desired action.
 */
function evaluateBehavior(ent, world) {
  const state = ent.state;
  const drive = ent.drive || {};
  const speciesDef = world.getSpeciesDef(ent.species);

  // ── Fleeing (highest priority — threat detected locally) ──
  if (state === 'FLEEING') {
    return evaluateFleeing(ent, world, speciesDef);
  }

  // ── Drinking ──
  if (state === 'DRINKING' || (ent.canDrink && drive.hydration < 0.3)) {
    return evaluateDrinking(ent, world);
  }

  // ── Reproduction seeking ──
  if (ent.reproEligible && drive.reproductive_drive > 0.5) {
    const mateAction = evaluateMateSeeking(ent, world);
    if (mateAction) return mateAction;
  }

  // ── Foraging / Herbivory ──
  if (state === 'FORAGING' && ent.canConsume) {
    return evaluateForaging(ent, world, speciesDef);
  }

  // ── Hunting / Predation ──
  if ((state === 'HUNTING' || state === 'FORAGING') && ent.canPredatate) {
    return evaluateHunting(ent, world, speciesDef);
  }

  // ── Pollination ──
  if (ent.canPollinate && speciesDef?.is_pollinator) {
    return evaluatePollination(ent, world, speciesDef);
  }

  // ── Resting / Idle — wander with latent-modulated style ──
  return evaluateWandering(ent, world);
}

// ─── Behavior Evaluators ──────────────────────────────

function evaluateFleeing(ent, world, speciesDef) {
  const fleeTargets = speciesDef?.flee_targets || [];
  if (fleeTargets.length === 0) return { type: 'wander' };

  // Find nearest threat
  let nearestThreat = null;
  let bestDist = Infinity;
  for (const other of world.entities.values()) {
    if (!other.isAlive) continue;
    const def = world.getSpeciesDef(other.species);
    if (!def || !fleeTargets.includes(other.species)) continue;
    const d2 = ent.distSqTo(other);
    if (d2 < bestDist) {
      bestDist = d2;
      nearestThreat = other;
    }
  }

  if (nearestThreat && bestDist < 400) { // ~20 world units sensory range²
    // Flee away from threat
    const dx = ent.x - nearestThreat.x;
    const dz = ent.z - nearestThreat.z;
    const dist = Math.sqrt(dx * dx + dz * dz) || 1;
    return {
      type: 'flee',
      targetX: clamp(ent.x + (dx / dist) * 8, GRID_SIZE),
      targetZ: clamp(ent.z + (dz / dist) * 8, GRID_SIZE),
    };
  }

  // No threat nearby — fall through to wander
  return { type: 'wander' };
}

function evaluateDrinking(ent, world) {
  const water = world.findNearestWater(ent.x, ent.z);
  if (water) {
    // Approach water source edge
    const wx = water.position[0], wz = water.position[2];
    const r = water.radius || 1;
    const dx = wx - ent.x, dz = wz - ent.z;
    const dist = Math.sqrt(dx * dx + dz * dz) || 1;
    // Approach to just outside the water edge
    const approachR = Math.max(r - 0.5, 0.3);
    return {
      type: 'drink',
      targetX: clamp(wx - (dx / dist) * approachR, GRID_SIZE),
      targetZ: clamp(wz - (dz / dist) * approachR, GRID_SIZE),
    };
  }
  return { type: 'wander' }; // no water found
}

function evaluateMateSeeking(ent, world) {
  const mate = world.findNearestMate(ent);
  if (mate && ent.distanceTo(mate) < 15) {
    return {
      type: 'seek_mate',
      targetX: mate.x,
      targetZ: mate.z,
      targetId: mate.id,
    };
  }
  return null; // no mate nearby, fall through to other behaviors
}

function evaluateForaging(ent, world, speciesDef) {
  const dietOrder = speciesDef?.diet_order || [];

  // Try each food preference in order
  for (const [foodSpecies] of dietOrder) {
    const food = world.findNearestSpecies(ent.x, ent.z, [foodSpecies], ent.id);
    if (food && ent.distanceTo(food) < 15) {
      return {
        type: 'forage',
        targetX: food.x,
        targetZ: food.z,
        targetId: food.id,
      };
    }
  }

  // No preferred food — wander to search
  return { type: 'wander' };
}

function evaluateHunting(ent, world, speciesDef) {
  const dietOrder = speciesDef?.diet_order || [];
  const preySpecies = dietOrder.map(([s]) => s);

  if (preySpecies.length > 0) {
    const prey = world.findNearestSpecies(ent.x, ent.z, preySpecies, ent.id);
    if (prey && ent.distanceTo(prey) < 15) {
      return {
        type: 'hunt',
        targetX: prey.x,
        targetZ: prey.z,
        targetId: prey.id,
      };
    }
  }

  // No prey — wander (or fall back to foraging if omnivore)
  return { type: 'wander' };
}

function evaluatePollination(ent, world, speciesDef) {
  const pollTargets = speciesDef?.pollination_targets || [];
  if (pollTargets.length === 0) return { type: 'wander' };

  // Find nearest FRUITING flower
  for (const flower of world.entities.values()) {
    if (!flower.isAlive) continue;
    if (!pollTargets.includes(flower.species)) continue;
    if (flower.state !== 'FRUITING') continue;
    const dist = ent.distanceTo(flower);
    if (dist < 15 && dist > 0.5) {
      return {
        type: 'pollinate',
        targetX: flower.x,
        targetZ: flower.z,
        targetId: flower.id,
      };
    }
  }

  // No fruiting flowers — wander to search
  return { type: 'wander' };
}

function evaluateWandering(ent, world) {
  // Reuse existing wander target if still valid and not reached
  if (ent.hasTarget && ent._lastActionType === 'wander') {
    const dx = ent.targetX - ent.x;
    const dz = ent.targetZ - ent.z;
    if (Math.sqrt(dx * dx + dz * dz) > 0.5) {
      // Still have distance to current wander target — keep it
      return { type: 'wander', targetX: ent.targetX, targetZ: ent.targetZ };
    }
  }

  const ml = ent.motionLatent || [0, 0, 0, 0];
  const urgency = Math.abs(ml[0]); // dim 0 = pace/urgency

  // Wander range modulated by urgency — high urgency = tighter wander
  const wanderRange = 2 + (1 - urgency) * 4;
  return {
    type: 'wander',
    targetX: clamp(ent.x + (Math.random() - 0.5) * wanderRange * 2, GRID_SIZE),
    targetZ: clamp(ent.z + (Math.random() - 0.5) * wanderRange * 2, GRID_SIZE),
  };
}

// ─── Action Execution ─────────────────────────────────

/**
 * Execute an action for one entity over one frame.
 * Handles movement toward target and interaction triggers.
 */
function executeAction(ent, action, world, dt, events) {
  if (!action || !action.targetX && action.type !== 'wander') return;

  const ml = ent.motionLatent || [0, 0, 0, 0];
  const urgency = (ml[0] + 1) * 0.5; // normalize to 0..1
  const caution = Math.abs(ml[1]);   // dim 1 = alertness

  // Base speed from species or default
  let baseSpeed = ent.speed || 2.0;

  // Modulate speed by action type and latent urgency
  switch (action.type) {
    case 'flee':
      baseSpeed *= 1.5 + urgency * 0.5; // flee fast
      break;
    case 'hunt':
      baseSpeed *= 1.2 + urgency * 0.3;
      break;
    case 'forage':
      baseSpeed *= 0.8 + urgency * 0.4;
      break;
    case 'drink':
      baseSpeed *= 0.7; // approach water calmly
      break;
    case 'seek_mate':
      baseSpeed *= 0.6 + urgency * 0.3;
      break;
    case 'pollinate':
      baseSpeed *= 0.5 + urgency * 0.3; // butterflies are slow
      break;
    default: // wander
      baseSpeed *= 0.3 + (1 - caution) * 0.4;
  }

  // Move toward target
  const dx = action.targetX - ent.x;
  const dz = action.targetZ - ent.z;
  const dist = Math.sqrt(dx * dx + dz * dz);

  if (dist > 0.1) {
    const step = Math.min(baseSpeed * dt, dist);
    // Add slight curvature based on caution (dim 1) — high caution = more wobble
    const wobble = caution * Math.sin(performance.now() * 0.003 + ent.x) * 0.3;

    ent.x += (dx / dist) * step + wobble * dt;
    ent.z += (dz / dist) * step - wobble * dt;
    ent.x = clamp(ent.x, GRID_SIZE);
    ent.z = clamp(ent.z, GRID_SIZE);

    ent.velocityX = (dx / dist) * baseSpeed;
    ent.velocityZ = (dz / dist) * baseSpeed;
  } else {
    // Arrived at target
    ent.velocityX = 0;
    ent.velocityZ = 0;

    // Check for interaction triggers on arrival (with cooldown)
    checkInteraction(ent, action, world, events);

    // Reset wander target so next frame picks a new one
    if (action.type === 'wander') {
      ent.hasTarget = false;
    }
  }

  ent.targetX = action.targetX;
  ent.targetZ = action.targetZ;
  ent._lastActionType = action.type;
  ent.hasTarget = true;
}

/**
 * Check if an entity should trigger an interaction on target arrival.
 * Reports events upstream for server absorption.
 * Uses per-target cooldown to prevent event spam.
 */
function checkInteraction(ent, action, world, events) {
  const targetEnt = action.targetId ? world.entities.get(action.targetId) : null;
  if (!targetEnt || !targetEnt.isAlive) return;

  const dist = ent.distanceTo(targetEnt);
  if (dist > 3.0) return; // too far for any interaction

  // Cooldown: don't re-interact with same target within 2 seconds
  const now = performance.now();
  const key = `${ent.id}:${action.targetId}`;
  if (!ent._interactionCooldowns) ent._interactionCooldowns = {};
  if (ent._interactionCooldowns[key] && now - ent._interactionCooldowns[key] < 2000) {
    return; // still on cooldown
  }

  switch (action.type) {
    case 'forage':
      if (dist < 2.0 && ent.canConsume) {
        events.push({
          type: 'consumption',
          source_id: ent.id,
          target_id: targetEnt.id,
          position: [targetEnt.x, 0, targetEnt.z],
        });
        ent._interactionCooldowns[key] = now;
      }
      break;

    case 'hunt':
      if (dist < 1.5 && ent.canPredatate) {
        events.push({
          type: 'predation',
          source_id: ent.id,
          target_id: targetEnt.id,
          kill_position: [targetEnt.x, 0, targetEnt.z],
        });
        ent._interactionCooldowns[key] = now;
      }
      break;

    case 'pollinate':
      if (dist < 1.5 && ent.canPollinate) {
        events.push({
          type: 'pollination',
          source_id: ent.id,
          target_id: targetEnt.id,
          position: [targetEnt.x, 0, targetEnt.z],
        });
        ent._interactionCooldowns[key] = now;
      }
      break;

    case 'seek_mate':
      if (dist < 3.0 && ent.reproEligible) {
        events.push({
          type: 'repro',
          parent_id: ent.id,
          offspring_count: 1,
          client_position: [ent.x, 0, ent.z],
        });
        ent._interactionCooldowns[key] = now;
      }
      break;
  }
}

// ─── Helpers ──────────────────────────────────────────

function clamp(v, max) {
  return Math.max(0.5, Math.min(max - 0.5, v));
}
