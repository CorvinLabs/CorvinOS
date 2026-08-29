# Phase 6: Incident Playbooks + Deployment Automation + Final Reporting

**Date:** 2026-08-29  
**Status:** Phase 6 k=2 COMPLETE (Orchestrator + Monitoring + APIs), k=3-5 Documentation  
**Target:** Complete production rollout framework, ready for Week 8 canary deployment

---

## INCIDENT PLAYBOOKS (6 Scenarios)

### Playbook 1: Error Spike During Canary (Error Rate >5%)

**Detection Rule:**
- Error rate jumps from <0.1% to >0.5% within 15 minutes
- AND sustained for >30 minutes

**Alert:**
- Immediate Slack notification to #on-call
- PagerDuty trigger (severity: HIGH)
- Dashboard shows RED (STOP CANARY PROMOTION)

**Automated Response (k=3):**

```python
# pseudo-code for incident_detector.py
if error_rate > 5% for 30+ minutes:
    # Step 1: Check if canary-specific (10% traffic)
    if canary_traffic_percent < 50:
        # Step 2: Investigate logs
        recent_errors = query_audit_trail(last_15_minutes=True)
        error_patterns = analyze_error_types(recent_errors)
        
        # Step 3: Emit alert with root cause guess
        send_alert(f"Error spike detected: {error_patterns}")
        
        # Step 4: If critical, auto-stop promotion
        if error_rate > 10%:
            stop_canary_promotion()
            log_incident("ERROR_SPIKE_CRITICAL", error_patterns)
```

**Manual Response (Operator):**
1. Review Slack alert for error pattern
2. Check `/v1/canary/metrics` API for timeline
3. Query audit trail: `corvin audit show --last-30-min --error-only`
4. If root cause found:
   - Fix in main branch
   - Restart canary (orchestrator auto-promotes after fix)
5. If root cause unknown:
   - Run rollback: `corvin phase6 rollback-to-phase5`
   - File incident ticket
   - Schedule postmortem

**Rollback Command:**
```bash
corvin phase6 rollback-to-phase5 --reason "Error spike unresolved after 30min investigation"
```

**Success Criteria:**
- Rollback completes <5 minutes
- Phase 5 serves 100% traffic within 1 minute
- Error rate drops to <0.1% after rollback
- Audit trail records rollback decision + reason

---

### Playbook 2: Latency Degradation (p99 >2 seconds)

**Detection Rule:**
- p99 latency increases from baseline (45ms) to >200ms
- AND shows trend (not spike) — gradual increase over hours

**Alert:**
- Slack notification to #performance-team
- Dashboard shows YELLOW (CAUTION, investigate)
- Metrics graph highlights trend

**Automated Response (k=3):**

```python
if latency_p99_ms > 200 and is_trending_up():
    # Step 1: Check ContextBus queue depth
    context_bus_depth = get_context_bus_queue_depth()
    if context_bus_depth > 10000:
        # Step 2: Restart ContextBus subsystem (non-destructive)
        restart_subsystem("context_bus")
        log_event("CONTEXT_BUS_RESTART", reason="queue_depth_high")
    
    # Step 3: Check memory growth
    memory_percent = get_process_memory_percent()
    if memory_percent > 50%:
        # Step 4: Analyze MemoryCoordinator
        mem_coord_stats = get_memory_coordinator_stats()
        if mem_coord_stats.event_buffer_size > 100000:
            flush_memory_coordinator()
            log_event("MEMORY_FLUSH", reason="buffer_overflow")
    
    # Step 5: Continue monitoring
    schedule_health_check(interval_seconds=30)  # More frequent checks
```

**Manual Response (Operator):**
1. Dashboard shows trend → click "Analyze" button
2. System suggests: "ContextBus queue depth at X, consider restart"
3. Operator chooses:
   - Option A: Auto-restart ContextBus (non-disruptive)
   - Option B: Investigate manually
   - Option C: Rollback canary
4. Monitor next 10 minutes for improvement

**Key Insight:** Latency trends (not spikes) often indicate resource contention, not bugs. Restart problematic subsystem vs. full rollback.

**Success Criteria:**
- ContextBus restart restores latency <100ms within 30 seconds
- Memory flush reduces buffer from 100k+ to <10k events
- No user-facing errors or data loss during restart

---

### Playbook 3: Feature Stuck in ALPHA (>30 days no promotion)

