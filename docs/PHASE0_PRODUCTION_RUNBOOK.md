# Phase 0 Production Runbook: Daily Operations

**Status:** 🟢 **PRODUCTION READY**  
**Date:** 2026-09-22  
**Purpose:** Operational procedures for Phase 0 (foundation layers)  
**Audience:** On-call operators, SRE team, incident responders

---

## Daily Operations Checklist

Run this checklist **every morning** (06:00 UTC) before Phase 1 work begins.

### 1. Liveness Check (2 minutes)
```bash
# Is the process responding?
curl -s http://127.0.0.1:8765/health/live
# Expected: HTTP 200
# If FAIL: System not running. See "System Won't Boot" section below.
```

### 2. Readiness Check (2 minutes)
```bash
# Can we serve traffic?
curl -s http://127.0.0.1:8765/health/ready | jq .
# Expected:
# {
#   "ready": true,
#   "checks": {
#     "audit_chain": "✅",
#     "compliance_gates": "✅", 
#     "learning": "✅",
#     "plugins": "✅",
#     "routes": "✅"
#   }
# }

# If any check is NOT ✅:
#   1. Note which check failed
#   2. See "Readiness Check Failures" section below
#   3. Do not proceed with Phase 1 work until all checks pass
```

### 3. Audit Chain Verification (1 minute, automated daily 03:00 UTC)
```bash
# Check if daily audit runs successfully
journalctl --user -u corvin-audit-verify.timer | tail -5
# Expected: 
#   Sep 22 03:00:07 laptop systemd[..]: Started Audit Chain Verification.
#   Sep 22 03:00:08 laptop audit-verify[..]: ✅ Chain height 142857, all verified

# If FAIL: Check was never run
# Solution: Manually trigger:
#   systemctl --user start corvin-audit-verify.service
#   Wait 30s, check result:
#   journalctl --user -u corvin-audit-verify.service | tail -3
```

### 4. Compliance Gates Status (1 minute)
```bash
# Are all 6 gates active and fail-closed?
corvin gates health-check --all
# Expected: ✅ All 6 gates active and fail-closed

# If FAIL: One or more gates are offline
# See "Compliance Gate Failure" section below
```

### 5. Plugin Status (1 minute)
```bash
# Are all plugins loaded and error-free?
corvin plugins status
# Expected:
#   compliance:  ✅ LOADED (bootstrap layer, non-disableable)
#   core:        ✅ LOADED (3 subsystems, 0 errors)
#   bundled:     ✅ LOADED (6 plugins, CPU <2%)

# If any plugin shows ERROR: See "Plugin Failure" section below
```

### 6. Learning Loop Status (1 minute)
```bash
# Is the learning loop running and persisting events?
corvin learning status --tenant=_default
# Expected:
#   Events today: >100 (depends on workload)
#   Optimizer converged: 2-3/5 skills (depends on feedback)
#   Feedback latency p99: <100ms

# If "Events today: 0" after 1 hour of work: See "Learning Loop Silence" section
```

### 7. System Resources (1 minute)
```bash
# Are we within resource limits?
systemctl --user status corvin-webui.service | grep -E "Memory:|CPU:"
# Expected:
#   Memory: 500M (should be <1G)
#   CPU: <5%

# If Memory: >2G or CPU: >50%: See "Resource Exhaustion" section below
```

### ✅ Morning Checklist Complete
If all 7 checks pass, the system is healthy. Proceed with Phase 1 work.

**If ANY check fails:** Do NOT proceed with Phase 1 work. Debug using sections below.

---

## Weekly Health Review (Friday 14:00 UTC)

Run this review **every Friday afternoon** to catch trends.

### 1. Performance Analysis (15 minutes)

```bash
# Get this week's metrics
corvin stats weekly
# Output:
#   Requests: 12,345 total
#   Latency p50: 42ms
#   Latency p99: 287ms (should be <500ms)
#   Error rate: 0.03% (should be <0.1%)
#   Uptime: 99.98%

# Interpretation:
#   p99 < 500ms ✅ Good
#   error_rate < 0.1% ✅ Good
#   uptime > 99.9% ✅ Excellent

# If p99 > 500ms or error_rate > 0.1%:
#   1. Check the "Issue Patterns" section below
#   2. Investigate recent changes (git log --since=1w)
#   3. File a bug if needed
```

### 2. Learning Convergence (10 minutes)

