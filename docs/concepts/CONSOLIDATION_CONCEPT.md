# Vibe Engineering Console Panel Consolidation Strategy

**Status:** SUPERSEDED (2026-08-27) — see ADR-0431. The shipped shape is NOT the
hybrid plan below: the operator chose full replacement. The Vibe Engineering nav
group is now exactly ONE entry — the Learning Dashboard. It was five for a
while (Dashboard · Brain Monitor · Context Intelligence · Learning Hub · Session
Explorer); the four secondary panels were retired on 2026-09-05 as duplicates,
and the dashboard's own audit tabs went the same day. See
`docs/components/vibe-dashboard.md` and
`core/vibe_engineering/CONSOLE_REDESIGN_UNIFIED_CONCEPT.md`, and the eleven panels
this document analyses were REMOVED — routes, `PANELS` entries, `NAV_GROUPS` entries
and page files. Keep the analysis below for its per-panel audit; ignore its
recommendations.

**Original status:** PROPOSED (LDD k=1-3 audit + dialectical design)  
**Date:** 2026-08-27  
**Related ADRs:** ADR-0353 (Panel Registry), ADR-0370 (Vibe Overview), ADR-0365 (Token Metrics)  
**Concept Goal:** Design a strategic consolidation of 12 vibe-engineering panels to reduce cognitive load while preserving user value and discoverability.

---

## Executive Summary

The Vibe Engineering system currently operates 12 console panels (Overview, Your Talent, Context Pipeline, Token Metrics, TreeOfThoughts, Learning Objectives, Cross-Device Learning, Task Graph, Brain Status, Context Intelligence, Learning Hub, Debug Panel). This analysis identifies:

- **3 stub panels** (Brain Status, Context Intelligence, Learning Hub, Debug Panel — 27 LOC each, reusing common infrastructure)
- **2 informational mirrors** (Overview and TreeOfThoughts duplicating Context Pipeline data)
- **4 functional silos** that could be grouped by user workflow (metrics, learning, tasks, debugging)
- **Clear consolidation path** from 12 panels → 7–8 grouped panels without losing user value

**Proposed consolidation:** Keep the foundation (Context Pipeline, Learning Objectives), merge stubs into a unified **Brain Engineering Dashboard**, fold Overview into Learning Hub, and group Task/Cross-Device panels with Context Intelligence into **Advanced Analysis**.

---

## K=1: Panel Audit Table

| Panel | Route | Lines | Type | Status | Purpose | Data Source | Redundancy Risk |
|-------|-------|-------|------|--------|---------|-------------|-----------------|
| **1. Overview** | `/app/vibe-overview` | 103 | Standalone | Live | CEL flow explainer + aggregate counters | useTraces | HIGH — duplicates Context Pipeline data, adds only explainer |
| **2. Your Talent** | `/app/talent` | 641 | Foundational | Live | User skill profile, learnings, recommendations | API `/talent` | NONE — unique content |
| **3. Context Pipeline** | `/app/vibe-engineering` | 681 | Foundational | Live | Per-turn context stages, star-graph, glass-box prompt | useTraces (vibe adapter) | PRIMARY — the canonical stage visualization |
| **4. Token Metrics** | `/app/token-metrics` | 309 | Dashboard | Live | Session token usage, cost savings, subsystem attribution | getTokenMetrics API | NONE — unique real-time data |
| **5. TreeOfThoughts** | `/app/learning` | 44 | Thin wrapper | Live | Learned patterns, confidence scores | LearningDashboard component | HIGH — mirrors Context Pipeline (same /traces data) + skill grading |
| **6. Learning Objectives** | `/app/learning-objectives` | 372 | Functional | Live | User-set learning goals, progress tracking, compliance rate | API `/ulo/objectives` | NONE — unique content |
| **7. Cross-Device Learning** | `/app/multi-instance` | 393 | Dashboard | Live | GitHub sync, multi-device pattern merge, freshness | API `/sync/status` | MEDIUM — can merge into a larger Learning Dashboard |
| **8. Task Graph** | `/app/task-graph` | 93 | Visualization | Live | Task DAG, checkpoints, decisions | API `/task/list` + TaskGraphVisualizer | MEDIUM — specialized but addressable via a Tasks panel |
| **9. Brain Status** | `/app/brain-status` | 27 | Stub | NEW | Active task, worker health, decision logs | useVibeData hook | HIGH — stub, can merge into consolidated Brain Dashboard |
| **10. Context Intelligence** | `/app/context-intelligence` | 34 | Stub | NEW | Context quality analysis, gate policy | useVibeData hook | HIGH — stub, can merge into consolidated Dashboard |
| **11. Learning Hub** | `/app/learning-hub` | 27 | Stub | NEW | Learning content, resources, training paths | useVibeData hook | HIGH — stub, can rename to consolidated Learning Dashboard |
| **12. Debug Panel** | `/app/debug-panel` | 27 | Stub | NEW | Developer debugging, telemetry inspection | useVibeData hook | HIGH — stub, can merge into Console Settings or standalone Debug |

