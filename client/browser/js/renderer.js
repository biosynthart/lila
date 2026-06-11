// ═══════════════════════════════════════════════════════
// līlā — Renderer (Canvas Drawing)
// ═══════════════════════════════════════════════════════

import { GRID_SIZE, CELL_PX, PADDING, CANVAS_SIZE, COLORS } from './constants.js';
import { getParticles } from './particles.js';

// Song ring particles keyed by entity id
const birdSongRings = new Map();

export function worldToCanvas(wx, wz) {
  return [PADDING + wx * CELL_PX, PADDING + wz * CELL_PX];
}

export function drawGrid(ctx) {
  for (let i = 0; i <= GRID_SIZE; i++) {
    const major = i % 8 === 0;
    ctx.strokeStyle = major ? COLORS.gridMajor : COLORS.grid;
    ctx.lineWidth = major ? 0.5 : 0.3;
    const p = PADDING + i * CELL_PX;

    ctx.beginPath();
    ctx.moveTo(p, PADDING);
    ctx.lineTo(p, PADDING + GRID_SIZE * CELL_PX);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(PADDING, p);
    ctx.lineTo(PADDING + GRID_SIZE * CELL_PX, p);
    ctx.stroke();
  }
}

export function drawMoistureHeatmap(ctx, moisture) {
  for (let z = 0; z < GRID_SIZE; z++) {
    for (let x = 0; x < GRID_SIZE; x++) {
      const val = moisture[z * GRID_SIZE + x];
      const [cx, cz] = worldToCanvas(x, z);

      let r, g, b;
      if (val > 0.5) {
        const f = (val - 0.5) * 2;
        r = lerp(COLORS.moistureMid[0], COLORS.moistureHigh[0], f);
        g = lerp(COLORS.moistureMid[1], COLORS.moistureHigh[1], f);
        b = lerp(COLORS.moistureMid[2], COLORS.moistureHigh[2], f);
      } else {
        const f = val * 2;
        r = lerp(COLORS.moistureLow[0], COLORS.moistureMid[0], f);
        g = lerp(COLORS.moistureLow[1], COLORS.moistureMid[1], f);
        b = lerp(COLORS.moistureLow[2], COLORS.moistureMid[2], f);
      }

      ctx.fillStyle = `rgb(${r|0},${g|0},${b|0})`;
      ctx.fillRect(cx, cz, CELL_PX, CELL_PX);
    }
  }
}

