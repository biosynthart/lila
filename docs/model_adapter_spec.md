<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# Model Adapter Spec

How to build your own motor adapter for līlā.

## The idea in 30 seconds

The engine handles ecology — hunger, thirst, movement, death. Your
model handles *style* — how an entity moves, not where it goes.

Every tick, the engine collects entities that have skeletons, builds a
context vector for each one from its state variables, and calls your
adapter's `infer()` method. You return a latent vector per entity. The
client maps those latents to bone transforms. The user never sees the
inference; they just see a deer that moves like a deer.

## The protocol

A motor adapter implements three methods (though context_spec_for can 
just delegate to context_spec if you don't need type-specific inputs):

```python
class MyMotorAdapter:
    def context_spec(self) -> ContextSpec:
        """What inputs your model needs."""
        ...

    def context_spec_for(self, entity_type: str) -> ContextSpec:
        """Type-specific inputs. Defaults to context_spec()."""
        return self.context_spec()

    def infer(self, contexts: list[list[float]]) -> list[list[float]]:
        """Batch of context vectors → batch of latent vectors."""
        ...
```

That's it. No base class to inherit, no registration. If it has these
two methods, the engine accepts it (it's a Python `Protocol`).

## Context spec

Your adapter declares its inputs as a `ContextSpec` — a tuple of
`ContextField` objects that tell the engine what to put in the vector
and how to normalize it:

```python
from ecosim.model_adapter import ContextField, ContextSpec

MY_SPEC = ContextSpec(
    fields=(
        ContextField("hunger",         "state_var"),
        ContextField("energy",         "state_var"),
        ContextField("hydration",      "state_var"),
        ContextField("health",         "state_var"),
        ContextField("movement_speed", "metadata",  normalize=10.0),
        ContextField("body_mass",      "metadata",  normalize=200.0),
        ContextField("temperature",    "climate",   normalize=50.0),
        ContextField("state_code",     "derived"),
    ),
    latent_dim=4,
)
```

Each field specifies:

- **name** — the key to look up
- **source** — where to find it:
  - `state_var` — entity's continuous state (hunger, energy, etc.)
  - `metadata` — entity's config (body_mass, movement_speed, etc.)
  - `climate` — world climate dict (temperature, humidity, etc.)
  - `biome` — biome config attributes (metabolic_scaling, etc.)
  - `derived` — engine-computed values (currently just `state_code`)
- **normalize** — raw value is divided by this (default 1.0). State
  vars are already [0,1] so they don't need normalization. Metadata
  values like body_mass need scaling.
- **default** — fallback when the field is missing (default 0.0)

The engine calls `build_context(spec, entity, biome, climate)` to
assemble the vector. Your model never touches entity dicts directly.

## State codes

The `state_code` derived field encodes the entity's discrete state as
a float so your model can condition on it:

| State        | Code |
|--------------|------|
| IDLE         | 0.0  |
| RESTING      | 0.1  |
| DRINKING     | 0.2  |
| GROWING      | 0.3  |
| FORAGING     | 0.4  |
| POLLINATING  | 0.4  |
| REPRODUCING  | 0.5  |
| DORMANT      | 0.6  |
| HUNTING      | 0.7  |
| SWARMING     | 0.8  |
| WILTING      | 0.9  |
| FLEEING      | 1.0  |

## Output: the latent vector

`infer()` returns one latent vector per entity. The default dimension
is 4. The values should be in [-1, 1] (matching tanh output range).

The latent encodes movement *style*, not position or velocity. The
engine handles where entities go. Your model handles how they look
getting there. On the client side, the motion retargeter maps latents
to bone transforms:

```
R_final(bone) = R_base + Σ(latent[i] × W[bone][i])  for i in 0..3
```

The four dimensions have no fixed semantics. The reference MLP learns
whatever features the training data supports. The static adapter uses
an informal convention:

- dim 0: energy/intensity (calm → vigorous)
- dim 1: regularity (erratic → rhythmic)
- dim 2: verticality (crouched → upright)
- dim 3: alertness (relaxed → tense)

## Full example: a custom adapter

Here's a complete adapter that modulates motion style based on hunger
and energy — stressed animals move more erratically:

```python
from ecosim.model_adapter import ContextField, ContextSpec

STRESS_SPEC = ContextSpec(
    fields=(
        ContextField("hunger",    "state_var"),
        ContextField("energy",    "state_var"),
        ContextField("health",    "state_var"),
        ContextField("state_code","derived"),
    ),
    latent_dim=4,
)

class StressMotorAdapter:
    """Motion style shifts under physiological stress."""

    def context_spec(self) -> ContextSpec:
        return STRESS_SPEC

    def context_spec_for(self, entity_type: str) -> ContextSpec:
        return self.context_spec()

    def infer(self, contexts: list[list[float]]) -> list[list[float]]:
        results = []
        for ctx in contexts:
            hunger, energy, health, state = ctx

            # Stress factor: high hunger + low energy = stressed
            stress = max(0.0, hunger - energy)

            results.append([
                0.2 + stress * 0.6,   # intensity rises with stress
                0.4 - stress * 0.5,   # regularity drops (erratic)
                0.5 - hunger * 0.3,   # posture drops when hungry
                stress * 0.8,         # alertness spikes
            ])
        return results
```

Wire it into the engine:

```python
from ecosim.engine import EcosystemEngine

engine = EcosystemEngine(world_config, adapters={
    "motor": StressMotorAdapter(),
})
```

## Type-specific specs

If your model needs different inputs for different entity types (e.g.,
animals have hydration but insects have colony_health), implement
`context_spec_for()`:

```python
class MultiSpecAdapter:
    def context_spec(self) -> ContextSpec:
        return ANIMAL_SPEC  # default

    def context_spec_for(self, entity_type: str) -> ContextSpec:
        if entity_type == "INSECT":
            return INSECT_SPEC
        return ANIMAL_SPEC

    def infer(self, contexts: list[list[float]]) -> list[list[float]]:
        # All specs must produce the same input_dim
        return [self._forward(ctx) for ctx in contexts]
```

The engine checks for `context_spec_for()` and uses it when available.
Both specs must have the same `input_dim` so inference can be batched.

## Training and weight export

The reference MLP supports save/load:

```python
from ecosim.adapters import create_adapter

adapter = create_adapter("mlp", seed=42)
adapter.save_weights("weights/motion_v0.json")

# Later, or in production:
adapter = create_adapter("mlp", weights="weights/motion_v0.json")
```

The weight format is a JSON dict of layer names to 2D arrays. A
PyTorch training script can target this architecture and export via
`training/scripts/export_weights.py`.

## Built-in adapters

| Adapter  | What it does                        | When to use it              |
|----------|-------------------------------------|-----------------------------|
| `mlp`    | 4-layer feedforward (10→16→12→8→4)  | Default, trainable          |
| `static` | Fixed latent per discrete state     | Artist tuning, baselines    |
| `random` | Random [-1,1] each tick             | Client pipeline testing     |

```python
from ecosim.adapters import create_adapter

create_adapter("mlp", seed=42)
create_adapter("mlp", weights="weights/motion_v0.json")
create_adapter("static")
create_adapter("static", state_map={"IDLE": [0,0,0,0], ...})
create_adapter("random", seed=123)
```

## Future: behavior and narrative adapters

Two additional adapter levels are defined but not yet wired into the
engine:

**Behavior** (every tick) — influences guard condition thresholds.
Your model could bias an animal toward foraging vs resting based on
learned preferences. Input: entity state + nearby entity summaries.
Output: guard bias vector.

**Narrative** (every N ticks) — shapes macro-scale dynamics. Input:
ecosystem snapshot (population counts, resource levels, event history).
Output: event injections or parameter adjustments. Could enforce
ecological plausibility, create spatial structure, or produce seasonal
rhythms.

These protocols are defined in `model_adapter.py` as `BehaviorAdapter`
and `NarrativeAdapter`. They're visible to contributors now so the
hierarchy is clear, but the engine integration is planned for v0.2.
