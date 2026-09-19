# Adversarial Review Stream B: Architectural Compliance Audit

**Report Generated:** 2026-09-19  
**Scope:** ADR-0264 & ADR-0516 Compliance + Entry Point Wiring  
**Status:** 🔴 **CRITICAL VIOLATIONS DETECTED**

---

## EXECUTIVE SUMMARY

| Check | Result | Severity | Issues |
|---|---|---|---|
| **ADR-0516 Compliance** | ❌ FAIL | CRITICAL | 5 ADRs outside canonical Corvin-ADR |
| **ADR Duplicate IDs** | ⚠️  WARNING | HIGH | Same ADR-IDs with different content |
| **ADR-0264 Frontmatter** | ✅ PASS | — | 527 ADRs in Corvin-ADR checked |
| **Entry Point Wiring** | ⏳ DEFERRED | — | Will be checked on Days 6-8 code |

**Total Critical Issues:** 5 + duplicate IDs  
**Blocking Merge:** YES (must remediate before commit)

---

## FINDING 1: ADR-0516 VIOLATIONS (CRITICAL)

### Problem

5 ADR files found in `/home/shumway/projects/CorvinOS/outputs/` — violates ADR-0516 (single source of truth):

| File | ID | Status | Topic |
|---|---|---|---|
| ADR-0886-FINAL-ALL-FIXES-INTEGRATED.md | ADR-0886 | ACCEPTED | Task Orchestrator + Model Routing |
| ADR-0886-task-orchestrator-model-routing.md | ADR-0886 | ? | Task Orchestrator model routing |
| ADR-0887-0888-0889-0890-FINAL-ALL-FIXES.md | ADR-0887 | ? | Worker monitor steering (multi-ADR) |
| ADR-0887-os-worker-monitor-steering.md | ADR-0887 | ? | Worker monitor steering |
| ADR-0888-0889-0890-complete.md | ADR-0888,0889,0890 | ? | Multi-ADR composite file |

### Root Cause Analysis

- **ADR-0886 Conflict:** Two different ADRs claim the same ID but different topics:
  - `outputs/ADR-0886-FINAL-ALL-FIXES-INTEGRATED.md` → **Task Orchestrator + Model Routing** (newer, comprehensive)
  - `Corvin-ADR/ADR-0886-tier3-advanced-loss-signals.md` → **Tier 3: Advanced Loss Signals** (older, narrower scope)
  
- **Root Cause:** Session-local drafts not migrated to Corvin-ADR before commit
- **Impact:** Knowledge graph cannot disambiguate; audit trails break on ID collisions

### Remediation

#### Option A: Consolidate (Recommended for Task Orchestrator)
```bash
# 1. Review outputs/ADR-0886-FINAL-ALL-FIXES-INTEGRATED.md (newer version)
# 2. Verify it's more complete than Corvin-ADR/ADR-0886-tier3-advanced-loss-signals.md
# 3. If yes: REPLACE the canonical version
cd /home/shumway/projects/Corvin-ADR/decisions
cp /home/shumway/projects/CorvinOS/outputs/ADR-0886-FINAL-ALL-FIXES-INTEGRATED.md \
   ADR-0886-task-orchestrator-model-routing.md
git add ADR-0886-task-orchestrator-model-routing.md
# 4. Delete old version if confirmed redundant
# 5. Commit: "fix(adr): migrate ADR-0886 from outputs [ADR-0516 compliance]"
```

#### Option B: Create New IDs (If content is truly different)
```bash
# For each ADR that's substantively different from the Corvin-ADR version:
# 1. Assign new ID (e.g., ADR-0900, ADR-0901, etc.)
# 2. Update id: field in frontmatter
# 3. Migrate to Corvin-ADR/decisions/
# 4. Update any depends_on references
```

#### Option C: Delete (If duplicates/drafts)
```bash
# For draft files that are superseded by Corvin-ADR versions:
rm -f /home/shumway/projects/CorvinOS/outputs/ADR-*.md
```

**Recommended Action:** Inspect each file (A/B comparison), decide per ADR, then remediate.

---

## FINDING 2: DUPLICATE ADR IDS WITH DIFFERENT CONTENT (HIGH)

### Example: ADR-0886

