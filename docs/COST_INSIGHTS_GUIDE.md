# Cost Insights & Optimization — Stream 3 (Phase 1 Week 3)

## Overview

**Cost Insights** is a comprehensive cost tracking, analysis, and optimization system for CorvinOS. It provides real-time cost visibility, optimization recommendations, and budget enforcement across all LLM calls, skills, and tasks.

**10 User Stories (21 Story Points):**

### Cost Calculation (Stories 1-3)
1. **Cost Calculator** — Compute cost per LLM call (input_tokens, cache_read, cache_write, output_tokens at model-specific rates)
2. **Per-Skill Cost** — Aggregate costs by installed skill
3. **Cost per Task** — Show breakdown: how much did this task cost?

### Cost Dashboard (Stories 4-6)
4. **Daily Spend Trend** — Line chart: $/day over 30 days
5. **Cost by Component** — Pie chart: % split (LLM vs compute vs storage)
6. **Cost by Model** — Bar chart: total $ per model (Opus vs Sonnet vs Haiku)

### Optimization Insights (Stories 7-8)
7. **Savings Recommendation** — Suggest: LoRA fine-tuning, prompt caching, batch processing
8. **ROI Projection** — Show: if you implement X, save Y% in Z weeks

### Guardrails (Stories 9-10)
9. **Budget Alert** — Notify when spend > $X/day (configurable)
10. **Spend Cap** — Hard limit: deny requests if cumulative spend exceeds monthly budget

---

## Architecture

```
core/cost/
├── __init__.py                 # Public API
├── calculator.py               # Stories 1-3: Cost computation
│   ├── CallCost                # Immutable cost record per call
│   ├── SkillCost               # Aggregated cost per skill
│   ├── TaskCostRecord          # Cost breakdown per task
│   └── CostCalculator          # Main calculator service
├── optimizer.py                # Stories 7-8: Insights & ROI
│   ├── OptimizationRecommendation
│   ├── ROIProjection
│   └── CostOptimizer
└── guardrails.py               # Stories 9-10: Budget enforcement
    ├── BudgetConfig
    ├── BudgetStatus
    ├── BudgetAlert
    └── BudgetGuard

routes/cost_insights_routes.py  # Stories 4-6: API endpoints
├── GET  /v1/console/cost/summary
├── GET  /v1/console/cost/breakdown
├── GET  /v1/console/cost/by-model
├── GET  /v1/console/cost/recommendations
├── GET  /v1/console/cost/guardrails
└── POST /v1/console/cost/guardrails

web-next/src/panels/CostInsightsPanel.tsx  # Stories 4-6: React UI
├── Daily Spend Trend (line chart)
├── Cost by Component (pie chart)
├── Cost by Model (bar chart)
└── Optimization Recommendations (cards)
```

---

## Story Details

### Story 1: Cost Calculator — Cost Per Call

**Purpose:** Compute cost for a single LLM call.

**Module:** `core/cost/calculator.py::CostCalculator`

**API:**
```python
call_cost = calculator.calculate_call_cost(
    call_id="call_001",
    model_id="claude-opus-5",
    input_tokens=1000,
    output_tokens=500,
    cache_read_tokens=100,   # Optional
    cache_write_tokens=50,   # Optional
    skill_id="os.router",    # Optional
    task_id="task_123",      # Optional
)

# Returns CallCost with:
# - input_cost, output_cost, cache_read_cost, cache_write_cost
# - total_cost (sum of all)
# - Persisted to disk for audit
```

**Pricing Model:**
- Input tokens: €0.000003/token (Opus), €0.00000008/token (Haiku)
- Output tokens: €0.000015/token (Opus), €0.0000004/token (Haiku)
- Cache read tokens: 10% of input cost
- Cache write tokens: 10% of input cost

**Data Persistence:**
- Stored in: `~/.corvin/tenants/_default/global/costs/calls_YYYY-MM-DD.jsonl`
- Format: One JSON record per line (no array wrapper)
- Immutable append-only

---

### Story 2: Per-Skill Cost — Aggregate Costs by Skill

**Purpose:** Sum costs across all calls for a specific skill.

**Module:** `core/cost/calculator.py::CostCalculator.get_skill_cost()`

**API:**
```python
skill_cost = calculator.get_skill_cost("os.delegation_router")

# Returns SkillCost with:
# - skill_id
# - total_cost (sum of all calls)
# - call_count (number of calls)
# - avg_cost_per_call (total / call_count)
```

