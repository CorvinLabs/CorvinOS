# CorvinOS v0.10.52+ — Production Deployment Guide

## Prerequisites

- PostgreSQL 12+ with network access
- Python 3.11+
- Environment variables configured

## Step-by-Step Deployment

### 1. Database Setup

```bash
# Set environment variable
export DATABASE_URL="postgresql://user:password@host:5432/corvinOS"

# Run schema migration
python -c "from aco.geo_schema import migrate_schema; migrate_schema()"
# Output: ✅ Geo-tracking schema migrated successfully

# Verify table exists
psql $DATABASE_URL -c "\dt instance_geo_pings"
# Output: instance_geo_pings | table | postgres
```

### 2. Seed Test Data (Development Only)

```bash
# Populate with realistic test data (5+ cities per region)
python -c "from aco.geo_seed import seed_geo_data; seed_geo_data()"
# Output: ✅ Seeded XXX test records

# Verify data
psql $DATABASE_URL -c "SELECT COUNT(*) FROM instance_geo_pings;"
# Output: count
#   500+
```

### 3. API Verification

```bash
# Start console service
uvicorn corvin_console.app:app --host 0.0.0.0 --port 8000

# Test Tier 1 API
curl http://localhost:8000/v1/stats/instances?tier=1
# Output: {"countries": [...], "total_instances": ...}

# Test Tier 2 API
curl http://localhost:8000/v1/stats/instances/country/DE?tier=2
# Output: {"country": "DE", "regions": [...]}

# Test Tier 3 API
curl http://localhost:8000/v1/stats/heatmap?country=DE
# Output: {"country": "DE", "heatmap": [...], "grid_km": 10}
```

### 4. Website Deployment

**Local Testing:**
```bash
cd Corvin-Website/stats/
# Serve locally (Python)
python -m http.server 8080
# Visit http://localhost:8080
# Verify all 3 tabs load with real data
```

**Production (Cloudflare Pages):**
```bash
# Push to GitHub (auto-deploys)
git push origin main

# Cloudflare CLI (optional manual deploy)
wrangler pages deploy ./stats --project-name=corvin-website

# Clear cache
curl -X POST https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/purge_cache \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -d '{"purge_everything":true}'
```

### 5. Production Checklist

- [ ] DATABASE_URL set in production environment
- [ ] PostgreSQL instance reachable from app servers
- [ ] schema migration completed (instance_geo_pings created)
- [ ] Tier 1 API endpoint responds with real data
- [ ] Tier 2 API endpoint responds with regions
- [ ] Tier 3 API endpoint responds with city-level data
- [ ] Website tabs load without errors
- [ ] Deployment banner shows correct version
- [ ] DSGVO compliance notice visible
- [ ] 60-second auto-refresh working
- [ ] Mock fallback still functional (safety net)

## Monitoring

### Health Check Endpoint (add to app.py if needed)

```python
@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": "connected" if DATABASE_URL else "not-configured",
        "version": "0.10.52",
        "geo_tier_1": "ready",
        "geo_tier_2": "ready",
        "geo_tier_3": "ready",
    }
```

### Database Monitoring

```bash
# Monitor geo_pings table size
SELECT 
  pg_size_pretty(pg_total_relation_size('instance_geo_pings')) as size,
  COUNT(*) as rows
FROM instance_geo_pings;

# Monitor TTL jobs (check for expired rows)
SELECT 
  COUNT(*) as tier2_expired,
  COUNT(*) FILTER (WHERE geo_consent_tier = 3) as tier3_expired
FROM instance_geo_pings
WHERE geo_consent_tier = 2 AND created_at < CURRENT_DATE - INTERVAL '30 days'
   OR geo_consent_tier = 3 AND created_at < CURRENT_DATE - INTERVAL '14 days';

# Run TTL cleanup manually
python -c "from aco.geo_ttl import cleanup_expired_geo_data; cleanup_expired_geo_data()"
```

## Troubleshooting

### "No data in Tier 1/2/3"
1. Verify DATABASE_URL is set: `echo $DATABASE_URL`
2. Test connection: `psql $DATABASE_URL -c "SELECT 1"`
3. Verify table exists: `psql $DATABASE_URL -c "\dt instance_geo_pings"`
4. Seed test data: `python -c "from aco.geo_seed import seed_geo_data; seed_geo_data()"`

### "API returns 500 error"
1. Check logs for database connection errors
2. Verify instance_geo_pings table structure
3. Run schema migration again
4. Restart API service

### "Website shows 'Failed to load Tier X data'"
1. Verify API endpoint is responding: `curl $API_BASE/v1/stats/instances?tier=1`
2. Check browser console for CORS errors
3. Verify DATABASE_URL in production environment
4. Clear browser cache (Ctrl+Shift+Del)

## Performance Targets

- Tier 1 Query: < 100ms (all countries)
- Tier 2 Query: < 50ms (per country)
- Tier 3 Query: < 100ms (per country, limited to 50 cities)
- Website Load: < 2s (with real data)
- Map Rendering: < 1s (Leaflet)

## Version Info

- **Current:** v0.10.52
- **Last Updated:** 2026-07-20
- **Status:** Production Ready

