# Learning Dashboard — GDPR Compliance (ADR-0321)

**Effective:** 2026-09-02  
**Scope:** `core/learning/dashboard.py`, `routes/learning_dashboard.py`  
**Relevant Regulations:** GDPR Art. 5, 6, 12, 21, 30, 32; EU AI Act Art. 50, 5

## 1. Data Processing Basis (GDPR Art. 6)

### Legal Basis: Legitimate Interest (Art. 6(1)(f))

The Learning Dashboard processes aggregated learning metrics for:
- **System Health:** Monitor skill performance, identify bottlenecks
- **Quality Assurance:** Detect learning convergence issues, confidence collapse
- **Transparency:** Enable users to understand how their data influences model decisions

**Balancing Test (Art. 6(1)(f) transparency requirement):**
- Users' reasonable expectations: HIGH (dashboard opt-in via WebSocket subscription)
- Impact on users: LOW (aggregated metrics only, no individual profiling)
- Necessity: HIGH (operability requires observability)
- **Conclusion:** Legitimate interest overrides user privacy interests

## 2. Data Minimization (GDPR Art. 5(1)(c))

### What is Processed

**Allowed:**
- Aggregated accuracy rates (%, not individual predictions)
- Latency statistics (mean/p50/p95/p99, not per-request traces)
- Confidence scores (aggregate, not per-user breakdown)
- User satisfaction ratings (1–5 scale average)
- Event counts + timestamps

**NOT Processed:**
- User prompts, queries, or conversation history
- Model inputs/outputs
- Personal identifiers beyond user_id + tenant_id
- Skill names (used only for aggregation grouping)

### Data Retention

- **Metrics:** 90 days (deleted per ADR-0319)
- **Audit Trail:** 1 year (per GDPR Art. 30 record-keeping)
- **User Subscriptions:** Active only during WebSocket session + prune after 5min inactivity

## 3. Transparency & User Rights (GDPR Art. 12, 21)

### Transparency: Users Can See Their Data

**Dashboard Endpoint: `/api/learning/user/{user_id}`**

Returns:
```json
{
  "user_id": "user_123",
  "satisfaction_avg": 4.2,
  "engagement_score": 0.78,
  "query_count": 456,
  "last_query": "2026-09-02T10:15:30Z"
}
```

**No surprises:** User can inspect exactly what metrics the system tracks.

### Right to Object (GDPR Art. 21)

Users can opt out of dashboard data processing:
- **Mechanism:** Consent gate (L16) filters learning events before metrics aggregation
- **If opted out:** Dashboard metrics exclude this user's events
- **Implementation:** `EventStore.query_events()` filters by tenant_id + consent_granted = True

### Right to Erasure (GDPR Art. 17)

When a user requests deletion (ADR-0536):
1. EventStore removes all learning events for this user_id
2. Dashboard queries re-aggregate (old metrics expire from cache in 5s)
3. User subscription broadcasts updates to all active subscribers
4. Audit event `user_erased` logged (hash-chained, immutable)

## 4. Integrity & Authenticity (GDPR Art. 32)

### Hash-Chained Audit Trail (ADR-0232/0233)

Every dashboard query is audited:

```python
{
  "event_id": "uuid-4",
  "event_type": "dashboard_query_executed",
  "query_type": "summary" | "skill_stats" | "user_stats",
  "filters": { "skill_name": "skill_1" },
  "tenant_id": "_default",
  "timestamp": "2026-09-02T12:34:56.789Z",
  "lom": "core.learning.dashboard.LearningDashboard.get_summary_stats",
  "hash": "sha256(...)",
  "prev_hash": "sha256(...)"
}
```

**Guarantees:**
- Immutable: Events cannot be modified post-write
- Chained: Each event references previous (tamper detection via hash mismatch)
- Atomic: Write fails completely or succeeds completely (no partial records)
- Fail-Closed: If audit backend unavailable, dashboard queries log warning but continue
  - Trade-off: Observability > audit completeness (audit missing is better than dashboard down)

### Caching Safety

Dashboard uses 5-second TTL cache to avoid EventStore hammering:
- Cache key includes tenant_id (prevents cross-tenant leakage)
- Expired entries automatically purged
- Cache is in-memory (not persisted; survives process restart)

**Safety:** No PII in cache (only aggregated metrics).

## 5. Tenant Isolation (GDPR Art. 32 - Security)

### Multi-Tenant Data Separation

**All queries include tenant_id filter:**

```python
def get_summary_stats(self, tenant_id: str) -> DashboardMetrics:
    # Enforced: only metrics for this tenant
    self.event_store.query_events(tenant_id=tenant_id, ...)
```

**Validation (fail-closed):**
```python
def _validate_tenant_id(tenant_id: str) -> None:
    if not re.match(r'^[a-zA-Z0-9_-]+$', tenant_id):
        raise ValueError(f"Invalid tenant_id: {tenant_id}")
```

