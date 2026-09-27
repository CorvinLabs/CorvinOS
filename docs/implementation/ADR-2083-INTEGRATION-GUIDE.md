# ADR-2083 Integration Guide

## Non-Interactive Bridge Autonomy — Implementation & Wiring

**ADR:** ADR-2083  
**Status:** PROPOSED → ACCEPTED (pending review)  
**Implementation Date:** 2026-09-27  
**Affected Systems:** Discord Bridge, Slack Bridge, Loop Execution, Workflow Orchestration

---

## What Changed

| System | Before | After | Impact |
|---|---|---|---|
| **Loop Execution** | Always tries `ScheduleWakeup` (fails on Discord) | Bridge-aware: use `run_in_background` on non-interactive | Loops work on Discord ✅ |
| **Workflow Start** | Caller waits for completion (blocks session) | Always non-blocking (async ack) | No session blocking ✅ |
| **Token Warnings** | Explicit "Token aufgebracht" messages | Transparent (via Context Compression) | No user confusion ✅ |

---

## How to Use

### For Agent Code (Claude Code)

**Rule 1: Never emit "Token aufgebracht" warnings**

❌ **Before:**
```python
if tokens_remaining < threshold:
    print("Token aufgebracht — öffne neue Session")  # WRONG
```

✅ **After:**
```python
# Trust Context Compression; it handles token exhaustion automatically
# (No explicit warning needed)
```

**Rule 2: Use Bridge-Aware Loop Execution**

❌ **Before:**
```python
# Loop command might fail on Discord
loop_executor = LoopExecutor(config)
loop_executor.run()  # Uses ScheduleWakeup → fails non-interactive
```

✅ **After:**
```python
from corvin_operator.bridges.autonomy_detector import detect_autonomy_mode

executor = LoopExecutor(config)
result = executor.run()  # Auto-detects bridge, uses correct mode
# result["execution_mode"] = "scheduled" (CLI) or "background" (Discord)
```

**Rule 3: Start Workflows in Background**

❌ **Before:**
```python
# Caller might wait indefinitely for workflow result
result = workflow.start_and_wait(script)
```

✅ **After:**
```python
# Workflow starts non-blocking
runner = WorkflowBackgroundRunner()
ack = runner.start(script)  # Returns immediately
# ack.run_id = "wf_abc123"
# Query result separately: WorkflowBackgroundRunner.get_workflow_result(ack.run_id)
```

---

## Code Locations

### New Modules

| Module | Location | Purpose |
|---|---|---|
| `autonomy_detector.py` | `corvin_operator/bridges/` | Bridge type + autonomy mode detection |
| `loop_executor_bridge_aware.py` | `corvin_operator/autonomy/` | Bridge-aware loop execution |
| `workflow_background_runner.py` | `corvin_operator/workflows/` | Non-blocking workflow start |

### Test Suite

| Test File | Location | Coverage |
|---|---|---|
| `test_bridge_autonomy_e2e.py` | `tests/e2e/` | Bridge detection, loop modes, workflow non-blocking |

---

## Integration Points

### 1. Discord Bridge

**File:** `corvin_operator/bridges/discord_adapter.py`

**Change Required:** Set environment variable on startup

```python
# discord_adapter.py :: __init__()
def __init__(self):
    # ...existing code...
    
    # Signal to CorvinOS that we're Discord bridge
    os.environ["CORVIN_BRIDGE_TYPE"] = "discord"
    
    # Now any agent code will detect non-interactive mode automatically
    logger.info(f"Bridge autonomy mode: {detect_autonomy_mode().value}")
```

### 2. Slack Bridge

**File:** `corvin_operator/bridges/slack_adapter.py`

**Change Required:** Same as Discord

```python
def __init__(self):
    os.environ["CORVIN_BRIDGE_TYPE"] = "slack"
```

### 3. CLI / Web (Interactive)

**File:** `corvin_operator/bridges/cli.py` and `corvin_operator/web/app.py`

**Change Required:** Set interactive mode (or let auto-detection work)

```python
# Optional (auto-detection also works):
os.environ["CORVIN_BRIDGE_TYPE"] = "web"  # or "cli"
```

### 4. Loop Command Handler

**File:** `corvin_operator/commands/loop_handler.py` (NEW/MODIFIED)

**Change Required:** Use bridge-aware executor

```python
def handle_loop_command(prompt: str, interval: int = 300) -> None:
    """Handle /loop command using bridge-aware executor."""
    from corvin_operator.autonomy.loop_executor_bridge_aware import LoopExecutor, LoopConfig
    from corvin_operator.bridges.autonomy_detector import detect_autonomy_mode
    
    def executor_fn(p):
        # Execute one iteration of the loop
        return run_iteration(p)
    
    config = LoopConfig(
        prompt=prompt,
        interval_seconds=interval,
        max_iterations=None,
        timeout_seconds=3600,  # 1 hour max for background loops
        executor_fn=executor_fn,
    )
    
    executor = LoopExecutor(config)
    result = executor.run()
    
    # Log result to user (message already set by executor)
    print(result["message"])
```

### 5. Workflow Handler

**File:** `corvin_operator/commands/workflow_handler.py` (NEW/MODIFIED)