```bash
# Which Skills are converging?
corvin skills convergence --weekly
# Expected output:
#   os.delegation_router:
#     Confidence: 0.83 → 0.87 (↑4%)
#     Feedback count: 145
#     Status: CONVERGING ✅
#
#   os.context_adapter:
#     Confidence: 0.0 (not wired yet)
#     Status: NOT_WIRED 🟡

# Interpretation:
#   CONVERGING = confidence trending up (learning working)
#   FLAT = confidence stalled (stuck, needs investigation)
#   NOT_WIRED = no calls yet (expected for some skills)

# If a Skill shows FLAT:
#   1. Check feedback: corvin skills feedback --skill=<name>
#   2. Are users giving consistent feedback?
#   3. If feedback is contradictory, mark it as "data quality issue"
#   4. Filter out bad feedback or provide more training
```

### 3. Audit Trail Summary (15 minutes)

```bash
# How many events this week?
corvin audit summary --since=7d
# Expected output:
#   Total events: 12,000
#   Audit events: 2,000 (chain writes, verifications)
#   Skill events: 5,000 (router decisions, outcomes)
#   Compliance events: 200 (gate checks, denials)
#   Plugin events: 400 (loads, unloads, errors)
#   Learning events: 4,400 (feedback, optimizer updates)

# Denied requests (gates triggered)?
grep "gate_denied" ~/.corvin/audit.jsonl | jq -c 'select(.timestamp >= now - 7 * 86400)' | wc -l
# Expected: <10 per week (gates should rarely trigger)
# If >10: Investigate what's being blocked

# Compliance violations?
grep "compliance_violation\|consent_denied\|house_rule_denied" ~/.corvin/audit.jsonl \
  | jq -c 'select(.timestamp >= now - 7 * 86400)' | wc -l
# Expected: 0 (system is working correctly)
# If >0: Review each violation, understand the pattern
```

### 4. Capacity Planning (10 minutes)

```bash
# Storage growth?
du -sh ~/.corvin/
# Expected: ~100 MB after 1 month (for single tenant)
# Growth should be ~20 MB/week for Phase 1 volume

# Audit chain size?
wc -l ~/.corvin/audit.jsonl
# Expected growth: ~2,000 lines/day
# Weekly: ~14,000 new lines

# Disk space remaining?
df -h ~/.corvin/
# Expected: >10 GB free (for 5+ years of data)
# If <5 GB free: Plan upgrade
```

### 5. Team Feedback (5 minutes)

```bash
# Gather feedback from team using Phase 0:
# - Any operational pain points?
# - Any confusing alerts?
# - Any blind spots in monitoring?
# - Any changes needed for Phase 1?

# Document findings in PHASE0_RUNBOOK_FEEDBACK.md
```

---

## Scheduled Maintenance

### Daily (03:00 UTC)

- **Audit chain verification** (automated)
  ```bash
  systemctl --user start corvin-audit-verify.service
  # Verifies hash-chain, reports to log
  ```

- **Learning optimizer update** (automated)
  ```bash
  systemctl --user start corvin-learning-optimize.service
  # Reads feedback events, updates skill config
  ```

- **Backup of audit trail** (automated)
  ```bash
  systemctl --user start corvin-backup-audit.service
  # Copies audit.jsonl to S3/GCS daily backup
  ```

### Weekly (Sunday 02:00 UTC)

- **Full compliance audit** (automated)
  ```bash
  corvin audit verify-compliance --full
  # Checks all 6 gates, tenant isolation, encryption
  ```

- **Plugin dependency check** (automated)
  ```bash
  corvin plugins verify-deps
  # Detects circular deps, missing versions
  ```

- **Backup integrity test** (automated)
  ```bash
  corvin backup verify --latest
  # Tests restore from latest backup
  ```

### Monthly (1st of month, 04:00 UTC)

- **Capacity forecast** (automated)
  ```bash
  corvin capacity forecast --months=12
  # Predicts storage/CPU/RAM needs
  ```

- **Security key rotation** (semi-automatic)
  ```bash
  # Manual trigger required:
  corvin secrets rotate-keys
  # Backs up old keys, generates new ones, re-encrypts data
  # Runtime: ~5 min, no downtime
  ```

- **Audit chain retention cleanup** (automatic, if configured)
  ```bash
  corvin audit delete --before=5y
  # Deletes audit events older than 5 years (keeps chain intact)
  ```

---

## On-Call Procedures

### When Paged (Incident)

