# Autonomy Status Tracker

## Overview

Autonomy Status Tracker monitors the lifecycle, hardening state, and recovery metrics of autonomous sessions. It observes task progression, system state transitions, resilience against failures, and provides comprehensive diagnostics for session health and autonomy levels.

**Why it matters:**
- Tracks autonomous execution state across all sessions
- Detects degradation in self-management capabilities
- Validates hardening measures are active
- Enables recovery orchestration on anomalies

## Architecture

```
┌─────────────────────────────────────────────────────┐
│       Autonomy Status Tracker                       │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────────────┐        ┌──────────────────┐  │
│  │ Session Tracker │───────▶│ State Manager    │  │
│  │                 │        │                  │  │
│  │ • Lifecycle     │        │ • Current State  │  │
│  │ • Progress      │        │ • Transitions    │  │
│  └─────────────────┘        └──────────────────┘  │
│          │                                         │
│          ▼                                         │
│  ┌─────────────────┐        ┌──────────────────┐  │
│  │ Hardening Audit │───────▶│ Recovery Manager │  │
│  │                 │        │                  │  │
│  │ • Guards        │        │ • Failure Detect │  │
│  │ • Constraints   │        │ • Auto-Recovery  │  │
│  └─────────────────┘        └──────────────────┘  │
│          │                                         │
│          └────────┬────────────────────┬──────────┘
│                   │                    │
│          ┌────────▼────────┐  ┌────────▼──────┐
│          │ Metrics Store   │  │ Audit Trail   │
│          │ (in-memory +    │  │ (hash-chained)│
│          │  persistent)    │  │               │
│          └─────────────────┘  └───────────────┘
│                                                    │
└─────────────────────────────────────────────────────┘
```

**Event Flow:**
1. Session registers with tracker at boot
2. Tracker observes lifecycle transitions (INIT → ACTIVE → HARDENED → RECOVERING → COMPLETE)
3. Hardening audit runs at each state
4. Recovery manager triggers on degradation detection
5. Diagnostics aggregated and exposed via metrics

## Usage

### Initialize the Tracker

```python
from buildin.observability.autonomy_status_tracker import AutonomyStatusTracker
from buildin.observability.autonomy_status_tracker.events import AutonomyEvent

# Create tracker instance (singleton per tenant)
tracker = AutonomyStatusTracker(tenant_id="default")
await tracker.initialize()

# Register a session
session_info = {
    "session_id": "sess_abc123",
    "user_id": "user_xyz",
    "started_at": datetime.now(timezone.utc),
    "autonomy_level": "FULL"
}
await tracker.register_session(session_info)
```

### Handle Lifecycle Events

```python
# Signal state transition
transition_event = AutonomyEvent(
    event_type="state_transition",
    session_id="sess_abc123",
    data={
        "from_state": "INIT",
        "to_state": "ACTIVE",
        "reason": "bootstrap_complete",
        "timestamp": datetime.now(timezone.utc)
    }
)
await tracker.emit_event(transition_event)

# Signal hardening checkpoint
hardening_event = AutonomyEvent(
    event_type="hardening_checkpoint",
    session_id="sess_abc123",
    data={
        "checkpoint": "constraint_guards_active",
        "passed": True,
        "details": {
            "path_gate": "locked",
            "consent_model": "verified",
            "audit_chain": "intact"
        }
    }
)
await tracker.emit_event(hardening_event)

# Signal recovery attempt
recovery_event = AutonomyEvent(
    event_type="recovery_attempt",
    session_id="sess_abc123",
    data={
        "failure_detected": "context_loss",
        "recovery_strategy": "context_restore_from_checkpoint",
        "success": True,
        "recovery_time_ms": 245
    }
)
await tracker.emit_event(recovery_event)
```

### Get Diagnostics

```python
# Retrieve current session status
session_status = await tracker.get_session_status("sess_abc123")
print(f"State: {session_status['current_state']}")
print(f"Autonomy Level: {session_status['autonomy_level']}")
print(f"Health Score: {session_status['health_score']}/100")

# Retrieve full diagnostics
diagnostics = await tracker.get_diagnostics("sess_abc123")
print(f"Total Events: {diagnostics['total_events']}")
print(f"State Transitions: {diagnostics['state_transitions']}")
print(f"Hardening Checkpoints: {diagnostics['hardening_checkpoints']}")
print(f"Recovery Attempts: {diagnostics['recovery_attempts']}")
print(f"Last Updated: {diagnostics['last_updated']}")

# Get aggregate metrics
aggregate = await tracker.get_aggregate_metrics()
print(f"Active Sessions: {aggregate['active_sessions']}")
print(f"Mean Health Score: {aggregate['mean_health_score']:.2f}")
print(f"Sessions Recovering: {aggregate['sessions_recovering']}")
```

### Shutdown

```python
# Graceful shutdown
await tracker.shutdown()
```

## Performance Metrics

| Metric | Target | Typical | Notes |
|--------|--------|---------|-------|
| Event Ingestion | <5ms | 1-2ms | Per event, sync path |
| Diagnostics Retrieval | <50ms | 10-15ms | From in-memory store |
| State Transition | <10ms | 3-5ms | Includes audit log write |
| Hardening Audit | <100ms | 40-60ms | Validates 5+ constraints |
| Recovery Detection | <200ms | 80-120ms | Anomaly detection + trigger |
| Daily Persistence | <500ms | 200-300ms | Nightly checkpoint to disk |

**SLA:** 99.9% of operations complete within target latency

## Testing

### Run Unit Tests
```bash
cd /home/shumway/projects/CorvinOS/buildin/observability/autonomy_status_tracker
pytest tests/unit/ -v
```

### Run E2E Tests
```bash
pytest tests/e2e_autonomy_status_tracker.py -v --log-cli-level=INFO
```

### Profiling
```bash
pytest tests/e2e_autonomy_status_tracker.py::test_performance_ingestion -v --profile
```

## Compliance

**GDPR Art. 30 (Records of Processing):**
- Session lifecycle events logged with timestamp, session_id (pseudonymized), state
- No PII in event payloads; user_id indexed separately with consent verification
- Audit trail hash-chained, 90-day retention default

**GDPR Art. 32 (Security):**
- Autonomy state guarded by session ACL
- Recovery events tied to audit record; fail-closed if chain breaks
- Metrics aggregated over pseudonymized session IDs

**EU AI Act Art. 50 (Recordkeeping):**
- State transitions logged as disclosure checkpoints
- Hardening audit results recorded as transparency artifacts
- Recovery actions documented for incident investigation

## Related Plugins

- **Brain Diagnostics (22):** Complements with subsystem-level health
- **Brain Layer Monitor (23):** Provides per-layer performance data
- **Diagnostics Dashboard (24):** Aggregates autonomy metrics into visual dashboard
- **Self Repair Engine (27):** Executes recovery strategies detected by this tracker
- **Heartbeat Monitor (26):** Tracks presence; informs session lifecycle

## ADR Reference

See [ADR-0521](https://github.com/CorvinLabs/Corvin-ADR/decisions/ADR-0521-autonomy-status-tracker.md) for architectural decisions.

## Metadata

- **Version:** 1.0.0
- **License:** Apache-2.0
- **Maintainer:** CorvinOS Core Team
- **Boot Layer:** bundled
- **Tier:** buildin
- **Category:** observability/session-tracking