---

## K=2: Redundancy & Dependency Analysis

### High-Redundancy Pairs (Candidates for Consolidation)

**Pair 1: Overview + TreeOfThoughts**
- Both read `/traces` (useTraces hook)
- Overview: 103 LOC (CEL explainer + counters)
- TreeOfThoughts: 44 LOC (thin wrapper around LearningDashboard)
- **Finding:** Overview is a mental-model introduction; TreeOfThoughts is a skill-grade viewer. They serve different cognitive steps in the same user journey (understand the flow → see what was learned).
- **Action:** Merge Overview into Learning Hub as an **"Introduction" tab**, keep TreeOfThoughts as **"Patterns" tab** in Learning Hub.

**Pair 2: Brain Status + Context Intelligence + Learning Hub (Stub Cluster)**
- All 3 are stubs using `useVibeData` (178 LOC shared hook)
- All 3 are NEW (not yet in production, minimal user feedback)
- All 3 live under `vibe-engineering/components/` (BrainStatus 126 LOC, ContextIntelligence 151 LOC, LearningHub 134 LOC = 411 LOC total)
- **Finding:** These are intentionally modular sub-components designed to be composed, not standalone pages. They should live as *tabs* in a unified **Brain Engineering Dashboard**, not as separate routes.
- **Action:** Create one unified `/app/brain-engineering` route that hosts Brain Status, Context Intelligence, and Learning Hub as tabs.

**Pair 3: Task Graph + Cross-Device Learning**
- Task Graph: 93 LOC (task DAG visualization)
- Cross-Device Learning: 393 LOC (sync status, pattern merge, GitHub integration)
- **Finding:** Task Graph is specialized (single-task DAG). Cross-Device Learning is session-wide (multi-device merge). Both are about *understanding data flow*, but at different granularities.
- **Action:** Keep Task Graph standalone (specialist tool). Move Cross-Device Learning into a larger **Learning Dashboard** alongside TreeOfThoughts.

### Shared Infrastructure Dependencies

All vibe-engineering panels share:
- **Common hooks:** useTraces, useVibeData (178 LOC hook shared across stubs)
- **UI library:** Card, Badge, Button, Dialog (from @/components/ui)
- **Vibe adapter:** @/adapters/vibe (traces, brief, assembly, forged, pipeline)

**Implication:** Any consolidation will **reduce component mount overhead** (fewer React lazy-load boundaries, fewer route re-renders) but **not reduce shared logic** (the hooks and adapter remain the same).

---

## K=3: Dialectical Design (Thesis / Antithesis / Synthesis)

### Thesis: Keep All 12 (Status Quo)

**Pros:**
- Minimal disruption, no migration cost
- Each panel has its own clear URL, easy to bookmark/link
- User can focus on one task at a time
- Low coupling between pages

**Cons:**
- 12 routes in the sidebar → cognitive overload (a 12-item group is hard to scan)
- 4 stub panels (Brain, Context Intelligence, Learning Hub, Debug) are undifferentiated in the nav — unclear which to click first
- Overview + TreeOfThoughts duplicate data from Context Pipeline; unclear which to use
- Stubs were designed as *tabs*, not *pages*; keeping them separate violates their design intent
- Total 2115 LOC in vibe-engineering subsystem spread across 12 routes = maintenance fragmentation
- Learning Objectives + Cross-Device Learning + TreeOfThoughts + Learning Hub all touch "learning" but are scattered across the menu

