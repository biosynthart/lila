# Copyright 2025 BioSynthArt Studios LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Simulation atlas: UMAP projection of discovered ecosystems.

Takes illumination results (embeddings + thumbnails), projects into 2D
with UMAP, grid-samples the space, and composites the nearest thumbnail
into each tile. The output is a single image showing the full diversity
of discovered ecosystem configurations — the visual artifact that
demonstrates the substrate works.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

try:
    import umap
except ImportError:
    umap = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None


def build_atlas(
    embeddings: np.ndarray,
    thumbnails: list[np.ndarray],
    grid_cells: int = 8,
    thumb_size: int = 128,
    output_path: str | Path | None = None,
    umap_seed: int = 42,
) -> np.ndarray:
    """Build a simulation atlas image from illumination results.

    Parameters
    ----------
    embeddings : np.ndarray
        Shape (n, embed_dim), L2-normalized CLIP embeddings.
    thumbnails : list[np.ndarray]
        Corresponding rendered frames, each (H, W, 3) uint8.
    grid_cells : int
        Number of cells per side in the atlas grid (total = grid_cells²).
    thumb_size : int
        Size to resize each thumbnail to in the atlas.
    output_path : str or Path, optional
        If provided, save the atlas image here.
    umap_seed : int
        Random seed for UMAP reproducibility.

    Returns
    -------
    np.ndarray
        Atlas image of shape (grid_cells*thumb_size, grid_cells*thumb_size, 3).
    """
    if umap is None:
        raise ImportError("umap-learn is required for atlas generation: pip install umap-learn")

    n = len(embeddings)
    assert len(thumbnails) == n, f"Got {n} embeddings but {len(thumbnails)} thumbnails"

    # Project to 2D with UMAP
    reducer = umap.UMAP(
        n_components=2,
        metric="cosine",
        random_state=umap_seed,
        n_neighbors=min(15, n - 1),
    )
    coords_2d = reducer.fit_transform(embeddings)

    # Normalize to [0, 1]
    mins = coords_2d.min(axis=0)
    maxs = coords_2d.max(axis=0)
    ranges = maxs - mins
    ranges[ranges == 0] = 1.0  # avoid div by zero
    coords_norm = (coords_2d - mins) / ranges

    # Grid sample: for each cell, find the nearest simulation
    atlas_size = grid_cells * thumb_size
    atlas = np.ones((atlas_size, atlas_size, 3), dtype=np.uint8) * 240  # light gray bg

    used = set()
    for row in range(grid_cells):
        for col in range(grid_cells):
            # Center of this grid cell in normalized coords
            cx = (col + 0.5) / grid_cells
            cy = (row + 0.5) / grid_cells

            # Find nearest unused simulation
            dists = np.sqrt((coords_norm[:, 0] - cx) ** 2 + (coords_norm[:, 1] - cy) ** 2)

            # Prefer unused simulations, but allow reuse if needed
            sorted_idx = np.argsort(dists)
            best = None
            for idx in sorted_idx:
                if idx not in used:
                    best = idx
                    break
            if best is None:
                best = sorted_idx[0]  # all used, pick closest

            used.add(best)

            # Resize thumbnail and place in atlas
            thumb = Image.fromarray(thumbnails[best])
            thumb = thumb.resize((thumb_size, thumb_size), Image.NEAREST)
            thumb_arr = np.array(thumb)

            y0 = row * thumb_size
            x0 = col * thumb_size
            atlas[y0:y0 + thumb_size, x0:x0 + thumb_size] = thumb_arr

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(atlas).save(output_path)
        print(f"Atlas saved to {output_path}")

    return atlas


def plot_embedding_space(
    embeddings: np.ndarray,
    output_path: str | Path | None = None,
    umap_seed: int = 42,
    diversity_scores: np.ndarray | None = None,
) -> None:
    """Plot the 2D UMAP projection of embeddings as a scatter plot.

    Parameters
    ----------
    embeddings : np.ndarray
        Shape (n, embed_dim).
    output_path : str or Path, optional
        Save plot here.
    umap_seed : int
        UMAP random seed.
    diversity_scores : np.ndarray, optional
        Per-point scores for coloring. If None, uses nearest-neighbor distance.
    """
    if umap is None or plt is None:
        raise ImportError("umap-learn and matplotlib are required")

    n = len(embeddings)
    reducer = umap.UMAP(
        n_components=2,
        metric="cosine",
        random_state=umap_seed,
        n_neighbors=min(15, n - 1),
    )
    coords = reducer.fit_transform(embeddings)

    if diversity_scores is None:
        # Color by nearest-neighbor distance
        sim = embeddings @ embeddings.T
        dist = 1.0 - sim
        np.fill_diagonal(dist, np.inf)
        diversity_scores = dist.min(axis=1)

    fig, ax = plt.subplots(1, 1, figsize=(8, 8))
    scatter = ax.scatter(
        coords[:, 0], coords[:, 1],
        c=diversity_scores, cmap="viridis",
        s=30, alpha=0.8, edgecolors="none",
    )
    plt.colorbar(scatter, ax=ax, label="NN distance")
    ax.set_title("Ecosystem embedding space")
    ax.set_xticks([])
    ax.set_yticks([])

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Scatter plot saved to {output_path}")
    plt.close(fig)


def plot_diversity_curve(
    diversity_history: list[float],
    output_path: str | Path | None = None,
) -> None:
    """Plot diversity (min NN distance) over generations."""
    if plt is None:
        raise ImportError("matplotlib is required")

    fig, ax = plt.subplots(1, 1, figsize=(8, 4))
    ax.plot(diversity_history, linewidth=1.5, color="#534AB7")
    ax.set_xlabel("Generation")
    ax.set_ylabel("Min nearest-neighbor distance")
    ax.set_title("Diversity over search")
    ax.grid(True, alpha=0.3)

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Diversity curve saved to {output_path}")
    plt.close(fig)
