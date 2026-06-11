// ═══════════════════════════════════════════════════════
// līlā — World Model (Client-Side Scene Graph)
//
// Maintains the client's local view of all entities, species
// definitions from server, and provides query methods for
// agency logic (nearest food, nearest water, etc.).
// ═══════════════════════════════════════════════════════

import { GRID_SIZE } from './constants.js';

/**
 * Entity record in the client's local world model.
 * Tracks both server reference position and client-agency position.
 */
export class WorldEntity {
  constructor(id, type = '', species = '') {
    this.id = id;
    this.type = type;       // ANIMAL, PLANT, TREE, INSECT, BIRD, MICROORGANISM
    this.species = species;

    // Server reference position (gravity well for reconciliation)
    this.refX = 0;
    this.refZ = 0;

    // Client-agency position (where the entity actually is in local sim)
    this.x = 0;
    this.z = 0;

    // Discrete state from server
    this.state = 'IDLE';

    // Drive values from server intent packet
    this.drive = {};

    // Motion latent vector (4D, from motor model)
    this.motionLatent = [0, 0, 0, 0];

    // Eligibility flags from server
    this.canConsume = false;
    this.canPredatate = false;
    this.canPollinate = false;
    this.reproEligible = false;
    this.canDrink = false;
    this.spreadEligible = false;

    // Skeleton ID (for future Godot client)
    this.skeletonId = null;

    // Local agency state
    this.targetX = 0;
    this.targetZ = 0;
    this.hasTarget = false;
    this.velocityX = 0;
    this.velocityZ = 0;
    this.speed = 1.0;       // derived from species or default

    // Acknowledgment tracking
    this.ackReceived = false;
  }

  /** Distance to another entity (2D xz-plane). */
  distanceTo(other) {
    const dx = this.x - other.x;
    const dz = this.z - other.z;
    return Math.sqrt(dx * dx + dz * dz);
  }

  /** Distance squared (for comparisons without sqrt). */
  distSqTo(other) {
    const dx = this.x - other.x;
    const dz = this.z - other.z;
    return dx * dx + dz * dz;
  }

  /** Is this entity alive and active? */
  get isAlive() {
    return !['DEAD', 'DYING', 'DORMANT'].includes(this.state);
  }

  /** Is this a mobile consumer (animal, bird, insect)? */
  get isMobileConsumer() {
    return ['ANIMAL', 'BIRD', 'INSECT'].includes(this.type);
  }
}

/**
 * World model — the client's local scene graph.
 * Provides entity lookup, spatial queries, and species definitions.
 */
export class WorldModel {
  constructor() {
    this.entities = new Map();       // id → WorldEntity
    this.speciesDefs = {};           // species_id → server-provided definition
    this.waterSources = [];          // [{ position, radius, water_level }]
    this.moisture = new Float32Array(GRID_SIZE * GRID_SIZE).fill(0.65);
  }

  // ─── Entity Management ──────────────────────────────

  /** Add or update an entity from a server tick packet. */
  applyUpdate(u) {
    let ent = this.entities.get(u.id);
    if (!ent) {
      const info = inferEntityTypeFromId(u.id);
      ent = new WorldEntity(u.id, info.type, info.species);
      // Initialize client position from server reference on first sight
      if (u.ref_position) {
        ent.x = u.ref_position[0];
        ent.z = u.ref_position[2];
        ent.refX = u.ref_position[0];
        ent.refZ = u.ref_position[2];
      }
      this.entities.set(u.id, ent);
    }

    // Update server reference position (gravity well)
    if (u.ref_position) {
      ent.refX = u.ref_position[0];
      ent.refZ = u.ref_position[2];
    }

    // Update state and drives from intent packet
    if (u.state) ent.state = u.state;
    if (u.drive) Object.assign(ent.drive, u.drive);
    if (u.motion_latent) ent.motionLatent = u.motion_latent;

    // Eligibility flags
    ent.canConsume = !!u._can_consume;
    ent.canPredatate = !!u._can_predate;
    ent.canPollinate = !!u._can_pollinate;
    ent.reproEligible = !!u._repro_eligible;
    ent.canDrink = !!u._can_drink;
    ent.spreadEligible = !!u._spread_eligible;

    // Acknowledgment tracking
    if (u._ack) {
      ent.ackReceived = true;
    } else {
      ent.ackReceived = false;
    }

    return ent;
  }

  /** Spawn a new entity from server spawn packet. */
  applySpawn(s) {
    const ent = new WorldEntity(
      s.id, s.type || '', s.species || ''
    );
    ent.refX = s.ref_position[0];
    ent.refZ = s.ref_position[2];
    // Client starts at server reference position for new entities
    ent.x = s.ref_position[0];
    ent.z = s.ref_position[2];
    if (s.state) ent.state = s.state;
    if (s.drive) Object.assign(ent.drive, s.drive);
    if (s.skeleton_id) ent.skeletonId = s.skeleton_id;
    this.entities.set(s.id, ent);
    return ent;
  }

