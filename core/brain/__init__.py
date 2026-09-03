"""
⚠️ DEPRECATED — CorvinOS Brain Engineering (L28–L30)

Conversational recall, delegation logic, decision history.

**DEPRECATION NOTICE (ADR-0538):** This module is being phased out in favor of ACP Skills:
- Context recall → `os.context_adapter` Skill (ADR-0532 Phase 1)
- Workflow optimization → `os.workflow_optimizer` Skill (ADR-0532 Phase 2)
- Learning + decision history → ADR-0314 Learning Infrastructure

**Timeline:**
- Phase A (weeks 1–2): Mark deprecated + audit (NOW)
- Phase B (weeks 3–4): Compat layer routes old Brain APIs → Skills transparently
- Phase C (weeks 5–8): Measured deletion (after telemetry confirms 0 live calls)

**Migration:** See docs/implementation/MIGRATION_GUIDE_TO_SKILLS.md

**For new code:** Use ACP Skills (os.context_adapter, os.workflow_optimizer) instead.
**For existing code:** Compat layer (Phase B) maintains API compatibility transparently.

---

This module is currently NOT imported from production code (audit: 0 callsites, ADR-0538).
All imports are test-only. The module itself has no __init__.py (was not importable).
Re-export pattern broken; do not restore.
"""

__all__ = []  # Deprecated; do not import
