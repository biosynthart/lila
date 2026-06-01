# līlā — BYOM Ecosystem Simulation Engine
# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
#
# ecosim/trait_compiler.py — Compile trait vectors into engine-ready parameters
#
# Runs ONCE at world initialization. Produces:
#   1. Per-species DerivedParams (all constants the tick loop needs)
#   2. Sparse interaction matrix (which species interact, how)
#   3. Resource tag registry (maps species → food tags)
#   4. Decomposer registry (which species accelerate mineralization)
#
# Per-tick cost: dict lookups only — O(1). The compiler does the expensive
# work upfront so the tick loop stays fast.
#
# stdlib only — no external dependencies.

from __future__ import annotations

from dataclasses import dataclass, field

from .interactions import (
    ALL_TEMPLATES,
    Decomposition,
    InteractionParams,
)
from .traits import (
    DerivedParams,
    TraitVector,
    derive_all,
    parse_species_definitions,
)

# ─────────────────────────────────────────────────────────────────────────────
# Compiled Output
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompiledEcology:
    """Complete engine-ready output from the trait compiler.

    The engine stores this and reads from it every tick. All the trait-derived
    intelligence lives here — the tick loop is just physics + lookups.
    """

    # species_id → DerivedParams
    derived_params: dict[str, DerivedParams] = field(default_factory=dict)

    # (actor_species_id, target_species_id) → list[InteractionParams]
    # Sparse: only populated for pairs that actually interact.
    interaction_matrix: dict[tuple[str, str], list[InteractionParams]] = field(
        default_factory=dict
    )

    # species_id → list of resource tags this species provides
    resource_tags: dict[str, list[str]] = field(default_factory=dict)

    # species_id → InteractionParams for decomposer↔voxel interaction
    decomposers: dict[str, InteractionParams] = field(default_factory=dict)

    # species_id → TraitVector (kept for runtime queries, e.g. thermal exclusion)
    traits: dict[str, TraitVector] = field(default_factory=dict)

    # species_id → list of species_ids this entity flees from
    flee_from: dict[str, list[str]] = field(default_factory=dict)

    # species_id → list of (target_species_id, preference_order) for foraging
    # Sorted by preference (lower = preferred food source)
    diet_preferences: dict[str, list[tuple[str, int]]] = field(default_factory=dict)

    def get_params(self, species_id: str) -> DerivedParams | None:
        """Get derived params for a species, or None if unknown."""
        return self.derived_params.get(species_id)

    def get_interactions(self, actor_id: str, target_id: str) -> list[InteractionParams]:
        """Get all interactions between actor and target species."""
        return self.interaction_matrix.get((actor_id, target_id), [])

    def get_flee_targets(self, species_id: str) -> list[str]:
        """Get species IDs that this entity flees from."""
        return self.flee_from.get(species_id, [])

    def get_diet_order(self, species_id: str) -> list[tuple[str, int]]:
        """Get preferred food sources in order (target_species, preference)."""
        return self.diet_preferences.get(species_id, [])

    def is_decomposer(self, species_id: str) -> bool:
        return species_id in self.decomposers

    def get_decomposer_params(self, species_id: str) -> InteractionParams | None:
        return self.decomposers.get(species_id)


# ─────────────────────────────────────────────────────────────────────────────
# TraitCompiler
# ─────────────────────────────────────────────────────────────────────────────

