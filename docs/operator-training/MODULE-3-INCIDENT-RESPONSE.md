# Brain v0.2 Operator Training — Module 3: Incident Response
## 60-Minute Diagnostic Deep Dive

**Version:** 1.0 (2026-08-23)  
**Target Audience:** Production operators, on-call engineers, incident commanders  
**Prerequisite:** Modules 1–2 (Architecture + Monitoring)  
**Outcome:** Diagnose root cause and recover from 4 real production scenarios

---

## Learning Objectives

By the end of this module, you will:
1. Diagnose subsystem crashes using metrics and logs
2. Respond to memory leaks with targeted restart
3. Recover from event queue overflow
4. Escalate audit chain corruption to maintainers
5. Execute rollback procedure when needed

---

## Section 1: The Incident Response Flowchart (5 minutes)

```
┌─────────────────────────────┐
│   ALERT FIRES               │
│  (email/Slack notification) │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ TRIAGE (< 2 minutes)        │
│ ├─ Page on-call?            │
│ ├─ Acknowledge alert        │
│ ├─ Open incident tracking   │
│ └─ Gather initial context   │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ DIAGNOSE (5–15 minutes)     │
│ ├─ Check affected subsystem │
│ ├─ Read error log           │
│ ├─ Query metrics            │
│ └─ Identify root cause      │
└─────────┬───────────────────┘
          │
   ┌──────┴──────┐
   │             │
   ▼             ▼
RESTART?    ROLLBACK?
   │             │
   │      ┌──────▼──────┐
   │      │ CRITICAL?   │
   │      │ (audit fail)│
   │      └──────┬──────┘
   │             │
   ▼             ▼
RECOVER      ESCALATE
(< 30s)      (maintainer)
   │             │
   └──────┬──────┘
          │
          ▼
┌─────────────────────────────┐
│ MONITOR RECOVERY (30 min)   │
│ ├─ Error rate dropping?     │
│ ├─ Latency normalizing?     │
│ ├─ No new errors?           │
│ └─ Declare RESOLVED         │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ ROOT-CAUSE ANALYSIS (< 24h) │
│ ├─ What went wrong?         │
│ ├─ Why was it not caught?   │
│ ├─ File GitHub issue        │
│ └─ Update playbook          │
└─────────────────────────────┘
```

---

## Section 2: Scenario 1 — Subsystem Crash (15 minutes)

### The Alert

```
[CRITICAL] DeploymentErrorRateHigh fired at 10:31 UTC

Error rate: 3.2% (threshold 2%)
Sustained for: 5 minutes
Affected subsystem: cost_controller
Impact: All cost estimation requests failing
```

### Step 1: Acknowledge & Triage (< 1 minute)

```bash
# 1. Acknowledge alert in Prometheus
curl -X POST http://localhost:9093/api/v1/alerts/acknowledge \
  -d '{"id": "DeploymentErrorRateHigh", "start": 1692785400}'

# 2. Open incident tracking
# -> Create ticket in Jira/GitHub (template below)

# 3. Notify team
# -> Post to #incident-response Slack channel
# -> "@team There's a cost_controller spike, investigating..."

# 4. Check current status
corvin metrics query 'rate(corvin_errors_total{subsystem="cost_controller"}[5m])'
# Should return ~3.2% or higher
```

### Step 2: Diagnose (5 minutes)

```bash
# A. Check subsystem health
corvin status cost_controller
# Output: UNHEALTHY, last_heartbeat: 2026-08-23T10:25:00Z (6 min ago)

# B. Check error log
tail -100 ~/.corvin/tenants/_default/audit.jsonl | grep -i "cost_controller"
# Output:
# 2026-08-23T10:26:15Z [ERROR] cost_controller: Division by zero in budget.py:45
# 2026-08-23T10:26:15Z [ERROR] cost_controller: Request timeout (30s exceeded)
# 2026-08-23T10:26:15Z [ERROR] cost_controller: Subsystem crashed, restarting...

# C. Check memory usage
corvin metrics query 'corvin_process_memory_bytes{subsystem="cost_controller"}'
# Output: 1.2 GB (normal is ~50MB for this subsystem) → MEMORY ISSUE

# D. Check CPU usage
corvin metrics query 'rate(corvin_cpu_seconds_total{subsystem="cost_controller"}[5m])'
# Output: 85% of one CPU core (should be <5%)

# E. Check recent code changes
git log --oneline -10 | grep -i cost
# Output: abc1234 "feat: add cost optimization for large tasks"
#         def5678 "fix: race condition in cost estimation"
```

