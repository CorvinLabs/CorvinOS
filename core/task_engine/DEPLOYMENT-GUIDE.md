# Task Engine Deployment Guide (Phase E, ADR-0545)

**Status:** Phase E Roadmap (Weeks 17–20)

## Canary Deployment (Weeks 1–2)

### Environment: Staging
- Single tenant: `_default`
- Task quota: 5 concurrent, 100 max total
- Monitoring: Enabled (Prometheus + Grafana)

### Validation Checklist
- [ ] 3-phase task DAG executes without errors
- [ ] Audit-trail: zero gaps, chain verified
- [ ] EventStore: snapshots created, no corruption
- [ ] State continuity: snapshots preserved across phases
- [ ] Rollback: tested, recovery works
- [ ] Dashboard: real-time metrics displayed
- [ ] Learning: confidence curve smooth, no drift
- [ ] Monitoring: alerts configured (audit-chain failures, orphaned worktrees)

### Duration: 1–2 weeks
- Deploy to staging (internal users only)
- Monitor logs + metrics
- Validate all proof points
- Iterate on bugs

### Success Gate
- 0 CRITICAL bugs
- ≤2 HIGH bugs (documented, planned fixes)
- Audit-chain verified daily
- No data loss incidents

## Production Deployment (Week 3)

### Environment: Production
- Multi-tenant (all tenants)
- Task quota: 50 concurrent per tenant
- Monitoring: High-alert mode

### Pre-Deployment Checklist
- [ ] Canary passed all validation gates
- [ ] Runbook reviewed + ready
- [ ] Monitoring + alerting deployed
- [ ] Rollback procedure tested
- [ ] Data backup configured

### Deployment Steps
1. Deploy Phase A code (core/task_engine/)
2. Deploy Phase B (crypto + verification)
3. Deploy Phase C (gates + rollback)
4. Deploy Phase D (dashboard)
5. Enable TaskExecutor in L5 routing (auto-trigger for long tasks)

### Rollback Procedure
If production incident:
1. Disable new long-task routing (kill TaskExecutor)
2. Revert code to Phase A
3. Restore EventStore from backup
4. Verify audit-chain integrity

## Monitoring & Alerting

### Prometheus Metrics
- `task_executor_tasks_total` — Total tasks executed
- `task_executor_phases_complete_total` — Phases completed
- `task_executor_duration_seconds` — Task execution time
- `event_store_size_bytes` — EventStore disk usage
- `audit_chain_verification_failed_total` — Chain verification failures (ALERT)
- `phase_gate_failures_total` — Phase gates that blocked

### Grafana Dashboard
- Task execution timeline (Gantt chart)
- Phase progress (real-time)
- Confidence curve (learning signal)
- Audit-chain health
- Worktree usage (disk, count)
- Event-store size trend

### Alerts (High Priority)
- **audit_chain_broken:** Verification failed → immediate investigation
- **event_store_size_exceeded:** Disk usage >80% → archive old tasks
- **worktree_orphaned:** Worktree exists 24h+ after task complete → manual cleanup
- **phase_gate_failures_spike:** Unexpectedly high gate failures → check skill behavior
- **confidence_drift:** Confidence delta > 0.15 → check optimizer tuning

## Incident Response Runbook

### Incident: "Task rolled back unexpectedly"
**Symptom:** Task failed in Phase N, rolled back  
**Diagnosis:**
1. Check audit-trail: which gate blocked?
2. Check phase output: what was the error?
3. Check logs: skill failure? timeout?

**Resolution:**
- If skill failure: fix skill, re-run Phase N
- If timeout: increase timeout_hours, re-run
- If gate failure: check gate config, adjust threshold if needed

**Prevention:** Review gate thresholds monthly

### Incident: "Audit-chain verification failed"
**Symptom:** Daily cron found chain gap  
**Diagnosis:**
1. Check which task has the gap
2. Check EventStore for corruption
3. Check git for divergence (git commit != snapshot.code_state_hash)

**Resolution:**
- If corruption: restore from backup snapshot
- If git divergence: manual git reset + revert
- Escalate: contact on-call architect

**Prevention:** Daily cron verification, weekly full chain scan

