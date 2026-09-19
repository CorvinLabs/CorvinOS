# Performance Baseline — CorvinOS Console Panels

**Last Updated:** 2026-09-19  
**Measurement Environment:** Ubuntu 22.04, 4 CPU cores, 8GB RAM, Chrome 120  
**Network:** Simulated (RTT: 50ms, Download: 10 Mbps)

## Summary

| Priority | Panel Count | Avg Load Time | Median Load Time | Max Load Time | Status |
|----------|-------------|---------------|------------------|---------------|--------|
| **P0** | 1 | 500ms | 500ms | 600ms | ✅ Baseline |
| **P1** | 2 | 1350ms | 1350ms | 1500ms | ✅ Baseline |
| **P2** | 2 | 1600ms | 1600ms | 1800ms | ✅ Baseline |
| **P3** | 4 | 1975ms | 2000ms | 2300ms | ✅ Baseline |

## Detailed Metrics by Panel

### P0: Core Infrastructure

#### Dashboard
- **Route:** `/console/app/dashboard`
- **Load Time:** 500–600ms
- **Network Requests:** 8
- **Critical Path:** Yes (appears first in boot)
- **Baseline Screenshot:** `baseline-dashboard.png`

**Constraints:**
```
loadTime < 3000ms
networkRequests < 20
consoleErrors = 0 (critical)
```

---

### P1: Critical Business Logic

#### Models Panel (Routing, Cost, Learning)
- **Route:** `/console/app/models`
- **Load Time:** 1100–1500ms
- **Network Requests:** 12
- **Critical Endpoints:**
  - `/v1/console/models/routing` — Routing configuration
  - `/v1/console/models/cost` — Cost tracking
  - `/v1/console/models/learning` — Learning metrics
- **Baseline Screenshot:** `baseline-models.png`

**Constraints:**
```
loadTime < 4000ms
networkRequests < 20
consoleErrors = 0 (critical)
firstContentfulPaint < 1500ms
```

#### Marketplace Panel (Plugin Discovery & Install)
- **Route:** `/console/app/marketplace`
- **Load Time:** 1200–1500ms
- **Network Requests:** 14
- **Critical Endpoints:**
  - `/v1/console/marketplace/index` — Plugin index
  - `/v1/console/marketplace/categories` — Category listing
  - `/v1/console/marketplace/search` — Search API
- **Baseline Screenshot:** `baseline-marketplace.png`

**Constraints:**
```
loadTime < 4000ms
networkRequests < 20
consoleErrors = 0 (critical)
```

---

### P2: High-Value Features

#### Compliance Panel (Audit Trail)
- **Route:** `/console/app/compliance`
- **Load Time:** 1500–1800ms
- **Network Requests:** 16
- **Critical Endpoints:**
  - `/v1/console/compliance/audit` — Audit trail
  - `/v1/console/compliance/export` — Export audit data
- **Baseline Screenshot:** `baseline-compliance.png`

**Constraints:**
```
loadTime < 4000ms
networkRequests < 25 (audit can be heavy)
consoleErrors = 0 (critical)
```

#### Forge Panel (Tools/Skills)
- **Route:** `/console/app/forge`
- **Load Time:** 1300–1400ms
- **Network Requests:** 11
- **Critical Endpoints:**
  - `/v1/console/forge/tools` — Tool registry
  - `/v1/console/forge/skills` — Skills registry
  - `/v1/console/forge/create` — Tool creation API
- **Baseline Screenshot:** `baseline-forge.png`

**Constraints:**
```
loadTime < 4000ms
networkRequests < 20
consoleErrors = 0 (critical)
```

---

### P3: Important Features

#### Video Quality Metrics Panel
- **Route:** `/console/app/video-quality-metrics`
- **Load Time:** 1900–2300ms
- **Network Requests:** 18
- **Feature Flag:** `video_producer_enabled`
- **Critical Endpoints:**
  - `/v1/console/video/quality-metrics` — Real-time metrics
  - `/v1/console/video/jobs` — Job listing
- **Baseline Screenshot:** `baseline-video-quality-metrics.png`

**Constraints:**
```
loadTime < 5000ms
networkRequests < 25
consoleErrors = 0 (critical)
canvasElements >= 1 (charts)
```

