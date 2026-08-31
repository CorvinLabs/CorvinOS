---
id: ADR-0350
status: proposed
depends_on: [ADR-0347, ADR-0348, ADR-0349]
related: []
commits: []
paths:
  - core/orchestration/config.py
docs:
  - docs/configuration/brain-config-reference.md
---

# ADR-0350 — Configuration-Driven Plugin Loading

**Status:** Proposed  
**Date:** 2026-08-16  

**Summary:** YAML-based configuration for enabling/disabling subsystems without code changes.
- File: ~/.corvin/brain-config.yaml
- Built-in subsystems hardcoded registry
- Custom subsystems loaded from user paths
- Configuration validation on load

See full documentation in `Corvin-ADR/decisions/ADR-0350-configuration-driven-plugin-loading.md`
