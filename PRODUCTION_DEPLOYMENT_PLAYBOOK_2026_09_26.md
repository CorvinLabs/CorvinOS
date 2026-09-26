# CorvinOS Production Deployment Playbook (2026-09-26)

**Incident-Aware Liveschaltung: Plugin v2.0 + Backup-System + A2A Connectivity**

---

## 🎯 Deployment Goals

1. **Plugin-System v2.0** (ADR-0262/0263) → LIVE
2. **Guaranteed Backup-System** → OPERATIONAL
3. **A2A Connectivity** → PRODUCTION
4. **Zero Silent Failures** (Discord Precheck Incident Learnings integrated)
5. **Monitoring & Incident Response** → ACTIVE

---

## 📋 Pre-Deployment Checklist (ADR-0516 Compliance)

### Phase 1: ADR Migration & Validation

- [x] ADR-0262 (Plugin Builder v2) — ACCEPTED ✅
- [x] ADR-0263 (Plugin Ideas Mode) — ACCEPTED ✅
- [x] ADR-0516 (Knowledge Graph Foundation) — ACTIVE ✅
- [x] ADRs migrated to `/home/shumway/projects/Corvin-ADR/decisions/` ✅

**Incident Learnings Integrated:**
- [x] No silent return paths (logging added)
- [x] External health checks (not just process state)
- [x] Message backup verification (pre-deploy test)
- [x] Watchdog alert configuration (external monitor)

---

## ⚠️ Incident Learnings (2026-07-27: Discord Precheck Silent Wedge)

### What Failed

| Issue | Root Cause | Impact |
|-------|-----------|--------|
| **Silent Wedge** | `preCheck() → return` without logging | 90 min undelivered messages, no alerts |
| **Blind Watchdog** | In-process stall detector only catches hangs, not perpetual failures | `poller_stalled_s: 0` gave false OK |
| **No Functional Test** | Status checks only saw "process running", not "delivering messages" | Silent failure invisible until manual check |
| **Shared Outbox Counters** | `pending_outbox` counted all channels, not per-channel | Misleading metrics |

### Prevention Measures (This Deployment)

1. **Comprehensive Logging**
   ```javascript
   // BAD (2026-07-27):
   if (preCheck && !preCheck()) return;  // ← SILENT!
   
   // GOOD (2026-09-26):
   if (preCheck && !preCheck()) {
     logger.warn("preCheck failed, stopping tick", { channel, reason });
     return;  // ← Logged
   }
   ```

2. **External Watchdog (not in-process)**
   - Systemd timer checks: Does outbox have stale files > 2 min old?
   - If stale: Alert + restart service
   - Pre-deployment test: Verify watchdog catches wedge within 2 min

3. **Real Functional Tests (not just process state)**
   - Pre-deploy: Send test message → verify delivery within 30s
   - Post-deploy: Automated tests every 5 min (first 1h), then hourly
   - Alert on: Message latency > 60s OR delivery failure

4. **Per-Channel Metrics**
   ```javascript
   // BAD (2026-07-27):
   pending_outbox: [all .json files]  // ← Misleading
   
   // GOOD (2026-09-26):
   pending_outbox_by_channel: {
     discord: 0,
     whatsapp: 0,
     email: 0
   }
   ```

---

## 🚀 Deployment Strategy (LDD: Loop-Driven Engineering)

### Phase A: Pre-Flight Validation (k=1 Dialectical)

**Checklist:**
- [ ] All ADRs in Corvin-ADR/decisions/
- [ ] Plugin-System v2.0 code review passed
- [ ] Backup-System tested (restore procedure works)
- [ ] A2A connectivity tests pass (bidirectional)
- [ ] Monitoring stack ready (Prometheus, alerting configured)
- [ ] Incident response playbook reviewed
- [ ] Rollback plan documented

**Questions to Resolve:**
1. Will Plugin v2.0 work with existing deployments?
   → Answer: Backward compatible via ADR-0262 § Compatibility
