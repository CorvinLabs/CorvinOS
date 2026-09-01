# Brain Diagnostics

## Overview

Brain Diagnostics monitors the health and performance of Brain subsystems, aggregating metrics from all 13 subsystems (ExecutionContext, ContextBus, VoiceCoordinator, TaskManager, etc.). It provides hierarchical diagnostics: system-level, subsystem-level, and component-level views.

**Why it matters:**
- Detects subsystem degradation in real time
- Enables root-cause analysis across 13+ components
- Validates subsystem inter-dependencies
- Provides data for the unified diagnostics dashboard

## Architecture

```
┌──────────────────────────────────────────────────┐
│       Brain Diagnostics Aggregator               │
├──────────────────────────────────────────────────┤
│                                                  │
│  ┌──────────────────┐   ┌────────────────────┐ │
│  │ Subsystem Probes │   │ Health Evaluator   │ │
│  │                  │   │                    │ │
│  │ • ExecutionCtx   │───▶│ • Scoring         │ │
│  │ • ContextBus     │   │ • Thresholding    │ │
│  │ • VoiceCoord.    │   │ • Anomaly Detect  │ │
│  │ • TaskManager    │   └────────────────────┘ │
│  │ • 9+ more        │           │              │
│  └──────────────────┘           ▼              │
│           │         ┌─────────────────────┐    │
│           │         │ Aggregate Metrics   │    │
│           │         │ (hierarchical)      │    │
│           │         └──────────┬──────────┘    │
│           │                    │               │
│           └────────────────────┼───────────────┘
│                                │
│                    ┌───────────▼───────────┐
│                    │ Audit Trail (GDPR)    │
│                    │ Hash-chained events   │
│                    └───────────────────────┘
│
```

**Subsystems Monitored (13 total):**
1. ExecutionContext — State tracking
2. ContextBus — Event routing
3. VoiceCoordinator — Speech management
4. TaskManager — Task orchestration
5. PluginSystem — Plugin lifecycle
6. AuditWriter — Compliance logging
7. ContextPipeline — Context preservation
8. SessionManager — Session tracking
9. LearningSystem — Phase 3.1 integration
10. SecurityPipeline — Auth + validation
11. HealingTraces — Error recovery
12. TelemetryClient — Metrics emission
13. ComplianceReporter — Regulatory reporting

## Usage

### Initialize Diagnostics

```python
from buildin.observability.brain_diagnostics import BrainDiagnostics
from buildin.observability.brain_diagnostics.events import DiagnosticEvent

# Create diagnostics instance (singleton per tenant)
diagnostics = BrainDiagnostics(tenant_id="default")
await diagnostics.initialize()

# Register subsystems
subsystems = [
    "execution_context",
    "context_bus",
    "voice_coordinator",
    "task_manager",
    # ... register all 13
]
for subsystem_name in subsystems:
    await diagnostics.register_subsystem(subsystem_name)
```

### Emit Subsystem Metrics

```python
# From ExecutionContext subsystem
ctx_event = DiagnosticEvent(
    subsystem="execution_context",
    event_type="metrics_update",
    data={
        "context_switches_per_sec": 42.5,
        "avg_context_size_kb": 256,
        "memory_usage_percent": 45.2,
        "last_gc_duration_ms": 12.5,
        "gc_count_total": 1247,
        "errors_last_5min": 0
    }
)
await diagnostics.emit_metric(ctx_event)

# From ContextBus
bus_event = DiagnosticEvent(
    subsystem="context_bus",
    event_type="metrics_update",
    data={
        "messages_per_sec": 523.1,
        "queue_length": 12,
        "avg_latency_ms": 2.3,
        "error_rate": 0.001,
        "subscriber_count": 8
    }
)
await diagnostics.emit_metric(bus_event)
```

### Get Hierarchical Diagnostics

