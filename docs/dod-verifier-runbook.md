# Definition-of-Done Verifier — Operator Runbook

## Overview

The DoD Verifier Skill evaluates task completion against 5 canonical checks. A score < 0.80 blocks task closure; >= 0.80 allows it.

**Key Principle:** Fail-closed. Missing evidence always means task is not done.

## Quick Start

### Running Verification

```bash
# Call the Skill (from CLI or console)
dod_verify --task-id=my_task --task-type=api_endpoint \
  --symbol-name=delete_endpoint \
  --commit-msg="feat(api): delete endpoint with pytest"

# Returns: score (0.0–1.0), passed (bool), checks breakdown, reason
```

### Interpreting Results

```
Score: 75.0%
Passed: ❌ NO

Checks:
  ✅ reachability    (weight: 20%) — found call site
  ❌ audit_trail     (weight: 25%) — 0 events logged
  ✅ test_evidence   (weight: 20%) — tests passed
  ✅ docs_sync       (weight: 20%) — docs updated
  ✅ reproducibility (weight: 15%) — reproduce cmd found

Reason: Task incomplete. Failed checks: audit_trail. Score: 75.0%
```

## Interpreting Checks

### ✅ Reachability (20% by default)
**What:** Is there a real call site outside tests?

**Pass:** Symbol found in grep (outside `test/` dirs)
**Fail:** No matches, grep timeout, permission denied

**If failing:** 
- Endpoint exists but nobody calls it? → Wire into router/endpoints
- CLI command defined but not registered? → Add to CLI parser
- Plugin exported but bootstrap doesn't load it? → Check plugin registry

### ✅ Audit Trail (25% by default, learned)
**What:** Are there audit events in audit.jsonl?

**Pass:** >= 1 events with matching task_id
**Fail:** 0 events, malformed JSON, audit file missing

**If failing:**
- Code executed but didn't log? → Verify audit backend is wired
- Skill calls `_emit_audit_event()`? → Check EventStore integration (Phase 3)
- File permissions? → Ensure `~/.corvin/.../audit.jsonl` is writable

### ✅ Test Evidence (20% by default)
**What:** Do tests exist, pass, and produce output?

**Pass:** pytest output found, "passed" keyword, no failures
**Fail:** No test output file, failures detected, empty output

**If failing:**
- Tests don't exist? → Write them first
- Tests exist but output not captured? → Run pytest with `-s` flag
- Tests fail? → Fix the failures

### ✅ Docs Sync (20% by default)
**What:** Do docs mention the code changes?

**Pass:** `git diff docs/` contains keyword (default: "feat:")
**Fail:** Keyword not found, git error

**If failing:**
- Docs not updated? → Update `docs/` to reference the change
- Wrong keyword? → Default is "feat:" — adjust if needed
- No docs/ folder? → Create it or adjust git diff path

### ✅ Reproducibility (15% by default)
**What:** Is there a reproduce command in commit message?

**Pass:** Commit msg contains pytest, python, npm, make, bash, etc.
**Fail:** No reproduce keyword found

**If failing:**
- Commit message missing instructions? → Add: `# Reproduce: pytest tests/test_xyz.py`
- Different build tool? → Add the command (cargo, go run, docker, etc.)

## Learning (Weights)

Weights adapt based on operator feedback. When you confirm/correct a score:

```bash
# Operator feedback
Score was: 65% (Skill said too low)
You say:   85% (Actually done)
Reason:    "audit_trail check was too harsh"

↓

System learns: for api_endpoints, audit_trail weight should be HIGHER (0.25 → 0.28)

↓

Next api_endpoint uses new weights (0.28 instead of 0.25)
```

### Checking Learned Weights

```bash
# View current weights (all task types)
cat ~/.corvin/tenants/_default/global/forge/dod_weights.json

# View convergence status
dod_status --task-type=api_endpoint
# Returns: converged (bool), sample_count, confidence per weight
```

### Resetting Weights to Defaults

If learning goes wrong (weights diverged), reset to defaults:

```bash
rm ~/.corvin/tenants/_default/global/forge/dod_weights.json
# System will fallback to defaults on next run
```

## Troubleshooting

### "Score was too harsh" — Operator feedback wasn't heeded

**Symptom:** Similar tasks keep getting low scores even though they're done.

**Root cause:** Weights haven't converged yet (confidence < 0.8).

**Fix:** 
1. Give feedback on a few more similar tasks
2. Check convergence: `dod_status --task-type=your_type`
3. If still low confidence after 8+ feedback events, reset weights

### "Audit trail check always fails"

**Symptom:** `audit_trail` check fails for all tasks.

**Root cause:** Audit events not being emitted (EventStore not wired).

**Fix:**
1. Verify `~/.corvin/.../audit.jsonl` exists and is writable
2. Check DoD_OutcomeSink is being called: `tail -100 ~/.corvin/.../audit.jsonl | grep dod_verified`
3. If no events: wire Phase 2 API handler to Skill.execute()

### "Reachability times out"

**Symptom:** Reachability check hangs (5s timeout).

**Root cause:** Grep stuck on symlink loops or large dirs.

**Fix:**
1. Check for symlink loops: `find /path/to/repo -type l | head`
2. Remove problematic symlinks or exclude via `--exclude-dir`
3. Run manual grep to verify: `grep -r symbol /repo --exclude-dir=test`

### "Reproducibility always fails"

**Symptom:** Reproducibility check never passes.

**Root cause:** Commit message doesn't contain a recognized keyword.

**Fix:**
1. Supported keywords: pytest, python, npm, make, bash, sh, cargo, go run, maven, gradle, docker
2. Update commit msg: `git commit --amend -m "feat: ... \n\nReproduce: pytest tests/test_xyz.py"`

## Monitoring (Phase 3)

Monitor these metrics:

| Metric | Warning | Critical |
|---|---|---|
| P99 Latency | > 20s | > 30s |
| Error Rate | > 5% | > 10% |
| Timeouts | 1+ per 100 | 5+ per 100 |
| Weight Divergence | confidence > 1.0 (impossible) | Σ(weights) != 1.0 |

## Performance

**Typical latency:**
- P50: 2–3s (all checks, mocked audit)
- P99: 5–8s (grep on large repos)
- Timeout: 30s (circuit breaker)

**Concurrency limit:** 5 concurrent DoD verifications (prevents overload)

## Support

- **Bugs:** File in Corvin-ADR/issues
- **Questions:** See ADR-0721, ADR-0722, ADR-0723
- **Design:** Consult CONCEPT-0043 (dialektical synthesis)
