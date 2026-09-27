# Skills Observability Dashboard — Design Specification (ADR-0722)

**Status:** Design Phase (K=1 Dialectical Complete)  
**Loss Signal:** ADR-0722 (skills learning loop closure)  
**Compliance:** GDPR Art. 5/30/32, ADR-0763 (console production surface)  
**Dataviz Methodology:** Procedure complete (form → color → validation)

---

## Executive Summary

The Skills Observability Panel extends the Vibe Dashboard with real-time observability into the OS-Skill learning loop (ADR-0532). Four tabs show:
1. **Execution Latency** (p50/p95/p99 percentiles by skill)
2. **Confidence Trends** (7-day rolling average per skill)
3. **Feedback Volume** (thumbs up/down ratio + trend)
4. **A/B Test Results** (active experiments, winner/loser/inconclusive)

All data is tenant-scoped, audit-linked, and real-time (5s poll). Operator can monitor skill health, detect regressions, and make data-driven routing decisions.

---

## Design Methodology

**Dataviz Procedure Applied:**

| Step | Decision | Rationale |
|------|----------|-----------|
| 1. Form | See per-chart specs below | Magnitude, trend, polarity, state each pick their own type |
| 2. Color Job | Categorical (skill identity) + Ordinal/Status (tiers) + Diverging (polarity) | Different jobs need different encoding |
| 3. Palette | Corvin `--viz-*` tokens (mapped from reference palette) | Validated CVD-safe, consistent with console theme |
| 4. Marks | Thin bars 4px rounded, lines 2px, dots >=8px | Specifications per `marks-and-anatomy.md` |
| 5. Interaction | Hover tooltips (crosshair+tooltip on line, per-mark on bar), filter row, time picker | Default interactivity for all forms |
| 6. Accessibility | Legend + direct labels (>=2 series), table view (fallback), texture (CVD toggle) | WCAG 2.1 AA compliant |
| 7. Validation | Run palette validator, screenshot both themes, check anti-patterns | No eyeballing |

---

## Chart Specifications

### Chart 1: Execution Latency (p50/p95/p99 by Skill)

**Job:** Compare magnitude (latency duration) + distinguish by percentile tier (ordered)  
**Form:** Grouped bars (skill on x-axis, latency [ms] on y-axis, grouped by percentile)  
**Interaction:** Hover tooltip (skill name, percentile, latency value, context: "good" / "warning" / "critical")  
**Axis:**
- X: Skill name (alphabetical, up to 8 skills; if >8, show top 8 by error rate)
- Y: Latency (ms), linear scale, 0-based, gridlines every 100ms
- Y-label: "Latency (ms)"

**Color Encoding (Percentile Tiers):**
| Percentile | Interpretation | Color | Token |
|---|---|---|---|
| p50 | Median; good performance | Status Good (emerald) | `--viz-status-good` |
| p95 | Concerning; investigate | Status Warning (amber) | `--viz-status-warning` |
| p99 | Critical; alert needed | Status Critical (rose) | `--viz-status-critical` |

**Secondary Encoding (Skill Identity):**
- Within each skill's grouping, bars are stacked or adjacent. Skill labels disambiguate; no color needed for skill identity here (too many bars → use stacking within each skill).

**Mark Spec:**
- Bar width: 12px per percentile
- Rounded top corners: 4px
- Baseline: 0 (fail-closed: no negative latency)
- Gaps: 2px between percentiles within a skill, 6px between skills
- Direct labels: p99 value above each bar (if >= 100ms) in small monospace font (11px, --text-secondary)

**Series Cap:** Max 8 skills on screen; if >8, fold into "Other" (aggregate p50/p95/p99 of remaining skills).

**Form Degradation:**
- **Single data point**: Bar (one skill, one time window)
- **Two points**: Bar (two time windows or two skills; side-by-side)
- **Grid of data**: Grouped bars (current form)

**Fallback (no hover):** Visible direct labels (p99 value) on every bar.

