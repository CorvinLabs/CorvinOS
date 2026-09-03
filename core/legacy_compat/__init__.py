"""
Legacy Compat Layer — Phase B (ADR-0538)

Transparent routing of deprecated Brain/Vibe/Context-v1 APIs → ACP Skills.

This layer makes Phase B safe: old APIs still work (backward compatible),
but they transparently call new Skills under the hood. Telemetry tracks
every call, enabling Phase C measurement gates.

**Key Properties (Load-Bearing):**
1. **Fail-closed:** Errors propagate or retry with audit (never silent fallback)
2. **Transparent:** Callers see no difference (same input/output shape)
3. **Auditable:** Every call logged to ADR-0314 audit trail
4. **Temporary:** Compat layer retired in Phase C (week 10+) after zero calls
5. **Tenant-safe:** All calls tenant-scoped (GDPR Art. 5, 6, 32)

**Timeline:**
- Phase B (weeks 3–4): Compat layer live, old code still present
- Phase C (weeks 5–8): Measured deletion (only if <5 compat calls/day)
- Week 10+: Compat layer retired, old code gone

**Usage (callers don't change):**
```python
# Old import still works via compat layer
from core.legacy_compat.brain_compat import get_session_context
ctx = get_session_context(task_id="my_task")
# Internally: calls os.context_adapter Skill
```

**Testing:**
- Unit tests verify compat output matches old API shape
- E2E tests verify Skill is called (audit trail check)
- Load tests verify <5ms added latency
"""

__all__ = [
    "brain_compat",
    "vibe_compat",
    "context_compat",
]
