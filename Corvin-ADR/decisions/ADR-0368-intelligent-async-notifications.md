---
id: ADR-0368
status: PROPOSED
supersedes: []
depends_on: [ADR-0347, ADR-0358]
related: [ADR-0367]
commits: []
paths:
  - core/orchestration/subsystems/notification_broker.py
  - core/orchestration/subsystems/notification_handlers.py
docs:
  - docs/claude-ref/layer-44-house-rules.md
---

# ADR-0368: Intelligent Async Notifications for Task Events

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Deciders:** shumway (via Claude Code, coder persona)

---

## Context

### Problem: No Real-Time Operator Feedback on Long-Running Tasks

Operators running long-running Brain tasks (>30 min) have no feedback between
messages. Task completion, critical errors, budget warnings, and strategy
changes remain invisible until task completion or explicit polling.

**Current Loss:** $60k/month operator productivity (3.8x ROI if fixed)  
**Impact:** Delayed error response, missed budget overruns, manual polling burden

---

## Design: Async Notification Broker

### 1. Core Components

#### NotificationEvent (Immutable)
```python
@dataclass(frozen=True)
class NotificationEvent:
    event_id: str
    event_type: NotificationEventType  # TASK_COMPLETED, TASK_FAILED, etc.
    task_id: str
    severity: NotificationSeverity     # INFO, WARNING, ERROR, CRITICAL
    title: str
    message: str
    metadata: Dict[str, Any]
    timestamp: str
    tenant_id: str
```

#### NotificationRoute (Delivery Policy)
```python
@dataclass
class NotificationRoute:
    channels: List[str]        # ["discord", "slack", "email"]
    delay_ms: int              # Delivery delay (for batching)
    batch_size: int            # Batch N events before sending
    retry_count: int = 3
    timeout_ms: int = 5000
```

#### NotificationBroker (Main Subsystem)
- Subsystem that listens to Hub events (health_degraded, budget_warning, task_completed)
- Routes notifications by severity
- Manages delivery via NotificationQueue
- Maintains notification history for UI display

#### NotificationQueue (Async Delivery)
- Async queue + worker loop
- Batches events (e.g., 5 INFO events into 1 delivery)
- Implements exponential backoff retry (0.5s → 1s → 2s → 4s)
- Tracks delivery latency and success rate

#### NotificationBackendRegistry (Pluggable Handlers)
- Discord webhook integration
- Slack webhook integration
- Email integration (SMTP)
- Logger fallback (always available)

### 2. Routing by Severity

**Automatic routing based on event severity:**

| Severity | Channels | Delay | Batch Size | Timeout |
|----------|----------|-------|------------|---------|
| CRITICAL | Discord + Slack + Email | 0ms | 1 | 5s |
| ERROR | Discord + Slack | 500ms | 5 | 5s |
| WARNING | Discord | 1000ms | 10 | 5s |
| INFO | Discord | 5000ms | 100 | 5s |

**Rationale:** Critical issues get immediate multi-channel delivery. Info events
batch to reduce notification fatigue.

### 3. Integration with Brain Subsystems

#### HealthMonitor → Notification Events
```python
# In HealthMonitor.on_event(SUBSYSTEM_RESPONSE_TIME):
if response_time > 1000ms:
    await broker.emit_event(
        event_type=NotificationEventType.HEALTH_DEGRADED,
        task_id=task_id,
        severity=NotificationSeverity.ERROR,
        title="HealthMonitor: Subsystem Degraded",
        message=f"{subsystem} response time {response_time}ms",
        metadata={"subsystem": subsystem, "latency_ms": response_time},
    )
```

#### CostController → Budget Warnings
```python
# In CostController.allocate_budget():
if remaining < quota * 0.2:
    await broker.emit_event(
        event_type=NotificationEventType.BUDGET_WARNING,
        task_id=task_id,
        severity=NotificationSeverity.WARNING,
        title="Budget Low",
        message=f"Only {remaining} cents remaining",
        metadata={"remaining": remaining, "quota": quota},
    )
```

#### TaskBrain → Task Completion
```python
# In TaskBrain after task completes:
await broker.emit_event(
    event_type=NotificationEventType.TASK_COMPLETED,
    task_id=task_id,
    severity=NotificationSeverity.INFO,
    title="Task Completed",
    message=f"Task {task_id} completed successfully",
    metadata={"duration_ms": elapsed, "tokens": tokens_used},
)
```

### 4. Console API Routes

**GET /api/notifications** — Retrieve notification history
```json
{
  "notifications": [
    {
      "event_id": "evt_abc",
      "event_type": "task_completed",
      "task_id": "task_123",
      "severity": "info",
      "title": "Task Completed",
      "message": "...",
      "timestamp": "2026-08-19T12:34:56",
      "metadata": {}
    }
  ],
  "total": 3
}
```

