"""līlā Python Client — Pygame scene renderer.

Mirrors the browser client's canvas renderer: moisture heatmap, grid,
water sources, and entities drawn as layered sprites.
"""

from __future__ import annotations

import math
from typing import Optional

import pygame

from .constants import (
    CELL_PX,
    COLORS,
    GRID_SIZE,
    PADDING,
)
from .world_model import WorldEntity

# ─── Color helpers ───────────────────────────────────────────────────────────


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _world_to_canvas(wx: float, wz: float) -> tuple[float, float]:
    return (PADDING + wx * CELL_PX, PADDING + wz * CELL_PX)


# ─── Drawing functions ──────────────────────────────────────────────────────


def draw_moisture_heatmap(
    surface: pygame.Surface,
    moisture: list[float],
) -> None:
    """Draw the 32×32 moisture heatmap."""
    high = COLORS["moistureHigh"]
    mid = COLORS["moistureMid"]
    low = COLORS["moistureLow"]

    for z in range(GRID_SIZE):
        for x in range(GRID_SIZE):
            val = moisture[z * GRID_SIZE + x]
            cx, cz = _world_to_canvas(x, z)

            if val > 0.5:
                f = (val - 0.5) * 2
                r = int(_lerp(mid[0], high[0], f))
                g = int(_lerp(mid[1], high[1], f))
                b = int(_lerp(mid[2], high[2], f))
            else:
                f = val * 2
                r = int(_lerp(low[0], mid[0], f))
                g = int(_lerp(low[1], mid[1], f))
                b = int(_lerp(low[2], mid[2], f))

            pygame.draw.rect(surface, (r, g, b), (cx, cz, CELL_PX, CELL_PX))