export function drawWater(ctx, waterSources) {
  const now = performance.now();

  for (const ws of waterSources) {
    const [wx, , wz] = ws.position;
    const [cx, cz] = worldToCanvas(wx, wz);
    const centerX = cx + CELL_PX / 2;
    const centerZ = cz + CELL_PX / 2;
    const radiusPx = ws.radius * CELL_PX;
    const level = ws.water_level !== undefined ? ws.water_level : 1.0;

    if (level < 0.02 || radiusPx < 1) continue;

    const alpha = 0.3 + level * 0.4;

    // Outer soft glow
    const glowR = radiusPx * 1.8;
    const glow = ctx.createRadialGradient(centerX, centerZ, radiusPx * 0.5, centerX, centerZ, glowR);
    glow.addColorStop(0, `rgba(${COLORS.waterFill[0]}, ${COLORS.waterFill[1]}, ${COLORS.waterFill[2]}, ${alpha * 0.8})`);
    glow.addColorStop(0.6, `rgba(${COLORS.waterFill[0]}, ${COLORS.waterFill[1]}, ${COLORS.waterFill[2]}, ${alpha * 0.5})`);
    glow.addColorStop(1, 'rgba(45, 85, 110, 0)');
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(centerX, centerZ, glowR, 0, Math.PI * 2);
    ctx.fill();

    // Main water body
    const waterGrad = ctx.createRadialGradient(centerX - radiusPx * 0.2, centerZ - radiusPx * 0.2, 0, centerX, centerZ, radiusPx);
    waterGrad.addColorStop(0, `rgba(${COLORS.waterShine[0]}, ${COLORS.waterShine[1]}, ${COLORS.waterShine[2]}, ${alpha * 0.95})`);
    waterGrad.addColorStop(0.6, `rgba(${COLORS.waterFill[0]}, ${COLORS.waterFill[1]}, ${COLORS.waterFill[2]}, ${alpha * 0.9})`);
    waterGrad.addColorStop(1, `rgba(${COLORS.waterEdge[0]}, ${COLORS.waterEdge[1]}, ${COLORS.waterEdge[2]}, ${alpha * 0.7})`);
    ctx.fillStyle = waterGrad;
    ctx.beginPath();
    ctx.arc(centerX, centerZ, radiusPx, 0, Math.PI * 2);
    ctx.fill();

    // Edge ring
    ctx.strokeStyle = `rgba(${COLORS.waterEdge[0]}, ${COLORS.waterEdge[1]}, ${COLORS.waterEdge[2]}, ${alpha * 0.4})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(centerX, centerZ, radiusPx, 0, Math.PI * 2);
    ctx.stroke();

    // Animated ripples (only when water is above 30%)
    if (level > 0.3) {
      for (let i = 0; i < 2; i++) {
        const phase = (now * 0.001 + i * 1.8) % 3.0;
        const rippleR = radiusPx * 0.3 + phase * radiusPx * 0.25;
        const rippleAlpha = Math.max(0, (0.15 - phase * 0.05) * level);
        ctx.strokeStyle = `rgba(${COLORS.waterShine[0]}, ${COLORS.waterShine[1]}, ${COLORS.waterShine[2]}, ${rippleAlpha})`;
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.arc(centerX + Math.sin(i * 4.7) * radiusPx * 0.2, centerZ + Math.cos(i * 4.7) * radiusPx * 0.15, rippleR, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }
}

export function drawEntities(ctx, world) {
  const layers = [
    { types: ['TREE'], draw: drawTree },
    { types: ['PLANT'], species: ['meadow_grass'], draw: drawGrass },
    { types: ['PLANT'], species: ['wildflower'], draw: drawFlower },
    { types: ['MICROORGANISM'], draw: drawMushroom },
    { types: ['ANIMAL'], draw: drawDeer },
    { types: ['BIRD'], draw: drawBird },
    { types: ['INSECT'], draw: drawButterfly },
  ];

  for (const layer of layers) {
    for (const ent of world.entities.values()) {
      if (!ent.isAlive && ent.state !== 'DORMANT') continue;
      if (!layer.types.includes(ent.type)) continue;
      if (layer.species && !layer.species.includes(ent.species)) continue;

      // Guard against NaN positions
      if (typeof ent.x !== 'number' || typeof ent.z !== 'number') continue;
      if (isNaN(ent.x) || isNaN(ent.z)) continue;

      const [cx, cz] = worldToCanvas(ent.x, ent.z);
      layer.draw(ctx, cx, cz, ent);
    }
  }
}

export function drawParticles(ctx) {
  for (const p of getParticles()) {
    const [cx, cz] = worldToCanvas(p.x, p.z);
    const alpha = p.life / p.maxLife;

    ctx.fillStyle = p.color;
    ctx.globalAlpha = alpha * 0.8;
    ctx.beginPath();
    ctx.arc(cx, cz, p.size * alpha, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1.0;
}

// ─── Entity Draw Functions ────────────────────────────

function drawTree(ctx, cx, cz, ent) {
  const growth = ent.drive?.growth ?? ent.state_vars?.growth ?? 0.5;
  const canopyR = 4.0 * CELL_PX * growth * 0.5;

  ctx.fillStyle = COLORS.oakCanopy;
  ctx.beginPath();
  ctx.arc(cx, cz, canopyR, 0, Math.PI * 2);
  ctx.fill();

  const trunkR = 3 + growth * 3;
  ctx.fillStyle = COLORS.oak;
  ctx.beginPath();
  ctx.arc(cx, cz, trunkR, 0, Math.PI * 2);
  ctx.fill();

  ctx.strokeStyle = 'rgba(61, 107, 61, 0.3)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(cx, cz, trunkR + 1, 0, Math.PI * 2);
  ctx.stroke();
}

function drawGrass(ctx, cx, cz, ent) {
  if (ent.state === 'DORMANT') {
    ctx.fillStyle = '#5a5244';
    ctx.globalAlpha = 0.3;
    ctx.beginPath();
    ctx.arc(cx, cz, 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1.0;
    return;
  }

  const growth = ent.drive?.growth ?? 0.1;
  const hydration = ent.drive?.hydration ?? 0.5;
  const size = 2 + growth * 4;
  const color = hydration > 0.3 ? COLORS.grass : COLORS.grassWilt;

  ctx.fillStyle = color;
  ctx.globalAlpha = 0.5 + growth * 0.5;

  for (let i = 0; i < 3; i++) {
    const ox = Math.sin(i * 2.1) * 3;
    const oz = Math.cos(i * 2.1) * 3;
    ctx.beginPath();
    ctx.arc(cx + ox, cz + oz, size * 0.6, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1.0;
}

function drawFlower(ctx, cx, cz, ent) {
  if (ent.state === 'DORMANT') {
    ctx.fillStyle = '#6b5e3d';
    ctx.globalAlpha = 0.3;
    ctx.beginPath();
    ctx.arc(cx, cz, 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1.0;
    return;
  }

  const growth = ent.drive?.growth ?? 0.1;
  const isFruiting = ent.state === 'FRUITING';

  ctx.fillStyle = COLORS.wildflower;
  ctx.beginPath();
  ctx.arc(cx, cz, 2, 0, Math.PI * 2);
  ctx.fill();

  if (isFruiting) {
    const pulse = 0.7 + Math.sin(performance.now() * 0.004) * 0.3;
    const bloomR = 4 + growth * 3;

    ctx.fillStyle = COLORS.flowerBloom;
    ctx.globalAlpha = pulse;
    ctx.beginPath();
    ctx.arc(cx, cz, bloomR, 0, Math.PI * 2);
    ctx.fill();

    ctx.globalAlpha = pulse * 0.2;
    ctx.beginPath();
    ctx.arc(cx, cz, bloomR * 2, 0, Math.PI * 2);
    ctx.fill();

    ctx.globalAlpha = 1.0;
  }
}

function drawMushroom(ctx, cx, cz, ent) {
  const activity = ent.drive?.activity ?? 0.5;
  const size = 2 + activity * 3;

  ctx.fillStyle = `rgba(160, 140, 120, ${0.3 + activity * 0.4})`;
  ctx.beginPath();
  ctx.arc(cx, cz, size, 0, Math.PI * 2);
  ctx.fill();
}

function drawDeer(ctx, cx, cz, ent) {
  const state = ent.state || 'IDLE';
  const hunger = ent.drive?.hunger ?? 0;
  const size = state === 'RESTING' ? 5 : 7;

  // Direction from velocity
  const dx = ent.velocityX || 0;
  const dz = ent.velocityZ || 0;
  const angle = Math.atan2(dz, dx);

  ctx.save();
  ctx.translate(cx, cz);
  ctx.rotate(angle);

  // Body (triangle pointing in movement direction)
  ctx.fillStyle = COLORS.deer;
  ctx.beginPath();
  ctx.moveTo(size, 0);
  ctx.lineTo(-size * 0.7, -size * 0.5);
  ctx.lineTo(-size * 0.7, size * 0.5);
  ctx.closePath();
  ctx.fill();

  // Head dot
  ctx.fillStyle = COLORS.deerHead;
  ctx.beginPath();
  ctx.arc(size * 0.6, 0, 2, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();

  // State indicator ring
  if (state === 'DRINKING') {
    ctx.strokeStyle = 'rgba(90, 140, 180, 0.5)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cz, size + 3, 0, Math.PI * 2);
    ctx.stroke();
  } else if (state === 'RESTING') {
    ctx.strokeStyle = 'rgba(180, 170, 140, 0.3)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.arc(cx, cz, size + 2, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Motion latent visualization (subtle halo)
  if (ent.motionLatent && ent.skeletonId) {
    const ml = ent.motionLatent;
    const intensity = (Math.abs(ml[0]) + Math.abs(ml[2])) * 0.3;
    ctx.strokeStyle = `rgba(196, 149, 106, ${intensity * 0.2})`;
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.arc(cx, cz, size + 5, 0, Math.PI * 2);
    ctx.stroke();
  }

  // Label
  ctx.fillStyle = COLORS.label;
  ctx.font = '8px JetBrains Mono';
  ctx.textAlign = 'center';
  ctx.fillText(state.toLowerCase(), cx, cz + size + 10);
}

function drawBird(ctx, cx, cz, ent) {
  const state = ent.state || 'IDLE';
  const time = performance.now() * 0.001;

  const dx = ent.velocityX || 0;
  const dz = ent.velocityZ || 0;
  const angle = Math.atan2(dz, dx);

  ctx.save();
  ctx.translate(cx, cz);
  ctx.rotate(angle);

  let flapSpeed = 6;
  let flapAmp = 0.45;
  if (state === 'HUNTING' || state === 'FLEEING') {
    flapSpeed = 14; flapAmp = 0.6;
  } else if (state === 'FORAGING') {
    flapSpeed = 4; flapAmp = 0.35;
  } else if (state === 'RESTING' || state === 'DRINKING') {
    flapSpeed = 1; flapAmp = 0.1;
  }

  const wingAngle = Math.sin(time * flapSpeed + cx * 0.3) * flapAmp;

  // Body — teardrop
  const bodyLen = 6, bodyWid = 2.5;
  ctx.fillStyle = COLORS.bird;
  ctx.beginPath();
  ctx.moveTo(bodyLen, 0);
  ctx.quadraticCurveTo(bodyLen * 0.3, -bodyWid, -bodyLen * 0.6, 0);
  ctx.quadraticCurveTo(bodyLen * 0.3, bodyWid, bodyLen, 0);
  ctx.fill();

  // Tail — dovetail
  const tailSpread = state === 'FLEEING' ? 4 : 2.5;
  const tailBaseX = -bodyLen * 0.5;
  const tailTipX = -bodyLen * 0.9;
  ctx.fillStyle = COLORS.birdTail;
  ctx.beginPath();
  ctx.moveTo(tailBaseX, -tailSpread);
  ctx.lineTo(tailTipX, -tailSpread * 0.3);
  ctx.lineTo(tailTipX + 1, 0);
  ctx.lineTo(tailTipX, tailSpread * 0.3);
  ctx.lineTo(tailBaseX, tailSpread);
  ctx.closePath();
  ctx.fill();

  // Wings
  const wingLen = 7, wingBaseX = 0.5;
  ctx.strokeStyle = COLORS.bird;
  ctx.lineWidth = 1.2;
  ctx.globalAlpha = 0.6 + Math.abs(Math.sin(time * flapSpeed)) * 0.4;

  ctx.beginPath();
  ctx.moveTo(wingBaseX, -bodyWid * 0.5);
  const uwEndX = wingBaseX - wingLen * Math.cos(wingAngle);
  const uwEndY = -wingLen * Math.sin(Math.abs(wingAngle) + 0.3);
  ctx.quadraticCurveTo(wingBaseX - wingLen * 0.4, -bodyWid - wingLen * 0.5, uwEndX, uwEndY);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(wingBaseX, bodyWid * 0.5);
  const lwEndX = wingBaseX - wingLen * Math.cos(-wingAngle);
  const lwEndY = wingLen * Math.sin(Math.abs(wingAngle) + 0.3);
  ctx.quadraticCurveTo(wingBaseX - wingLen * 0.4, bodyWid + wingLen * 0.5, lwEndX, lwEndY);
  ctx.stroke();

  ctx.globalAlpha = 1;
  ctx.restore();

  // Song rings (IDLE / REPRODUCING)
  if (state === 'IDLE' || state === 'REPRODUCING') {
    let rings = birdSongRings.get(ent.id);
    if (!rings) { rings = []; birdSongRings.set(ent.id, rings); }

    const spawnInterval = 1200 + (ent.id.charCodeAt(ent.id.length - 1) % 600);
    if (!rings.lastSpawn || time * 1000 - rings.lastSpawn > spawnInterval) {
      rings.push({ birth: time, maxAge: 2.5 });
      rings.lastSpawn = time * 1000;
    }

    for (let i = rings.length - 1; i >= 0; i--) {
      const ring = rings[i];
      const age = time - ring.birth;
      if (age > ring.maxAge) { rings.splice(i, 1); continue; }
      const progress = age / ring.maxAge;
      const radius = 4 + progress * 20;
      const alpha = (1 - progress) * 0.35;

      ctx.strokeStyle = COLORS.birdSong + alpha + ')';
      ctx.lineWidth = 1 - progress * 0.6;
      ctx.beginPath();
      ctx.arc(cx, cz, radius, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (rings.length === 0) birdSongRings.delete(ent.id);
  } else {
    birdSongRings.delete(ent.id);
  }

  // State indicator ring
  if (state === 'HUNTING') {
    ctx.strokeStyle = 'rgba(180, 120, 90, 0.4)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cz, 10, 0, Math.PI * 2);
    ctx.stroke();
  } else if (state === 'DRINKING') {
    ctx.strokeStyle = 'rgba(90, 140, 180, 0.5)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cz, 9, 0, Math.PI * 2);
    ctx.stroke();
  } else if (state === 'RESTING') {
    ctx.strokeStyle = 'rgba(140, 130, 110, 0.25)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.arc(cx, cz, 8, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Label
  ctx.fillStyle = COLORS.label;
  ctx.font = '7px JetBrains Mono';
  ctx.textAlign = 'center';
  ctx.fillText(state.toLowerCase(), cx, cz + 14);
}

function drawButterfly(ctx, cx, cz, ent) {
  const time = performance.now() * 0.008;
  const wingFlap = Math.sin(time + cx * 0.1) * 0.5 + 0.5;

  ctx.save();
  ctx.translate(cx, cz);

  const wingSpan = 5;
  const wingH = 3 * (0.5 + wingFlap * 0.5);

  ctx.fillStyle = COLORS.butterfly;
  ctx.globalAlpha = 0.7 + wingFlap * 0.3;

  // Left wing
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(-wingSpan, -wingH);
  ctx.lineTo(-wingSpan * 0.3, -wingH * 0.5);
  ctx.closePath();
  ctx.fill();

  // Right wing
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(wingSpan, -wingH);
  ctx.lineTo(wingSpan * 0.3, -wingH * 0.5);
  ctx.closePath();
  ctx.fill();

  // Body
  ctx.globalAlpha = 1;
  ctx.fillStyle = COLORS.butterflyBody;
  ctx.beginPath();
  ctx.arc(0, 0, 1.5, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();

  // Pollinating glow
  if (ent.state === 'POLLINATING') {
    ctx.fillStyle = 'rgba(196, 166, 74, 0.15)';
    ctx.beginPath();
    ctx.arc(cx, cz, 10, 0, Math.PI * 2);
    ctx.fill();
  }
}

// ─── Utility ──────────────────────────────────────────

function lerp(a, b, t) {
  return a + (b - a) * t;
}
