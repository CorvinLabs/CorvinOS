# ADR-0274 Production Deployment Checklist

**Status:** Ready for deployment  
**Timeline:** ~2 hours (dev to prod)  
**Risk Level:** Medium (learning system, but fallback to baseline works)

---

## Phase 1: Pre-Deployment (30 minutes)

### Code Review
- [ ] All commits reviewed and tested
  - `bd13c5b` (K=1) → `4076e1b` (K=5)
  - 10/10 tests passing
  - Zero known gaps
- [ ] Merge conflicts resolved (if any)
- [ ] Security review complete (no leaks, no backdoors)
- [ ] Performance impact assessed (<5% overhead)

### Git Operations
- [ ] Branch status clean
  ```bash
  git status  # should be clean
  ```
- [ ] All changes committed
  ```bash
  git log --oneline | head -5
  # Should show: 4076e1b (K=5), f543d39 (K=4), f94674b (K=3), 49d4bf8 (K=2), bd13c5b (K=1)
  ```

### Environment Check
- [ ] Python version: 3.9+
  ```bash
  python3 --version
  ```
- [ ] Dependencies installed
  ```bash
  uv sync
  ```
- [ ] Test environment clean (no stale processes)
  ```bash
  pgrep -f "corvin\|task_engine" | xargs kill -9 2>/dev/null || true
  ```

---

## Phase 2: Final Integration Testing (30 minutes)

### Unit Tests
- [ ] Core tests pass
  ```bash
  uv run pytest operator/context_engineering/tests/test_k3_integration.py -v
  # Expected: 5/5 pass
  ```

### Integration Tests
- [ ] Guard wiring tests pass
  ```bash
  uv run pytest operator/context_engineering/tests/test_cr6_wiring.py -v
  # Expected: 5/5 pass
  ```

### System Integration
- [ ] Critical fixes in place
  ```bash
  grep -q "def compute_record_checksum" operator/context_engineering/critical_fixes_roundk2.py
  grep -q "class AggregatorCheckpoint" operator/context_engineering/critical_fixes_roundk2.py
  ```
- [ ] Guard integration hook available
  ```bash
  grep -q "class ContextSuggestionGate" operator/context_engineering/guard_integration_hook.py
  ```

### Staging Smoke Test (If staging available)
- [ ] Deploy to staging
  ```bash
  export CORVIN_ENVIRONMENT=staging
  corvin-serve &
  sleep 3
  curl http://localhost:8000/health
  # Expected: {"status": "ok"}
  ```
- [ ] Verify logs clean
  ```bash
  grep -i "error\|exception" ~/.corvin/logs/session.log | head -5
  # Expected: no errors
  ```

---

## Phase 3: Deployment (45 minutes)

### Pre-Deployment Backups
- [ ] Backup current production profiles
  ```bash
  cp -r ~/.corvin/tenants/_default/profiles ~/.corvin/tenants/_default/profiles.backup.$(date +%s)
  ```
- [ ] Backup current queue files
  ```bash
  cp -r ~/.corvin/tenants/_default/learning-queue ~/.corvin/tenants/_default/learning-queue.backup.$(date +%s)
  ```

### Code Merge & Deploy
- [ ] Pull latest main
  ```bash
  git fetch origin main
  git merge origin/main  # if needed
  ```
- [ ] Verify deployment state
  ```bash
  git status
  # Expected: clean (or only untracked local files)
  ```
- [ ] Install dependencies
  ```bash
  uv sync --all
  ```
- [ ] Run final test
  ```bash
  uv run pytest operator/context_engineering/tests/test_k3_integration.py operator/context_engineering/tests/test_cr6_wiring.py -v
  # Expected: 10/10 pass
  ```

### Service Deployment
- [ ] Stop current service (if running)
  ```bash
  corvin stop
  sleep 2
  ```
- [ ] Clear temporary files
  ```bash
  rm -rf ~/.corvin/tenants/_default/.checkpoint/*.tmp 2>/dev/null || true
  rm -f ~/.corvin/tenants/_default/learning-queue/*.lock* 2>/dev/null || true
  ```
- [ ] Start new service
  ```bash
  export CORVIN_TELEMETRY_OPTIN=true
  export CEL_PHASE4_MEASUREMENT=true
  corvin-serve &
  sleep 5
  ```

### Service Health Check
- [ ] Service running
  ```bash
  pgrep -f "corvin-serve" > /dev/null
  # Expected: exit code 0
  ```
- [ ] Logs clean
  ```bash
  tail -20 ~/.corvin/logs/session.log | grep -i "error\|exception"
  # Expected: no errors (or only expected ones)
  ```
- [ ] API responding
  ```bash
  curl http://localhost:8000/health
  # Expected: {"status": "ok"}
  ```

---

## Phase 4: Post-Deployment Verification (15 minutes)