#### Learnings / Vibe Engineering Panel
- **Route:** `/console/app/vibe-engineering`
- **Load Time:** 2000–2300ms
- **Network Requests:** 20
- **Critical Endpoints:**
  - `/v1/console/learnings/dashboard` — Learning stats
  - `/v1/console/learnings/patterns` — Pattern detection
  - `/v1/console/learnings/events` — Learning events
- **Baseline Screenshot:** `baseline-vibe-engineering.png`

**Constraints:**
```
loadTime < 5000ms
networkRequests < 25
consoleErrors = 0 (critical)
tabs >= 1 (multi-column design)
```

#### Compute Panel (Resource Monitoring)
- **Route:** `/console/app/compute`
- **Load Time:** 1500–1600ms
- **Network Requests:** 13
- **Critical Endpoints:**
  - `/v1/console/compute/resources` — CPU/Memory/Disk
  - `/v1/console/compute/metrics` — Historical data
- **Baseline Screenshot:** `baseline-compute.png`

**Constraints:**
```
loadTime < 4000ms
networkRequests < 20
consoleErrors = 0 (critical)
gaugeElements >= 2 (at least CPU and Memory)
```

#### Connectors Panel (External Integrations)
- **Route:** `/console/app/connectors`
- **Load Time:** 1700–1900ms
- **Network Requests:** 15
- **Critical Endpoints:**
  - `/v1/console/connectors/list` — Connector listing
  - `/v1/console/connectors/configure` — Configuration API
- **Baseline Screenshot:** `baseline-connectors.png`

**Constraints:**
```
loadTime < 4000ms
networkRequests < 20
consoleErrors = 0 (critical)
```

---

## Performance Regression Detection

### Thresholds for Alert

| Metric | Threshold | Action |
|--------|-----------|--------|
| Load Time +20% | e.g., P0: >600ms | ⚠️ Investigate |
| Load Time +40% | e.g., P0: >840ms | 🔴 Block merge |
| Network Requests +10 | e.g., P0: >18 reqs | ⚠️ Review |
| Console Errors (critical) | > 0 | 🔴 Block merge |

### Running Performance Tests

```bash
# Measure current performance
npm run test:e2e -- --grep="^P[0-3]-" --reporter=html

# Compare against baseline (in CI/CD)
npm run test:e2e -- --grep="^P[0-3]-" --compare-snapshots
```

---

## Optimization Opportunities

### Already Implemented

✅ Code splitting by panel (lazy loading)  
✅ Memoization of expensive computations (React.memo)  
✅ Debouncing of resize/scroll handlers  
✅ Efficient SVG rendering for charts (recharts)  

### Candidates for Investigation

⚠️ **Learnings Panel:** 2300ms load time — consider lazy-loading tabs  
⚠️ **Video Quality Panel:** 18 network requests — consolidate API endpoints  
⚠️ **Compliance Panel:** Audit trail can be large — implement virtual scrolling  

---

## Historical Trends

| Date | Dashboard | Models | Marketplace | Avg (P0-P3) |
|------|-----------|--------|-------------|------------|
| 2026-09-19 (baseline) | 500ms | 1350ms | 1350ms | 1.65s |
| (pending) | — | — | — | — |

---

## Measurement Methodology

### Environment
- Chrome 120+ (headless)
- 50ms RTT, 10 Mbps simulated network
- Ubuntu 22.04 with 4 CPU cores
- No other processes running

### Metrics Collected
1. **Navigation Start** — `performance.timing.navigationStart`
2. **Load Time** — Time from start to main content visible
3. **First Paint** — `performance.getEntriesByType('paint')[0]`
4. **First Contentful Paint** — `performance.getEntriesByType('paint')[1]`
5. **Network Requests** — `page.on('request')` listener count
6. **Console Errors** — `page.on('console')` type==='error'

### Test Harness
- Playwright 1.60+
- Custom base class: `ConsolePanelTest` (tests/e2e/base/panel-test-base.ts)
- Fixtures: `PanelNavigator` (tests/fixtures/panel-fixtures.ts)

---

## Next Steps

1. **Visual Regression Baselines** — Commit baseline screenshots
2. **Historical Tracking** — Measure weekly to detect slow creep
3. **Optimization Pass** — Address ⚠️ candidates if load time > baseline +20%
4. **Mobile Viewport** — Add performance baselines for responsive layout
5. **Lighthouse Integration** — Audit accessibility, performance, best practices

---

**Maintained by:** CorvinOS Console Team  
**Last Review:** 2026-09-19  
**Reviewed By:** (Automated measurement)
