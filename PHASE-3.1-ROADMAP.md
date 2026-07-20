# Phase 3.1 Roadmap — Full PostgreSQL Integration (Post v0.10.50)

**Status:** Planned  
**Target Version:** v0.10.51  
**Estimated Duration:** 1-2 weeks  
**Depends On:** v0.10.50 (released)  

---

## Overview

Phase 3.1 completes the Geo-Tracking feature by wiring the live PostgreSQL backend, replacing all mock data with production telemetry, and enabling Tier 2/3 analytics.

**Current State (v0.10.50):**
- ✅ GeoIP library (Tier 1-3 support)
- ✅ Stats API endpoints (mock data)
- ✅ Website dashboard (mock data)
- ✅ PostgreSQL schema (DDL ready)
- ❌ Live database connection
- ❌ Tier 2/3 real data
- ❌ Tier 2/3 heatmap visualization

**End State (v0.10.51):**
- ✅ All of above PLUS:
- ✅ Live PostgreSQL integration
- ✅ Real Tier 1-3 telemetry flowing
- ✅ City-level heatmap (Tier 3)
- ✅ Regional trends API
- ✅ TTL-based auto-delete jobs

---

## Implementation Plan

### Task 1: Database Migration (Week 1, Day 1-2)

**What:** Execute PostgreSQL schema, create TTL jobs

**Files to Change:**
- `core/console/corvin_console/aco/geo_tracking.py` — add DB connection pool
- `core/console/routes/stats_geo.py` — replace mock data with live queries
- New: `core/console/migrations/001_instance_geo_pings.py` — Alembic migration

**Tests Needed:**
- Database connectivity (Tier 3)
- TTL job execution (14-day auto-delete)
- Query performance (indexes working)

**Acceptance Criteria:**
- [ ] `instance_geo_pings` table created
- [ ] TTL jobs running (verified in logs)
- [ ] Live data flowing (first ping recorded)
- [ ] No query regressions (< 200ms per endpoint)

---

### Task 2: GeoTracker → Database Sink (Week 1, Day 3)

**What:** Wire GeoTracker output to PostgreSQL

**Files to Change:**
- `core/console/aco/geo_tracking.py:GeoTracker.track()` — add DB insert after tracking
- Add `GeoTracker.write_to_db(result)` method
- Add connection pooling (HikariCP equivalent or asyncpg)

**Tests Needed:**
- Unit: GeoTracker writes correct row structure
- Integration: Full path (IP lookup → DB insert → query back)
- E2E: Instance ping → database → API response

**Acceptance Criteria:**
- [ ] Every geo tracking call persists to DB
- [ ] No data loss under load (100+ parallel pings)
- [ ] Audit trail correct (instance_id_hash, tier, created_at)
- [ ] Zero PII in database (regex scan)

---

### Task 3: Live API Queries (Week 1, Day 4-5)

**What:** Replace mock data in `stats_geo.py` with live PostgreSQL queries

**Endpoints to Update:**
- `GET /v1/stats/instances?tier=1|2|3` — group by (country | region | city)
- `GET /v1/stats/instances/country/{code}?tier=2|3` — region/city breakdown
- `GET /v1/stats/instances/live` — real-time aggregation
- `GET /v1/stats/insights?tier=1` — calculate live metrics

**SQL Queries Needed:**
```sql
-- Tier 1: Country aggregation
SELECT country, COUNT(*) as instances, COUNT(CASE WHEN active_24h THEN 1 END) as online
FROM instance_geo_pings
WHERE created_at >= CURRENT_DATE - INTERVAL '1 day'
GROUP BY country
ORDER BY instances DESC;

-- Tier 2: Region breakdown (per country)
SELECT country, region, COUNT(*) as instances
FROM instance_geo_pings
WHERE created_at >= CURRENT_DATE - INTERVAL '1 day'
AND geo_consent_tier >= 2
GROUP BY country, region
ORDER BY instances DESC;

-- Tier 3: Heatmap (city + grid)
SELECT city, geo_grid_lat, geo_grid_lng, COUNT(*) as instances
FROM instance_geo_pings
WHERE created_at >= CURRENT_DATE - INTERVAL '1 day'
AND geo_consent_tier >= 3
GROUP BY city, geo_grid_lat, geo_grid_lng
ORDER BY instances DESC;
```

**Tests Needed:**
- Load testing: 1M+ rows, queries still < 200ms
- Accuracy: aggregate counts match raw count
- Edge cases: empty results, ties in ranking

**Acceptance Criteria:**
- [ ] All 4 endpoints return live data (not mock)
- [ ] Response times < 200ms at 1M row scale
- [ ] Tier-gating enforced (Tier 2 data only if consent)
- [ ] No N+1 queries (single query per endpoint)

---

### Task 4: Heatmap & Regional Trends (Week 2, Day 1-2)

**What:** Build Tier 3 heatmap visualization + regional trend charts

**Files to Change:**
- `stats-hero.html` — add heatmap layer (Leaflet.heat)
- `stats-hero.html` — add trends chart (Chart.js time-series)
- New endpoint: `GET /v1/stats/trends?region=EU&days=7` — time-series data

