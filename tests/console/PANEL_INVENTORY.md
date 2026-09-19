# Console Panel Inventory (E2E Testing)

**Generated:** 2026-09-19  
**Total Panels:** 47 (34 main + 3 sub-groups with 6 sub-panels)  
**Coverage Goal:** 100% by Sep 21 (E2E tests + critical path priority)

---

## Priority Classification

| Priority | Category | Count | Definition |
|----------|----------|-------|-----------|
| **P0** | Critical infrastructure | 3 | Must work; blocks other features |
| **P1** | Core user-facing | 5 | Primary workflows; frequent use |
| **P2** | High-value features | 6 | Common tasks; second-tier importance |
| **P3–P5** | Secondary features | 19 | Useful but less critical; can skip in first pass |
| **P6–P7** | Edge cases / future | 14 | Rarely used, experimental, or planned |

---

## Panel Registry (47 Total)

### PRIMARY GROUP (2 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `vibe-engineering` | `pages/vibe-engineering/index.tsx` | **P0** | CORE | `/app/vibe-engineering` | Learnings | Tabs + Chart |
| `dashboard` | `pages/dashboard.tsx` | **P0** | CORE | `/app/dashboard` | Dashboard | Components + Tables |

### MARKETPLACE GROUP (1 panel + 1 manifest-driven)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `marketplace` | `pages/marketplace/index.tsx` | **P1** | CORE | `/app/marketplace` | Marketplace | Plugin/Package discovery |

### OBSERVABILITY GROUP (3 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `quality` | `pages/quality.tsx` | **P2** | ACTIVE | `/app/quality` | Quality Gates | List + Status |
| `sync-monitor` | `pages/sync-monitor.tsx` | **P3** | ACTIVE | `/app/sync-monitor` | Sync Monitor | Real-time events |
| `otel-telemetry` | `pages/otel-telemetry.tsx` | **P4** | ACTIVE | `/app/otel-telemetry` | OTEL Telemetry | Metrics graph |

### MESSAGING GROUP (2 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `bridges` | `pages/bridges.tsx` | **P2** | ACTIVE | `/app/bridges` | Channels | Config + list |
| `voice` | `pages/voice.tsx` | **P2** | ACTIVE | `/app/voice` | Profile | Form + settings |

### INTELLIGENCE / ASSISTANT GROUP (2 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `models` | `pages/models/index.tsx` | **P0** | CORE | `/app/models` | Models | Tabs (routing/usage/learning) |
| `memory` | `pages/memory.tsx` | **P3** | ACTIVE | `/app/memory` | Memory | Text editor + save |

### BUILD GROUP (6 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `compute` | `pages/compute.tsx` | **P3** | ACTIVE | `/app/compute` | Compute | Cluster info + metrics |
| `forge` | `pages/forge.tsx` | **P1** | CORE | `/app/forge` | Forge | Tool builder + code editor |
| `skills` | `pages/skills.tsx` | **P1** | CORE | `/app/skills` | Skills | Library + search |
| `skill-forge-generator` | `pages/skill-forge-generator.tsx` | **P3** | ACTIVE | `/app/skill-forge-generator` | Skill Forge | Wizard + generator |
| `ldd` | `pages/ldd.tsx` | **P3** | ACTIVE | `/app/ldd` | Quality (LDD) | Metrics + gates |
| `video-producer` | `pages/video-producer.tsx` | **P1** | FEATURE | `/app/video-producer` | Video Producer | Video playback + settings |

### NETWORK GROUP (3 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `agent-hub` | `pages/agent-hub.tsx` | **P2** | ACTIVE | `/app/agent-hub` | Agent Hub | List + search + details |
| `connectors` | `pages/connectors.tsx` | **P3** | ACTIVE | `/app/connectors` | Connectors | Config list + add |
| `custom-provider` | `pages/custom-provider.tsx` | **P4** | ACTIVE | `/app/custom-provider` | Custom Provider | Form + validation |

### KNOWLEDGE / DATA GROUP (5 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `rag` | `pages/rag.tsx` | **P3** | ACTIVE | `/app/rag` | RAG | Index + search |
| `rag-hub` | `pages/rag-hub.tsx` | **P3** | ACTIVE | `/app/rag-hub` | RAG Hub | Directory + browsing |
| `data-sources` | `pages/data-sources.tsx` | **P3** | ACTIVE | `/app/data-sources` | Data Sources | Config + connections |
| `flows` | `pages/flows.tsx` | **P4** | ACTIVE | `/app/flows` | Flows | DAG visualization |
| `datahub-unified` | `pages/datahub-unified.tsx` | **P4** | ACTIVE | `/app/datahub-unified` | DataHub | Unified view |

