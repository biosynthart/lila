// ═══════════════════════════════════════════════════════
// līlā — Main Entry Point
//
// Sets up WebSocket connection, manages the render loop,
// and coordinates between world model, agency engine,
// heartbeat sender, and renderer.
// ═══════════════════════════════════════════════════════

import { CANVAS_SIZE, COLORS, MAX_EVENT_LOG } from './constants.js';
import { WorldModel } from './world-model.js';
import { stepAgency } from './agency.js';
import { HeartbeatSender } from './heartbeat.js';
import { reconcile } from './reconciliation.js';
import * as renderer from './renderer.js';
import * as particles from './particles.js';

// ─── Canvas Setup ─────────────────────────────────────

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
canvas.width = CANVAS_SIZE;
canvas.height = CANVAS_SIZE;

// ─── State ────────────────────────────────────────────

let ws = null;
let connected = false;
let currentTick = 0;
let totalEvents = 0;
const world = new WorldModel();

// FPS tracking
let frameCount = 0;
let lastFpsTime = performance.now();
let displayFps = 0;

// Event log
const eventLog = [];

// ─── WebSocket ────────────────────────────────────────

function connect() {
  const wsUrl = `ws://${location.host}/ws`;
  setStatus('connecting');

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    setStatus('connected');

    // Init heartbeat sender now that WS is open
    heartbeatSender = new HeartbeatSender(ws);

    fetch('/world.json')
      .then(r => r.json())
      .then(worldDef => { ws.send(JSON.stringify(worldDef)); })
      .catch(() => {
        console.warn('Could not load world.json, using minimal world');
        ws.send(JSON.stringify(buildMinimalWorld()));
      });
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'session_started') {
      onSessionStarted(data);
      return;
    }
    handleTickPacket(data);
  };

  ws.onclose = () => {
    setStatus('disconnected');
    connected = false;
    setTimeout(connect, 3000);
  };

  ws.onerror = () => { ws.close(); };
}

function onSessionStarted(data) {
  console.log('Session started:', data);
  logEvent(`▸ ecosystem alive — ${data.entity_count} entities`);

  // Store species definitions from server for client-side agency
  if (data.species) {
    world.speciesDefs = data.species;
  }
}

function setStatus(state) {
  const el = document.getElementById('status');
  const dot = state === 'connected' ? 'connected'
    : state === 'connecting' ? 'connecting' : 'disconnected';
  el.innerHTML = `<span class="status-dot ${dot}"></span>${state}`;
  connected = state === 'connected';
}

// ─── Tick Packet Processing ──────────────────────────

function handleTickPacket(packet) {
  currentTick = packet.tick || 0;

  // Entity updates (intent-based format)
  if (packet.entity_updates) {
    for (const u of packet.entity_updates) {
      world.applyUpdate(u);
    }
  }

  // Spawns
  if (packet.entity_spawns) {
    for (const s of packet.entity_spawns) {
      world.applySpawn(s);
    }
  }

  // Removals
  if (packet.entity_removals) {
    for (const id of packet.entity_removals) {
      const ent = world.entities.get(id);
      if (ent) particles.spawnParticles(ent.x, ent.z, COLORS.death, 6, 40);
      world.applyRemoval(id);
    }
  }

  // Voxel deltas (moisture for heatmap)
  if (packet.voxel_deltas) {
    world.applyVoxelDeltas(packet.voxel_deltas);
  }

  // Water sources
  if (packet.water_sources && packet.water_sources.length > 0) {
    world.applyWaterSources(packet.water_sources);
  }

  // Events from server
  if (packet.events) {
    for (const ev of packet.events) {
      totalEvents++;
      handleEvent(ev);
    }
  }

  // Reconcile client positions with server references
  reconcile(world);

  // Update UI
  document.getElementById('tick').textContent = currentTick;
  document.getElementById('entities').textContent = world.entities.size;
  document.getElementById('event-count').textContent = totalEvents;
}

function handleEvent(ev) {
  const pos = ev.position || [0, 0, 0];
  const x = pos[0], z = pos[2];

  switch (ev.type) {
    case 'CONSUMPTION':
      particles.spawnParticles(x, z, COLORS.consumption, 5, 30);
      logEvent(`🌿 ${ev.source_id} grazes ${ev.target_id}`);
      break;
    case 'POLLINATION':
      particles.spawnParticles(x, z, COLORS.pollination, 8, 50);
      logEvent(`🦋 ${ev.source_id} pollinates ${ev.target_id}`);
      break;
    case 'DEATH_NATURAL':
    case 'DEATH_STARVE':
      particles.spawnParticles(x, z, COLORS.death, 10, 60);
      logEvent(`💀 ${ev.source_id} dies`);
      break;
    case 'STATE_CHANGE':
      logEvent(`◇ ${ev.source_id}: ${ev.prev_state} → ${ev.new_state}`);
      break;
  }
}

