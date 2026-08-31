# ADR-0274 Incident Response Runbook

**For:** Week 6 Measurement Phase (2026-08-11 to 2026-08-17)  
**Purpose:** Quick response to common issues during measurement  
**Audience:** On-call operators, measurement team

---

## Quick Reference

| Issue | Severity | Response Time | Action |
|-------|----------|---|--------|
| Service down | 🔴 CRITICAL | <5 min | Restart service |
| Checksum failures | 🔴 CRITICAL | <5 min | Stop measurement, investigate |
| Lock timeout | 🟡 HIGH | <15 min | Check aggregation, restart if hung |
| Measurement gap | 🟡 HIGH | <30 min | Verify tracking enabled, restart collection |
| Slow queries | 🟠 MEDIUM | <1 hour | Monitor latency, optimize if needed |
| Stale lock | 🟠 MEDIUM | <1 hour | Remove .lock file manually |

---

## Incident Types & Responses

### 1. 🔴 Service Crashed or Won't Start

**Symptoms:**
- `pgrep corvin-serve` returns nothing
- HTTP requests timeout
- Logs show FATAL errors

**Immediate Response (5 min):**
```bash
# Check service status
pgrep -f "corvin-serve"
tail -20 ~/.corvin/logs/session.log

# Restart service
corvin stop 2>/dev/null || true
sleep 2
export CORVIN_TELEMETRY_OPTIN=true
export CEL_PHASE4_MEASUREMENT=true
corvin-serve &

# Verify
sleep 3
curl http://localhost:8000/health
```

**If still failing:**
1. Check for locked files: `ls -la ~/.corvin/tenants/_default/learning-queue/*.lock*`
2. Clean stale locks: `rm ~/.corvin/tenants/_default/learning-queue/*.lock* 2>/dev/null || true`
3. Try restart again
4. If still fails → escalate (see **Escalation** below)

**Root Cause Investigation:**
- Check logs for permission errors
- Verify disk space: `df -h ~/.corvin/`
- Check Python version: `python3 --version`
- Run health check: `bash operator/context_engineering/scripts/health-check.sh`

---

### 2. 🔴 Checksum Validation Failures

**Symptoms:**
- Logs show: `Corrupted record ... skipped`
- Queue files unreadable
- Aggregation fails

**Immediate Response (5 min):**
```bash
# Stop measurement
export CEL_PHASE4_MEASUREMENT=false

# Investigate queue
ls -la ~/.corvin/tenants/_default/learning-queue/
file ~/.corvin/tenants/_default/learning-queue/*.jsonl

# Check recent logs
grep "checksum\|corrupted" ~/.corvin/logs/session.log | tail -20

# Count corruption
grep -c "Corrupted record" ~/.corvin/logs/session.log || echo "0"
```

**If <5 corrupted records:**
- Mark as acceptable (skip and continue)
- Log incident ticket
- Resume measurement: `export CEL_PHASE4_MEASUREMENT=true`

**If >5 corrupted records:**
1. Stop service: `corvin stop`
2. Backup affected queue: `cp ~/.corvin/tenants/_default/learning-queue/* /tmp/queue-backup/`
3. Check filesystem: `fsck` or `SMART` status (if available)
4. Restore from backup if available
5. Escalate (data corruption investigation needed)

---

### 3. 🟡 Lock Timeout (Aggregation Hung)

**Symptoms:**
- Aggregation takes >15 minutes
- Logs show: `Failed to acquire lock after 30s`
- Queue files accumulating without processing

**Immediate Response (15 min):**
```bash
# Check for stale locks
ls -la ~/.corvin/tenants/_default/learning-queue/*.lock*
stat ~/.corvin/tenants/_default/learning-queue/.lock 2>/dev/null || echo "No lock"

# Check aggregation process
ps aux | grep -i aggregat
ps aux | grep -i "exclusive\|lock"

# Try to get lock info
lsof 2>/dev/null | grep learning-queue || echo "No open files"
```

**If lock is stale (>30 min old):**
```bash
# Remove stale lock (safe, PID is dead)
rm ~/.corvin/tenants/_default/learning-queue/.lock

# Verify removed
ls ~/.corvin/tenants/_default/learning-queue/.lock 2>&1

# Retry aggregation manually
# (Or just wait, next aggregation will acquire fresh lock)
```

**If lock is recent (<30 min):**
- Wait for aggregation to complete (may be slow but working)
- Monitor: `watch -n 5 'ls -la ~/.corvin/tenants/_default/learning-queue/.lock'`
- If still hung after 1 hour → escalate

---

### 4. 🟡 Measurement Not Collecting Data