### SYSTEM GROUP (6 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `settings` | `pages/settings.tsx` | **P1** | CORE | `/app/settings` | Settings | Form fields + save |
| `compliance` | `pages/compliance.tsx` | **P2** | ACTIVE | `/app/compliance` | Audit & Compliance | Logs + filters |
| `api-keys` | `pages/api-keys.tsx` | **P2** | ACTIVE | `/app/api-keys` | API Keys | List + generate + copy |
| `license` | `pages/license.tsx` | **P3** | ACTIVE | `/app/license` | License | Status + validation |
| `licensing-audit` | `pages/licensing-audit.tsx` | **P4** | ACTIVE | `/app/licensing-audit` | Licensing Audit | Report view + export |
| `settings/github` | `pages/github.tsx` | **P4** | ACTIVE | `/app/settings/github` | GitHub | Repo config |

### HIDDEN / AUXILIARY PANELS (6 panels)

| Route | Panel File | Priority | Status | Route Path | Nav Label | Test Type |
|-------|-----------|----------|--------|-----------|-----------|-----------|
| `files` | `pages/files.tsx` | **P3** | ACTIVE | `/app/files` | Files | Upload + list + delete |
| `orgs` | `pages/orgs.tsx` | **P5** | FUTURE | `/app/orgs` | Orgs | (hidden, multi-tenant) |
| `people` | `pages/people.tsx` | **P5** | FUTURE | `/app/people` | People | (hidden, future) |
| `login` | `pages/login.tsx` | **P0** | AUTH | `/login` | Login | Auth flow |
| `not-found` | `pages/not-found.tsx` | **P6** | ERROR | `/404` | Not Found | Error page |
| `landing` | `pages/landing.tsx` | **P6** | ONBOARD | `/` | Landing | Onboarding |

### MODELS SUB-PANELS (2 auxiliary files)

| Route | Panel File | Priority | Status | Purpose | Test Type |
|-------|-----------|----------|--------|---------|-----------|
| `models/header` | `pages/models/header.tsx` | **P1** | SUPPORT | Tab selector for main panel | Header component |
| `models/tabs` | `pages/models/tabs.ts` | **P1** | SUPPORT | Tab config export | Config/constants |

### MARKETPLACE SUB-PANELS (2 auxiliary files)

| Route | Panel File | Priority | Status | Purpose | Test Type |
|-------|-----------|----------|--------|---------|-----------|
| `marketplace/header` | `pages/marketplace/header.tsx` | **P1** | SUPPORT | Tab selector | Header component |
| `marketplace/tabs` | `pages/marketplace/tabs.ts` | **P1** | SUPPORT | Tab config export | Config/constants |

### VIBE ENGINEERING SUB-PANELS (2 auxiliary files)

| Route | Panel File | Priority | Status | Purpose | Test Type |
|-------|-----------|----------|--------|---------|-----------|
| `vibe-engineering/VibeDashboard` | `pages/vibe-engineering/VibeDashboard.tsx` | **P0** | CORE | Main dashboard component (3-column) | Chart + Table |
| `vibe-engineering/index` | `pages/vibe-engineering/index.tsx` | **P0** | CORE | Panel entry point | Composition |

### VIDEO PRODUCER QUALITY METRICS (1 sub-panel)

| Route | Panel File | Priority | Status | Purpose | Test Type |
|-------|-----------|----------|--------|---------|-----------|
| `video-quality-metrics` | *lazy import* | **P1** | FEATURE | Video quality dashboard (ADR-0695 P2) | Metrics graph |

---

## Test Priority Schedule (Critical Path)

### Phase 1 (Sep 19–20, 8–12h): P0–P1 Panels (9 tests)

**BLOCKING:** Must pass before moving to Phase 2

1. ✅ `login.tsx` — Auth flow (foundational)
2. ✅ `dashboard.tsx` — Main dashboard (P0)
3. ✅ `vibe-engineering/` — Learnings dashboard (P0, 3 sub-components)
4. ✅ `models/index.tsx` — Models panel (P0, 3 tabs)
5. ✅ `settings.tsx` — Settings panel (P1, forms)
6. ✅ `forge.tsx` — Forge builder (P1, code editor)
7. ✅ `skills.tsx` — Skills library (P1, search/filter)
8. ✅ `marketplace/index.tsx` — Marketplace (P1, plugin discovery)
9. ✅ `video-producer.tsx` — Video player (P1, media controls)

**Expected Effort:** 1.5h per panel × 9 = 13.5h (can parallelize 3–4 concurrently)

### Phase 2 (Sep 20–21, 4–6h): P2 Panels (6 tests)

2. ✅ `bridges.tsx` — Channels config
3. ✅ `voice.tsx` — Voice profile
4. ✅ `quality.tsx` — Quality gates
5. ✅ `api-keys.tsx` — API key management
6. ✅ `compliance.tsx` — Audit logs
7. ✅ `agent-hub.tsx` — Agent discovery

**Expected Effort:** 0.8h per panel × 6 = 4.8h

### Phase 3 (Sep 21+, 2–3h): Spot checks (P3–P4, sample 5)

1. `compute.tsx` — Cluster metrics
2. `rag.tsx` — RAG index
3. `connectors.tsx` — Connector config
4. `files.tsx` — File upload
5. `github.tsx` — GitHub integration

**Expected Effort:** 0.5h per panel × 5 = 2.5h

---

## Fixtures & Helpers (conftest.py)

### Parametrized Panel Fixtures