**ROOT CAUSE ANALYSIS:**
The recent cost optimization commit introduced a memory leak. On tasks with >5000 tokens, the list of cost_history items grows unbounded.

### Step 3: Recover (< 2 minutes)

```bash
# Option A: Quick Restart (Preferred for suspected hang/crash)
echo "Restarting cost_controller..."
corvin restart-subsystem cost_controller

# Monitor recovery
watch -n 1 'corvin status cost_controller'
# Should show HEALTHY within 10 seconds

# Check error rate drop
watch -n 2 'corvin metrics query "rate(corvin_errors_total{subsystem=\"cost_controller\"}[5m])"'
# Should drop to <0.5% within 30 seconds
```

**SUCCESS CRITERIA:**
- [ ] Subsystem health → HEALTHY
- [ ] Error rate → drops below 1%
- [ ] Latency → returns to baseline
- [ ] No new errors in log

### Step 4: Post-Recovery Actions (< 5 minutes)

```bash
# 1. Verify audit chain (most important safety check)
corvin audit verify
# Output: "Chain integrity: VALID, hash mismatches: 0"

# 2. Verify all other subsystems still healthy
corvin status all
# Output: 13/13 subsystems HEALTHY

# 3. Run smoke test (simple transaction)
corvin test smoke
# Output: PASSED - 3/3 test scenarios passed

# 4. Declare incident RESOLVED
# -> Update ticket: "RESOLVED - cost_controller restarted at 10:35 UTC"
# -> Post to Slack: "Incident resolved. Error rate back to 0.3%"
```

### Step 5: Root-Cause Analysis (Schedule within 24h)

```bash
# 1. Identify the bad commit
git log --oneline core/orchestration/cost_controller.py
# abc1234 "feat: add cost optimization for large tasks"

# 2. Review the change
git show abc1234 | head -50
# Look for: unbounded list growth, missing cleanup

# 3. File GitHub issue
# Title: "[BUG] Cost controller memory leak on large tasks"
# Body: "Introduced by abc1234, memory grows 10MB per 1000-token task"

# 4. Create hotfix
git checkout main
git pull
git branch fix/cost-memory-leak
# ... make fix ...
git push origin fix/cost-memory-leak

# 5. Request review and merge
# -> PR to main, request review, merge
```

---

## Section 3: Scenario 2 — Memory Leak (20 minutes)

### The Alert

```
[CRITICAL] MemoryLeak detected at 14:32 UTC

Memory growing at: 5 MB/minute
Current: 890 MB (baseline 500 MB)
Sustained for: 30 minutes
Suspect subsystem: learning_engine
```

### Step 1: Confirm the Leak (< 2 minutes)

```bash
# 1. Graph memory over time
corvin metrics query -span=2h \
  'corvin_process_memory_bytes{subsystem="learning_engine"}'

# Should show linear growth over 30+ minutes
# Expected output (timestamp, value):
# 14:00 UTC → 500 MB
# 14:15 UTC → 575 MB (+75 MB)
# 14:30 UTC → 650 MB (+75 MB)
# 14:45 UTC → 725 MB (+75 MB)
# Confirmed: growing at ~2.5 MB/min

# 2. Check if it's GC-able or real leak
corvin debug gc-stats learning_engine
# Output:
# Last GC: 14:30:00 (freed 5 MB)
# GC frequency: every 10 min
# Verdict: GC running, but memory still grows
# → REAL LEAK (not temporary allocation)
```

### Step 2: Diagnose Source (5 minutes)

```bash
# A. Check event store size (learning_engine stores events)
find ~/.corvin/tenants/_default -name "*.db" -exec du -h {} \;
# Output: learning_engine.db → 2.1 GB (should be <100 MB!)

# B. Query the database
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  "SELECT COUNT(*) FROM events;"
# Output: 5,234,891 events (should be <10,000, clearly unbounded!)

# C. Check when events started accumulating
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  "SELECT COUNT(*) FROM events WHERE timestamp > datetime('now', '-1 hour');"
# Output: 1.2 million events in last hour
# Verdict: Something started emitting events at high rate

# D. Check what event types
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  "SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY COUNT(*) DESC LIMIT 10;"
# Output:
# context_updated → 950k events (ANOMALY!)
# decision_recorded → 200k events
# outcome_recorded → 50k events
# Verdict: context_updated event is being emitted 10x per second
```

