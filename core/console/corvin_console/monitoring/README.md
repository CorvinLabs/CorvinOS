# CorvinOS Console Monitoring System

Comprehensive monitoring and alerting for CorvinOS production deployments.

## Overview

The monitoring system has 4 integrated components:

1. **Sentry Integration** - Error tracking and performance monitoring
2. **Prometheus Metrics** - Real-time metrics collection (routing, SLO, learning)
3. **Grafana Dashboards** - Pre-built dashboards for visualization
4. **Alert Rules** - Automated alerting for SLO violations and critical events

## Quick Start

### 1. Install Dependencies

```bash
pip install sentry-sdk prometheus-client pyyaml
```

### 2. Configure Environment

Create `.env` file (see `env.example`):

```bash
# Sentry error tracking
SENTRY_DSN=https://your-key@sentry.io/project-id

# Prometheus scrape endpoint (exposes /metrics)
PROMETHEUS_SCRAPE_PORT=9090

# Alertmanager webhook for PagerDuty/Slack
PAGERDUTY_SERVICE_KEY=your-pagerduty-key
SLACK_WEBHOOK_URL_MARKETPLACE_SLO=https://hooks.slack.com/...
SLACK_WEBHOOK_URL_AUDIT_CHAIN=https://hooks.slack.com/...
```

### 3. Initialize at App Startup

```python
from fastapi import FastAPI
from corvin_console.monitoring import (
    initialize_sentry,
    initialize_metrics,
    create_metrics_router,
    add_sentry_middleware,
)

app = FastAPI()

# Initialize monitoring at startup
initialize_sentry()
initialize_metrics()
add_sentry_middleware(app)

# Expose /metrics endpoint for Prometheus scraping
metrics_router = create_metrics_router()
app.include_router(metrics_router)
```

### 4. Import Grafana Dashboards

```bash
# Export dashboards
python -m corvin_console.monitoring.grafana_dashboards

# Import into Grafana:
# 1. Open Grafana: http://localhost:3000
# 2. Settings → Import
# 3. Upload grafana_dashboards.json
# 4. Select Prometheus data source
```

### 5. Configure Prometheus Scraping

Add to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'corvinos-console'
    static_configs:
      - targets: ['localhost:8765']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

### 6. Install Alert Rules

```bash
cp prometheus_alerts.yaml /etc/prometheus/rules/

# Add to prometheus.yml:
rule_files:
  - /etc/prometheus/rules/prometheus_alerts.yaml
```

### 7. Configure Alertmanager

See alert routing in `prometheus_alerts.yaml` for example `alertmanager.yml` configuration.

---

## Component Details

### Component 1: Sentry Integration

**File**: `sentry_config.py`

Tracks:
- Routing decisions (model, confidence, cost)
- Model API failures (fallback logic)
- Token estimation errors
- Budget exceeded scenarios
- Audit chain failures (CRITICAL)

**Usage**:

```python
from corvin_console.monitoring import SentryBreadcrumbManager

# Record routing decision
SentryBreadcrumbManager.routing_decision(
    model_selected="claude-opus-5",
    complexity_tier="complex",
    confidence=0.95,
    cost_estimate=0.0045,
    latency_estimate_ms=2500,
    reasoning="Task complexity > 250 tokens",
)

# Record model API failure
SentryBreadcrumbManager.model_api_failure(
    model="claude-opus-5",
    error="Rate limit exceeded",
    fallback_model="claude-sonnet-5",
    retry_count=1,
)

# Record budget exceeded (CRITICAL)
SentryBreadcrumbManager.budget_exceeded(
    estimated_cost=45.0,
    daily_budget=50.0,
    percentage_used=90.0,
    tenant_id="default",
)

# Record audit chain failure (CRITICAL)
SentryBreadcrumbManager.audit_chain_failure(
    error_type="hash_mismatch",
    message="Event hash does not match chain",
    chain_height=12345,
    last_hash="sha256_...",
)
```

**Sentry Dashboard**: https://sentry.io/organizations/your-org/issues/