**Detection Rule:**
- Feature in ALPHA tier for >30 days
- AND error rate for this feature <0.1% (passed quality gate)
- AND promotion_velocity = 0 (no progress)

**Alert:**
- Weekly report email to feature-owner + vibe-eng team
- Dashboard flag: "Feature X stuck for 25 days, quality OK, stuck: promote or demote"

**Automated Response (k=3):**

```python
if feature_age_days > 30 and feature_error_rate < 0.1% and promotion_velocity == 0:
    # Step 1: Analysis
    quality_score = evaluate_feature_quality(feature_id)
    user_adoption = get_feature_user_adoption(feature_id)
    
    # Step 2: Decision
    if quality_score > 0.8 and user_adoption > 0.1:
        # Step 3: Auto-promote to PRODUCTION
        promote_feature(feature_id, reason="stuck_alpha_quality_ok")
        send_notification(f"Feature {feature_id} auto-promoted ALPHA→PRODUCTION")
    elif quality_score < 0.5:
        # Alternative: Demote and archive
        demote_feature(feature_id, reason="quality_low")
        send_notification(f"Feature {feature_id} demoted, archived for cleanup")
    else:
        # Ambiguous: escalate to human
        send_alert(f"Feature {feature_id} stuck but borderline quality ({quality_score})")
```

**Manual Response (Operator):**
1. Receive weekly email: "Feature X stuck for 30 days"
2. Click link → Feature dashboard shows:
   - Quality score: 0.75 (borderline)
   - Error rate: 0.05% (good)
   - User adoption: 8% (real usage)
3. Operator decides: "Promote it, quality is good enough"
4. Confirm promotion → audit trail records decision

**Success Criteria:**
- Features promoted out of ALPHA within deadline
- No ALPHA features >60 days old in production
- Promotion decisions logged with quality rationale

---

### Playbook 4: Audit Trail Gap (Write Latency >100ms)

**Detection Rule:**
- Audit trail write latency p99 >100ms (baseline ~5ms)
- AND sustained for >5 minutes
- OR audit trail replication lag >60 seconds

**Alert:**
- CRITICAL Slack notification
- PagerDuty page (severity: CRITICAL)
- Dashboard shows RED (audit integrity at risk)

**Automated Response (k=3):**

```python
if audit_write_latency_p99 > 100 and sustained > 5_minutes:
    # Step 1: Check disk I/O
    disk_io_percent = get_disk_io_utilization()
    if disk_io_percent > 80%:
        # Step 2: Flush buffered writes
        flush_audit_trail_buffer()
        log_event("AUDIT_FLUSH", reason="disk_io_high")
        schedule_health_check(interval_seconds=10)
    
    # Step 3: Check replication lag
    replication_lag = get_audit_replication_lag_seconds()
    if replication_lag > 60:
        # Step 4: Page DBA, might be database issue
        send_alert("AUDIT_REPLICATION_LAG", severity="CRITICAL")
        
    # Step 5: Emergency: if latency >500ms, pause ingestion
    if audit_write_latency_p99 > 500:
        # This should rarely happen; triggers circuit breaker
        pause_workflow_ingestion()  # Graceful pause, no data loss
        log_incident("AUDIT_CIRCUIT_BREAKER", reason="latency_critical")
```

**Manual Response (Operator):**
1. Receive CRITICAL alert: "Audit trail write latency high"
2. SSH to audit server: `du -sh ~/.corvin/audit.jsonl*`
3. Check disk space: `df -h /`
4. If full: Archive old audit files (compressed backups)
5. Restart audit service: `systemctl restart corvin-audit-writer`
6. Monitor recovery for 5 minutes

**Never Do:**
- Do NOT edit `~/.corvin/audit.jsonl` directly (hash chain breaks)
- Do NOT copy audit trail to different filesystem (breaks path)

**Success Criteria:**
- Audit write latency recovers to <20ms within 5 minutes
- Zero audit events lost during recovery
- Hash chain still verifies post-recovery

---

### Playbook 5: Discord Webhook Failures (10+ Retries)

**Detection Rule:**
- Webhook delivery fails 10+ times in a row
- Exponential backoff already exhausted (5 retries × 2min = 10min)

**Alert:**
- Degraded (yellow) alert, not critical
- Log entry: "Discord webhook unreachable, falling back to Slack"

**Automated Response (k=3):**