class TraitCompiler:
    """Compiles trait vectors into engine-ready parameters.

    Usage:
        traits = parse_species_definitions(world_config)
        compiler = TraitCompiler(traits, biome_config)
        compiled = compiler.compile()
        # Engine stores `compiled` and reads from it every tick.

    The compiler:
      1. Derives per-species params via allometric functions
      2. Evaluates all (actor, target) pairs against interaction templates
      3. Builds flee-from and diet-preference indices for fast engine lookups
      4. Registers decomposers for voxel interaction
    """

    def __init__(self, trait_vectors: list[TraitVector],
                 biome_config: dict | None = None):
        self.trait_vectors = {tv.species_id: tv for tv in trait_vectors}
        self.biome = biome_config or {}
        self._templates = list(ALL_TEMPLATES)

    def compile(self) -> CompiledEcology:
        """Run all derivations and return the compiled ecology."""
        result = CompiledEcology()

        # Phase 1: Derive per-species params
        metabolic_rates: dict[str, float] = {}
        for sid, tv in self.trait_vectors.items():
            params = derive_all(tv)
            result.derived_params[sid] = params
            result.traits[sid] = tv
            result.resource_tags[sid] = list(tv.resource_tags)
            metabolic_rates[sid] = params.metabolic_rate

        # Phase 2: Build interaction matrix
        self._build_interactions(result, metabolic_rates)

        # Phase 3: Build flee-from index (derived from predation interactions)
        self._build_flee_index(result)

        # Phase 4: Build diet preference ordering
        self._build_diet_preferences(result)

        # Phase 5: Register decomposers
        self._register_decomposers(result, metabolic_rates)

        return result

    def _build_interactions(self, result: CompiledEcology,
                            metabolic_rates: dict[str, float]) -> None:
        """Evaluate all (actor, target) pairs against all templates."""
        species_ids = list(self.trait_vectors.keys())

        for actor_id in species_ids:
            actor_tv = self.trait_vectors[actor_id]
            actor_bmr = metabolic_rates[actor_id]

            for target_id in species_ids:
                if actor_id == target_id:
                    continue
                target_tv = self.trait_vectors[target_id]

                matched: list[InteractionParams] = []
                for template in self._templates:
                    if template.matches(actor_tv, target_tv):
                        params = template.compute_rates(actor_tv, target_tv, actor_bmr)
                        matched.append(params)

                if matched:
                    result.interaction_matrix[(actor_id, target_id)] = matched

    def _build_flee_index(self, result: CompiledEcology) -> None:
        """Build species_id → [predator_species_ids] for flee response.

        Any species targeted by a predation interaction should flee from
        that predator when it's within sensory range.
        """
        flee_map: dict[str, list[str]] = {}

        for (actor_id, target_id), interactions in result.interaction_matrix.items():
            for ip in interactions:
                if ip.flee_trigger:
                    if target_id not in flee_map:
                        flee_map[target_id] = []
                    if actor_id not in flee_map[target_id]:
                        flee_map[target_id].append(actor_id)

        result.flee_from = flee_map

    def _build_diet_preferences(self, result: CompiledEcology) -> None:
        """Build species_id → [(target_species, preference)] for foraging.

        Combines herbivory and predation targets, sorted by preference order
        (lower = preferred). The engine uses this to decide which nearby
        target to pursue first.
        """
        diet_map: dict[str, list[tuple[str, int]]] = {}

        for (actor_id, target_id), interactions in result.interaction_matrix.items():
            for ip in interactions:
                if ip.interaction_type in ("herbivory", "predation"):
                    if actor_id not in diet_map:
                        diet_map[actor_id] = []
                    diet_map[actor_id].append((target_id, ip.preference_order))

        # Sort each species' preferences by order
        for sid in diet_map:
            diet_map[sid].sort(key=lambda x: x[1])

        result.diet_preferences = diet_map

    def _register_decomposers(self, result: CompiledEcology,
                              metabolic_rates: dict[str, float]) -> None:
        """Register species that decompose organic matter (voxel interaction)."""
        decomp_template = Decomposition()

        for sid, tv in self.trait_vectors.items():
            if decomp_template.matches_voxel(tv):
                params = decomp_template.compute_voxel_rates(
                    tv, metabolic_rates[sid]
                )
                result.decomposers[sid] = params


# ─────────────────────────────────────────────────────────────────────────────
# Top-level convenience
# ─────────────────────────────────────────────────────────────────────────────

def compile_world(world_config: dict,
                  biome_config: dict | None = None) -> CompiledEcology:
    """Compile a world config into engine-ready parameters.

    Requires ``species_definitions`` in the world config. Raises ValueError
    if none are found — all worlds must define their species via trait vectors.

    This is the main entry point the engine calls at init.
    """
    traits = parse_species_definitions(world_config)
    if not traits:
        raise ValueError(
            "World config missing 'species_definitions'. "
            "All worlds must define species via trait vectors."
        )

    compiler = TraitCompiler(traits, biome_config)
    return compiler.compile()
