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

"""Illumination search: discover maximally diverse ecosystem configurations.

Implements ASAL's illumination mode — a genetic algorithm where the
selection pressure is *diversity*, not fitness. The population is
maintained to maximize the minimum nearest-neighbor distance in CLIP
embedding space. The output is a set of θ vectors, their embeddings,
and rendered thumbnails — the raw material for a simulation atlas.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm

from lila_search.substrate import LilaSubstrate
from lila_search.evaluator import CLIPEvaluator
from lila_search.theta import ThetaSpec


def _rollout_worker(args: tuple) -> list[np.ndarray]:
    """Module-level rollout function for ProcessPoolExecutor.

    Each worker creates its own LilaSubstrate instance since the
    engine can't be pickled across processes.
    """
    theta, n_steps, n_frames, seed = args
    substrate = LilaSubstrate()
    return substrate.rollout(theta, n_steps=n_steps, n_frames=n_frames, seed=seed)


@dataclass
class IlluminationResult:
    """Output of an illumination run."""
    thetas: np.ndarray          # (pop_size, ndim) — final population
    embeddings: np.ndarray      # (pop_size, embed_dim) — CLIP embeddings
    thumbnails: list[np.ndarray]  # rendered frames for atlas visualization
    diversity_history: list[float]  # min nearest-neighbor distance per generation
    elapsed_seconds: float


@dataclass
class IlluminationConfig:
    """Configuration for illumination search."""
    pop_size: int = 64            # population size
    n_children: int = 32          # children generated per generation
    n_generations: int = 100      # total generations
    n_steps: int = 2000           # simulation ticks per rollout
    n_frames: int = 20            # frames captured per rollout
    mutation_scale: float = 0.1   # std of Gaussian mutation (relative to range)
    seed: int = 0
    save_interval: int = 10       # save checkpoint every N generations
    n_workers: int = 1            # parallel CPU rollout workers (1 = sequential)


def _min_nn_distance(embeddings: np.ndarray) -> float:
    """Compute minimum nearest-neighbor distance across the population.

    This is the diversity metric ASAL optimizes for illumination.
    Higher = more diverse population.
    """
    n = len(embeddings)
    if n < 2:
        return 0.0

    # Cosine distance matrix (embeddings are L2-normalized)
    sim = embeddings @ embeddings.T
    dist = 1.0 - sim

    # Set diagonal to infinity so we skip self-distances
    np.fill_diagonal(dist, np.inf)

    # For each point, find its nearest neighbor distance
    nn_distances = dist.min(axis=1)

    # Return the minimum of all nearest-neighbor distances
    # This is the "bottleneck" — the closest pair in the population
    return float(nn_distances.min())


def _mean_nn_distance(embeddings: np.ndarray) -> float:
    """Mean nearest-neighbor distance — alternative diversity metric."""
    n = len(embeddings)
    if n < 2:
        return 0.0
    sim = embeddings @ embeddings.T
    dist = 1.0 - sim
    np.fill_diagonal(dist, np.inf)
    nn_distances = dist.min(axis=1)
    return float(nn_distances.mean())


def _select_most_diverse(
    embeddings: np.ndarray,
    keep_n: int,
) -> np.ndarray:
    """Greedy farthest-point selection to keep the most diverse subset.

    Iteratively selects the point that is farthest from the already-selected
    set. This is a standard approach for diversity maximization and matches
    ASAL's illumination selection pressure.

    Parameters
    ----------
    embeddings : np.ndarray
        Shape (n_candidates, embed_dim), L2-normalized.
    keep_n : int
        Number of points to keep.

    Returns
    -------
    np.ndarray
        Indices of the selected points, shape (keep_n,).
    """
    n = len(embeddings)
    if n <= keep_n:
        return np.arange(n)

    # Precompute pairwise distances
    sim = embeddings @ embeddings.T
    dist = 1.0 - sim

    # Start with the point that has the highest mean distance to all others
    selected = [int(dist.mean(axis=1).argmax())]

    # Greedily add the farthest point from the selected set
    min_dist_to_selected = dist[selected[0]].copy()

    for _ in range(keep_n - 1):
        # Find the candidate farthest from any selected point
        # (max of the min-distance-to-selected)
        candidates = np.ones(n, dtype=bool)
        candidates[selected] = False
        candidate_idx = np.where(candidates)[0]

        best = candidate_idx[min_dist_to_selected[candidate_idx].argmax()]
        selected.append(int(best))

        # Update min distances
        np.minimum(min_dist_to_selected, dist[best], out=min_dist_to_selected)

    return np.array(selected)


def _mutate(
    theta: np.ndarray,
    spec: ThetaSpec,
    scale: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Mutate a θ vector with Gaussian noise scaled to parameter ranges."""
    ranges = spec.bounds[:, 1] - spec.bounds[:, 0]
    noise = rng.normal(0, scale, size=theta.shape) * ranges
    child = theta + noise
    return spec.clip(child)