**ROOT CAUSE:** A recent change to ContextAPI makes it emit context_updated event on every single context read, not just writes. The learning engine subscribes to these events and stores them all.

### Step 3: Recover (< 3 minutes)

```bash
# Option A: Restart Learning Engine (Quick Fix)
echo "Restarting learning_engine..."
corvin restart-subsystem learning_engine

# Monitor memory drop
watch -n 2 'corvin metrics query "corvin_process_memory_bytes{subsystem=\"learning_engine\"}"'
# Should drop to ~50 MB within 10 seconds (subsystem resets)

# Check for memory leak
watch -n 30 'corvin metrics query "corvin_process_memory_bytes{subsystem=\"learning_engine\"}"'
# Monitor for 5 minutes to ensure memory stays low
# Expected: flat or small sawtooth, NOT linear growth
```

**CRITICAL DECISION POINT:**
```
Is memory stable after restart?
  ├─ YES (flat): Proceed with Option B (cleanup)
  └─ NO (still growing): Rollback (proceed to Scenario 4)
```

### Step 4: Cleanup Database (2 minutes)

```bash
# A. Backup the database (in case investigation needed)
cp ~/.corvin/tenants/_default/learning_engine.db \
   ~/.corvin/tenants/_default/learning_engine.db.backup-$(date +%s)

# B. Clear the event store
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  "DELETE FROM events WHERE event_type='context_updated';"

# C. Verify cleanup
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  "SELECT COUNT(*) FROM events;"
# Output: should be much smaller (e.g., 50k instead of 5.2M)

# D. Verify learning engine still works
corvin test learning_engine
# Output: PASSED - event recording works
```

### Step 5: Root-Cause Fix (Schedule within 24h)

```bash
# 1. Identify the change to ContextAPI
git log --oneline core/orchestration/context_api.py | head -5
# Output: xyz9999 "refactor: emit context_updated on all reads"

# 2. Review the change
git show xyz9999 | grep -A 5 "def query_context"
# Look for: publish_event("context_updated") in every query method
# This is the bug!

# 3. Create hotfix
# Should only emit on writes (update_context), not reads (query_context)

# 4. Test the fix
# Create test case: query_context 1000x, verify event count < 10
# Before fix: 1000 events
# After fix: 0 events

# 5. Merge fix
```

---

## Section 4: Scenario 3 — Audit Chain Corruption (15 minutes)

### The Alert

```
[CRITICAL] AuditChainInvalid fired at 16:47 UTC

Bootstrap tripwire failure: audit.jsonl hash mismatch
Last valid hash: 2026-08-23T16:45:22Z
Gap detected: 17 minutes (16:30–16:47 UTC)
Service: FAILED TO START
```

### THIS IS A CRITICAL SITUATION

**MOST IMPORTANT RULE:** Do NOT restart the service. This is a data integrity issue, not a temporary glitch.

### Step 1: Immediate Actions (< 1 minute)

```bash
# 1. STOP any service restarts (notify ops)
echo "DO NOT RESTART CORVIN SERVICE - AUDIT CHAIN CORRUPTED"
systemctl stop corvin-service

# 2. Notify maintainer immediately
# Email: shumway@corvin.ai, cc: ops-team
# Subject: "[CRITICAL] Audit chain corruption - manual intervention required"
# Body: "Service unable to start. Bootstrap tripwire failed. Backup taken."

# 3. Preserve evidence (critical!)
mkdir -p ~/incident-backup-$(date +%Y%m%d-%H%M%S)
cp -r ~/.corvin ~/incident-backup-$(date +%Y%m%d-%H%M%S)/
echo "Backup location: ~/incident-backup-$(date +%Y%m%d-%H%M%S)"
```

### Step 2: Investigate (< 10 minutes, but DO NOT FIX)

