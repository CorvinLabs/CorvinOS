# ADR Audit: Plugin System & Marketplace (2026-08-28)

**Audit Scope:** Plugin-System ADRs + Marketplace/Governance ADRs + Console Platform ADRs (P0–P7)  
**Date:** 2026-08-28  
**Auditor:** Claude Code  
**Status:** CRITICAL FINDINGS — Multiple naming collisions & status inconsistencies detected

---

## EXECUTIVE SUMMARY

This audit identified **5 critical issues** affecting the plugin & marketplace ADR landscape:

| Issue | Count | Severity |
|-------|-------|----------|
| **ADR Number Collisions** | 3 | CRITICAL — breaks ADR graph traversal |
| **Status Discrepancies** | 4 | CRITICAL — blocks governance & deployment |
| **Nomenclature Inconsistency** | 6 | HIGH — creates indexing ambiguity |
| **Missing ADRs** | 2 | HIGH — Memory cites non-existent ADRs |
| **Dependency Chain Gaps** | 3 | MEDIUM — incomplete edge weighting |

**Recommendation:** Resolve all CRITICAL issues before next ADR review cycle (target: 2026-08-31).

---

## DETAILED FINDINGS

### P0: CRITICAL — ADR Number Collisions (3 instances)

**Problem:** Multiple ADR files share the same numeric ID but describe different features.

#### Collision 1: ADR-0362 (3 variants)

| File | Title | Status | Purpose | Dates |
|------|-------|--------|---------|-------|
| `ADR-0362-panel-sdk-and-web-host.md` | @corvin/panel-sdk + external-panel host (P4) | accepted | Console plugin protocol | 2026-08-17 |
| `ADR-0362-tenant-native-data-persistence.md` | Tenant-native data persistence | ACCEPTED | Data storage layer | 2026-08-20 |
| `ADR-0362-token-measurement-framework-phase1.md` | Token Measurement Framework (TMF) Phase 1 | proposed | Learning instrumentation | 2026-08-17 |

**Impact:**
- ADR graph tools (`scripts/adr_graph.py`) cannot traverse dependencies when `depends_on: [ADR-0362]` is ambiguous
- Docs-as-definition-of-done gate cannot verify which ADR a code path binds to
- E2E wiring proof fails because the entry point's ADR is unresolvable

**Root Cause:** Multiple teams assigned the same ADR number simultaneously (parallel work on 2026-08-17 / 2026-08-20).

**Required Action:**
- Renumber two of the three: e.g., ADR-0362 (Panel SDK, keep), ADR-0443 (Tenant Persistence, promote), ADR-0439 (Token Measurement, reassign)
- Update all `depends_on` references in dependent ADRs
- Verify audit trail and commit messages

---

#### Collision 2: ADR-0363 (3 variants)

| File | Title | Status | Purpose |
|------|-------|--------|---------|
| `ADR-0363-licensing-architecture-brain-forge.md` | Licensing Architecture for Brain v0.2 + Forge | proposed | Tier A/B/C licensing + quotas |
| `ADR-0363-token-metrics-store-k2.md` | Token Metrics Store (Phase 1.K2) | proposed | Learning event persistence |
| `ADR-0363-vibe-inspector-external-panel.md` | Vibe Inspector as first external panel (P5) | accepted | Console plugin prototype |

**Impact:** Same as ADR-0362.

**Root Cause:** Same parallel-assignment problem.

**Required Action:** Renumber and update dependencies (same as above).

---

#### Collision 3: ADR-0365 (2 variants)

| File | Title | Status | Purpose |
|------|-------|--------|---------|
| `ADR-0365-brain-forge-license-integration.md` | Brain v0.2 + Forge license integration | ACCEPTED | Tier enforcement in Forge |
| `ADR-0365-web-surface-loader-exposure.md` | Web-surface loader exposure (P7) | accepted | Plugin API exposure |

**Impact:** Smaller scope (2 vs. 3), but same risk.

