# Skill System Operator Runbook (Phase 8)

Quick reference for monitoring and troubleshooting CorvinOS Skill System hardening layer (rate-limiting, circuit-breaker, cache).

## Monitoring Commands

### Check System Health
```bash
corvin skills health
```
Shows overall status (HEALTHY/DEGRADED), cache hit-rate, circuit-breaker state.

**Green:** hit-rate > 70%, circuit-breaker CLOSED  
**Yellow:** hit-rate 50–70% or circuit-breaker HALF_OPEN  
**Red:** hit-rate < 50% or circuit-breaker OPEN

### View Cache Statistics
```bash
corvin skills cache-stats [--tenant TENANT_ID] [--format {text,json}]
```
- Size / max_size (current entries in cache, capacity)
- Hit rate (target: >70%)
- Evictions (high count = cache too small)
- Invalidations (each manifest write clears cache)

**Interpretation:**
| Metric | Threshold | Action |
|---|---|---|
| hit_rate | < 70% | Increase TTL or max_size, check if manifest is unstable |
| evictions | > 100 | Increase max_size (256 is default) |
| invalidations | > 1/min | Skill system is unstable (skills creating frequently) |

### View Circuit Breaker State
```bash
corvin skills circuit-breaker
```
- State: CLOSED (healthy), OPEN (failing), HALF_OPEN (recovering)
- Failure count (resets when CLOSED)
- Last failure timestamp

**What each state means:**
- **CLOSED:** Manifest loading successful; all requests allowed
- **OPEN:** Manifest loading failed ≥5 times; requests denied without trying (fail-fast)
- **HALF_OPEN:** Recovery timeout expired; testing next request; will CLOSE on 2 successes or OPEN on 1 failure

### View Rate Limiter Quota
```bash
corvin skills rate-limiter USER_ID
```
Shows token bucket state for a specific client/user.
- tokens (remaining)
- rate_limit_per_minute (default: 1000)
- quota_status (GREEN > 50%, YELLOW 0–50%, RED exhausted)

**Per-minute refill:** 1000 / 60 ≈ 16.7 tokens/sec. At 1000 req/min, each request costs ~1 token.

## Recovery Procedures

### Issue: Cache Hit Rate Below 70%

**Diagnose:**
```bash
corvin skills cache-stats --format json | jq '.hit_rate'
```

**Causes:**
1. Manifest changes frequently → invalidations too high
2. Cache too small for working set → evictions high
3. TTL too short → entries expire before reuse

**Recovery:**

1. **If evictions > 100:**
   ```bash
   # Increase cache max_size (default 256)
   # Edit SkillCache(max_size=512) in resolver.py, redeploy
   corvin skills cache-clear  # Clear old stats
   ```

2. **If invalidations high:**
   ```bash
   # Skill registry is unstable. Check skill-forge/manifest.json write rate.
   # High-frequency creates → consider batching or debouncing.
   ls -la ~/.corvin/tenants/_default/skill-forge/manifest.json
   ```

3. **If hit_rate still low:**
   Increase TTL (default 30 min):
   ```bash
   # Edit SkillCache(ttl_minutes=60) in resolver.py, redeploy
   ```

### Issue: Circuit Breaker OPEN

**Diagnose:**
```bash
corvin skills circuit-breaker
```

**Causes:**
1. Manifest file corrupted or unreadable
2. Disk I/O failures
3. Permissions issue on skill-forge directory

**Recovery:**

1. **Check manifest integrity:**
   ```bash
   cd ~/.corvin/tenants/_default/skill-forge/
   cat manifest.json | jq .  # Should parse without errors
   ```

2. **Check file permissions:**
   ```bash
   ls -la ~/.corvin/tenants/_default/skill-forge/manifest.json
   # Should be readable by the CorvinOS process user
   ```

3. **Check disk space:**
   ```bash
   df ~/.corvin/
   # Should have > 100MB free
   ```

4. **If manifest is corrupted:** restore from backup or rebuild
   ```bash
   cp ~/.corvin/tenants/_default/skill-forge/manifest.json.bak manifest.json
   # Or: corvin skills rebuild-manifest (TODO: implement if needed)
   ```

5. **Wait for recovery:** Circuit-breaker auto-recovers after 60 seconds (recovery_timeout) if manifest load succeeds.
   ```bash
   sleep 61 && corvin skills circuit-breaker  # Should be HALF_OPEN or CLOSED
   ```

