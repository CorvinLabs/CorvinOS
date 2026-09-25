# T05 ADR Deduplication Strategy (Wave 1, Blocker Task)

**Status:** ANALYSIS PHASE  
**Date:** 2026-09-25  
**Priority:** P0 (blocks all downstream tasks)

---

## Problem Statement

28 ADR IDs are duplicated across 49 files (28 conflicts × 2–3 files each):
- 6 ADRs have 3 files (0730, 0846, 0847, 0852, 0853, 0863)
- 22 ADRs have 2 files each

**Root Cause:** Two parallel ADR numeration schemes merged without conflict resolution:
1. **OTEL/Observability Track** (original): 0681 = OTEL Metrics, 0682 = Multi-Tenant Learning, etc.
2. **Skill Forge v2.0 Track** (new): 0681 = Phase 5 Skill Manager, 0682 = Phase 6 Marketplace, etc.

---

## Deduplication Matrix

### Category A: Clear Winner (Keep Skill Forge, Renumber Observability)

| Duplicate ID | Keep (Skill Forge) | Renumber (OTEL/Observability) | New ID | Rationale |
|---|---|---|---|---|
| 0681 | `phase5-console-skill-manager.md` | `otel-metrics-schema.md` | 0910 | Skill Forge is active/merged; OTEL is observability |
| 0682 | `phase6-marketplace-discovery.md` | `multi-tenant-learning.md` | 0911 | Phase 6 is shipping; OTEL track is blocked |
| 0683 | `phase7-learning-feedback.md` | `geo-enrichment-privacy-filtering.md` | 0912 | Phase 7 active; Geo is independent |
| 0684 | `phase8-observability-dashboard.md` | `observability-dashboard-corvin-labs-api.md` | 0913 | Skill Forge phase 8; OTEL is redundant |

### Category B: Temporal Duplicates (Keep Latest, Delete Draft)

| Duplicate ID | Keep | Delete | Rationale |
|---|---|---|---|
| 0730 | `operator-unification.md` | `DRAFT` + previous version | Keep final, one DRAFT copy only |
| 0820 | `dod-verifier-skill-2-0.md` | `0545-plugin-buildin-memory.md` (wrong ID, orphaned) | DOD Verifier is active |
| 0845 | `os-model-selector-tier1-3.md` | `0727-marketplace-api-v3.md` (wrong ID) | Model Selector active; 0727 renumber |
| 0846 | `os-model-selector-learning-loop.md` | `0728-phase2-feature1.md` + BTW Steering | Model Selector active |
| 0847 | `os-model-selector-skill-wiring.md` | `0729-marketplace-api-v2` + Voice Integration | Model Selector active |
| 0848 | `ldd-learning-loop.md` | `0770-phase2-k1.md` (wrong ID) | LDD is active; 0770 renumber |
| 0849 | `vibe-dashboard.md` | `0771-phase3-k1-decision-history.md` (wrong ID) | Vibe active; 0771 renumber |
| 0850 | `task-manager-subsystem.md` | `0772-phase3-k1-outcome-feedback.md` (wrong ID) | TaskMgr active; 0772 renumber |
| 0851 | `live-stats-observability.md` | `0773-phase3-k2-confidence.md` (wrong ID) | Live stats active; 0773 renumber |
| 0852 | `phase-b-master-plan.md` (ACCEPTED) | `phase-2-tier4.md` + Sprint 1 verification | Phase B is canon; others archived |
| 0853 | `skill-forge-v2-implementation.md` (ACCEPTED) | `tier3-advanced-loss-signals.md` (orphaned) | Skill Forge v2 active |
| 0854–0863 | *Similar pattern* | *See full audit* | *Apply same logic* |

### Category C: Orphans & Misfiles (Rename or Archive)

| File | Issue | Action |
|---|---|---|
| `ADR-0469-*.md` + `ADVERSARIAL-REVIEW_ADR-0469.md` | Non-standard naming | Rename ADVERSARIAL to `ADR-0469-adversarial-review.md`, delete IMPLEMENTATION-PLAN (belongs in separate dir) |
| `ADR-0545-plugin-buildin-memory.md` (filed under 0820) | Wrong ID in filename | Either rename to 0820 or assign new ID (e.g., 0914) + migrate if active |

---

## Deduplication Action Plan

