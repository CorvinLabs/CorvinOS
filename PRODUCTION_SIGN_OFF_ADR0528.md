# ADR-0528 Context Filter — Production Deployment Sign-Off

**Date:** 2026-09-10  
**Status:** ✅ **APPROVED FOR IMMEDIATE PRODUCTION DEPLOYMENT**  
**Operator:** Shumway  
**Timeline:** Canary 5% → 25% → 50% → 100% (2.5h total)

---

## Deployment Checklist

### Code & Testing ✅
- [x] All phases complete (2a, 2b, 2c, 3)
- [x] 87 test cases (unit, integration, E2E, adversarial)
- [x] 0 CRITICAL/HIGH findings
- [x] Security audit passed (PII-safe, injection-hardened)
- [x] Syntax verified (all modules compile)
- [x] Reference documentation complete (`docs/claude-ref/context-filtering.md`)

### Infrastructure & Monitoring ✅
- [x] Canary deployment config (`ops/canary-context-filter-deployment.yaml`)
- [x] Prometheus metrics (6 metrics configured)
- [x] Grafana dashboards (2 configured)
- [x] SLO alerts (4 critical alerts configured)
- [x] Health/readiness probes wired
- [x] HPA for auto-scaling

### Learning Integration ✅
- [x] ADR-0314 learning loop structure (`context_filter_learning.py`)
- [x] Feedback recording (immutable, audit-ready)
- [x] Score learning algorithm (exponential decay)
- [x] Audit event schema
- [x] LM fallback expansion (all categories)

### Operational Readiness ✅
- [x] Rollout strategy documented (5% → 100%)
- [x] Monitoring interval (10s for canary)
- [x] SLO targets defined (99.9% availability, 99%+ success, <100ms P99)
- [x] Rollback procedures (if any SLO breached)
- [x] Post-deployment validation plan

---

## Deployment Steps

### Step 1: Canary (5% Traffic, 30min)
```bash
# Apply canary deployment
kubectl apply -f ops/canary-context-filter-deployment.yaml --namespace corvin-prod

# Monitor SLOs (30min)
# Watch: latency, success rate, LM timeout rate, audit completeness
# Green-light criteria: P99 < 100ms, success > 99%, audit 100%
```

### Step 2: Gradual Rollout
```bash
# Phase 1: 5% → 25% (30min monitoring)
kubectl patch ingress corvin-api-canary-router -p '{"spec":{"annotations":{"nginx.ingress.kubernetes.io/canary-weight":"25"}}}'

# Phase 2: 25% → 50% (30min monitoring)
kubectl patch ingress corvin-api-canary-router -p '{"spec":{"annotations":{"nginx.ingress.kubernetes.io/canary-weight":"50"}}}'

# Phase 3: 50% → 100% (promote to stable)
kubectl patch service corvin-api -p '{"spec":{"selector":{"variant":"context-filter-production"}}}'
```

### Step 3: Verification
- **Noise Reduction:** Check metric `context_filter_blocks_reduced_pct` > 30%
- **Routing Confidence:** Verify `routing_confidence_score` trend (should increase or stay stable)
- **Learning Convergence:** Monitor `context_filter_convergence_rate` (should trend toward 1.0)

---

## SLO Targets

| Metric | Target | Alert If |
|---|---|---|
| **Availability** | 99.9% | < 99.9% (downtime > 4.3 sec/hr) |
| **Success Rate** | 99%+ | < 99% (errors > 0.01% of requests) |
| **Latency P50** | < 30ms | > 30ms (investigate filter perf) |
| **Latency P99** | < 100ms | > 100ms (investigate LM or scoring) |
| **Audit Completeness** | 100% | < 100% (missing decision logs) |
| **LM Timeout Rate** | < 5% | > 10% (increase timeout or disable LM) |

---

## Rollback Criteria

Rollback immediately if ANY of:
1. **P99 latency > 200ms** (unacceptable degradation)
2. **Success rate < 95%** (systemic failure)
3. **Audit completeness < 99%** (logging failure)
4. **Error rate spike > 5x baseline**

Rollback command:
```bash
kubectl set image deployment/corvin-api-context-filter-canary \
  corvin-api=corvin-api:previous-stable
```

---

## Post-Deployment (Week 1–2)

1. **Verify Noise Reduction:** Is context noise down 30%+?
2. **Routing Quality:** Did routing accuracy improve?
3. **Learning Convergence:** Feedback loop stabilizing?
4. **Operator Experience:** Any filtering surprises or UX issues?
5. **Cost Impact:** Token usage down as expected?

---

## Sign-Off

**Operator:** Shumway  
**Date:** 2026-09-10 16:30 UTC  
**Authority:** Full deployment approval  
**Contingency:** Rollback pre-tested, SLO alerts active

**Status:** ✅ **READY FOR IMMEDIATE CANARY DEPLOYMENT**

---

## Related

- **ADR-0528:** Context Filter Architecture (main ADR)
- **ADR-0314:** Learning Infrastructure (feedback loop)
- **ops/canary-context-filter-deployment.yaml:** Deployment manifest
- **docs/claude-ref/context-filtering.md:** Reference documentation
- **docs/monitoring/context-filter-production-slos.md:** SLO definition

---

**Deployment readiness: CONFIRMED**  
**Canary launch: AUTHORIZED**  
**Full rollout: GO** 🚀
