# Cost Calculation Specification (Technical)

## Pricing Formula

### Per-Token Pricing

```
cost = (input_tokens × input_rate) 
     + (output_tokens × output_rate)
     + (cache_read_tokens × cache_read_rate)
     + (cache_write_tokens × cache_write_rate)
```

**Units:** All costs in EUR (Euro)  
**Precision:** Decimal with 6 decimal places (e.g., 0.000003)

### Model Pricing Table (v1.0)

| Model | Input (€/M) | Output (€/M) | Cache Read (€/M) | Cache Write (€/M) |
|-------|-----------|------------|----------------|-----------------|
| claude-opus-5 | 0.003 | 0.015 | 0.0003 | 0.0003 |
| claude-sonnet-5 | 0.003 | 0.015 | 0.0003 | 0.0003 |
| claude-haiku-4-5 | 0.00008 | 0.0004 | 0.00001 | 0.00001 |

**Notes:**
- Cache read/write are 10% of input rate (Anthropic pricing model)
- All rates negotiable per contract (v1.1)

## Data Structures

### CallCost (Immutable)

```python
@dataclass(frozen=True)
class CallCost:
    call_id: str                    # Unique per call
    timestamp: str                  # ISO 8601 with timezone
    tenant_id: str                  # For multi-tenant isolation
    model_id: str                   # claude-opus-5, etc.
    
    input_tokens: int               # Raw count
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    
    input_cost: str                 # Decimal stringified (EUR)
    output_cost: str
    cache_read_cost: str = "0"
    cache_write_cost: str = "0"
    total_cost: str                 # Sum of above
    
    skill_id: Optional[str] = None  # E.g., "os.router"
    task_id: Optional[str] = None   # E.g., "task_123"
```

**Invariants:**
- `total_cost = input_cost + output_cost + cache_read_cost + cache_write_cost` (verified)
- All costs non-negative
- `timestamp` must be valid ISO 8601
- `tenant_id` never empty (fail-closed)

### SkillCost (Aggregated)

```python
@dataclass
class SkillCost:
    skill_id: str
    total_cost: Decimal             # Sum across all calls
    call_count: int                 # Number of calls
    avg_cost_per_call: Decimal      # total_cost / call_count
```

### TaskCostRecord (Breakdown)

```python
@dataclass
class TaskCostRecord:
    task_id: str
    timestamp: str                  # ISO 8601
    tenant_id: str
    
    total_cost: Decimal             # Sum of all calls in task
    llm_call_count: int
    worker_call_count: int
    duration_seconds: int
    
    cost_by_skill: dict[str, Decimal]   # {skill_id: cost}
    cost_by_model: dict[str, Decimal]   # {model_id: cost}
```

## Persistence Layer

### File Format: JSON Lines (JSONL)

**File:** `~/.corvin/tenants/_default/global/costs/calls_YYYY-MM-DD.jsonl`

**One record per line, no array wrapper:**
```jsonl
{"call_id":"c1","model_id":"claude-opus-5","input_tokens":1000,"total_cost":"0.003000"}
{"call_id":"c2","model_id":"claude-haiku-4-5","input_tokens":500,"total_cost":"0.000040"}
```

**Constraints:**
- Immutable append-only (never update/delete)
- Atomic writes (entire line or nothing)
- Daily rotation (new file each day)
- Tenant-scoped (separate files per tenant)
- File mode: `0o600` (owner read/write only)

### Daily Cost Aggregation

**Algorithm:**
1. Scan all `calls_YYYY-MM-DD.jsonl` files
2. For each line, parse timestamp (ISO 8601)
3. Extract date (YYYY-MM-DD)
4. Sum total_cost by date
5. Return dict[date, cost]

**Complexity:** O(N) where N = total records across all files  
**Optimization:** Index by date (v1.1)

### Skill Cost Aggregation

**Algorithm:**
1. Scan all `calls_*.jsonl` files
2. Filter: where `skill_id == target_skill`
3. Sum total_cost and call_count
4. Calculate avg_cost_per_call = total / count

**Complexity:** O(N)

### Task Cost Breakdown

**Algorithm:**
1. Scan all `calls_*.jsonl` files
2. Filter: where `task_id == target_task`
3. Group by `model_id` and `skill_id`
4. Sum costs within each group

**Complexity:** O(N)

## API Contracts

### Calculator API

```python
class CostCalculator:
    def calculate_call_cost(
        call_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        skill_id: Optional[str] = None,
        task_id: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> CallCost
    
    def get_skill_cost(skill_id: str) -> SkillCost
    
    def get_task_cost(task_id: str) -> TaskCostRecord
    
    def get_daily_cost(date: Optional[str] = None) -> Decimal
    
    def get_daily_costs_trend(days: int = 30) -> list[dict]
    
    def get_cost_by_model(days: int = 30) -> dict[str, str]
```

**Failure Modes:**
- Missing cost file → return 0 (graceful degradation)
- Corrupt line → skip and log error (fail-safe)
- Decimal overflow → cap at MAX_DECIMAL (no exceptions)

### REST API

#### GET /v1/console/cost/summary

**Response:**
```json
{
  "today_spend": "25.50",
  "yesterday_spend": "23.30",
  "weekly_avg": "24.10",
  "monthly_avg": "22.80",
  "trend_direction": "up",
  "trend_pct": 9.4,
  "projections": {
    "projected_monthly": "687.50"
  }
}
```