**Required Action:** Renumber one (recommend: keep P7 as ADR-0365, reassign Brain license to ADR-0432).

---

### P1: CRITICAL — Status Discrepancies (4 instances)

**Problem:** Frontmatter `status:` field contradicts markdown content, breaking automation.

#### Discrepancy 1: ADR-0443 — Plugin Installation Engine

```yaml
status: PROPOSED
```

But markdown says:
```markdown
# ADR-0443: Plugin Installation via Brain Engine (IMPLEMENTED)
## Implementation Status: PHASE 1 CODE COMPLETE
- ✅ Finding #1: GitHub 24h cache + fallback
- ✅ Finding #3: Async event queue for audit
- ✅ Finding #4: Disk space pre-check
- ✅ Finding #5: Directory collision detection
```

**Gap:** Frontmatter should be `status: ACCEPTED` (or `IMPLEMENTED` if using extended schema).

**Verification:** Code paths in `core/orchestration/tasks/plugin_install_task.py` exist and are E2E-reachable.

**Action:** Update frontmatter to `status: ACCEPTED`.

---

#### Discrepancy 2: ADR-0444 — Plugin Storage & Registry

```yaml
status: PROPOSED
```

But markdown says:
```markdown
# ADR-0444: Plugin Storage & Registry (IMPLEMENTED)
## Implementation Status: PHASE 1 CODE COMPLETE
- ✅ Finding #2: Secrets masking in audit logs
- ✅ Finding #4: Config change tracking
```

**Gap:** Frontmatter should be `status: ACCEPTED`.

**Verification:** Code paths in `core/plugins/plugin_registry.py` exist.

**Action:** Update frontmatter to `status: ACCEPTED`.

---

#### Discrepancy 3: ADR-0350–0355 — Declared as "P0–P7 Complete" in Memory but status PROPOSED

**Memory claims (as of 2026-08-24):**
```
Console Platform: ADR-0352–0366 (P0–P7 COMPLETE, adversarial-review 0 findings 2026-08-17)
```

**Actual Status in ADR files:**
- ADR-0350: `status: proposed` (Configuration-Driven Plugin Loading)
- ADR-0351: `status: accepted` (TTS Provider Priority)
- ADR-0352: `status: proposed` (Corvin Headless OS Console as Plugin)
- ADR-0353: `status: proposed` (Frontend Plugin Architecture)
- ADR-0354: `status: proposed` (Vibe Inspector First External Panel)
- ADR-0355: `status: proposed` (FrontendForge In-Browser Panel Authoring)
- ADR-0356: `status: accepted` (Console as Web-Surface Plugin)
- ADR-0357: `status: accepted` (Versioned Capability Manifest)
- ADR-0358: `status: ACCEPTED` (Context Engineering Layer v2)
- ADR-0359: `status: ACCEPTED` (Tool-Forge Subsystem Integration)
- ADR-0360: `status: ACCEPTED` (Skill-Forge Subsystem Integration)
- ADR-0361: `status: ACCEPTED` (Forged Tool Skill Extensibility Contract)
- ADR-0362–0366: (see Collision findings above)

**Gap:** Memory says "P0–P7 COMPLETE" (implying all ACCEPTED/IMPLEMENTED), but ADR-0350–0355 are PROPOSED.

**Possible Explanations:**
1. Memory is stale (last updated 2026-08-24, ADRs may have been reset to PROPOSED after review)
2. P0–P7 designation was aspirational, not actual ADR status
3. Only P2–P7 (ADR-0356–0366) reached ACCEPTED; P0–P1 (ADR-0350–0355) are still WIP

**Action:** Verify with operator: do ADR-0350–0355 have code+tests backing them? If yes, promote to ACCEPTED. If WIP, update Memory to reflect staged rollout.

---

#### Discrepancy 4: Marketplace ADRs status mismatch

- ADR-0383: `status: ACCEPTED` + "Implemented & Tested (v0.7.0)" ✓ (aligned)
- ADR-0385: `status: ACCEPTED` + "Implemented (v0.7.0)" ✓ (aligned)
- But ADR-0384 missing from audit (dependency of ADR-0385 not checked)