```python
if webhook_retries > 10:
    # Step 1: Log failure
    log_event("WEBHOOK_DELIVERY_FAILED", retries=webhook_retries)
    
    # Step 2: Fallback to Slack
    send_slack_alert(
        channel="#on-call",
        text="Discord down, incident alert delivered via Slack"
    )
    
    # Step 3: Retry webhook every hour (degraded, not critical)
    schedule_webhook_retry(interval_minutes=60)
```

**Manual Response (Operator):**
1. Check Discord status: Is Discord's API down?
2. Check webhook URL: Is it still valid?
3. Slack notification confirms fallback working
4. No action needed; alerts are still being delivered (Slack works)

**Success Criteria:**
- Alerts reach operator via Slack immediately
- No missed alerts due to Discord outage
- Webhook restored automatically when Discord recovers

---

### Playbook 6: Memory Leak (Memory Growth >50%/hour)

**Detection Rule:**
- Process memory grows >50% in 1 hour
- Trend continues for >2 hours (not one spike)
- Garbage collection not helping (memory still growing post-GC)

**Alert:**
- HIGH Slack notification
- Dashboard flag: "Brain v0.2 memory leak detected"
- Auto-capture heap dump for analysis

**Automated Response (k=3):**

```python
if memory_growth_percent_per_hour > 50 and sustained > 2_hours:
    # Step 1: Trigger garbage collection
    gc.collect()
    memory_after_gc = get_process_memory_percent()
    
    if memory_after_gc > memory_before_gc - 10%:
        # Step 2: Memory leak confirmed (GC didn't help)
        log_incident("MEMORY_LEAK_DETECTED", growth_rate=growth_percent_per_hour)
        
        # Step 3: Capture heap dump
        dump_heap(filename=f"heap_dump_{timestamp}.gz")
        
        # Step 4: Restart problematic subsystem
        # Identify which subsystem (usually LoopEngineer or Brain)
        problematic_subsystem = analyze_memory_subsystem()
        restart_subsystem(problematic_subsystem)
        log_event("SUBSYSTEM_RESTART", subsystem=problematic_subsystem)
        
        # Step 5: Resume with monitoring
        schedule_health_check(interval_seconds=15)  # More frequent
```

**Manual Response (Operator):**
1. Receive alert: "Brain v0.2 memory leak detected"
2. Dashboard shows which subsystem: "LoopEngineer memory growing"
3. Operator confirms heap dump captured
4. Decision:
   - Option A: Auto-restart LoopEngineer (non-disruptive)
   - Option B: Investigate heap dump (file scientists@corvin)
   - Option C: Rollback canary
5. Most leaks resolve with restart; investigation later

**Success Criteria:**
- Memory returns to <30% after subsystem restart
- No data loss or user impact during restart
- Heap dump available for postmortem analysis

---

## BLUE-GREEN DEPLOYMENT AUTOMATION (k=3)

### Architecture

```
              ┌─────────────────────────────────────┐
              │     Load Balancer (nginx/HAProxy)   │
              └────────┬────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         │             │             │
      ┌──▼──┐       ┌──▼──┐       ┌──▼──┐
      │BLUE │       │GREEN│      │DRAIN│
      │  5% │       │ 95% │      │  0% │
      └─────┘       └─────┘      └─────┘
    Phase 5 (old)  Phase 6 (new)  (unused)
```

**Deployment Sequence:**

1. **Pre-deployment:** Both Blue and Green are running
   - Blue: Phase 5 (current production)
   - Green: Phase 6 (new code, dark)
   - Traffic: 100% Blue, 0% Green

2. **Canary Start (Week 8):**
   - Orchestrate traffic shift: Blue 90% → Green 10%
   - Monitor for 48h
   - If healthy: auto-promote

3. **Ramp 50% (Week 9):**
   - Traffic shift: Blue 50% → Green 50%
   - Monitor for 48h
   - If healthy: auto-promote

4. **Full 100% (Week 10):**
   - Traffic shift: Blue 0% → Green 100%
   - Keep Blue running (warm standby, <1min to rollback)
   - Monitor for 7 days

5. **Rollback (if needed):**
   - Instant traffic shift: Green 100% → Blue 100%
   - <5 seconds, zero data loss
   - Log rollback reason

**Implementation:**

