# Task Engine Prometheus Metrics (ADR-0267)

Production-ready Prometheus exporter for Task Engine monitoring.

## Overview

The `TaskMetrics` class exports **7 core metrics** across all 6 phases of the task analysis pipeline:

| # | Metric | Type | Labels | Purpose |
|---|--------|------|--------|---------|
| 1 | `task_analysis_phase_duration_seconds` | Histogram | `phase`, `outcome` | Measure timing + success/failure |
| 2 | `task_analysis_confidence_score` | Histogram | — | Routing confidence distribution |
| 3 | `task_analysis_routing_decision_total` | Counter | `target`, `carve_out_reason` | Delegation target frequency |
| 4 | `task_analysis_model_selection_total` | Counter | `model` | Haiku vs Opus selection ratio |
| 5 | `task_analysis_graph_redundancy_ratio` | Gauge | — | Graph deduplication efficiency |
| 6 | `task_analysis_estimated_cost_usd` | Histogram | — | Cost estimation distribution |
| 7 | `task_analysis_contract_violations_total` | Counter | `phase` | Data contract breach frequency |

## Usage

### Basic Integration

```python
from operator.task_analysis import TaskEngine, TaskMetrics

# Create engine with metrics
metrics = TaskMetrics()
engine = TaskEngine(metrics=metrics)

# Route a task (metrics recorded automatically)
result = engine.route_task("Fix bug in voice module")

# Access metrics summary for this run
summary = metrics.summary()
print(f"Total duration: {summary['total_duration_seconds']:.3f}s")
print(f"Contract violations: {summary['total_contract_violations']}")
```

### Context Manager (Manual Timing)

```python
from operator.task_analysis.metrics import TaskMetrics, MetricsPhase, MetricsOutcome

metrics = TaskMetrics()

# Auto-timing a phase
with metrics.phase_timer(MetricsPhase.ENRICHMENT) as ctx:
    result = enricher.enrich(task)
    # Timer stops and records automatically
    ctx['outcome'] = MetricsOutcome.SUCCESS

# Or record manually
metrics.record_phase(MetricsPhase.ENRICHMENT, 0.123, MetricsOutcome.SUCCESS)
```

### Recording Individual Metrics

```python
# Confidence (0.0–1.0, auto-clamped)
metrics.record_confidence(0.85)

# Routing decision (e.g., "native", "acs", "tde")
metrics.record_decision("acs", "big_data_vocabulary")

# Model selection
metrics.record_model_selection("opus")

# Graph deduplication efficiency
metrics.record_redundancy(original_count=10, filtered_count=5)

# Estimated cost
metrics.record_cost(0.05)  # USD
```

## Prometheus Integration

### Exporting to Prometheus

1. **Direct registry:**
   ```python
   from prometheus_client import CollectorRegistry, start_http_server
   
   registry = CollectorRegistry()
   metrics = TaskMetrics(registry=registry)
   
   # Start metrics endpoint on :8000
   start_http_server(8000, registry=registry)
   ```

2. **Scrape configuration (prometheus.yml):**
   ```yaml
   scrape_configs:
     - job_name: 'task_engine'
       static_configs:
         - targets: ['localhost:8000']
   ```

## No-op Mode

If `prometheus_client` is not installed, `TaskMetrics` runs in no-op mode:
- All methods complete without error
- No metrics are collected
- Useful for development/testing

```python
metrics = TaskMetrics()
if not metrics._enabled:
    print("prometheus_client not installed (no-op mode)")
```

## Metrics Semantics

### Phase Duration (`phase_duration_seconds`)

- **Buckets:** 0.01s, 0.05s, 0.1s, 0.5s, 1s, 5s, 10s
- **Labels:**
  - `phase`: normalization | classification | filtering | validation | enrichment | delegation
  - `outcome`: success | failure | partial
- **Use case:** SLA monitoring, bottleneck detection

### Confidence Score (`confidence_score`)

- **Buckets:** 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0
- **Auto-clamps to [0.0, 1.0]**
- **Use case:** Decision reliability histogram