1. **Acknowledge the alert** (within 2 minutes)
   ```bash
   # Check what alert fired
   journalctl --user -p err -n 20
   # Look for ERROR or CRITICAL messages
   ```

2. **Assess severity**
   - **CRITICAL:** System won't boot, audit chain broken, tenant isolation violated
   - **HIGH:** Compliance gate offline, learning stopped
   - **MEDIUM:** Plugin crashed, performance degraded
   - **LOW:** Minor monitoring alert, recoverable error

3. **Escalation path**
   - CRITICAL: Page SRE lead + security team + manager (now)
   - HIGH: Page SRE lead + manager (within 30 min)
   - MEDIUM: Create ticket, notify team at standup
   - LOW: Create ticket, resolve next business day

### Common Incidents & Responses

#### Incident: System Won't Boot

**Symptom:** `systemctl --user status corvin-webui` → failed to boot  
**Cause:** Audit chain broken, config corrupted, or secrets missing

**Debug:**
```bash
# Check boot log for errors
journalctl --user -u corvin-webui -n 50 --output=short
# Look for lines with ERROR or CRITICAL

# Common errors:
# 1. "Audit chain broken at event 12345"
#    → Hash chain verification failed at boot tripwire
#
# 2. "Config file not found or unreadable"
#    → ~/.corvin/tenant.corvin.yaml missing or wrong permissions
#
# 3. "Secrets key not found"
#    → ~/.corvin/secrets/keyring.json missing or corrupted
```

**Response:**
```bash
# Option 1: Restore from backup (safest)
systemctl --user stop corvin-webui
corvin backup restore --latest
systemctl --user start corvin-webui
# Wait 30s for boot to complete
curl -s http://127.0.0.1:8765/health/live

# Option 2: Fix audit chain manually (risky, only if backup unavailable)
# DO NOT do this without SRE lead approval
# Contact: page-security-on-call

# Option 3: Hard reset (last resort, data loss possible)
# Wipes ~/.corvin/, boots fresh
# DO NOT do this without explicit manager approval
corvin factory-reset --unsafe-yes
```

**Prevention:**
- Backup tested daily (automated)
- Audit chain verified daily (automated)
- Config stored in version control (git)

---

#### Incident: Compliance Gate Offline

**Symptom:** `curl http://127.0.0.1:8765/health/ready` → "compliance_gates": "❌"  
**Cause:** Gate plugin crashed, gate configuration invalid, or gate logic error

**Debug:**
```bash
# Which gate is offline?
corvin gates health-check --verbose
# Output will show which gate(s) failed

# Check plugin status
corvin plugins status | grep compliance
# Expected: compliance ✅ LOADED

# Check gate logs
journalctl --user -u corvin-webui | grep -i gate | tail -10

# Common causes:
# 1. Plugin crashed → restart
# 2. Config invalid → fix config
# 3. Dependency missing → check plugin deps
```

**Response:**
```bash
# Step 1: Do NOT serve traffic (readiness check failing)
# System will automatically reject all requests if gate is down

# Step 2: Restart the compliance layer
systemctl --user restart corvin-webui
# Wait 10s for restart

# Step 3: Verify gate is back online
corvin gates health-check --all
# Expected: All 6 gates ✅

# Step 4: If still offline after restart
# → Manual investigation required, page SRE lead
```

**Prevention:**
- Gates have no dependencies (load-bearing, must always work)
- Gates tested at boot (fail-closed means boot fails if gate broken)
- No feature flags for gates (cannot be disabled)

---

#### Incident: Learning Loop Silence

**Symptom:** `corvin learning status` → "Events today: 0" after >1 hour of work  
**Cause:** EventStore corrupted, learning plugin crashed, or network I/O error

**Debug:**
```bash
# Is the learning plugin loaded?
corvin plugins status | grep learning
# Expected: learning ✅ LOADED (in core layer)

# Can we write events?
corvin learning test-write
# Expected: Event 12345 written successfully

# Are events persisting to disk?
ls -la ~/.corvin/learning/events/
# Should have files named: events-2026-09-22.jsonl, events-2026-09-21.jsonl, etc.

# Check event count
wc -l ~/.corvin/learning/events/*.jsonl

# Check for errors
journalctl --user -u corvin-webui | grep -i learning | tail -10
```

**Response:**
```bash
# Option 1: Restart learning plugin (safe)
systemctl --user restart corvin-webui
# Wait 30s

# Option 2: Rebuild EventStore from audit trail (advanced)
# If event files are corrupted, rebuild from audit events
# DO NOT do this without SRE lead approval

# Option 3: Restore from backup
corvin backup restore --latest
```