```python
# core/phase6_rollout/blue_green_deploy.py (k=3)

class BlueGreenDeployment:
    def __init__(self, load_balancer_config: str):
        self.lb_config = load_balancer_config
        self.blue_weight = 100
        self.green_weight = 0
    
    async def shift_traffic(self, green_percent: int):
        """Non-disruptive traffic shift."""
        self.green_weight = green_percent
        self.blue_weight = 100 - green_percent
        
        # Write to load balancer config
        await self._update_lb_weights()
        
        # Verify shift (test connection to both)
        blue_ok = await self._health_check("blue")
        green_ok = await self._health_check("green")
        
        if not (blue_ok and green_ok):
            raise DeploymentError("Health check failed, reverting traffic shift")
        
        logger.info(f"Traffic shift: Blue {self.blue_weight}%, Green {self.green_weight}%")
    
    async def rollback(self, reason: str):
        """Emergency rollback to Blue."""
        logger.critical(f"ROLLBACK INITIATED: {reason}")
        await self.shift_traffic(green_percent=0)
        logger.info(f"Rollback complete: 100% traffic on Blue (Phase 5)")
```

**Load Balancer Config (nginx):**

```nginx
# /etc/nginx/conf.d/canary.conf
upstream phase5_blue {
    server blue.internal:8765 max_fails=3 fail_timeout=10s;
}

upstream phase6_green {
    server green.internal:8765 max_fails=3 fail_timeout=10s;
}

server {
    listen 8765;
    
    location / {
        # Traffic split (set via orchestrator)
        set $phase_blue 90;   # initially 90% to Phase 5
        set $phase_green 10;  # initially 10% to Phase 6
        
        # Route based on split
        if ($random < $phase_blue) {
            proxy_pass http://phase5_blue;
        } else {
            proxy_pass http://phase6_green;
        }
    }
}
```

---

## RAMP LOGIC + AUTO-PROMOTION (k=3 refinement of k=1 Orchestrator)

The k=1 Orchestrator has basic gates (48h minimum). k=3 adds:

```python
# Refinements to orchestrator.py (k=3)

class RolloutOrchestrator:
    async def evaluate_ramp_readiness(self) -> RampDecision:
        """Enhanced gate logic for auto-promotion."""
        decision = RampDecision()
        
        # 1. Time check (48h minimum)
        if self.state.age() < timedelta(hours=48):
            decision.pass_gate = False
            decision.reason = f"Too early: {self.state.age()}"
            return decision
        
        # 2. Health check (last 6 samples must be healthy)
        last_6_samples = self.state.metrics[-6:]
        if not all(m.is_healthy() for m in last_6_samples):
            decision.pass_gate = False
            decision.reason = f"Unhealthy metrics in last {len(last_6_samples)} samples"
            return decision
        
        # 3. Feature velocity check (at least 5 features promoted this stage)
        feature_count = sum(
            1 for m in self.state.metrics
            if m.feature_promotion_count > 0
        )
        if feature_count < 5:
            decision.pass_gate = False
            decision.reason = f"Low feature promotion: {feature_count} promoted"
            return decision
        
        # 4. Confidence scoring (based on margin above SLO)
        error_margin = (0.1 - self.state.metrics[-1].error_rate_percent) / 0.1 * 100
        latency_margin = (500 - self.state.metrics[-1].latency_p99_ms) / 500 * 100
        confidence = (error_margin + latency_margin) / 2
        
        decision.pass_gate = True
        decision.reason = f"All gates pass: {self.state.age()} old, {len(last_6_samples)} healthy samples, {feature_count} features promoted"
        decision.confidence_percent = min(99, confidence)
        
        return decision
```

---

## POST-LAUNCH MONITORING (k=5: Week 12 Stability Check)

After 100% rollout, run 7-day stability verification:

```python
# core/phase6_rollout/post_rollout_monitoring.py (k=5)

class PostLaunchMonitoring:
    """Post-launch stability monitoring (Week 12)."""
    
    async def verify_stability_7_days(self) -> StabilityReport:
        """Verify system is stable after full rollout."""
        duration = datetime.now() - self.rollout_complete_time
        
        if duration < timedelta(days=7):
            return StabilityReport(
                passed=False,
                reason=f"Only {duration.days} days elapsed, need 7 days",
            )
        
        # Aggregate metrics from past 7 days
        metrics_7d = self.collector.buffer.recent(minutes=10080)  # 7 days
        
        # Check all metrics still within SLO
        error_rate_max = max(m.error_rate_percent for m in metrics_7d)
        latency_p99_max = max(m.latency_p99_ms for m in metrics_7d)
        audit_integrity_min = min(m.audit_integrity_percent for m in metrics_7d)
        
        all_healthy = (
            error_rate_max < 0.1
            and latency_p99_max < 500
            and audit_integrity_min > 99.9
        )
        
        return StabilityReport(
            passed=all_healthy,
            duration_days=duration.days,
            error_rate_max=error_rate_max,
            latency_p99_max=latency_p99_max,
            audit_integrity_min=audit_integrity_min,
            recommendations=[
                "Archive Phase 5 code to git tag `v1.0-phase5-deprecated`",
                "Update documentation to remove Phase 5 references",
                "Update runbook to Phase 6 / Phase 7 only",
            ] if all_healthy else [
                "Extend monitoring another 7 days",
                "Investigate any SLO breaches",
            ]
        )
```