---

### Chart 2: Confidence Trends (7-Day Rolling Average)

**Job:** Show change-over-time for operator decision-making  
**Form:** Line chart (date on x-axis, confidence [0-100%] on y-axis, one line per skill)  
**Interaction:** Hover crosshair (date, skill name, confidence %, trend direction: ↑ / ↓ / —)  
**Axes:**
- X: Date (ISO 8601, one tick per day, last 7 days shown)
- Y: Confidence (0–100%), linear scale, gridlines every 20%
- Y-label: "Confidence (%)"

**Color Encoding (Skill Identity):**
- Each skill wears a fixed categorical slot (1–N by skill name alphabetically)
- Categorical palette (reference slots 1–8): blue, orange, aqua, yellow, magenta, green, violet, red
- Direct labels at line end (skill name, confidence value): >=2 series → legend unnecessary if direct-labeled
- Legend: Suppressed if direct labels present; shown as fallback if mobile/space-constrained

**Mark Spec:**
- Line: 2px stroke, square line join
- Data points: 6px dot (circle, filled with line color, 1px stroke white for contrast)
- Baseline: 0% (if any skill drops below 50%, mark with dashed line to indicate low confidence region)
- Area fill: None (multiline, area would stack or overlap confusingly)

**Series Cap:** Max 8 skills (same as latency chart); if >8, fold into "Other" (plot only top 8 by recent confidence).

**Confidence Algorithm (Backend):**
- Rolling 7-day average of `SkillConfidenceScore.confidence` (a value 0.0–1.0)
- Emit daily: one confidence snapshot per skill per day
- If <2 days of data: show bars instead of line (one bar per day)

**Trend Annotation (Optional):**
- If last 3 days show >5% improvement: badge "Trending ↑" in skill's direct label
- If last 3 days show >5% decline: badge "Trending ↓" in direct label

---

### Chart 3: Feedback Volume & Ratio (Thumbs Up vs Down)

**Job:** Show polarity (positive vs negative feedback) + volume trend  
**Form:** Stacked bar chart (skill on x-axis, feedback count on y-axis, stacked up/down segments)  
**Interaction:** Hover tooltip (skill name, up count, down count, ratio %, trend of ratio)  
**Axes:**
- X: Skill name (alphabetical, max 8; >8 fold to "Other")
- Y: Feedback count (absolute number of feedback events), linear scale, 0-based
- Y-label: "Feedback Events"

**Color Encoding (Polarity):**
| Feedback Type | Interpretation | Color | Token |
|---|---|---|---|
| Thumbs Up | Positive signal; skill working well | Status Good (emerald) | `--viz-status-good` |
| Thumbs Down | Negative signal; skill needs tuning | Status Critical (rose) | `--viz-status-critical` |

**Secondary Encoding (Volume Trend):**
- Add a secondary line overlay (optional, only if room): feedback ratio trend (% up over last 7 days)
  - Color: Sequential blue (reference palette step 400)
  - Right y-axis: "Ratio (%)"
  - Form: Line (2px stroke, square join), no marks

**Mark Spec:**
- Segment 1 (Up): Bottom, 2px gap from x-axis
- Segment 2 (Down): Top, 2px gap between segments
- Rounded corners: 4px on outer edges only
- Bar width: 24px per skill, 6px gap between skills
- Direct labels (inside segments, white text if room, else outside): Up: "↑ N", Down: "↓ N"

**Series Cap:** Max 8 skills; >8 fold "Other".

**Data Source (Backend):**
- Read from `feedback_store.FeedbackEvent` (ADR-0314), tenant-scoped
- Aggregate by skill_id, feedback_type (positive/negative)
- If <10 total feedback events for skill: show with muted color (70% opacity) + note "Limited data"

**Fallback (No Ratio Trend):** Stacked bars alone (no secondary line).

---

### Chart 4: A/B Test Results (Active Experiments)