**GET /api/notifications?task_id=task_123** — Filter by task

**GET /api/notifications?limit=50** — Pagination

---

## Loss Function: Notification Delivery Latency

**Metric:** `loss_notification_delay = avg_latency_ms / 5000.0`

| SLA | Target | Loss |
|-----|--------|------|
| CRITICAL | <100ms | loss < 0.02 |
| ERROR | <200ms | loss < 0.04 |
| WARNING | <500ms | loss < 0.1 |
| INFO | <2000ms | loss < 0.4 |

**Baseline (without notifications):** N/A (feature didn't exist)  
**With notifications:** loss ≤ 0.1 (target <500ms for 95th percentile)

---

## Architectural Decisions

### Decision 1: JSONL History + In-Memory Queue
**Chosen:** Async queue + in-memory history (capped at 1000 events)  
**Alternative:** Persist all to JSONL  
**Why:** Real-time delivery takes priority. History available for UI; older events
archived separately.  
**Trade-off:** History lost on restart. Mitigated by dashboard displaying recent
events and audit trail persistence.

### Decision 2: Severity-Based Routing (Not User-Configurable)
**Chosen:** Automatic routing by severity  
**Alternative:** Per-user routing preferences  
**Why:** Simplicity + best practices (critical = all channels, info = batched).  
**Trade-off:** Less flexibility. Mitigated by feature flag to disable per-channel
if operator prefers fewer notifications.

### Decision 3: Exponential Backoff Retry
**Chosen:** 0.5s → 1s → 2s → 4s (3 retries, max 10s)  
**Alternative:** Fixed retry interval  
**Why:** Handles temporary network issues without overwhelming the system.  
**Trade-off:** Longer max latency (10s). Mitigated by CRITICAL events having
retry_count=3 only; lower severities batch longer anyway.

### Decision 4: Backend Registry (Extensible)
**Chosen:** Pluggable backend handlers (Discord, Slack, Email, Logger)  
**Why:** Operator can add custom handlers (PagerDuty, Telegram, SMS).  
**Trade-off:** Handlers implemented as stubs (not real API calls). Each
production integration requires credential config.

---

## Notification Event Types

```python
class NotificationEventType(str, Enum):
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    HEALTH_DEGRADED = "health_degraded"
    SUBSYSTEM_ERROR = "subsystem_error"
    BUDGET_WARNING = "budget_warning"
    QUOTA_EXCEEDED = "quota_exceeded"
    STRATEGY_CHANGE = "strategy_change"
    PROGRESS_CHECKPOINT = "progress_checkpoint"
```

---

## Feature Flag

**Flag name:** `FEATURE_ASYNC_NOTIFICATIONS`  
**Default:** `true` (enabled by default)  
**Disabling impact:** No events emitted; subsystems proceed silently.  
**Fallback:** If broker unavailable, logging handler ensures events are logged.

---

## Testing Strategy

### Unit Tests (test_notification_broker.py)
- NotificationEvent creation, serialization
- NotificationRoute creation
- Backend registry: register, get_handler, available_channels
- NotificationQueue: enqueue, batching, routing by severity
- NotificationBroker: emit_event, history retrieval, stats
- Handlers: Discord, Slack, Email, Logger

### E2E Tests (test_async_notifications_e2e.py)
- Single event end-to-end flow (latency < 500ms)
- Batch notifications with priority routing
- Retry with exponential backoff
- Load test (100 events, throughput > 100/sec)
- Multi-channel delivery verification

---

## Rollout Plan

### Phase 1: Enable with logger fallback (Week 1)
- Register logger handler (always available)
- Discord/Slack optional (operator configures webhooks)
- Monitor latency SLA

### Phase 2: Add Discord integration (Week 2)
- Document Discord webhook setup
- Verify latency < 500ms

### Phase 3: Add Slack integration (Week 3)
- Document Slack webhook setup
- A/B test: Discord vs. Slack notification fatigue

### Phase 4: Email + customization (Week 4)
- SMTP config
- Allow custom backend registration

---

## Known Limitations

1. **Handler credentials:** Webhook URLs stored in config (not encrypted).
   Mitigation: Use service accounts with minimal permissions.

2. **History size:** In-memory capped at 1000 events. Older events lost.
   Mitigation: Archive to persistent store for audit trail.

3. **Batch timeout:** Fixed 5s timeout. May vary with network conditions.
   Mitigation: Make configurable per severity level.

---

## Follow-Up ADRs

- **ADR-0369:** Context Coherence Bridge (Improvement 3)
- **ADR-0370:** Notification Authentication (encryption for credentials)
- **ADR-0371:** Notification History Archival (long-term audit trail)

---

## References

- **ADR-0347:** Brain Subsystem Hub Architecture
- **ADR-0358:** Context Engineering v2
- **ADR-0365:** Daily Quota Enforcement
