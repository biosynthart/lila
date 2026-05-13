"""Headless renderer: EcosystemEngine state → 256×256 RGB numpy array.

Produces a top-down 2D image encoding the semantically important features
that CLIP can differentiate: soil moisture, water sources, entity positions
by type and state, plant growth, dormancy markers.

Mirrors the browser visualizer's color semantics without animation or
interpolation — this is a single-frame snapshot for FM embedding.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IMG_SIZE = 256
GRID_SIZE = 32  # locked in design decisions
CELL_PX = IMG_SIZE / GRID_SIZE  # 8.0

# Colors (RGB tuples) — chosen to be visually distinct in CLIP space
# and semantically matched to the browser visualizer
COLOR_WATER = (50, 130, 200)
COLOR_DEER = (180, 100, 50)
COLOR_BUTTERFLY = (220, 180, 40)
COLOR_OAK = (60, 100, 50)
COLOR_GRASS_HEALTHY = (80, 170, 80)
COLOR_GRASS_DRY = (160, 150, 80)
COLOR_WILDFLOWER = (180, 60, 130)
COLOR_WILDFLOWER_FRUITING = (240, 200, 50)
COLOR_DORMANT = (120, 95, 70)

# Soil moisture gradient endpoints
SOIL_DRY = np.array([200, 175, 130], dtype=np.float32)   # warm amber
SOIL_WET = np.array([100, 170, 170], dtype=np.float32)    # cool teal


# ---------------------------------------------------------------------------
# Engine state extraction
# ---------------------------------------------------------------------------
# These functions isolate the engine API assumptions. If attribute names
# differ from what's documented, adjust here only.

def _extract_entities(engine: Any) -> list[dict]:
    """Pull entity data from engine into plain dicts.

    Expected engine API:
        engine.entities → dict[str, dict] mapping entity_id to entity data
        Each entity has: type, state, health, growth, hydration, and position
        stored either as x/y/z keys or a position [x,y,z] array.

    Adjust this function if the actual API differs.
    """
    entities = []
    for eid, e in engine.entities.items():
        # Handle position as [x,y,z] array or as separate x/y/z keys
        if "position" in e:
            pos = e["position"]
            x, y, z = pos[0], pos[1], pos[2]
        else:
            x = e.get("x", 0)
            y = e.get("y", 0)
            z = e.get("z", 0)

        entities.append({
            "id": eid,
            "type": e.get("type", ""),
            "state": e.get("state", ""),
            "x": x,
            "y": y,
            "z": z,
            "health": e.get("health", 1.0),
            "growth": e.get("growth", 0.0),
            "hydration": e.get("hydration", 1.0),
            "species": e.get("species", ""),
        })
    return entities


def _extract_moisture_grid(engine: Any) -> np.ndarray:
    """Get soil moisture as a 2D grid (GRID_SIZE × GRID_SIZE), values 0–1.

    Expected engine API:
        engine.voxel_manager.get_layer_slice("moisture", y=0)
        → 2D array of shape (GRID_SIZE, GRID_SIZE)

    If the voxel manager exposes data differently, adjust here.
    Falls back to a uniform mid-moisture grid if extraction fails.
    """
    try:
        vm = engine.voxel_manager
        # Try direct layer access — the voxel manager uses sparse storage
        # but should provide a way to read a 2D slice
        if hasattr(vm, "get_layer_slice"):
            return np.clip(vm.get_layer_slice("moisture", y=0), 0, 1)

        # Fallback: read from the sparse grid directly
        # Voxel layers: 0=nutrients, 1=moisture, 2=temperature, 3=organic_matter
        grid = np.full((GRID_SIZE, GRID_SIZE), 0.3, dtype=np.float32)
        if hasattr(vm, "grid"):
            for (x, y, z), layers in vm.grid.items():
                if y == 0 and 0 <= x < GRID_SIZE and 0 <= z < GRID_SIZE:
                    grid[z, x] = np.clip(layers.get(1, layers.get("moisture", 0.3)), 0, 1)
        return grid
    except Exception:
        return np.full((GRID_SIZE, GRID_SIZE), 0.3, dtype=np.float32)


def _extract_water_sources(engine: Any) -> list[dict]:
    """Get water source positions, radii, and levels.

    Expected engine API:
        engine.water_sources → list of dicts with keys:
            position: [x, y, z] (or x/z keys), radius, water_level (0–1)
    """
    sources = []
    try:
        for ws in engine.water_sources:
            # Handle position as array or separate keys
            if "position" in ws:
                pos = ws["position"]
                x, z = pos[0], pos[2]
            else:
                x = ws.get("x", 0)
                z = ws.get("z", 0)

            sources.append({
                "x": x,
                "z": z,
                "radius": ws.get("radius", 2.0),
                "water_level": ws.get("water_level", 1.0),
            })
    except (AttributeError, TypeError):
        pass
    return sources


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _grid_to_px(gx: float, gz: float) -> tuple[float, float]:
    """Convert grid coordinates to pixel coordinates."""
    return gx * CELL_PX, gz * CELL_PX


def _draw_moisture_background(img: np.ndarray, moisture: np.ndarray) -> None:
    """Render soil moisture as a teal→amber gradient background."""
    # Upscale moisture grid to image size via nearest-neighbor
    for gz in range(GRID_SIZE):
        for gx in range(GRID_SIZE):
            m = moisture[gz, gx]
            color = (SOIL_WET * m + SOIL_DRY * (1 - m)).astype(np.uint8)
            px_x = int(gx * CELL_PX)
            px_z = int(gz * CELL_PX)
            px_x2 = int((gx + 1) * CELL_PX)
            px_z2 = int((gz + 1) * CELL_PX)
            img[px_z:px_z2, px_x:px_x2] = color


def _draw_water_sources(draw: ImageDraw.Draw, sources: list[dict]) -> None:
    """Draw water sources as blue circles scaled by water_level."""
    for ws in sources:
        level = ws["water_level"]
        if level < 0.05:
            continue  # dried up — skip, matching engine behavior
        px_x, px_z = _grid_to_px(ws["x"], ws["z"])
        r_px = ws["radius"] * CELL_PX * level * 0.5  # scaled down to let entities show
        alpha_color = tuple(int(c * (0.4 + 0.6 * level)) for c in COLOR_WATER)
        draw.ellipse(
            [px_x - r_px, px_z - r_px, px_x + r_px, px_z + r_px],
            fill=alpha_color,
        )


def _draw_entity(draw: ImageDraw.Draw, entity: dict) -> None:
    """Draw a single entity based on its type and state."""
    etype = entity["type"]
    state = entity["state"]
    px_x, px_z = _grid_to_px(entity["x"], entity["z"])

    if state == "DORMANT":
        # Faded brown root marker for dormant plants
        r = 3
        draw.ellipse([px_x - r, px_z - r, px_x + r, px_z + r], fill=COLOR_DORMANT)
        return

    if etype == "ANIMAL":
        # Directional triangle — simplified without heading angle
        size = 7
        draw.polygon(
            [(px_x, px_z - size), (px_x - size * 0.6, px_z + size * 0.5),
             (px_x + size * 0.6, px_z + size * 0.5)],
            fill=COLOR_DEER,
        )

    elif etype == "INSECT":
        # Small dot with color indicating state
        r = 3.5
        color = COLOR_WILDFLOWER_FRUITING if state == "POLLINATING" else COLOR_BUTTERFLY
        draw.ellipse([px_x - r, px_z - r, px_x + r, px_z + r], fill=color)

    elif etype == "TREE":
        # Large circle with canopy halo
        canopy_r = 10
        trunk_r = 4
        # Canopy shadow (lighter)
        canopy_color = tuple(min(c + 40, 255) for c in COLOR_OAK)
        draw.ellipse(
            [px_x - canopy_r, px_z - canopy_r, px_x + canopy_r, px_z + canopy_r],
            fill=canopy_color,
        )
        # Trunk center (darker)
        draw.ellipse(
            [px_x - trunk_r, px_z - trunk_r, px_x + trunk_r, px_z + trunk_r],
            fill=COLOR_OAK,
        )

    elif etype == "PLANT":
        # Scale with growth, tint with hydration
        growth = entity.get("growth", 0.5)
        hydration = entity.get("hydration", 0.5)
        r = max(2.5, 4.5 * growth)

        # Determine if this is grass or wildflower from species field or ID
        species = entity.get("species", "")
        eid = entity.get("id", "")
        is_wildflower = "wildflower" in species or "flower" in eid

        if is_wildflower:
            if state == "FRUITING":
                color = COLOR_WILDFLOWER_FRUITING
            else:
                color = COLOR_WILDFLOWER
        else:
            # Grass — interpolate between dry and healthy based on hydration
            t = np.clip(hydration, 0, 1)
            color = tuple(
                int(COLOR_GRASS_DRY[i] * (1 - t) + COLOR_GRASS_HEALTHY[i] * t)
                for i in range(3)
            )

        draw.ellipse([px_x - r, px_z - r, px_x + r, px_z + r], fill=color)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_headless(engine: Any, img_size: int = IMG_SIZE) -> np.ndarray:
    """Render current engine state as an RGB image.

    Parameters
    ----------
    engine : EcosystemEngine
        The simulation engine instance after one or more steps.
    img_size : int
        Output image dimensions (square). Default 256.

    Returns
    -------
    np.ndarray
        RGB image array of shape (img_size, img_size, 3), dtype uint8.
    """
    # Allocate image buffer
    img = np.zeros((img_size, img_size, 3), dtype=np.uint8)

    # Layer 1: soil moisture background
    moisture = _extract_moisture_grid(engine)
    _draw_moisture_background(img, moisture)

    # Convert to PIL for shape drawing
    pil_img = Image.fromarray(img)
    draw = ImageDraw.Draw(pil_img)

    # Layer 2: water sources
    water_sources = _extract_water_sources(engine)
    _draw_water_sources(draw, water_sources)

    # Layer 3: entities (plants first, then animals/insects on top)
    entities = _extract_entities(engine)

    # Sort by draw order: dormant → plants → trees → animals → insects
    type_order = {"DORMANT": 0, "PLANT": 1, "TREE": 2, "ANIMAL": 3, "BIRD": 4, "INSECT": 5}
    entities.sort(key=lambda e: type_order.get(
        "DORMANT" if e["state"] == "DORMANT" else e["type"], 3
    ))

    for entity in entities:
        _draw_entity(draw, entity)

    return np.array(pil_img)
