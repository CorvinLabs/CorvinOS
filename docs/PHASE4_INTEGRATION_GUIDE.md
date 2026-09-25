# Phase 4: Real-Time Drift Detection & Alerting — Integration Guide

**Status:** Ready for Integration  
**ADR:** ADR-0409  
**Depends on:** Phase 1 (Drift Prevention)  
**Timeline:** 16h implementation + N hours integration testing  

---

## What Phase 4 Delivers

1. **Background Monitoring Thread** — Polls all instances every 30 seconds
2. **Drift Detection** — Integrates with Phase 1 (DeploymentStateManager)
3. **Alert Routing** — Slack (all severities) + PagerDuty (CRITICAL only)
4. **Audit Trail** — Every alert logged to security event log
5. **Dashboard Ready** — Recent 100 events stored for operator visibility

---

## Integration Checklist

### Step 1: Verify Phase 1 is integrated

Ensure `DeploymentStateManager` is initialized and instances are registered:

```python
# In core/console/corvin_console/app.py or main entry point

from core.deployment.state_sync import get_deployment_manager

@app.lifespan("startup")
async def startup():
    # Register this instance (Phase 1)
    manager = get_deployment_manager()
    manager.register_instance(
        instance_id=os.getenv("INSTANCE_ID", "dev-local"),
        tenant_id=os.getenv("CORVIN_TENANT_ID", "_default"),
    )
    print(f"✅ Instance registered: canonical state captured")
```

### Step 2: Initialize Phase 4 monitoring service

Add to the same startup block:

```python
from core.monitoring.drift_detector import get_drift_service

@app.lifespan("startup")
async def startup():
    # ... Phase 1 init (above) ...
    
    # Start drift monitoring (Phase 4)
    detector = get_drift_service()
    detector.start_monitoring()
    print("✅ Drift detection service started (30s polling)")
```

### Step 3: Configure alert integrations

Set environment variables (`.env` or deployment config):

```bash
# Slack integration (required for alerts)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# PagerDuty integration (for CRITICAL alerts)
PAGERDUTY_API_KEY=YOUR_PAGERDUTY_API_KEY

# Instance ID (must match what Phase 1 registered)
INSTANCE_ID=prod-us-east-1
CORVIN_TENANT_ID=_default
```

**Note:** If either webhook is missing, alerts gracefully degrade:
- No `SLACK_WEBHOOK_URL` → alerts logged only (no Slack)
- No `PAGERDUTY_API_KEY` → CRITICAL alerts sent to Slack only (no page)

### Step 4: Wire shutdown hook

Add to the same startup block:

```python
@app.lifespan("shutdown")
async def shutdown():
    detector = get_drift_service()
    detector.stop_monitoring()
    print("❌ Drift detection service stopped")
```

### Step 5: Verify integration with manual test

```bash
# 1. Register a second "instance" for testing
curl -X POST http://localhost:8765/v1/admin/instances \
  -H "Content-Type: application/json" \
  -d '{"instance_id": "test-drift", "git_sha": "wrong_sha"}'

# 2. Trigger drift detection
curl -X POST http://localhost:8765/v1/admin/drift-detector/poll

# 3. Check Slack webhook received the alert
# → Should see alert in #corvin-alerts or similar

# 4. Check PagerDuty incident created
# → Should see incident in PagerDuty console (CRITICAL drifts only)

# 5. Verify audit trail
grep "drift_alert_sent" ~/.corvin/audit.jsonl | tail -1
```

### Step 6: Wire console dashboard (optional, Phase 5)

Future: Add endpoint to display recent drifts:

```python
from core.monitoring.drift_detector import get_drift_service

@app.get("/v1/console/monitoring/drifts")
async def get_recent_drifts(limit: int = 100):
    detector = get_drift_service()
    events = detector.get_recent_events(limit=limit)
    return {
        "recent_events": [
            {
                "timestamp": e.timestamp,
                "instance_id": e.instance_id,
                "drift_type": e.drift_type,
                "severity": e.severity.value,
                "message": e.message,
            }
            for e in events
        ],
        "total_alerts": len(detector.monitoring_events),
        "critical_count": len([e for e in events if e.severity.name == "CRITICAL"]),
    }
```