#### GET /v1/console/cost/breakdown?days=30

**Response:**
```json
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

#### GET /v1/console/cost/by-model?days=30

**Response:**
```json
{
  "models": {
    "claude-opus-5": "450.00",
    "claude-sonnet-5": "150.00",
    "claude-haiku-4-5": "50.00"
  }
}
```

#### GET /v1/console/cost/guardrails

**Response:**
```json
{
  "current_spend": "75.00",
  "budget_limit": "100.00",
  "percentage_used": 75.0,
  "remaining_budget": "25.00",
  "daily_limit": "3.33",
  "daily_spend": "2.50",
  "alert_active": true,
  "alert_level": "warning"
}
```

#### POST /v1/console/cost/guardrails

**Request:**
```json
{
  "monthly_budget": "200",
  "daily_budget": "6.67",
  "warning_threshold_pct": 75.0,
  "critical_threshold_pct": 90.0
}
```

**Response:** Same as GET

## Security & Compliance

### Tenant Isolation

- **Field:** Every record includes `tenant_id`
- **Query Filter:** All reads must filter by tenant_id
- **Enforcement:** No cross-tenant leakage allowed
- **Test:** `test_tenant_isolation_cost_records` in test suite

### Audit Trail

- All cost records immutable (frozen dataclasses)
- Recorded to `audit.jsonl` via `audit_backend` (ADR-0232)
- Hash-chained with previous record
- Boot tripwire verifies chain before startup (ADR-0232)

### GDPR Compliance (Art. 5, 32)

- **Data Minimization:** Only token counts + costs, no user content
- **Storage Limitation:** 90-day retention (enforced by ADR-0319)
- **Integrity & Confidentiality:** File mode 0o600, hash-chained
- **Right to Erasure (Art. 17):** L36 orchestrator handles deletion

## Testing Strategy

### Unit Tests
- `TestStory1CostCalculation` — Basic cost math
- `TestStory2SkillCost` — Skill aggregation
- `TestStory3TaskCost` — Task breakdown

### Integration Tests
- `TestAllStoriesIntegration` — Full flow: record → aggregate → recommend

### Edge Cases
- Zero-cost calls (0 tokens)
- Very large token counts (1M+)
- Different models in same task
- Corrupt cost files (skip gracefully)
- Missing files (return 0)
- Concurrent writes (atomic append)

### Performance Tests
- Record 10,000 calls, measure latency (<100ms)
- Query 30-day trend with 1M records (<1s)
- Concurrent readers (no lock contention)

## Error Handling

### Graceful Degradation
- Corrupt line in JSONL → skip, log, continue
- Missing cost file → assume 0 cost
- Invalid model ID → use default pricing

### Fail-Closed
- Spend cap denial when budget exceeded
- Alert on threshold breach (never suppressed)
- Decimal overflow → cap at MAX_DECIMAL

### Logging
- All errors logged to `logging.error()`
- No exceptions bubbled to caller (fire-and-forget)
- Cost calculation always succeeds

## Performance Characteristics

| Operation | Time Complexity | Space Complexity | Example |
|-----------|-----------------|------------------|---------|
| Record call | O(1) | O(1) | 1 line append |
| Get daily cost | O(N) | O(1) | N = lines in JSONL |
| Get skill cost | O(N) | O(1) | N = lines in all JSONL |
| Get task cost | O(N) | O(K) | K = unique models/skills |
| 30-day trend | O(N) | O(30) | Always return 30 days |

**N = total cost records (millions possible)**

**Optimization Roadmap (v1.1+):**
- Index by date (O(1) daily cost lookup)
- Index by skill (O(1) skill cost lookup)
- Cache aggregations (invalidate on new record)

## Versioning

**Current Version:** 1.0 (Phase 1 Week 3)

**Schema Versioning:**
- `CallCost` dataclass definition is canonical
- Adding fields: append-only (backward compatible)
- Removing fields: new major version
- Changing types: fail-closed conversion

**Data Migration (v1.0 → v1.1):**
- No migration needed (all fields optional after init)
- Old records (without new fields) still readable

## Integration Points

### Inputs
1. **LLM Call** → `calculate_call_cost()` call from `routes/chat.py`
2. **Worker Execution** → `calculate_call_cost()` call from worker engine
3. **Skill Invocation** → `skill_id` parameter on calculator

### Outputs
1. **Console Panel** → `GET /v1/console/cost/*` endpoints
2. **Budget Enforcement** → `enforce_spend_cap()` before LLM call
3. **Learning Loop** → Cost signal fed to `core.learning.optimizer`

### Dependencies
- `decimal.Decimal` (standard library)
- `pathlib.Path` (standard library)
- `json` (standard library)
- `dataclasses` (standard library)
- FastAPI (existing in CorvinOS)

## Known Limitations

- **v1.0:** Compute/storage costs estimated (stubbed)
- **v1.0:** No anomaly detection
- **v1.0:** No cost forecasting
- **v1.0:** Pricing rates hardcoded (not from BillingSchema)
- **v1.0:** No caching/indexing (full scan per query)

## Future Enhancements

1. **Cost Allocation** — Bill internal teams by cost center
2. **Anomaly Detection** — Flag unusual spending patterns (>2σ)
3. **Forecasting** — ML-based cost projection
4. **Carbon Tracking** — Cost per model including carbon cost
5. **Scheduled Reports** — Email/Slack daily summaries
6. **API Rate Limiting** — By-user cost quotas
