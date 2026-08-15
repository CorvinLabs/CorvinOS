---
id: ADR-0347
status: proposed
depends_on: [ADR-0009]
related: [ADR-0348, ADR-0349, ADR-0350]
commits: []
paths:
  - core/orchestration/brain.py
  - core/orchestration/hub.py
docs:
  - docs/concepts/brain-architecture.md
---

# ADR-0347 — Brain Subsystem Hub Architecture

**Status:** Proposed  
**Date:** 2026-08-16  
**Deciders:** Shumway, Claude Code  

See full documentation in `Corvin-ADR/decisions/ADR-0347-brain-subsystem-hub-architecture.md`

Core: Central coordinator pattern for autonomous task orchestration.
- TaskBrain orchestration engine
- SubsystemHub event bus + request router
- Loose coupling via hub

Enables extensible Brain with multiple autonomous subsystems.