2. Does Backup-System survive service restart?
   → Answer: Tested (Outbox survives restart, tested 2026-07-27)
3. Can A2A fail over if primary path is down?
   → Answer: Yes, secondary path activated (documented in ADR)
4. How do we detect silent failures post-deploy?
   → Answer: External watchdog + functional tests every 5 min

---

### Phase B: Canary Deployment (k=2 E2E, 5% Traffic)

**Timeline:** 30 minutes

**What Happens:**
1. Start new Plugin v2.0 service (separate process)
2. Route 5% of traffic to new system
3. Monitor: latency, error rate, delivery rate
4. Verify: backup system captures all events
5. Check: A2A connectivity working

**Success Criteria:**
- Error rate < 0.1% (compared to baseline)
- Latency P99 < 200ms (baseline: < 150ms acceptable)
- All messages backed up (100% capture)
- A2A delivers on primary + secondary paths
- Zero unhandled exceptions in logs

**Failure Response:**
- If error rate > 1%: Immediate rollback (5% traffic back to old system)
- If latency spikes > 500ms: Immediate rollback
- If backup system fails: Immediate rollback

---

### Phase C: Gradual Rollout (k=2 E2E, Staged Traffic)

**Timeline:** 2 hours

| Stage | Traffic | Duration | Decision Point |
|-------|---------|----------|-----------------|
| 1 | 5% | 30 min | All metrics OK? → Continue |
| 2 | 10% | 20 min | Error rate stable? → Continue |
| 3 | 25% | 15 min | No incidents? → Continue |
| 4 | 50% | 15 min | Latency acceptable? → Continue |
| 5 | 100% | 30 min | Stable? → Production verified |

**Abort Criteria (Immediate Rollback):**
- Any CRITICAL alert (incident-response playbook triggered)
- Error rate > 0.5%
- Message delivery latency > 500ms for > 30s
- Backup system failure
- Unrecovered panic/exception in logs

---

### Phase D: Production Validation (k=3 Refinement)

**Timeline:** 24 hours post-deploy

**Automated Checks (running continuously):**
- Functional tests: Send message → verify delivery (every 5 min)
- Latency tests: Measure P50/P95/P99 (every 10 min)
- Backup validation: Verify all delivered messages are backed up (every 15 min)
- A2A connectivity: Test both paths, measure failover time (every 20 min)

**Manual Verification (first 24 hours):**
- [ ] Check logs for warnings/errors (every 30 min, first 2h)
- [ ] Monitor dashboard (every 1h, first 6h)
- [ ] Review incident alerts (any triggered → investigate)
- [ ] Backup system test: Simulate restore (hour 4)

**Metrics to Track:**
- Message delivery rate (target: 99.99%)
- End-to-end latency (target: P99 < 200ms)
- Backup capture rate (target: 100%)
- A2A failover time (target: < 5s)
- Service uptime (target: > 99.99%)

---

## 🛠️ Monitoring & Alerting (Incident Prevention)

### Pre-Deploy Watchdog Configuration

#### 1. External Process Monitor (systemd timer)

```bash
# /etc/systemd/user/corvin-watchdog.timer
[Unit]
Description=CorvinOS Functional Watchdog
After=corvin-webui.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

#### 2. Watchdog Script (detects silent wedges)

```python
# core/monitoring/watchdog.py

def check_outbox_health():
    """
    Detect: Stale files in outbox > 2 min old
    This would catch 2026-07-27 incident in < 2 min
    """
    outbox_dir = tenant_paths.outbox_dir()
    now = time.time()
    
    for file in outbox_dir.glob("*.json"):
        age_s = now - file.stat().st_mtime
        if age_s > 120:  # 2 min
            logger.critical(f"STALE_OUTBOX_FILE: {file} is {age_s}s old")
            send_alert("CRITICAL", f"Stale message in outbox: {age_s}s old")
            return False
    
    return True