**Prevention:**
- EventStore writes are audit-first (chain verified before disk write)
- Failure-safe design: if write fails, event is dropped (system continues)
- Events are immutable and append-only (no corruption risk from rewrite)

---

#### Incident: Plugin Crash Loop

**Symptom:** Plugin keeps restarting (more than 3 restarts in 5 minutes)  
**Cause:** Plugin code error, missing dependency, or invalid configuration

**Debug:**
```bash
# Which plugin is crashing?
journalctl --user -u corvin-webui | grep "plugin.*crash\|plugin.*error" | tail -5

# Check plugin status
corvin plugins status --verbose | grep ERROR

# Check plugin logs
corvin plugins logs --plugin=<name> --tail=20

# Common causes:
# 1. Dependency missing (import error)
# 2. Configuration invalid (schema mismatch)
# 3. Code error (runtime exception)
```

**Response:**
```bash
# Step 1: Disable the crashing plugin (safe, if not compliance layer)
corvin plugins disable <plugin-name>
# (If it's in compliance layer, cannot disable—see CRITICAL incident)

# Step 2: Restart system
systemctl --user restart corvin-webui

# Step 3: Verify system is healthy
curl -s http://127.0.0.1:8765/health/ready | jq .

# Step 4: Investigate plugin error
# Open a bug ticket with the plugin logs
# Contact plugin maintainer
```

