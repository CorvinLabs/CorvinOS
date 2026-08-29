# Implementation Roadmap (6 Weeks)

**Project:** CorvinOS Live Stats System  
**Timeline:** 6 weeks (Nov 2026)  
**Team Size:** 4-5 engineers  
**Effort:** ~430 hours  

---

## Phase 1: Core Collector (Week 1)

**Goal:** Build collector API that receives, validates, and persists telemetry reports.

### Milestones

| Milestone | Deadline | Owner | Effort |
|---|---|---|---|
| Schema design + JSON validation | Mon | Backend | 8h |
| FastAPI collector endpoint | Tue | Backend | 16h |
| De-duplication + rate limiting | Tue | Backend | 12h |
| Ed25519 signature verification | Wed | Backend | 10h |
| InfluxDB schema + retention | Wed | Infra | 8h |
| Unit tests (90%+ coverage) | Thu | Backend | 16h |
| E2E tests (collector ↔ DB) | Thu | Backend | 8h |
| Security audit | Fri | Security | 8h |
| **Total** | **Fri** | - | **86h** |

### Deliverables

1. **Collector API Specification** (DONE: COLLECTOR_API_SPEC.md)
   - OpenAPI 3.1 endpoint definition
   - Request/response examples
   - Error handling rules
   - Rate limiting logic

2. **FastAPI Implementation** (`core/telemetry/collector/app.py`)
   ```python
   # Key files:
   # - app.py (FastAPI app)
   # - handlers.py (POST /telemetry/report)
   # - validators.py (schema + signature)
   # - dedup.py (Redis-based deduplication)
   # - errors.py (error responses)
   ```

3. **InfluxDB Setup** (`deploy/influxdb.yaml`)
   - Bucket creation script
   - Retention policy configuration
   - Time-series schema definition

4. **Unit Tests** (`tests/telemetry/collector/`)
   - `test_schema_validation.py` (50 tests)
   - `test_signature_verification.py` (20 tests)
   - `test_dedup.py` (15 tests)
   - `test_rate_limiting.py` (10 tests)

5. **Deployment** (`deploy/collector-deployment.yaml`)
   - Kubernetes manifests (4 replicas)
   - Load balancer config
   - TLS certificate setup

### Success Criteria

- ✅ Collector accepts 200+ requests/sec
- ✅ Signature verification 100% accurate
- ✅ Zero rejected valid payloads
- ✅ De-dup catches 99%+ of duplicates
- ✅ All unit tests pass
- ✅ Security audit findings: 0

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Clock skew (timestamp validation) | Use NTP sync + ±1h tolerance |
| Redis connection issues | Connection pooling + retry logic |
| InfluxDB query performance | Index on instance_id + timestamp |
| DDoS on collector | Cloudflare rate limiting + DDoS shield |

---

## Phase 2: Instance Reporter (Week 2)

**Goal:** Build telemetry agent that runs on instances, collects metrics, and sends to collector.

### Milestones

| Milestone | Deadline | Owner | Effort |
|---|---|---|---|
| Metrics collection implementation | Mon | Core | 20h |
| PII scrubbing + validation | Mon | Core | 12h |
| Ed25519 signing | Tue | Core | 8h |
| HTTP transport + retry logic | Tue | Core | 16h |
| Consent flag integration | Wed | Core | 8h |
| Audit trail logging | Wed | Core | 8h |
| Unit tests | Thu | Core | 16h |
| E2E tests (instance → collector) | Thu | Core | 12h |
| **Total** | **Fri** | - | **100h** |

### Deliverables

1. **Instance Reporter Specification** (DONE: INSTANCE_REPORTER_SPEC.md)
2. **Instance Reporter Module** (`core/telemetry/instance_reporter.py`)
   ```python
   # Key classes:
   # - TelemetryReporter (main class)
   # - MetricsCollector (collects system + usage metrics)
   # - PayloadSigner (Ed25519)
   # - PayloadScrubber (fail-closed PII removal)
   # - ReportSender (HTTP + retry)
   ```

