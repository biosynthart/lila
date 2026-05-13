"""ASAL-compatible substrate wrapping the līlā EcosystemEngine.

Provides the Init/Step/Render interface that the search loop expects.
The engine is a black box — this module imports it, feeds it a world
config from theta, and extracts rendered frames.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from lila_search.theta import ThetaSpec, make_eco_rates_spec, theta_to_world_config
from lila_search.renderer import render_headless

# Import ecosim — adjust this path if your PYTHONPATH or package
# installation differs. The search package depends on ecosim being
# importable (either installed via pip or on sys.path).
from ecosim.engine import EcosystemEngine


class SimState:
    """Opaque simulation state passed between init/step/render."""
    __slots__ = ("engine", "tick", "config", "rain_interval", "rain_intensity")

    def __init__(self, engine: Any, tick: int, config: dict):
        self.engine = engine
        self.tick = tick
        self.config = config
        rain_cfg = config.get("rain", {})
        self.rain_interval = rain_cfg.get("interval", 0)
        self.rain_intensity = rain_cfg.get("intensity", 0.8)


class LilaSubstrate:
    """ASAL-compatible substrate interface for līlā.

    Usage::

        substrate = LilaSubstrate()
        state = substrate.init(theta, seed=42)
        for _ in range(2000):
            state = substrate.step(state)
        frame = substrate.render(state)  # (256, 256, 3) uint8
    """

    def __init__(self, spec: ThetaSpec | None = None, img_size: int = 256):
        self.spec = spec or make_eco_rates_spec()
        self.img_size = img_size

    def theta_spec(self) -> ThetaSpec:
        """Describes the parameter space: names, ranges, types."""
        return self.spec

    def init(self, theta: np.ndarray, seed: int = 0) -> SimState:
        """Initialize simulation state from parameter vector θ.

        Converts θ to a world config, constructs the engine, and
        returns an opaque state object.
        """
        theta = self.spec.clip(theta)
        config = theta_to_world_config(theta, seed=seed)
        engine = EcosystemEngine(config)
        return SimState(engine=engine, tick=0, config=config)

    def step(self, state: SimState) -> SimState:
        """Advance simulation by one tick."""
        state.engine.step()
        state.tick += 1

        # Apply periodic rain if configured
        if (state.rain_interval > 0
                and state.tick > 0
                and state.tick % state.rain_interval == 0):
            state.engine.apply_rain(state.rain_intensity)

        return state

    def render(self, state: SimState) -> np.ndarray:
        """Render current state as RGB image (H, W, 3) uint8."""
        return render_headless(state.engine, img_size=self.img_size)

    def rollout(
        self,
        theta: np.ndarray,
        n_steps: int = 2000,
        n_frames: int = 20,
        seed: int = 0,
    ) -> list[np.ndarray]:
        """Run a full rollout and collect evenly-spaced rendered frames.

        Parameters
        ----------
        theta : np.ndarray
            Parameter vector.
        n_steps : int
            Total simulation ticks.
        n_frames : int
            Number of frames to capture (evenly spaced).
        seed : int
            Random seed for initialization.

        Returns
        -------
        list[np.ndarray]
            List of RGB frames, each (img_size, img_size, 3) uint8.
        """
        state = self.init(theta, seed=seed)
        frames = []
        capture_interval = max(1, n_steps // n_frames)

        for t in range(n_steps):
            state = self.step(state)
            if (t + 1) % capture_interval == 0 and len(frames) < n_frames:
                frames.append(self.render(state))

        # Ensure we have exactly n_frames (capture final state if short)
        if len(frames) < n_frames:
            frames.append(self.render(state))

        return frames
