# Phase 1: CorvinOS Troubleshooting Guide

**Version:** 1.0  
**Date:** 2026-09-22  
**Scope:** Common issues and solutions (20+ scenarios)

---

## Table of Contents

1. [Installation Issues](#installation-issues)
2. [Configuration Problems](#configuration-problems)
3. [API & Connectivity](#api--connectivity)
4. [Skill Execution](#skill-execution)
5. [Learning Loop](#learning-loop)
6. [Audit Chain](#audit-chain)
7. [Console UI](#console-ui)
8. [Performance](#performance)
9. [Plugin Issues](#plugin-issues)
10. [Emergency Recovery](#emergency-recovery)

---

## Installation Issues

### Issue 1: `Python version not supported`

**Error:**
```
ERROR: CorvinOS requires Python 3.10+. Found: 3.9.5
```

**Root Cause:** Python version is too old.

**Solution:**
```bash
# Check current version
python3 --version

# Install newer version
# On Ubuntu/Debian:
sudo apt update && sudo apt install python3.10

# On macOS:
brew install python@3.10

# Set as default
alias python3=/usr/bin/python3.10

# Verify
python3 --version  # Should be 3.10+
```

**Prevention:** Always check Python version before installation.

---

### Issue 2: `Permission denied` during install

**Error:**
```
ERROR: [Errno 13] Permission denied: '/usr/local/lib/python3.10/site-packages/...'
```

**Root Cause:** Installing to system Python instead of virtual environment.

**Solution:**
```bash
# Ensure virtual environment is activated
which python
# Should show: /home/username/.corvin-env/bin/python

# If not activated:
source ~/.corvin-env/bin/activate

# Retry installation
pip install -e .
```

**Prevention:** Always use virtual environments for local development.

---

### Issue 3: `git submodule update failed`

**Error:**
```
fatal: failed to recurse into directory 'corvin_decisions'
Failed to clone 'corvin_decisions' a second time
```

**Root Cause:** ADR submodule repository is unreachable or doesn't exist.

**Solution:**
```bash
# Remove broken submodule
git submodule deinit -f corvin_decisions
rm -rf .git/modules/corvin_decisions
git rm -f corvin_decisions

# Re-add submodule
git submodule add https://github.com/CorvinLabs/Corvin-ADR.git corvin_decisions

# Update
git submodule update --init --recursive

# Verify
ls -la corvin_decisions/decisions/ADR-* | head -5
```

---

## Configuration Problems

### Issue 4: `ANTHROPIC_API_KEY not set`

**Error:**
```
ERROR: ANTHROPIC_API_KEY environment variable not found.
Please set it: export ANTHROPIC_API_KEY="your-key-here"
```

**Root Cause:** Environment variable not exported.

**Solution:**
```bash
# Check if set
echo $ANTHROPIC_API_KEY

# If empty, set it
export ANTHROPIC_API_KEY="sk-ant-..."

# Make permanent (add to ~/.bashrc or ~/.zshrc)
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.bashrc
source ~/.bashrc

# Verify
echo $ANTHROPIC_API_KEY  # Should print your key
```

**Prevention:** Add to your shell profile permanently.

---

### Issue 5: `Invalid API key format`

**Error:**
```
ERROR: API key validation failed. Expected format: sk-ant-...
```

**Root Cause:** API key has wrong format or is incomplete.

**Solution:**
```bash
# Get a new API key from https://console.anthropic.com
# Verify format
echo $ANTHROPIC_API_KEY | grep -E "^sk-ant-[a-zA-Z0-9_-]{40,}$"

# If no match, key is invalid. Generate new one at console.anthropic.com

# Test connectivity
curl -X POST https://api.anthropic.com/v1/messages \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-opus-5","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  | jq .
```

**Prevention:** Always validate keys before adding to `.bashrc`.

---

### Issue 6: `Tenant initialization failed`

**Error:**
```
ERROR: Failed to initialize tenant '_default': Permission denied on ~/.corvin/
```

**Root Cause:** Directory doesn't exist or has wrong permissions.

**Solution:**
```bash
# Check ~/.corvin directory
ls -la ~/.corvin/

# If doesn't exist:
mkdir -p ~/.corvin
chmod 700 ~/.corvin

# Retry bootstrap
corvin bootstrap --tenant=_default

# Verify
ls -la ~/.corvin/tenants/_default/
```

---

## API & Connectivity

### Issue 7: `Connection refused: localhost:8765`

**Error:**
```
curl: (7) Failed to connect to localhost:8765: Connection refused
```

**Root Cause:** CorvinOS API server is not running.

**Solution:**
```bash
# Check if server is running
curl -s http://localhost:8765/v1/health | jq .

# If not running, start it
corvin serve --port=8765 &

# Or use systemd service
systemctl --user status corvin-webui

# If not running:
systemctl --user start corvin-webui

# View logs
journalctl --user -u corvin-webui -n 50
```

**Prevention:** Start the server before making API calls.

---

### Issue 8: `Invalid authentication token`

**Error:**
```
{
  "error": {
    "type": "InvalidAuthToken",
    "message": "Authorization token is invalid or expired"
  }
}
```

**Root Cause:** API key is wrong or expired.

**Solution:**
```bash
# Verify API key is set correctly
echo $ANTHROPIC_API_KEY

# If empty, set it
export ANTHROPIC_API_KEY="sk-ant-..."

# Test with curl
curl -X GET http://localhost:8765/v1/health \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" | jq .

# If still fails, get new key from https://console.anthropic.com
```

---

### Issue 9: `Rate limit exceeded (429)`

**Error:**
```
{
  "error": {
    "type": "RateLimitExceeded",
    "message": "Too many requests. Limit: 1000/hour"
  }
}
```

**Root Cause:** API quota exceeded.

**Solution:**
```bash
# Check rate limit headers
curl -s -X GET http://localhost:8765/v1/health \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -w "\nX-RateLimit-Remaining: %{header{X-RateLimit-Remaining}}\n" | tail -5

# Wait and retry (exponential backoff)
sleep 2
curl -X GET http://localhost:8765/v1/health \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" | jq .

# Or batch requests to reduce volume
```

**Prevention:** Monitor rate limit headers and pace requests.

---

## Skill Execution

### Issue 10: `Skill not found`

**Error:**
```
{
  "error": {
    "code": "SKILL_NOT_FOUND",
    "message": "Skill 'nonexistent' is not installed"
  }
}
```

**Root Cause:** Skill is not installed.

**Solution:**
```bash
# List available skills
corvin skill list

# Install the skill
corvin skill install assistant.quick_fix

# Verify installation
corvin skill status assistant.quick_fix

# Retry execution
corvin skill execute assistant.quick_fix --input="test error"
```

---

### Issue 11: `Skill execution timeout`

**Error:**
```
ERROR: Skill execution timed out after 30 seconds
```

**Root Cause:** Skill is hanging or taking too long.

**Solution:**
```bash
# Increase timeout
corvin skill execute assistant.quick_fix \
  --input="test" \
  --timeout=60  # 60 seconds instead of 30

# Or check skill status
corvin skill status assistant.quick_fix

# View recent errors
corvin skill logs assistant.quick_fix --tail=20

# If skill is broken, restart it
corvin skill restart assistant.quick_fix
```

**Prevention:** Monitor skill performance and set appropriate timeouts.

---

### Issue 12: `Skill execution failed with error`

**Error:**
```
{
  "error": {
    "type": "SkillExecutionError",
    "message": "Skill failed: ImportError: No module named 'numpy'"
  }
}
```

**Root Cause:** Skill dependencies are missing.

**Solution:**
```bash
# Check skill dependencies
corvin skill info assistant.quick_fix | grep -A 5 dependencies

# Install missing dependencies
pip install numpy

# Verify
python3 -c "import numpy; print(numpy.__version__)"

# Retry execution
corvin skill execute assistant.quick_fix --input="test"
```

---

## Learning Loop

### Issue 13: `Learning processor not running`

**Error:**
```
ERROR: Learning processor is not running
Status: INACTIVE
```

**Root Cause:** Learning service failed to start.

**Solution:**
```bash
# Check status
corvin learning status

# Enable learning
corvin learning enable

# Start manually if needed
systemctl --user start corvin-learning-processor

# View logs
journalctl --user -u corvin-learning-processor -n 50

# If still failing, check dependencies
corvin learning verify
```

---

### Issue 14: `Feedback not being processed`

**Error:**
```
Feedback sent but not appearing in learning history
```

**Root Cause:** Learning processor is not consuming feedback events.

**Solution:**
```bash
# Check event queue
corvin learning status | grep -i queue

# Flush queue
corvin learning flush-queue

# Check for errors
corvin learning logs --level=error

# If stuck, restart processor
corvin learning restart

# Retry feedback
curl -X POST http://localhost:8765/v1/learning/feedback \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_id": "assistant.quick_fix",
    "feedback_type": "outcome",
    "signal": "correct",
    "tenant_id": "_default"
  }' | jq .
```

---

### Issue 15: `Skill confidence not improving`

**Error:**
```
Skill confidence stuck at 0.65, not improving despite feedback
```

**Root Cause:** Optimizer not tuning or feedback signal is inconsistent.

**Solution:**
```bash
# Check optimizer status
corvin learning optimizer-status --skill=assistant.quick_fix

# View feedback history
corvin learning view-feedback --skill=assistant.quick_fix --limit=50

# Check for conflicting feedback
corvin learning analyze-feedback --skill=assistant.quick_fix

# Force optimization run
corvin learning optimize --skill=assistant.quick_fix --force

# Review recommendations
corvin learning recommendations --skill=assistant.quick_fix
```

---

## Audit Chain

### Issue 16: `Audit chain verification failed`

**Error:**
```
ERROR: Audit chain verification failed
Chain height: 1234, broken at event 1001
Expected hash: abc123... Got: xyz789...
```

**Root Cause:** Audit chain hash mismatch (data corruption or tampering).

**Solution:**
```bash
# Verify chain integrity
corvin audit verify-chain --tenant=_default

# If broken, attempt repair
corvin audit repair --tenant=_default

# Check for gaps
corvin audit check-gaps --tenant=_default

# If repair fails, inspect the broken event
corvin audit inspect-event --event-id=evt_1001 --tenant=_default

# Last resort: reinitialize (WARNING: loses audit history)
rm -rf ~/.corvin/audit.jsonl
corvin bootstrap --tenant=_default
```

**Prevention:** Never manually edit `audit.jsonl`. Always use API.

---

### Issue 17: `Audit trail too large`

**Error:**
```
ERROR: Audit trail file is 5GB and growing
System running out of disk space
```

**Root Cause:** Audit events accumulating; retention policy not enforced.

**Solution:**
```bash
# Check audit file size
du -sh ~/.corvin/audit.jsonl

# View retention policy
corvin audit policy get --tenant=_default

# Set retention (keep 90 days)
corvin audit policy set --retention-days=90 --tenant=_default

# Archive old events
corvin audit archive --before=2026-06-22 --output=/backup/audit-archive.jsonl

# Verify archive
ls -lh /backup/audit-archive.jsonl

# Monitor future growth
watch -n 60 'du -sh ~/.corvin/audit.jsonl'
```

---

## Console UI

### Issue 18: `Console returns blank page`

**Error:**
```
Browser shows blank white page at http://localhost:8765/console
```

**Root Cause:** Console UI failed to load or JavaScript error.

**Solution:**
```bash
# Check browser console for errors (F12 → Console)
# Common errors:
# - "Failed to load assets" → rebuild console
# - "API unreachable" → check server status

# Rebuild console
cd core/console/corvin_console/web-next
rm -rf dist/ node_modules/.vite/
npm run build

# Restart API server
systemctl --user restart corvin-webui

# Clear browser cache
# Firefox: Ctrl+Shift+Del → select "Everything" → delete
# Chrome: Ctrl+Shift+Del → select "All time" → delete cookies/cache

# Hard refresh
Ctrl+Shift+R  (or Cmd+Shift+R on macOS)
```

---

### Issue 19: `Console settings not saving`

**Error:**
```
Console settings revert after page reload
```

**Root Cause:** Settings API failing or browser local storage disabled.

**Solution:**
```bash
# Check if settings API is working
curl -X GET http://localhost:8765/v1/console/settings \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" | jq .

# Check browser local storage (F12 → Storage → Local Storage)
# Should have key: corvin_settings

# Clear and reset
curl -X DELETE http://localhost:8765/v1/console/settings \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"

# Re-save settings
curl -X PUT http://localhost:8765/v1/console/settings \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-opus-5","learning_enabled":true}' | jq .

# Hard refresh console
Ctrl+Shift+R
```

---

## Performance

### Issue 20: `Slow API responses (>5s)`

**Error:**
```
Skill execution taking >5 seconds for simple tasks
API response latency: 7.2s
```

**Root Cause:** High load, slow disk, or inefficient query.

**Solution:**
```bash
# Check system resources
top -bn1 | head -20
df -h | grep -E "/$|/home"
free -h

# Check API server CPU/memory
systemctl --user status corvin-webui

# Monitor real-time
watch -n 1 'ps aux | grep corvin | head -5'

# Optimize database (if using)
corvin db optimize --tenant=_default

# Check network latency
curl -w "@curl-format.txt" -o /dev/null -s http://localhost:8765/v1/health
# Expected response: <200ms

# If slow, restart server
systemctl --user restart corvin-webui

# Scale up (if available)
corvin scale --workers=4
```

---

### Issue 21: `High memory usage (>2GB)`

**Error:**
```
CorvinOS process using 3.5GB RAM
System running slowly
```

**Root Cause:** Memory leak in plugin/skill or large audit trail loaded in memory.

**Solution:**
```bash
# Monitor memory over time
watch -n 5 'ps aux | grep corvin-webui'

# Identify memory spike
systemctl --user status corvin-webui | grep -i memory

# Clear caches
corvin cache clear-all --tenant=_default

# Check audit trail size
du -sh ~/.corvin/audit.jsonl

# Archive old audit events
corvin audit archive --before=2026-06-22

# Restart service to free memory
systemctl --user restart corvin-webui

# Monitor again
watch -n 5 'ps aux | grep corvin-webui | head -1'
```

---

## Plugin Issues

### Issue 22: `Plugin failed to load`

**Error:**
```
ERROR: Plugin 'video-producer' failed to load: ModuleNotFoundError: No module named 'opencv'
```

**Root Cause:** Plugin dependency not installed.

**Solution:**
```bash
# Check plugin requirements
corvin plugin info video-producer | grep -i requirements

# Install dependencies
pip install opencv-python

# Unload plugin
corvin plugin disable video-producer

# Reload plugin
corvin plugin enable video-producer

# Verify
corvin plugin status video-producer
```

---

## Emergency Recovery

### Issue 23: `System completely broken (all services down)`

**Nuclear Option (⚠️ CAUTION: Clears all data)**

```bash
# Backup existing audit trail first
cp ~/.corvin/audit.jsonl ~/audit-backup-$(date +%Y%m%d).jsonl

# Backup plugins
cp -r ~/.corvin/plugins ~/plugins-backup-$(date +%Y%m%d)

# Clean slate
rm -rf ~/.corvin/

# Reinitialize
corvin bootstrap --tenant=_default

# Restore plugins (if needed)
cp -r ~/plugins-backup-$(date +%Y%m%d)/* ~/.corvin/plugins/

# Verify
corvin verify --all
```

---

## Getting Help

If none of these solutions work:

1. **Collect diagnostic information:**
   ```bash
   corvin diagnostics collect --output=diag-$(date +%Y%m%d-%H%M%S).tar.gz
   ```

2. **Check logs:**
   ```bash
   journalctl --user -u corvin-webui --since=1h --no-pager > logs.txt
   journalctl --user -u corvin-learning-processor --since=1h --no-pager >> logs.txt
   ```

3. **Open an issue:**
   - https://github.com/CorvinLabs/CorvinOS/issues
   - Include: `diagnostics.tar.gz` + `logs.txt` + description

4. **Contact support:**
   - Email: support@corvinlabs.io
   - Slack: https://corvinlabs.slack.com

---

**Last Updated:** 2026-09-22  
**Version:** 1.0.0