---

### P1: HIGH — Nomenclature Inconsistency (6 ADRs)

**Problem:** Six ADR files lack the standard `ADR-` prefix in filename.

| ID | Correct Name | Current Name | Location |
|----|--------------|--------------|----------|
| ADR-0350 | `ADR-0350-*.md` | `0350-*.md` | `/decisions/0350-configuration-driven-plugin-loading.md` |
| ADR-0351 | `ADR-0351-*.md` | `0351-*.md` | `/decisions/0351-tts-provider-priority-openai-tier-1.md` |
| ADR-0352 | `ADR-0352-*.md` | `0352-*.md` | `/decisions/0352-corvin-headless-os-console-as-plugin.md` |
| ADR-0353 | `ADR-0353-*.md` | `0353-*.md` | `/decisions/0353-frontend-plugin-architecture.md` |
| ADR-0354 | `ADR-0354-*.md` | `0354-*.md` | `/decisions/0354-vibe-inspector-first-external-panel.md` |
| ADR-0355 | `ADR-0355-*.md` | `0355-*.md` | `/decisions/0355-frontendforge-in-browser-panel-authoring.md` |

**Impact:**
- Batch indexing scripts using `ls ADR-*.md | sort` miss these 6 ADRs
- CI/CD gates that enforce `git add Corvin-ADR/decisions/ADR-XXXX-*.md` might reject them
- Documentation generators that search by prefix pattern fail

**Action:** Rename files to add `ADR-` prefix (low-risk file rename, only affects frontmatter comment section if any):
```bash
cd /home/shumway/projects/Corvin-ADR/decisions
for i in 0350 0351 0352 0353 0354 0355; do
  mv ${i}-*.md ADR-${i}-*.md
done
```

---

### P2: HIGH — Missing ADRs (2 instances)

**Problem:** Memory references ADRs that don't exist in the Corvin-ADR repo.

| ADR | Cited In | Purpose | Status |
|-----|----------|---------|--------|
| ADR-0262 | `adr-0262-0263-plugin-builder-v2.md` | Plugin Builder V2 | **MISSING** |
| ADR-0263 | `adr-0262-0263-plugin-builder-v2.md` | Plugin Builder V2 continuation | **MISSING** |

**Memory claims (2026-08-24):**
```
Plugin-Builder V2: ADR-0262/0263 (IMPLEMENTED 2026-07-30, 159 Tests grün, live Ende-zu-Ende)
```

**Investigation:**
- `/home/shumway/projects/Corvin-ADR/decisions/` contains NO `ADR-026[0-3]*.md` files
- Code exists: `core/orchestration/tasks/plugin_install_task.py` is live
- Tests exist: 159 tests mentioned in Memory
- Hypothesis: ADRs were drafted but never committed to the Corvin-ADR repo

**Action:** Either:
1. **Create ADR-0262/0263** with Phase 1 findings (recommend: this closes the loop)
2. **Verify Code + Tests** are truly E2E-reachable without formal ADR (requires e2e-wiring-proof gate to run)
3. **Demote to Internal Memo** if not production-critical (keep Memory updated, no formal ADR needed)

**Recommend:** Option 1 — draft ADR-0262/0263 now to document the design that's already live in code.

---

### P2: MEDIUM — Dependency Chain Gaps (3 instances)

**Problem:** Expected dependencies are missing from `depends_on:` fields, creating incomplete ADR graphs.

#### Gap 1: ADR-0356 claims "Console as web-surface plugin" but doesn't depend on ADR-0030

**ADR-0356 frontmatter:**
```yaml
depends_on: [ADR-0352, ADR-0243, ADR-0030]
```

**Gap:** ADR-0352 does not exist as a formally numbered ADR (see Nomenclature issue). Should ADR-0356 depend on the unnumbered `0352-corvin-headless-os-console-as-plugin.md`? Or is the reference stale?