**Prevention:**
- Plugins run in subprocess (crash isolated, doesn't crash system)
- Plugin watchdog monitors crashes (auto-restart up to 3x in 5 min, then stop)
- Compliance layer plugins cannot crash (non-disableable, must work)

---

#### Incident: Tenant Data Leak

**Symptom:** Operator discovers data from another tenant in audit trail  
**Cause:** Cross-tenant read in query, missing `tenant_id` filter, or privilege escalation

**Debug:**
```bash
# CRITICAL: Do NOT investigate further yourself
# → Page security-on-call immediately

# What we will do:
# 1. Enter system lockdown (deny all new requests)
# 2. Audit the query that leaked data
# 3. Check if leak was one-off or systemic
# 4. Restore from known-good backup if systemic
# 5. Rotate encryption keys
# 6. Notify affected tenants
```

**Prevention:**
- Every query filtered by `tenant_id` (code review enforces this)
- Tenant isolation verified daily (automated)
- Auth tokens carry `tenant_id` (cannot be forged)
- Boot tripwire checks tenant scope (refuses boot if cross-tenant refs detected)

---

## Escalation Contacts

**24/7 On-Call:**
1. **Ops Lead** (primary): On-call schedule in #ops-chat
2. **SRE Team** (backup): Page SRE on-call via PagerDuty
3. **Security Team** (CRITICAL): Page security-on-call for compliance violations
4. **Manager** (urgent): Page manager for CRITICAL issues

**Communication:**
- Incident channel: #incident-response (Slack)
- Post mortem docs: Google Drive → Incident Reports folder
- Status page: https://status.corvin-labs.com (update every 30 min)

---

## Issue Patterns & Root Causes

### Pattern: High Latency (p99 > 500ms)

**Possible causes:**
1. Learning optimizer update running (heavy computation)
   - Response: Tune optimizer schedule (run off-peak)
2. Too many plugins loaded (startup time increases)
   - Response: Disable unnecessary plugins
3. Audit chain getting large (slower hash verification)
   - Response: Archive old audit events (keep recent 1 year online)
4. Resource contention (CPU/memory/disk I/O)
   - Response: Vertical scale machine (more resources)

**Debug:**
```bash
# Is optimizer running?
ps aux | grep -i optimizer
# If yes: kill it (systemctl stop corvin-learning-optimize)

# How many plugins loaded?
corvin plugins status | wc -l

# Audit chain size?
wc -l ~/.corvin/audit.jsonl

# System resources?
free -h && df -h ~/.corvin/
```

---

### Pattern: High Error Rate (>0.1%)

**Possible causes:**
1. Compliance gate denying requests (expected behavior)
   - Response: Check gate logic, ensure config is correct
2. Backend bug (route not handling error)
   - Response: Check logs, file bug, update code
3. Upstream service unavailable (API, model, database)
   - Response: Check external services, add retry logic

**Debug:**
```bash
# What errors are occurring?
journalctl --user -u corvin-webui -p err -n 20

# Are they gate denials?
grep "gate_denied" ~/.corvin/audit.jsonl | wc -l

# Are they 5xx errors?
journalctl --user -u corvin-webui | grep "500\|502\|503" | wc -l

# Sample an error
journalctl --user -u corvin-webui -p err -n 1 | jq .
```

---

### Pattern: Memory Leak (RAM >1G)

**Possible causes:**
1. Plugin holding references (not releasing memory)
   - Response: Restart plugin
2. Learning optimizer state unbounded
   - Response: Limit state size, archive old events
3. Skill cache growing without eviction
   - Response: Add cache eviction policy

**Debug:**
```bash
# Current memory usage?
ps aux | grep corvin-webui | awk '{print $6}'
# (column 6 is RSS in KB)

# Memory over time?
# Check if trending up
# If trending flat: not a leak
# If trending up: investigate which component
```

---

## Disaster Recovery Drills

**Every month:** Run a disaster recovery drill (Saturday 10:00 UTC)

### Drill 1: Backup Restore (30 minutes)

```bash
# Scenario: Audit chain corrupted, must restore from backup
# Goal: Verify backup is usable, restoration takes <5 min

# Steps:
# 1. Stop system
systemctl --user stop corvin-webui

# 2. Restore from backup
corvin backup restore --latest
# Confirm: Y/N [Y]

# 3. Verify restoration
# - Audit chain integrity check passes?
corvin audit verify-chain

# 4. Boot system
systemctl --user start corvin-webui

# 5. Health check
curl -s http://127.0.0.1:8765/health/ready | jq .ready
# Expected: true

# Document result:
# - Start time: [TIME]
# - End time: [TIME]
# - Duration: [MINUTES]
# - Success: [Y/N]
# - Issues: [if any]
```

### Drill 2: Failover to Standby (45 minutes)

```bash
# Scenario: Primary machine fails, switch to standby
# Goal: Verify standby is ready, failover takes <10 min

# Prerequisites:
# - Standby machine running with replicated audit chain + learning events
# - Standby has same configuration as primary

# Steps:
# 1. Kill primary (simulate failure)
# 2. Switch DNS to standby
# 3. Verify all health checks pass on standby
# 4. Run Phase 1 workload on standby
# 5. Restore primary and rejoin cluster

# Document result:
# - Time to failover: [SECONDS]
# - Data loss: [Y/N]
# - Downtime: [SECONDS]
# - Issues: [if any]
```

---

## Postmortem Template

Every incident (CRITICAL or HIGH severity) requires a postmortem within 24 hours.

**Use this template:**

```markdown
# Postmortem: [Incident Name]

**Date:** 2026-09-22
**Duration:** 14:30 - 15:45 UTC (75 minutes)
**Severity:** CRITICAL / HIGH / MEDIUM / LOW

## Timeline

- 14:30 Alert fired: audit chain verification failed
- 14:31 On-call acknowledged
- 14:35 Root cause identified: audit.jsonl corrupted
- 14:45 Backup restore initiated
- 15:00 System back online
- 15:15 All health checks passing
- 15:45 Incident resolved

## Root Cause

[Describe what went wrong, why it went wrong, and what the chain of events was]

## Impact

- Downtime: 15 minutes
- Users affected: All Phase 0 operators
- Data loss: None (restored from backup)

## Remediation

### Immediate (done in incident)
1. Restored system from 1-hour-old backup
2. Verified audit chain integrity
3. Verified learning events not lost

### Short-term (this week)
1. [ ] Add more verbose logging around audit chain writes
2. [ ] Increase backup frequency from hourly to every 30 min
3. [ ] Add pre-write validation to EventStore

### Long-term (next month)
1. [ ] Implement multi-region replication
2. [ ] Add automatic failover
3. [ ] Upgrade storage backend to more reliable system

## Preventive Actions

- Daily backup integrity test (already automated)
- Better monitoring of EventStore write errors
- Operator training on restore procedures

## Lessons Learned

1. Backup frequency was too low (hourly → 30 min)
2. Audit chain write validation was insufficient
3. Team needed more training on restore procedures

---
```

---

## Phase 0 Complete

✅ All daily operations documented  
✅ Escalation procedures defined  
✅ Common incidents & responses captured  
✅ Scheduled maintenance automated  
✅ Disaster recovery drills quarterly  
✅ Team trained + on-call schedule active

**Next:** PHASE1_ROADMAP.md (Phase 1 features and dependencies)
