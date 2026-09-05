# VibeDashboard Component (Phase 4 k=1-4)

**Status:** Production Ready (Phase 4 k=4 Refinement Complete)  
**Location:** `src/pages/vibe-engineering/VibeDashboard.tsx`  
**ADR:** ADR-0561 Phase 4, ADR-0564  
**Test Coverage:** 10 mocked E2E + 5 live E2E + 5 unit tests (all green, 2026-09-05)

## Overview

VibeDashboard is the unified Vibe Engineering hub — a tab-based interface with
URL-synced tab state, and since 2026-09-05 the **only** panel in the sidebar's
Vibe Engineering group.

**URL:** `/app/vibe-engineering?tab=<id>` — tabs: `graph`, `inspector`,
`timeline`, `learning`.

### Retired panels (2026-09-05)

Brain Monitor, Context Intelligence, Learning Hub and Session Explorer were
removed. They duplicated the dashboard in the sidebar, and their content is
reachable as dashboard tabs. Removed in the same commit, everywhere a panel is
registered:

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
VibeDashboard
├── useVibeData(5000)          [Poll vibe-engineering/state every 5s]
├── Tabs (Radix UI)            [Tab navigation with aria-selected]
│   ├── Graph View             [AuditChainGraph — cytoscape hash-chain graph]
│   ├── Inspector              [GraphInspector — selected node detail]
│   ├── Timeline               [Linear audit fallback when the graph is dense]
│   └── Learning               [LearningDashboard — ADR-0321 maturity view]
└── URL State Sync             [searchParams ↔ tab via React Router]
```

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

- **Active Tab:** Stored in URL query param (`?tab=...`), source of truth
- **Vibe Data:** Fetched via `useVibeData(5000)` hook (internal state)
- **Tab Content:** Lazy-loaded React components (React.lazy + Suspense)

## Testing

### Unit Tests (`tests/unit/vibe-dashboard.test.tsx`)
Rewritten 2026-09-05 for the audit-graph dashboard (it previously asserted the
retired five-tab shape and failed at HEAD for want of a `QueryClientProvider`):
- ✅ Renders heading
- ✅ Renders all four tabs (Graph View · Inspector · Timeline · Learning)
- ✅ Tab list is a 4-column grid
- ✅ Selecting Learning syncs `?tab=learning` and renders the learning dashboard
- ✅ `?tab=` on mount opens that tab

Radix activates a trigger on `mousedown`, not on a synthetic `click` — a
`fireEvent.click` here silently does nothing.

**Run:** `npm run test -- tests/unit/vibe-dashboard.test.tsx`

Further guards:

- `tests/unit/vibe-dashboard-query-stability.test.tsx` — real hook + real React
  Query over a mocked transport; asserts exactly ONE audit request per mount and
  "Live • N events" from the payload.
- `tests/unit/audit-chain-graph-layout.test.ts` — runs `AUDIT_GRAPH_LAYOUT`
  against headless cytoscape (jsdom has no canvas; the mocks hid the typo).
- `core/console/tests/test_console_manifest_route.py` — HTTP-level manifest
  test; every panel source must carry both gate keys.

### E2E Tests
`tests/e2e/vibe-engineering.spec.ts` — 10 mocked-transport scenarios: the four
tabs are present, no retired tab is rendered, each tab activates and renders,
the Learning tab shows the learning dashboard, `?tab=` round-trips, mobile
layout, no console errors, and tab switching **replaces** the history entry
(`setSearchParams({ replace: true })` — pushing per click buried the page the
operator came from).

`tests/e2e/vibe-engineering-panel.spec.ts` — 5 scenarios against the LIVE
console: the sidebar carries exactly one Vibe entry, no retired route is
linked, the dashboard renders its four tabs, the Learning tab renders, and
every retired route 404s instead of serving a stale panel.

Paths in both specs are `/console/app/<route>`: `baseURL` already ends in
`/console`, and an absolute `/app/...` discards that prefix and hits the
gateway's 404.

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
- **Sub-components:** `AuditChainGraph`, `GraphInspector`, `LearningDashboard` in `src/pages/vibe-engineering/components/`
- **Compliance:** GDPR Art. 30/32 (audit trail), EU AI Act Art. 50 (transparency)

---

**Last Updated:** 2026-09-05 (four secondary panels retired; four-tab dashboard)  
**Maintainer:** shumway (Claude)  
**Review Status:** Ready for Phase 5