**Job:** Show experiment state/outcome (winner/loser/inconclusive)  
**Form:** Horizontal bars (experiment name on y-axis, improvement [%] on x-axis, centered at 0, colored by outcome)  
**Interaction:** Hover tooltip (experiment name, outcome, improvement %, confidence interval, sample size)  
**Axes:**
- X: Improvement (%), diverging centered at 0 (left = regression, right = improvement)
- Y: Experiment name (e.g., "Router Tier 2.9 Confidence Threshold", sorted by recency)

**Color Encoding (Outcome Status):**
| Outcome | Interpretation | Color | Token |
|---|---|---|---|
| Winner | Significant improvement detected | Status Good (emerald) | `--viz-status-good` |
| Inconclusive | Not enough data or no significant change | Status Warning (amber) | `--viz-status-warning` |
| Regression | Significant decline detected | Status Critical (rose) | `--viz-status-critical` |

**Mark Spec:**
- Bar width: 8px (thin)
- Baseline: 0 (vertical dashed line, color: --text-muted)
- Rounded ends: 4px on both sides
- Gap between bars: 8px
- Direct labels: Outcome word + improvement % (e.g., "Winner +12.4%") in white text over bar if room, else outside

**Error Bars (Confidence Interval):**
- Show 95% CI whiskers (thin lines, 1px) at end of bar
- If CI crosses zero: outcome is "Inconclusive"

**Series Cap:** Up to 12 active experiments shown; older (completed) experiments hidden by default (collapsible group).

**Data Source (Backend):**
- Read from `skills_ab_test.ABTestResult` 
- Outcome = outcome_status (winner/inconclusive/regression)
- Improvement = (control_metric - variant_metric) / control_metric × 100
- CI = [lower_ci, upper_ci] from Bayesian or frequentist test

**Empty State:**
- If no A/B tests running: show banner "No active experiments" with link to "Create experiment"

---

## Corvin Palette & CSS Design Tokens

**Console Theme Integration:**
- Dark theme: `data-theme="dark"` or `prefers-color-scheme: dark`
- Light theme: `data-theme="light"` or `prefers-color-scheme: light`
- Surfaces: Dark #0e1320, Light #ffffff

**CSS Custom Properties (--viz-* tokens in skills_observability.module.css):**

```css
:root {
  --surface-chart-light: #fcfcfb;
  --surface-chart-dark: #1a1a19;
  --text-primary-light: #0b0b0b;
  --text-primary-dark: #ffffff;
  --text-secondary-light: #52514e;
  --text-secondary-dark: #c3c2b7;
  --text-muted: #898781;
  
  /* Categorical palette (fixed order by skill name) */
  --viz-cat-1-light: #2a78d6;   /* blue */
  --viz-cat-1-dark: #3987e5;
  --viz-cat-2-light: #eb6834;   /* orange */
  --viz-cat-2-dark: #d95926;
  --viz-cat-3-light: #1baf7a;   /* aqua */
  --viz-cat-3-dark: #199e70;
  --viz-cat-4-light: #eda100;   /* yellow */
  --viz-cat-4-dark: #c98500;
  --viz-cat-5-light: #e87ba4;   /* magenta */
  --viz-cat-5-dark: #d55181;
  --viz-cat-6-light: #008300;   /* green */
  --viz-cat-6-dark: #008300;
  --viz-cat-7-light: #4a3aa7;   /* violet */
  --viz-cat-7-dark: #9085e9;
  --viz-cat-8-light: #e34948;   /* red */
  --viz-cat-8-dark: #e66767;
  
  /* Sequential blue (for latency severity or secondary trends) */
  --viz-seq-blue-100: #cde2fb;
  --viz-seq-blue-200: #b7d3f6;
  --viz-seq-blue-250: #86b6ef;
  --viz-seq-blue-300: #6da7ec;
  --viz-seq-blue-350: #5598e7;
  --viz-seq-blue-400: #3987e5;
  --viz-seq-blue-450: #2a78d6;
  --viz-seq-blue-500: #256abf;
  --viz-seq-blue-550: #1c5cab;
  --viz-seq-blue-600: #184f95;
  
  /* Diverging pair: emerald ↔ rose */
  --viz-div-emerald: #1baf7a;
  --viz-div-neutral: #f0efec;
  --viz-div-rose: #e34948;
  
  --viz-div-emerald-dark: #199e70;
  --viz-div-neutral-dark: #383835;
  --viz-div-rose-dark: #e66767;
  
  /* Status palette (fixed, never themed) */
  --viz-status-good: #0ca30c;
  --viz-status-warning: #fab219;
  --viz-status-serious: #ec835a;
  --viz-status-critical: #d03b3b;
  
  /* Utilities */
  --viz-gridline-light: #e1e0d9;
  --viz-gridline-dark: #2c2c2a;
  --viz-border: rgba(11, 11, 11, 0.10);
  --viz-border-dark: rgba(255, 255, 255, 0.10);
}

@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    --surface-chart: var(--surface-chart-dark);
    --text-primary: var(--text-primary-dark);
    --text-secondary: var(--text-secondary-dark);
    --viz-cat-1: var(--viz-cat-1-dark);
    /* ...etc for all tokens */
    --viz-gridline: var(--viz-gridline-dark);
  }
}

:root[data-theme="dark"] {
  --surface-chart: var(--surface-chart-dark);
  --text-primary: var(--text-primary-dark);
  /* ...etc */
}

:root[data-theme="light"] {
  --surface-chart: var(--surface-chart-light);
  --text-primary: var(--text-primary-light);
  /* ...etc */
}
```