**Use Cases:**
- Identify expensive skills
- Optimize high-cost skill implementations
- Track cost per persona/skill

---

### Story 3: Cost Per Task — Task Cost Tracking

**Purpose:** Break down cost for a single task, showing which models and skills contributed.

**Module:** `core/cost/calculator.py::CostCalculator.get_task_cost()`

**API:**
```python
task_cost = calculator.get_task_cost("task_xyz_123")

# Returns TaskCostRecord with:
# - task_id
# - total_cost
# - llm_call_count, worker_call_count
# - cost_by_skill: {skill_id: cost}
# - cost_by_model: {model_id: cost}
```

**Use Cases:**
- Show cost in task details panel
- Identify expensive task patterns
- Track cost per task type

---

### Story 4: Daily Spend Trend — 30-Day Line Chart

**Purpose:** Visualize spending over 30 days for trend analysis.

**Module:** `core/cost/calculator.py::CostCalculator.get_daily_costs_trend()`

**API:**
```python
GET /v1/console/cost/summary

# Returns:
{
  "today_spend": "25.50",
  "yesterday_spend": "23.30",
  "weekly_avg": "24.10",
  "monthly_avg": "22.80",
  "trend_direction": "up",           # up, down, stable
  "trend_pct": 9.4,                  # % change week-over-week
  "projections": {
    "projected_monthly": "687.50"    # Weekly avg × 4.33 weeks
  }
}
```

**Chart:**
- X-axis: Date (YYYY-MM-DD)
- Y-axis: Cost in EUR
- Line: Daily spend
- Hover: Date, amount

---

### Story 5: Cost by Component — Pie Chart

**Purpose:** Show cost split between LLM, compute, and storage.

**Module:** `core/cost/calculator.py` + `routes/cost_insights_routes.py`

**API:**
```python
GET /v1/console/cost/breakdown?days=30

# Returns:
{
  "llm_cost": "500.00",
  "compute_cost": "100.00",
  "storage_cost": "50.00",
  "total_cost": "650.00",
  "llm_pct": 76.9,
  "compute_pct": 15.4,
  "storage_pct": 7.7
}
```

**Chart:**
- Pie slices: LLM, Compute, Storage
- Colors: Amber (LLM), Blue (Compute), Green (Storage)
- Labels: Name + percentage
- Hover: Amount in EUR

---

### Story 6: Cost by Model — Bar Chart

**Purpose:** Compare costs across models (Opus, Sonnet, Haiku).

**Module:** `core/cost/calculator.py::get_cost_by_model()`

**API:**
```python
GET /v1/console/cost/by-model?days=30

# Returns:
{
  "models": {
    "claude-opus-5": "450.00",
    "claude-sonnet-5": "150.00",
    "claude-haiku-4-5": "50.00"
  }
}
```

**Chart:**
- X-axis: Model name
- Y-axis: Total cost (EUR)
- Bars: One per model
- Hover: Cost, % of total

---

### Story 7: Savings Recommendations — LoRA, Cache, Batch

**Purpose:** Generate cost-saving suggestions based on usage patterns.

**Module:** `core/cost/optimizer.py::CostOptimizer.generate_recommendations()`

**API:**
```python
GET /v1/console/cost/recommendations

# Returns:
{
  "recommendations": [
    {
      "recommendation_id": "rec-lora-1",
      "title": "LoRA Fine-tuning for Claude Opus",
      "description": "Your usage is 60% Claude Opus. Fine-tuning a LoRA adapter can reduce costs by 40%...",
      "optimization_type": "lora",
      "estimated_savings_pct": 40.0,
      "estimated_savings_monthly": "200.00",
      "implementation_cost": "500",
      "roi_weeks": 2,
      "applicable_models": ["claude-opus-5"],
      "affected_calls_pct": 60.0,
      "confidence": 0.85
    },
    ...
  ],
  "total_potential_savings": "450.00"
}
```

**Recommendation Types:**
1. **LoRA Fine-tuning** — 40% cost reduction for repeat patterns
   - Cost: €500 one-time
   - Payback: 2 weeks
   
2. **Prompt Caching** — 9% savings by caching repeated context
   - Cost: €0 (free with API)
   - Payback: Immediate
   
3. **Batch Processing** — 15% savings on non-interactive tasks
   - Cost: €200 (engineering)
   - Payback: 3 weeks
   
4. **Model Downsampling** — Use Sonnet/Haiku for simple tasks
   - Cost: €100 (task classification)
   - Payback: 2 weeks