**Location 1 (CorvinOS/outputs/):**
```
ADR-0886-FINAL-ALL-FIXES-INTEGRATED.md
  id: ADR-0886
  title: Task Orchestrator + Model Routing (FINAL — ALL FIXES INTEGRATED)
  status: ACCEPTED
  depends_on: [ADR-0069, ADR-0222, ADR-0314, ADR-0532, ADR-0759, ADR-0232]
  related: [ADR-0887, ADR-0888, ADR-0889, ADR-0890]
  paths: [core/orchestration/task_orchestrator.py, ...]
  commits: []
```

**Location 2 (Corvin-ADR/decisions/):**
```
ADR-0886-tier3-advanced-loss-signals.md
  id: ADR-0886
  title: Tier 3: Advanced Loss Signals
  status: accepted
  depends_on: [ADR-0848]
  relates_to: [ADR-0853]
  paths: [core/learning/loss_signals/advanced_loss_signals.py, ...]
  commits: []
```

### Problem
- **Same ID** (ADR-0886), **different topics** → ambiguity
- Knowledge graph cannot determine: which ADR-0886 is "canonical"?
- Audit records referencing ADR-0886 are **unambiguous** (no way to know which one was meant)
- Downstream systems (learning, routing, validation) fail on ID collision

### Analysis

**Scenario A: outputs/ version is NEWER**
- Task Orchestrator (outputs) was recently drafted
- Tier 3 signals (Corvin-ADR) is older, narrower
- **Fix:** Replace Corvin-ADR version, OR rename outputs version to new ID

**Scenario B: outputs/ version is OLDER DRAFT**
- outputs/ is outdated session work
- Corvin-ADR version is current
- **Fix:** Delete outputs/ version

**Required Action:** Compare dates + commits to determine which is canonical.

---

## FINDING 3: ADR-0264 COMPLIANCE

### Status: ✅ PASS (527 ADRs in Corvin-ADR)

All ADRs in the canonical Corvin-ADR/decisions/ directory have proper frontmatter structure:
- ✅ `id` field present
- ✅ `status` field present (one of: PROPOSED, ACCEPTED, REJECTED, SUPERSEDED, DEPRECATED)
- ✅ `depends_on` field present (list or empty)
- ✅ `paths` and `docs` fields present

**Minor notes:**
- Some files have malformed YAML (e.g., colons in titles without quotes) but are still parseable
- These should be cleaned up in a future linting pass

---

## FINDING 4: ENTRY POINT WIRING (DEFERRED)

### Scope
All new entry points (functions, endpoints, routes, MCP tools, plugin hooks) in Days 6-8 code must have:
1. ✅ Real call site outside their definition file
2. ✅ Call site traceable to a system trigger (route table, CLI registration, UI render tree, etc.)
3. ✅ E2E test proving the call path works

### Status
Will be evaluated **during Days 6-8 implementation** using:

```bash
# Phase 1: Identify all new entry points
git diff --name-only HEAD~1 | xargs grep -E '^\s*(async )?def|class ' | grep -v __pycache__

# Phase 2: Find external callers
grep -r '<entry_point_name>' --include='*.py' --include='*.ts' --include='*.tsx' \
  | grep -v '<definition_file>'

# Phase 3: Verify E2E path exists
pytest tests/e2e/test_<subsystem>_e2e.py -v
```

### Gate
Entry point wiring audit **MUST PASS** before any code can be merged.  
Gate: **E2E Wiring Proof** (skill requirement, part of LDD mandatory cycle)

---

## REMEDIATION PLAN (Priority Order)

### Priority 1: Clear ADR-0516 Violations (BLOCKING)
**Effort:** 1-2 hours  
**Checkpoint:** `git status | grep outputs/ADR` shows 0 files

```bash
# Step 1: Inspect each ADR in outputs/
for f in /home/shumway/projects/CorvinOS/outputs/ADR-*.md; do
  echo "=== $(basename $f) ==="
  head -20 "$f"
  echo ""
done

# Step 2: For each, decide: MIGRATE or DELETE
#   - If newer than Corvin-ADR version → migrate
#   - If draft/duplicate → delete

# Step 3: Migrate or delete
cd /home/shumway/projects/Corvin-ADR/decisions
# (copy/update/delete as needed)

# Step 4: Delete from outputs
rm /home/shumway/projects/CorvinOS/outputs/ADR-*.md
```

**Verification:**
```bash
cd /home/shumway/projects/CorvinOS
git status outputs/ | grep ADR  # Should be empty

python3 /tmp/audit_stream_b_final.py | grep "ADR-0516"  # Should show ✅ PASS
```

### Priority 2: Handle Duplicate ADR IDs (HIGH)
**Effort:** 2-3 hours  
**Gate:** Manual review + decision per ADR