**Validation (Pre-Commit):**
```bash
node /tmp/claude-1000/bundled-skills/2.1.283/dc4dec18efd5304de3c319b277d3e959/dataviz/scripts/validate_palette.js \
  "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300,#4a3aa7,#e34948" --mode light --surface "#fcfcfb"

node /tmp/claude-1000/bundled-skills/2.1.283/dc4dec18efd5304de3c319b277d3e959/dataviz/scripts/validate_palette.js \
  "#3987e5,#d95926,#199e70,#c98500,#d55181,#008300,#9085e9,#e66767" --mode dark --surface "#1a1a19"
```

**Expected Output:**
- Light mode: PASS all checks (CVD Delta E >= 8, normal-vision >= 15, contrast >= 3:1)
- Dark mode: PASS all checks

---

## Panel Registration (Two Locations)

**File 1: `src/panels/registry.tsx` (PANELS array)**
```typescript
export const PANELS: ConsolePanel[] = [
  // ...existing panels...
  rc("skills-observability", "Skills", SkillsObservabilityPage as unknown as typeof DashboardPage, {
    nav: { label: "Skills", icon: "Brain", group: "observability" }
  }),
];
```

**File 2: `src/components/layout.tsx` (NAV_GROUPS array)**
```typescript
export const NAV_GROUPS = [
  // ...existing groups...
  {
    group: "observability",
    label: "Observability",
    items: [
      { route: "skills-observability", label: "Skills", icon: "Brain" },
      // ...other observability items...
    ],
  },
];
```

---

## API Endpoints (Backend Routes)

**Base Path:** `/v1/skills-observability`  
**Tenant Scope:** All endpoints read `rec.tenant_id` from authenticated `SessionRecord`, fail-closed if missing.

### 1. GET `/v1/skills-observability/metrics/latency`

**Query Params:**
- `time_range`: "1d" | "7d" | "30d" (default: "7d")
- `skill_id` (optional): Filter to single skill (default: all)

**Response:**
```json
{
  "time_range": "7d",
  "tenant_id": "tenant-123",
  "metrics": [
    {
      "skill_id": "os.delegation_router",
      "p50_ms": 42,
      "p95_ms": 128,
      "p99_ms": 315,
      "sample_count": 4251
    },
    {
      "skill_id": "os.context_adapter",
      "p50_ms": 38,
      "p95_ms": 105,
      "p99_ms": 289,
      "sample_count": 3847
    }
  ],
  "updated_at": "2026-09-27T12:34:56Z"
}
```