**Loss signal:** User confusion on first use: "I have 12 panels, which one do I click? Are Brain Status and Context Intelligence redundant? Why is Overview separate from Context Pipeline?"

### Antithesis: Aggressive Merge to 3–4 Mega-Panels

**Hypothesis:** Consolidate all 12 into:
1. **Observability** (Context Pipeline + Overview + Brain Status + Context Intelligence + Debug)
2. **Learning** (TreeOfThoughts + Learning Objectives + Learning Hub + Cross-Device Learning)
3. **Metrics** (Token Metrics)
4. **Tasks** (Task Graph + Your Talent)

**Pros:**
- 4 routes instead of 12 → simpler nav, less cognitive load
- Clear mental-model grouping by subsystem
- Stubs merged into tabs → design intent honored
- Fewer lazy-load boundaries → faster first paint

**Cons:**
- Each mega-panel becomes 800–1200 LOC (complex to maintain)
- Tab switching adds latency (no code-splitting per tab)
- Operator can't deep-link to "Token Metrics" specifically, only to "Metrics dashboard, find the token tab"
- Tab nesting can hide features (buried tabs under tabs → discoverability fails)
- Over-consolidation: Context Pipeline is important enough to warrant its own route, shouldn't be buried in Observability
- Learning Objectives (goal-setting) is very different from TreeOfThoughts (pattern viewing); merging mixes workflows

**Loss signal:** User workflow fragmentation: "I want to check token savings quickly, but I have to load 5 other components first. And I can't bookmark the token-savings tab."

### Synthesis: Strategic Consolidation (7–8 Grouped Panels)

**Design Principle:** Merge only where *intent* aligns (stubs designed as tabs) AND *data model* aligns (same source, same user question). Keep foundational panels separate. Use **collapsible nav groups** to reduce visual clutter without losing discoverability.

#### Proposed Final Structure (After Dialectical Refinement)

| Section | Route | Components | Status | Notes |
|---------|-------|-----------|--------|-------|
| **Vibe Engineering (Collapsible Nav Group)** | — | — | REFINED | 8–9 items instead of 12; Brain Status/Context Intelligence/Learning Hub grouped (not merged) in nav |
| 1. Overview | `/app/vibe-engineering/intro` or merged into Learning Hub | CEL explainer, turn counts, degraded flags | **MERGED** (into Learning Hub intro) | Folded as opening tab of Learning Dashboard; data model aligns (same /traces source) |
| 2. Context Pipeline | `/app/vibe-engineering` | Per-turn stage pills, star-graph, glass-box prompt, audit integrity | **STANDALONE** | Foundational, too important for a tab, needs deep interactivity |
| 3. Your Talent | `/app/talent` | Skill profile, learned patterns, recommendations | **STANDALONE** | Orthogonal to Vibe, user-centered not data-centered |
| 4. Brain Status | `/app/brain-status` | Active task, worker health, decision logs | **GROUPED IN NAV** (not merged) | Stays as separate route; grouped under "Brain Engineering" nav section; no code consolidation |
| 5. Context Intelligence | `/app/context-intelligence` | Context quality analysis, gate policy | **GROUPED IN NAV** (not merged) | Stays as separate route; grouped under "Brain Engineering" nav section; no code consolidation |
| 6. Learning Hub | `/app/learning-hub` | Learning content, resources, training paths | **GROUPED IN NAV** (not merged) | Stays as separate route; grouped under "Brain Engineering" nav section; no code consolidation |
| 7. Debug Panel | `/app/debug-panel` | Telemetry inspection, log tail, CEL trace replay | **GROUPED IN NAV** (not merged) | Stays as separate route; grouped under "Brain Engineering" nav section; no code consolidation |
| 8. Token Metrics | `/app/token-metrics` | Real-time session metrics, cost attribution, subsystem breakdown | **STANDALONE** | Unique operational data, different user role (operators vs. explorers) |
| 9. Learning Dashboard | `/app/learning` | **Tabs:** Intro (Overview merged), Patterns (TreeOfThoughts), Objectives, Cross-Device Sync | **REDESIGNED** | Reuse current `/app/learning` route; fold Overview, TreeOfThoughts, Objectives, and Sync status into a learning-centric dashboard |
| 10. Task Graph | `/app/task-graph` | DAG visualization, checkpoint selection, phase/iteration drill-down | **STANDALONE** | Specialized single-purpose tool, needs focus |

