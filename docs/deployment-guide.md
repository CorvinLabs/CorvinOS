# Deployment Guide: Zero-Downtime Skill Updates

Deploy Skill changes without restarting CorvinOS. Stage the rollout, monitor metrics, and rollback instantly if needed.

![Staged Rollout Timeline](docs/assets/staged-rollout-timeline.svg)

---

## Three-Stage Deployment Pattern

### Stage 1: Canary (10%, 24 hours)

```bash
corvin skills deploy os.vibe_engineering v0.4 --canary 10%

Output:
  Deployment started
  Stage: Canary (10% traffic)
  v0.3 (old): 90%
  v0.4 (new): 10%
  Monitor duration: 24 hours
```

**What's Happening:**
- 10% of requests routed to v0.4
- 90% still on v0.3 (safe fallback)
- All metrics collected in separate bucket

**Monitor These:**
- Error rate (target: < 0.1% increase)
- Latency (target: p99 < 50ms over v0.3)
- Confidence score (target: > 0.70)
- User feedback (target: > 80% positive)

**If metrics look good after 24h:**
```bash
corvin skills scale os.vibe_engineering 50% --after-stage canary
```

### Stage 2: Scale (50%, 24 hours)

```bash
corvin skills scale os.vibe_engineering 50%

Output:
  Stage: Scale (50% traffic)
  v0.3 (old): 50%
  v0.4 (new): 50%
  Monitor duration: 24 hours
```

**Same monitoring as Stage 1**, but with more traffic. Canary was a dry run; now we have real scale.

**If metrics still good:**
```bash
corvin skills scale os.vibe_engineering 100%
```

### Stage 3: Full (100%, monitor 1 hour)

```bash
corvin skills scale os.vibe_engineering 100%

Output:
  Stage: Full Deployment (100% traffic)
  v0.3 (old): 0%
  v0.4 (new): 100%
  Monitor duration: 1 hour
```

**Final safety check**, then declare success.

---

## Zero-Downtime Mechanism

![Zero-Downtime Architecture](docs/assets/zero-downtime-architecture.svg)

```
Timeline:

T=0s:  v0.3 running, handling requests
       └─ request_1 → v0.3 (completes)
       └─ request_2 → v0.3 (in progress)
       └─ request_3 → v0.3 (queued)

T=1ms: [Switch] v0.4 warmed up and ready
       Old v0.3 stays in memory for in-flight requests
       New v0.4 takes new requests

T=1ms+: v0.3 handles remaining in-flight requests (graceful drain)
        v0.4 handles all new requests
        Old v0.3 removed after 30-second timeout

T=31s: Both v0.3 and v0.4 fully drained
       Deployment complete
```

**Key: No request lost, old version stays in memory during transition**

---

## Rollback (< 30 seconds)

### Automatic Rollback (on error)

```bash
# If error rate exceeds threshold during canary
corvin skills rollback os.vibe_engineering

Output:
  Rollback detected (error_rate > 5%)
  Reverting to os.vibe_engineering v0.3
  Current traffic: 10% (canary)
  Rollback time: 8 seconds
  Status: OK (v0.3 active again)
```

### Manual Rollback

```bash
corvin skills rollback os.vibe_engineering --to 0.2

Output:
  Rollback to os.vibe_engineering v0.2
  Rollback time: 12 seconds
  Status: OK
```

---

## Metrics to Monitor

### During Canary

| Metric | Target | Action if Exceeded |
|---|---|---|
| Error Rate | < +0.1% vs v0.3 | Rollback immediately |
| p99 Latency | < +50ms vs v0.3 | Investigate (may retry if cache miss) |
| Confidence Score | > 0.70 | Block scale-up |
| User Feedback | > 80% positive | Pause, analyze, rollback if < 60% |

### During Scale

| Metric | Target | Action |
|---|---|---|
| Error Rate | < 0.5% | Rollback if > 1% |
| p99 Latency | < 500ms | Investigate if > 600ms |
| Confidence | > 0.75 | Continue to full if > 0.80 |

---

## Pre-Deployment Checklist

Before deploying a new Skill version:

- [ ] **Tests pass:** `pytest tests/skill_xyz.py -v` (100% pass rate)
- [ ] **Audit verified:** Skill execution produces audit events (check with `corvin audit show-task`)
- [ ] **Dependencies resolved:** All pinned versions exist in registry
- [ ] **No circular deps:** DAG validation passed
- [ ] **Rollback plan:** Old version is stable and available
- [ ] **Config defaults:** New version has sensible defaults
- [ ] **Compliance:** ADR written if structural change

---

## Deployment Script

```bash
#!/bin/bash
# deploy-skill.sh: Full deployment workflow

SKILL="os.vibe_engineering"
VERSION="0.4"

echo "1. Deploy canary (10%)"
corvin skills deploy $SKILL v$VERSION --canary 10%
echo "   Waiting 24 hours... (or Ctrl+C to rollback)"
sleep 86400

echo "2. Scale to 50%"
corvin skills scale $SKILL 50%
echo "   Waiting 24 hours..."
sleep 86400

echo "3. Full deployment"
corvin skills scale $SKILL 100%
echo "   Monitoring for 1 hour..."
sleep 3600

echo "4. Success! $SKILL v$VERSION is live"
```

---

## Dependency Pins

When you deploy a new version of a Skill, dependent Skills see the change immediately:

```python
class ContextAdapter(Skill):
    dependencies = ["os.delegation_router"]  # No version pin

# When os.delegation_router v1.2 → v1.3 deploys
# ContextAdapter immediately uses v1.3
```

To keep old behavior, pin the version:

```python
class ContextAdapter(Skill):
    dependencies = ["os.delegation_router@1.2"]  # Pin to v1.2

# Even if v1.3 exists, ContextAdapter uses v1.2
# Operator must manually upgrade: dependencies = ["os.delegation_router@1.3"]
```

---

## FAQ

**Q: Can I deploy multiple Skills simultaneously?**  
A: Yes, but recommended: stagger by 1 hour to keep monitoring clean.

**Q: What's the minimum canary duration?**  
A: 24 hours. Catches day-shift and night-shift patterns.

**Q: Can I abort a canary without rolling back?**  
A: Yes: `corvin skills abort-stage <skill>`. Keeps v0.3 at 100%.

**Q: What if a dependency updates while I'm deploying?**  
A: If unpinned, new dependency version is used immediately. Can cause unexpected behavior. Always pin in production.

**Q: How do I deploy a breaking change (v1.0 → v2.0)?**  
A: Canary % is smaller (5%), monitor duration longer (48h). Plan communication with users.

---

## Next Steps

- **[Learning Loop](learning-loop.md)** — Monitor confidence during rollout
- **[Audit Trail](audit-trail.md)** — Check audit events for deployment proof
- **[Skills API Reference](skills-api-reference.md)** — `registry.scale()` API