  /** Remove an entity. */
  applyRemoval(id) {
    this.entities.delete(id);
  }

  // ─── Spatial Queries (for agency logic) ─────────────

  /** Find nearest alive entity of given type(s) from a position. */
  findNearest(x, z, types, excludeId = null) {
    let bestDist = Infinity;
    let bestEnt = null;
    for (const ent of this.entities.values()) {
      if (!ent.isAlive) continue;
      if (excludeId && ent.id === excludeId) continue;
      if (!types.includes(ent.type)) continue;
      const d2 = (x - ent.x) ** 2 + (z - ent.z) ** 2;
      if (d2 < bestDist) {
        bestDist = d2;
        bestEnt = ent;
      }
    }
    return bestEnt;
  }

  /** Find nearest alive entity of given species from a position. */
  findNearestSpecies(x, z, speciesList, excludeId = null) {
    let bestDist = Infinity;
    let bestEnt = null;
    for (const ent of this.entities.values()) {
      if (!ent.isAlive) continue;
      if (excludeId && ent.id === excludeId) continue;
      if (!speciesList.includes(ent.species)) continue;
      const d2 = (x - ent.x) ** 2 + (z - ent.z) ** 2;
      if (d2 < bestDist) {
        bestDist = d2;
        bestEnt = ent;
      }
    }
    return bestEnt;
  }

  /** Find nearest non-dry water source from a position. */
  findNearestWater(x, z) {
    let bestDist = Infinity;
    let bestSource = null;
    for (const ws of this.waterSources) {
      if ((ws.water_level ?? 1.0) < 0.05) continue; // dry
      const d2 = (x - ws.position[0]) ** 2 + (z - ws.position[2]) ** 2;
      if (d2 < bestDist) {
        bestDist = d2;
        bestSource = ws;
      }
    }
    return bestSource;
  }

  /** Find nearest mate (same species, different sex — approximated by ID). */
  findNearestMate(ent) {
    let bestDist = Infinity;
    let bestEnt = null;
    for (const other of this.entities.values()) {
      if (!other.isAlive) continue;
      if (other.id === ent.id) continue;
      if (other.species !== ent.species) continue;
      // Simple heuristic: different IDs of same species are potential mates
      const d2 = ent.distSqTo(other);
      if (d2 < bestDist) {
        bestDist = d2;
        bestEnt = other;
      }
    }
    return bestEnt;
  }

  /** Get all alive entities of a given type. */
  getAliveOfType(type) {
    const result = [];
    for (const ent of this.entities.values()) {
      if (ent.isAlive && ent.type === type) result.push(ent);
    }
    return result;
  }

  /** Get species definition from server-provided reference. */
  getSpeciesDef(speciesId) {
    return this.speciesDefs[speciesId] || null;
  }

  // ─── Voxel / Environment ────────────────────────────

  applyVoxelDeltas(deltas) {
    if (deltas && deltas.moisture) {
      for (const [coord, val] of Object.entries(deltas.moisture)) {
        const parts = coord.split(',').map(Number);
        const x = parts[0], z = parts[2];
        if (x >= 0 && x < GRID_SIZE && z >= 0 && z < GRID_SIZE) {
          this.moisture[z * GRID_SIZE + x] = val;
        }
      }
    }
  }

  applyWaterSources(sources) {
    this.waterSources = sources || [];
  }
}

// ─── Helpers ──────────────────────────────────────────

/** Infer entity type from ID prefix (fallback when server data is sparse). */
function inferEntityTypeFromId(id) {
  if (id.startsWith('deer'))      return { type: 'ANIMAL', species: 'deer' };
  if (id.startsWith('wolf'))      return { type: 'ANIMAL', species: 'wolf' };
  if (id.startsWith('bird') || id.startsWith('songbird')) return { type: 'BIRD', species: 'songbird' };
  if (id.startsWith('butterfly')) return { type: 'INSECT', species: 'monarch' };
  if (id.startsWith('oak'))       return { type: 'TREE',  species: 'meadow_oak' };
  if (id.startsWith('grass'))     return { type: 'PLANT', species: 'meadow_grass' };
  if (id.startsWith('flower'))    return { type: 'PLANT', species: 'wildflower' };
  if (id.startsWith('mushroom') || id.startsWith('fungus')) return { type: 'MICROORGANISM', species: 'mushroom' };
  return { type: 'ANIMAL', species: 'unknown' };
}
