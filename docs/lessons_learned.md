<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# Lessons Learned

Hard-won knowledge from building līlā. Updated as the project evolves.

## WebSocket API

**`process_request` signature changed between websockets versions.**
In v10, it's `process_request(path, request_headers)` returning a
tuple `(status, headers, body)`. In v13+, it's
`process_request(connection, request)` returning a
`websockets.http11.Response` object. The assertion error is cryptic —
it manifests as `assert isinstance(response, Response)` deep in the
handshake code. If you see "unexpected internal error" during the
opening handshake, check your `process_request` return type first.

**Worker must use two concurrent asyncio tasks.** A blocking
read-forward-read-forward relay deadlocks under WebSocket. The fix
is `asyncio.wait(FIRST_COMPLETED)` with separate tick and listener
tasks.

## Hysteresis

**Guard conditions must have hysteresis bands.** Without them,
entities thrash between states every tick at threshold boundaries.
A deer at hydration 0.299 enters DRINKING, recovers to 0.301, exits
DRINKING, drains to 0.299, enters DRINKING — oscillating every two
ticks forever.

Pattern: enter DRINKING at 0.2, stay until 0.6. The 0.4 gap is the
hysteresis band.

## Guard Chain Bugs

**`elif` chains in Python are exclusive — if one branch enters, no
subsequent branch is evaluated, even if the body does nothing.**

This caused deer to starve in the middle of a lush meadow. The
reproduction guard checked `elif drive > 0.8:` → entered the branch
→ `_find_mate()` returned False → nothing happened → but DRINKING
and FORAGING branches below were skipped → deer defaulted to IDLE
→ couldn't eat or drink → died.

Fix: combine both checks into the `elif` condition:
```python
# Broken — blocks the chain even when no mate found
elif sv["reproductive_drive"] > 0.8:
    if self._find_mate(e):
        ...

# Fixed — falls through to next elif when no mate
elif sv["reproductive_drive"] > 0.8 and self._find_mate(e):
    ...
```

## Movement & Targeting

**Entities must seek targets purposefully, not wander randomly.**
Random wandering means a deer in a field of grass might walk in
circles for 30 seconds without eating anything. Movement should be
state-driven: FORAGING → seek nearest food, DRINKING → seek nearest
water, high reproductive drive → seek nearest mate.

**Skip targets you're already at.** Without a minimum distance check
(~1.0 units), entities re-target the plant they're standing on
forever. Arrive → target clears → pick new target → same plant →
arrive instantly → repeat.

**Pollination needs a cooldown on the flower, not a memory on the
insect.** Tracking `_last_pollinated` on the butterfly breaks when
only one flower is blooming — the butterfly can never return to it.
A cooldown timer on the flower (~50 ticks) solves single-flower,
multi-flower, and multi-insect cases cleanly.

**Mate-seeking needs grid-wide search.** `_find_mate` checks within
sensory range for reproduction eligibility, but if two deer are on
opposite sides of the meadow, they never get close enough. Solution:
a separate `_find_nearest_mate_pos` that searches the full entity
list (simulating scent/calls) for movement targeting, while `_find_mate`
stays range-limited for the actual reproduction trigger.

## Reproduction

**Children must inherit parent stress.** If children spawn with
fresh defaults (hunger 0, energy 1.0, full health), a starving
population becomes immortal — each generation starts clean, breeds
before dying, spawns another clean generation. Fix: children inherit
a fraction of parent state (hunger × 0.3, energy × 0.9, etc.) so
generational decline occurs without food.

**Reproduction must cost the parent.** Energy drain alone isn't
enough. For insects, colony_health should also drain on reproduction
to prevent exponential population growth.