---

### Story 8: ROI Projection — Weekly/Cumulative Savings

**Purpose:** Show financial return over time for an optimization.

**Module:** `core/cost/optimizer.py::CostOptimizer.project_roi()`

**API:**
```python
POST /v1/console/cost/recommendations/{recommendation_id}/roi?weeks=12

# Returns:
{
  "recommendation_id": "rec-lora-1",
  "optimization_type": "lora",
  "weekly_savings": ["46.15", "46.15", "46.15", ...],
  "cumulative_savings": ["46.15", "92.30", "138.45", ..., "553.80"],
  "payback_week": 11   # When cumulative > implementation cost
}
```

**Chart:**
- X-axis: Week 1-12
- Y-axis: Cost in EUR (stacked)
- Area 1: Weekly savings
- Area 2: Cumulative savings (above weekly)
- Vertical line: Payback week

---

### Story 9: Budget Alert — Thresholds & Notifications

**Purpose:** Alert when spending approaches monthly budget.

**Module:** `core/cost/guardrails.py::BudgetGuard`

**API:**
```python
GET /v1/console/cost/guardrails

# Returns:
{
  "current_spend": "75.00",
  "budget_limit": "100.00",
  "percentage_used": 75.0,
  "remaining_budget": "25.00",
  "daily_limit": "3.33",
  "daily_spend": "2.50",
  "alert_active": true,
  "alert_level": "warning",
  "alert_message": "Warning: 75% of monthly budget used. Remaining: €25.00"
}
```

**Alert Levels:**
- **Warning** (75% of budget): Orange notification
- **Critical** (90% of budget): Red notification
- **Exceeded** (100%+ of budget): Red blocking alert

**Thresholds (Configurable):**
```python
POST /v1/console/cost/guardrails

{
  "monthly_budget": "200",
  "daily_budget": "6.67",
  "warning_threshold_pct": 75.0,
  "critical_threshold_pct": 90.0
}
```

---

### Story 10: Spend Cap — Hard Limit Enforcement

**Purpose:** Deny requests that would exceed budget.

**Module:** `core/cost/guardrails.py::BudgetGuard.enforce_spend_cap()`

**API:**
```python
# Before executing an LLM call:
allowed, denial_reason = guard.enforce_spend_cap(
    current_spend=Decimal("95"),
    requested_cost=Decimal("10"),
)

if not allowed:
    raise BudgetExceeded(denial_reason)

# Request is denied if: current_spend + requested_cost > budget_limit
```

**Denial Response:**
```json
{
  "error": "BudgetExceeded",
  "message": "Request would exceed monthly budget. Remaining: €5, Requested: €10"
}
```

**Configuration:**
- Can be disabled globally via config (`spend_cap_enabled: false`)
- Hard limit: no override flag (fail-closed)

---

## Usage Examples

### Example 1: Track Cost for a Chat Turn

```python
from core.cost.calculator import CostCalculator
from decimal import Decimal

calculator = CostCalculator("~/.corvin")

# After LLM call completes:
call_cost = calculator.calculate_call_cost(
    call_id="turn_abc123_llm_call_1",
    model_id="claude-opus-5",
    input_tokens=2500,
    output_tokens=800,
    cache_read_tokens=500,
    skill_id="chat.stream_handler",
    task_id="turn_abc123",
)

print(f"Call cost: €{call_cost.total_cost}")
```

### Example 2: Check Budget Before Executing

```python
from core.cost.guardrails import BudgetGuard
from decimal import Decimal

guard = BudgetGuard("~/.corvin")

# Set monthly budget
guard.set_budget(
    monthly_budget=Decimal("1000"),
    daily_budget=Decimal("33"),
)

# Before executing LLM call:
allowed, reason = guard.enforce_spend_cap(
    current_spend=Decimal("950"),
    requested_cost=Decimal("100"),
)

if not allowed:
    print(f"Denied: {reason}")
else:
    print("Request approved, executing LLM call...")
```

### Example 3: Get Optimization Recommendations

```python
from core.cost.optimizer import CostOptimizer
from decimal import Decimal

optimizer = CostOptimizer()

recommendations = optimizer.generate_recommendations(
    daily_spend=Decimal("100"),
    daily_call_count=1500,
    model_distribution={
        "claude-opus-5": 800,
        "claude-sonnet-5": 600,
        "claude-haiku-4-5": 100,
    },
    cache_hit_rate=0.30,
)

for rec in recommendations:
    print(f"{rec.title}")
    print(f"  Estimated savings: €{rec.estimated_savings_monthly}/month")
    print(f"  ROI: {rec.roi_weeks} weeks")
```

