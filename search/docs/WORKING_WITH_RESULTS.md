<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# Working with Illumination Results

## Output Files

### `thetas_final.npy` — The discovered parameter vectors

Shape: `(64, 17)` — 64 surviving ecosystem configurations, each a 17-dimensional θ vector.

Each row is a complete world recipe. The columns map to `dim_names` in metadata.json:

```
 0: rate_consumption          6: soil_nitrogen         12: butterfly_count
 1: rate_hunger               7: soil_moisture         13: oak_count
 2: rate_thirst               8: climate_temperature   14: grass_count
 3: rate_growth               9: water_count           15: wildflower_count
 4: rate_reproduction        10: water_radius          16: rain_interval
 5: rate_water_replenishment 11: deer_count
```

**Load and inspect:**

```python
import numpy as np
from lila_search.theta import make_eco_rates_spec, theta_to_world_config

thetas = np.load("results/illuminate_v1/thetas_final.npy")
spec = make_eco_rates_spec()

# Look at simulation #0
print(dict(zip(spec.names, thetas[0])))

# Which simulation has the most deer?
deer_idx = spec.names.index("deer_count")
densest = thetas[:, deer_idx].argmax()
print(f"Sim {densest}: {thetas[densest, deer_idx]:.0f} deer")

# Regenerate and run any discovered world
config = theta_to_world_config(thetas[42], seed=42)
# Pass to EcosystemEngine(config) to replay it
```

### `embeddings_final.npy` — CLIP representations

Shape: `(64, 512)` — each simulation's visual identity as a 512-dimensional
vector in CLIP space. L2-normalized, so cosine similarity = dot product.

**What these encode:** CLIP was trained on internet-scale image-text pairs, so
these embeddings capture what looks visually *meaningful* to a human observer.
Two simulations that are "far apart" in this space look genuinely different —
different spatial patterns, densities, color distributions, entity arrangements.

```python
embeddings = np.load("results/illuminate_v1/embeddings_final.npy")

# Find the two most similar simulations
sim_matrix = embeddings @ embeddings.T
np.fill_diagonal(sim_matrix, -1)  # ignore self-similarity
i, j = np.unravel_index(sim_matrix.argmax(), sim_matrix.shape)
print(f"Most similar pair: sim {i} and sim {j} (cosine sim: {sim_matrix[i,j]:.4f})")

# Find the most unique simulation (highest mean distance to all others)
dist_matrix = 1 - sim_matrix
np.fill_diagonal(dist_matrix, 0)
most_unique = dist_matrix.mean(axis=1).argmax()
print(f"Most unique: sim {most_unique}")
```

### `metadata.json` — Run configuration and metrics

Contains the full config (pop_size, generations, etc.), dimension names,
diversity history, and elapsed time. Use it to reproduce or compare runs.

### `thumbnails/` — Rendered snapshots

64 PNG images (`sim_0000.png` through `sim_0063.png`), one per surviving
configuration. These are the final-tick renders used in the atlas.

### `checkpoints/` — Evolution snapshots

Saved every 10 generations:
- `thetas_gen0010.npy`, `thetas_gen0020.npy`, ... — population at that generation
- `embeddings_gen0010.npy`, ... — corresponding embeddings

**Track how a specific region of the atlas evolved:**

```python
import numpy as np

# Load early and late populations
early = np.load("results/illuminate_v1/checkpoints/thetas_gen0010.npy")
late = np.load("results/illuminate_v1/checkpoints/thetas_gen0100.npy")

# Compare parameter distributions
spec_names = ["rate_consumption", "rate_hunger", "rate_thirst", ...]
for i, name in enumerate(spec_names):
    print(f"{name:30s}  gen10: {early[:,i].mean():.2f} ± {early[:,i].std():.2f}"
          f"  gen100: {late[:,i].mean():.2f} ± {late[:,i].std():.2f}")
```

---

## Exploring the Embedding Space

### Which θ dimensions drive visual diversity?

This tells you what the search actually learned to vary:

