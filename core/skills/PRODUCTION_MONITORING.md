# Production Monitoring — Phase 1 Go-Live (2026-09-04)

**Deployment:** 100% rollout to all tenants  
**SLOs:** P99 latency <100ms, error rate <0.1%  
**On-Call:** Alert to #ops-skills-pager

---

## Real-Time Alerts (Prometheus + PagerDuty)

### 1. PII Scrubbing Events (FIX #4, #7, #8, #10)

```yaml
alert: SkillPIIScrubDetected
expr: rate(skill_audit_pii_scrubbed_count[5m]) > 10  # >10 scrubs/sec is anomaly
for: 2m
annotations:
  summary: "High PII scrubbing rate on Skill {{ $labels.skill_id }}"
  action: "Check for credential leakage in inputs; review audit trail"
```

**Expected baseline:** 0-5 scrubs/hour (training data, legitimate PII in contexts)  
**Threshold:** >50 scrubs/hour triggers investigation  
**Severity:** WARNING (not CRITICAL; scrubbing means protection is working)

### 2. Confidence Score Clamping (FIX #9)

```yaml
alert: SkillConfidenceOutOfBounds
expr: rate(skill_confidence_clamped_count[5m]) > 5
for: 5m
annotations:
  summary: "Skill {{ $labels.skill_id }} emitting out-of-bounds confidence scores"
  action: "Check Skill code; validate confidence calculation bounds"
```

**Expected baseline:** 0 (never happens in normal operation)  
**Threshold:** >1 clamp event triggers CODE REVIEW  
**Severity:** CRITICAL (indicates Skill bug)

### 3. Tenant-Scoped Auto-Disable (FIX #12)

```yaml
alert: SkillAutoDisabledForTenant
expr: skill_auto_disabled_total{reason="consecutive_failures"} > 0
for: 1m
annotations:
  summary: "Skill {{ $labels.skill_id }} auto-disabled for tenant {{ $labels.tenant_id }}"
  action: "Investigate failure logs; manually re-enable with enable_skill() if safe"
```

**Expected baseline:** 0-1 per hour (one flaky Skill per tenant)  
**Threshold:** >5 per hour indicates systemic issue  
**Severity:** HIGH (Skill is down for that tenant)

### 4. Circular Reference Protection (FIX #2)

```yaml
alert: SkillCircularRefDetected
expr: rate(skill_audit_circular_ref_count[5m]) > 1
for: 1m
annotations:
  summary: "Circular reference detected in Skill {{ $labels.skill_id }}"
  action: "Check Skill output structure; audit log has scrubbed copy"
```

**Expected baseline:** 0  
**Threshold:** Any occurrence = CODE REVIEW  
**Severity:** WARNING (protection is working; log it for Skill author)

### 5. Skill Timeout (All Fixes Impact)

```yaml
alert: SkillTimeoutRate
expr: rate(skill_timeout_total[5m]) > 0.01  # >0.01 timeouts/sec = 600/min
for: 2m
annotations:
  summary: "High timeout rate on Skill {{ $labels.skill_id }}"
  action: "Check resource usage; if >4s consistent, investigate FIX #5 (Phase 2)"
```

**Expected baseline:** <1 per 10k calls  
**Threshold:** >10 per hour indicates performance regression  
**Severity:** HIGH (not CRITICAL; timeouts gracefully degrade)

---

## Metrics to Export (Prometheus Endpoints)

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `skill_audit_pii_scrubbed_count` | Counter | skill_id, tenant_id, pattern_type | Track PII redactions |
| `skill_confidence_clamped_count` | Counter | skill_id, tenant_id | Track invalid confidence |
| `skill_auto_disabled_total` | Gauge | skill_id, tenant_id, reason | Track auto-disables |
| `skill_audit_circular_ref_count` | Counter | skill_id, tenant_id | Track circular refs |
| `skill_timeout_total` | Counter | skill_id, tenant_id | Track timeouts |
| `skill_execution_latency_ms` | Histogram | skill_id, tenant_id, status | P99/P50/P95 latency |
| `skill_error_rate` | Gauge | skill_id, tenant_id | % failed executions |

---

## Dashboards (Grafana)

### Dashboard 1: Skills Health (Real-Time)

**Panels:**
- Success rate % by skill (donut chart)
- P99 latency by skill (bar chart)
- Error rate trend (line chart over 24h)
- Top 5 PII scrubs (table, last 1h)
- Auto-disabled skills (list with tenant + reason)