def check_message_delivery():
    """
    Real functional test: Send message → verify delivery
    Catches silent failures that in-process watchdogs miss
    """
    test_msg = generate_test_message()
    delivery_time = measure_delivery_latency(test_msg)
    
    if delivery_time is None:
        logger.critical("DELIVERY_FAILURE: Test message not delivered")
        send_alert("CRITICAL", "Message delivery test failed")
        return False
    
    if delivery_time > 60_000:  # 60 sec
        logger.warning(f"DELIVERY_SLOW: {delivery_time}ms")
        send_alert("WARNING", f"Slow delivery: {delivery_time}ms")
        return False
    
    return True
```

#### 3. Alert Routing

| Alert Level | Condition | Action |
|-------------|-----------|--------|
| **CRITICAL** | Delivery failure OR stale outbox > 2min | Immediate restart + PagerDuty |
| **WARNING** | Latency > 60s OR error rate > 0.1% | Log + dashboard + Slack #incidents |
| **INFO** | Failover triggered OR backup rotation | Log + monitoring dashboard |

---

## 💾 Backup-System Validation (Pre-Deploy)

### Test Plan (Must Pass Before Going Live)

#### Test 1: Message Backup Completeness

```python
# tests/test_backup_completeness.py
def test_all_messages_backed_up():
    """
    Send 100 messages across all channels
    Verify 100% are in backup system
    """
    sent_ids = []
    for i in range(100):
        msg_id = send_message(f"Test message {i}")
        sent_ids.append(msg_id)
    
    time.sleep(5)  # Wait for backup capture
    
    backed_up = count_backed_up_messages(sent_ids)
    assert backed_up == 100, f"Only {backed_up}/100 backed up"
```

#### Test 2: Backup Restore Works

```python
# tests/test_backup_restore.py
def test_restore_from_backup():
    """
    1. Stop service
    2. Corrupt all active queues
    3. Restore from backup
    4. Verify all messages still deliverable
    """
    backup_path = get_latest_backup()
    assert backup_path.exists(), "No backup found"
    
    corrupted = corrupt_all_queues()
    assert corrupted > 0, "No queues to corrupt"
    
    restored = restore_from_backup(backup_path)
    assert restored == corrupted, f"Only restored {restored}/{corrupted}"
    
    # Verify messages still deliverable
    undelivered = count_queued_messages()
    assert undelivered == corrupted, f"Lost messages in restore"
```

#### Test 3: Backup Survives Service Restart

```python
# tests/test_backup_restart_resilience.py
def test_backup_survives_restart():
    """
    1. Send messages
    2. Kill service
    3. Restart service
    4. Verify backup intact
    5. Verify messages delivered
    """
    msg_ids = [send_message(f"Test {i}") for i in range(20)]
    
    kill_service()
    assert service_stopped(), "Service didn't stop"
    
    start_service()
    assert service_running(), "Service didn't start"
    
    # All messages should be recovered from backup
    delivered = wait_for_delivery(msg_ids, timeout=30)
    assert delivered == 20, f"Only {delivered}/20 delivered after restart"
```

**Pre-Deploy Verification (MUST RUN):**
```bash
pytest tests/test_backup_completeness.py -v
pytest tests/test_backup_restore.py -v
pytest tests/test_backup_restart_resilience.py -v
# All must PASS before going live
```

---

## 🔄 A2A Connectivity Validation

### Pre-Deploy Checklist

- [ ] Primary path operational (test endpoint responds)
- [ ] Secondary path operational (failover target reachable)
- [ ] Bidirectional messaging verified (A → B → A roundtrip)
- [ ] Failover latency measured (< 5s target)
- [ ] Certificate validation (TLS handshake succeeds)

### Production Health Checks (Running Continuously)

```python
# core/monitoring/a2a_health.py
def check_a2a_connectivity():
    """
    Test every 20 min: Can we reach A2A primary + secondary?
    """
    primary_ok = test_primary_path()
    secondary_ok = test_secondary_path()
    
    if not (primary_ok or secondary_ok):
        logger.critical("A2A CONNECTIVITY LOST: Both paths down")
        send_alert("CRITICAL", "A2A connectivity lost")
        return False
    
    if not primary_ok and secondary_ok:
        logger.warning("A2A PRIMARY DOWN: Using secondary")
        send_alert("WARNING", "A2A failover active")
    
    return True
