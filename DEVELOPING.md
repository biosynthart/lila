<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# Developing līlā

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended) or pip

## Quick Start with uv

```bash
cd server/

# Create venv and install everything (ecosim + websockets + dev tools)
uv sync

# Run the smoke test
uv run python tests/smoke_test.py

# Run pytest
uv run pytest tests

# Start the worker (serves viz on http://localhost:8001)
uv run lila-worker

# Lint
uv run ruff check ecosim/
uv run ruff format ecosim/

# Type check
uv run pyright ecosim/
```

## Without uv (pip)

```bash
cd server/
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[all]"
pytest
python -m ecosim.worker
```

## Dependency Groups

| Extra       | Installs                          | When you need it                |
|-------------|-----------------------------------|---------------------------------|
| (none)      | nothing — stdlib only             | Embedding ecosim as a library   |
| `worker`    | websockets                        | Running the WebSocket worker    |
| `dev`       | pytest, ruff, pyright             | Development and CI              |
| `all`       | worker + dev                      | Local development (recommended) |

> **Note:** A `gateway` group (fastapi, uvicorn, redis) is reserved in
> pyproject.toml for the upcoming multi-session gateway (Milestone 2).
> It is not functional yet — don't install it expecting a working service.

## Debugging with VS Code

A launch configuration is included at `.vscode/launch.json` to run the
worker as a Python module with the debugger attached.

**To use it:**

1. Open the `server/` folder (or the repo root) in VS Code.
2. Make sure the Python extension is installed and your interpreter is
   set to the uv-managed venv (`server/.venv/bin/python`).
3. Open the Run and Debug panel (`Ctrl+Shift+D` / `Cmd+Shift+D`).
4. Select **"Run Worker"** from the dropdown and press `F5`.

This launches `python -m ecosim.worker` under the debugger so you can
set breakpoints in `engine.py`, `worker.py`, entity code, etc.

If you prefer the command line instead:

```bash
cd server/
uv run lila-worker
```

## Project Layout

```
server/
├── pyproject.toml          # Package config, uv reads this
├── .python-version         # uv Python version pin (3.12)
├── uv.lock                 # Lockfile (committed, deterministic builds)
├── ecosim/                 # Core library (stdlib only)
│   ├── engine.py
│   ├── entities.py
│   ├── biome.py
│   ├── voxel_manager.py
│   ├── model_adapter.py
│   ├── worker.py
│   └── adapters/
│       ├── mlp.py
│       ├── static.py
│       └── random.py
├── tests/
│   ├── smoke_test.py       # 50-tick integration test
│   └── test_ecosim.py      # Unit tests
├── examples/
│   └── demo_world.json     # Temperate meadow with randomization
└── weights/                # Placeholder for trained model weights
```

## Adding Dependencies

```bash
# Add to core (avoid this — ecosim is stdlib-only by design)
uv add some-package

# Add to an optional group
uv add --optional worker some-package

# Add a dev dependency
uv add --dev some-package

# After any change, uv.lock updates automatically
```

## Docker

The Docker build does NOT use uv — it pip-installs from pyproject.toml
for a minimal image. Local dev uses uv for speed and lockfile guarantees.

```bash
cd deploy/compose
docker compose up --build
```

## Training Pipeline

> **Not yet implemented.** The `training/` directory and its separate
> pyproject.toml are scaffolded for Milestone 3 (trained motion model).
> Heavy ML dependencies (PyTorch, numpy, tensorboard) will live there
> and are never imported by the core ecosim package.