**Refresh:** 30 seconds  
**Audience:** On-call engineer, SRE team

### Dashboard 2: Compliance & Security (Daily)

**Panels:**
- PII scrubbing volume (trend, last 30d)
- Circular-ref occurrences (list + frequency)
- Confidence clamping events (with code location)
- Tenant isolation violations (should be 0)
- Audit trail health (hash-chain verified: % green)

**Refresh:** 5 minutes  
**Audience:** Security team, compliance auditor

---

## Runbooks (On-Call Response)

### Runbook 1: High PII Scrubbing Rate

1. Check audit log: `grep "REDACTED_PII" ~/.corvin/audit.jsonl | tail -100`
2. Identify pattern: which PII type is matching? (password, email, API key?)
3. Review Skill input: Are users passing secrets in task descriptions?
4. Remediation: Educate users OR update scrub patterns (FIX #8) OR update Skill to NOT log secrets
5. Escalate to security@corvin if credential exposure confirmed

### Runbook 2: Skill Auto-Disabled

1. Check failure logs: `grep "auto-disabled" ~/.corvin/audit.jsonl`
2. Identify root cause: Timeout? Memory leak? Invalid state?
3. Decision:
   - Transient (network blip): Call `enable_skill(skill_id, tenant_id)` to re-enable
   - Persistent (code bug): Alert Skill author; await fix + redeploy
   - Resource exhaustion: Scale horizontally (Phase 2 work)
4. Document incident in post-mortem

### Runbook 3: Confidence Score Clamping

1. Check which Skill emitted invalid confidence: `grep "Confidence score .* out of bounds" logs/`
2. Review Skill code: Why is it returning >1.0 or <0.0?
3. This is a BUG — should never happen in production
4. Page Skill author immediately (CRITICAL severity)
5. Temporarily disable Skill until fix is verified

---

## Go-Live Checklist (Before 100% Rollout)

- [ ] Prometheus scrape targets configured (all 7 metric types)
- [ ] Alert rules deployed and tested (test alert via `amtool`)
- [ ] Grafana dashboards created + team access granted
- [ ] On-call rotation updated (SRE oncall receives alerts)
- [ ] Runbooks reviewed by team + linked in Slack
- [ ] Production validation script passes (PRODUCTION_VALIDATION.py)
- [ ] Rollback procedure tested (documented in ROLLBACK.md)
- [ ] Tenant ID audit log configured (GDPR Art. 30 compliance)
- [ ] Audit trail hash-chain verification running (daily cron job)
- [ ] Team briefing: all 9 fixes explained + alert meanings

---

## Rollback Procedure (If Critical Issues Found)

**Trigger:** Any CRITICAL alert that can't be resolved in <30 min

```bash
# 1. Pause deployments
kubectl patch deployment corvin-skills --patch '{"spec": {"replicas": 0}}'

# 2. Revert to last known-good commit
git revert HEAD  # Reverts all Phase 1 fixes
git push origin main

# 3. Redeploy previous version
kubectl set image deployment/corvin-skills \
  skills=corvin-skills:PREVIOUS_COMMIT_SHA

# 4. Verify rollback
curl http://localhost:8765/api/skills/health | jq .

# 5. Post-mortem: Review which fix caused issue
grep "CRITICAL" /var/log/corvin-skills/phase1.log
```

**RTO:** 10 minutes  
**RPO:** 0 (no data loss; audit trail is append-only)

---

## Success Criteria (48 Hours Post-Launch)

| Metric | Target | Action |
|--------|--------|--------|
| P99 latency | <100ms | If >150ms → investigate (latency regressed) |
| Error rate | <0.1% | If >0.5% → rollback immediately |
| PII scrubs | <50/hour | If >100/hour → investigate data exposure |
| Auto-disables | <5/hour | If >10/hour → check Skill health |
| Audit trail | 100% hash-chain verified | If <100% → investigate corruption |
| Tenant isolation | 0 violations | If >0 → SECURITY INCIDENT |

**Go/No-Go Decision:** At 24h and 48h, team reviews metrics and decides to proceed to Phase 2 or extend Phase 1 stabilization.

---

**Launch Window:** 2026-09-04 16:00 UTC → 100% rollout to production  
**On-Call:** @ops-skills-pager (2h response SLA)  
**Escalation:** If metrics not green by 48h, pause Phase 2 and stabilize Phase 1