**Refined Rationale (After Dialectical Challenge):**

**Why NOT merge Brain Status / Context Intelligence / Learning Hub as tabs:**
- **Antithesis revealed:** These stubs do *not* share a data model (task-centric, context-centric, content-centric are orthogonal concerns).
- **User workflows are separate:** An operator debugging a failed context gate doesn't need Brain Status; a learner exploring training paths doesn't need Context Intelligence.
- **Conflicting refresh rates:** Brain Status (5s), Context Intelligence (10s), Learning Hub (60s) — merging forces a compromise.
- **Tab pattern violation:** Tabs work when users switch between views of the *same data*; these are fundamentally different data sources.
- **Cost of reversal:** If consolidation is wrong, splitting back out requires code restructuring. Keeping separate is reversible.
- **Performance:** Separate routes allow lazy-loading and per-component optimization. Merged tabs load all four upfront, wasting resources for single-use operators.

**Why merge Overview into Learning Hub and TreeOfThoughts into Learning:**
- **Data model alignment:** Both read `/traces` (useTraces hook), same upstream source.
- **User workflow coherence:** Overview (understand the flow) → TreeOfThoughts (see what was learned) → Learning Objectives (set goals) → Cross-Device Sync (merge patterns) = one learning journey.
- **Strong use case:** Operator learning about Vibe Engineering naturally progresses through these four views in sequence, not as scattered separate pages.
- **Reduced indirection:** Instead of "I want to understand Vibe, click here, then here, then here," it's "Click Learning Dashboard, flip through the four tabs."

**Result: 9–10 routes (down from 12)**
- Nav shows: **Vibe Engineering (collapsible)** with 8–10 items (not 12)
- Brain Engineering section is grouped but not consolidated (preserves independent routes, reduces nav sprawl)
- Learning Dashboard is genuinely merged (same data model, coherent workflow)
- All other panels standalone as today

**Result:** 8 routes (down from 12), grouped into a single **"Vibe Engineering"** collapsible nav section, each route serving a clear user workflow.

---

## Migration Roadmap (Refined: No Code Consolidation, Nav Grouping Only)

### Phase 1: Nav Grouping & Learning Dashboard Merge (Week 1–2)

**Goal:** Reduce nav clutter by grouping Brain Engineering panels; merge Overview/TreeOfThoughts into Learning Dashboard.

1. Update nav structure (`layout.tsx`):
   - Create a new collapsible nav group: **"Brain Engineering"** (label, icon: Brain)
   - Move these 4 items under that group:
     - Brain Status
     - Context Intelligence
     - Learning Hub
     - Debug Panel
   - These routes stay as **separate routes** (no code consolidation yet)
   - Keep all other panels as-is: Context Pipeline, Token Metrics, Task Graph, Your Talent

2. Implement Learning Dashboard:
   - Reimplement `/app/learning.tsx` (current TreeOfThoughts wrapper):
     - **Tab 1: Intro** (fold Overview: CEL explainer, aggregate counters, flow diagram)
     - **Tab 2: Patterns** (fold TreeOfThoughts: learned patterns, confidence scores, skill grades)
     - **Tab 3: Objectives** (fold Learning Objectives: goals, progress, compliance rate)
     - **Tab 4: Sync** (fold Cross-Device Learning: GitHub repo, multi-device freshness, pattern merge status)
   - Keep all four tabs' data sources and logic; just reorganize the page structure.

3. Update panel registry (`registry.tsx`):
   - Learning Dashboard remains at `/app/learning` (no route change)
   - All Brain Engineering panels keep current routes (no change)
   - Remove or mark deprecated: `vibe-overview` route (folded into Learning Dashboard)

4. Backward compatibility:
   - Redirect `/app/vibe-overview` → `/app/learning#intro` (hash-based tab selection)
   - Keep `/app/learning` working (operator bookmarks unaffected)