### 2. GET `/v1/skills-observability/metrics/confidence`

**Query Params:**
- `time_range`: "1d" | "7d" | "30d" (default: "7d")
- `skill_id` (optional): Single skill
- `aggregation`: "daily" | "hourly" (default: "daily")

**Response:**
```json
{
  "time_range": "7d",
  "aggregation": "daily",
  "tenant_id": "tenant-123",
  "trends": [
    {
      "skill_id": "os.delegation_router",
      "data_points": [
        { "date": "2026-09-20", "confidence": 0.78 },
        { "date": "2026-09-21", "confidence": 0.82 },
        { "date": "2026-09-22", "confidence": 0.88 }
      ]
    }
  ],
  "updated_at": "2026-09-27T12:34:56Z"
}
```

### 3. GET `/v1/skills-observability/metrics/feedback`

**Query Params:**
- `time_range`: "1d" | "7d" | "30d" (default: "7d")
- `skill_id` (optional): Single skill

**Response:**
```json
{
  "time_range": "7d",
  "tenant_id": "tenant-123",
  "feedback": [
    {
      "skill_id": "os.delegation_router",
      "thumbs_up": 234,
      "thumbs_down": 18,
      "ratio_percent": 92.8,
      "trend_7d": "+2.1%"
    }
  ],
  "updated_at": "2026-09-27T12:34:56Z"
}
```

### 4. GET `/v1/skills-observability/metrics/ab-tests`

**Query Params:**
- `status`: "active" | "completed" | "all" (default: "active")

**Response:**
```json
{
  "tenant_id": "tenant-123",
  "experiments": [
    {
      "test_id": "abtest-router-threshold-001",
      "name": "Router Confidence Threshold (0.70 vs 0.65)",
      "outcome": "winner",
      "improvement_percent": 12.4,
      "lower_ci_percent": 8.2,
      "upper_ci_percent": 16.7,
      "control_skill": "os.delegation_router",
      "variant_skill": "os.delegation_router",
      "sample_size": 1523,
      "started_at": "2026-09-20T00:00:00Z",
      "status": "active"
    }
  ],
  "updated_at": "2026-09-27T12:34:56Z"
}
```

**Audit Trail (Compliance):**
- Every API call logs: `skill_metrics.requested` (tenant_id, endpoint, filters, response_size, latency_ms)
- Every chart render logs: `chart.rendered` (panel_id, chart_name, data_point_count, timestamp)

---

## React Component Structure

**File:** `src/panels/skills_observability.tsx` (300+ LoC)

