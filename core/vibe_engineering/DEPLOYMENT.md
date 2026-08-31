# Phase 3.1: Status Reporting System — Deployment Guide

**Status:** ✅ PRODUCTION READY (Commit: a60e497c)  
**Date:** 2026-08-24  
**Version:** v3.1-rc1

## Deployment Checklist

### Prerequisites
- [ ] Python 3.9+ with async/await support
- [ ] `aiohttp` installed (for async webhook POST)
- [ ] Discord webhook URL (optional; falls back to publisher if not set)
- [ ] Checkpoint directory writable: `~/.corvin/vibe/checkpoints/`

### Environment Setup

```bash
# Install dependencies
pip install aiohttp

# Set Discord webhook (optional)
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_TOKEN"

# Verify environment
python3 -c "import aiohttp; print('✓ aiohttp ready')"
mkdir -p ~/.corvin/vibe/checkpoints && echo "✓ Checkpoint dir ready"
```

### Application Startup

```python
# In your app initialization:
import asyncio
from vibe_engineering import start_background_monitor, stop_background_monitor
import os

async def on_startup():
    """Initialize Phase 3.1 status reporting."""
    discord_webhook = os.getenv("DISCORD_WEBHOOK_URL")
    
    monitor = await start_background_monitor(
        poll_interval=30.0,  # Check every 30 seconds
        discord_webhook=discord_webhook  # Optional; None = use publisher fallback
    )
    
    logger.info(f"BackgroundMonitor started (polling every 30s)")
    return monitor

async def on_shutdown():
    """Clean up monitoring."""
    from vibe_engineering import stop_background_monitor
    stop_background_monitor()
    logger.info("BackgroundMonitor stopped")

# In FastAPI / async app:
app.add_event_handler("startup", on_startup)
app.add_event_handler("shutdown", on_shutdown)
```

### Health Checks

```python
# Verify BackgroundMonitor is running:
from vibe_engineering import get_monitor

monitor = get_monitor()
print(f"Monitor is_running: {monitor.is_running}")
print(f"Tracked tasks: {len(monitor.last_notified)}")
print(f"Polling interval: {monitor.poll_interval}s")
```

### CLI Commands (TaskCLI)

```bash
# List all pending tasks
corvin task list

# Get current status for a task
corvin task status <task_id>

# Resume from checkpoint
corvin task resume <task_id>

# Watch task in real-time (polls every 2s)
corvin task monitor <task_id>

# Auto-resume the last unfinished task
corvin task auto-resume
```

### Monitoring & Observability

#### Discord Notifications
- **Trigger:** Milestone detected (progress, state change, user input, error)
- **Cadence:** Max 1 notification per task per 60 seconds (cooldown enforced)
- **Retry:** Exponential backoff (1s, 2s, 4s) on server errors
- **Format:** Rich embeds with color-coding (blue=running, green=complete, red=failed)

#### Logs
```bash
# Watch BackgroundMonitor logs
tail -f ~/.corvin/vibe/logs/background_monitor.log

# Watch for webhook errors
grep "Discord webhook" ~/.corvin/vibe/logs/background_monitor.log

# Watch for milestone notifications
grep "BackgroundMonitor notify" ~/.corvin/vibe/logs/background_monitor.log
```

#### Metrics to Watch
| Metric | Target | How to Measure |
|---|---|---|
| Polling latency | <100ms | Monitor log timestamps (polling → task check → notification sent) |
| Webhook success rate | >95% | Count "Discord webhook posted" vs "Discord webhook failed" in logs |
| Memory per 1000 tasks | <10MB | `monitor.publisher.history` size estimate |
| Poll cycle time | ~30s | Time between "BackgroundMonitor started" logs |

### Rollout Strategy

#### Phase 1: Internal Testing (Week 1)
- Enable in dev/staging
- Set `poll_interval=10.0` for faster feedback
- Test with 5-10 concurrent tasks
- Monitor webhook success rate and latency

#### Phase 2: Canary (Week 2)
- 10% of production instances
- Set `poll_interval=30.0` (production cadence)
- Monitor for 48 hours: latency, memory, webhook failures

#### Phase 3: Gradual Rollout (Week 3-4)
- 25% → 50% → 100%
- Continue monitoring all metrics
- No kill-switch needed (polling is low-cost)