**Tests:**
- Nav grouping test: "Brain Engineering" section shows 4 items when expanded
- Learning Dashboard: all 4 tabs load, data is consistent across tabs
- E2E: click through Brain Engineering → Context Intelligence → back to Brain Status; no errors
- Redirect: old `/app/vibe-overview` URL still works, lands on Learning Dashboard intro tab

### Phase 2: Document Phase 2 (Optional) — Future Tab Consolidation

**Goal:** Document conditions for merging Brain Status / Context Intelligence as tabs (if warranted by user feedback).

Write a **Phase 2 ADR** (do not implement yet) titled:
- "ADR-0XXX: Brain Status + Context Intelligence as Co-tabs (conditional on user feedback)"

Scope:
- Only merge if users confirm they compare these two side-by-side in the same session (100x+ per week).
- Only if both come to share a common data model (e.g., both feed from a "context-health" API).
- Learning Hub remains separate (orthogonal content-exploration workflow).
- Debug Panel remains separate (developer tool, not observability).

Trigger conditions:
- 3+ months of usage data showing high cross-tab usage
- User feedback requesting direct comparison view
- Shared data model implementation (ADR-0XXX: context-health-unified-api)

Keep this as a future option, not a committed design.

### Phase 3: Cleanup (Week 3+)

**Goal:** Remove deprecated routes and finalize docs.

1. Delete old page files (if not merged into Learning Dashboard):
   - `/pages/vibe-overview.tsx` (absorbed into `/pages/learning.tsx`)
   - Optionally: `/pages/learning-objectives.tsx` (if not kept as its own route for external links; recommend keeping for backward compat)

2. Update docs:
   - Architecture ref: "Vibe Engineering Panels" section shows 9–10 routes, grouped under "Brain Engineering" nav
   - User guide: "Getting Started with Vibe Engineering" walk through Context Pipeline → Token Metrics → Learning Dashboard (4 tabs) → Task Graph
   - ADR: update ADR-0353 with final panel layout and nav grouping

3. Remove any feature flags related to consolidation (not needed for nav-only changes)

**Tests:**
- No broken links in docs
- Nav wiring test passes with final structure (Brain Engineering group + standalone panels)
- E2E: all routes reachable via nav, all data loads correctly

---

## Success Metrics

### Operational Metrics

| Metric | Baseline (Current) | Target (After Consolidation) | Measurement |
|--------|------------------|---------------------------|-------------|
| **Nav group items (Vibe section)** | 12 flat items | 10 items (8 in Brain Engineering group, 2 ungrouped) | Length of sidebar after grouping |
| **Cognitive load on first use** | High (12 items, unclear which to click) | Medium (8 grouped, clear hierarchy) | Operator usability test: "Find Brain Status" (target: <2 clicks) |
| **Lazy-load boundaries** | 12 | 10 (no change, separate routes remain separate) | Number of React.lazy imports in registry.tsx |
| **Learning Dashboard coherence** | 3–4 separate routes (Overview, Tree, Objectives, Sync) | 1 route with 4 tabs | Single entry point for learning workflows |
| **Redirect success rate** | N/A | >99% | `/app/vibe-overview` → Learning Dashboard intro tab, no 404s |

### Code Quality Metrics

| Metric | Baseline | Target | Why |
|--------|----------|--------|-----|
| **Total LOC in vibe-engineering** | ~2115 | ~2115 | No code consolidation; nav grouping is config-only, Learning Dashboard merge adds ~50 LOC (tab plumbing), removes ~100 LOC (defunct page files) = net neutral |
| **Cyclomatic complexity per page** | <15 (baseline) | <15 (Learning Dashboard <20) | Only Learning Dashboard becomes more complex (4 tabs vs. 4 separate files); Brain Status/Context Intelligence remain separate, stay simple |
| **Test coverage in vibe-engineering** | TBD | ≥90% | Learning Dashboard tab-switching and data consistency need tests; Brain Engineering grouping is nav-only (no new logic) |
| **Performance: lazy-load time** | Baseline: Brain Status ~200ms, Context Intelligence ~180ms (separate) | Target: Brain Status ~200ms, Context Intelligence ~180ms (unchanged; still separate routes) | No regression expected; separate routes still load independently |
| **Learning Dashboard tab-switch latency** | N/A | <100ms | Tab switches should not require data fetch (all tabs load on mount); measure React render time |

