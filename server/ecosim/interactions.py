# līlā — BYOM Ecosystem Simulation Engine
# Copyright 2025 BioSynthArt Studios LLC
# Licensed under the Apache License, Version 2.0
#
# ecosim/interactions.py — Parameterized interaction templates
#
# Replaces per-species-pair interaction code with a small set of templates.
# Each template encodes an ecological interaction class (herbivory, predation,
# pollination, decomposition) parameterized by trait compatibility. The
# TraitCompiler evaluates all (actor, target) pairs at init time to build
# a sparse interaction matrix — no per-tick species checks needed.
#
# References:
#   Harfoot et al. 2014 — Madingley Model interaction classes
#   Brown et al. 2004   — consumption ∝ metabolic rate (MTE)
#
# stdlib only — no external dependencies.

from __future__ import annotations

from dataclasses import dataclass

from .traits import TraitVector

# ─────────────────────────────────────────────────────────────────────────────
# Predation mass-ratio windows by diet category
# ─────────────────────────────────────────────────────────────────────────────
# These are ecological constants, not tunable parameters. Well-established
# from body-size ecology literature.
#
# Key: actor's diet category → (min_ratio, max_ratio) where
#   ratio = actor_mass / target_mass
# A ratio of 0.5 means the predator is half the prey's mass.
# A ratio of 50 means the predator is 50× the prey's mass.

MASS_RATIO_WINDOWS: dict[str, tuple[float, float]] = {
    "carnivore":    (0.1, 2.0),     # mammalian predation: predators ≤ 2× prey mass
    "insectivore":  (1.0, 1000.0),  # insectivory: predator always much larger
    "omnivore":     (0.1, 100.0),   # broader window for generalist feeders
    "piscivore":    (0.01, 10.0),   # fish predation: wide range
}

# Linger time for pollinators (ticks spent at a flower, inversely ∝ metabolic rate)
POLLINATION_LINGER_BASE = 20.0      # ticks at reference metabolic rate (deer=1.0)
POLLINATION_COOLDOWN_TICKS = 50     # ticks before a flower can be re-pollinated


# ─────────────────────────────────────────────────────────────────────────────
# Interaction Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InteractionParams:
    """Parameters for a matched interaction between two species.

    Produced by InteractionTemplate.compute_rates() and stored in the
    interaction matrix. The engine reads these at interaction time.
    """
    interaction_type: str           # "herbivory", "predation", "pollination",
                                    # "decomposition"
    actor_species: str              # species_id of the acting entity
    target_species: str             # species_id of the target entity (or "__voxel__")

    consumption_rate: float = 0.0   # resource consumed per event
    preference_order: int = 0       # lower = preferred (for diet_breadth ordering)
    linger_ticks: int = 0           # ticks actor stays at target (pollination)
    cooldown_ticks: int = 0         # ticks target is unavailable after interaction
    capture_probability: float = 1.0  # predation success rate
    flee_trigger: bool = False      # does the target flee from the actor?
    mass_ratio: float = 0.0         # actor/target mass ratio (for diagnostics)

    # Decomposition-specific
    mineralization_boost: float = 0.0  # multiplier on local mineralization rate


# ─────────────────────────────────────────────────────────────────────────────
# Interaction Templates
# ─────────────────────────────────────────────────────────────────────────────

class InteractionTemplate:
    """Base class for ecological interaction templates.

    Subclasses implement matches() and compute_rates(). The TraitCompiler
    evaluates these at init time for every (actor, target) pair.
    """
    interaction_type: str = ""

    def matches(self, actor: TraitVector, target: TraitVector) -> bool:
        """Does this interaction apply between actor and target?"""
        raise NotImplementedError

    def compute_rates(self, actor: TraitVector, target: TraitVector,
                      actor_metabolic_rate: float) -> InteractionParams:
        """Compute interaction-specific parameters."""
        raise NotImplementedError


