# DoD Verifier Skill — Quick Start

## Integration (3 Wege)

### 1. **Standalone (jetzt, ohne Console)**
```python
from core.skills.os_skills.definition_of_done_verifier import DoD_VerifierSkill

skill = DoD_VerifierSkill()
result = skill.execute(
    task_id="my_task",
    task_type="api_endpoint",  # or: cli_command, lib_function, plugin, skill
    symbol_name="my_endpoint",
    commit_msg="feat(api): implement with tests"
)

# result.score: 0.0–1.0
# result.passed: True if score >= 0.80
# result.checks: dict of all 5 checks (passed/evidence)
```

### 2. **Console Quality API (beta)**
```bash
# POST /v1/console/quality/dod/verify
curl -X POST http://localhost:8765/v1/console/quality/dod/verify \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "my_task",
    "task_type": "api_endpoint",
    "symbol_name": "my_endpoint",
    "commit_msg": "feat(api): implement with tests"
  }'

# POST /v1/console/quality/dod/feedback
curl -X POST http://localhost:8765/v1/console/quality/dod/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "my_task",
    "dod_score_automatic": 0.75,
    "dod_score_operator": 0.85,
    "reason": "audit_trail check was too harsh",
    "affected_checks": ["audit_trail"],
    "task_type": "api_endpoint"
  }'

# GET /v1/console/quality/dod/weights
curl http://localhost:8765/v1/console/quality/dod/weights?task_type=api_endpoint
```

### 3. **SkillForge Registration (future)**
```bash
# Once SkillForge v2.0 is live:
skill_register core/skills/os_skills/definition_of_done_verifier
skill_inject definition_of_done_verifier  # Auto-loads in future tasks
```

---

## Skill Details

| Field | Value |
|---|---|
| **ID** | `definition_of_done_verifier` |
| **Version** | `2.0.0` |
| **Type** | Quality Gate Skill |
| **Language** | Python 3.11+ |
| **Dependencies** | None (stdlib only) |
| **Timeout** | 30s total (5s per check, circuit breaker) |
| **Concurrency Limit** | 5 parallel executions |

---

## Checks (5 Canonical)

| # | Check | Weight (Default) | Pass Criteria | Fail Reason |
|---|---|---|---|---|
| 1 | **Reachability** | 0.20 | Symbol found in grep (real call site) | No matches, timeout, permission denied |
| 2 | **Audit Trail** | 0.25 | ≥1 audit event with task_id | 0 events, malformed JSON, file missing |
| 3 | **Test Evidence** | 0.20 | Tests pass + output captured | No test output, failures, empty output |
| 4 | **Docs Sync** | 0.20 | `git diff docs/` contains keyword | Keyword not found, git error |
| 5 | **Reproducibility** | 0.15 | Commit msg has reproduce command | No keyword (pytest, python, npm, make, etc.) |

**Scoring:** `Σ(weight_i × check_i) / Σ(weight_i)` where check_i ∈ {0, 1}

**Threshold:** score >= 0.80 → PASS (task can close)

---

## Learning (Weights Adapt)

Operator gives feedback → Skill learns → Next task uses tuned weights.

```bash
# View current weights
cat ~/.corvin/tenants/_default/global/forge/dod_weights.json

# Reset to defaults (if learning diverges)
rm ~/.corvin/tenants/_default/global/forge/dod_weights.json
```

Convergence detection: confidence >= 0.8 (after ~8 feedback samples).

---

## Files & Locations

```
core/skills/os_skills/definition_of_done_verifier/
├── skill.py                      # Main orchestrator (200 LoC)
├── checks/
│   ├── reachability.py          # Grep-based search
│   ├── audit_trail.py           # Scan audit.jsonl
│   ├── test_evidence.py         # Validate pytest output
│   ├── docs_sync.py             # git diff keyword match
│   └── reproducibility.py       # Commit msg analysis
├── scoring.py                   # ScoringEngine (5 checks → 0.0–1.0)
├── weight_optimizer.py          # DoD_WeightOptimizer (learns from feedback)
├── weight_persistence.py        # JSON load/save
├── api_handlers.py              # Outcome sink + feedback collector
├── hardening.py                 # CircuitBreaker + ConcurrencyMonitor
└── manifest.json                # Skill config (weights, timeouts)

tests/skills/
├── test_dod_verifier_phase1.py   # 24 tests
├── test_dod_verifier_phase2.py   # 43 tests
└── test_dod_verifier_phase3_adversarial.py  # 19 tests

docs/
└── dod-verifier-runbook.md       # Operator guide

Corvin-ADR/decisions/
├── ADR-0721-*.md                 # Skill architecture
├── ADR-0722-*.md                 # Learning loop
└── ADR-0723-*.md                 # Implementation plan
```

---

## Status

✅ **Production Ready**
- 86 tests passing (Phase 1 + 2 + 3)
- 0 CRITICAL/HIGH security findings
- Fail-closed design (all missing evidence = fail)
- Audit-first (every decision logged)
- Hardened (timeouts, concurrency, monitoring)

---

## Next Steps

1. ✅ **Skill is working** — Use via Python import or Console API
2. ⏳ **Console UI** — React component wire-in (1–2 days, optional)
3. ⏳ **Dashboard** — Vibe learning trends panel (1–2 days, optional)
4. ⏳ **Full Integration** — EventStore + Monitoring + CLI (3 days, optional)

---

## Support

- **Runbook:** `docs/dod-verifier-runbook.md`
- **Design:** ADR-0721/0722/0723 (Corvin-ADR repo)
- **Concept:** CONCEPT-0043 (Corvin-ADR repo)

---

**🚀 Skill is ready. Use it now.**
