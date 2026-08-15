---
id: ADR-0348
status: proposed
depends_on: [ADR-0347]
related: [ADR-0349, ADR-0350]
commits: []
paths:
  - core/orchestration/hub.py
docs:
  - docs/concepts/brain-event-catalog.md
---

# ADR-0348 — Event Bus Pattern (Pub/Sub Communication)

**Status:** Proposed  
**Date:** 2026-08-16  

**Summary:** Async pub/sub event bus for one-way broadcasts between subsystems.
- Non-blocking queue-based delivery
- Concurrent subscriber execution
- Error isolation (crashes don't cascade)
- Core events: task_started, error_detected, strategy_applied, etc.

See full documentation in `Corvin-ADR/decisions/ADR-0348-event-bus-pattern.md`