### Incident: "Worktree disk full"
**Symptom:** Task fails "no space left"  
**Diagnosis:**
1. Check /tmp/task_worktrees/ disk usage
2. Check for orphaned worktrees (age > 24h)
3. Check EventStore size

**Resolution:**
- Archive old worktrees to cold storage
- Purge EventStore snapshots >30 days old
- Increase disk quota if needed

**Prevention:** Auto-cleanup cron (daily), monitor disk trend

## Configuration

### Environment Variables
```bash
# Crypto key for snapshots (Phase B)
TASK_ENGINE_CRYPTO_KEY="<external-key-from-hsm>"

# EventStore location
TASK_ENGINE_EVENTSTORE_PATH="<cloud-storage-or-db>"

# Worktree base path
TASK_ENGINE_WORKTREE_BASE="/tmp/task_worktrees"

# Daily verification cron (Phase B)
TASK_ENGINE_VERIFY_CRON="0 2 * * *"  # 02:00 UTC daily

# Monitoring + alerting
PROMETHEUS_PUSHGATEWAY="http://prometheus:9091"
GRAFANA_DATASOURCE="prometheus"
```

### Tenant Configuration (tenant.corvin.yaml)
```yaml
task_engine:
  enabled: true
  max_concurrent_tasks: 50
  max_task_queue_size: 500
  phase_timeout_hours: 24
  monitoring:
    enabled: true
    alert_on_gate_failure: true
    alert_on_chain_failure: true
  archival:
    snapshot_retention_days: 30
    worktree_cleanup_hours: 24
```

## Disaster Recovery

### Backup Strategy
- Daily: EventStore snapshot to cold storage (S3, Azure Blob, etc.)
- Daily: Git worktree state snapshot
- Weekly: Full EventStore export (JSON)

### Restore Procedure
1. Stop TaskExecutor (kill all running tasks)
2. Restore EventStore from latest backup
3. Restore git worktrees from snapshot
4. Verify audit-chain integrity
5. Resume TaskExecutor

### RTO / RPO Targets
- **RTO (Recovery Time Objective):** < 1 hour
- **RPO (Recovery Point Objective):** < 1 hour (daily backups)

## Post-Deployment Monitoring (Week 4+)

### Weekly Checklist
- [ ] Audit-chain verification: all tasks ✓
- [ ] Worktree disk usage: < 80%
- [ ] EventStore size growth: < 5% week-over-week
- [ ] No critical alerts in past 7 days
- [ ] Phase success rate: > 99%

### Monthly Review
- [ ] Confidence curve stability (no unexpected drift)
- [ ] Gate threshold calibration (adjust if >5% failures)
- [ ] Optimizer tuning effectiveness (compare Phase 1 vs Phase N confidence)
- [ ] Performance metrics (latency, throughput trends)
- [ ] Cost analysis (worktree storage, EventStore size, compute)

## SLO (Service Level Objectives)

| Metric | Target | Alert Threshold |
|---|---|---|
| **Task Success Rate** | 99.5% | < 99% |
| **Audit-Chain Integrity** | 100% verified daily | Any failure |
| **Phase Gate Pass Rate** | 95% | < 90% |
| **Phase Completion Time** | ≤ timeout_hours | Timeout exceeded |
| **State Continuity** | 100% (snapshots verified) | Any mismatch |

## Support & Escalation

### On-Call Runbook
1. Check Grafana dashboard for immediate status
2. Review audit-trail for the failed task
3. Follow incident runbook above
4. If unresolved after 15 min: escalate to architect
5. Create incident ticket in Linear (project: TASK_ENGINE)

### Escalation Contacts
- **L1 Support:** ops-team@
- **L2 Architect:** shumway@
- **L3 Critical Incident:** engineering-leads@

---

## Success Metrics (Post-Launch)

After 4 weeks in production:
- ✅ Zero data loss
- ✅ Zero audit-chain breaks
- ✅ 99.5%+ task success rate
- ✅ All proof points hold under load
- ✅ Learning optimizer converges (confidence increasing)

Then: Celebrate 🎉 and plan Phase F (optimizations).

---

**Phase E Status:** Ready for canary deployment (weeks 17–20)