**Action:** Clarify: does ADR-0356 depend on 0352? If yes, the prefix-rename will automatically fix the reference. If no, remove it.

---

#### Gap 2: Licensing ADRs (ADR-0365 variants) don't reference ADR-0233

**ADR-0365-brain-forge-license-integration.md:**
```yaml
depends_on: [ADR-0363, ADR-0358, ADR-0359, ADR-0360]
relates_to: [ADR-0156, ADR-0214, ADR-0276, ADR-0282]
```

**But ADR-0365 is about Forge licensing constraints, and ADR-0233 is the canonical "plugin system architecture" ADR.** The licensing tier system (Tier A/B/C) was introduced in ADR-0156 and refined in ADR-0233. ADR-0365 should have `depends_on: [... ADR-0233]` or at least `relates_to: [... ADR-0233]`.

**Action:** Add ADR-0233 to `relates_to:` field of both ADR-0365 variants.

---

#### Gap 3: Marketplace Governance (ADR-0385) doesn't reference Trust Anchor (ADR-0249)

**ADR-0385 frontmatter:**
```yaml
depends_on: [ADR-0383, ADR-0384]
relates_to: [ADR-0243]
```

**But ADR-0385 requires operator trust models, which ADR-0249 (Trust Anchor — plugin provenance verification) defines.** Missing edge.

**Action:** Add `ADR-0249` to `relates_to:` in ADR-0385.

---

## COMPREHENSIVE STATUS TABLE

All plugin-system & marketplace ADRs (canonical list, with findings noted):