```python
@pytest.fixture(params=[...], ids=[...])
def panel(request):
    """All 47 panels parametrized."""
    return request.param

@pytest.fixture
def critical_panels():
    """P0–P1 panels only (9 total)."""
    return [...]

@pytest.fixture
def secondary_panels():
    """P2–P3 panels only (12 total)."""
    return [...]
```

### Navigation Fixture

```python
async def panel_navigate(page, panel_route):
    """Navigate to panel by route; wait for ready."""
    await page.goto(f"/app/{panel_route}")
    await page.wait_for_load_state("networkidle")

async def panel_wait_for_ready(page):
    """Wait for any loading spinners to finish."""
    await page.wait_for_selector(":not(.loader)", state="visible")
```

### Interaction Fixtures

```python
async def panel_interact_table(page):
    """Common table interactions (sort, filter, paginate)."""
    
async def panel_interact_form(page):
    """Common form interactions (fill, submit, validate)."""
    
async def panel_interact_tabs(page):
    """Tab switching (click, verify active state)."""

async def panel_interact_search(page, search_term):
    """Search/filter in lists."""
```

### Assertion Fixtures

```python
async def panel_assert_visible(page, text=None):
    """Assert panel content is visible."""
    
async def panel_assert_has_content(page, content_type="table|form|chart"):
    """Assert panel has expected content type."""
    
async def panel_assert_no_errors(page):
    """Assert no error messages or 404s."""
```

### Performance Fixtures

```python
async def panel_measure_load_time(page, route):
    """Measure panel load time (ms)."""
    
async def panel_measure_tti(page):
    """Measure Time To Interactive."""
```

---

## Known Issues & Caveats

### File/Directory Shadowing (Resolved)

**Status:** ✅ FIXED (ADR-0431, 2026-08-27)

- Sibling file `pages/vibe-engineering.tsx` shadowed the `pages/vibe-engineering/` directory
- File beats directory in Node module resolution
- **Fix:** Old file deleted; new directory exported from `vibe-engineering/index.tsx`
- **Test:** `tests/unit/page-dir-shadow.test.ts` prevents recurrence

### Removed Panels (No Longer in Registry)

- ❌ `chat` — removed 2026-09-15 (never in PANELS registry, only hardcoded in App.tsx)
- ❌ `learning-dashboard` — removed per operator request
- ❌ `agents` — superseded by `agent-hub` (duplication)
- ❌ `space` — superseded by modern UI
- ❌ `webhooks`, `audit`, `releases` — backend routes 404 (not implemented)

### Video Producer (P1, requires flag)

- Flag: `video_producer_enabled` in capabilities manifest
- Test must verify: flag gating + feature availability

### Multi-Tenant Panels (Hidden)

- `orgs`, `people` — hidden from nav (future multi-tenant features)
- Routes exist but no sidebar entry
- Deep-linking works: `/app/orgs` is reachable but not discoverable

---

## Pytest Markers (pytest.ini)

```ini
[pytest]
markers =
    p0: Critical infrastructure panels (dashboard, login, models, vibe-engineering)
    p1: Core user-facing panels (settings, forge, skills, marketplace, video-producer)
    p2: High-value secondary panels (bridges, voice, quality, compliance, api-keys, agent-hub)
    p3: Secondary features (files, memory, compute, skill-forge, ldd, rag, rag-hub, connectors, etc.)
    p4: Edge cases (custom-provider, flows, datahub, licensing, telemetry, github)
    p5: Rarely used / future (orgs, people)
    p6: Error/onboarding pages (login, not-found, landing)
    critical_path: P0 + P1 + auth flow (run first, blocks Phase 2)
    slow: Panels that take >2s to load
    interactive: Panels with forms, tables, or tabs
    manifest_driven: Panels from backend capability manifest (plugins, installed)
```

---

## Execution Commands

### Run all panels (smoke test)

```bash
pytest tests/console/test_panel_smoke.py -v --marker=critical_path
```

### Run critical path only (P0–P1)

```bash
pytest tests/console/test_panel_*.py -m critical_path -v --tb=short
```

### Run with screenshots on failure

```bash
pytest tests/console/test_panel_*.py -v --screenshot=on-failure
```

### Run single panel

```bash
pytest tests/console/test_panel_dashboard.py -v
```

### Measure performance

```bash
pytest tests/console/test_panel_perf.py -v --benchmark
```

---

## Success Metrics

- ✅ 47/47 panels catalogued
- ✅ P0–P1 panels have E2E tests (9 tests)
- ✅ P2 panels have smoke tests (6 tests)
- ✅ No missing routes or 404s
- ✅ Navigation wiring verified (PANELS ↔ NAV_GROUPS sync)
- ✅ All lazy-loaded components render
- ✅ All forms validate & submit
- ✅ All tables paginate/sort/filter
- ✅ All charts render (no console errors)
- ✅ All tabs switch correctly
- ✅ No unhandled promise rejections
- ✅ Performance: P0 panels load <2s, P1 <3s

---

**Last Updated:** 2026-09-19 (Session 6, Phase A Blocker 1)  
**Next Review:** After Phase 2 E2E tests (Sep 21)
