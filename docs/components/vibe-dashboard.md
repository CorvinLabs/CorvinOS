# VibeDashboard Component (Phase 4 k=1-4)

**Status:** Production Ready (Phase 4 k=4 Refinement Complete)  
**Location:** `src/pages/vibe-engineering/VibeDashboard.tsx`  
**ADR:** ADR-0561 Phase 4, ADR-0564  
**Test Coverage:** 14 E2E tests, 5 unit tests (71% pass rate with mocks)

## Overview

VibeDashboard is the unified Vibe Engineering hub — a tab-based interface consolidating five separate panels (Dashboard, Brain Monitor, Context Intelligence, Learning Hub, Session Explorer) into a single route with URL-synced tab state.

**URL:** `/app/vibe-engineering?tab=<id>`

## Architecture

```
VibeDashboard
├── useVibeData(5000)          [Poll vibe-engineering/state every 5s]
├── Tabs (Radix UI)            [Tab navigation with aria-selected]
│   ├── Dashboard              [Overview card, placeholder for widgets]
│   ├── Brain Monitor          [Lazy-loaded, no data prop]
│   ├── Context Intelligence   [Lazy-loaded, data prop passed]
│   ├── Learning Hub           [Lazy-loaded, data prop passed]
│   └── Session Explorer       [Lazy-loaded, no data prop]
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
- ✅ Renders heading and description
- ✅ Renders all five tab buttons
- ✅ Renders dashboard content by default
- ✅ Component renders with Radix UI structure
- ⏳ Tab switching and URL state sync (blocked by Radix UI complexity in unit test environment)

**Run:** `npm run test -- tests/unit/vibe-dashboard.test.tsx`

Note (2026-09-04): this file predates the audit-graph rewrite and fails at HEAD
(no `QueryClientProvider`, five-tab assertions). Current guards:

- `tests/unit/vibe-dashboard-query-stability.test.tsx` — real hook + real React
  Query over a mocked transport; asserts exactly ONE audit request per mount and
  "Live • N events" from the payload.
- `tests/unit/audit-chain-graph-layout.test.ts` — runs `AUDIT_GRAPH_LAYOUT`
  against headless cytoscape (jsdom has no canvas; the mocks hid the typo).
- `core/console/tests/test_console_manifest_route.py` — HTTP-level manifest
  test; every panel source must carry both gate keys.

### E2E Tests (`tests/e2e/vibe-engineering.spec.ts`)
- 14 Playwright scenarios covering:
  - Tab navigation (click, aria-selected)
  - URL state persistence & direct URL navigation
  - Lazy loading fallbacks
  - Browser back/forward navigation
  - Responsive layout (mobile/tablet/desktop)
  - Console error detection

**Run:** `PLAYWRIGHT_MOCK_AUTH=1 CONSOLE_BASE_URL="http://localhost:5173/console" npm run test:e2e -- tests/e2e/vibe-engineering.spec.ts`  
**Note:** Requires live backend or comprehensive mocks

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

1. **Sub-component independence:** BrainMonitor and SessionExplorer self-fetch data, leading to potential double-polls if VibeDashboard also polled
   - **Workaround:** Components check `loading` state before fetching (TODO: verify in code)
2. **Lazy loading doesn't work offline:** Requires `/vibe-engineering/state` endpoint
   - **Workaround:** Mock the endpoint (see `tests/fixtures/mock-api.ts`)
3. **No error fallback UI:** If vibe data fetch fails, components render empty
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
- **Sub-components:** `BrainMonitor`, `ContextIntelligence`, `LearningHub`, `SessionExplorer` in `src/pages/vibe-engineering/components/`
- **Compliance:** GDPR Art. 30/32 (audit trail), EU AI Act Art. 50 (transparency)

---

**Last Updated:** 2026-09-03 (Phase 4 k=4)  
**Maintainer:** shumway (Claude)  
**Review Status:** Ready for Phase 5
