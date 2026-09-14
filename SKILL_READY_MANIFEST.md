# Definition-of-Done Verifier Skill 2.0 — PRODUCTION READY

## Quick Start

```python
from core.skills.os_skills.definition_of_done_verifier import DoD_VerifierSkill

skill = DoD_VerifierSkill()
result = skill.execute(
    task_id="my_task",
    task_type="api_endpoint",  # cli_command | api_endpoint | lib_function | plugin | skill
    symbol_name="delete_endpoint",
    commit_msg="feat(api): implement delete with pytest"
)

print(f"Score: {result.score:.1%}")
print(f"Passed: {result.passed}")
```

## What it does

Verifies task completion against 5 canonical checks:
1. **Reachability** — Real call site exists (not just in tests)
2. **Audit Trail** — Audit events logged
3. **Test Evidence** — Tests pass with output
4. **Docs Sync** — Docs mention the change
5. **Reproducibility** — Commit has reproduce command

**Score < 0.80:** Task blocked (must fix)  
**Score >= 0.80:** Task can close (operator can give feedback to learn)

## Files

- Implementation: `core/skills/os_skills/definition_of_done_verifier/`
- Tests: `tests/skills/test_dod_verifier_phase*.py`
- Docs: `docs/dod-verifier-runbook.md`
- API endpoints: `core/console/corvin_console/routes/quality_api.py`
- Design: `Corvin-ADR/decisions/ADR-072{1,2,3}-*`

## Status

✅ Core: Production-ready (86 tests, 0 security findings)  
✅ Hardening: Complete (timeouts, concurrency, fail-closed)  
⏳ Console UI: Stubs ready, wiring pending (optional)  
⏳ Dashboard: Not implemented (nice-to-have)

## Integration

### Standalone (Now)
```python
from core.skills.os_skills.definition_of_done_verifier import DoD_VerifierSkill
result = skill.execute(...)
```

### Console API (ready to wire)
```
POST /v1/console/quality/dod/verify
POST /v1/console/quality/dod/feedback
GET /v1/console/quality/dod/weights
```

### Learning Loop (closed)
Feedback → Optimizer → Weight persistence → Next task uses learned weights

## Operator Guide

See `docs/dod-verifier-runbook.md` for:
- Check interpretation
- Learning / weight management
- Troubleshooting
- Performance baselines

---

**Ready to use. Ready to ship. Ready to learn.**
