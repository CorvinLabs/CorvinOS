# CorvinOS v0.10.53 Production Checklist

## Pre-Flight (Before Going Live)

- [ ] PostgreSQL 12+ provisioned and accessible
- [ ] DATABASE_URL environment variable set
- [ ] Schema migration completed (`instance_geo_pings` created)
- [ ] Test data seeded (geo_seed.py executed)
- [ ] API endpoints verified (all 3 tiers returning data)
- [ ] Website tabs load without errors
- [ ] Deployment metrics visible (version, uptime, DSGVO)
- [ ] Error messages display correctly (no 500s)

## Deployment (Production)

### Database
- [ ] PostgreSQL connection string validated
- [ ] Schema migration: `python -c "from aco.geo_schema import migrate_schema; migrate_schema()"`
- [ ] Verify table: `psql $DATABASE_URL -c "\dt instance_geo_pings"`
- [ ] Index verification: `psql $DATABASE_URL -c "\di"`

### API
- [ ] Console service started
- [ ] Tier 1 endpoint responds: `/v1/stats/instances?tier=1`
- [ ] Tier 2 endpoint responds: `/v1/stats/instances/country/DE?tier=2`
- [ ] Tier 3 endpoint responds: `/v1/stats/heatmap?country=DE`
- [ ] All endpoints return real data (not 500 errors)

### Website
- [ ] Stats page loads: `https://corvin-labs.com/stats`
- [ ] Tab 1 (Tier 1): Map renders with countries
- [ ] Tab 2 (Tier 2): Dropdown works, regions display
- [ ] Tab 3 (Tier 3): Heatmap renders with cities
- [ ] Deployment banner shows v0.10.53
- [ ] DSGVO compliance notice visible
- [ ] 60-second auto-refresh working

### Monitoring
- [ ] Database health check: connection works
- [ ] API health check: endpoints respond <200ms
- [ ] Website performance: loads <2 seconds
- [ ] Error logs: no 500 errors, no SQL errors
- [ ] TTL jobs: scheduled for daily 2 AM UTC

## Post-Deployment (Daily Monitoring)

- [ ] Database size < 10GB (for 10M rows)
- [ ] Query performance < 100ms (p95)
- [ ] Website availability 99.9%+
- [ ] Error rate < 0.1%
- [ ] TTL cleanup jobs completed successfully
- [ ] Disk space adequate (>20% free)

## Rollback Plan (If Issues)

1. Disable DATABASE_URL → falls back to mock data
2. Website still accessible (mock fallback)
3. API returns mock data (Tier 1 only)
4. No data loss, full reversibility