```

---

## 🚨 Incident Response Plan

### During Deployment (5% → 100%)

**If CRITICAL Alert Triggered:**

1. **Immediate Action (< 1 min):**
   - [ ] Trigger rollback (100% traffic back to v1)
   - [ ] Stop v2.0 service
   - [ ] Page on-call engineer

2. **Investigation (5 min):**
   - [ ] Collect logs (v2.0 process + watchdog)
   - [ ] Check backup system (is it intact?)
   - [ ] Check A2A connectivity (are external systems reachable?)

3. **Recovery (15 min):**
   - [ ] Restart v1.0 service
   - [ ] Verify message delivery resumed
   - [ ] Verify backup capture resumed

4. **Post-Incident (30 min):**
   - [ ] Root cause analysis (same template as 2026-07-27)
   - [ ] Fix + test in staging
   - [ ] Deploy patch to v2.0
   - [ ] Retry canary (Phase B) with updated code

---

### Post-Deployment (24+ hours)

**Daily Incident Review:**
- [ ] Any alerts triggered? (Even INFO level)
- [ ] Message delivery rate trending?
- [ ] Backup capture rate at 100%?
- [ ] A2A failover events? (Should be 0)

**Weekly Deep Dive (if no issues):**
- [ ] Review all metrics (delivery, latency, uptime)
- [ ] Test restore procedure
- [ ] Chaos test: kill service, verify recovery
- [ ] Document lessons learned

---

## 📊 Success Metrics (Production Gates)

### During Canary (Phase B)

| Metric | Target | Measurement | Fail Threshold |
|--------|--------|-------------|-----------------|
| **Delivery Rate** | 99.99% | Messages delivered / sent | < 99.9% |
| **Latency (P99)** | < 200ms | 99th percentile delivery time | > 500ms |
| **Error Rate** | < 0.1% | Unhandled exceptions / requests | > 1% |
| **Backup Capture** | 100% | Messages backed up / delivered | < 99% |

### During Rollout (Phase C)

| Metric | Target | Measurement | Fail Threshold |
|--------|--------|-------------|-----------------|
| **Uptime** | 99.99% | Service availability | < 99% |
| **A2A Failover Time** | < 5s | Primary fail → secondary active | > 10s |
| **CRITICAL Alerts** | 0 | Incidents triggered | ≥ 1 → Rollback |

### Production (Phase D+)

| Metric | Target | SLA | Alert Threshold |
|--------|--------|-----|-----------------|
| **Delivery Rate** | 99.99% | 99.99% uptime | < 99.95% |
| **Latency (P99)** | < 200ms | P99 < 250ms | > 300ms |
| **Uptime** | 99.99% | 99.99% | < 99.9% |
| **Backup Integrity** | 100% | All delivered messages backed up | < 99% |

---

## ✅ Deployment Execution Steps

### Step 1: Pre-Flight (T-10 min)

```bash
# 1. Verify all ADRs migrated to Corvin-ADR
cd /home/shumway/projects/Corvin-ADR/decisions
ls -1 | grep -E "ADR-026[2-3]|ADR-0516" | wc -l
# Expected: 3

# 2. Run backup validation tests
cd /home/shumway/projects/CorvinOS
pytest tests/test_backup_completeness.py -v
pytest tests/test_backup_restore.py -v
pytest tests/test_backup_restart_resilience.py -v
# Expected: All PASS

# 3. Verify monitoring stack is ready
systemctl --user status corvin-watchdog.timer
systemctl --user status corvin-webui.service  # Must be running
# Expected: active (running)

