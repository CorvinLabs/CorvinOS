# CorvinOS Deployment Strategy — Single-User Mode

**Effective:** 2026-09-11  
**Status:** 🚀 **100% DEPLOYMENT POLICY (No Canary)**  
**Operator:** Shumway  
**Rationale:** Single-user instance → Canary deployments add no value

---

## Executive Summary

CorvinOS runs in a **single-user, single-tenant context** (operator: Shumway). Traditional canary deployments (5%→25%→50%→100% traffic ramps) are designed for multi-user production systems to minimize blast radius on errors.

**Single-user context = No blast radius to minimize.** Any feature either works for the one user or doesn't—ramp speed is irrelevant.

**Decision:** All features deploy at **100% immediately** after passing tests and adversarial review. No canary stages, no time-gated promotions, no separate traffic-ramp infrastructure.

---

## Previous Canary Plans (Reference — DEPRECATED)

The following features had canary deployment plans that are **now obsolete:**

| Feature | Plan | Timeline | ADR | Status |
|---|---|---|---|---|
| **Personas Elimination** | 5%→25%→50%→100% weekly | 2026-09-06 | — | ✅ Tested, 100% deploy now |
| **Infinite Session Engine** | 5%→25%→50%→100% weekly | 2026-09-10 | ADR-0540–0545 | ✅ Tested, 100% deploy now |
| **Model Selection Skill** | Phased rollout | 2026-09-10 | ADR-0641–0644 | ✅ Design ready, 100% deploy |
| **Context Filter (ADR-0528)** | 5%→25%→50%→100% (2.5h total) | 2026-09-10 | ADR-0528 | ✅ Production-ready, 100% deploy |
| **License-Gated Token Savings** | Phased rollout | 2026-09-11 | — | ✅ Ready, 100% deploy |

All these are **production-ready now**. No time waiting for canary gates.

---

## New Deployment Policy: 100% Immediate

### Deployment Checklist (Simplified)

For any new feature (Skill, Plugin, Layer, ADR-driven change):

1. **Code Complete & Tested**
   - [ ] All unit tests pass (100% coverage for new paths)
   - [ ] E2E tests pass (real entry point called, audited)
   - [ ] Adversarial review passed (0 CRITICAL/HIGH findings)

2. **Documentation**
   - [ ] ADR written (if architectural decision)
   - [ ] Docs updated (if user-facing)
   - [ ] Commit message clear

3. **Deploy**
   - [ ] `git push main`
   - [ ] Feature is live **immediately** (100% traffic)
   - [ ] No waiting, no stages, no monitoring gates

4. **Monitor** (Post-deployment, not pre-deployment)
   - [ ] Audit trail working (events logged)
   - [ ] No exceptions in logs
   - [ ] Expected behavior observed

### Removed Artifacts

The following infrastructure is **no longer needed** and can be removed:

| File/Script | Purpose | Action |
|---|---|---|
| `deploy/canary-rollout.sh` | Canary stage orchestrator (ADR-0461) | ❌ REMOVE — not needed |
| `scripts/canary_executor.sh` | Canary executor | ❌ REMOVE — not needed |
| `scripts/production_rollout.sh` | Phased production rollout | ❌ REMOVE — use 100% deploy |
| `ops/canary-*-deployment.yaml` | Kubernetes canary config | ❌ REMOVE — all 100% |
| `deploy/ROLLOUT_v1.0.0.md` | Phased rollout playbook | ❌ REMOVE — obsolete |
| Canary state/decision files | `~/.corvin/canary-deployment/*` | ❌ REMOVE — not needed |
| Canary SLO gates (48h waits) | Gate duration enforcement | ❌ REMOVE — deploy now |

**Note:** These files are not harmful if left in place, but they document a deployment strategy that no longer applies. Remove them for clarity.

### Removed Gates & Timelines

These gates are **no longer enforced:**

| Gate | Old Requirement | Status |
|---|---|---|
| **Health gate at 10%** | 48h minimum healthy at 10% traffic | ❌ REMOVED |
| **Health gate at 50%** | 48h minimum healthy at 50% traffic | ❌ REMOVED |
| **7-day stability gate** | 7d stable before 100% traffic | ❌ REMOVED |
| **Canary promotion window** | 48h–7d per stage | ❌ REMOVED |
| **Prometheus metric collection** | Only for canary health checks | ⚠️ Keep if useful for observability |
| **Alert escalation workflow** | Slack/email alerts at each stage | ⚠️ Keep if useful for ops |

---

## What Still Matters

### LDD Gates (MANDATORY, unchanged)

The Loss-Driven Development gates remain mandatory:

| Gate | Applies? | When |
|---|---|---|
| **E2E Wiring Proof** | ✅ YES | Before any entry-point code is "done" |
| **Adversarial Review** | ✅ YES | Before declaring a feature "done" |
| **ADR Gate** | ✅ YES | After structural decisions |
| **Concept Gate** | ✅ YES | After reusable patterns |
| **Docs-as-Definition-of-Done** | ✅ YES | Before declaring "done" |

**These gates are NOT gates on deployment time.** They are gates on *code quality*. Once passed, code deploys at 100%.

### Audit Trail (MANDATORY, unchanged)

Every feature that deploys must emit audit events. Single-user doesn't weaken audit requirements:

- ✅ Every decision logged to `audit.jsonl`
- ✅ Hash-chain integrity verified at boot
- ✅ Tenant-scoped and immutable
- ✅ Operator can inspect full proof

**Audit is NOT for multi-user safety; it is for operator verification and compliance (GDPR Art. 30, 32).**