**Symptoms:**
- No new files in `~/.corvin/measurement/$(date +%Y-%m-%d)/`
- After 2+ hours, all tracks should have ≥5 records
- Logs don't show `record_prediction`, `record_feedback`, etc.

**Immediate Response (15 min):**
```bash
# Verify directories exist AND are writable (M4 FIX)
ls -la ~/.corvin/measurement/$(date +%Y-%m-%d)/ || echo "Missing today's directory"
touch ~/.corvin/measurement/test && rm ~/.corvin/measurement/test || echo "Not writable"

# Check environment variables
echo $CORVIN_MEASUREMENT_TRACK_UNCERTAINTY
echo $CORVIN_MEASUREMENT_TRACK_FEEDBACK
echo $CORVIN_MEASUREMENT_TRACK_PREFERENCES
echo $CORVIN_MEASUREMENT_TRACK_BUDGET

# Verify hooks are being called (grep for debug logs)
grep "record_prediction\|record_feedback" ~/.corvin/logs/session.log | wc -l

# Check if tasks are actually running
grep "execute_task\|task-[0-9]" ~/.corvin/logs/session.log | wc -l

# NEW (H4 FIX): Check if queue files are being skipped (post-window uploads prevented)
grep "post-window upload detected" ~/.corvin/logs/session.log || echo "No post-window detection"
```

**If PermissionError on queue_dir (M4 FIX):**
1. Queue directory exists but is not writable
2. Check directory: `ls -ld ~/.corvin/measurement/`
3. Fix permissions: `chmod 755 ~/.corvin/measurement/`
4. MeasurementCollector now tests writability at init (fails fast)

**If environment variables not set:**
```bash
export CORVIN_MEASUREMENT_TRACK_UNCERTAINTY=true
export CORVIN_MEASUREMENT_TRACK_FEEDBACK=true
export CORVIN_MEASUREMENT_TRACK_PREFERENCES=true
export CORVIN_MEASUREMENT_TRACK_BUDGET=true

# Re-export and restart service
corvin stop
sleep 2
corvin-serve &
```

**If environment variables set but no data:**
1. Check if tasks are running: `grep "task-" ~/.corvin/logs/session.log`
2. **NEW (H4 FIX):** If tasks exist but files are small: check post-window filtering
   - Aggregation snapshots queue files at window start time
   - Files modified AFTER snapshot are **skipped intentionally** (prevents inconsistent state)
   - This is NOT a bug; it's the H4 snapshot-enforcement fix
3. If no tasks: measurement is working, just no activity (normal)
4. If tasks but no records: hooks not being called → escalate (integration issue)

---

### 5. 🟠 Measurement Track Below Target

**Symptoms:**
- ADR-0270: Confidence accuracy <±5%
- ADR-0271: Learning rate delta not ±0.03
- ADR-0272: User profile recall <0.80
- ADR-0273: Budget/complexity match <0.80

**Immediate Response (30 min):**
```bash
# Gather data
cd ~/.corvin/measurement/$(date +%Y-%m-%d)/

# Count samples
echo "Predictions: $(wc -l < predictions.jsonl)"
echo "Feedback: $(wc -l < feedback.jsonl)"
echo "User choices: $(wc -l < user_choices.jsonl)"
echo "Budget: $(wc -l < budget_allocations.jsonl)"

# Quick stats (if you have jq)
cat predictions.jsonl | jq '.confidence_pred' | sort -n | tail -5
```

**Analysis:**
1. **Sample size:** If <100 samples, wait for more data (track is still ramping up)
2. **Data quality:** Check for outliers or obvious errors
3. **Algorithm:** Verify Bayesian update math, decay weighting

**Actions:**
- If sample size <100: Continue collecting, re-measure tomorrow
- If quality issue: Document in incident log, continue measuring
- If algorithm wrong: Escalate (code review needed)

**Don't stop measurement.** Continue collecting even if one track is low.

---

### 6. 🟠 Stale Lock Files Accumulating

**Symptoms:**
- Multiple `.lock` files present: `ls ~/.corvin/tenants/_default/learning-queue/.lock*`
- Lock files older than 2 hours

**Immediate Response (1 hour):**
```bash
# List all locks
ls -la ~/.corvin/tenants/_default/learning-queue/.lock*

# Check if processes own them
for lock in ~/.corvin/tenants/_default/learning-queue/.lock*; do
    if [ -f "$lock" ]; then
        pid=$(cat "$lock" 2>/dev/null || echo "unknown")
        ps -p "$pid" > /dev/null && echo "$lock: ALIVE" || echo "$lock: DEAD"
    fi
done

# Remove dead locks
find ~/.corvin/tenants/_default/learning-queue/ -name ".lock*" -mtime +0.083 -delete
```

