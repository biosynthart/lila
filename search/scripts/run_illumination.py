#!/usr/bin/env python3
"""Run ASAL illumination search over līlā ecosystem configurations.

Usage:
    python -m scripts.run_illumination --output results/run_01
    python -m scripts.run_illumination --pop-size 32 --generations 50 --output results/quick

After the run completes, results/ will contain:
    - thetas_final.npy       — parameter vectors for all discovered ecosystems
    - embeddings_final.npy   — CLIP embeddings
    - thumbnails/            — rendered frames for each ecosystem
    - atlas.png              — the simulation atlas (UMAP grid)
    - scatter.png            — embedding space scatter plot
    - diversity.png          — diversity curve over generations
    - metadata.json          — run configuration and metrics
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ASAL illumination search over līlā ecosystems",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="results/illumination",
        help="Output directory for results (default: results/illumination)",
    )
    parser.add_argument(
        "--pop-size",
        type=int,
        default=64,
        help="Population size (default: 64)",
    )
    parser.add_argument(
        "--children",
        type=int,
        default=32,
        help="Children per generation (default: 32)",
    )
    parser.add_argument(
        "--generations",
        type=int,
        default=100,
        help="Number of generations (default: 100)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=2000,
        help="Simulation ticks per rollout (default: 2000)",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=20,
        help="Frames captured per rollout (default: 20)",
    )
    parser.add_argument(
        "--mutation-scale",
        type=float,
        default=0.1,
        help="Mutation scale relative to parameter ranges (default: 0.1)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed (default: 0)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Torch device for CLIP (default: auto-detect cuda/cpu)",
    )
    parser.add_argument(
        "--atlas-grid",
        type=int,
        default=8,
        help="Atlas grid cells per side (default: 8, produces 8x8 atlas)",
    )
    parser.add_argument(
        "--skip-atlas",
        action="store_true",
        help="Skip atlas generation (just run search and save raw results)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel CPU rollout workers (default: 1, sequential)",
    )

    args = parser.parse_args()
    output_dir = Path(args.output)

    # -----------------------------------------------------------------------
    # Import here so --help works without torch installed
    # -----------------------------------------------------------------------
    from lila_search.substrate import LilaSubstrate
    from lila_search.evaluator import CLIPEvaluator
    from lila_search.illumination import illuminate, IlluminationConfig
    from lila_search.viz.atlas import build_atlas, plot_embedding_space, plot_diversity_curve

    print("=" * 60)
    print("līlā — ASAL Illumination Search")
    print("=" * 60)
    print(f"  Population:   {args.pop_size}")
    print(f"  Children:     {args.children}")
    print(f"  Generations:  {args.generations}")
    print(f"  Steps/rollout:{args.steps}")
    print(f"  Frames:       {args.frames}")
    print(f"  Mutation:     {args.mutation_scale}")
    print(f"  Seed:         {args.seed}")
    print(f"  Output:       {output_dir}")
    print(f"  Device:       {args.device or 'auto'}")
    print(f"  Workers:      {args.workers}")
    print("=" * 60)

    # Initialize components
    substrate = LilaSubstrate()
    evaluator = CLIPEvaluator(device=args.device)

    config = IlluminationConfig(
        pop_size=args.pop_size,
        n_children=args.children,
        n_generations=args.generations,
        n_steps=args.steps,
        n_frames=args.frames,
        mutation_scale=args.mutation_scale,
        seed=args.seed,
        n_workers=args.workers,
    )

    # Run search
    result = illuminate(
        substrate=substrate,
        evaluator=evaluator,
        config=config,
        output_dir=output_dir,
    )

    # Generate visualizations
    if not args.skip_atlas:
        print("\nGenerating visualizations...")

        build_atlas(
            result.embeddings,
            result.thumbnails,
            grid_cells=args.atlas_grid,
            output_path=output_dir / "atlas.png",
        )

        plot_embedding_space(
            result.embeddings,
            output_path=output_dir / "scatter.png",
        )

        plot_diversity_curve(
            result.diversity_history,
            output_path=output_dir / "diversity.png",
        )

    print(f"\nDone. Results in {output_dir}/")


if __name__ == "__main__":
    main()