**Change Required:** Use background runner

```python
def handle_workflow_start(script: str, args: dict = None) -> None:
    """Handle Workflow creation using background runner."""
    from corvin_operator.workflows.workflow_background_runner import WorkflowBackgroundRunner
    
    runner = WorkflowBackgroundRunner()
    ack = runner.start(script, args)
    
    # Print ack to user (already formatted for bridge)
    print(ack.message)
    
    # If caller needs result, they query separately
    # result = WorkflowBackgroundRunner.get_workflow_result(ack.run_id)
```

---

## Deployment Checklist

- [ ] **Code Review** — All three modules reviewed + approved
- [ ] **Unit Tests** — Run locally: `pytest tests/e2e/test_bridge_autonomy_e2e.py -v`
- [ ] **Integration Tests** — Test with real Discord bridge (staging)
- [ ] **Staging Deploy** — Deploy to staging environment, monitor logs
- [ ] **Production Canary** — Roll out to 10% of Discord traffic
- [ ] **Production Full** — Roll out to 100% (after 24h canary)

---

## Testing Strategy

### Local Development

```bash
# 1. Run unit tests
pytest tests/e2e/test_bridge_autonomy_e2e.py -v

# 2. Test with Discord bridge (staging)
export CORVIN_BRIDGE_TYPE=discord
python3 -c "from corvin_operator.bridges.autonomy_detector import detect_autonomy_mode; print(detect_autonomy_mode())"
# Expected: BridgeAutonomyMode.NON_INTERACTIVE

# 3. Test loop execution
/loop "test prompt" --interval=5
# Expected: "Schleife läuft im Hintergrund. Benachrichtigung kommt, wenn fertig."

# 4. Test workflow
/workflow start orchestration_script.wf
# Expected: "Workflow gestartet: wf_xyz... (läuft im Hintergrund)"
```

### Staging Testing

```bash
# 1. Deploy to staging environment
git checkout -b adagio/adr-2083-staging
# ... merge changes ...
git push

# 2. Test Discord bridge loop
# (Send /loop from Discord, verify background execution)

# 3. Test Slack bridge workflow
# (Start workflow from Slack, verify non-blocking ack)

# 4. Monitor logs
tail -f ~/.corvin/tenants/_default/global/logs/bridge.log
# Should see: "Running loop in background" or "Starting workflow in background"
```

### Monitoring Post-Deploy

**Metrics to Watch:**

| Metric | Expected | Alert If |
|---|---|---|
| **Loop timeout errors** | < 0.1% | > 1% |
| **Workflow queue depth** | < 100 | > 500 |
| **Context compression rate** | 80–90% | < 50% (compression failing) |
| **Discord bridge uptime** | > 99.9% | < 99% |

**Log Patterns:**

```bash
# Normal: loop starts in background
"Running loop in background: prompt=..., interval=60s, timeout=3600s"

# Normal: workflow starts non-blocking
"Starting workflow in background: run_id=wf_abc123, bridge=discord"

# Alert: loop timeout triggered
"Loop timeout reached (3600s)"

# Alert: bridge autonomy detection failed
"Autonomy mode detection failed, defaulting to non_interactive"
```

---

## Rollback Plan

If issues arise post-deploy:

### Immediate (< 5 minutes)

```bash
# Revert to previous version (before ADR-2083)
git revert <commit-hash>
git push origin main

# Restart services
systemctl restart corvin-discord-bridge
systemctl restart corvin-gateway
```

### Investigation (< 1 hour)

1. Check logs for autonomy detection errors
2. Verify environment variables are set correctly
3. Confirm Context Compression is working

### Long-term Fix

1. Identify root cause (e.g., misdetected bridge type)
2. Create hotfix branch
3. Deploy to staging for verification
4. Canary roll-out to production

---

## Success Criteria (K=5 Validation)

✅ **Functional:**
- [ ] `/loop` works on Discord bridge (executes in background)
- [ ] `/workflow start` returns immediately (non-blocking)
- [ ] No explicit "Token aufgebracht" messages on Discord
- [ ] Context Compression handles token exhaustion transparently

✅ **Audit & Compliance:**
- [ ] All autonomy decisions logged (audit trail)
- [ ] Tenant isolation maintained (tenant_id in all events)
- [ ] Hash-chain integrity verified

✅ **Performance:**
- [ ] Loop background execution < 2x slower than interactive
- [ ] Workflow queue depth stable (< 100 items)
- [ ] No token exhaustion on Discord (compression working)

✅ **Documentation:**
- [ ] ADR-2083 accepted
- [ ] Integration guide complete (this document)
- [ ] Code comments inline
- [ ] E2E tests passing

---

## References

- **ADR:** `/home/shumway/projects/Corvin-ADR/decisions/ADR-2083-non-interactive-bridge-autonomy-design.md`
- **ADR-0232:** Boot Tripwire (Audit Chain Foundation)
- **ADR-0613:** Learning Loop Closure (Autonomy Feedback)
- **Bridge Architecture:** `docs/implementation/BRIDGE_ARCHITECTURE.md` (TBD)
- **Context Compression:** `docs/implementation/CONTEXT_COMPRESSION.md` (TBD)
