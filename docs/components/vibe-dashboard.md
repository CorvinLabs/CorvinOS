# Learning Dashboard (the `vibe-engineering` panel)

**Status:** Production  
**Location:** `src/pages/vibe-engineering/VibeDashboard.tsx`  
**ADR:** ADR-0561 Phase 4, ADR-0564 (audit-graph era, now retired), ADR-0321 (learning view)  
**Test Coverage:** 5 mocked E2E + 5 live E2E + 4 unit tests (all green, 2026-09-05)

## Overview

The panel is the **Learning Dashboard**: tenant maturity across the five
learning subsystems. It is the only entry in the sidebar's Vibe Engineering
group, and it has no tabs.

**URL:** `/app/vibe-engineering` — the route id, the `PANELS` id and the
manifest id all stay `vibe-engineering`; only the visible name is "Learning
Dashboard". Renaming the route would break bookmarks and the manifest/registry
match for nothing.

### Two rounds of removal (2026-09-05)

The panel's shape changed twice on the same day, on operator instruction. Both
are recorded here because both have been "restored" by a later edit before:

1. The Vibe group's four secondary panels were retired (see below).
2. The dashboard's own audit tabs — Graph View, Inspector, Timeline (ADR-0564) —
   were removed too, leaving the Learning view alone.

The audit-graph building blocks are still in the tree, **unmounted on purpose**:
`components/AuditChainGraph.tsx`, `components/GraphInspector.tsx` and
`hooks/useAuditQuery.ts`. A parallel line of work (`components/AuditGraphPanel.tsx`,
`components/ContextLayersPanel.tsx`) builds on them. They are not dead code to
delete without asking.

### Retired panels (2026-09-05)

Brain Monitor, Context Intelligence, Learning Hub and Session Explorer were
removed — they duplicated the dashboard in the sidebar. Removed in the same
commit, everywhere a panel is registered:

| Registration | File |
|---|---|
| Sidebar entries | `src/components/layout.tsx` (`NAV_GROUPS`) |
| Routes | `src/panels/registry.tsx` (`PANELS`) |
| Lazy components | `src/lazy-pages.ts` |
| Route wrappers + panel components | `src/pages/{brain-monitor,context-intelligence,learning-hub,session-explorer}.tsx`, `src/pages/vibe-engineering/components/{BrainMonitor,ContextIntelligence,LearningHub,SessionExplorer}.tsx` (deleted) |
| Backend capability manifest | `core/console/corvin_console/routes/capabilities.py` |

The backend manifest matters as much as the frontend: `mergeManifestNav()` is
**additive** to `NAV_GROUPS`, so leaving `brain-monitor` in the manifest puts
the sidebar link back on its own — which it did, live, until the console
service was restarted with the updated `capabilities.py`.
`core/console/tests/test_console_manifest_route.py` now asserts all four stay
out of the manifest.

## Architecture

```
VibeDashboard (thin wrapper, no page chrome of its own)
└── LearningDashboard          [ADR-0321 maturity view; renders its own <h1>]
    ├── HeroScoreTile          [aggregate 0–10 learning score + trend]
    ├── RadarChart             [5-system confidence profile]
    ├── TrajectoriesChart      [30-day confidence per system]
    ├── HealthHeatmap          [confidence × outcome × velocity]
    └── SystemDetailCards      [per-system detail]
```

`VibeDashboard` deliberately renders no header: `LearningDashboard` brings its
own `<h1>Learning Dashboard</h1>`, and a wrapper header would show the title
twice.

### Data Flow

1. `VibeDashboard` fetches data via `useVibeData()` hook (5s polling)
2. Data is passed downstream to components that need it:
   - `ContextIntelligence` receives `data` + `onQualityGateChange` callback
   - `LearningHub` receives `data`
   - `BrainMonitor`, `SessionExplorer` self-fetch via their own `useVibeData()` calls
3. Tab state is stored in URL query param (`?tab=<id>`)
4. Browser back/forward navigation restores tab state automatically