```python
# System-level health (0-100)
system_health = await diagnostics.get_system_health()
print(f"Brain Health: {system_health['overall_score']}/100")
print(f"Status: {system_health['status']}")  # HEALTHY/DEGRADED/CRITICAL

# Subsystem-level breakdown
subsystem_health = await diagnostics.get_subsystem_health()
for subsystem_name, health in subsystem_health.items():
    print(f"{subsystem_name}: {health['score']}/100")

# Component-level deep dive
exec_ctx_details = await diagnostics.get_subsystem_details("execution_context")
print(f"Context Switches/sec: {exec_ctx_details['context_switches_per_sec']}")
print(f"Memory Usage: {exec_ctx_details['memory_usage_percent']}%")

# Check for anomalies
anomalies = await diagnostics.get_anomalies()
for anomaly in anomalies:
    print(f"⚠️  {anomaly['subsystem']}: {anomaly['description']}")
```

### Query Interdependencies

```python
# Which subsystems depend on ExecutionContext?
dependents = await diagnostics.get_subsystem_dependents("execution_context")
print(f"Subsystems depending on ExecutionContext: {dependents}")

# Will degradation of ContextBus affect TaskManager?
impact = await diagnostics.get_degradation_impact("context_bus", "task_manager")
print(f"Impact score: {impact}")  # 0-1, where 1 is critical

# Get dependency graph
graph = await diagnostics.get_dependency_graph()
for subsystem, deps in graph.items():
    print(f"{subsystem} → {deps}")
```

### Shutdown

```python
await diagnostics.shutdown()
```

## Performance Metrics

| Metric | Target | Typical | Notes |
|--------|--------|---------|-------|
| Metric Ingestion | <2ms | 0.5-1ms | Per metric, sync path |
| System Health Calculation | <50ms | 15-25ms | Aggregates 13 subsystems |
| Subsystem Details | <20ms | 5-10ms | Per subsystem query |
| Anomaly Detection | <100ms | 30-50ms | Scan all subsystem data |
| Dependency Impact | <30ms | 5-15ms | Traverse dependency graph |
| Daily Persistence | <500ms | 150-250ms | Nightly checkpoint to disk |

**SLA:** 99.9% of operations complete within target latency

## Testing

### Run Unit Tests
```bash
cd /home/shumway/projects/CorvinOS/buildin/observability/brain_diagnostics
pytest tests/unit/ -v
```

### Run E2E Tests
```bash
pytest tests/e2e_brain_diagnostics.py -v --log-cli-level=INFO
```

### Performance Profiling
```bash
pytest tests/e2e_brain_diagnostics.py::test_performance_metric_ingestion -v --profile
```

## Compliance

**GDPR Art. 30 (Records of Processing):**
- Subsystem metrics logged with timestamp, subsystem_id
- No PII in metric payloads; only pseudonymized subsystem identifiers
- Audit trail hash-chained, 90-day retention default

**GDPR Art. 32 (Security):**
- Metrics queried through ACL-gated interface
- Dependency graph immutable after boot
- Daily hash-chain verification ensures integrity

**EU AI Act Art. 50 (Recordkeeping):**
- Subsystem state transitions logged as transparency artifacts
- Anomalies documented with root-cause details
- Performance degradation correlated with user impact

## Related Plugins

- **Autonomy Status Tracker (21):** Provides session-level context for subsystem diagnostics
- **Brain Layer Monitor (23):** Extends with per-layer performance metrics
- **Diagnostics Dashboard (24):** Visualizes brain subsystem health
- **Error Healing (25):** Uses diagnostics to guide recovery strategies
- **Vibe Health Monitor (30):** Aggregates brain + session metrics

## ADR Reference

See [ADR-0522](https://github.com/CorvinLabs/Corvin-ADR/decisions/ADR-0522-brain-diagnostics.md) for architectural decisions.

## Metadata

- **Version:** 1.0.0
- **License:** Apache-2.0
- **Maintainer:** CorvinOS Core Team
- **Boot Layer:** bundled
- **Tier:** buildin
- **Category:** observability/system-health