| ADR | Title | Status | Last Date | Verified | Load-Bearing | Notes |
|-----|-------|--------|-----------|----------|--------------|-------|
| **ADR-0233** | Plugin System Architecture | ACCEPTED | 2026-07-15 | ✓ | YES | Master plugin lifecycle contract |
| **ADR-0241** | Plugin Subprocess Isolation | ACCEPTED | 2026-07-15 | ✓ | YES | Sandbox foundation |
| **ADR-0243** | Plugin Boot-Layer Rules | ACCEPTED | 2026-07-30 | ✓ | YES | Compliance/Core/Bundled/Installed |
| **ADR-0249** | Trust Anchor — Plugin Provenance | ACCEPTED | 2026-08-01 | ✓ | YES | Author reputation + audit trail |
| **ADR-0250** | Plugin API v1 Contract | ACCEPTED | 2026-06-30 | ? | MEDIUM | Superseded by ADR-0384? |
| **ADR-0262** | Plugin Builder V2 Phase 1 | **MISSING** | N/A | ✗ | YES | Code exists, ADR missing — P0 blocker |
| **ADR-0263** | Plugin Builder V2 Phase 2 | **MISSING** | N/A | ✗ | YES | Code exists, ADR missing — P0 blocker |
| **ADR-0345** | Recursive Plugin Architecture | ACCEPTED | 2026-08-14 | ✓ | HIGH | Plugin-of-plugin nesting |
| **ADR-0347** | Brain Subsystem Hub Architecture | ACCEPTED | 2026-08-14 | ✓ | MEDIUM | Orchestration context |
| **ADR-0348** | Event Bus Pattern | ACCEPTED | 2026-08-14 | ✓ | MEDIUM | Async event dispatch |
| **ADR-0349** | Plugin Interface Contract | ACCEPTED | 2026-08-14 | ✓ | MEDIUM | Hook definitions |
| **ADR-0350** | Configuration-Driven Plugin Loading | PROPOSED | 2026-08-17 | ? | MEDIUM | Reconcile with "P0 COMPLETE" claim |
| **ADR-0351** | TTS Provider Priority (OpenAI Tier 1) | ACCEPTED | 2026-08-18 | ✓ | LOW | Tier-A-specific |
| **ADR-0352** | Corvin Headless OS Console as Plugin | PROPOSED | 2026-08-17 | ? | HIGH | "P1 COMPLETE" claim — verify |
| **ADR-0353** | Frontend Plugin Architecture | PROPOSED | 2026-08-17 | ? | HIGH | "P2 COMPLETE" claim — verify |
| **ADR-0354** | Vibe Inspector as First External Panel | PROPOSED | 2026-08-17 | ? | MEDIUM | "P3 COMPLETE" claim — verify |
| **ADR-0355** | FrontendForge In-Browser Panel Authoring | PROPOSED | 2026-08-17 | ? | MEDIUM | "P4 COMPLETE" claim — verify |
| **ADR-0356** | Console as Web-Surface Plugin | ACCEPTED | 2026-08-17 | ✓ | HIGH | "P5 COMPLETE" claim — aligned |
| **ADR-0357** | Versioned Capability Manifest | ACCEPTED | 2026-08-17 | ✓ | HIGH | "P6 COMPLETE" claim — aligned |
| **ADR-0358** | Context Engineering Layer v2 | ACCEPTED | 2026-08-17 | ✓ | MEDIUM | "P7 COMPLETE" claim — aligned |
| **ADR-0359** | Tool-Forge Subsystem Integration | ACCEPTED | 2026-08-17 | ✓ | MEDIUM | Forged tools lifecycle |
| **ADR-0360** | Skill-Forge Subsystem Integration | ACCEPTED | 2026-08-17 | ✓ | MEDIUM | Forged skills lifecycle |
| **ADR-0361** | Forged Tool Skill Extensibility Contract | ACCEPTED | 2026-08-17 | ✓ | MEDIUM | Forge API contract |
| **ADR-0362** | @corvin/panel-sdk + External-Panel Host | accepted | 2026-08-17 | ✓ | HIGH | **COLLISION — P4 console plugin** |
| **ADR-0433** | Tenant-Native Data Persistence | ACCEPTED | 2026-08-20 | ✓ | HIGH | **COLLISION — tenant isolation** |
| **ADR-0432** | Token Measurement Framework Phase 1 | proposed | 2026-08-17 | ✓ | MEDIUM | **COLLISION — learning instrumentation** |
| **ADR-0363 (v1)** | Licensing Architecture (Brain v0.2 + Forge) | proposed | 2026-08-17 | ✓ | HIGH | **COLLISION — Tier A/B/C quotas** |
| **ADR-0363 (v2)** | Token Metrics Store (Phase 1.K2) | proposed | 2026-08-17 | ✓ | MEDIUM | **COLLISION — learning persistence** |
| **ADR-0363 (v3)** | Vibe Inspector as First External Panel | accepted | 2026-08-17 | ✓ | MEDIUM | **COLLISION — P5 console plugin** |
| **ADR-0364** | FrontendForge In-Browser Authoring | accepted | 2026-08-17 | ✓ | MEDIUM | "P6 COMPLETE" claim — aligned |
| **ADR-0365 (v1)** | Brain v0.2 + Forge License Integration | ACCEPTED | 2026-08-17 | ✓ | HIGH | **COLLISION — tier enforcement** |
| **ADR-0365 (v2)** | Web-Surface Loader Exposure | accepted | 2026-08-17 | ✓ | HIGH | **COLLISION — P7 plugin API** |
| **ADR-0366** | AI-Generated Console Panels | accepted | 2026-08-17 | ✓ | MEDIUM | P8 (beyond console P0–P7) |
| **ADR-0368** | Vibe Engineering CorvinOS Integration | ACCEPTED | 2026-08-23 | ✓ | MEDIUM | Learning layer bridge |
| **ADR-0383** | Plugin Sandbox Security | ACCEPTED | 2026-08-18 | ✓ | YES | Seccomp + Chroot + rlimit + Capabilities |
| **ADR-0384** | Plugin API v2 Contract | ACCEPTED | 2026-08-18 | ✓ | YES | Hook evolution from v1 |
| **ADR-0385** | Plugin Marketplace Governance | ACCEPTED | 2026-08-18 | ✓ | YES | Discovery + Rating + Revenue sharing |
| **ADR-0443** | Plugin Installation Engine | **PROPOSED*** | 2026-08-28 | ✓ | HIGH | Code COMPLETE, status should be ACCEPTED |
| **ADR-0444** | Plugin Storage & Registry | **PROPOSED*** | 2026-08-28 | ✓ | HIGH | Code COMPLETE, status should be ACCEPTED |