**Drive decay must have a dead zone.** If reproductive drive decays
in the `else` branch (any tick conditions aren't met), the drive
erases faster than it builds. Animals forage, hunger rises above the
threshold, drive resets. Fix: only decay drive when truly struggling
(hunger > 0.7 or energy < 0.2), not just when conditions aren't
perfect.

## Plant Ecology

**Deer eat everything unless filtered.** Without species checks,
deer consume wildflowers before they can bloom, breaking the entire
pollination chain. Fix: deer prefer grass, fall back to flowers only
when grass is exhausted.

**Plants should go dormant, not die.** Root systems persist through
drought. When health hits zero, grass and flowers enter DORMANT
(growth zeroed, no metabolism, no resource consumption) instead of
being removed. The entity stays in the simulation at its position,
waiting for moisture. Trees still die permanently — their loss is
the final act.

**Dormant recovery needs adequate thresholds.** The guard checks
soil moisture and nutrients each tick. If the recovery rate is too
slow relative to evaporation, plants never emerge from dormancy even
with rain. Fix: rain should directly boost plant hydration and health
(not just soil), and suppress evapotranspiration temporarily.

**Flowers need a lower bloom threshold under stress.** With stress
rates, wildflowers never reached the original growth ≥ 0.8 for
FRUITING. Lowering to 0.5 lets them bloom before drought kills them.

## Water System

**Water sources need dynamic levels.** Fixed-radius ponds that
refill instantly don't create drought pressure. Each source tracks
`water_level` (0–1), which controls the effective radius. Evaporation
drains it, rain replenishes it, drinking animals deplete it. The viz
renders shrinking ponds as the level drops.

**Background soil evaporation is essential.** Without it, soil
moisture persists forever once set. A global evaporation pass each
tick (scaled by temperature, humidity, and rate_thirst) ensures the
entire grid dries out when water sources deplete.

**Rain must work at multiple levels.** Just boosting soil moisture
isn't enough — plant evapotranspiration drains it immediately. Rain
should: boost soil moisture globally, replenish water source levels,
boost plant hydration directly, add a small nutrient bonus (atmospheric
nitrogen), and suppress evaporation for ~8 seconds.

## Ecosystem Collapse

**Trees need collapse pressure from ecosystem loss.** Without
surrounding plants and animals (nutrient cycling, mycorrhizal
networks), trees should decline faster. Count active non-dormant,
non-insect entities — when support drops below a threshold, trees
get accelerated health and hydration drain.

**Butterfly swarms can't sustain trees.** If the collapse check
counts all alive entities, a swarm of 12 butterflies keeps the
alive_count high and oaks never die. Fix: only count entities that
actually contribute to the tree's support network (plants, animals —
not insects).

## Visualization

**`worldToCanvas` returns cell top-left, not center.** Water circles,
entity positions, and any centered rendering needs `+ CELL_PX / 2`
on both axes.

**Water glow must cover moisture voxels.** The engine sets high
moisture on a square grid, but water renders as a circle. Options:
skip heatmap cells inside water (creates black holes) or make the
water gradient opaque enough to mask the squares (better). Extend
the glow radius to 1.8× and bump main body opacity.

**Entity type inference from ID prefixes.** First-tick entities from
the world definition never get a spawn packet, so the viz can't know
their type from the protocol alone. Prefix matching (`deer_` →
ANIMAL, `grass_` → PLANT) is the fallback. Fragile but functional
for the alpha.

## Docker & Build

**Don't use hatchling in Docker.** The `python:*-slim` images ship
with setuptools pre-installed but not hatchling. Using hatchling as
the build backend requires an extra pip install step in the Dockerfile.
Setuptools works everywhere with zero setup.

**`readme` in pyproject.toml breaks Docker builds.** If `readme =
"README.md"` is specified but the README isn't copied into the build
context, setuptools fails. Either copy it or omit the field.

**Use `".[worker]"` not hardcoded packages.** `pip install ".[worker]"`
reads from pyproject.toml optional dependencies. If you add deps later,
only the TOML changes — not the Dockerfile.

## Randomization

**World randomization must be opt-in from JSON.** A `"randomize"`
key in the world config controls whether positions are jittered,
layouts are transformed, and extra entities spawn. Omitting the key
gives exact positions from the JSON — essential for level designers
and deterministic tests.

**Apply grid transforms (rotation + flip) before jittering.** The D4
symmetry group (4 rotations × 2 axis flips = 8 orientations) makes
the same JSON feel like 8 different levels. Transform entities and
water sources together so relative positions stay coherent.

**Push plants out of water after randomization.** When positions are
jittered, plants can land inside ponds. A post-randomization pass
pushes any plant radially outward to just past the water's edge —
which accidentally looks more realistic than a uniform grid.

## WSL

**`Zone.Identifier` files appear when copying from Windows.** NTFS
Alternate Data Streams materialize as literal files named
`filename:Zone.Identifier` in WSL. Harmless to Python but confusing.
Clean with `find . -name "*:Zone.Identifier" -delete`.