class Herbivory(InteractionTemplate):
    """Herbivore/omnivore consuming plants.

    Match: actor diet_breadth tags overlap with target resource_tags.
    Target must be a plant or tree.

    Examples:
      deer ["graminoid", "forb"] × grass ["graminoid"] → match (preference 0)
      deer ["graminoid", "forb"] × wildflower ["forb"] → match (preference 1)
      butterfly ["forb:fruiting"] × wildflower ["forb"] → no match (tag mismatch;
        nectarivory is handled by Pollination, not Herbivory)
    """
    interaction_type = "herbivory"

    def matches(self, actor: TraitVector, target: TraitVector) -> bool:
        if actor.diet_type not in ("herbivore", "omnivore"):
            return False
        if not target.is_plant:
            return False
        return self._tag_overlap(actor.diet_breadth, target.resource_tags)

    def compute_rates(self, actor: TraitVector, target: TraitVector,
                      actor_metabolic_rate: float) -> InteractionParams:
        # Preference order: position of first matching tag in diet_breadth
        pref = self._preference_index(actor.diet_breadth, target.resource_tags)

        return InteractionParams(
            interaction_type=self.interaction_type,
            actor_species=actor.species_id,
            target_species=target.species_id,
            consumption_rate=actor_metabolic_rate * 0.05,
            preference_order=pref,
        )

    @staticmethod
    def _tag_overlap(diet_breadth: list[str], resource_tags: list[str]) -> bool:
        """Check if any diet tag matches any resource tag.

        Supports exact match ("graminoid") and state-qualified match
        ("forb:fruiting" matches "forb" only if checked at interaction time
        with state=FRUITING — but at the template level, we match the base tag).
        """
        for diet_tag in diet_breadth:
            base_diet = diet_tag.split(":")[0]
            for res_tag in resource_tags:
                base_res = res_tag.split(":")[0]
                if base_diet == base_res:
                    return True
        return False

    @staticmethod
    def _preference_index(diet_breadth: list[str], resource_tags: list[str]) -> int:
        """Index of first matching tag in diet_breadth (lower = preferred)."""
        for i, diet_tag in enumerate(diet_breadth):
            base_diet = diet_tag.split(":")[0]
            for res_tag in resource_tags:
                base_res = res_tag.split(":")[0]
                if base_diet == base_res:
                    return i
        return 999


class Predation(InteractionTemplate):
    """Carnivore/insectivore hunting prey.

    Match: actor diet_breadth includes target's functional_group, AND body
    mass ratio falls within the diet-category-specific window.

    The mass-ratio window handles the ecological constraint that carnivores
    can't take prey much larger than themselves, but insectivores are always
    much larger than their insect prey.
    """
    interaction_type = "predation"

    def matches(self, actor: TraitVector, target: TraitVector) -> bool:
        if actor.diet_type not in ("carnivore", "insectivore", "omnivore"):
            return False
        if target.is_plant:
            return False
        # Actor's diet_breadth must reference target's functional group
        if not self._diet_targets_group(actor.diet_breadth, target.functional_group):
            return False
        # Body mass ratio check
        ratio_window = self._get_ratio_window(actor.diet_type, actor.diet_breadth)
        if target.body_mass_kg <= 0:
            return False
        ratio = actor.body_mass_kg / target.body_mass_kg
        return ratio_window[0] <= ratio <= ratio_window[1]

    def compute_rates(self, actor: TraitVector, target: TraitVector,
                      actor_metabolic_rate: float) -> InteractionParams:
        ratio = actor.body_mass_kg / target.body_mass_kg if target.body_mass_kg > 0 else 0

        # Capture probability: faster predator = higher success
        actor_speed = _approx_speed(actor)
        target_speed = _approx_speed(target)
        if actor_speed + target_speed > 0:
            capture_prob = min(0.95, actor_speed / (actor_speed + target_speed) + 0.2)
        else:
            capture_prob = 0.5

        return InteractionParams(
            interaction_type=self.interaction_type,
            actor_species=actor.species_id,
            target_species=target.species_id,
            consumption_rate=actor_metabolic_rate * 0.1,  # predation yields more per event
            capture_probability=capture_prob,
            flee_trigger=True,
            mass_ratio=ratio,
        )

    @staticmethod
    def _diet_targets_group(diet_breadth: list[str], group: str) -> bool:
        """Does diet_breadth reference this functional group?"""
        return group in diet_breadth

    @staticmethod
    def _get_ratio_window(diet_type: str, diet_breadth: list[str]) -> tuple[float, float]:
        """Mass-ratio window for this predator's diet category.

        If diet_breadth contains insect-targeting tags, use the insectivory window
        even if diet_type is "omnivore".
        """
        # Check for insectivory signals in diet_breadth
        insect_groups = {"pollinator", "insect", "arthropod"}
        if any(tag in insect_groups for tag in diet_breadth):
            return MASS_RATIO_WINDOWS.get("insectivore", (1.0, 1000.0))

        return MASS_RATIO_WINDOWS.get(diet_type, (0.1, 2.0))