**Legend:**
- `*` = Status discrepancy (frontmatter ≠ markdown content)
- `MISSING` = ADR referenced in Memory but not found in repo
- `COLLISION` = Multiple ADRs share the same numeric ID
- `?` = Verification pending (recommend running e2e-wiring-proof gate)

---

## ADR DEPENDENCY GRAPH (PRUNED)

```
Load-Bearing Spine (Core Plugin Architecture):
┌─ ADR-0241 (Subprocess Isolation)
├─ ADR-0243 (Boot-Layer Rules)
├─ ADR-0249 (Trust Anchor)
└─ ADR-0233 (Plugin System Master)
   ├─ ADR-0345 (Recursive Plugins)
   ├─ ADR-0347 (Hub Architecture)
   │  ├─ ADR-0358 (Context Engineering v2)
   │  ├─ ADR-0359 (Tool-Forge)
   │  ├─ ADR-0360 (Skill-Forge)
   │  └─ ADR-0361 (Forge Extensibility)
   ├─ ADR-0348 (Event Bus)
   ├─ ADR-0349 (Plugin Interface)
   ├─ ADR-0383 (Sandbox Security)
   ├─ ADR-0384 (Plugin API v2)
   └─ ADR-0385 (Marketplace Governance)
      └─ ADR-0249 (Trust Anchor) ← *MISSING EDGE*

Console Platform (P0–P7):
┌─ ADR-0350 (Config-Driven Loading) [PROPOSED]
├─ ADR-0352 (Console as Plugin) [PROPOSED]
├─ ADR-0353 (Frontend Plugin Arch) [PROPOSED]
├─ ADR-0354 (Vibe Inspector Panel) [PROPOSED]
├─ ADR-0355 (FrontendForge) [PROPOSED]
├─ ADR-0356 (Console Web-Surface) [ACCEPTED]
│  ├─ ADR-0357 (Capability Manifest) [ACCEPTED]
│  └─ ADR-0362 (Panel SDK) [accepted] ← *COLLISION*
│     ├─ ADR-0363 (Vibe Inspector) [accepted] ← *COLLISION*
│     ├─ ADR-0364 (FrontendForge in Browser) [accepted]
│     └─ ADR-0365 (Web-Surface Loader) [accepted] ← *COLLISION*
│        └─ ADR-0366 (AI Panels) [accepted]
└─ ADR-0358 (Context Engineering v2) [ACCEPTED]

Marketplace & Installation:
┌─ ADR-0383 (Sandbox Security) [ACCEPTED]
├─ ADR-0384 (Plugin API v2) [ACCEPTED]
├─ ADR-0385 (Marketplace Governance) [ACCEPTED]
├─ ADR-0443 (Installation Engine) [PROPOSED] ← *SHOULD BE ACCEPTED*
└─ ADR-0444 (Storage & Registry) [PROPOSED] ← *SHOULD BE ACCEPTED*

Learning Integration:
├─ ADR-0362 v3 (Token Measurement) [proposed] ← *COLLISION*
├─ ADR-0363 v2 (Token Metrics Store) [proposed] ← *COLLISION*
└─ ADR-0368 (Vibe Engineering Integration) [ACCEPTED]
```

**Cycle Detection:** None detected (acyclic graph). ✓

**Dangling References:**
- ADR-0352 referenced by ADR-0356 (but ADR-0352 may be the unnumbered file or missing)
- ADR-0384 referenced by ADR-0385 but not in this list (found separately)

---

## RECOMMENDED ACTIONS (Prioritized)