**Current code — Graph Engineering Edition (ADR-0564), documented 2026-09-04.**
The steps above describe the v1 `useVibeData()` dashboard; the shipped
`VibeDashboard.tsx` is the audit-first version:

- The page queries `GET /v1/console/vibe-engineering/audit?since=&limit=100`
  through `useAuditQuery()` (React Query) and renders the hash chain as a graph.
- The query filter (`since`, `limit`) is fixed **once per mount** with a
  `useState` initializer, in both `VibeDashboard` and the hook's default. The
  filter is part of the query key: computing `since` inline re-keyed the query
  on every render, which re-fetched forever (~33 requests/s against the live
  console, spinner never resolved, "Live • 0 events" — fixed 2026-09-04).
- Graph View draws with cytoscape's built-in `breadthfirst` layout, exported as
  `AUDIT_GRAPH_LAYOUT` from `components/AuditChainGraph.tsx`. The name is
  case-sensitive and cytoscape throws at construction on an unknown one
  (`breadthFirstSearch` shipped and left the Graph View empty until 2026-09-04).
- The Inspector renders what a chain record actually carries: the backend maps
  every real audit event to `{event_type, severity, details}` and only classifies
  `type`; the typed fields on the frontend event subtypes (`confidence`,
  `entropy_score`, `decision_name`, `latency_ms`, `input`, …) are optional and
  rendered only when present. Reading them unguarded crashed the page on the
  first node click ("Cannot read properties of undefined (reading 'toFixed')",
  fixed 2026-09-04; guard: `tests/unit/graph-inspector-backend-shape.test.tsx`).
- The console manifest (`/v1/console/capabilities/manifest`) is additive: a
  manifest failure falls back to the static panel registry, so the route still
  mounts — which is why the 500 it answered until 2026-09-04 (a builtin panel
  dict without `requiredFlag`) went unnoticed from the UI.

### Design Decisions

| Decision | Why |
|---|---|
| **Single polling loop** | Efficiency: one `/vibe-engineering/state` poll instead of N per tab |
| **URL query params for state** | Bookmarkable, browser-navigable, no Redux/Zustand needed |
| **Lazy loading (Suspense)** | Smaller initial JS bundle; tabs only load when clicked |
| **Selective data prop passing** | Components that need data get it; self-sufficient components stay decoupled |

## Props

None. VibeDashboard is a route-level component; it doesn't accept props.

## State Management

The panel holds no state. `LearningDashboard` keeps its metrics in a `useState`
initialised from `mockLearningMetrics` (see Known Limitations).

## Testing

### Unit Tests (`tests/unit/vibe-dashboard.test.tsx`)
- ✅ Renders the learning dashboard
- ✅ Is named "Learning Dashboard"
- ✅ Has NO tab bar (`[role="tablist"]` absent, zero `role="tab"`)
- ✅ Renders none of the retired views

**Run:** `npm run test -- tests/unit/vibe-dashboard.test.tsx`

Further guards:

- `tests/unit/vibe-dashboard-query-stability.test.tsx` — real `useAuditQuery` +
  real React Query over a mocked transport; asserts exactly ONE audit request
  per mount. It drove this through `VibeDashboard` until the panel stopped
  querying the audit chain; the defect lives in the hook's default filter, so
  the test now mounts the hook directly and keeps guarding the next consumer.
- `tests/unit/audit-chain-graph-layout.test.ts` — runs `AUDIT_GRAPH_LAYOUT`
  against headless cytoscape (jsdom has no canvas; the mocks hid the typo).
- `core/console/tests/test_console_manifest_route.py` — HTTP-level manifest
  test; every panel source must carry both gate keys.

### E2E Tests
`tests/e2e/vibe-engineering.spec.ts` — 5 mocked-transport scenarios: the
"Learning Dashboard" heading, the learning content (score, radar, heatmap), NO
tab bar and no retired view, mobile layout, no console errors.

`tests/e2e/vibe-engineering-panel.spec.ts` — 5 scenarios against the LIVE
console: the sidebar carries exactly one Vibe entry and it reads "Learning
Dashboard", no retired route is linked, the route renders the learning
dashboard, the audit tabs are gone, and every retired route 404s instead of
serving a stale panel.