class Pollination(InteractionTemplate):
    """Pollinator visiting flowering plants.

    Match: actor has floral_affinity matching target's pollination_syndrome.
    At interaction time, the engine also checks target state == FRUITING.

    The template provides linger time (inversely proportional to metabolic rate —
    fast metabolisms spend less time per flower) and cooldown on the target.
    """
    interaction_type = "pollination"

    def matches(self, actor: TraitVector, target: TraitVector) -> bool:
        if actor.diet_type not in ("nectarivore", "omnivore"):
            return False
        if not actor.floral_affinity:
            return False
        if not target.pollination_syndrome:
            return False
        # Check affinity match
        return target.pollination_syndrome in actor.floral_affinity or \
               any(aff in target.pollination_syndrome for aff in actor.floral_affinity)

    def compute_rates(self, actor: TraitVector, target: TraitVector,
                      actor_metabolic_rate: float) -> InteractionParams:
        # Linger time: smaller/faster pollinators spend less time per flower.
        # Use log-scale relationship capped to a reasonable range [5, 50] ticks.
        # At deer-scale BMR (1.0): linger ≈ 20. At butterfly scale: linger ≈ 15-25.
        if actor_metabolic_rate > 0.001:
            linger = max(5, min(50, int(POLLINATION_LINGER_BASE /
                                        (actor_metabolic_rate ** 0.3))))
        else:
            # Very small organisms: use moderate default
            linger = int(POLLINATION_LINGER_BASE)

        return InteractionParams(
            interaction_type=self.interaction_type,
            actor_species=actor.species_id,
            target_species=target.species_id,
            linger_ticks=linger,
            cooldown_ticks=POLLINATION_COOLDOWN_TICKS,
        )


class Decomposition(InteractionTemplate):
    """Decomposer converting dead organic matter to soil nutrients.

    Unique: the "target" is the voxel organic_matter layer, not an entity.
    At the template level, we check that the actor is a decomposer. The actual
    interaction happens between the entity and the voxel cell at its position.

    For the interaction matrix, the target is a synthetic species_id "__voxel__"
    representing the organic_matter layer.
    """
    interaction_type = "decomposition"

    def matches(self, actor: TraitVector, target: TraitVector) -> bool:
        """Decomposition doesn't match against other species.

        Instead, the TraitCompiler registers decomposers separately and the
        engine checks organic_matter levels at the decomposer's position.
        """
        return False  # Never matched via the pair-wise matrix

    def matches_voxel(self, actor: TraitVector) -> bool:
        """Does this species decompose organic matter?"""
        return actor.diet_type == "decomposer"

    def compute_voxel_rates(self, actor: TraitVector,
                            actor_metabolic_rate: float) -> InteractionParams:
        """Rates for decomposer ↔ voxel interaction."""
        # Boost scales with metabolic rate — bigger/faster decomposers
        # process organic matter faster
        boost = max(0.5, actor_metabolic_rate * 5.0) if actor_metabolic_rate > 0 else 1.0

        return InteractionParams(
            interaction_type=self.interaction_type,
            actor_species=actor.species_id,
            target_species="__voxel__",
            mineralization_boost=boost,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Template Registry
# ─────────────────────────────────────────────────────────────────────────────

# Canonical list of all interaction templates. The TraitCompiler iterates
# these when building the interaction matrix.
ALL_TEMPLATES: list[InteractionTemplate] = [
    Herbivory(),
    Predation(),
    Pollination(),
    Decomposition(),
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _approx_speed(traits: TraitVector) -> float:
    """Quick speed estimate for capture probability (avoids circular import).

    Uses the same scaling laws as traits.derive_speed but with inline constants.
    """
    m = traits.body_mass_kg
    if traits.locomotion in ("sessile", "rooted"):
        return 0.0
    elif traits.locomotion == "flight_insect":
        return 0.60 * (m ** 0.17)
    elif traits.locomotion == "flight_bird":
        return 0.55 * (m ** 0.17)
    elif traits.locomotion in ("quadruped", "biped"):
        return 0.0502 * (m ** 0.25)
    return 0.0