**WebSocket Isolation:**
- Each subscriber has tenant_id
- Broadcasts filter by tenant_id (users only see their own tenant's metrics)
- Admin subscribers (user_scoped=False) still see only their tenant's system metrics

### Access Control

**Endpoint: `/api/learning/user/{user_id}`**
- Users can query their own metrics only (GDPR Art. 21 — right to object)
- Admin users (has "admin" role) can query any user's metrics
- Enforced: `if requesting_user_id != user_id and not _is_admin(): raise HTTPException(403)`

## 6. Consent (L16 Compliance)

### Consent Gate Integration

Learning events flow through consent gate before dashboard ingestion:

```python
EventStore.query_events(tenant_id=tenant_id)
  → filters to only events where consent_granted = True (per LearningEvent.signal)
  → MetricsAggregator.build_dashboard()
```

**User Consent Options:**
- **Opt-In (default-OFF):** User must explicitly enable learning
- **Opt-Out (default-ON):** User is opted in; can disable
- **Read-Only:** User can query metrics but not adjust learning behavior

Configured per user in `~/.corvin/tenants/{tenant_id}/user/{user_id}/learning.yaml`:
```yaml
learning:
  consent_granted: true  # or false to exclude from dashboard metrics
  dashboard_subscriber: false  # or true for WebSocket updates
```

## 7. EU AI Act 2026 Compliance

### Art. 50: Transparency About AI System

**Dashboard satisfies:**
- Users can inspect how their interactions influence system metrics
- No hidden profiling (all metrics visible via API)
- Decision-making (e.g., confidence thresholds) is observable

### Art. 5: Prohibited Practices

**Dashboard is a monitoring tool, not a manipulation vector:**
- Dashboard metrics are read-only (dashboard cannot modify learning events)
- Users cannot be targeted based on metrics
- No dark patterns (straightforward numeric displays)

## 8. API Response Examples

### System-Wide Summary (Least Sensitive)
```json
{
  "status": "ok",
  "data": {
    "timestamp": "2026-09-02T12:34:56Z",
    "accuracy_summary": {
      "metric_type": "accuracy",
      "count": 1250,
      "mean": 0.92,
      "min": 0.75,
      "max": 1.0,
      "stddev": 0.08
    },
    "latency_summary": { ... },
    "total_events": 5000
  }
}
```

### Per-Skill Stats (Medium Sensitivity)
```json
{
  "status": "ok",
  "data": {
    "skill_name": "os.delegation_router",
    "accuracy": 0.95,
    "latency_ms": 42.5,
    "confidence": 0.88,
    "usage_count": 450,
    "last_updated": "2026-09-02T12:30:00Z"
  }
}
```

### User-Scoped Metrics (High Sensitivity)
```json
{
  "status": "ok",
  "data": {
    "user_id": "user_123",
    "satisfaction_avg": 4.2,
    "engagement_score": 0.78,
    "query_count": 456,
    "last_query": "2026-09-02T10:15:30Z"
  }
}
```

**No PII in any response:** Only aggregates and counts.

## 9. Security Measures

| Threat | Mitigation | GDPR Basis |
|---|---|---|
| **Unauthorized access** | Tenant isolation + role-based ACL (users ≤ own metrics, admins ≤ all) | Art. 32 (access control) |
| **Data leakage via cache** | Cache key includes tenant_id; no PII in cached values | Art. 32 (integrity) |
| **Audit tampering** | Hash-chained events; continuous verification on load | Art. 30, 32 (record-keeping, integrity) |
| **Subscriber DoS** | Automatic prune after 5min inactivity; max 1000 subscribers per tenant | Art. 32 (availability) |
| **Query bombing** | EventStore has limit=10000 (prevent OOM); cache hits avoid repeated queries | Art. 32 (availability) |

## 10. Exception Handling

### Audit Backend Failure

If audit backend is unreachable:
- Dashboard logs warning but continues serving
- Query is NOT executed (fail-closed for user safety)
- Operator is notified to investigate

**Rationale:** Audit unavailability should not take down observability; transparency > completeness.

### EventStore Failure

If event queries fail:
- Dashboard returns 500 Internal Server Error (fail-closed)
- Error message is generic (no details leaked to client)
- Operator is notified

### WebSocket Disconnection

If subscriber connection drops:
- Server-side: subscriber unregistered automatically
- Client-side: connection closes gracefully with error code
- Audit: no explicit "disconnect" event logged (implicit in subscriber lifecycle)

## 11. Regular Audits

### Compliance Checklist (Annual)

- [ ] Audit trail verified (no gaps, all queries logged)
- [ ] Consent flags respected (opted-out users excluded from metrics)
- [ ] Tenant isolation validated (cross-tenant queries blocked)
- [ ] Data retention policy enforced (90-day deletion triggers)
- [ ] Access control tested (user queries own metrics only)
- [ ] Hash-chain integrity verified (`verify_audit_chain.py`)

### Operator Commands

```bash
# Verify audit chain integrity
python3 core/compliance/verify_audit_chain.py --tenant=_default --since=2026-01-01

# Check dashboard query volume
grep "dashboard_query_executed" ~/.corvin/audit.jsonl | wc -l

# Find all user metrics queries
grep '"user_stats"' ~/.corvin/audit.jsonl | jq .filters.user_id

# Prune expired cache (manual)
# (automatic via LearningDashboard.cache.clear())
```

---

**Last Updated:** 2026-09-02  
**Review Date:** 2027-09-02 (annual)  
**Compliance Officer:** shumway  
