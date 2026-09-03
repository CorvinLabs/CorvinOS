# Phase C: Measurement Gates & Deletion Checklist (Weeks 5–8)

**Status:** Ready to execute (weeks 5–8, after Phase B complete)  
**Prerequisite:** Phase B compat layer live + stable + plugin migration ≥95%  
**Decision:** Delete old code ONLY if all 5 gates PASS

---

## The Five Measurement Gates (All Must Pass)

### Gate 1: Learning Optimizer Stable (2–3 weeks production)

**Purpose:** Ensure Learning optimizer convergence is stable before deleting old heuristics.

**Measurement:**
```bash
# Check optimizer convergence + fallback rates
grep "optimizer_convergence_rate\|optimizer_fallback_count" ~/.corvin/audit.jsonl | tail -100

# Metrics:
#   - convergence_rate >= 0.95 (95% stable)
#   - fallback_count < 1% of total calls
#   - confidence_score stable (no divergence >0.1)
```

**Pass Threshold:**
- Convergence rate ≥ 0.95
- Fallback rate < 1%
- Confidence volatility < 0.1 (no sudden swings)

**Timeline:** Week 5–7 (2–3 weeks observation)

**If FAIL:** Extend Phase B + C; Learning needs more tuning (ADR-0314)

---

### Gate 2: Old Code Unreachable (<5 compat calls/day)

**Purpose:** Prove old code is no longer called (telemetry proof).

**Measurement:**
```bash
# Count deprecated API calls from audit trail
grep "event_type.*deprecated_api_call" ~/.corvin/audit.jsonl \
  | jq -r '.timestamp' | cut -d'T' -f1 | sort | uniq -c

# By day (e.g., 2026-10-03: 2 calls, 2026-10-04: 0 calls, 2026-10-05: 3 calls)
# Average should be <5/day
```

**Pass Threshold:**
- Mean compat calls/day < 5
- Zero spikes (>10 calls in a day = investigate)

**Timeline:** Week 5–7 (continuous measurement during Phase C)

**If FAIL:** Extend compat layer; investigate new callsites (ADR-0538 Phase C extension)

---

### Gate 3: No Direct Imports (Grep + AST verification)

**Purpose:** Verify no code directly imports old modules (only via compat layer).

**Measurement:**
```bash
# Check for direct old imports (outside compat layer)
grep -r "from core.brain\|from core.vibe_engineering\|ContextSnapshot" \
  core/ --include="*.py" | grep -v "legacy_compat" | grep -v "test_"

# Should return: 0 results (all imports routed through compat layer)
```

**Pass Threshold:**
- Zero direct imports outside compat layer
- All old imports redirected to `core/legacy_compat/*`

**Timeline:** Week 5 (one-time check before deletion)

**If FAIL:** Migrate remaining callsites (Phase B extension)

---

### Gate 4: Plugins Migrated (≥95% using new APIs)

**Purpose:** Ensure plugins have migrated (laggards owned or deprecated).

**Measurement:**
```bash
# Count plugins using old APIs (via compat layer telemetry)
grep "event_type.*deprecated_api_call" ~/.corvin/audit.jsonl \
  | jq -r '.caller_file' | grep "plugins" | sort -u | wc -l

# Should be ≤ 5% of installed plugins
# Find owner for each laggard OR mark plugin "deprecated"
```

**Pass Threshold:**
- ≤ 5% of plugins using compat layer
- All laggards have written deadline (week 7) + owner assigned
- OR laggards formally deprecated (community-maintained, no support)

**Timeline:** Week 5–7 (send final migration reminder week 5, deadline week 7)

**If FAIL:** Phase C delayed; send final notices to plugin authors

---

### Gate 5: Tenant Isolation Safe (Zero cross-tenant leaks)

**Purpose:** Ensure no GDPR violations (cross-tenant data exposure).

**Measurement:**
```bash
# Check audit trail for cross-tenant anomalies
grep "tenant_id" ~/.corvin/audit.jsonl | \
  jq -s 'group_by(.tenant_id) | map({tenant_id: .[0].tenant_id, count: length}) | sort_by(.count)'

# Also check: any call with mismatched tenant_id in caller vs. event?
grep "deprecated_api_call" ~/.corvin/audit.jsonl | \
  jq 'select(.tenant_id != .caller_tenant_id)'
# Should return: 0 results
```

**Pass Threshold:**
- Zero tenant_id mismatches
- All events properly scoped
- No cross-tenant data exposure

**Timeline:** Week 5 (one-time audit before deletion)

**If FAIL:** Investigate + fix tenant isolation bugs before deleting (GDPR Art. 5, 32)

---

## Execution Sequence (Week 5–8)

### Week 5 (Phase C Start)

**Monday (Week 5, Day 1):**
- Run all 5 gates (initial snapshot)
- Check convergence (Gate 1) — should be trending up
- Check compat calls (Gate 2) — should be <10/day by now
- Check imports (Gate 3) — should be 0 direct imports
- Check plugins (Gate 4) — send final migration notice (deadline: week 7 EOD)
- Check tenant isolation (Gate 5) — audit trail clean

**Decision:**
- If 5/5 PASS → proceed to Week 6
- If ≥1 FAIL → extend Phase B or delay Phase C (no forced deletion)

### Week 6–7 (Observation + Plugin Migration)