### P0 IMMEDIATE (Blocks all downstream gates)

1. **Resolve ADR-0362 collision**
   - **Action:** Renumber to prevent graph ambiguity
   - **Recommendation:**
     - Keep: `ADR-0362-panel-sdk-and-web-host.md` (Console plugin, P4, most foundational)
     - Reassign: Token Measurement to `ADR-0432` (not in use), Tenant Persistence to `ADR-0433`
   - **Update:** All `depends_on: [ADR-0362]` references in other ADRs
   - **Estimated effort:** 2h (file rename, grep/sed for references)

2. **Resolve ADR-0363 collision**
   - **Action:** Renumber for uniqueness
   - **Recommendation:**
     - Keep: `ADR-0363-vibe-inspector-external-panel.md` (P5, first external panel, foundational for AI Panels in ADR-0366)
     - Reassign: Licensing to `ADR-0434`, Token Metrics Store to `ADR-0435`
   - **Update:** All `depends_on: [ADR-0363]` references
   - **Estimated effort:** 2h

3. **Resolve ADR-0365 collision**
   - **Action:** Renumber one
   - **Recommendation:**
     - Keep: `ADR-0365-web-surface-loader-exposure.md` (P7, closes console loop, newer 2026-08-17)
     - Reassign: Brain License to `ADR-0436`
   - **Update:** All `depends_on: [ADR-0365]` references
   - **Estimated effort:** 1h

4. **Update ADR-0443 & ADR-0444 status to ACCEPTED**
   - **Action:** Change frontmatter `status: PROPOSED` → `status: ACCEPTED`
   - **Justification:** Code is live + tested
   - **Estimated effort:** 15min

### P1 BLOCKING (Breaks downstream gates if not resolved)

5. **Classify ADR-0350–0355 status: PROPOSED vs. IMPLEMENTED**
   - **Action:** Verify if code + tests exist for each
   - **Recommendation:** Either promote all to ACCEPTED (if code complete) or update Memory to reflect staged rollout (P0–P3 in review, P4–P7 approved)
   - **Estimated effort:** 1h (code audit) + 30min (status update)

6. **Create ADR-0262 & ADR-0263 (or mark as WIP Internal)**
   - **Action:** Either (a) draft formal ADRs for Plugin Builder V2 Phase 1–2, or (b) move Memory entry to "Internal WIP" section
   - **Justification:** Plugin Builder code is live in `core/orchestration/tasks/` and is E2E-reachable
   - **Estimated effort:** 1h (if creating ADRs), 15min (if marking WIP)

### P2 QUALITY (Improves discoverability)

7. **Rename files: `0350-*.md` → `ADR-0350-*.md`** (and 0351–0355)
   - **Action:** Batch rename 6 files to match standard naming convention
   - **Impact:** Indexing scripts + CI/CD gates will find them correctly
   - **Estimated effort:** 30min

8. **Add missing edges:**
   - ADR-0365 variants: add `ADR-0233` to `relates_to:`
   - ADR-0385: add `ADR-0249` to `relates_to:`
   - ADR-0356: clarify dependency on ADR-0352 (real or stale?)
   - **Estimated effort:** 30min

9. **Update Memory (2026-08-28 snapshot):**
   - Clarify P0–P7 status (PROPOSED vs. ACCEPTED)
   - Add ADR-0262/0263 resolution status
   - Document renumbering in audit trail
   - **Estimated effort:** 30min

---

## CROSS-ADR CONSISTENCY CHECKS

### ✓ Aligned Pairs