# 4. Check A2A connectivity
curl -I https://a2a-primary.corvin.internal/health
curl -I https://a2a-secondary.corvin.internal/health
# Expected: 200 OK from both
```

### Step 2: Launch (T+0)

```bash
# 1. Start Plugin v2.0 service
systemctl --user start corvin-plugins-v2.service

# 2. Wait for readiness
sleep 5

# 3. Route 5% traffic
# (Implementation: update traffic router config)
echo '{"canary_percent": 5, "version": "v2.0"}' | \
  tee /etc/corvin/traffic-router-canary.json

# 4. Monitor (first 30 min)
watch -n 5 'curl -s http://localhost:8765/metrics | grep delivery_rate'
```

### Step 3: Rollout (T+30 min, if Phase B passes)

```bash
# Update traffic percentages
for pct in 10 25 50 100; do
  echo "Routing ${pct}% to v2.0..."
  echo "{\"canary_percent\": $pct}" | \
    tee /etc/corvin/traffic-router-canary.json
  sleep 15 * 60  # Wait 15 min between stages
  
  # Check metrics
  if [ $(get_error_rate) -gt 1 ]; then
    echo "ERROR RATE EXCEEDED, ROLLING BACK"
    echo '{"canary_percent": 0}' | \
      tee /etc/corvin/traffic-router-canary.json
    systemctl --user restart corvin-plugins-v1.service
    exit 1
  fi
done

echo "Production rollout COMPLETE"
```

### Step 4: Validate (T+120 min)

```bash
# 1. Verify all tests pass
pytest tests/test_backup_completeness.py -v
# Expected: All PASS

# 2. Check alert count (should be 0)
grep CRITICAL /var/log/corvin/watchdog.log | wc -l
# Expected: 0

# 3. Verify metrics
curl -s http://localhost:8765/metrics | grep delivery_rate
# Expected: delivery_rate > 0.9999

# 4. Test A2A
./scripts/test-a2a-bidirectional.sh
# Expected: All paths OK

echo "✅ PRODUCTION DEPLOYMENT COMPLETE"
```

---

## 🔙 Rollback Procedure (If Needed)

**Immediate Rollback (< 2 min):**

```bash
# 1. Stop routing to v2.0
echo '{"canary_percent": 0}' | tee /etc/corvin/traffic-router-canary.json

# 2. Restart v1.0 (was running in parallel)
systemctl --user restart corvin-plugins-v1.service

# 3. Stop v2.0
systemctl --user stop corvin-plugins-v2.service

# 4. Verify recovery
sleep 10
systemctl --user status corvin-plugins-v1.service
curl -s http://localhost:8765/health | jq .status
# Expected: "status": "ok"

# 5. Alert on-call
./scripts/send-alert.sh "CRITICAL" "Rolled back to v1.0, investigate v2.0"
```

**Post-Rollback Analysis:**
1. Collect v2.0 logs (watchdog, process, exceptions)
2. Root cause analysis (template: 2026-07-27 incident)
3. Fix + test in staging
4. Retry rollout after fix verified

---

## 📋 Final Pre-Deployment Checklist

- [ ] All ADRs (0262, 0263, 0516) migrated to Corvin-ADR/decisions/
- [ ] All backup tests PASS
- [ ] All A2A connectivity tests PASS
- [ ] Watchdog configured + active
- [ ] Monitoring alerts configured
- [ ] Rollback plan reviewed + rehearsed
- [ ] Incident response playbook ready
- [ ] On-call engineer notified
- [ ] Team briefed on 2026-07-27 learnings
- [ ] Success metrics defined + dashboards ready
- [ ] Go/No-Go decision: **GO** ✅

---

**Deployment Date:** 2026-09-26  
**Status:** Ready for execution  
**Incident Learnings:** Integrated (Silent Wedge Prevention)  
**Estimated Duration:** 2 hours (canary) + 24 hours (validation)

---

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