def illuminate(
    substrate: LilaSubstrate | None = None,
    evaluator: CLIPEvaluator | None = None,
    config: IlluminationConfig | None = None,
    output_dir: str | Path | None = None,
) -> IlluminationResult:
    """Run illumination search over the līlā parameter space.

    Parameters
    ----------
    substrate : LilaSubstrate, optional
        Substrate instance. Created with defaults if None.
    evaluator : CLIPEvaluator, optional
        CLIP evaluator. Created with defaults if None.
    config : IlluminationConfig, optional
        Search configuration. Uses defaults if None.
    output_dir : str or Path, optional
        Directory to save checkpoints and results. No saving if None.

    Returns
    -------
    IlluminationResult
        Final population, embeddings, thumbnails, and metrics.
    """
    if config is None:
        config = IlluminationConfig()
    if substrate is None:
        substrate = LilaSubstrate()
    if evaluator is None:
        evaluator = CLIPEvaluator()

    spec = substrate.theta_spec()
    rng = np.random.default_rng(config.seed)

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    t_start = time.time()

    # -----------------------------------------------------------------------
    # Helper: run rollouts (optionally parallel), then batch-embed on GPU
    # -----------------------------------------------------------------------
    def _run_rollouts(thetas: np.ndarray, base_seed: int) -> tuple[list[list[np.ndarray]], list[np.ndarray]]:
        """Run simulations on CPU, return (all_frames, thumbnails)."""
        all_frames = []
        thumbnails = []

        if config.n_workers > 1:
            from concurrent.futures import ProcessPoolExecutor
            tasks = [(thetas[i], config.n_steps, config.n_frames, base_seed + i)
                     for i in range(len(thetas))]
            with ProcessPoolExecutor(max_workers=config.n_workers) as pool:
                results = list(tqdm(pool.map(_rollout_worker, tasks),
                                    total=len(tasks), desc="Rollouts", leave=False))
            for frames in results:
                all_frames.append(frames)
                thumbnails.append(frames[-1])
        else:
            for i in tqdm(range(len(thetas)), desc="Rollouts", leave=False):
                frames = substrate.rollout(thetas[i], n_steps=config.n_steps,
                                           n_frames=config.n_frames, seed=base_seed + i)
                all_frames.append(frames)
                thumbnails.append(frames[-1])

        return all_frames, thumbnails

    # -----------------------------------------------------------------------
    # Initialize population with random θ vectors
    # -----------------------------------------------------------------------
    print(f"Initializing population of {config.pop_size}...")
    pop_thetas = np.array([spec.sample_uniform(rng) for _ in range(config.pop_size)])

    # CPU: run all rollouts
    init_frames, pop_thumbnails = _run_rollouts(pop_thetas, config.seed)
    # GPU: batch embed all at once
    pop_embeddings = evaluator.embed_rollouts_batch(init_frames)

    diversity_history = [_min_nn_distance(pop_embeddings)]
    print(f"Initial diversity (min NN dist): {diversity_history[-1]:.4f}")

    # -----------------------------------------------------------------------
    # Evolution loop
    # -----------------------------------------------------------------------
    for gen in tqdm(range(config.n_generations), desc="Illumination"):
        # Generate children by mutating random parents
        parent_idx = rng.integers(0, config.pop_size, size=config.n_children)
        child_thetas = np.array([
            _mutate(pop_thetas[pi], spec, config.mutation_scale, rng)
            for pi in parent_idx
        ])

        # CPU: run all child rollouts
        child_base_seed = config.seed + config.pop_size + gen * config.n_children
        child_frames, child_thumbnails = _run_rollouts(child_thetas, child_base_seed)
        # GPU: batch embed all children at once
        child_embeddings = evaluator.embed_rollouts_batch(child_frames)

        # Combine parents + children
        all_thetas = np.concatenate([pop_thetas, child_thetas], axis=0)
        all_embeddings = np.concatenate([pop_embeddings, child_embeddings], axis=0)
        all_thumbnails = pop_thumbnails + child_thumbnails

        # Select the most diverse subset
        keep_idx = _select_most_diverse(all_embeddings, config.pop_size)

        pop_thetas = all_thetas[keep_idx]
        pop_embeddings = all_embeddings[keep_idx]
        pop_thumbnails = [all_thumbnails[i] for i in keep_idx]

        div = _min_nn_distance(pop_embeddings)
        diversity_history.append(div)

        if (gen + 1) % 10 == 0:
            mean_div = _mean_nn_distance(pop_embeddings)
            tqdm.write(
                f"Gen {gen + 1:4d} | min NN dist: {div:.4f} | "
                f"mean NN dist: {mean_div:.4f}"
            )

        # Save checkpoint
        if output_dir and config.save_interval > 0 and (gen + 1) % config.save_interval == 0:
            _save_checkpoint(output_dir, gen + 1, pop_thetas, pop_embeddings, diversity_history)

    elapsed = time.time() - t_start

    result = IlluminationResult(
        thetas=pop_thetas,
        embeddings=pop_embeddings,
        thumbnails=pop_thumbnails,
        diversity_history=diversity_history,
        elapsed_seconds=elapsed,
    )

    # Save final results
    if output_dir:
        _save_final(output_dir, result, spec)

    print(f"\nIllumination complete in {elapsed:.1f}s")
    print(f"Final diversity (min NN dist): {diversity_history[-1]:.4f}")

    return result


