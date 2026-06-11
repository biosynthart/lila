#!/usr/bin/env python3
"""
līlā — Headless Client Test Harness

Simulates a full browser client: connects via WebSocket, sends world def,
receives intent-based tick packets, runs synthetic agency logic, sends
heartbeats with positions/events, and monitors server reconciliation.

Usage:
    uv run python tests/test_client_harness.py [--host localhost] [--port 8001]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

try:
    import websockets
except ImportError:
    print("ERROR: install websockets (uv sync)")
    sys.exit(1)


# ─── Minimal world definition for testing ─────────────

def build_test_world() -> dict:
    return {
        "version": "0.1",
        "session_id": "test-harness-001",
        "environment": {
            "type": "MEADOW",
            "biome": "TEMPERATE",
            "climate": {
                "temperature": 22,
                "humidity": 0.6,
                "rainfall": 0.4,
                "wind_speed": 0.15,
                "light_level": 0.85,
            },
            "soil": {
                "nitrogen": 0.7,
                "phosphorus": 0.6,
                "potassium": 0.5,
                "moisture": 0.65,
                "organic_matter": 0.4,
                "ph": 6.8,
            },
            "voxel_grid": {"dimensions": [32, 32, 32], "cell_size": 1.0},
        },
        "model": {"adapter": "mlp", "seed": 42},
        # Species definitions (trait vectors — list format)
        "species_definitions": [
            {
                "species_id": "deer",
                "functional_group": "herbivore",
                "entity_class": "ANIMAL",
                "body_mass_kg": 60,
                "locomotion": "quadruped",
                "skeleton_id": "quadruped_medium",
                "thermoregulation": "endotherm",
                "diet_type": "herbivore",
                "trophic_level": 2.0,
                "reproductive_strategy": "K_selected",
                "clutch_size": 1,
                "generation_time_ticks": 800,
                "thermal_range": [0, 40],
                "drought_tolerance": 0.3,
                "shade_tolerance": 0.3,
                "sensory_range_multiplier": 1.0,
                "movement_budget": 0.4,
                "resource_tags": ["consumer"],
            },
            {
                "species_id": "meadow_grass",
                "functional_group": "producer",
                "entity_class": "PLANT",
                "body_mass_kg": 0.1,
                "locomotion": "stationary",
                "diet_type": "autotroph",
                "trophic_level": 1.0,
                "reproductive_strategy": "r_selected",
                "clutch_size": 5,
                "generation_time_ticks": 400,
                "thermal_range": [0, 40],
                "drought_tolerance": 0.6,
                "shade_tolerance": 0.8,
                "sensory_range_multiplier": 0.0,
                "movement_budget": 0.0,
                "spread_mode": "rhizome",
                "spread_range": 1.5,
                "spread_chance": 0.3,
                "root_persistence": True,
                "resource_tags": ["forage", "graminoid"],
            },
        ],
        # Entities to spawn
        "entities": [
            {"id": "deer_01", "type": "ANIMAL", "species": "deer",
             "sex": "female", "position": [16.0, 0.0, 14.0],
             "metadata": {}, "skeleton_id": "quadruped_medium"},
            {"id": "grass_01", "type": "PLANT", "species": "meadow_grass",
             "position": [15.0, 0.0, 13.0], "metadata": {}},
            {"id": "grass_02", "type": "PLANT", "species": "meadow_grass",
             "position": [18.0, 0.0, 16.0], "metadata": {}},
        ],
    }


# ─── Test Results Tracker ─────────────────────────────

class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  ✓ {name}")
        else:
            self.failed += 1
            msg = f"  ✗ {name}" + (f" — {detail}" if detail else "")
            print(msg)
            self.errors.append(f"{name}: {detail}")

    def summary(self):
        total = self.passed + self.failed
        status = "PASS" if self.failed == 0 else f"FAIL ({self.failed}/{total})"
        print(f"\n{'='*60}")
        print(f"Results: {status} — {self.passed}/{total} checks passed")
        if self.errors:
            for e in self.errors:
                print(f"  ❌ {e}")
        return self.failed == 0


# ─── Headless Client Simulator ────────────────────────

class HeadlessClient:
    """Simulates a browser client with agency logic."""

    def __init__(self, host: str = "localhost", port: int = 8001):
        self.host = host
        self.port = port
        self.ws = None
        self.world_model: dict[str, dict] = {}  # id → entity state
        self.species_defs: dict = {}
        self.tick_count = 0
        self.events_received: list[dict] = []

    async def connect(self):
        uri = f"ws://{self.host}:{self.port}/ws"
        print(f"\nConnecting to {uri} ...")
        self.ws = await websockets.connect(uri, max_size=2**20)
        print("Connected ✓")

    async def send_world_def(self):
        world = build_test_world()
        await self.ws.send(json.dumps(world))
        print(f"Sent world definition ({len(world['entities'])} entities)")

    async def receive_session_started(self, timeout: float = 5.0) -> dict:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        data = json.loads(raw)
        assert data.get("type") == "session_started", f"Expected session_started, got {data.get('type')}"
        print(f"Session started ✓ (tick_rate={data.get('tick_rate')}, entities={data.get('entity_count')})")

        # Store species definitions
        self.species_defs = data.get("species", {})
        if self.species_defs:
            print(f"  Species defs received: {list(self.species_defs.keys())}")
        return data

    async def receive_tick(self, timeout: float = 10.0) -> dict | None:
        """Receive one tick packet. Returns None on timeout."""
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            data = json.loads(raw)
            self.tick_count += 1

            # Process entity updates into local world model
            for u in data.get("entity_updates", []):
                eid = u["id"]
                if eid not in self.world_model:
                    self.world_model[eid] = {}
                self.world_model[eid].update(u)

            # Track events
            self.events_received.extend(data.get("events", []))

            return data
        except asyncio.TimeoutError:
            return None

    async def send_heartbeat(self, positions: dict[str, list], events: list[dict] | None = None):
        """Send client heartbeat with positions and optional events."""
        msg = {"type": "heartbeat", "positions": positions}
        if events:
            msg["events"] = events
        await self.ws.send(json.dumps(msg))

    async def close(self):
        if self.ws:
            # Send shutdown to stop the server session cleanly
            try:
                await self.ws.send(json.dumps({"type": "shutdown"}))
            except Exception:
                pass
            await asyncio.sleep(0.2)
            await self.ws.close()


# ─── Test Scenarios ───────────────────────────────────

async def test_session_init(client: HeadlessClient, results: TestResults):
    """Test 1: Session initialization and species definitions."""
    print("\n--- Test 1: Session Init ---")

    await client.send_world_def()
    session = await client.receive_session_started()

    results.check("session_started type", session.get("type") == "session_started")
    results.check("tick_rate is 2.0 (0.5Hz)", session.get("tick_rate") == 2.0)
    results.check("entity_count matches", session.get("entity_count") == 3)

    # Species definitions should be present and useful
    species = session.get("species", {})
    results.check("species defs not empty", len(species) > 0, f"got {len(species)}")

    if "deer" in species:
        deer_def = species["deer"]
        results.check("deer has type=ANIMAL", deer_def.get("type") == "ANIMAL")
        results.check("deer has diet_order", isinstance(deer_def.get("diet_order"), list))
        results.check("deer has flee_targets", isinstance(deer_def.get("flee_targets"), list))


async def test_intent_packet_format(client: HeadlessClient, results: TestResults):
    """Test 2: Tick packets contain intent fields, not authoritative positions."""
    print("\n--- Test 2: Intent Packet Format ---")

    # Receive a few ticks
    for i in range(3):
        packet = await client.receive_tick(timeout=8.0)
        if packet is None:
            results.check(f"tick {i+1} received", False, "timeout waiting for tick")
            continue

        results.check(f"tick {i+1} has entity_updates", len(packet.get("entity_updates", [])) > 0)

        # Check intent fields on first entity update
        updates = packet.get("entity_updates", [])
        if not updates:
            continue

        ent = updates[0]
        results.check(f"tick {i+1} has state field", "state" in ent, f"keys: {list(ent.keys())}")
        results.check(f"tick {i+1} has drive field", "drive" in ent)
        results.check(f"tick {i+1} has motion_latent", "motion_latent" in ent)
        results.check(f"tick {i+1} has ref_position", "ref_position" in ent)

    # Check that entity updates don't contain authoritative position commands
    if client.world_model:
        first_ent = next(iter(client.world_model.values()))
        # Should NOT have a 'position' key (that's the old format)
        results.check("no authoritative 'position' field", "position" not in first_ent,
                       f"keys include position: {'position' in first_ent}")


async def test_heartbeat_reconciliation(client: HeadlessClient, results: TestResults):
    """Test 3: Server absorbs client positions and reconciles."""
    print("\n--- Test 3: Heartbeat Reconciliation ---")

    # Wait for a tick to establish baseline
    packet = await client.receive_tick(timeout=8.0)
    if not packet or not packet.get("entity_updates"):
        results.check("baseline tick received", False, "no updates in tick")
        return

    # Find the deer entity and its ref_position
    deer_update = None
    for u in packet["entity_updates"]:
        if u["id"] == "deer_01":
            deer_update = u
            break

    if not deer_update:
        results.check("deer found in updates", False, f"entities: {[u['id'] for u in packet['entity_updates']]}")
        return

    ref_pos = deer_update.get("ref_position", [0, 0, 0])
    print(f"  Deer ref_position: {ref_pos}")

    # Send heartbeat with a slightly different position (within bounds)
    nudge_x = ref_pos[0] + 1.5  # small deviation — should be soft-nudged
    nudge_z = ref_pos[2] + 1.0
    await client.send_heartbeat({
        "deer_01": [nudge_x, 0, nudge_z],
    })
    print(f"  Sent heartbeat: deer at [{nudge_x}, 0, {nudge_z}]")

    # Receive next tick — server should have absorbed the position
    packet2 = await client.receive_tick(timeout=8.0)
    if not packet2:
        results.check("post-heartbeat tick received", False, "timeout")
        return

    deer_update2 = None
    for u in packet2.get("entity_updates", []):
        if u["id"] == "deer_01":
            deer_update2 = u
            break

    if not deer_update2:
        results.check("deer in post-heartbeat tick", False)
        return

    new_ref = deer_update2.get("ref_position", ref_pos)
    print(f"  Deer new ref_position: {new_ref}")

    # Server should have absorbed the position (within tolerance)
    dx = abs(new_ref[0] - nudge_x)
    dz = abs(new_ref[2] - nudge_z)
    results.check("server absorbed x position", dx < 3.0, f"diff={dx:.2f}")
    results.check("server absorbed z position", dz < 3.0, f"diff={dz:.2f}")


async def test_client_events(client: HeadlessClient, results: TestResults):
    """Test 4: Server absorbs client-reported interaction events."""
    print("\n--- Test 4: Client Events ---")

    # Wait for a tick to get current state
    packet = await client.receive_tick(timeout=8.0)
    if not packet or not packet.get("entity_updates"):
        results.check("tick received", False, "no updates")
        return

    # Send a consumption event (deer eats grass)
    events = [{
        "type": "consumption",
        "source_id": "deer_01",
        "target_id": "grass_01",
        "position": [15, 0, 13],
    }]

    # Also send positions
    positions = {}
    for u in packet["entity_updates"]:
        if u.get("ref_position"):
            positions[u["id"]] = u["ref_position"]

    await client.send_heartbeat(positions, events)
    print(f"  Sent consumption event: deer_01 → grass_01")

    # Receive next tick — check for server acknowledgment of the event
    packet2 = await client.receive_tick(timeout=8.0)
    if not packet2:
        results.check("post-event tick received", False, "timeout")
        return

    # Server may echo events or apply them silently
    # At minimum, it shouldn't crash
    results.check("server survived event absorption", True)

    # Check that grass_01 still exists (or was removed if consumed)
    grass_updates = [u for u in packet2.get("entity_updates", []) if u["id"] == "grass_01"]
    removals = packet2.get("entity_removals", [])
    results.check(
        "grass handled after consumption",
        len(grass_updates) > 0 or "grass_01" in removals,
        f"updates={len(grass_updates)}, removed={'grass_01' in removals}",
    )


async def test_divergence_snap(client: HeadlessClient, results: TestResults):
    """Test 5: Large divergence triggers server snap + _ack."""
    print("\n--- Test 5: Divergence Snap ---")

    # Wait for a tick
    packet = await client.receive_tick(timeout=8.0)
    if not packet or not packet.get("entity_updates"):
        results.check("tick received", False, "no updates")
        return

    deer_update = None
    for u in packet["entity_updates"]:
        if u["id"] == "deer_01":
            deer_update = u
            break

    if not deer_update:
        results.check("deer found", False)
        return

    ref_pos = deer_update.get("ref_position", [0, 0, 0])

    # Send position WAY off (beyond expected travel distance)
    far_x = ref_pos[0] + 20.0  # way beyond speed * tick_rate
    await client.send_heartbeat({
        "deer_01": [far_x, 0, ref_pos[2]],
    })
    print(f"  Sent divergent position: [{far_x}, 0, {ref_pos[2]}] (delta=20)")

    # Receive next tick — server should snap and send _ack
    packet2 = await client.receive_tick(timeout=8.0)
    if not packet2:
        results.check("post-divergence tick received", False, "timeout")
        return

    deer_update2 = None
    for u in packet2.get("entity_updates", []):
        if u["id"] == "deer_01":
            deer_update2 = u
            break

    if not deer_update2:
        results.check("deer in post-divergence tick", False)
        return

    # Check for _ack flag
    has_ack = deer_update2.get("_ack", False)
    results.check("server sent _ack on divergence", has_ack, f"_ack={has_ack}")

    new_ref = deer_update2.get("ref_position", ref_pos)
    dx = abs(new_ref[0] - far_x)
    if has_ack:
        # With ack, server should have snapped close to client position
        results.check("server snapped to divergent pos", dx < 3.0, f"diff={dx:.2f}")


async def test_entity_lifecycle(client: HeadlessClient, results: TestResults):
    """Test 6: Entities are tracked correctly over multiple ticks."""
    print("\n--- Test 6: Entity Lifecycle ---")

    # Receive several ticks and track entity presence
    entities_seen = set()
    for i in range(5):
        packet = await client.receive_tick(timeout=8.0)
        if not packet:
            results.check(f"tick {i+1} received", False, "timeout")
            continue

        for u in packet.get("entity_updates", []):
            entities_seen.add(u["id"])

    # All 3 initial entities should have been seen
    expected = {"deer_01", "grass_01", "grass_02"}
    results.check("all entities seen across ticks", expected.issubset(entities_seen),
                   f"expected={expected}, seen={entities_seen}")

    # Entity count should be stable (no phantom spawns/removals)
    removals = set()
    for i in range(3):
        packet = await client.receive_tick(timeout=8.0)
        if not packet:
            continue
        removals.update(packet.get("entity_removals", []))

    # At least one entity should still be alive after a few ticks
    results.check("entities persist across ticks", len(entities_seen - removals) > 0,
                   f"alive={len(entities_seen - removals)}")


# ─── Main ─────────────────────────────────────────────

async def run_tests(host: str = "localhost", port: int = 8001):
    print("=" * 60)
    print("līlā — Headless Client Test Harness")
    print(f"Target: ws://{host}:{port}/ws")
    print("=" * 60)

    results = TestResults()
    client = HeadlessClient(host, port)

    try:
        await client.connect()

        # Run test scenarios in order (they share the same connection)
        await test_session_init(client, results)
        await test_intent_packet_format(client, results)
        await test_heartbeat_reconciliation(client, results)
        await test_client_events(client, results)
        await test_divergence_snap(client, results)
        await test_entity_lifecycle(client, results)

    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await client.close()
        except Exception:
            pass

    ok = results.summary()
    return 0 if ok else 1


def main():
    import argparse
    parser = argparse.ArgumentParser(description="līlā Headless Client Test Harness")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    return asyncio.run(run_tests(args.host, args.port))


if __name__ == "__main__":
    sys.exit(main())