---

### Component 2: Prometheus Metrics

**File**: `metrics.py`

Exposes 8 core metrics:

1. `routing_decisions_total` - Counter of routing decisions
2. `routing_confidence_score` - Gauge of confidence scores
3. `cost_total` - Counter of cost by model
4. `token_estimation_error` - Histogram of token accuracy
5. `model_latency_seconds` - Histogram of model latency
6. `audit_events_logged_total` - Counter of audit events
7. `circuit_breaker_state` - Gauge of circuit breaker (0=OPEN, 1=CLOSED)
8. `learning_events_received_total` - Counter of learning events

**Usage**:

```python
from corvin_console.monitoring import get_metrics

metrics = get_metrics()

# Record routing decision
metrics.record_routing_decision(
    model_selected="claude-opus-5",
    complexity_tier="complex",
    cost_estimate=0.0045,
    confidence=0.95,
    tenant_id="default",
)

# Record token estimation accuracy
metrics.record_token_estimation(
    estimated_tokens=250,
    actual_tokens=240,
    task_type="general",
)

# Record model latency
metrics.record_model_latency(
    model_selected="claude-opus-5",
    latency_seconds=2.5,
)

# Set circuit breaker state
metrics.set_circuit_breaker_state(
    endpoint="marketplace",
    is_open=True,  # True=healthy, False=triggered
)

# Record learning event
metrics.record_learning_event(
    event_type="outcome_feedback",
    skill_id="os.delegation_router",
)
```

**Prometheus Endpoint**: http://localhost:8765/metrics

---

### Component 3: Grafana Dashboards

**File**: `grafana_dashboards.py`

3 pre-built dashboards:

#### Dashboard 1: Model Routing Overview
- Routing distribution (pie/donut)
- Cost per model (stacked bar)
- Confidence scores (line chart, trending up)
- Token estimation accuracy (scatter/heatmap)
- Stat tiles: tasks routed, cost, confidence, SLO status

#### Dashboard 2: SLO Monitoring
- P99 latency (line, threshold 500ms)
- Error rate % (line, threshold 0.1%)
- Circuit breaker status (gauge)
- SLO compliance % (stat)
- Request rate by status

#### Dashboard 3: Learning Loop Health
- Feedback signals (bars: outcome/preference/confidence/metric)
- Config updates per skill (line)
- Total feedback events (stat)
- Active skills (stat)
- Feedback distribution (pie)

**Import Dashboards**:
```bash
python core/console/corvin_console/monitoring/grafana_dashboards.py
# Outputs: /tmp/grafana_dashboards.json
```

Then import in Grafana: Settings → Import → Upload JSON

---

### Component 4: Alert Rules

**File**: `prometheus_alerts.yaml`

10 production-ready alerts:

1. **SLO Latency Violation** (CRITICAL) - P99 > 500ms for 5m
2. **SLO Error Rate Violation** (CRITICAL) - Error rate > 0.1% for 5m
3. **Circuit Breaker Triggered** (WARNING) - Marketplace circuit breaker open 2m+
4. **ReDoS Detected** (CRITICAL) - Regex DoS attack (should be 0)
5. **Audit Chain Failure** (CRITICAL) - Hash chain verification failed (GDPR)
6. **Budget Exceeded** (HIGH) - Daily cost > $50
7. **Model API Errors - Opus** (WARNING) - Opus errors > 5 in 5m
8. **Model API Errors - Sonnet** (WARNING) - Sonnet errors > 5 in 5m
9. **Model API Errors - Haiku** (CRITICAL) - Haiku errors (last fallback)
10. **Learning Convergence Stalled** (INFO) - Learning not improving

**Alert Routing** (configure in `alertmanager.yml`):
- CRITICAL → PagerDuty (page oncall)
- CRITICAL + audit_chain → Security team (immediate)
- WARNING → Slack #ops-alerts
- INFO → Slack #ml-optimization

---

## Integration with Intelligent Router

The monitoring system integrates with the intelligent router to track every decision:

```python
# In intelligent_router.py
from corvin_console.monitoring import get_metrics, SentryBreadcrumbManager

class IntelligentRouter:
    def route_request(self, task: Task) -> RoutingDecision:
        # ... routing logic ...
        
        # Record decision in metrics
        metrics = get_metrics()
        metrics.record_routing_decision(
            model_selected=decision.model,
            complexity_tier=decision.tier,
            cost_estimate=decision.cost_estimate,
            confidence=decision.confidence,
            tenant_id=task.tenant_id,
        )
        
        # Record breadcrumb for context
        SentryBreadcrumbManager.routing_decision(
            model_selected=decision.model,
            complexity_tier=decision.tier,
            confidence=decision.confidence,
            cost_estimate=decision.cost_estimate,
            latency_estimate_ms=decision.latency_estimate_ms,
            reasoning=decision.reasoning,
        )
        
        return decision
```

---

## Monitoring Checklist

- [ ] Sentry DSN configured in `.env`
- [ ] `/metrics` endpoint exposed and reachable
- [ ] Prometheus scraping `/metrics` every 15s
- [ ] Grafana dashboards imported and visible
- [ ] Alert rules loaded in Prometheus
- [ ] Alertmanager configured with PagerDuty/Slack webhooks
- [ ] Metrics appearing in dashboards (check within 1m)
- [ ] Sample alert tested (manually trigger threshold)
- [ ] On-call routing verified (page sent to correct team)
- [ ] Dashboard refresh rates reasonable (10s-30s)

---

## Troubleshooting

### Metrics not appearing in dashboard

```bash
# Check Prometheus scraping
curl http://localhost:9090/api/v1/targets

# Check metrics endpoint
curl http://localhost:8765/metrics | head -20

# Check metric names match dashboard queries
curl http://localhost:8765/metrics | grep routing_decisions_total
```

### Alerts not firing

```bash
# Check alert rules loaded
curl http://localhost:9090/api/v1/rules | jq '.data.groups[0].rules'

# Test alert expression manually
curl http://localhost:9090/api/v1/query?query=corvinos_console_slo_latency_p99_ms

# Check Prometheus rule evaluation
curl http://localhost:9090/-/reload  # Reload rules
```

### Sentry events not appearing

```bash
# Check DSN is valid
echo $SENTRY_DSN

# Trigger test event
python -c "
from corvin_console.monitoring import initialize_sentry, SentryBreadcrumbManager
initialize_sentry()
SentryBreadcrumbManager.routing_decision(
    model_selected='test',
    complexity_tier='test',
    confidence=1.0,
    cost_estimate=0.001,
    latency_estimate_ms=100,
    reasoning='test'
)
"

# Check Sentry dashboard
# https://sentry.io/organizations/YOUR-ORG/issues/
```

---

## Performance Impact

| Component | Overhead | Notes |
|-----------|----------|-------|
| Sentry breadcrumbs | <1ms | Async queue, non-blocking |
| Metrics recording | <0.5ms | In-process, no I/O |
| Prometheus scraping | <100ms | Every 15s, not on request path |
| Dashboard load | ~2s | Queries run at dashboard open |

All components are designed for **zero impact on request latency**.

---

## References

- [Sentry Documentation](https://docs.sentry.io/platforms/python/integrations/fastapi/)
- [Prometheus Metrics](https://prometheus.io/docs/concepts/data_model/)
- [Grafana Dashboards](https://grafana.com/docs/grafana/latest/dashboards/)
- [Alertmanager Configuration](https://prometheus.io/docs/alerting/latest/configuration/)
- [CorvinOS ADR-0892: Marketplace SLO Monitoring](../../../../../../corvin_decisions/decisions/ADR-0892-marketplace-slo-monitoring.md)

---

## Support

For issues or questions:
1. Check Sentry dashboard for error patterns
2. Review Prometheus metrics and alert rules
3. Check Grafana dashboard data freshness
4. Verify environment variables configured
5. Review `/metrics` endpoint output

Last updated: 2026-09-20