Paths in both specs are `/console/app/<route>`: `baseURL` already ends in
`/console`, and an absolute `/app/...` discards that prefix and hits the
gateway's 404.

The live spec's `goto()` waits for "Loading session…" to detach before
asserting. Without it every assertion raced the auth gate on a freshly booted
console and the suite was flaky at 4 workers.

**Run:** `npx playwright test tests/e2e/vibe-engineering.spec.ts tests/e2e/vibe-engineering-panel.spec.ts`  
**Note:** the live spec needs a valid session in `tests/e2e/auth-state.json`.
Sessions do not survive a console restart — refresh it via
`GET /v1/console/auth/local-login` when every live spec suddenly fails on a
missing sidebar.

## Accessibility

- ✅ Radix UI Tabs (accessible by default)
- ✅ `aria-selected` attribute on active tab
- ✅ Keyboard navigation (Arrow keys, Tab)
- ✅ Focus management (Suspense fallback has loading spinner)
- ✅ Semantic HTML (no divs masquerading as buttons)

## Performance

- **Initial Load:** ~400ms (lazy components load on first tab click)
- **Polling:** 5s interval (configurable via `useVibeData(pollInterval)`)
- **Tab Switch:** <100ms (re-renders only active tab content)
- **Network:** Single `/vibe-engineering/state` request shared across tabs

## Known Limitations

1. **Learning tab renders mock data (open, 2026-09-05):** `LearningDashboard`
   holds `mockLearningMetrics` in `useState`; the `fetch('/v1/vibe/learning-metrics')`
   that would replace it is still commented out. The layout, palette and charts
   are real — the numbers are not.
2. **No error fallback UI:** If the audit query fails, the tab renders its error
   card but sub-panels render empty
   - **Workaround:** Add error boundary (Phase 5 enhancement)
4. **Audit source is the home-dir file, not the live root (open, 2026-09-04):**
   `routes/vibe_engineering.py::get_audit_chain` reads `Path.home()/.corvin/audit.jsonl`
   instead of resolving through `CORVIN_HOME` / the tenant chain paths, ignores
   `since`/`until` (only `limit` applies), and stamps the session's `tenant_id` on
   events from that global file. On a repo-local install the graph therefore shows
   the stale home-dir chain. Which chain(s) the graph should visualise is a product
   decision — not changed here.
5. **Cold-start flag window after a backend restart (open, 2026-09-04):** the first
   `os.capabilities` executions after boot hit the 5 s Skill timeout (audit chain
   shows `status: timeout`), so `/capabilities` answers every flag `False` and the
   Vibe sidebar group is hidden until the next refetch.

## Future Enhancements (Phase 5+)

- [ ] Error boundary with retry mechanism
- [ ] Configurable polling interval per tenant
- [ ] Tab persistence across sessions (localStorage)
- [ ] Telemetry for tab usage (ADR-0314 learning events)
- [ ] Responsive tab bar on mobile (hamburger menu for small screens)
- [ ] Real-time updates via WebSocket (instead of polling)

## Related

- **ADR:** [ADR-0561](../../../Corvin-ADR/decisions/ADR-0561-console-redesign-unified-concept.md) (Console Redesign), [ADR-0564](../../../Corvin-ADR/decisions/ADR-0564-console-vibedashboard-unified-tabs.md) (VibeDashboard Design)
- **Hooks:** `useVibeData()` in `src/pages/vibe-engineering/hooks/useVibeData.ts`
- **Sub-component:** `LearningDashboard` in `src/pages/vibe-engineering/components/`
  (`AuditChainGraph`, `GraphInspector` and `hooks/useAuditQuery` remain in the
  tree, unmounted, for the parallel audit-graph work)
- **Compliance:** GDPR Art. 30/32 (audit trail), EU AI Act Art. 50 (transparency)

---

**Last Updated:** 2026-09-05 (secondary panels retired; audit tabs removed; panel renamed Learning Dashboard)  
**Maintainer:** shumway (Claude)  
**Review Status:** Ready for Phase 5