**Features:**
- 📍 Heatmap: Leaflet + Circle markers at 10km grid cells
- 📈 Trends: Region-by-region 7-day activity trend
- 🎯 Cluster breakdown: Top cities per region

**Tests Needed:**
- E2E: Map renders without freeze (performance)
- E2E: Trends chart updates on data change
- Unit: Time-series aggregation logic

**Acceptance Criteria:**
- [ ] Heatmap renders 100+ markers smoothly
- [ ] Trends chart shows 7-day progression (line chart)
- [ ] Mobile heatmap still usable (touch interactions)
- [ ] No memory leaks (re-render 10x safely)

---

### Task 5: TTL & Data Lifecycle (Week 2, Day 3)

**What:** Implement TTL enforcement, verify data cleanup

**Implementation:**
- PostgreSQL `DELETE` scheduled job (runs daily 2 AM UTC)
- CloudSQL scheduler OR cron job in corvin-daemon
- Logging: every deletion logged to audit.jsonl

**Tests Needed:**
- Unit: TTL calculation correct (30d from created_at)
- Integration: Job runs, deletes correct rows
- E2E: Verify audit trail for deletions

**Acceptance Criteria:**
- [ ] Tier 2 rows auto-delete after 30 days
- [ ] Tier 3 rows auto-delete after 14 days
- [ ] Deletion audit logged with count
- [ ] Zero accidental deletions of Tier 1 data

---

### Task 6: Performance & Optimization (Week 2, Day 4-5)

**What:** Database indexing, query optimization, caching

**Optimizations:**
- Composite indexes: `(country, created_at)`, `(region, country, created_at)`
- Query plan analysis (EXPLAIN ANALYZE)
- Materialized view for daily aggregates (pre-compute at midnight)
- Redis caching for hourly snapshots

**Acceptance Criteria:**
- [ ] All queries < 100ms (p95) at 100M row scale
- [ ] Index sizes tracked (no runaway disk growth)
- [ ] Query plans confirmed efficient (no sequential scans)
- [ ] Cache hit rate > 90% for repeated queries

---

### Task 7: E2E Testing & Release (Week 2, Day 6-7)

**What:** Full regression testing, update docs, release v0.10.51

**Tests:**
- Replay v0.10.50 test suite against live DB
- New Tier 2/3 E2E tests (heatmap, trends)
- Load test: 1000 concurrent pings
- Compliance re-audit (data lifecycle verification)

**Release:**
- Update CHANGELOG-0.10.51.md
- ADR-0206 (post-mortem, lessons learned)
- Tag v0.10.51 + GitHub Release
- PyPI upload

**Acceptance Criteria:**
- [ ] 100+ tests passing (old + new)
- [ ] Zero regressions vs v0.10.50
- [ ] Compliance audit passed (TTL, anonymization)
- [ ] Deployment to production verified

---

## Dependencies

- **v0.10.50:** Must be released first (tests depend on it)
- **PostgreSQL:** Production instance with replication (if applicable)
- **GeoIP Database:** Already included in v0.10.50

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|-----------|
| Query performance degrades at scale | Medium | High | Early load testing + materialized views |
| TTL job deletes Tier 1 data accidentally | Low | Critical | Unit test, dry-run job first |
| Heatmap freezes UI on large datasets | Medium | Medium | Clustering + lazy-load tiles |
| Data consistency during replication lag | Low | High | Read-after-write with replica consistency |

---

## Success Criteria

✅ **Functional:**
- All API endpoints return live data (not mock)
- Heatmap renders smoothly for 100K+ geopoints
- TTL cleanup working (verified in 30-day production run)

✅ **Performance:**
- Query times < 100ms p95 at 100M rows
- Heatmap FPS > 30 on mobile
- No memory leaks in dashboard

✅ **Compliance:**
- GDPR/DSGWO re-audit passed
- Zero accidental Tier 1 deletions
- Audit trail complete for all ops

✅ **Release:**
- v0.10.51 on PyPI
- Zero regressions vs v0.10.50
- Documentation updated

---

## Estimated Effort

| Task | Effort | Days | Parallel |
|------|--------|------|----------|
| 1: DB Migration | 8h | 2 | Solo |
| 2: GeoTracker→DB | 12h | 2 | After #1 |
| 3: Live Queries | 16h | 3 | After #2 |
| 4: Heatmap | 12h | 2 | Parallel w/ #3 |
| 5: TTL | 8h | 1 | Parallel w/ #3-4 |
| 6: Perf/Opt | 16h | 2 | Parallel w/ #3-5 |
| 7: E2E+Release | 12h | 1 | Final |
| **TOTAL** | **84h** | **~10 days** | **Pipelined** |

---

## Success Outcome

**CorvinOS v0.10.51 delivers:**
- 🚀 Fully live, production-grade geo-tracking
- 📊 Interactive city-level heatmaps
- 📈 Regional trend analytics
- 🔒 DSGWO-compliant data lifecycle
- ⚡ Sub-100ms query performance at scale
- 📝 Complete audit trail for every operation

**corvin-labs.com/stats becomes:** The definitive real-time deployment intelligence dashboard for CorvinOS.

---

## Next Reviewer

→ shumway (maintainer)  
→ Review this roadmap for feasibility before Phase 3.1 kickoff