```bash
# 1. Check audit file integrity
corvin audit verify --verbose
# Output will show: where the chain breaks

# 2. Dump the audit tail (last 100 lines)
tail -100 ~/.corvin/tenants/_default/audit.jsonl > audit-tail.txt

# 3. Check if single tenant or all tenants affected
for tenant_dir in ~/.corvin/tenants/*/; do
  echo "Checking $(basename $tenant_dir)..."
  tail -1 "$tenant_dir/audit.jsonl" | jq .
done

# 4. File incident report
cat > incident-report.md << 'EOF'
# Audit Chain Corruption - 2026-08-23 16:47 UTC

## Timeline
- 16:30 UTC: Last known good hash
- 16:45 UTC: Error observed in logs
- 16:47 UTC: Bootstrap tripwire failed
- 16:48 UTC: Service unable to start

## Affected Tenants
- _default (chain broken)
- tenant_b (status unknown - need maintainer check)

## Evidence Collected
- Backup: ~/incident-backup-20260823-164800/
- Audit tail: ./audit-tail.txt
- Last known good hash: abc123def456...

## Suspect Root Cause
- Recent ADR-0403 integration (Phase C tenant changes)?
- Hardware issue (disk corruption)?
- Concurrent write to audit.jsonl?

## Next Steps (Maintainer Only)
1. Validate if specific tenant or all affected
2. Restore from backup or re-initialize from audit events
3. Update bootstrap tripwire logic if needed
4. File GitHub issue for post-mortem
EOF

cat incident-report.md
```

### Step 3: Escalation (MANDATORY)

```bash
# 1. Send incident report to maintainer
mail -s "[CRITICAL] Audit Chain Corruption" shumway@corvin.ai < incident-report.md

# 2. Post to #incident-response Slack
curl -X POST https://hooks.slack.com/services/YOUR/WEBHOOK/URL \
  -d '{
    "text": "🚨 [CRITICAL] Audit chain corruption detected. Service offline. Maintainer escalation in progress."
  }'

# 3. Wait for maintainer response
# Expected: <30 min on business hours, <2h on weekends

# 4. Provide information as requested
# Maintainer will ask for:
# - Audit file backup
# - System logs (journalctl)
# - Recent code changes
```

### Step 4: Recovery (Maintainer Only)

Once maintainer arrives, they will:

```python
# Option A: Restore from daily export (if available)
# corvin repair-audit _default --from-export

# Option B: Re-initialize from event store
# corvin repair-audit _default --rebuild-from-events

# Option C: Manual hash-chain rebuild
# (Most invasive, requires deep review)
```

**Your role:** Provide information, monitor service health after recovery, update documentation.

---

## Section 5: Scenario 4 — Event Queue Overflow (10 minutes)

### The Alert

```
[WARNING] ContextBusQueueFull at 09:15 UTC

Queue depth: 100/100 (full)
Events dropped: 5 (since 09:10 UTC)
Processing rate: 10 events/sec (too slow!)
Publishing rate: 20 events/sec (incoming traffic too high)
Imbalance: publishing 2x faster than processing
```

### Step 1: Diagnose (< 5 minutes)

```bash
# 1. Check queue status
corvin metrics query 'corvin_context_bus_queue_depth'
# Output: 100/100 (FULL)

# 2. Check event publishing rate
corvin metrics query 'rate(corvin_events_published_total[1m])'
# Output: 25 events/sec (high!)

# 3. Check event processing rate
corvin metrics query 'rate(corvin_events_processed_total[1m])'
# Output: 10 events/sec (too slow!)

# 4. Check which subsystem is slowest
corvin metrics query 'corvin_event_handler_duration_ms' --labels

# Output:
# {handler="context_bridge"} → 5ms (fast)
# {handler="learning_engine"} → 450ms (SLOW!)
# {handler="safety_validator"} → 2ms (fast)

# Verdict: learning_engine is bottleneck
# It's taking 450ms to process each event,
# but events arrive every 40ms.
# → Queue backs up!
```

### Step 2: Recover (< 5 minutes)

```bash
# Option A: Disable slow subscriber temporarily
echo "Disabling learning_engine as subscriber (quickfix)..."
corvin config set features.learning_engine_subscriber_enabled=false
systemctl restart corvin-service

# Monitor queue depth
watch -n 2 'corvin metrics query "corvin_context_bus_queue_depth"'
# Should drop from 100 to 0 within 30 seconds

# Check processing rate
corvin metrics query 'rate(corvin_events_processed_total[1m])'
# Should now match publishing rate (~20 events/sec)

# Option B: If Option A doesn't work, restart context bus
corvin restart-subsystem context_bus
```

### Step 3: Root Cause (Within 24h)