### Phase 1: Audit & Mapping (Done — you're here)
✅ Identify all 28 duplicates  
✅ Categorize by conflict type  
✅ Build deduplication matrix

### Phase 2: Renumbering (Today, T05)

**Step 1:** Find next available ADR ID
```bash
cd /home/shumway/projects/Corvin-ADR/decisions
ls -1 | grep -oE 'ADR-[0-9]{4}' | sed 's/ADR-//' | sort -n | tail -1
# → Find max, then allocate 0910–0930 for OTEL/Observability renumbering
```

**Step 2:** Create Renumbering Map (CSV)
```
OLD_ID,OLD_FILE,NEW_ID,NEW_FILE,ACTION,RATIONALE
0681,otel-metrics-schema.md,0910,ADR-0910-otel-metrics-schema.md,RENAME,OTEL track → observability sequence
0682,multi-tenant-learning.md,0911,ADR-0911-multi-tenant-otel-learning.md,RENAME,OTEL track
...
```

**Step 3:** Execute Renames (with git tracking)
```bash
# For each RENAME in map:
git mv "Corvin-ADR/decisions/ADR-0681-otel-metrics-schema.md" \
       "Corvin-ADR/decisions/ADR-0910-otel-metrics-schema.md"

# For each DELETE:
git rm "Corvin-ADR/decisions/ADR-0730-DRAFT.md"

# Commit:
git commit -m "refactor(adr): T05 Deduplication Wave 1 — Renumber OTEL track 0681–0684→0910–0913, consolidate Phase B+VIBE ADRs"
```

**Step 4:** Update Cross-References
```bash
# Grep all references to old IDs in:
#  - CLAUDE.md
#  - ADR frontmatter (depends_on, related, supersedes)
#  - Core code comments (ADR-XXXX citations)
#  - Tests & docs

# Bulk-replace with sed (with git tracking):
cd /home/shumway/projects/CorvinOS
git grep -l 'ADR-0681-otel' | xargs sed -i 's/ADR-0681-otel/ADR-0910-otel/g'
git grep -l 'ADR-0682-multi' | xargs sed -i 's/ADR-0682-multi/ADR-0911-multi/g'
# ... (repeat for all 28 × file references)

git commit -m "refactor(codebase): Update ADR cross-references (T05 dedup) — 0681→0910, 0682→0911, ... 0684→0913"
```

### Phase 3: Pre-Commit Hook (Today, T05 part 2)

Update `.git/hooks/pre-commit` to reject new duplicate IDs:

```bash
#!/bin/bash
# Pre-commit hook: Reject duplicate ADR IDs

cd /home/shumway/projects/Corvin-ADR/decisions
duplicates=$(ls -1 | grep -oE 'ADR-[0-9]{4}' | sort | uniq -d | wc -l)

if [ "$duplicates" -gt 0 ]; then
  echo "❌ COMMIT REJECTED: $duplicates duplicate ADR IDs found"
  echo "Run: git grep -o 'ADR-[0-9]{4}' | sort | uniq -d"
  exit 1
fi
```

---

## Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Cross-reference breakage | High | Bulk-update grep + sed; verify all cross-refs post-rename |
| Commit history confusion | Medium | Git log will show renames (good for traceability) |
| Downstream code references miss a rename | Medium | CI gate: test that all ADR-XXXX patterns in code resolve to live files |
| Merge conflicts if branches reference old IDs | Medium | Keep rename commits separate, communicate timeline |

---

## Estimated Effort

- **Phase 2 Renaming:** 4–6 hours (mechanical, high-risk if cross-refs missed)
- **Phase 3 Pre-Commit:** 1 hour
- **Validation + Testing:** 2–3 hours
- **Total T05:** ~1 day (conservative)

---

## Next Action

1. Execute Phase 2, Step 1: Find max ADR-ID
2. Build the Renumbering Map (CSV)
3. Get sign-off on the map (no renames until approved)
4. Execute renames + cross-ref updates
5. Implement pre-commit hook
6. Validate: `duplicates=$(ls -1 | grep -oE 'ADR-[0-9]{4}' | sort | uniq -d | wc -l) && echo $duplicates`
   - Result must be 0

---

**Blocked Until:** Phase 2 complete (No downstream task can proceed without resolved ADRs)  
**Assigned Stream:** A (primary blocker)  
**Parallel Streams Waiting:** B, C, D can proceed independently (started after T05 identification, don't depend on resolution)

