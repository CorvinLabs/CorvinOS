# CorvinOS Incident Response Runbook

**Version:** 1.0  
**Date:** 2026-09-22  
**Scope:** 12 common incidents + response procedures  
**Audience:** On-call operators, incident commanders

---

## Table of Contents

1. [Incident Classification](#incident-classification)
2. [Response Procedures](#response-procedures)
3. [Communication Template](#communication-template)
4. [Post-Incident](#post-incident)

---

## Incident Classification

| Severity | Response Time | Scope | Examples |
|---|---|---|---|
| **P1 - Critical** | 5 min | Service down | API server down, audit chain broken, data loss |
| **P2 - High** | 15 min | Partial outage | Skills failing, learning loop stuck, slow API |
| **P3 - Medium** | 1 hour | Degraded | Console UI issues, plugin load failed |
| **P4 - Low** | 4 hours | Informational | Documentation typo, minor configuration drift |

---

## Response Procedures

### P1-01: API Server Down (Critical)

**Symptoms:**
- `curl http://localhost:8765/v1/health` returns "Connection refused"
- Users cannot access console or API
- All API-dependent skills fail

**Detection:**
```bash
# Health check fails
curl -s http://localhost:8765/v1/health | jq .
# Returns: Connection refused (error)
```

**Response (⏱ 5 min SLA):**

1. **Verify the problem (1 min)**
   ```bash
   # Check if service is running
   systemctl --user status corvin-webui
   
   # Expected: inactive (dead) or errors
   ```

2. **Restart the service (2 min)**
   ```bash
   # Restart API server
   systemctl --user restart corvin-webui
   
   # Wait 10 seconds for startup
   sleep 10
   
   # Verify it's up
   curl -s http://localhost:8765/v1/health | jq .
   # Expected: {"status": "healthy", ...}
   ```

3. **If restart fails (3 min)**
   ```bash
   # Check logs for errors
   journalctl --user -u corvin-webui -n 50 --no-pager
   
   # Common errors:
   # - "Port 8765 already in use" → kill process on that port
   # - "ImportError: No module named ..." → missing dependency
   # - "Permission denied: ~/.corvin/" → fix directory permissions
   ```

4. **If still down, escalate (5 min)**
   ```bash
   # Check system resources
   top -bn1 | head -20
   df -h
   
   # If disk full (>95%), clear audit trail:
   corvin audit archive --before=2026-06-22 --tenant=_default
   
   # If out of memory, restart everything:
   systemctl --user restart corvin-webui corvin-learning-processor
   
   # Verify
   curl -s http://localhost:8765/v1/health | jq .
   ```

5. **Alert if recovery fails**
   - Page on-call engineer
   - Alert escalation: on-call → tech lead → CTO
   - See [Communication Template](#communication-template)

**Recovery Confirmation:**
```bash
# All of these should succeed:
curl -s http://localhost:8765/v1/health | jq .status  # "healthy"
curl -s http://localhost:8765/v1/skills | jq '.skills | length'  # > 0
curl -s http://localhost:8765/v1/audit/trail?limit=1 | jq '.events | length'  # > 0
```

**Post-Incident:**
- Document root cause
- Create issue if bug found
- Schedule post-mortem if affecting SLA

---

### P1-02: Audit Chain Broken (Critical)

**Symptoms:**
- Audit verification fails: "Hash mismatch at event XXXX"
- New operations cannot be recorded
- System logs: "audit_chain_broken" alerts

**Detection:**
```bash
corvin audit verify-chain --tenant=_default
# Expected failure: "Chain height 1234, broken at event 1001"
```

**Response (⏱ 5 min SLA):**

1. **Acknowledge and notify (1 min)**
   - This is a **HARD STOP** situation
   - Page on-call engineer immediately
   - Alert: "CRITICAL: Audit chain broken, system integrity compromised"

2. **Attempt repair (2 min)**
   ```bash
   corvin audit repair --tenant=_default
   
   # Verify repair succeeded
   corvin audit verify-chain --tenant=_default
   # Expected: "Chain verified successfully"
   ```

3. **If repair fails, nuclear option (5 min)**
   ```bash
   # ⚠️ WARNING: This clears audit history
   
   # Backup existing audit
   cp ~/.corvin/audit.jsonl ~/audit-backup-$(date +%Y%m%d-%H%M%S).jsonl
   
   # Reinitialize
   rm ~/.corvin/audit.jsonl
   corvin bootstrap --tenant=_default
   
   # Restore plugins (if present)
   cp -r ~/plugins-backup/* ~/.corvin/plugins/
   
   # Verify
   corvin audit verify-chain --tenant=_default
   ```

4. **Escalate to engineering**
   - Save audit backup for analysis
   - Create critical bug report
   - Schedule emergency post-mortem

**Post-Incident:**
- Root cause analysis required
- Code audit for potential data corruption
- Backup/recovery procedure test

---

### P2-01: Learning Processor Stuck (High)

**Symptoms:**
- Learning feedback events pile up (queue > 100)
- Skill confidence doesn't improve
- Learning logs show repeated errors

**Detection:**
```bash
corvin learning status
# Event queue: 250 (should be < 10)
# Last optimization: > 1 hour ago
```

**Response (⏱ 15 min SLA):**

1. **Assess the situation (2 min)**
   ```bash
   corvin learning status
   corvin learning logs --tail=50 | grep -i error
   ```

2. **Try soft restart (5 min)**
   ```bash
   corvin learning restart
   sleep 5
   corvin learning status
   
   # Check queue is processing
   corvin learning status | grep "Event queue"
   # Should decrease over time
   ```

3. **If still stuck, flush and restart (10 min)**
   ```bash
   corvin learning flush-queue --force
   systemctl --user restart corvin-learning-processor
   sleep 10
   
   # Verify
   corvin learning status
   ```

4. **If still failing, disable and alert (15 min)**
   ```bash
   corvin learning disable --reason="processor_stuck"
   
   # Alert: Learning loop disabled, manual intervention needed
   # Page on-call engineer
   ```

---

### P2-02: Skill Execution Timeout (High)

**Symptoms:**
- Skill execution returns "timeout after 30s"
- Multiple skill requests failing
- Latency > 30 seconds for simple tasks

**Detection:**
```bash
corvin skill execute assistant.quick_fix --input="test" 2>&1
# Error: Timeout after 30 seconds
```

**Response (⏱ 15 min SLA):**

1. **Check if skill is running (2 min)**
   ```bash
   corvin skill status assistant.quick_fix
   ps aux | grep -i quick_fix
   ```

2. **Increase timeout and retry (5 min)**
   ```bash
   corvin skill execute assistant.quick_fix \
     --input="test" \
     --timeout=60
   
   # If succeeds → likely just slow, monitor
   # If still times out → skill is hung
   ```

3. **Kill hung skill process (10 min)**
   ```bash
   # Find skill process
   ps aux | grep corvin | grep -v grep
   
   # Kill it
   kill -9 <PID>
   
   # Restart skill
   corvin skill restart assistant.quick_fix
   
   # Verify
   corvin skill execute assistant.quick_fix --input="test"
   ```

4. **If skill keeps timing out**
   ```bash
   # Disable until fixed
   corvin skill disable assistant.quick_fix
   
   # Inform users
   # Page on-call engineer
   ```

---

### P2-03: API Latency Spike (High)

**Symptoms:**
- API responses taking > 3 seconds (normal: < 500ms)
- User complaints: "Console is slow"
- Load testing shows degraded performance

**Detection:**
```bash
# Measure latency
for i in {1..5}; do
  curl -w "Time: %{time_total}s\n" -o /dev/null -s \
    http://localhost:8765/v1/health
done
# All should be < 0.5s; if > 3s, latency spike
```

**Response (⏱ 15 min SLA):**

1. **Check system resources (2 min)**
   ```bash
   top -bn1 | head -20
   free -h
   df -h
   ```

2. **If CPU high (> 80%)**
   ```bash
   # Find process using CPU
   top -bn1 | head -5
   
   # If API server is responsible:
   systemctl --user restart corvin-webui
   ```

3. **If memory high (> 80%)**
   ```bash
   # Clear caches
   corvin cache clear-all
   
   # Archive old audit trail
   corvin audit archive --before=2026-06-22
   
   # Restart service
   systemctl --user restart corvin-webui
   ```

4. **If disk usage high**
   ```bash
   du -sh ~/.corvin/*
   
   # Archive old data
   corvin archive cleanup --older-than=90d
   ```

---

### P3-01: Console UI Blank Page (Medium)

**Symptoms:**
- Browser shows blank white page at `/console`
- No errors in console (F12)
- API server is up (`/v1/health` returns 200)

**Detection:**
```bash
# API is up but console fails
curl -s http://localhost:8765/v1/health | jq .status  # "healthy"
curl -s http://localhost:8765/console | head -10  # Blank HTML
```

**Response (⏱ 1 hour SLA):**

1. **Clear browser cache (5 min)**
   ```bash
   # Firefox: Ctrl+Shift+Del → All time → Delete
   # Chrome: Ctrl+Shift+Del → All time → Delete
   # Safari: Develop → Clear website data
   
   # Hard refresh
   Ctrl+Shift+R (Cmd+Shift+R on macOS)
   ```

2. **Rebuild console (15 min)**
   ```bash
   cd core/console/corvin_console/web-next
   rm -rf dist/ node_modules/.vite/
   npm run build
   
   # Restart API server
   systemctl --user restart corvin-webui
   
   # Test
   curl -s http://localhost:8765/console | grep -i "<title>"
   # Should have actual HTML, not blank
   ```

3. **If rebuild fails**
   ```bash
   # Check for build errors
   npm run build 2>&1 | grep -i error
   
   # Fix dependency issues
   npm install
   npm run build
   ```

---

### P3-02: Plugin Load Failed (Medium)

**Symptoms:**
- Plugin doesn't appear in marketplace
- Plugin logs show "ImportError" or similar
- Plugin status: "inactive" with error

**Detection:**
```bash
corvin plugin status video-producer
# Status: inactive
# Error: ModuleNotFoundError: No module named 'cv2'
```

**Response (⏱ 1 hour SLA):**

1. **Identify missing dependency (5 min)**
   ```bash
   corvin plugin status video-producer | grep -i error
   
   # Example output: ModuleNotFoundError: No module named 'cv2'
   ```

2. **Install dependency (10 min)**
   ```bash
   pip install opencv-python
   
   # Verify
   python3 -c "import cv2; print(cv2.__version__)"
   ```

3. **Reload plugin (5 min)**
   ```bash
   corvin plugin disable video-producer
   corvin plugin enable video-producer
   
   # Verify
   corvin plugin status video-producer
   # Should be: "active"
   ```

---

### P4-01: Slow Skill Response (Low)

**Symptoms:**
- Skill execution taking 2-3 seconds (normal: < 500ms)
- Only one or two skills affected
- No user impact

**Detection:**
```bash
# Measure latency
time corvin skill execute assistant.cost_optimizer --input="test"
# real 0m2.345s (should be < 0.5s)
```

**Response (⏱ 4 hour SLA, can defer):**

1. **Monitor and benchmark (ongoing)**
   ```bash
   # Run benchmark
   corvin skill benchmark assistant.cost_optimizer --runs=10
   
   # Track metrics over time
   watch -n 60 'corvin skill status assistant.cost_optimizer | grep latency'
   ```

2. **If getting slower, investigate**
   ```bash
   corvin skill optimize --skill=assistant.cost_optimizer
   ```

---

## Communication Template

### Initial Alert

```
[INCIDENT ALERT - P<severity>] <service name> <status>

Service: CorvinOS <component>
Severity: P<N> - <level>
Status: <ongoing/investigating/recovering>
Impact: <X users/Y% functionality>
ETA: <timestamp>

Details:
- Symptoms: <what users see>
- Affected services: <list>
- Root cause: <if known>

Next update: <in X minutes>

On-call: <name> · Slack: @<oncall>
```

### Update

```
[INCIDENT UPDATE - P<severity>] <service name>

Status: <investigating/recovering/resolved>
Progress: <% or action taken>
ETA: <new estimate>

Latest action: <what we did>
Next step: <what we're doing>

On-call: <name>
```

### Resolution

```
[INCIDENT RESOLVED - P<severity>] <service name>

Service: CorvinOS <component>
Duration: <HH:MM:SS>
Affected: <X users/Y transactions>
Root cause: <brief explanation>

Actions taken:
- <action 1>
- <action 2>

Follow-up:
- Post-mortem: <date/time>
- Ticket: <issue URL>
- Prevention: <what we'll do>

Thanks for your patience! 🙏
```

---

## Post-Incident

### Immediate (< 24 hours)

1. **Create incident report**
   - What happened
   - When it started/ended
   - Root cause
   - Impact (users, duration, services)

2. **Create follow-up issue**
   - Link to incident report
   - Root cause fix (if applicable)
   - Prevention measures

3. **Alert team**
   - Send incident summary to Slack
   - Schedule post-mortem

### Short-term (< 1 week)

1. **Post-mortem meeting**
   - Review incident timeline
   - Root cause analysis
   - Assign prevention tasks

2. **Implement fixes**
   - Code changes (if applicable)
   - Monitoring improvements
   - Runbook updates

### Long-term (< 1 month)

1. **Test improvements**
   - Run chaos engineering tests
   - Verify incident won't recur
   - Load testing

2. **Update documentation**
   - Update this runbook
   - Update troubleshooting guide
   - Update runbook with lessons learned

---

## Quick Reference

| Incident | P | SLA | First Action |
|---|---|---|---|
| API down | P1 | 5 min | `systemctl restart corvin-webui` |
| Audit broken | P1 | 5 min | `corvin audit repair` |
| Learning stuck | P2 | 15 min | `corvin learning restart` |
| Skill timeout | P2 | 15 min | Kill skill process, restart |
| API latency spike | P2 | 15 min | Check resources, restart service |
| Console blank | P3 | 1 h | Clear cache, rebuild, refresh |
| Plugin failed | P3 | 1 h | Install dependency, reload |
| Slow skill | P4 | 4 h | Monitor, benchmark, optimize |

---

**Last Updated:** 2026-09-22  
**Version:** 1.0.0