### User Experience Metrics (Optional, Post-Launch)

If operator feedback is collected:
- **Confusion on first load:** "I found token savings in X clicks" (target: 2–3 clicks, was 3–4 before)
- **Bookmark frequency:** Operators bookmarking specific routes (stable or increasing)
- **Feature discoverability:** "I didn't know Brain Engineering had a Debug tab" (should drop to <5% after announcement)

---

## Known Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|-----------|
| **Tab-switching latency** | Medium | If Brain Engineering or Learning Dashboard feels sluggish, split back into separate routes. Measure with `performance.mark()` before / after Phase 2. |
| **Deep-linking breaks** | Low | Implement hash-based tab routing (`#brain-status`, `#patterns`, etc.); test all old bookmarks in Phase 2. |
| **Operator workflow fragmentation** | Medium | During Phase 1, gather feedback: "Is consolidating TreeOfThoughts into Learning Dashboard natural for you?" Adjust taxonomy if needed. |
| **Over-consolidation later** | Low | Document clear separation: Brain Engineering = observability (Worker, Task, Context), Learning Dashboard = learning dynamics (patterns, goals, sync). Don't merge these two later without an explicit ADR. |
| **Flag maintenance debt** | Low | Remove flag immediately after Phase 2 (don't let it linger). Phase 3 cleanup is non-optional. |

---

## Operator Notes

*Append-only section for user feedback & changes.*

### 2026-08-27 (LDD k=1-3: Dialectical Refinement)
- **Thesis (Initial):** Consolidate 12 panels → 8 by merging stubs (Brain Status, Context Intelligence, Learning Hub, Debug Panel) as tabs into one Brain Engineering Dashboard.
- **Antithesis:** Stubs have orthogonal data models, conflicting refresh rates, and different user workflows. Forcing them into tabs violates the tab pattern (which assumes shared model + high interaction). Consolidation introduces coupling and performance overhead.
- **Synthesis (Refined):** Keep stubs as separate routes; group them in nav under "Brain Engineering" section (reduces visual clutter without code coupling). Merge only Overview/TreeOfThoughts into Learning Dashboard (strong coherence: same data source, natural workflow progression).
- **Result:** 9–10 routes (down from 12) with nav grouping only; no code consolidation of stubs yet (Phase 2 ADR documents conditional merge if user data warrants).
- **Roadmap revised:** Phase 1 (nav grouping + Learning Dashboard merge), Phase 2 (optional: future stub consolidation ADR), Phase 3 (cleanup).
- **Migration path:** All 12 old routes have a home; `/app/vibe-overview` redirects to Learning Dashboard intro tab; other Brain Engineering routes stay as-is (separate, grouped in nav).
- **Load-bearing assumption challenge:** Question: "Do operators naturally flip between Brain Status and Context Intelligence in the same session?" If yes, Phase 2 ADR becomes active. If no, separate routes are correct.

---

## Related Concepts & ADRs

- **ADR-0353** (Panel Registry): The foundational panel architecture; this concept proposes a navigation / structuring layer on top.
- **ADR-0370** (Vibe Overview G2): Removed Vibe Inspector, moved data here; this concept folds Overview into Learning Hub intro.
- **ADR-0365** (Token Metrics Dashboard): Real-time telemetry; remains standalone per this strategy.
- **CONCEPT-0011** (Status Reporting): Orthogonal; not affected by panel consolidation.

---

## Next Steps

1. **Review & Feedback** (operators, designer): Does the Learning Dashboard taxonomy feel natural? Should Brain Engineering include Debug Panel, or keep it separate?
2. **Detailed Implementation Plan**: Write Phase 1 flag logic, phase 2 component composition, phase 3 cleanup checklist.
3. **E2E Test Suite**: Build tests for tab routing, redirect verification, data consistency across old/new paths.
4. **Operator Communication**: Write a blog post / announcement for Week 1 Phase 1: "We're consolidating Vibe panels for clarity. Here's what's changing and why."
5. **Measure & Iterate**: After Phase 2 launch, collect metrics for one week; escalate any discovery/confusion issues before Phase 3 cleanup.