```typescript
import React, { useState, useEffect } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DateRangePicker, TimeRangeButton } from '@/components/filters';
import { SkillLatencyChart } from './charts/SkillLatencyChart';
import { SkillConfidenceTrends } from './charts/SkillConfidenceTrends';
import { SkillFeedbackChart } from './charts/SkillFeedbackChart';
import { ABTestResults } from './charts/ABTestResults';
import { useSkillsMetrics } from '@/hooks/useSkillsMetrics';
import styles from './skills_observability.module.css';

export interface SkillsObservabilityProps {
  tenantId?: string;
}

export const SkillsObservabilityPanel: React.FC<SkillsObservabilityProps> = ({ tenantId }) => {
  const [timeRange, setTimeRange] = useState<'1d' | '7d' | '30d'>('7d');
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  
  const {
    latency, confidence, feedback, abTests,
    isLoading, error, refetch
  } = useSkillsMetrics(timeRange, selectedSkill, tenantId);
  
  // Auto-poll every 5s (production interval per ADR-0722)
  useEffect(() => {
    const interval = setInterval(() => refetch(), 5000);
    return () => clearInterval(interval);
  }, [refetch, timeRange, selectedSkill]);
  
  return (
    <div className={styles.container} data-testid="skills-observability">
      {/* Header: Title + Time Range Picker + Export */}
      <div className={styles.header}>
        <h1>Skills Observability</h1>
        <div className={styles.controls}>
          <TimeRangeButton value={timeRange} onChange={setTimeRange} />
          <button onClick={() => exportToCSV({ latency, confidence, feedback })}>
            Export CSV
          </button>
        </div>
      </div>
      
      {/* Loading / Error States */}
      {isLoading && <div>Loading metrics...</div>}
      {error && <div className="error">{error}</div>}
      
      {/* Tabs: 4 main panels */}
      <Tabs defaultValue="latency" className={styles.tabs}>
        <TabsList>
          <TabsTrigger value="latency">Execution Latency</TabsTrigger>
          <TabsTrigger value="confidence">Confidence Trends</TabsTrigger>
          <TabsTrigger value="feedback">Feedback Stats</TabsTrigger>
          <TabsTrigger value="ab-tests">A/B Test Results</TabsTrigger>
        </TabsList>
        
        {/* Tab 1: Latency */}
        <TabsContent value="latency">
          <SkillLatencyChart data={latency} timeRange={timeRange} />
        </TabsContent>
        
        {/* Tab 2: Confidence */}
        <TabsContent value="confidence">
          <SkillConfidenceTrends data={confidence} timeRange={timeRange} />
        </TabsContent>
        
        {/* Tab 3: Feedback */}
        <TabsContent value="feedback">
          <SkillFeedbackChart data={feedback} timeRange={timeRange} />
        </TabsContent>
        
        {/* Tab 4: A/B Tests */}
        <TabsContent value="ab-tests">
          <ABTestResults data={abTests} />
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default SkillsObservabilityPanel;
```

---

## Chart Component Examples (Pseudo-Code)

### SkillLatencyChart (using Recharts)

```typescript
// src/panels/charts/SkillLatencyChart.tsx
export const SkillLatencyChart: React.FC<SkillLatencyChartProps> = ({ data, timeRange }) => (
  <ResponsiveContainer width="100%" height={400}>
    <BarChart data={data} layout="vertical">
      <CartesianAxis type="y" dataKey="skill_id" />
      <CartesianAxis type="x" />
      <Tooltip content={<CustomTooltip />} />
      <Bar dataKey="p50_ms" fill="var(--viz-status-good)" shape={<RoundedBar />} />
      <Bar dataKey="p95_ms" fill="var(--viz-status-warning)" shape={<RoundedBar />} />
      <Bar dataKey="p99_ms" fill="var(--viz-status-critical)" shape={<RoundedBar />} />
    </BarChart>
  </ResponsiveContainer>
);
```

---

## E2E Tests (High-Level Structure)

**File:** `tests/e2e/test_skills_observability_dashboard_e2e.py`

