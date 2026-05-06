<!-- 
  līlā — BYOM Ecosystem Simulation Engine
  Copyright 2025 BioSynthArt Studios LLC
  Licensed under the Apache License, Version 2.0
  https://github.com/hellolifeforms/lila
-->

# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in līlā, please report it responsibly
by emailing **admin@hellolifeforms.org**. Do not open a public GitHub issue for
security vulnerabilities.

We will acknowledge receipt within 72 hours and aim to provide a fix or
mitigation plan within 30 days, depending on severity.

## Scope

līlā is an ecosystem simulation engine intended for local and research use.
The WebSocket worker serves on a single port and is not designed for
public-facing deployment without additional hardening. If you deploy līlā
behind a reverse proxy or on a public network, securing that infrastructure
is your responsibility.

## Supported Versions

| Version         | Supported |
|-----------------|-----------|
| v0.0.1-alpha    | ✅        |
| < v0.0.1-alpha  | ❌        |