---

## Testing

**Test Suite:** `tests/cost/test_cost_insights_stream3_all_stories.py`

**Run all tests:**
```bash
pytest tests/cost/test_cost_insights_stream3_all_stories.py -v
```

**Test coverage by story:**
- Story 1: `TestStory1CostCalculation` — 3 tests
- Story 2: `TestStory2SkillCost` — 1 test
- Story 3: `TestStory3TaskCost` — 1 test
- Story 4: `TestStory4DailyTrend` — 1 test
- Story 5: `TestStory5ComponentBreakdown` — 1 test
- Story 6: `TestStory6ModelComparison` — 1 test
- Story 7: `TestStory7Recommendations` — 3 tests
- Story 8: `TestStory8ROIProjection` — 2 tests
- Story 9: `TestStory9BudgetAlert` — 4 tests
- Story 10: `TestStory10SpendCap` — 3 tests
- Integration: `TestAllStoriesIntegration` — 1 test

**Total: 21 tests** (one per story point)

---

## Data Persistence

**File Locations:**
```
~/.corvin/tenants/_default/global/
├── costs/
│   ├── calls_2026-09-22.jsonl       # Daily call records
│   ├── calls_2026-09-23.jsonl
│   └── ...
├── budget/
│   ├── config.json                  # Budget configuration
│   ├── alerts.jsonl                 # Alert history
│   └── denials.jsonl                # Spend cap denials
└── savings/
    ├── savings_2026-09.json         # Monthly savings
    └── ...
```

**File Modes:**
- All files created at `0o600` (read/write for owner only)
- Tenant-scoped isolation via `tenant_id` field

---

## Integration Points

**Dependencies:**
- `core.licensing.billing.BillingSchema` — For model pricing (v1.0)
- `core.storage.savings_store.SavingsStore` — For savings tracking (existing)
- `core.learning.event_persistence.EventStore` — For learning events (Phase 3)

**Consumers:**
- Console panel: `CostInsightsPanel.tsx` (React, Stories 4-6)
- Chat routes: `routes/chat.py` (cost tracking on LLM calls)
- Worker engine: `core.models.worker.py` (cost calculation per turn)
- Budget enforcement: `core.licensing.compute_license_gate.py` (deny on cap hit)

---

## Compliance & Security

**Audit Trail:**
- All cost records hash-chained in `audit.jsonl` (ADR-0232)
- Cost calculations immutable (frozen dataclasses)
- Spend cap denials logged with timestamp

**Tenant Isolation:**
- `tenant_id` field on all records
- Queries filtered by tenant_id (fail-closed)
- No cross-tenant cost leakage

**GDPR Compliance (Art. 5, 32):**
- Cost records pseudonymized (no user names, only IDs)
- 90-day retention default (enforced by ADR-0319)
- Erasure supported via L36 orchestrator

---

## Configuration

**Budget Config** (`~/.corvin/tenants/_default/global/budget/config.json`):
```json
{
  "monthly_budget": "1000",
  "daily_budget": "33.33",
  "warning_threshold_pct": 75.0,
  "critical_threshold_pct": 90.0,
  "spend_cap_enabled": true
}
```

**Pricing Config** (in `core/cost/calculator.py::_get_model_pricing()`):
- Hardcoded for v1.0
- Integrates with `BillingSchema` in v1.1

---

## Known Limitations (v1.0)

- [ ] Compute and storage costs stubbed (estimated at 20% + 5%)
- [ ] Recommendations trained on synthetic data (real data needed for v1.1)
- [ ] No anomaly detection (flagging unusual spending patterns)
- [ ] No scheduled reports (email/Slack alerts)
- [ ] No cost forecasting (predict next month based on trend)

---

## Next Steps (v1.1 - Phase 2)

1. **Real Compute Costs** — Integrate with actual worker/compute billing
2. **Anomaly Detection** — Flag spending spikes (>2σ from 7-day avg)
3. **Scheduled Reports** — Daily/weekly cost emails
4. **Cost Forecasting** — ML-based trend projection
5. **Chargeback** — Bill internal teams by cost center
6. **Sustainability** — Track carbon cost per model

---

## Support

- **Documentation:** This guide
- **Issues:** File in CorvinOS repo under `cost` label
- **Questions:** Ping @team-cost-insights
