# Diagnostics Dashboard

## Overview

Unified health dashboard aggregating metrics from Autonomy Status Tracker (21), Brain Diagnostics (22), and Brain Layer Monitor (23). Provides real-time visualization of session health, subsystem performance, and layer-level metrics in a single Web UI.

**Why it matters:**
- Central visibility into all observability data
- Detects system degradation at a glance
- Enables rapid root-cause analysis
- Supports incident response with historical data

## Architecture

Dashboard aggregates three plugin inputs:
- Autonomy Status → Session health scores, state transitions
- Brain Diagnostics → Subsystem health, anomalies
- Brain Layer Monitor → Per-layer latency, compliance checkpoints

## Usage

Access via `http://localhost:8765/console/diagnostics` (requires `/diagnostics` feature flag).

Dashboard sections:
1. **System Overview:** Overall health score (0-100)
2. **Session Health:** Active sessions, health distribution
3. **Brain Subsystems:** 13 subsystem health cards
4. **Layers:** 36 layers grouped by performance/compliance
5. **Anomalies:** Active anomalies with impact assessment
6. **Incident History:** Last 24h of critical events

## Performance Metrics

| Metric | Target |
|--------|--------|
| Dashboard Load | <500ms |
| Real-time Updates | 2-5 sec refresh |
| Historical Query | <1 sec (last 7 days) |

## Testing

```bash
cd /home/shumway/projects/CorvinOS/buildin/observability/diagnostics_dashboard
pytest tests/e2e_diagnostics_dashboard.py -v
```

## Compliance

**GDPR Art. 30:** Dashboard access logged  
**GDPR Art. 32:** Only shows aggregated, pseudonymized metrics

## Related Plugins

- **Autonomy Status Tracker (21):** Data source
- **Brain Diagnostics (22):** Data source
- **Brain Layer Monitor (23):** Data source

## ADR Reference

See ADR-0524 for architectural decisions.

## Metadata

- **Version:** 1.0.0
- **License:** Apache-2.0
- **Maintainer:** CorvinOS Core Team
- **Boot Layer:** bundled
- **Tier:** buildin
- **Category:** observability/dashboard
