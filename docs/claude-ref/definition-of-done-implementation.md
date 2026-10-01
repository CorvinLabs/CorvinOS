# Definition-of-Done Verifier Implementation Guide

**Canonical Reference:** [ADR-0721](../../corvin_decisions/decisions/ADR-0721-dod-verifier-skill-architecture.md), [ADR-0722](../../corvin_decisions/decisions/ADR-0722-dod-loss-signal-learning-integration.md), [ADR-0723](../../corvin_decisions/decisions/ADR-0723-dod-implementation-plan.md)

**Last Updated:** 2026-10-01
**Status:** Implemented and reachable through the console API; HTTP-level E2E in
`core/console/tests/test_dod_verify_route_e2e.py`. No UI panel calls it, and no
optimizer consumes DoD feedback yet.

---

## Overview

The Definition-of-Done Verifier scores a task against five checks and returns a
score in `[0, 1]`; `passed` means `score >= 0.80`. Every verification is chained to
the tenant's audit trail before the result is returned (fail-closed).

**Until 2026-10-01 the verifier could not pass on this repository.** Reachability ran
`grep -r` over the whole checkout (`.venv`, `node_modules`, worktrees) and hit its 5 s
timeout every time, and the audit check looked for a top-level `task_id` that no chain
record carries. Those two checks are 45 % of the weight, so the ceiling was 55 %.

## The five checks

| Check | Weight | Passes when | Implementation |
|---|---|---|---|
| **reachability** | 0.20 | the symbol is used in a tracked code file (`*.py`, `*.ts(x)`, `*.js(x)`, `*.mjs`, `*.sh`, `*.y(a)ml`, `*.toml`) on a line that is not its `def`/`class`/`function` definition, not an import or `__all__` entry, and not in a test file | `git grep -n -w -F -e <symbol>` — whole-word, literal, tracked files only. Skipped (fails) when no `symbol_name` is given |
| **audit_trail** | 0.25 | the tenant chain holds ≥1 record whose `task_id` (top level or under `details`) equals the task id | scans `tenant_audit_chain(tenant)` |
| **test_evidence** | 0.20 | `test_path` exists and `test_output_file` contains `passed` and no failures | reads the captured file |
| **docs_sync** | 0.20 | `git diff <commit_range> -- docs/` contains `keyword` | heuristic |
| **reproducibility** | 0.15 | the commit message names a runnable command (`pytest`, `npm`, `make`, …) | keyword match — it does **not** re-run anything |

Checks run concurrently; a check that raises or exceeds its timeout fails.

```
core/skills/os_skills/definition_of_done_verifier/
  ├── skill.py              # DoD_VerifierSkill: runs the checks, scores
  ├── skill_base_wrapper.py # DoD_VerifierSkillWrapper: chains the result, then returns it
  ├── checks/               # the five checks above
  ├── scoring.py            # weighted score, threshold 0.80
  ├── weight_optimizer.py   # exists; nothing feeds it DoD feedback yet
  └── hardening.py
```

`DoD_VerifierSkill` writes no audit record itself (until 2026-10-01 its
`_emit_audit_event` only printed to stdout). `audit_event_id` in the result is the
digest of the verification, not the id of a chain record.

## Console API

Mounted at `/v1/console/api/dod/*` (`core/console/corvin_console/routes/dod_verifier_dashboard.py`).
Every route needs a console session; `POST` routes also need the `X-CSRF-Token` header.
The tenant comes from the session, never from the body.

### POST /v1/console/api/dod/verify

```json
{
  "task_id": "task_123",
  "task_type": "feature",
  "symbol_name": "DoD_VerifierSkillWrapper",
  "commit_range": "HEAD~5..HEAD",
  "keyword": "feat:",
  "commit_msg": "feat(core): add DoD verifier — pytest tests/…",
  "test_path": "tests/skills/test_dod_reachability.py",
  "test_output_file": "outputs/pytest.txt"
}
```

Input rules (HTTP 400 otherwise) — the body reaches `git` as arguments and names files
to read:

- `commit_range`: a revision or `A..B` / `A...B`; may not start with `-`.
- `symbol_name`: an identifier (`[A-Za-z_][A-Za-z0-9_.]*`).
- `test_path`, `test_output_file`: resolved (symlinks too) and must lie inside the
  checkout. Relative paths are taken from the checkout root.

Response:

```json
{
  "task_id": "task_123",
  "score": 0.8,
  "passed": true,
  "checks": {"reachability": {"passed": true, "evidence": "…", "check_name": "reachability"}, "…": {}},
  "weights": {"w_reach": 0.2, "w_audit": 0.25, "w_test": 0.2, "w_docs": 0.2, "w_repro": 0.15},
  "reason": "Task complete. DoD score: 80.0%",
  "audit_event_id": "<sha256 of the verification>",
  "timestamp": "2026-10-01T…"
}
```

Exactly one `skill.executed` record (`skill_id: os.definition_of_done_verifier`, input
and output hashed) is chained per verification. If it cannot be written the route
answers `500 "Verification audit failed. Task cannot be marked done."` and returns no
score. The result is also stored as a learning `OUTCOME` event, which `/history` reads.

### POST /v1/console/api/dod/feedback

`{"task_id": "…", "check_name": "…", "feedback": "accurate" | "inaccurate" | "not_applicable", "note": "…"}`
→ `{"accepted": true, "feedback_id": "…"}`. Stored as a learning `FEEDBACK` event; the
free-text `note` is not persisted. No weight is adjusted.

### GET /v1/console/api/dod/history/{task_id}?limit=10

→ `{"task_id": "…", "verifications": [{"timestamp": …, "task_id": …, "score": …, "passed": …}]}`

There is no WebSocket stream (the `/stream` stub that closed every connection was
removed 2026-10-01), and the Flask `quality_api.py` blueprint that nothing imported is gone.

## Running it

```bash
# Standalone
python - <<'EOF'
from core.skills.os_skills.definition_of_done_verifier.skill import DoD_VerifierSkill
r = DoD_VerifierSkill().execute(task_id="task_123", task_type="feature",
                                symbol_name="DoD_VerifierSkillWrapper")
print(r.score, r.checks["reachability"])
EOF
```

`DoD_VerifierSkill()` defaults to this checkout and the `_default` tenant chain
(`core.paths.tenant.tenant_audit_chain`), honouring `CORVIN_HOME`.

## Troubleshooting

| Failing check | Usual cause |
|---|---|
| reachability | the symbol is only defined, imported or used in tests — add a real call site; or the file is untracked (`git grep` sees tracked files only) |
| audit_trail | nothing chained a record with this `task_id` (task records carry it under `details`) |
| test_evidence | path outside the checkout (400), or the captured output has no `passed` |
| docs_sync | no change under `docs/` in `commit_range` mentions `keyword` |
| reproducibility | the commit message names no command |

## Testing

```bash
pytest core/console/tests/test_dod_verify_route_e2e.py   # real router, real session, sandbox chain
pytest tests/skills/test_dod_reachability.py             # incl. git grep on a throwaway repo
pytest tests/skills/test_dod_audit_trail.py tests/skills/test_dod_verifier_phase1.py \
       tests/skills/test_dod_verifier_phase2.py tests/skills/test_dod_verifier_phase3_adversarial.py
pytest core/skills/os_skills/definition_of_done_verifier/tests
```

Run the suites in separate processes: `tests/skills/*` puts `core/skills/os_skills/` on
`sys.path`, where its `audit` package shadows the console's.

## Compliance

- Inputs and outputs reach the chain only as SHA-256 digests; error messages are
  chained as a class name only.
- The tenant is taken from the authenticated session.
- Score, checks and reason are always returned together (EU AI Act Art. 50 transparency).

## Related

ADR-0721 (architecture), ADR-0722 (learning integration), ADR-0723 (plan),
ADR-0314 (learning events), ADR-0232/0233 (audit chain), ADR-0532 (Skills 2.0).