### Queue Operations
- [ ] First aggregation attempt
  ```bash
  python3 -c "
  from operator.context_engineering.critical_fixes_roundk2 import IntegrationAggregator
  from pathlib import Path
  agg = IntegrationAggregator(
    Path.home() / '.corvin/tenants/_default/learning-queue',
    Path.home() / '.corvin/tenants/_default/profiles'
  )
  result = agg.run_aggregation()
  print('Aggregation result:', result)
  assert result['success'], 'Aggregation failed!'
  "
  # Expected: {"success": True, "records_processed": 0, ...}
  ```

### Monitoring Setup
- [ ] Monitoring dashboard accessible (if applicable)
  ```bash
  # e.g., http://localhost:9090 (Prometheus)
  # e.g., http://localhost:3000 (Grafana)
  ```
- [ ] Alerting rules configured
  ```bash
  # Verify alert rules in prometheus.yml or via API
  ```

### Data Integrity
- [ ] Profiles loaded correctly
  ```bash
  ls -la ~/.corvin/tenants/_default/profiles/ | head -5
  # Expected: tenant-baseline.json (symlink) + versioned files
  ```
- [ ] Queue files intact
  ```bash
  ls -la ~/.corvin/tenants/_default/learning-queue/ | head -5
  # Expected: YYYY-MM-DD.jsonl files present
  ```

---

## Phase 5: Activation & Monitoring (30 minutes)

### Enable Measurement Tracks
- [ ] Set measurement environment variables
  ```bash
  export CORVIN_MEASUREMENT_TRACK_UNCERTAINTY=true
  export CORVIN_MEASUREMENT_TRACK_FEEDBACK=true
  export CORVIN_MEASUREMENT_TRACK_PREFERENCES=true
  export CORVIN_MEASUREMENT_TRACK_BUDGET=true
  ```

### Start Monitoring Collection
- [ ] Telemetry enabled
  ```bash
  # Verify in ~/.config/corvin-voice/consent or CORVIN_TELEMETRY_OPTIN=true
  ```
- [ ] Collection running
  ```bash
  tail -f ~/.corvin/tenants/_default/learning-queue/$(date +%Y-%m-%d).jsonl
  # Expected: New records appended (if tasks running)
  ```

### First Hour Observations
- [ ] No errors in logs
  ```bash
  grep -i "error\|exception\|critical" ~/.corvin/logs/session.log | wc -l
  # Expected: 0 (or only pre-existing)
  ```
- [ ] Lock operations clean
  ```bash
  grep "Acquired exclusive lock\|Released lock" ~/.corvin/logs/session.log | tail -5
  # Expected: balanced acquires/releases
  ```
- [ ] Checksum validation working
  ```bash
  grep "checksum\|validation" ~/.corvin/logs/session.log | tail -3
  # Expected: no "corrupted" messages
  ```

---

## Rollback Procedure (If Issues)

### Quick Rollback (< 5 minutes)
1. Stop service
   ```bash
   corvin stop
   ```
2. Restore backups
   ```bash
   rm -rf ~/.corvin/tenants/_default/profiles
   rm -rf ~/.corvin/tenants/_default/learning-queue
   cp -r ~/.corvin/tenants/_default/profiles.backup.* ~/.corvin/tenants/_default/profiles
   cp -r ~/.corvin/tenants/_default/learning-queue.backup.* ~/.corvin/tenants/_default/learning-queue
   ```
3. Revert code
   ```bash
   git reset --hard HEAD~5  # Go back to before K=1
   uv sync
   ```
4. Restart
   ```bash
   corvin-serve &
   ```

### Full Rollback (If no recovery)
1. Restore from full backup (if available)
2. Investigate root cause
3. File incident report
4. Plan remediation

---

## Post-Deployment Handoff

### Week 6 Measurement Phase Handoff
- [ ] Measurement team briefed on:
  - Profile location: `~/.corvin/tenants/_default/profiles/`
  - Queue location: `~/.corvin/tenants/_default/learning-queue/`
  - Aggregation runs: 2am UTC (nightly)
  - Dashboard location: (TBD)
- [ ] Day 1 (2026-08-11) standup scheduled
- [ ] Monitoring alerts assigned to on-call

### Documentation Updates
- [ ] Deployment guide committed
- [ ] Runbook accessible to ops
- [ ] Incident response plan reviewed

---

## Sign-Off

| Role | Name | Date | Notes |
|------|------|------|-------|
| **Dev Lead** | — | — | Verified K=1→K=5 tests pass |
| **Ops Lead** | — | — | Deployment checklist complete |
| **Measurement** | — | — | Ready for Week 6 |
| **Release Mgr** | — | — | Go/no-go decision |

---

## Reference Files

- `critical_fixes_roundk2.py` — Core implementation (479 LoC)
- `guard_integration_hook.py` — Integration wiring
- `ADR-0274-K5-VERIFICATION-REPORT.md` — Final verification
- `WEEK6-MEASUREMENT-PHASE-PLAN.md` — Next phase plan

---

**Deployment Ready:** ✅ YES  
**Estimated Downtime:** 5–10 minutes  
**Risk Assessment:** MEDIUM (new system, but fallback available)  
**Go-Date:** 2026-08-10 or 2026-08-11 (per stakeholder decision)

---

**Prepared:** 2026-08-08  
**Version:** 1.0  
**Last Updated:** 2026-08-08
