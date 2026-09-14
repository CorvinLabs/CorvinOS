# Monitoring Metrics Dashboard

## Overview
Real-time metrics for CorvinOS performance via OTEL Telemetry.

## Key Metrics

### Skill Execution
- **Skill Execution Count** — Number of skill runs
- **Skill Latency (p50/p99)** — Execution time percentiles
- **Skill Error Rate** — % of failed executions

### System Health
- **API Response Latency** — Console API response time
- **Cache Hit Rate** — Learning cache efficiency
- **Audit Chain Integrity** — Hash chain verification status

### Learning Loop
- **Confidence Mean** — Average skill confidence
- **Convergence Detection** — Skills that reached 0.80+
- **Feedback Latency** — Time from outcome to operator feedback

## Alerts

Configured alert rules:
- Error rate > 5% → Page oncall
- Latency p99 > 500ms → Warning
- Convergence stalled (>24h) → Investigation

## Dashboard Access
Console → OTEL Telemetry → View all metrics

## Grafana Integration
Public dashboards at: https://grafana.internal/d/corvinOS