3. **Configuration** (`spec.telemetry.*` in tenant.corvin.yaml)
4. **Integration** (`core/bootstrap.py` integration point)
5. **Tests** (`tests/telemetry/instance_reporter/`)
   - Collection accuracy tests
   - PII scrubbing tests
   - Signature verification
   - Retry logic tests
   - Integration tests

### Success Criteria

- ✅ Collects accurate metrics (≥95% accuracy vs. system truth)
- ✅ No PII in any payload (fuzz test with 100+ contamination cases)
- ✅ Signatures verify 100%
- ✅ Survives 1-hour collector downtime (buffer + replay)
- ✅ <5% CPU overhead on instances

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Metrics accuracy varies by OS | Platform-specific collection per OS |
| Token/cost tracking unavailable | Graceful fallback to 0 if unavailable |
| Network failures during boot | Start reporter in background thread |
| Buffering runs out of memory | Bounded buffer size (100 max) |

---

## Phase 3: Aggregation Pipeline (Week 2-3)

**Goal:** Build async processing that aggregates telemetry into time-series buckets.

### Milestones

| Milestone | Deadline | Owner | Effort |
|---|---|---|---|
| Aggregation worker setup | Mon | Infra | 12h |
| Time-bucket computations (1m, 1h, 1d) | Tue | Backend | 20h |
| Regional/version breakdowns | Tue | Backend | 12h |
| Cache publishing (Redis) | Wed | Backend | 8h |
| Dead-letter queue + error handling | Wed | Backend | 8h |
| Monitoring + alerting (Datadog) | Thu | Infra | 16h |
| Load testing (1000 reports/sec) | Thu | Infra | 12h |
| Integration tests | Fri | Backend | 10h |
| **Total** | **Fri** | - | **98h** |

### Deliverables

1. **Async Worker** (`core/telemetry/aggregator/worker.py`)
   - Celery/RQ task queue
   - Payload processing logic
   - Error handling

2. **Aggregation Logic** (`core/telemetry/aggregator/aggregate.py`)
   - Time-bucketing algorithm
   - Statistical computation (mean, percentiles)
   - Region/version mapping

3. **Cache Integration** (`core/telemetry/cache/publisher.py`)
   - Redis sorted sets for instance list
   - Global stats cache
   - TTL management

4. **Monitoring** (`deploy/monitoring/`)
   - Datadog dashboards
   - Alert rules (latency, error rate, queue depth)
   - Health check endpoints

5. **Load Tests** (`tests/telemetry/load/`)
   - `test_1000_reports_per_sec.py`
   - Memory usage profiling
   - Latency under load

### Success Criteria

- ✅ Processes 1000+ reports/sec
- ✅ Aggregation latency p99 <2s
- ✅ Cache hit rate >95%
- ✅ Zero data loss (DLQ handles errors)
- ✅ Monitoring alerts working
- ✅ Load test passes (1000 reports/sec for 30 min)

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Aggregation lag spikes | Increase worker count from 4 → 8 |
| Cache eviction | Pre-warm cache, monitor Redis memory |
| InfluxDB write bottleneck | Batch writes, increase batch size |
| Network partition | DLQ holds messages, retry when recovered |

---

## Phase 4: Dashboard Frontend (Week 3-4)

**Goal:** Build React dashboard at `corvin-labs.com/stats`.

### Milestones

| Milestone | Deadline | Owner | Effort |
|---|---|---|---|
| Next.js project setup | Mon | Frontend | 4h |
| Pages structure + layout | Mon | Frontend | 8h |
| KPI cards component | Tue | Frontend | 8h |
| World map (Mapbox) | Tue | Frontend | 16h |
| Time series charts (Recharts) | Wed | Frontend | 16h |
| Instance list + filters | Wed | Frontend | 16h |
| Instance detail page | Thu | Frontend | 16h |
| Real-time polling (TanStack Query) | Thu | Frontend | 8h |
| Dark mode + theme | Fri | Frontend | 8h |
| E2E tests (Playwright) | Fri | Frontend | 20h |
| **Total** | **Fri** | - | **120h** |

