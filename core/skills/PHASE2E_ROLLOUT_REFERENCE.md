# Phase 2e: Production Rollout Plan (Reference)

**Status:** Phase 1 production monitoring already in place.  
**This doc:** Phase 2-specific rollout strategy (reuses Phase 1 framework).

---

## Reuse from Phase 1

Phase 1 deployed:
- ✅ Prometheus metrics (Phase 1 Learning Optimizer + Phase 2 Learning Optimizer same metrics)
- ✅ Grafana dashboards (Skills Health, Compliance & Security)
- ✅ On-call runbooks (PII, auto-disable, confidence clamping)
- ✅ Rollback procedure (RTO 10 min)
- ✅ 48h observation SLA

**Phase 2 reuses ALL Phase 1 infrastructure** — no new deployment framework needed.

---

## Phase 2-Specific Additions

### New Metrics (from 2a, 2b, 2c)

| Metric | Source | Purpose |
|--------|--------|---------|
| `learning_feedback_count` | 2a.1 | Track feedback ingestion rate |
| `learning_drift_detected_count` | 2a.2 | Track drift detection frequency |
| `learning_config_tuned_count` | 2a.3 | Track config updates applied |
| `learning_canary_success_rate` | 2a.4 | Track A/B test winner rate |
| `manifest_validation_errors` | 2b | Track manifest parse failures |
| `skill_dependencies_unresolved` | 2b | Track unresolved DAG deps |
| `workflow_parallelism_ratio` | 2c | Track parallel vs serial decisions |
| `security_threat_level_avg` | 2c | Track avg threat score |
| `flow_guard_blocks_count` | 2c | Track data flow violations |

### New Alerts (Phase 2 specific)

```yaml
alert: LearningFeedbackDryUp
expr: rate(learning_feedback_count[24h]) == 0
for: 6h
annotations:
  summary: "No feedback received for {{ $labels.skill_id }}"
  action: "Check if users are rating decisions; encourage feedback"
severity: WARNING

alert: ManifestValidationFailure
expr: increase(manifest_validation_errors[1h]) > 5
for: 5m
annotations:
  summary: "Manifest validation failing"
  action: "Review recent manifest submissions; check schema compliance"
severity: HIGH

alert: DAGResolutionFailure
expr: increase(skill_dependencies_unresolved[1h]) > 0
for: 2m
annotations:
  summary: "Skill dependency DAG resolution failed"
  action: "Check manifest dependencies; resolve circular references"
severity: CRITICAL
```

### New Dashboards (Phase 2)

1. **Learning Feedback Loop (Real-time)**
   - Feedback rate (per skill, per hour)
   - Drift detected (timeline)
   - Config tuned (when + what changed)
   - Canary results (success rate comparison)

2. **Skill Marketplace (Daily)**
   - Manifest validation errors (trend)
   - DAG resolution failures (blocked Skills)
   - Community Skill submissions (approval rate)
   - Popular Skills (usage + confidence)

3. **Workflow & Security (Real-time)**
   - Parallelism decisions (% parallel vs serial)
   - Threat levels (by origin)
   - Flow guard blocks (by classification)

---

## Rollout Sequence (Canary → Full)

### Stage 1: Canary (5% traffic, 24h)
- Deploy 2a (Learning Optimizer)
- Monitor: Feedback ingestion, drift detection, config tuning
- Success criteria: Feedback rate > 10/hour, drift detected in <1 hour

### Stage 2: Canary Expanded (10% traffic, 24h)
- Add 2b (Manifest Validation) to canary pool
- Monitor: Manifest parsing, DAG resolution
- Success criteria: 0 manifest errors, DAG resolves in <100ms

### Stage 3: Staged Rollout (25% → 50% → 75%)
- Add 2c (OS-Skills) incrementally
- Monitor: Workflow optimization, security scoring, flow guard
- Success criteria: Confidence > 0.9 for all Skills

### Stage 4: Full Production (100%)
- Enable for all tenants
- Start 48h observation (same as Phase 1)
- Success criteria: All metrics green, no CRITICAL alerts

---

## Deployment Commands (Phase 2)

```bash
# 1. Pre-flight (same as Phase 1)
python3 core/skills/PRODUCTION_VALIDATION.py  # Validate all fixes
python3 core/learning/feedback_ingestion.py   # Validate 2a.1
python3 core/learning/confidence_drift.py     # Validate 2a.2
python3 core/learning/config_tuner.py         # Validate 2a.3
python3 core/skills/manifest_validator.py     # Validate 2b
python3 core/skills/os_skills_phase2.py       # Validate 2c

# 2. Build image (includes Phase 1 + Phase 2)
docker build -t corvin-skills:2026-09-24-phase2 .

# 3. Canary 5%
kubectl set image deployment/corvin-skills \
  skills=gcr.io/corvin-prod/corvin-skills:2026-09-24-phase2 \
  --record

# 4. Monitor for 24h
watch -n 30 'curl -s http://prometheus:9090/api/v1/query?query=learning_feedback_count | jq .'

# 5. If green: expand to 50%
# If red: rollback
kubectl rollout undo deployment/corvin-skills

# 6. When 100%: Start 48h observation
# Success criteria (same as Phase 1):
#   - P99 latency < 100ms
#   - Error rate < 0.1%
#   - No CRITICAL alerts
#   - All new metrics green
```

---

## Risk Mitigation (Phase 2)

| Risk | Phase 1 | Phase 2 |
|------|---------|---------|
| Config tuning diverges | Gradient descent clamp ±10% | A/B canary validates before apply |
| Manifest cycle | Static validation | DAG resolver + cycle detection |
| Feedback injection | Time-bound window | Audit trail + user validation |
| Security bypass | Flow guard hard blocks | Threat model + monitoring |

**Rollback trigger:** Any CRITICAL alert that persists >30 min.

---

## Success Metrics (48h SLA, Phase 2)

| Metric | Phase 1 Target | Phase 2 Target |
|--------|--------|--------|
| P99 Latency | <100ms | <120ms (config tuning overhead) |
| Error Rate | <0.1% | <0.15% (manifest parsing can fail) |
| Feedback Rate | — | >50/hour (closed-loop requirement) |
| Drift Detection | — | >1 per day (normal signal) |
| Config Tuned | — | >1 per day (optimization active) |
| Manifest Errors | — | <5/day (validation working) |
| DAG Resolve Failures | — | 0 (hard blocker) |

---

## Go/No-Go Decision (Phase 2)

**At 24h:**
- If all metrics green → expand canary to 50%
- If any CRITICAL alert → investigate + fix before expanding

**At 48h:**
- If all metrics green → declare Phase 2 STABLE
- If any HIGH alert → extend observation to 72h
- If any CRITICAL → full rollback + post-mortem

**Phase 3 kickoff:** After Phase 2 declared STABLE (48h+ green)

---

## Diff from Phase 1 Rollout

Phase 2 **reuses Phase 1 entirely** — same canary framework, same monitoring, same SLA.

**Only delta:** New metrics, new alerts, new dashboards (all backward compatible).

No new deployment procedure needed. Phase 2 is additive.

---

**Ready to deploy Phase 2 on top of Phase 1 production.** 🚀