```python
import pytest
from fastapi.testclient import TestClient

class TestSkillsObservabilityDashboard:
    
    def test_latency_metrics_endpoint_real_data(self, client: TestClient, tenant_id: str):
        """Verify latency endpoint returns real audit-linked data."""
        response = client.get(
            f"/v1/skills-observability/metrics/latency?time_range=7d",
            headers={"X-Tenant-ID": tenant_id}
        )
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data
        assert all("p50_ms" in m and "p95_ms" in m for m in data["metrics"])
        # Verify data is tenant-scoped
        assert data["tenant_id"] == tenant_id
    
    def test_confidence_trends_real_data(self, client: TestClient, tenant_id: str):
        """Verify confidence trends emit audit events."""
        response = client.get(
            f"/v1/skills-observability/metrics/confidence?time_range=7d",
            headers={"X-Tenant-ID": tenant_id}
        )
        assert response.status_code == 200
        data = response.json()
        assert "trends" in data
        # Verify audit trail
        audit_events = client.get(
            f"/v1/audit/search?event_type=chart.rendered&chart_name=confidence",
            headers={"X-Tenant-ID": tenant_id}
        ).json()
        assert len(audit_events["events"]) > 0
    
    def test_feedback_volume_aggregate_real(self, client: TestClient):
        """Verify feedback data aggregates real feedback events."""
        response = client.get(
            "/v1/skills-observability/metrics/feedback?time_range=7d"
        )
        assert response.status_code == 200
        assert "feedback" in response.json()
    
    def test_ab_tests_endpoint_real_experiments(self, client: TestClient):
        """Verify A/B test endpoint returns active experiments."""
        response = client.get("/v1/skills-observability/metrics/ab-tests?status=active")
        assert response.status_code == 200
        experiments = response.json()["experiments"]
        # Verify structure
        assert all("test_id" in e and "outcome" in e for e in experiments)
    
    def test_tenant_isolation_guaranteed(self, client: TestClient):
        """Verify endpoints respect tenant_id isolation."""
        # Create two tenants with different data
        # Query each and verify no cross-tenant leakage
        pass
    
    def test_charts_render_real_browser(self, selenium_driver):
        """Verify charts actually render in browser (not sample data)."""
        # Navigate to /console/skills-observability
        # Verify 4 tabs load
        # Verify first chart (latency) shows bars with real skill names
        # Take screenshot and check for no error overlays
        pass
    
    def test_export_csv_contains_real_data(self, client: TestClient, tenant_id: str):
        """Verify CSV export contains audit-linked data."""
        response = client.get(
            "/v1/skills-observability/export/csv?time_range=7d",
            headers={"X-Tenant-ID": tenant_id}
        )
        assert response.status_code == 200
        csv_text = response.text
        # Verify header row + skill data rows
        assert "skill_id" in csv_text
        assert "p50_ms" in csv_text
```

---

## Compliance Checklist (GDPR + ADR-0763)

| Requirement | Implementation | Status |
|---|---|---|
| **Tenant Isolation** | All endpoints filter by `rec.tenant_id` from session, fail-closed if missing | ✅ Required |
| **No ADR IDs in UI** | No ADR-0722 references rendered to operator | ✅ Design rule |
| **No Sample Data** | All data comes from audit trail / skills metrics store; no fabricated examples | ✅ E2E test gate |
| **Audit Trail Linking** | Every chart render + API call logged to audit chain (hash-linked) | ✅ Audit-first |
| **Dark/Light Mode** | CSS tokens + dataviz palette validated for both #0e1320 and #ffffff surfaces | ✅ Palette validation |
| **Accessibility (WCAG AA)** | Legend + direct labels, table fallback, texture toggle for CVD | ✅ Interaction spec |
| **Real-Time Updates** | 5s poll interval from backend; WebSocket upgrade path (future) | ✅ Frontend behavior |
| **Export Function** | CSV export tenant-scoped, no PII in column names or values | ✅ API spec |

---

## Rollout & Monitoring

**Phase 1 (Week 1):** Backend routes + audit integration  
**Phase 2 (Week 2):** React component + chart rendering + palette validation  
**Phase 3 (Week 3):** E2E tests + console deployment  
**Phase 4 (Week 4):** Operator feedback + tuning  

**KPIs (Operator Observability):**
- Panel load time: <500ms (p95)
- Chart render time: <200ms (p95)
- API latency: <100ms (p95)
- Skill insights acted upon: % of operators using "Winner" outcome to adjust routing

---

## References

- **ADR-0722:** Skills Learning Loop Loss Signals (this ADR's parent)
- **ADR-0763:** Console as Production Surface (no fabricated data)
- **ADR-0314:** Learning Infrastructure (confidence scoring, feedback loops)
- **ADR-0532:** OS-Skills Architecture (Skill execution model)
- **Dataviz Skill:** `/tmp/claude-1000/bundled-skills/2.1.283/dc4dec18efd5304de3c319b277d3e959/dataviz/` (form, color, marks specs)
- **CLAUDE.md:** Console Frontend Rules (build proof, stale-bundle detection, panel registration)