### Issue: Rate Limit Exceeded (429 Responses)

**Diagnose:**
```bash
corvin skills rate-limiter USER_ID
# If tokens = 0 and last_refill is recent, user is rate-limited
```

**Causes:**
1. User making too many concurrent requests (>16/sec)
2. Skill system used for bulk operations without batching

**Recovery:**

1. **For single user:** They are over quota. Either:
   - Wait for refill (next minute, new tokens)
   - Increase rate_limit_per_minute (default 1000 = 16/sec)

2. **For bulk operations:** Batch requests or use resolve_many()
   ```python
   # Instead of:
   for skill in skills: resolver.resolve(skill)
   
   # Use:
   results = resolver.resolve_many(skills)  # Single batch query
   ```

3. **To adjust global rate limit:** Edit SkillServiceHardening() init in resolver.py or monitoring.py:
   ```python
   hardening = SkillServiceHardening(rate_limit_per_minute=2000)  # 2x default
   ```

### Issue: Cache Corruption

**Symptom:** `get()` returns None for skills that should exist; manifest.json is invalid JSON.

**Recovery:**
```bash
# Option 1: Clear cache and reload from disk
corvin skills cache-clear

# Option 2: Repair manifest manually
cd ~/.corvin/tenants/_default/skill-forge/
# Restore from backup
cp manifest.json.bak manifest.json
```

## Monitoring Dashboard

The Console dashboard shows:
- **Cache Hit Rate:** (under Skills → Cache tab) Target >70%
- **Circuit Breaker Status:** (Health icon) CLOSED = green, OPEN = red
- **Rate Limiter Quota:** (under Skills → Quota tab) per-user quota status

API endpoints for custom dashboards:
- `GET /api/skills/cache-stats` → cache metrics
- `GET /api/skills/circuit-breaker` → circuit-breaker state
- `GET /api/skills/rate-limiter/{client_id}` → quota state
- `GET /api/skills/health` → synthesized health

All endpoints auth-gated; require valid Console session.

## Log Location

Skill System logs (if enabled) appear in:
```
~/.corvin/logs/skills.log
```

Look for:
- `cache_hit`, `cache_miss`, `cache_eviction` events
- `circuit_breaker_state_change` (CLOSED→OPEN, etc.)
- `rate_limit_denied` (quota exhausted)
- `manifest_load_error` (disk I/O, corruption)

## Capacity Planning

| Metric | Default | Recommended Range | Impact if Exceeded |
|---|---|---|---|
| max_size (cache) | 256 entries | 256–512 | High eviction rate, low hit-rate |
| ttl_minutes | 30 min | 15–60 | TTL too short → cache churn; too long → stale manifests |
| rate_limit_per_minute | 1000 req/min | 500–2000 | Too low → users rate-limited; too high → possible cascading failures |
| circuit_breaker failure_threshold | 5 failures | 3–10 | Too low → false positives (single glitch opens); too high → slow to fail-fast |
| recovery_timeout_seconds | 60 sec | 30–120 | Too short → aggressive retry; too long → slow recovery |

**Tuning guide:**
- **High-traffic install:** Increase max_size to 512, rate_limit to 2000
- **Unstable manifest (frequent updates):** Lower TTL to 15 min
- **High-reliability required:** Lower circuit_breaker failure_threshold to 3

Edit in resolver.py, skills_monitoring.py, or hardening.py + redeploy.

## Alerts to Set Up

Recommended monitoring thresholds (if using external monitoring):

1. **Cache hit-rate < 60%** → warning
2. **Circuit-breaker OPEN** → critical
3. **Rate limiter quota > 90%** → warning (for any user)
4. **Manifest load latency > 1 sec** → warning
5. **Cache invalidations > 5 per minute** → warning (unstable manifest)

Route alerts to on-call team. E-mail template:
```
[ALERT] Skill System Degraded: {issue}
  Circuit-breaker: {state}
  Cache hit-rate: {hit_rate}%
  Action: Run `corvin skills health` for diagnostics
```

## See Also

- [ADR-0422: Lazy-Load Cache (1000 skills)](../../Corvin-ADR/decisions/ADR-0422-lazy-load-cache-1000-skills.md)
- [ADR-0425: Skills Monitoring API](../../docs/adr/ADR-0425-skills-monitoring-api.md)
- Phase 8 integration tests: `core/skills/tests/test_integration_resolver_hardening.py`