### Deliverables

1. **Dashboard Specification** (DONE: DASHBOARD_SPEC.md)
2. **Next.js Project** (`core/console/corvin_console/web-next-stats/`)
   - App router structure
   - Component library
   - Styling (Tailwind + CSS modules)

3. **Components** (`src/components/`)
   - KPICards.tsx
   - WorldMap.tsx (Mapbox)
   - TimeSeriesChart.tsx (Recharts)
   - InstanceTable.tsx
   - AuditTrail.tsx
   - FilterBar.tsx
   - Sidebar.tsx

4. **Hooks** (`src/hooks/`)
   - useDashboardStats.ts
   - useInstances.ts
   - useTimeSeries.ts
   - useSettings.ts

5. **API Client** (`src/lib/api.ts`)
   - Fetch wrapper
   - Error handling
   - Request deduplication

6. **Tests** (`e2e/dashboard.spec.ts`)
   - 30+ E2E tests
   - Multi-browser (Chrome, Firefox, Safari)
   - Mobile responsiveness

### Success Criteria

- ✅ Dashboard loads <2s (first paint)
- ✅ Charts responsive to window resize
- ✅ Filtering + sorting works instantly
- ✅ Dark mode toggles smoothly
- ✅ Mobile friendly (375px, 768px)
- ✅ All E2E tests pass
- ✅ Accessibility: WCAG 2.1 AA

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Map rendering slow (1000s pins) | Implement clustering + disable labels at zoom <5 |
| Chart re-render performance | Use useMemo, memoize components |
| Dark mode flashing | Hydration strategy to read localStorage early |
| Mobile performance | Code split, lazy load charts |

---

## Phase 5: GitHub Pages Archive (Week 4)

**Goal:** Set up daily static snapshots to GitHub Pages.

### Milestones

| Milestone | Deadline | Owner | Effort |
|---|---|---|---|
| GitHub Actions workflow | Mon | DevOps | 8h |
| HTML snapshot generator | Tue | Backend | 8h |
| CSV export script | Tue | Backend | 6h |
| CHANGELOG automation | Wed | Backend | 4h |
| GitHub Pages setup | Wed | DevOps | 4h |
| SEO optimization | Thu | Frontend | 6h |
| Verification tests | Thu | QA | 8h |
| **Total** | **Fri** | - | **44h** |

### Deliverables

1. **GitHub Actions Workflow** (`.github/workflows/daily-archive.yml`)
2. **Archive Generator** (`scripts/archive_generator.py`)
3. **Repository** (`CorvinLabs/corvinOS-stats-archive` on GitHub)
4. **Website Content**
   - `index.html` (redirect to latest)
   - `README.md` (explanation)
   - `CHANGELOG.md` (weekly summaries)
   - Daily snapshots (`snapshots/YYYY-MM-DD/`)

### Success Criteria

- ✅ Daily archival at midnight UTC (never missed)
- ✅ GitHub Pages loads <1s
- ✅ SEO: indexed by Google (check Search Console)
- ✅ Offline browsing works (no external resources)
- ✅ Data consistency (matches live API)

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Scheduled job fails | Alerts + retry logic in workflow |
| Disk quota exceeded | Auto-delete snapshots >90 days old |
| Large file size | Compress JSON, limit instance count to 10K |
| GitHub API rate limit | Stagger pushes, use personal access token |

---

## Phase 6: Production Hardening (Week 5-6)

**Goal:** Deploy to production with monitoring, security audit, and incident procedures.

### Milestones

| Milestone | Deadline | Owner | Effort |
|---|---|---|---|
| Security audit (GDPR, HTTPS) | Mon-Tue | Security | 24h |
| Performance tuning (collector + DB) | Tue | Infra | 16h |
| HA setup (load balancer, replication) | Wed | Infra | 20h |
| Runbook documentation | Wed-Thu | DevOps | 16h |
| Incident response procedures | Thu | DevOps | 8h |
| Soft launch (internal instances) | Fri | DevOps | 8h |
| Beta launch (early adopters) | Week 5 Fri | PM | 8h |
| Full launch + blog post | Week 6 Mon | PM | 8h |
| 2-week monitoring + support | Week 6 | SRE | 32h |
| **Total** | **Week 6 Fri** | - | **140h** |