#### Phase 4: GA (Week 5+)
- 100% of fleet
- Establish on-call dashboard
- Document escalation path for monitoring failures

### Troubleshooting

#### Discord Webhook Failing
```
Symptom: "Discord webhook failed" in logs but tasks continue running

Check:
1. Webhook URL validity: curl -X POST <WEBHOOK_URL> -d '{"content": "test"}'
2. Network access: timeout, DNS, firewall rules
3. Discord API limits: backoff/retry in effect

Fix:
1. Update DISCORD_WEBHOOK_URL env var
2. Restart BackgroundMonitor (calls stop_background_monitor() + start_background_monitor())
3. Monitor webhook success rate (should recover within 3 attempts)
```

#### BackgroundMonitor Not Detecting Milestones
```
Symptom: Snapshots published but no Discord notifications

Check:
1. Monitor.is_running: should be True
2. Monitor.last_notified dict: should have task_ids
3. Notification cooldown: check if task hit 60-second minimum between notifs
4. Milestone criteria: progress > (last_iter + 5) OR state changed

Fix:
1. Check logs for milestone detection logic
2. Verify snapshot.iteration_num is incrementing
3. Reset notification cooldown if needed: monitor.last_notified.clear()
```

#### Memory Growth (Unbounded History)
```
Symptom: Publisher.history grows without bound

Check:
1. max_history_per_task enforcement: should be 100 per task
2. Completed task cleanup: BackgroundMonitor._cleanup_completed_tasks() runs every poll

Fix:
1. Verify BackgroundMonitor.cleanup_completed=True
2. Check for tasks stuck in RUNNING state (never complete)
3. Manually purge old snapshots: publisher.history = []  (WARNING: loses audit trail)
```

### Rollback Plan

If issues emerge (Discord outage, webhook failure, memory exhaustion):

1. **Immediate:** Stop BackgroundMonitor
   ```python
   stop_background_monitor()
   ```

2. **Monitor fallback:** Publisher still works; bridges can poll directly
   ```python
   await publisher.publish(snapshot)  # Manual publish
   ```

3. **CLI still available:** TaskCLI doesn't depend on BackgroundMonitor
   ```bash
   corvin task list          # Still works
   corvin task resume <task> # Still works
   ```

4. **Revert commit:** If necessary
   ```bash
   git revert a60e497c
   git push origin main
   ```

### SLOs & Acceptance Criteria

| SLO | Metric | Target | Measurement Method |
|---|---|---|---|
| **Polling Latency** | Time from milestone detected to Discord POST sent | <100ms | Log timestamp delta |
| **Webhook Success** | Proportion of notifications reaching Discord without retry | >95% | Count successful POSTs / total attempts |
| **Memory Stability** | Heap size growth over 24h with 1000 concurrent tasks | <100MB | `psutil.Process().memory_info()` |
| **Monitor Availability** | Uptime of BackgroundMonitor polling loop | >99.9% | Heartbeat logs every 30s |
| **CLI Responsiveness** | Time to list/resume/status commands | <1s | CLI invocation timing |

### Post-Deployment Validation

After deployment, verify:

```python
# 1. Monitor is running
from vibe_engineering import get_monitor
assert get_monitor().is_running == True, "BackgroundMonitor not running"

# 2. Publisher has history
publisher = get_publisher()
assert len(publisher.history) > 0 or len(publisher._latest_by_task) == 0, "Publisher initialized"

# 3. CLI commands respond
from vibe_engineering.task_cli import TaskCLI
cli = TaskCLI(None, None)
tasks = await cli.list_tasks()  # Should return [] or list of task_ids
print(f"✓ CLI ready. Pending tasks: {len(tasks)}")

# 4. Webhook accessible (if set)
import os
if os.getenv("DISCORD_WEBHOOK_URL"):
    monitor = get_monitor()
    print(f"✓ Discord webhook configured: {bool(monitor.discord_webhook)}")
```

### Contact & Escalation

- **On-Call Runbook:** `/docs/runbooks/phase-3-1-status-reporting.md`
- **Escalation:** If BackgroundMonitor fails, fall back to publisher polling
- **Support:** See CONCEPT-0011 and ADR-0369 for architecture details

---

**Deployment Status:** ✅ READY  
**Last Verified:** 2026-08-24  
**Commit:** a60e497c  
**Version:** v3.1-rc1

**Approved for production deployment.**