**Cleanup (Safe):**
```bash
# Only delete locks older than 2 hours
find ~/.corvin/tenants/_default/learning-queue/ -name ".lock*" -mmin +120 -delete

# Verify
ls ~/.corvin/tenants/_default/learning-queue/.lock*
```

---

## Escalation Paths

### 🔴 CRITICAL Issues (Escalate Immediately)

**When to escalate:**
- Checksum failures >10 in an hour
- Service crashes repeatedly
- Data corruption detected
- Measurement gap >2 hours with no recovery

**Escalation process:**
1. Stop measurement: `export CEL_PHASE4_MEASUREMENT=false`
2. Preserve logs: `cp -r ~/.corvin/logs /tmp/incident-logs-$(date +%s)`
3. Preserve queue: `cp -r ~/.corvin/tenants/_default/learning-queue /tmp/queue-backup-$(date +%s)`
4. Notify measurement team lead
5. **Do not resume** until incident investigated

**Recovery (After Investigation):**
- If data loss: Restore from backup
- If logic error: Fix code, re-deploy
- If hardware issue: Migrate to new instance

### 🟡 HIGH Issues (Escalate Within 1 Hour)

**When to escalate:**
- Lock timeout persists >30 min
- Track missing data >1 hour
- System running but slow (>50% latency increase)

**Escalation process:**
1. Gather diagnostic data (health check + logs)
2. Notify on-call engineer
3. Attempt standard fixes
4. If not resolved in 1 hour → escalate to team lead

**Recovery:**
- Usually just restart or stale-lock cleanup
- If persists → investigate root cause

---

## Prevention Checklist

**Before Each Day (9am Stand-up):**
- [ ] Service running: `pgrep corvin-serve`
- [ ] No errors overnight: `grep -c ERROR ~/.corvin/logs/session.log`
- [ ] No dangling symlinks: `grep -c "dangling symlink" ~/.corvin/logs/session.log` (H5 FIX: now logged as WARNING)
- [ ] Aggregation ran: Check checkpoint freshness
- [ ] Measurement collecting: 4 files present in today's directory
- [ ] Locks healthy: No stale locks older than 2 hours
- [ ] Disk space: `df -h ~/.corvin/ | grep -v 100%`

**During Day (Hourly Spot-Check):**
- [ ] Service still running
- [ ] No new errors in logs
- [ ] Measurement files growing (compare file size, line count)

**End of Day (5pm Review):**
- [ ] Total records collected: P, F, C, B
- [ ] Any incidents logged?
- [ ] Locks cleaned up?
- [ ] Aggregation scheduled (2am UTC nightly)

---

## Contact & Escalation

| Level | Owner | Response | Contact |
|-------|-------|----------|---------|
| L1 On-Call | Ops team | 15 min | Page to Slack |
| L2 Engineer | Dev team | 1 hour | Escalate issue |
| L3 Lead | Measurement lead | 4 hours | All-hands if needed |

---

## Quick Commands

```bash
# Health check
bash operator/context_engineering/scripts/health-check.sh --continuous --interval 60

# Restart service
corvin stop && sleep 2 && corvin-serve &

# View logs (last 50 lines, filter errors)
tail -50 ~/.corvin/logs/session.log | grep -i "error\|critical\|warn"

# Check measurement data
ls -lh ~/.corvin/measurement/$(date +%Y-%m-%d)/
wc -l ~/.corvin/measurement/$(date +%Y-%m-%d)/*.jsonl

# Clean stale locks
find ~/.corvin/tenants/_default/learning-queue/ -name ".lock*" -mmin +120 -delete

# Aggregation status
ls -la ~/.corvin/tenants/_default/.checkpoint/

# Queue health
du -sh ~/.corvin/tenants/_default/learning-queue/
find ~/.corvin/tenants/_default/learning-queue/ -name "*.jsonl" | wc -l
```

---

## Documentation

- **Deployment:** `docs/implementation/DEPLOYMENT-CHECKLIST.md`
- **Measurement:** `docs/implementation/WEEK6-MEASUREMENT-PHASE-PLAN.md`
- **Health Check:** `operator/context_engineering/scripts/health-check.sh`
- **Integration:** `docs/implementation/ADR-0274-INTEGRATION-GUIDE.md`

---

**Version:** 1.0  
**Created:** 2026-08-08  
**For:** Week 6 Measurement Phase (2026-08-11 to 2026-08-17)

**Last Updated:** 2026-08-08