**Continuous monitoring:**
- Gate 1: Convergence maintains ≥0.95, fallback < 1%
- Gate 2: compat calls trending toward 0 (<5/day avg)
- Gate 3: No new direct imports
- Gate 4: Plugin migration surge (laggards responding to week 5 notice)
- Gate 5: Audit trail clean

**End of Week 7:**
- Final plugin deadline (laggards must migrate or be deprecated)
- All gates should be stable/passing

### Week 8 (Deletion Day)

**Monday (Week 8, Day 1):**
- Final gate verification (all 5 must PASS)
- Decision: DELETE or EXTEND?

**If all 5 PASS:**
```bash
# Execute deletion
git rm -r core/brain/ core/vibe_engineering/ core/context_engineering/legacy_v1.py
git rm core/legacy_compat/  # Compat layer retired
git add docs/implementation/PHASE_C_EXECUTION_REPORT.md

git commit -m "feat(legacy): Phase C — Delete Brain/Vibe/Context-v1 (measured deletion, ADR-0538)

All Phase C gates passed:
- Gate 1: Learning stable ✓
- Gate 2: Old code unreachable ✓  
- Gate 3: No direct imports ✓
- Gate 4: Plugins migrated (≥95%) ✓
- Gate 5: Tenant isolation safe ✓

Deleted:
- core/brain/ (0 production imports, deprecated 2026-09-03)
- core/vibe_engineering/ (unreachable, internal only, deprecated 2026-09-03)
- core/context_engineering/legacy_v1.py (unreachable, deprecated 2026-09-03)
- core/legacy_compat/ (compat layer, mission accomplished, 2 months live)

Audit trail remains (immutable, hash-chained per ADR-0232/0233).

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

**If ≥1 FAIL:**
```bash
# Do NOT delete — extend indefinitely
echo "Phase C delayed: [gate name] did not pass"
echo "Decision: Keep compat layer + old code (coexistence)"
# Update ADR-0538 status: "RESOLVED (safe coexistence mode)"
```

---

## Automated Measurement Script (Phase C Audit)

```bash
#!/bin/bash
# phase_c_measurement.sh — Execute all 5 gates

echo "=== PHASE C MEASUREMENT GATES ==="

# Gate 1: Learning
echo -e "\n[Gate 1] Learning Optimizer Stable:"
grep "optimizer_convergence_rate" ~/.corvin/audit.jsonl | tail -10 | jq '.convergence_rate' | awk '{sum+=$1; count++} END {print "Mean:", sum/count}'

# Gate 2: Compat usage
echo -e "\n[Gate 2] Old Code Unreachable (<5 calls/day):"
grep "deprecated_api_call" ~/.corvin/audit.jsonl | jq -r '.timestamp' | cut -d'T' -f1 | uniq -c | sort | tail -7

# Gate 3: No direct imports
echo -e "\n[Gate 3] No Direct Imports (should be 0):"
grep -r "from core.brain\|from core.vibe_engineering" core/ --include="*.py" | grep -v "legacy_compat" | grep -v "test_" | wc -l

# Gate 4: Plugins
echo -e "\n[Gate 4] Plugins Using Compat Layer:"
grep "deprecated_api_call" ~/.corvin/audit.jsonl | jq -r '.caller_file' | grep "plugins" | sort -u | wc -l

# Gate 5: Tenant safety
echo -e "\n[Gate 5] Tenant Isolation Violations (should be 0):"
grep "deprecated_api_call" ~/.corvin/audit.jsonl | jq 'select(.tenant_id != .event.tenant_id)' | wc -l

echo -e "\n=== GATES SUMMARY ==="
echo "Gate 1 (Learning): Check convergence >= 0.95, fallback < 1%"
echo "Gate 2 (Compat): Check mean < 5 calls/day"
echo "Gate 3 (Imports): Check = 0"
echo "Gate 4 (Plugins): Check <= 5% of total plugins"
echo "Gate 5 (Tenant): Check = 0"
```

---

## Rollback Plan (if gates fail)

**If Phase C initiated but gates fail at week 8:**

```bash
# Option 1: Revert deletion (keep compat layer + old code permanently)
git revert <deletion_commit>

# Option 2: Extend compat layer indefinitely
git commit -m "decision: extend compat layer indefinitely (Phase C gated failed)

Gate [name] did not pass. Keeping compat layer + old code in coexistence mode.
This is acceptable per ADR-0538: 'No forced deletion if measurement says not ready.'"
```

---

## Sign-Off (Week 8)

| Gate | Status | Evidence | Decision |
|---|---|---|---|
| Gate 1 | ✅ PASS / ❌ FAIL | [grep output] | [proceed/extend] |
| Gate 2 | ✅ PASS / ❌ FAIL | [grep output] | [proceed/extend] |
| Gate 3 | ✅ PASS / ❌ FAIL | [grep output] | [proceed/extend] |
| Gate 4 | ✅ PASS / ❌ FAIL | [grep output] | [proceed/extend] |
| Gate 5 | ✅ PASS / ❌ FAIL | [grep output] | [proceed/extend] |
| **DECISION** | **✅ DELETE / ❌ EXTEND** | **All gates** | **[execute/defer]** |

**Signed:** [Date, Operator]

---

## References

- **ADR-0538:** Deprecation Covenant (gates + rollback plan)
- **Phase A:** Audit + Deprecation (weeks 1–2, DONE)
- **Phase B:** Compat Layer (weeks 3–4, in progress)
- **Phase C:** This document (weeks 5–8, measurement-driven deletion)