def _save_checkpoint(
    output_dir: Path,
    gen: int,
    thetas: np.ndarray,
    embeddings: np.ndarray,
    diversity_history: list[float],
) -> None:
    """Save a checkpoint to disk."""
    cp_dir = output_dir / "checkpoints"
    cp_dir.mkdir(exist_ok=True)
    np.save(cp_dir / f"thetas_gen{gen:04d}.npy", thetas)
    np.save(cp_dir / f"embeddings_gen{gen:04d}.npy", embeddings)


def _save_final(output_dir: Path, result: IlluminationResult, spec: ThetaSpec) -> None:
    """Save final results to disk."""
    np.save(output_dir / "thetas_final.npy", result.thetas)
    np.save(output_dir / "embeddings_final.npy", result.embeddings)

    # Save thumbnails as individual images
    thumb_dir = output_dir / "thumbnails"
    thumb_dir.mkdir(exist_ok=True)
    from PIL import Image
    for i, thumb in enumerate(result.thumbnails):
        Image.fromarray(thumb).save(thumb_dir / f"sim_{i:04d}.png")

    # Save metadata
    meta = {
        "pop_size": len(result.thetas),
        "embed_dim": result.embeddings.shape[1],
        "ndim": result.thetas.shape[1],
        "dim_names": spec.names,
        "diversity_history": result.diversity_history,
        "elapsed_seconds": result.elapsed_seconds,
    }
    with open(output_dir / "metadata.json", "w") as f:
        json.dump(meta, f, indent=2)
