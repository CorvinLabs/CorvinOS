---
id: ADR-0349
status: proposed
depends_on: [ADR-0347, ADR-0348]
related: [ADR-0350]
commits: []
paths:
  - core/orchestration/subsystems/base.py
docs:
  - docs/api/subsystem-interface.md
---

# ADR-0349 — Plugin Interface Contract (Subsystem Base Class)

**Status:** Proposed  
**Date:** 2026-08-16  

**Summary:** Formal Subsystem ABC with 5 required methods for Brain subsystems.
- Identity: name, version properties
- Lifecycle: startup(), shutdown()
- Event handling: on_event() async
- Request/response: handle_request() async
- All subsystems (built-in and custom) must implement

See full documentation in `Corvin-ADR/decisions/ADR-0349-plugin-interface-contract.md`