```python
import numpy as np
from lila_search.theta import make_eco_rates_spec

thetas = np.load("results/illuminate_v1/thetas_final.npy")
embeddings = np.load("results/illuminate_v1/embeddings_final.npy")
spec = make_eco_rates_spec()

# Correlation between each θ dimension and each embedding dimension
# High correlation = that parameter strongly influences the visual output
correlations = np.zeros(spec.ndim)
for d in range(spec.ndim):
    # Correlation between θ_d and the first 3 PCA components of embeddings
    from sklearn.decomposition import PCA
    pca = PCA(n_components=3)
    emb_pca = pca.fit_transform(embeddings)
    for pc in range(3):
        correlations[d] = max(correlations[d],
                              abs(np.corrcoef(thetas[:, d], emb_pca[:, pc])[0, 1]))

# Rank dimensions by influence
ranked = np.argsort(correlations)[::-1]
for idx in ranked:
    print(f"{spec.names[idx]:30s}  max |corr|: {correlations[idx]:.3f}")
```

### Cluster the atlas into ecological regimes

```python
from sklearn.cluster import KMeans

embeddings = np.load("results/illuminate_v1/embeddings_final.npy")
thetas = np.load("results/illuminate_v1/thetas_final.npy")
spec = make_eco_rates_spec()

# Find 4-6 natural clusters
km = KMeans(n_clusters=5, random_state=42)
labels = km.fit_predict(embeddings)

# What characterizes each cluster?
for c in range(5):
    mask = labels == c
    print(f"\n--- Cluster {c} ({mask.sum()} sims) ---")
    for d in range(spec.ndim):
        mean_val = thetas[mask, d].mean()
        global_mean = thetas[:, d].mean()
        if abs(mean_val - global_mean) > 0.3 * thetas[:, d].std():
            print(f"  {spec.names[d]:30s}  cluster: {mean_val:.2f}  overall: {global_mean:.2f}")
```

---

## ASAL Search Modes Using These Embeddings

The illumination run discovered *what's out there*. The embeddings enable
two more search modes that answer different questions:

### 1. Target Search — "Find me a simulation that looks like X"

Uses CLIP's text encoder to embed a natural language description, then
searches for a θ that produces a simulation whose embedding is close to
the text embedding. This is ASAL mode 1.

```python
import numpy as np
from lila_search.evaluator import CLIPEvaluator
from lila_search.substrate import LilaSubstrate
from lila_search.theta import make_eco_rates_spec

evaluator = CLIPEvaluator()
spec = make_eco_rates_spec()

# Embed a target description
target_emb = evaluator.embed_text(["a barren landscape with dried ponds"])[0]

# Option A: search existing population (instant)
embeddings = np.load("results/illuminate_v1/embeddings_final.npy")
thetas = np.load("results/illuminate_v1/thetas_final.npy")

similarities = embeddings @ target_emb
best_idx = similarities.argmax()
print(f"Best match: sim {best_idx} (similarity: {similarities[best_idx]:.4f})")
print(f"  θ = {dict(zip(spec.names, thetas[best_idx]))}")

# Option B: optimize with CMA-ES to find new θ (minutes)
import cma

substrate = LilaSubstrate()

def objective(theta):
    theta = np.array(theta)
    theta = spec.clip(theta)
    frames = substrate.rollout(theta, n_steps=2000, n_frames=20, seed=0)
    emb = evaluator.embed_rollout(frames)
    return -float(emb @ target_emb)  # maximize similarity = minimize negative

x0 = thetas[best_idx]  # warm-start from best existing match
sigma0 = 0.3
opts = {"maxiter": 50, "popsize": 16, "seed": 42}
es = cma.CMAEvolutionStrategy(x0, sigma0, opts)
es.optimize(objective)

best_theta = spec.clip(np.array(es.result.xbest))
print(f"\nOptimized θ: {dict(zip(spec.names, best_theta))}")
```

**Prompt ideas to try:**
- "a dense forest with many animals"
- "a barren landscape with dried ponds"
- "wildflowers and butterflies"
- "an ecosystem in collapse"
- "a thriving balanced ecosystem"

### 2. Open-Ended Search — "Find simulations that keep changing"

Instead of diversity across the *population*, this optimizes for diversity
across *time* within a single simulation. Finds θ values that produce
ecosystems which don't reach equilibrium — they keep generating novel
visual states. This is ASAL mode 2.

