// ═══════════════════════════════════════════════════════
// līlā — Particle System (Event Visualizations)
// ═══════════════════════════════════════════════════════

const particles = [];

export function spawnParticles(worldX, worldZ, color, count, lifetime) {
  for (let i = 0; i < count; i++) {
    particles.push({
      x: worldX,
      z: worldZ,
      vx: (Math.random() - 0.5) * 2,
      vz: (Math.random() - 0.5) * 2,
      life: lifetime + Math.random() * 20,
      maxLife: lifetime + 20,
      color: color,
      size: 2 + Math.random() * 2,
    });
  }
}

export function updateParticles() {
  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i];
    p.x += p.vx * 0.03;
    p.z += p.vz * 0.03;
    p.vx *= 0.97;
    p.vz *= 0.97;
    p.life--;
    if (p.life <= 0) {
      particles.splice(i, 1);
    }
  }
}

export function getParticles() {
  return particles;
}