### Post-Deployment Monitoring (RECOMMENDED)

Even though there's no multi-user blast radius, monitoring is still useful for:

- **Observability:** "Is this feature actually running?"
- **Performance:** "Did it make the system faster or slower?"
- **Debugging:** "What went wrong if it breaks?"

Recommended metrics to keep:

| Metric | Why Keep |
|---|---|
| `latency_p99_ms` | Know if the system got slower |
| `error_rate` | Know if something broke |
| `audit_completeness` | Compliance requirement (GDPR) |
| `skill_execution_time` | Know if skills are slow |
| `plugin_load_time` | Know if boot is slow |

---

## Deployment Workflow (New)

### Before Push

```bash
# 1. Ensure all tests pass
pytest tests/ -v

# 2. Run adversarial review (code review)
/code-review --level high

# 3. Write ADR if needed (adr_gate)
# → saved to Corvin-ADR/decisions/ before this commit

# 4. Commit with clear message
git commit -m "feat(feature): description

ADR-XXXX documents the architecture (see Corvin-ADR repo).

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"

# 5. Check docs are updated (docs-as-definition-of-done)
# → all user-facing behavior documented
```

### Push → Live

```bash
# That's it. Feature is now live at 100%.
git push main

# Monitor (in background, no blocking)
grep "error\|exception" ~/.corvin/logs/*.log

# Inspect audit trail if questions arise
grep "feature_name" ~/.corvin/audit.jsonl | tail -10
```

### If Something Breaks

```bash
# Rollback (hard reset is OK for single-user):
git reset --hard <prior-commit>
git push --force main

# Investigate in logs
# Fix the bug
# Test locally
# Push again

# Single-user = instant feedback loop
```

---

## Canary Deployments — When (If Ever) to Re-enable

This policy assumes CorvinOS stays single-user. If the operator:

1. **Starts accepting other users** (multi-tenant)
2. **Needs to limit blast radius** (high-stakes production)
3. **Wants to test with real traffic before full rollout** (A/B testing)

...then re-enable canary deployments by:

1. Restoring `deploy/canary-rollout.sh` from git history
2. Documenting a new ADR (e.g., ADR-0750: Multi-Tenant Deployment Strategy)
3. Reverting this document to reference the new policy

Until then: **100% deployment, always.**

---

## Impact on Current Features

### Infinite Session Engine
- **Status:** ✅ Production-ready (2026-09-07)
- **Old plan:** Canary 5%→25%→50%→100% weekly rollout
- **New plan:** 100% deploy now
- **Action:** Push to main if not already done

### Model Selection Skill
- **Status:** ✅ Design complete (2026-09-10)
- **Old plan:** Phased rollout over weeks
- **New plan:** Implement → Test → Deploy 100%
- **Timeline:** Next 2–3 weeks (Phase 1 kickoff)

### Context Filter (ADR-0528)
- **Status:** ✅ Production-ready (2026-09-10)
- **Old plan:** Canary 5%→25%→50%→100% (2.5h)
- **New plan:** 100% deploy now
- **Action:** Push to main if not already done

### DataHub + Unified Creator
- **Status:** ✅ Design complete (2026-09-10)
- **Old plan:** Phased rollout weeks 1–16
- **New plan:** Implement → Test → Deploy 100% (each phase)
- **Timeline:** Weeks 1–3 Phase 1, then 100% deploy

### Skill Forge v2.0
- **Status:** ✅ Design complete (2026-09-11)
- **Old plan:** Canary strategy TBD
- **New plan:** 100% deploy after Phase 1 impl.
- **Timeline:** Weeks 1–2 Phase 1, then 100% deploy

---

## Audit & Compliance

**Does removing canary deployments weaken compliance?**

No. Compliance requirements under GDPR Art. 30, 32 and EU AI Act focus on:

- ✅ **Audit trail:** Every decision logged and auditable
- ✅ **Consent:** User consent gates (ADR-0300+)
- ✅ **Disclosure:** Bot-disclosure card shown (L18)
- ✅ **Reversibility:** Operator can rollback (git)

None of these require canary deployments. They require **observability and auditability**, which this policy maintains.

---

## Summary Table: Features Ready for 100% Deploy

| Feature | Tested? | Audited? | Documented? | Ready? |
|---|---|---|---|---|
| Infinite Session Engine | ✅ 115+ tests | ✅ 0 findings | ✅ Phase E docs | 🚀 YES |
| Context Filter | ✅ 87 tests | ✅ 0 findings | ✅ Full docs | 🚀 YES |
| Model Selection Skill | ✅ Design phase | ✅ 6 vectors | ✅ Design docs | 🚀 Ready for impl. |
| DataHub Creator | ✅ Design phase | ✅ 3×0 review | ✅ Implementation plan | 🚀 Ready for impl. |
| Skill Forge v2.0 | ✅ Design phase | ✅ Design docs | ✅ 4 ADRs | 🚀 Ready for impl. |

---

## References

- **ADR-0461:** Canary Deployment Orchestrator (now obsolete for single-user)
- **ADR-0528:** Context Filter Production Sign-Off (ready for 100% deploy)
- **ADR-0540–0545:** Infinite Session Engine (ready for 100% deploy)
- **ADR-0641–0644:** Model Selection Skill (ready for impl. + 100% deploy)
- **CONCEPT-0035:** DataHub Creator (ready for impl. + 100% deploy)

---

**Last Updated:** 2026-09-11  
**Policy Owner:** Shumway  
**Next Review:** If multi-user support is added (estimated: TBD)