```python
def temporal_novelty(theta, substrate, evaluator, n_steps=2000, n_frames=40):
    """Score how much visual change happens over a rollout."""
    theta = np.array(theta)
    frames = substrate.rollout(theta, n_steps=n_steps, n_frames=n_frames, seed=0)
    frame_embeddings = evaluator.embed_frames(frames)

    # Measure mean distance between consecutive frame embeddings
    diffs = np.diff(frame_embeddings, axis=0)
    novelty = np.linalg.norm(diffs, axis=1).mean()
    return novelty

# Find which existing simulations are most temporally dynamic
thetas = np.load("results/illuminate_v1/thetas_final.npy")
substrate = LilaSubstrate()
evaluator = CLIPEvaluator()

novelties = []
for i in range(len(thetas)):
    n = temporal_novelty(thetas[i], substrate, evaluator)
    novelties.append(n)
    print(f"Sim {i:3d}: temporal novelty = {n:.4f}")

most_dynamic = np.argmax(novelties)
print(f"\nMost dynamic: sim {most_dynamic}")
```

### 3. Replay Any Discovered World

```python
from ecosim.engine import EcosystemEngine
from lila_search.theta import make_eco_rates_spec, theta_to_world_config
from lila_search.renderer import render_headless
from PIL import Image
import numpy as np

thetas = np.load("results/illuminate_v1/thetas_final.npy")

# Pick a simulation to replay
sim_idx = 42
config = theta_to_world_config(thetas[sim_idx], seed=sim_idx)
engine = EcosystemEngine(config)

# Run and capture frames
frames = []
for tick in range(2000):
    engine.step()
    if tick % 50 == 0:
        frames.append(render_headless(engine))

# Save as individual images
for i, frame in enumerate(frames):
    Image.fromarray(frame).save(f"replay/frame_{i:04d}.png")

# Or make a GIF
images = [Image.fromarray(f) for f in frames]
images[0].save("replay/sim42.gif", save_all=True,
               append_images=images[1:], duration=200, loop=0)
```

---

## Replay in Browser

Every simulation in the atlas is fully deterministic — θ + seed
reproduces it tick-for-tick. The search renderer (PIL, 256×256) is
just for CLIP. The browser visualizer gives you the full experience:
entity labels, state transitions, event log, soil heatmap, rain button.

### Export and run

```bash
# From search/ — export atlas entry #42 as a world config
uv run python -m scripts.export_world results/illuminate_v1 42

# Output:
#   Atlas entry #42
#   Seed: 42
#   Parameters:
#     rate_consumption               = 3.214
#     rate_hunger                    = 1.872
#     ...
#   World config written to replay.json

# From server/ — run the worker with that config
cd ../server
WORLD_FILE=../search/replay.json uv run python -m ecosim.worker

# Open http://localhost:8001 — you're watching that atlas entry live
```

### Export with a custom seed or output path

```bash
# Different seed (changes entity placement, same rates)
uv run python -m scripts.export_world results/illuminate_v1 42 --seed 99

# Custom output path
uv run python -m scripts.export_world results/illuminate_v1 42 -o worlds/drought_world.json
```

### Browse the atlas, then replay

The workflow: look at the atlas image, pick a tile that looks
interesting, find its index (row × 8 + col for an 8×8 atlas,
or check `thumbnails/sim_NNNN.png`), export it, replay in browser.

```bash
# Top-right tile of an 8×8 atlas = index 7
uv run python -m scripts.export_world results/illuminate_v1 7

# Bottom-left = index 56
uv run python -m scripts.export_world results/illuminate_v1 56
```

### Future: Godot holodeck

The Godot client connects to the same WebSocket as the browser.
When it ships, replay works identically — same worker, same config,
same tick packets. The atlas becomes a map you browse in 2D, then
step into in 3D.

---

## What to Try Next

**Immediate (uses existing results, no new search run):**
1. Run the dimension correlation analysis — find out which θ dims CLIP cares about
2. Cluster the atlas into ecological regimes — name them
3. Try target search against a few text prompts — see what CLIP thinks matches
4. Measure temporal novelty across the 64 sims — find the most dynamic worlds

**Next search run:**
- Target search with CMA-ES for a specific prompt ("ecosystem in collapse")
- `--workers 8` to use all your cores
- Try `--mutation-scale 0.15` for more exploration (current 0.1 might be conservative)

**When traits land:**
- Expand θ to encode body masses, diets, thermal tolerances
- Re-run illumination — the atlas goes from "interesting tunings" to "interesting ecologies"