### Deliverables

1. **Security Audit Report** (`deploy/SECURITY_AUDIT.md`)
   - GDPR compliance verification
   - HTTPS configuration audit
   - PII scrubbing validation
   - Key rotation procedures

2. **Runbook** (`deploy/RUNBOOK.md`)
   - How to scale collector (add replicas)
   - How to monitor latency spikes
   - How to restore from backup
   - How to rotate signing keys
   - Troubleshooting guide

3. **Incident Response** (`deploy/INCIDENT_RESPONSE.md`)
   - Escalation procedures
   - On-call rotation
   - Status page updates
   - Post-mortems

4. **Infrastructure as Code**
   - Kubernetes manifests (production config)
   - Terraform (if using cloud)
   - Monitoring alerts

5. **Deployment Plan**
   - Soft launch script
   - Canary rollout (10% → 50% → 100%)
   - Rollback procedures

6. **Blog Post** (Marketing)
   - Live stats system announcement
   - Feature highlights
   - API documentation
   - Getting started guide

### Success Criteria

- ✅ Security audit: 0 findings
- ✅ Collector SLA: 99.9% uptime
- ✅ Soft launch: 50+ internal instances
- ✅ Beta launch: 200+ early adopters
- ✅ Full launch: 1000+ instances
- ✅ Zero production incidents (first 2 weeks)

### Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Unexpected scale (2000+ instances) | Auto-scaling + queue monitoring |
| High latency on full launch | Progressive rollout (10% per day) |
| Data loss during migration | Dual-write to old + new system |
| Operator confusion | Comprehensive docs + video tutorial |

---

## Weekly Status Template

```
WEEK N — {PHASE NAME}
=====================

✅ Completed:
- Deliverable A
- Deliverable B

🔄 In Progress:
- Deliverable C (X% done)
- Deliverable D (Y% done)

❌ Blocked:
- Deliverable E (blocked by X)

📊 Metrics:
- Lines of code: X
- Tests written: X
- Tests passing: X

🚨 Risks:
- Risk A: High impact, low probability → Mitigation X
- Risk B: Medium impact, medium probability → Mitigation Y

📅 Next Week:
- Milestone 1
- Milestone 2
```

---

## Budget Summary

| Phase | Effort (hours) | Team | Cost (USD) |
|---|---|---|---|
| Phase 1 | 86 | Backend (2) | ~$15K |
| Phase 2 | 100 | Core (2) | ~$17K |
| Phase 3 | 98 | Backend + Infra (2) | ~$17K |
| Phase 4 | 120 | Frontend (2) | ~$21K |
| Phase 5 | 44 | DevOps (1) | ~$8K |
| Phase 6 | 140 | Full team (5) | ~$25K |
| **Total** | **588h** | - | **~$103K** |

*(Assumes $150-200/hour fully-loaded engineer cost)*

---

## Deployment Checklist

### Before Soft Launch
- [ ] All tests pass
- [ ] Security audit complete
- [ ] GDPR compliance verified
- [ ] Runbook written
- [ ] Monitoring alerts configured
- [ ] Rollback procedure tested
- [ ] Support team trained

### Before Beta Launch
- [ ] Soft launch successful (7 days)
- [ ] No incidents in soft launch
- [ ] Performance benchmarks met
- [ ] Documentation complete
- [ ] Blog post ready
- [ ] Early adopter invites sent

### Before Full Launch
- [ ] Beta launch successful (14 days)
- [ ] <1% error rate
- [ ] P99 latency <2s
- [ ] Collector healthy
- [ ] Dashboard performant
- [ ] Archive job running smoothly
- [ ] Legal review complete

---

**Status:** ✅ Roadmap ready for execution kickoff