function logEvent(text) {
  eventLog.unshift({ text, time: performance.now() });
  if (eventLog.length > MAX_EVENT_LOG) eventLog.pop();

  const panel = document.getElementById('events-panel');
  panel.innerHTML = eventLog.map((e) => {
    const age = (performance.now() - e.time) / 1000;
    const fresh = age < 2 ? ' fresh' : '';
    return `<div class="event-line${fresh}">${e.text}</div>`;
  }).join('');
}

// ─── Heartbeat Sender ────────────────────────────────

let heartbeatSender = null;
// Initialized in ws.onopen alongside world def fetch

// ─── Rain Control ─────────────────────────────────────

document.getElementById('rain-btn').addEventListener('click', () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: 'rain', intensity: 0.8 }));

  const btn = document.getElementById('rain-btn');
  btn.classList.add('raining');
  btn.textContent = '☔ raining...';
  setTimeout(() => {
    btn.classList.remove('raining');
    btn.textContent = '☔ rain';
  }, 1500);

  logEvent('🌧 rainfall — moisture replenished');
});

// ─── Recording ────────────────────────────────────────

let mediaRecorder = null;
let recordedChunks = [];

document.getElementById('record-btn').addEventListener('click', () => {
  const btn = document.getElementById('record-btn');

  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    return;
  }

  const stream = canvas.captureStream(30);
  const codecs = [
    'video/webm; codecs=vp9',
    'video/webm; codecs=vp8',
    'video/webm',
    'video/mp4',
  ];
  const mimeType = codecs.find(c => MediaRecorder.isTypeSupported(c)) || '';
  const ext = mimeType.includes('mp4') ? 'mp4' : 'webm';

  mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
  recordedChunks = [];

  mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) recordedChunks.push(e.data);
  };

  mediaRecorder.onstop = () => {
    const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `lila-recording.${ext}`;
    a.click();
    URL.revokeObjectURL(url);

    btn.classList.remove('recording');
    btn.textContent = '⏺ record';
    logEvent('⏹ recording saved');
  };

  mediaRecorder.start();
  btn.classList.add('recording');
  btn.textContent = '⏹ recording...';
  logEvent('⏺ recording started (10s)');

  setTimeout(() => {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      mediaRecorder.stop();
    }
  }, 10000);
});

// ─── Render Loop ──────────────────────────────────────

let lastFrameTime = performance.now();

function render() {
  const now = performance.now();
  const dt = Math.min((now - lastFrameTime) / 1000, 0.05); // cap at 50ms
  lastFrameTime = now;

  // FPS counter
  frameCount++;
  if (now - lastFpsTime > 1000) {
    displayFps = frameCount;
    frameCount = 0;
    lastFpsTime = now;
    document.getElementById('fps').textContent = displayFps;
  }

  // ── Step local agency (60 Hz, between server ticks) ──
  const clientEvents = stepAgency(world, dt);
  if (heartbeatSender && clientEvents.length > 0) {
    heartbeatSender.queueEvents(clientEvents);
  }

  // ── Send heartbeat if interval elapsed ──
  if (heartbeatSender) {
    heartbeatSender.trySend(world, now);
  }

  // ── Render frame ──
  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);

  renderer.drawMoistureHeatmap(ctx, world.moisture);
  renderer.drawWater(ctx, world.waterSources);
  renderer.drawGrid(ctx);
  renderer.drawEntities(ctx, world);
  renderer.drawParticles(ctx);

  particles.updateParticles();

  requestAnimationFrame(render);
}

// ─── Minimal World Fallback ──────────────────────────

function buildMinimalWorld() {
  return {
    version: '0.1',
    session_id: 'browser-viz-001',
    environment: {
      type: 'MEADOW', biome: 'TEMPERATE',
      climate: { temperature: 22, humidity: 0.6, rainfall: 0.4, wind_speed: 0.15, light_level: 0.85 },
      soil: { nitrogen: 0.7, phosphorus: 0.6, potassium: 0.5, moisture: 0.65, organic_matter: 0.4, ph: 6.8 },
      voxel_grid: { dimensions: [32, 32, 32], cell_size: 1.0 },
    },
    model: { adapter: 'mlp', seed: 42 },
    entities: [
      { id: 'deer_01', type: 'ANIMAL', species: 'deer', position: [16, 0, 14],
        metadata: { diet: 'herbivore', body_mass: 60, metabolism_rate: 1, sensory_range: 12, movement_speed: 3, lifespan: 800, reproduction_threshold: 0.8 },
        skeleton_id: 'quadruped_medium' },
    ],
  };
}

// ─── Start ────────────────────────────────────────────

connect();
requestAnimationFrame(render);