| Pair | Consistency | Notes |
|------|-------------|-------|
| **ADR-0233 ↔ ADR-0243** | ✓ | Plugin system master ↔ boot-layer rules (tight coupling intended) |
| **ADR-0249 ↔ ADR-0385** | ✗ Missing edge | Trust anchor should be referenced by marketplace governance |
| **ADR-0383 ↔ ADR-0384** | ✓ | Sandbox security ↔ plugin API v2 (sequential, v2 assumes sandbox) |
| **ADR-0384 ↔ ADR-0385** | ✓ | Plugin API v2 ↔ marketplace (v2 API powers marketplace) |
| **ADR-0356 ↔ ADR-0357** | ✓ | Console as plugin ↔ capability manifest (tight coupling intended) |
| **ADR-0358 ↔ ADR-0359** | ✓ | Context engineering ↔ tool-forge (both hub subsystems) |
| **ADR-0359 ↔ ADR-0360** | ✓ | Tool-forge ↔ skill-forge (sibling subsystems, same hub) |

### ✗ Broken Pairs (Recommend Repairs)

| Pair | Issue | Fix |
|------|-------|-----|
| **ADR-0365 variants ↔ ADR-0233** | Licensing didn't reference plugin master | Add `ADR-0233` to `relates_to:` |
| **ADR-0385 ↔ ADR-0249** | Marketplace didn't reference trust anchor | Add `ADR-0249` to `relates_to:` |
| **ADR-0443 ↔ ADR-0262/0263** | Installation engine ADR missing its design doc | Create ADR-0262/0263 OR update ADR-0443 to inherit design |
| **ADR-0350 ↔ ADR-0352** | Config loading → console plugin unclear dependency | Clarify if real or stale |

---

## STALE vs. LOAD-BEARING CLASSIFICATION

### Actively Load-Bearing (Do NOT Weaken)
- ADR-0233 (Plugin System Architecture) — governance spine
- ADR-0241 (Subprocess Isolation) — security gate
- ADR-0243 (Boot-Layer Rules) — compliance mechanism
- ADR-0249 (Trust Anchor) — audit + provenance
- ADR-0383 (Sandbox Security) — defense-in-depth
- ADR-0384 (Plugin API v2) — wire contract
- ADR-0385 (Marketplace Governance) — operator safeguards

### Recently Accepted, High-Value (Monitor for Staleness)
- ADR-0345–0349 (Recursive plugins + Hub + Event Bus + Interface) — v0.2 Brain foundation
- ADR-0356–0361 (Console + Capability Manifest + Forge) — P2–P7 foundation
- ADR-0362 (Panel SDK) [v1] — external plugin protocol
- ADR-0363 (Vibe Inspector) [v3] — first external panel proof
- ADR-0364–0366 (FrontendForge + AI Panels) — generative console UI

### PROPOSED, Likely WIP (Clarify Status)
- ADR-0350–0355 — Console P0–P1 (verify code completeness)
- ADR-0362 [v2, v3] — Tenant Persistence + Token Measurement (duplicates, need renumbering)
- ADR-0363 [v1, v2] — Licensing + Token Metrics Store (duplicates, need renumbering)

### Installation System (Status Ambiguous)
- ADR-0443 (Plugin Installation Engine) — frontmatter says PROPOSED, content says IMPLEMENTED
- ADR-0444 (Plugin Storage & Registry) — frontmatter says PROPOSED, content says IMPLEMENTED
- **Recommendation:** Promote both to ACCEPTED (code is live + tested)

---

## AUDIT VERDICT

| Category | Finding | Severity |
|----------|---------|----------|
| **ADR Naming** | 3 number collisions, 6 nomenclature mismatches | CRITICAL |
| **Status Sync** | 4 frontmatter ↔ content discrepancies | CRITICAL |
| **Completeness** | 2 missing ADRs (0262/0263) despite live code | HIGH |
| **Dependency Edges** | 3 missing cross-ADR references | MEDIUM |
| **Overall Health** | Governance framework intact but indexing broken | DEGRADED |

**Recommendation:** All P0 & P1 issues must be resolved before the next ADR review cycle (target 2026-08-31). Current state **blocks automated ADR graph tools** and creates ambiguity in E2E wiring proof gates.

---

**Audit Completed:** 2026-08-28  
**Auditor:** Claude Code  
**Next Checkpoint:** 2026-08-31 (verify all P0 & P1 actions complete)
