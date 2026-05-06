<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# līlā — Quick Start

Run the ecosystem simulation locally with Docker Compose.

## Prerequisites

- Docker and Docker Compose installed
- That's it. No Python, no Godot, no cloud account.

## Run

```bash
cd deploy/compose
docker compose up --build
```

Then open **http://localhost:8001** in your browser.

You'll see the meadow ecosystem: two deer grazing, two butterflies
pollinating wildflowers, twelve grass patches growing and being consumed,
oak trees anchoring the landscape. The moisture heatmap shifts as
entities drink and plants uptake water.

## What you're seeing

The browser visualizer connects to the simulation worker over WebSocket.
The worker runs the hybrid automaton engine at 10 Hz, streaming tick
packets with entity state, positions, motion latent vectors, events,
and voxel deltas. The visualizer interpolates between ticks at 60 fps.

Entities with skeletons (deer, butterfly) show a motion latent indicator —
a subtle halo whose intensity reflects the ML-driven animation vector.
In the full Godot client, this vector drives skeletal animation blending.

## Controls

- **☔ Rain** (bottom-right) — trigger rainfall to replenish soil moisture
  and water sources. Watch dormant plants revive.
- **⏺ Record** (bottom-right) — capture a 10-second WebM clip. Click
  again to stop early. The file auto-downloads.

Convert recording to GIF:
```bash
ffmpeg -i lila-recording.webm -vf "fps=15,scale=480:-1" -loop 0 demo.gif
```

## Stop

```bash
docker compose down
```
