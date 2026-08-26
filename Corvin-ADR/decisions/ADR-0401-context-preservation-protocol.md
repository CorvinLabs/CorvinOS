---
id: ADR-0401
status: ACCEPTED
depends_on: [ADR-0363, ADR-0362]
relates_to: [ADR-0348, ADR-0349]
paths:
  - core/vibe_engineering/task_registry.py
  - core/vibe_engineering/checkpoint_manager.py
docs:
  - docs/architecture/context-preservation-two-layers.md
---

# ADR-0401: Context-Preservation Protocol — Two-Layer Model

## Problem

Session renewal breaks context. Must preserve original task intent across session boundaries without token bloat.

## Solution

Two-layer immutable + additive model:
- **Layer 1 (Original Context):** Frozen at session entry, never modified. Task description, user prefs, tenant_id.
- **Layer 2 (Pipeline Context):** Additive only, accumulates during session. Phase summaries, learned patterns, decisions why.

## Implementation

- `OriginalContext` (frozen dataclass)
- `PipelineContext` (append-only list)
- `SessionCheckpoint` serializes both layers
- `RecoveryEngine` restores on resume (Layer 1 unchanged, Layer 2 empty for new session)

## Trade-offs

✅ Prevents task drift, GDPR-compliant (data minimization)
❌ Two-layer logic adds complexity, dropped context marked but not recovered (Phase 2)

## Verification

Week 3-4: E2E tests verify 100% fidelity on cross-session resume