def draw_water(
    surface: pygame.Surface,
    water_sources: list[dict],
    now_ms: float,
) -> None:
    """Draw water sources with radial gradients and animated ripples."""
    fill = COLORS["waterFill"]
    edge = COLORS["waterEdge"]
    shine = COLORS["waterShine"]

    for ws in water_sources:
        wx, _, wz = ws.get("position", [0, 0, 0])
        cx, cz = _world_to_canvas(wx, wz)
        center = (cx + CELL_PX / 2, cz + CELL_PX / 2)
        radius = ws.get("radius", 1.0) * CELL_PX
        level = ws.get("water_level", 1.0)

        if level < 0.02 or radius < 1:
            continue

        # Draw water on an alpha surface for proper transparency
        glow_r = int(radius * 2.0)
        size = glow_r * 2 + 1
        overlay = pygame.Surface((size, size), pygame.SRCALPHA)
        oc = (size // 2, size // 2)

        # Main water body (gradient)
        for r in range(int(radius), 0, -1):
            t = 1.0 - r / radius
            if t > 0.6:
                col = shine
            else:
                col = fill if t > 0.3 else edge
            a = int((0.3 + level * 0.4) * 255 * (0.7 + t * 0.25))
            pygame.draw.circle(overlay, (*col, a), oc, r, width=1)

        # Edge ring
        pygame.draw.circle(overlay, (*edge, int(0.5 * 255)), oc, int(radius), width=1)

        surface.blit(overlay, (center[0] - size // 2, center[1] - size // 2))

        # Animated ripples (thin lines on surface - no alpha needed)
        if level > 0.3:
            for i in range(2):
                phase = (now_ms * 0.001 + i * 1.8) % 3.0
                ripple_r = radius * 0.3 + phase * radius * 0.25
                ox = math.sin(i * 4.7) * radius * 0.2
                oz = math.cos(i * 4.7) * radius * 0.15
                ripple_center = (center[0] + ox, center[1] + oz)
                # Lighten the shine color for ripple
                rc = tuple(min(c + 30, 255) for c in shine)
                pygame.draw.circle(surface, rc, ripple_center, int(ripple_r), width=1)


def draw_grid(surface: pygame.Surface) -> None:
    """Draw the 33×33 grid with major lines every 8 cells."""
    bg = COLORS.get("bg", (15, 16, 15))
    # Blend grid color with background for subtle lines (no alpha support)
    grid_minor = tuple(int(b * 0.95 + 184 * 0.05) for b in bg)  # very faint
    grid_major = tuple(int(b * 0.88 + 184 * 0.12) for b in bg)  # slightly more visible
    span = PADDING + GRID_SIZE * CELL_PX

    for i in range(GRID_SIZE + 1):
        p = PADDING + i * CELL_PX
        major = i % 8 == 0
        # Vertical
        pygame.draw.line(
            surface, grid_major if major else grid_minor,
            (p, PADDING), (p, span), width=1,
        )
        # Horizontal
        pygame.draw.line(
            surface, grid_major if major else grid_minor,
            (PADDING, p), (span, p), width=1,
        )


def _draw_tree(
    surface: pygame.Surface, cx: float, cz: float, ent: WorldEntity,
) -> None:
    growth = ent.drive.get("growth", 0.5) if ent.drive else 0.5
    canopy_r = 4.0 * CELL_PX * growth * 0.5
    trunk_r = 3 + growth * 3

    # Canopy (semi-transparent)
    oak = COLORS.get("oak", (61, 107, 61))
    surf = pygame.Surface((int(canopy_r * 2 + 1), int(canopy_r * 2 + 1)), pygame.SRCALPHA)
    pygame.draw.circle(surf, (*oak, int(30)), (surf.get_width() // 2, surf.get_height() // 2), int(canopy_r))
    surface.blit(surf, (cx - canopy_r, cz - canopy_r))

    # Trunk
    pygame.draw.circle(surface, (*oak, 255), (cx, cz), int(trunk_r))


def _draw_grass(
    surface: pygame.Surface, cx: float, cz: float, ent: WorldEntity,
) -> None:
    if ent.state == "DORMANT":
        pygame.draw.circle(surface, (90, 82, 68), (cx, cz), 2)
        return
    growth = ent.drive.get("growth", 0.1) if ent.drive else 0.1
    hydration = ent.drive.get("hydration", 0.5) if ent.drive else 0.5
    size = 2 + growth * 4
    color = COLORS.get("grass", (107, 143, 94)) if hydration > 0.3 else (122, 114, 84)
    alpha = int((0.5 + growth * 0.5) * 255)

    surf = pygame.Surface((int(size * 2 + 1), int(size * 2 + 1)), pygame.SRCALPHA)
    for i in range(3):
        ox = math.sin(i * 2.1) * 3
        oz = math.cos(i * 2.1) * 3
        pygame.draw.circle(surf, (*color, min(alpha, 255)),
                           (surf.get_width() // 2 + ox, surf.get_height() // 2 + oz),
                           int(size * 0.6))
    surface.blit(surf, (cx - size, cz - size))


def _draw_flower(
    surface: pygame.Surface, cx: float, cz: float, ent: WorldEntity,
) -> None:
    if ent.state == "DORMANT":
        pygame.draw.circle(surface, (107, 94, 61), (cx, cz), 2)
        return
    growth = ent.drive.get("growth", 0.1) if ent.drive else 0.1
    flower_color = COLORS.get("wildflower", (122, 143, 94))

    pygame.draw.circle(surface, flower_color, (cx, cz), 2)

    if ent.state == "FRUITING":
        bloom_color = COLORS.get("flowerBloom", (196, 166, 74))
        pulse = 0.7 + math.sin(pygame.time.get_ticks() * 0.004) * 0.3
        bloom_r = 4 + growth * 3
        # Bloom glow
        surf = pygame.Surface((int(bloom_r * 4 + 1), int(bloom_r * 4 + 1)), pygame.SRCALPHA)
        pygame.draw.circle(surf, (*bloom_color, int(pulse * 50)),
                           (surf.get_width() // 2, surf.get_height() // 2), int(bloom_r * 2))
        surface.blit(surf, (cx - bloom_r * 2, cz - bloom_r * 2))
        # Bloom core
        pygame.draw.circle(surface, (*bloom_color, int(pulse * 255)),
                           (cx, cz), int(bloom_r))


def _draw_mushroom(
    surface: pygame.Surface, cx: float, cz: float, ent: WorldEntity,
) -> None:
    activity = ent.drive.get("activity", 0.5) if ent.drive else 0.5
    size = 2 + activity * 3
    alpha = int((0.3 + activity * 0.4) * 255)
    pygame.draw.circle(surface, (160, 140, 120, alpha), (cx, cz), int(size))


def _draw_deer(
    surface: pygame.Surface, cx: float, cz: float, ent: WorldEntity,
) -> None:
    state = ent.state or "IDLE"
    deer_color = COLORS.get("deer", (196, 149, 106))
    size = 5 if state == "RESTING" else 7

    # Draw deer sprite on temp surface pointing right, then rotate
    sprite_size = size * 2 + 6
    sprite = pygame.Surface((sprite_size, sprite_size), pygame.SRCALPHA)
    sc = sprite_size // 2
    # Body (triangle pointing right)
    points = [
        (sc + size, sc),
        (sc - size * 0.7, sc - size * 0.5),
        (sc - size * 0.7, sc + size * 0.5),
    ]
    pygame.draw.polygon(sprite, (*deer_color, 200), points)
    # Head dot
    pygame.draw.circle(sprite, (*deer_color, 255), (sc + int(size * 0.6), sc), 2)

    # Rotate to face travel direction
    rotated = pygame.transform.rotozoom(sprite, math.degrees(-ent.facing_angle), 1.0)
    surface.blit(rotated, (int(cx - rotated.get_width() / 2), int(cz - rotated.get_height() / 2)))

    # State ring (unrotated)
    if state == "DRINKING":
        pygame.draw.circle(surface, (90, 140, 180), (int(cx), int(cz)), size + 3, width=1)
    elif state == "RESTING":
        for d in range(0, size + 2, 2):
            pygame.draw.circle(surface, (180, 170, 140), (int(cx), int(cz)), size + 2 + d // 2, width=1)

    # Label
    _draw_label(surface, state.lower(), cx, cz + size + 10)


def _draw_bird(
    surface: pygame.Surface, cx: float, cz: float, ent: WorldEntity,
) -> None:
    state = ent.state or "IDLE"
    bird_color = COLORS.get("bird", (138, 123, 107))
    now_ms = pygame.time.get_ticks()

    # Draw bird sprite on temp surface pointing right, then rotate
    body_len, body_wid = 6, 2.5
    wing_len = 7
    flap_speed = {"HUNTING": 14, "FLEEING": 14, "FORAGING": 4}.get(state, 6)
    flap_amp = {"HUNTING": 0.6, "FLEEING": 0.6, "FORAGING": 0.35}.get(state, 0.45)
    wing_angle = math.sin(now_ms * 0.001 * flap_speed + cx * 0.3) * flap_amp

    sprite_size = (int(wing_len + body_len) * 2 + 4, 16)
    sprite = pygame.Surface(sprite_size, pygame.SRCALPHA)
    sc = (sprite_size[0] // 2, sprite_size[1] // 2)

    # Body — teardrop pointing right
    points = [
        (sc[0] + body_len, sc[1]),
        (sc[0] + body_len * 0.3, sc[1] - body_wid),
        (sc[0] - body_len * 0.6, sc[1]),
        (sc[0] + body_len * 0.3, sc[1] + body_wid),
    ]
    pygame.draw.polygon(sprite, (*bird_color, 200), points)

    # Wings (flapping)
    uw_x = 0.5 - wing_len * math.cos(wing_angle)
    uw_y = -wing_len * math.sin(abs(wing_angle) + 0.3)
    lw_x = 0.5 - wing_len * math.cos(-wing_angle)
    lw_y = wing_len * math.sin(abs(wing_angle) + 0.3)
    pygame.draw.line(sprite, bird_color, sc, (int(sc[0] + uw_x), int(sc[1] + uw_y)), width=1)
    pygame.draw.line(sprite, bird_color, sc, (int(sc[0] + lw_x), int(sc[1] + lw_y)), width=1)

    # Rotate to face travel direction
    rotated = pygame.transform.rotozoom(sprite, math.degrees(-ent.facing_angle), 1.0)
    surface.blit(rotated, (int(cx - rotated.get_width() / 2), int(cz - rotated.get_height() / 2)))

    # State ring (unrotated)
    if state == "HUNTING":
        pygame.draw.circle(surface, (180, 120, 90), (int(cx), int(cz)), 10, width=1)
    elif state == "DRINKING":
        pygame.draw.circle(surface, (90, 140, 180), (int(cx), int(cz)), 9, width=1)

    # Label
    _draw_label(surface, state.lower(), cx, cz + 14)


def _draw_butterfly(
    surface: pygame.Surface, cx: float, cz: float, ent: WorldEntity,
) -> None:
    now_ms = pygame.time.get_ticks()
    wing_flap = math.sin(now_ms * 0.008 + cx * 0.1) * 0.5 + 0.5
    butterfly_color = COLORS.get("butterfly", (168, 124, 196))
    wing_span, wing_h = 5, 3 * (0.5 + wing_flap * 0.5)
    alpha = int((0.7 + wing_flap * 0.3) * 255)

    surf = pygame.Surface((int(wing_span * 2 + 1), int(wing_h * 2 + 1)), pygame.SRCALPHA)
    w2, h2 = surf.get_width() // 2, surf.get_height() // 2
    # Left wing
    pygame.draw.polygon(surf, (*butterfly_color, alpha),
                        [(w2, h2), (0, 0), (w2 - int(wing_span * 0.3), int(wing_h * 0.5))])
    # Right wing
    pygame.draw.polygon(surf, (*butterfly_color, alpha),
                        [(w2, h2), (surf.get_width(), 0),
                         (w2 + int(wing_span * 0.3), int(wing_h * 0.5))])
    # Body
    pygame.draw.circle(surf, (122, 90, 143, 255), (w2, h2), 2)

    # Rotate to face travel direction
    rotated = pygame.transform.rotozoom(surf, math.degrees(-ent.facing_angle), 1.0)
    surface.blit(rotated, (int(cx - rotated.get_width() / 2), int(cz - rotated.get_height() / 2)))

    # Pollinating glow (unrotated)
    if ent.state == "POLLINATING":
        surf2 = pygame.Surface((21, 21), pygame.SRCALPHA)
        pygame.draw.circle(surf2, (196, 166, 74, 38), (10, 10), 10)
        surface.blit(surf2, (int(cx - 10), int(cz - 10)))


# ─── Entity drawing dispatch ────────────────────────────────────────────────

_DRAW_FUNCS = {
    "TREE": _draw_tree,
    "PLANT": lambda s, cx, cz, e: _draw_flower(s, cx, cz, e) if e.species == "wildflower" else _draw_grass(s, cx, cz, e),
    "MICROORGANISM": _draw_mushroom,
    "ANIMAL": _draw_deer,
    "BIRD": _draw_bird,
    "INSECT": _draw_butterfly,
}

LAYER_ORDER = ["TREE", "PLANT", "MICROORGANISM", "ANIMAL", "BIRD", "INSECT"]


def _draw_label(surface: pygame.Surface, text: str, x: float, y: float) -> None:
    """Draw a small label."""
    global _label_font
    if _label_font is None:
        _label_font = pygame.font.SysFont("monospace", 8)
    # Semi-transparent labels need an alpha overlay
    label_surf = _label_font.render(text, True, (184, 180, 168))
    alpha_surf = pygame.Surface(label_surf.get_size(), pygame.SRCALPHA)
    alpha_surf.blit(label_surf, (0, 0))
    alpha_surf.set_alpha(90)
    surface.blit(alpha_surf, (int(x - label_surf.get_width() // 2), int(y - label_surf.get_height() // 2)))


_label_font: Optional[pygame.font.Font] = None


def draw_entities(surface: pygame.Surface, world) -> None:
    """Draw all entities in layer order."""
    for etype in LAYER_ORDER:
        draw_func = _DRAW_FUNCS.get(etype)
        if draw_func is None:
            continue
        for ent in world.entities.values():
            if not ent.is_alive and ent.state != "DORMANT":
                continue
            if ent.type != etype:
                continue
            if math.isnan(ent.x) or math.isnan(ent.z):
                continue
            cx, cz = _world_to_canvas(ent.x, ent.z)
            draw_func(surface, cx, cz, ent)


def draw_particles(surface: pygame.Surface, particles: list[dict]) -> None:
    """Draw particle effects."""
    for p in particles:
        cx, cz = _world_to_canvas(p.get("x", 0), p.get("z", 0))
        alpha = p.get("life", 1) / max(p.get("maxLife", 1), 1)
        color = p.get("color", (200, 200, 200))
        if isinstance(color, str):
            # hex string
            color = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
        size = p.get("size", 2) * alpha
        if size < 0.5:
            continue
        a = int(alpha * 0.8 * 255)
        pygame.draw.circle(surface, (*color[:3], a), (cx, cz), int(size))


def draw_all(surface: pygame.Surface, world, particles: list[dict] | None = None) -> None:
    """Draw the complete scene in one call."""
    now_ms = pygame.time.get_ticks()

    # Background
    bg = COLORS.get("bg", (15, 16, 15))
    surface.fill(bg)

    # Moisture heatmap
    if hasattr(world, "moisture") and world.moisture:
        draw_moisture_heatmap(surface, world.moisture)

    # Water sources
    if hasattr(world, "water_sources") and world.water_sources:
        draw_water(surface, world.water_sources, now_ms)

    # Grid
    draw_grid(surface)

    # Entities
    draw_entities(surface, world)

    # Particles
    if particles:
        draw_particles(surface, particles)