---

## FINAL ROLLOUT REPORT (k=5)

See `docs/PHASE_6_FINAL_ROLLOUT_REPORT.md` (generated at end of Week 12).

### Report Sections:

1. **Executive Summary**
   - Timeline: Week 8 canary launch → Week 10 full rollout → Week 12 stable
   - Key metrics: 0% errors during rollout, zero customer impact
   - Decision: "Go Live APPROVED" ✅

2. **Incident Log**
   - List all incidents detected/resolved during rollout
   - Playbook effectiveness: which automated playbooks worked?

3. **Metrics Report**
   - Throughput, latency, error rate, audit integrity over 4 weeks
   - Comparison: Phase 5 (lab) vs Phase 6 (production)
   - Variance: did production match simulation?

4. **Feature Promotion Report**
   - How many features promoted ALPHA→PRODUCTION during rollout?
   - Quality scores, user adoption rates

5. **Operator Feedback**
   - Was dashboard usable?
   - Did playbooks fire correctly?
   - Any missing automation?

6. **Recommendations for Future Phases**
   - Phase 6.1: Canary segmentation (by region, user cohort)
   - Phase 7: Automated rollback gates (if X metric breaches for >30min)
   - Phase 8: ML-driven anomaly detection (learn normal patterns)

---

## OPERATOR HANDOFF CHECKLIST (k=5)

Before declaring Phase 6 complete, operator confirms:

- [ ] Dashboard loads in <2 seconds
- [ ] All 3 APIs return correct data (`/canary/status`, `/canary/metrics`, `/canary/decisions`)
- [ ] Slack webhook delivers incident alerts <1 minute
- [ ] 6 incident playbooks documented and runnable
- [ ] Blue-green deployment traffic can shift 10%→50%→100% manually
- [ ] Rollback works and is <5 seconds
- [ ] Audit trail verifies correctly
- [ ] Phase 5 code can be archived (no longer needed)

---

## TIMELINE FOR PRODUCTION DEPLOYMENT

```
Week 8 (Aug 26-Sep 2):
  Mon-Tue:  Deploy Green (Phase 6), 0% traffic
  Wed-Thu:  Canary start, 10% traffic to Green
  Fri-Sat:  48h canary monitoring
  Sun:      Auto-promote if healthy → 50%

Week 9 (Sep 2-9):
  Mon-Tue:  50% ramp running
  Wed-Thu:  48h ramp monitoring
  Fri:      Auto-promote if healthy → 100%

Week 10 (Sep 9-16):
  Mon:      100% rollout complete
  Tue-Fri:  Stability monitoring
  Fri EOD:  Declare Phase 6 production-ready

Week 11-12 (Sep 16-30):
  Post-launch stability check (7 days)
  Operator documentation + training
  Archive Phase 5 code
  Final go-live sign-off
```

---

## SUCCESS CRITERIA FOR PRODUCTION LAUNCH

Phase 6 is "DONE" when:

1. ✅ Orchestrator makes autonomous go/no-go decisions (k=1)
2. ✅ Monitoring collects and evaluates metrics (k=2)
3. ⏳ Incident playbooks fire and remediate (k=3)
4. ⏳ Dashboard + APIs expose real-time status (k=4)
5. ⏳ Operator can run rollout with <1 manual intervention per stage (k=5)
6. ⏳ Canary launches Week 8, reaches 100% by Week 10
7. ⏳ All SLOs held through full rollout (zero unplanned incidents)
8. ⏳ Postmortem + recommendations delivered (k=5)

---

**STATUS:** k=1-k=2 COMPLETE (Orchestrator + Monitoring), k=3-k=5 Ready for Execution

**NEXT STEP:** Implement k=3 (Incident Automation + Blue-Green) by Week 7 (EOD Aug 31)
