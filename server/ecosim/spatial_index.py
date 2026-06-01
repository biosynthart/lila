# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
"""
līlā Spatial Index — Neighbor queries for entity interactions.

Provides a strategy interface for spatial neighbor lookups so the engine's
hot path can swap implementations without changing orchestration code.

Current implementation: brute-force O(n) scan (fine for <100 entities).
Planned upgrade: grid-hash O(1) neighbor queries when entity count grows.

Usage
─────
    spatial = BruteForceSpatialIndex()
    spatial.rebuild(entities)
    nearby = spatial.query(entity["position"], radius, exclude_id=entity["id"])

See Also:
- ``engine.py`` — uses SpatialIndex in _build_interaction_context()
"""

from __future__ import annotations

import math
from typing import Any, Protocol


# ── Canonical distance helper ────────────────────────────────────────────────

def distance_2d(a: list[float], b: list[float]) -> float:
    """2D Euclidean distance on the XZ plane (Y is vertical).

    This is the canonical implementation used by both the spatial index
    and movement actors. Avoid duplicating this formula across modules.
    """
    dx = a[0] - b[0]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dz * dz)


# ── Spatial Index Protocol ───────────────────────────────────────────────────

class SpatialIndex(Protocol):
    """Strategy interface for spatial neighbor queries.

    Implementations can range from brute-force O(n) to grid-hash O(1).
    The engine only depends on this protocol, not any concrete class.
    """

    def rebuild(self, entities: dict[str, dict[str, Any]]) -> None:
        """Rebuild the index from current entity positions.

        Called once per tick at the start of step(). Only living entities
        should be indexed (the caller filters with is_alive()).

        Args:
            entities: Full entity registry keyed by id.
        """
        ...

    def query(
        self,
        pos: list[float],
        radius: float,
        exclude_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find all indexed entities within radius of pos.

        Args:
            pos: Query center as [x, y, z].
            radius: Search radius in world units.
            exclude_id: Optional entity id to skip (usually the querier).

        Returns:
            List of matching living entity dicts.
        """
        ...


# ── Brute-Force Implementation ───────────────────────────────────────────────

class BruteForceSpatialIndex:
    """Brute-force spatial index — O(n) rebuild, O(n) query.

    Suitable for small worlds (<100 entities). Stores a flat dict of
    ``{eid: [x, y, z]}`` and scans it linearly on each query.

    This is the current default. Replace with GridHashSpatialIndex when
    entity counts grow by changing one line in engine init.
    """

    def __init__(self) -> None:
        self._positions: dict[str, list[float]] = {}

    def rebuild(self, entities: dict[str, dict[str, Any]]) -> None:
        """Rebuild position index from living entities."""
        self._positions = {
            eid: list(e["position"])
            for eid, e in entities.items()
            if e["state"] not in ("DEAD", "DYING")
        }

    def query(
        self,
        pos: list[float],
        radius: float,
        exclude_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find all indexed entities within radius of pos."""
        # Not used by this implementation but kept for protocol conformance
        _ = pos, radius, exclude_id
        results: list[dict[str, Any]] = []
        r2 = radius * radius

        for eid, epos in self._positions.items():
            if eid == exclude_id:
                continue
            dx = pos[0] - epos[0]
            dz = pos[2] - epos[2]
            if dx * dx + dz * dz <= r2:
                # Note: caller must pass entities ref for live lookup.
                # We store positions only; entity dicts are looked up by the caller.
                results.append(eid)

        return results  # type: ignore[return-value]


# ── Engine convenience wrapper ───────────────────────────────────────────────

class SpatialQuery:
    """Thin wrapper that bridges BruteForceSpatialIndex to engine usage.

    The brute-force index stores positions only (for cache efficiency).
    This wrapper holds the entity registry and resolves ids → dicts on query,
    filtering out entities that died between rebuild and query time.

    Usage in engine:
        self._spatial = SpatialQuery()
        self._spatial.rebuild(entities)
        nearby = self._spatial.query(pos, radius, exclude_id=eid)
    """

    def __init__(self) -> None:
        self._index = BruteForceSpatialIndex()
        self._entities: dict[str, dict[str, Any]] | None = None

    def rebuild(self, entities: dict[str, dict[str, Any]]) -> None:
        """Rebuild the spatial index from current entity registry."""
        self._entities = entities
        self._index.rebuild(entities)

    def query(
        self,
        pos: list[float],
        radius: float,
        exclude_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find all living entities within radius of pos."""
        if self._entities is None:
            return []

        # BruteForceSpatialIndex returns entity ids; resolve to dicts here.
        raw_ids = self._index.query(pos, radius, exclude_id)
        results: list[dict[str, Any]] = []
        for eid in raw_ids:
            entity = self._entities.get(eid)
            if entity and entity["state"] not in ("DEAD", "DYING"):
                results.append(entity)
        return results
