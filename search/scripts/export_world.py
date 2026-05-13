#!/usr/bin/env python3
"""Export an atlas entry as a world config JSON for browser replay.

Usage:
    # Export atlas entry #42
    uv run python -m scripts.export_world results/illuminate_v1 42

    # Then run the worker with it (from server/)
    cd ../server
    WORLD_FILE=../search/replay.json uv run python -m ecosim.worker

    # Or specify output path
    uv run python -m scripts.export_world results/illuminate_v1 42 -o my_world.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export an atlas entry as a world config JSON",
    )
    parser.add_argument(
        "results_dir",
        type=str,
        help="Path to illumination results directory",
    )
    parser.add_argument(
        "index",
        type=int,
        help="Atlas entry index (0-based, matches thumbnail sim_NNNN.png)",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Output JSON path (default: replay.json in current directory)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override seed (default: uses the atlas index as seed)",
    )

    args = parser.parse_args()
    results_dir = Path(args.results_dir)

    # Load thetas
    thetas_path = results_dir / "thetas_final.npy"
    if not thetas_path.exists():
        print(f"Error: {thetas_path} not found", file=sys.stderr)
        sys.exit(1)

    thetas = np.load(thetas_path)

    if args.index < 0 or args.index >= len(thetas):
        print(f"Error: index {args.index} out of range (0–{len(thetas) - 1})", file=sys.stderr)
        sys.exit(1)

    # Import here so --help works without full deps
    from lila_search.theta import make_eco_rates_spec, theta_to_world_config

    theta = thetas[args.index]
    seed = args.seed if args.seed is not None else args.index
    spec = make_eco_rates_spec()

    config = theta_to_world_config(theta, seed=seed)

    # Print theta summary
    print(f"Atlas entry #{args.index}")
    print(f"Seed: {seed}")
    print(f"Parameters:")
    for name, val in zip(spec.names, theta):
        print(f"  {name:30s} = {val:.3f}")

    # Write config
    output = args.output or "replay.json"
    with open(output, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\nWorld config written to {output}")
    print(f"\nTo replay in browser:")
    print(f"  cd ../server")
    print(f"  WORLD_FILE=../search/{output} uv run python -m ecosim.worker")
    print(f"  # Open http://localhost:8001")


if __name__ == "__main__":
    main()