**For each duplicate:**
1. Compare content + dates
2. Decide: Keep Corvin-ADR, update outputs, or create new ID
3. Update all depends_on references
4. Commit with "adr: resolve duplicate ADR-XXXX" message

### Priority 3: Entry Point Wiring Audit (POST-IMPLEMENTATION)
**Effort:** 1 hour per 50 entry points  
**Gate:** E2E wiring proof + pass audit before merge

```bash
# Days 6-8 implementation
# Before merge: run wiring audit
pytest tests/e2e/test_wiring_all_entry_points.py -v
```

### Priority 4: CI/CD Prevention (OPTIONAL)
**Effort:** 30 minutes  
**Goal:** Prevent this from recurring

```bash
# Add pre-commit hook
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/bash
# Reject commits adding ADRs to outputs/ (ADR-0516 enforcement)
if git diff --cached --name-only | grep -q 'outputs/ADR-'; then
  echo "ERROR: ADRs detected in outputs/ (ADR-0516 violation)"
  echo "ADRs belong in Corvin-ADR/decisions/ only"
  exit 1
fi
EOF
chmod +x .git/hooks/pre-commit
```

---

## COMPLIANCE MATRIX (Post-Remediation Target)

| Check | Current | Target | Gate |
|---|---|---|---|
| ADR-0516 violations | 5 | 0 | Merge blocker |
| Duplicate ADR IDs | ~5 | 0 | Merge blocker |
| ADR-0264 compliance | 100% | 100% | ✅ Current |
| Entry point wiring | ⏳ Pending | 100% | E2E gate |
| Pre-commit hook | ❌ No | ✅ Yes | Prevention |

---

## IMPLEMENTATION CHECKLIST

- [ ] **Step 1:** Audit each ADR-*.md in outputs/ (1h)
  - [ ] ADR-0886 — Decision: Migrate/Delete/New ID?
  - [ ] ADR-0887 — Decision: Migrate/Delete/New ID?
  - [ ] ADR-0888 — Decision: Migrate/Delete/New ID?
  - [ ] ADR-0889 — Decision: Migrate/Delete/New ID?
  - [ ] ADR-0890 — Decision: Migrate/Delete/New ID?

- [ ] **Step 2:** Execute remediation (1h)
  - [ ] Migrate canonical versions to Corvin-ADR
  - [ ] Delete outputs/ADR-*.md files
  - [ ] Commit: "fix(adr): ADR-0516 compliance - migrate from outputs"

- [ ] **Step 3:** Verify compliance (15 min)
  - [ ] Run audit script: `python3 /tmp/audit_stream_b_final.py`
  - [ ] Check: 0 ADR-0516 violations ✅
  - [ ] Check: 0 duplicate IDs ✅

- [ ] **Step 4:** Add prevention (30 min)
  - [ ] Create .git/hooks/pre-commit
  - [ ] Test hook with intentional violation
  - [ ] Commit hook to repo

- [ ] **Step 5:** Days 6-8 entry point wiring audit (see Days 6-8 plan)
  - [ ] Identify all new entry points
  - [ ] Verify call sites
  - [ ] Run E2E wiring tests

---

## GO/NO-GO DECISION

**Current Status:** 🔴 **NO-GO** (ADR-0516 violations block merge)

**Can Proceed to Days 6-8 If:**
- ✅ All ADRs from outputs/ are migrated or deleted
- ✅ Zero duplicate ADR IDs with different content
- ✅ Audit script passes: 0 critical violations

**Next Run:** Re-run `audit_stream_b_final.py` after remediation to confirm ✅ GO

---

## REFERENCE

- **ADR-0516:** Knowledge Graph Foundation (Single Source of Truth)  
  Location: `/home/shumway/projects/Corvin-ADR/decisions/ADR-0516-knowledge-graph-foundation.md`

- **ADR-0264:** ADR Decision Graph (Frontmatter Schema)  
  Location: `/home/shumway/projects/Corvin-ADR/decisions/ADR-0264-adr-decision-graph.md`

- **Remediation Script:** `/tmp/audit_stream_b_final.py`

- **CorvinOS CLAUDE.md Rules:**  
  - ADR-0516 Compliance (root MD files, SSoT enforcement)
  - ADR-Gate discipline (before declaring done)
  - Concept-Gate discipline (self-learning methods)

---

**Generated by:** Adversarial Review Stream B Compliance Audit  
**Date:** 2026-09-19  
**Severity:** CRITICAL (merge blocker until resolved)