```bash
# 1. Understand why learning_engine is slow
# Likely: storing too many events to disk (I/O bottleneck)

# 2. Optimize the slow path
# - Batch disk writes (write every 100 events, not every event)
# - Use async I/O instead of blocking I/O
# - Profile with: corvin profile learning_engine --duration=60s

# 3. Test the optimization
# Simulate event storm: 100 events/sec
# Verify queue doesn't overflow

# 4. Re-enable subscriber
corvin config set features.learning_engine_subscriber_enabled=true
```

---

## Section 6: Decision Tree — When to Rollback (5 minutes)

### Use This Tree to Decide

```
Has the incident been caused by recent code deployment?
│
├─ YES → Was incident severity CRITICAL?
│       (e.g., audit chain corruption, data loss risk)
│       │
│       ├─ YES: ROLLBACK IMMEDIATELY
│       │       Command: git revert <commit>
│       │       Deploy: corvin update <previous-stable-version>
│       │       Monitor: 30 minutes
│       │
│       └─ NO: Try quick fix first
│               └─ Did restart/config change resolve it?
│                   ├─ YES: Keep deployed, schedule hotfix
│                   └─ NO: ROLLBACK

└─ NO (incident in existing code)
    └─ Try restart/cleanup procedures
        └─ Did resolve?
            ├─ YES: Keep as-is, investigate later
            └─ NO: Check if affecting customers
                    ├─ YES: ROLLBACK to last known good
                    └─ NO: Schedule investigation
```

### Rollback Procedure

```bash
#!/bin/bash
set -e

echo "[$(date)] Starting rollback procedure..."

# 1. Identify current vs. target version
CURRENT=$(git rev-parse HEAD)
echo "Current: $CURRENT"

# 2. Get last known good
LAST_GOOD=$(git tag | grep v0.2 | sort -V | tail -1)
echo "Target: $LAST_GOOD"

# 3. Create rollback branch
git branch rollback/$CURRENT-to-$LAST_GOOD
git reset --hard $LAST_GOOD

# 4. Stop service
systemctl stop corvin-service

# 5. Re-initialize databases if needed
corvin reset --keep-audit  # Keep audit trail (immutable)

# 6. Restart service
systemctl start corvin-service

# 7. Verify
corvin health check
curl -f http://localhost:8765/health

echo "[$(date)] Rollback complete!"
echo "Rolled back from: $CURRENT"
echo "Rolled back to: $LAST_GOOD"
echo "Audit trail preserved: ~/.corvin/tenants/*/audit.jsonl (unchanged)"
```

---

## Quick Reference: Incident Checklist

```
[ ] Alert fires
    ↓
[ ] Acknowledge alert in Prometheus/PagerDuty
[ ] Post to #incident-response Slack
[ ] Create incident ticket (Jira/GitHub)
[ ] Assign on-call engineer
    ↓
[ ] Gather baseline metrics (error rate, latency, memory)
[ ] Check error log for patterns
[ ] Identify affected subsystem(s)
[ ] Determine root cause
    ↓
[ ] Execute recovery procedure
    ├─ Restart subsystem (< 30s)
    ├─ OR cleanup database
    ├─ OR rollback deployment
    └─ OR escalate (audit corruption only)
    ↓
[ ] Verify audit chain integrity (CRITICAL)
[ ] Verify all subsystems healthy
[ ] Run smoke test
[ ] Declare incident RESOLVED
    ↓
[ ] Monitor for 30 minutes (error rate, latency, memory)
[ ] Update incident ticket with timeline
[ ] Post resolution to Slack
    ↓
[ ] Schedule root-cause analysis (< 24h)
[ ] File GitHub issue if needed
[ ] Update playbook with learnings
```

---

## When to Escalate

**Page On-Call Immediately:**
- Error rate > 2% sustained > 5 min
- Any CRITICAL alert
- Audit chain corruption (escalate to maintainer)
- Memory > 80% of system limit
- Service unable to start

**Can Wait Until Business Hours:**
- Single subsystem slow (>200ms latency)
- Cost estimate error > 20%
- Strategy success rate < 50%
- Policy violations < 100/hour

---

**Next Module:** [Operational Runbooks Module](MODULE-4-RUNBOOKS.md) (45 min)  
**Time Spent:** 60 minutes  
**Status:** Ready to handle production incidents ✅