---

## Testing Integration

### Manual Test (Recommended)

```bash
# 1. Deploy Phase 4 code
git pull origin main
cd /home/shumway/projects/CorvinOS

# 2. Start the app
python3 -m corvin_console.app &
# → Should log: "✅ Drift detection service started (30s polling)"

# 3. Simulate drift (register a different version)
python3 <<EOF
from core.deployment.manifest import ManifestManager
from core.deployment.state_sync import get_deployment_manager

# Register "test-instance" with different code
manager = get_deployment_manager()
manager.register_instance("test-instance")

# Manually modify its state to create drift
test_state = manager.instance_states["test-instance"]
test_state.git_sha = "different_sha_than_canonical"

# Detect drifts
drifts = manager.detect_all_drifts()
print(f"✅ Found {len(drifts)} drifts")
for d in drifts:
    print(f"  - {d.alert_type}: {d.message}")
EOF

# 4. Wait 30s for monitoring loop to detect
sleep 30

# 5. Verify Slack webhook received alert
# → Check Slack #corvin-alerts for RED alert

# 6. Verify PagerDuty incident created
# → Check PagerDuty console for "CODE_VERSION_DRIFT" incident

# 7. Verify audit trail
grep "drift_alert_sent" ~/.corvin/audit.jsonl | jq .

# 8. Stop the app
pkill -f "python3 -m corvin_console.app"
# → Should log: "❌ Drift detection service stopped"
```

### Automated Test (CI/CD)

```bash
# Run Phase 4 test suite
cd /home/shumway/projects/CorvinOS
python3 -m pytest tests/monitoring/test_phase4_drift_detection.py -v

# Expected: 15+ tests passing ✅
```

---

## Monitoring the Monitor

Phase 4 is designed to be self-healing, but watch for these signals:

### ✅ Healthy Monitoring

```
2026-09-26 12:34:56 INFO  ✅ Drift detection service started (30s polling)
2026-09-26 12:35:26 INFO  ✅ Slack alert sent: CRITICAL — CODE_VERSION_DRIFT
2026-09-26 12:35:27 INFO  ✅ PagerDuty incident triggered: CODE_VERSION_DRIFT on prod-us-east-1
```

### ⚠️ Degraded Mode

```
2026-09-26 12:34:00 WARNING SLACK_WEBHOOK_URL not set; Slack alerts disabled
  → Phase 4 still runs, but no Slack alerts. Monitor via dashboard instead.

2026-09-26 12:35:00 ERROR   ❌ Slack alert failed: Connection timeout
  → Webhook is down. Alert logged to audit trail. Operator gets visibility in dashboard.
```

### ❌ Failed Monitoring

```
2026-09-26 12:34:56 CRITICAL ❌ Drift detection service failed to start
  → Check if DeploymentStateManager initialized (Phase 1).
  → Check if instance_states has at least one registered instance.
```

---

## Metrics & Observability

Phase 4 emits these metrics (via `monitoring_events` list):

| Metric | Source | Type | Example |
|--------|--------|------|---------|
| `drift_alerts_total` | `len(detector.monitoring_events)` | Counter | 42 total alerts since boot |
| `drift_alerts_by_severity` | Filter by `severity` | Counter | CRITICAL: 3, HIGH: 8, MEDIUM: 5, LOW: 26 |
| `drift_alerts_by_instance` | Filter by `instance_id` | Counter | prod-us-east-1: 15, staging-eu: 12, dev: 15 |
| `drift_detection_latency` | `time.time() - drift.timestamp` | Histogram | 0–30s (30s polling + alert routing) |
| `pagerduty_page_count` | Count `pagerduty_alerter.trigger_incident` calls | Counter | 3 pages in last 24h |
| `slack_webhook_failure_count` | Count `slack_alerter` exceptions | Counter | 0 failures (healthy) |

To query metrics in the future:

```bash
# Via dashboard
curl http://localhost:8765/v1/console/monitoring/drifts | jq .

# Via CLI
python3 <<EOF
from core.monitoring.drift_detector import get_drift_service
detector = get_drift_service()
events = detector.get_recent_events()
print(f"Total alerts: {len(detector.monitoring_events)}")
print(f"Recent: {len(events)}")
for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
    count = len([e for e in events if e.severity.name == severity])
    print(f"  {severity}: {count}")
EOF
```

---

## Troubleshooting

### "Drift detection service failed to start"

**Cause:** DeploymentStateManager not initialized (Phase 1)  
**Fix:** Ensure Phase 1 startup code runs first:

```python
# In app.py startup:
manager = get_deployment_manager()  # Phase 1 — MUST run first
manager.register_instance(...)      # Phase 1 — MUST register instance

detector = get_drift_service()      # Phase 4 — runs after Phase 1
detector.start_monitoring()
```

### "No alerts received, but drifts exist"

**Cause:** Instance not registered or monitoring thread blocked  
**Fix:**
1. Verify instance registered: `manager.instance_states` should be non-empty
2. Verify monitoring thread alive: `detector.is_running == True`
3. Wait 30s for polling interval to trigger
4. Check logs for errors: `grep "drift_alert" ~/.corvin/audit.jsonl`

### "Slack alerts not sent"

**Cause:** `SLACK_WEBHOOK_URL` missing or invalid  
**Fix:**
1. Set webhook URL: `export SLACK_WEBHOOK_URL=https://hooks.slack.com/...`
2. Verify URL is reachable: `curl -X POST $SLACK_WEBHOOK_URL -d '{"text":"test"}'`
3. Restart app for new env var to take effect

### "PagerDuty incidents not created"

**Cause:** `PAGERDUTY_API_KEY` missing or CRITICAL drift not detected  
**Fix:**
1. Set API key: `export PAGERDUTY_API_KEY=...`
2. Verify drift severity is CRITICAL (check Phase 1 alert_type)
3. Check PagerDuty integration events: `https://pagerduty.com/events`
4. Verify dedup key: `drift_{instance}_{type}` (should be unique per combination)

### "Monitoring loop consuming too much CPU"

**Cause:** Polling interval too short or drift detection slow  
**Fix:**
1. Increase polling interval: `POLLING_INTERVAL_SECONDS` in code (default: 30s)
2. Profile drift detection: `manager.detect_all_drifts()` should complete < 5s
3. If slow, offload to async Phase 5 architecture

---

## Phase 4 → Phase 5 Handoff

Phase 4 provides the foundation. Phase 5 (ADR-0410) will add:

- **Auto-remediation** — automatically fix safe drifts (plugin install, config sync)
- **Approval workflow** — require manual approval for high-risk drifts (code version)
- **Rollback capability** — revert failed remediation
- **Dashboard UI** — visualize drift timeline + metrics
- **Incident resolution** — auto-resolve when drift fixed
- **Custom thresholds** — operator-configurable alert rules

Data flow for Phase 5:

```
Phase 4: Detect + Alert ← (existing)
  └→ Phase 5: Remediate + Resolve (new)
     ├→ Auto-remediation for MEDIUM/LOW severity
     ├→ Approval workflow for HIGH/CRITICAL
     ├→ PagerDuty incident resolution when fixed
     └→ Dashboard feedback loop
```

---

## Sign-Off

**Phase 4 Status:** ✅ COMPLETE & READY FOR INTEGRATION

- [x] Implementation: drift_detector.py (full)
- [x] Tests: 15+ cases (all mocked, passing)
- [x] Docs: ADR-0409 (ACCEPTED), this guide
- [x] Integration: non-blocking startup (daemon thread)
- [x] Audit: all alerts logged to security event log
- [x] Fail-closed: webhook/API failures never crash monitoring

**Next Steps:**
1. Integrate Phase 4 into app.py (5 min)
2. Set SLACK_WEBHOOK_URL and PAGERDUTY_API_KEY (10 min)
3. Run manual test (15 min)
4. Monitor for 24h to verify stability
5. Plan Phase 5 (auto-remediation) rollout

---

**Created:** 2026-09-26  
**Author:** Claude  
**Status:** Ready for Deployment
