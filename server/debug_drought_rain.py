#!/usr/bin/env python3
"""Debug script: observe deer health through drought -> rain cycle (fast)."""
import json, sys
sys.path.insert(0, "/home/jarunn/projects/lila/server")

from ecosim.engine import EcosystemEngine

with open("/home/jarunn/projects/lila/server/examples/demo_world.json") as f:
    world = json.load(f)

engine = EcosystemEngine(world)

def deer_stats(label=""):
    for eid, e in engine.entities.items():
        if e.get("species") == "deer":
            sv = e["state_vars"]
            print(f"  {eid:12s} state={e['state']:10s} health={sv.get('health',0):.3f} hydr={sv.get('hydration',0):.3f} energy={sv.get('energy',0):.3f} hunger={sv.get('hunger',0):.3f}")

def plant_summary():
    states = {}
    for eid, e in engine.entities.items():
        sp = e.get("species", "")
        if sp in ("meadow_grass", "wildflower"):
            st = e["state"]
            states[st] = states.get(st, 0) + 1
    print(f"  Plants: {states}")

# Run fast (dt=1.0) to get drought conditions quickly
print("PHASE 1: Fast-forward to drought (dt=1.0)")
for _ in range(200):
    engine.step(1.0)

print(f"\n--- Tick {engine.tick} (pre-rain, drought conditions) ---")
deer_stats()
plant_summary()

# Now trigger rain
print("\n>>> TRIGGERING RAIN (intensity=0.8)")
engine.apply_rain(0.8)

print(f"\n--- Tick {engine.tick} (immediately after rain) ---")
deer_stats("post-rain immediate")

# Run more ticks and watch deer health recover (or not)
print("\nPHASE 2: Post-rain recovery (dt=1.0)")
for i in range(300):
    engine.step(1.0)
    if (i + 1) % 50 == 0:
        print(f"\n--- Tick {engine.tick} (+{i+1} post-rain) ---")
        deer_stats()

print("\nFINAL:")
deer_stats()
plant_summary()