### Routing Decision (`routing_decision_total`)

- **Labels:**
  - `target`: native | acs | tde
  - `carve_out_reason`: none | big_data_vocabulary | high_complexity_opus | tabular_paste | structured_source
- **Use case:** Carve-out frequency analysis, delegation split tracking

### Model Selection (`model_selection_total`)

- **Labels:** `model` = haiku | opus
- **Use case:** Model usage ratio, cost distribution

### Graph Redundancy Ratio (`graph_redundancy_ratio`)

- **Range:** 0.0–1.0 (1.0 = all graphs filtered, 0.0 = no filtering)
- **Calculation:** (original_count − filtered_count) / original_count
- **Use case:** Graph deduplication effectiveness

### Estimated Cost (`estimated_cost_usd`)

- **Buckets:** 0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0 USD
- **Clamped to non-negative**
- **Use case:** Cost projection, tier allocation

### Contract Violations (`contract_violations_total`)

- **Labels:** `phase` = normalization | classification | filtering | validation | enrichment | delegation
- **Increments on:** data contract breach between phases
- **Use case:** Data integrity monitoring, drift detection

## Contract Violation Tracking

Each phase's output is validated before passing to the next phase. Violations are:

1. Recorded as a counter (`contract_violations_total`)
2. Logged as a warning
3. Stored in the phase timer context (`ctx['contract_violation'] = True`)

```python
with metrics.phase_timer(MetricsPhase.CLASSIFICATION) as ctx:
    classified = classifier.classify(normalized)
    try:
        PhaseContracts.validate_phase1_output(classified)
    except ContractViolation as e:
        ctx['contract_violation'] = True
        ctx['violation_details'] = str(e)
        raise  # Propagate to engine error handler
```

## Example Prometheus Queries

### Average phase duration
```promql
avg(rate(task_analysis_phase_duration_seconds_sum[5m]) / rate(task_analysis_phase_duration_seconds_count[5m]))
```

### P95 routing confidence
```promql
histogram_quantile(0.95, rate(task_analysis_confidence_score_bucket[5m]))
```

### Delegation ratio (ACS vs TDE vs Native)
```promql
sum by (target) (rate(task_analysis_routing_decision_total[5m]))
```

### Contract violation rate (per phase)
```promql
sum by (phase) (rate(task_analysis_contract_violations_total[5m]))
```

### Average estimated cost per routing decision
```promql
sum(rate(task_analysis_estimated_cost_usd_sum[5m])) / sum(rate(task_analysis_routing_decision_total[5m]))
```

## Summary Method

After each `route_task()` call, fetch the run's metrics:

```python
summary = metrics.summary()
# Returns:
# {
#   "total_duration_seconds": 0.456,
#   "phases": {
#     "normalization": {"duration_seconds": 0.050, "outcome": "success", "contract_violation": false},
#     "classification": {"duration_seconds": 0.120, "outcome": "success", "contract_violation": false},
#     ...
#   },
#   "total_contract_violations": 0
# }
```

## Reset Behavior

- **Auto-reset:** `route_task()` calls `metrics.reset()` at start → only current run's phases tracked
- **Manual reset:** Call `metrics.reset()` between runs
- **Prometheus registry:** Counters/histograms accumulate across resets (that's how Prometheus works)

## Testing

All 13 unit tests in `tests/test_metrics.py` pass:

```bash
uv run pytest operator/task_analysis/tests/test_metrics.py -v
```

Tests cover:
- No-op mode fallback
- Phase timing + outcomes
- Confidence clamping
- Routing decision labels
- Model selection tracking
- Redundancy ratio calculation
- Cost non-negativity
- Context manager success/failure
- Contract violation recording
- Summary aggregation
- Multi-run reset isolation

## References

- **ADR-0267:** Task Engine architecture (Phase 0–5)
- **ADR-0217:** Big-data carve-out rules
- **Prometheus Client:** https://github.com/prometheus/client_python
- **Phase Contracts:** `operator/task_analysis/contracts.py`
