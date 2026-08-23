# Brain v0.2 Operator Training — Module 4: Operational Runbooks
## 45-Minute Step-by-Step Procedures

**Version:** 1.0 (2026-08-23)  
**Target Audience:** Production operators, deployment engineers, on-call rotations  
**Prerequisite:** Modules 1–3 (Architecture + Monitoring + Incident Response)  
**Outcome:** Execute all standard operations safely and repeatably

---

## Learning Objectives

By the end of this module, you will:
1. Execute startup procedure with health verification
2. Execute graceful shutdown without data loss
3. Apply configuration changes with validation
4. Perform backup and restore operations
5. Coordinate deployment across canary → full rollout stages

---

## Runbook 1: Startup Procedure (10 minutes)

### When to Use
- Initial service start after installation
- Service restart after kernel patches
- Manual recovery after shutdown

### Pre-Startup Checklist

```bash
# 1. Verify filesystem space (need ≥2GB free)
df -h ~/.corvin/
# Output: /home ... 45GB free ... ✓

# 2. Verify database integrity
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  "SELECT COUNT(*) FROM events;"
# Output: (should be <10,000) ✓

# 3. Verify audit trail exists
ls -lh ~/.corvin/tenants/_default/audit.jsonl
# Output: -rw-r--r-- ... 234M audit.jsonl ✓

# 4. Verify Python environment
python3 --version  # Should be ≥3.10
poetry env info   # Should show active env

# 5. Verify dependencies installed
poetry check
# Output: "All verified" ✓
```

### Startup Sequence

```bash
#!/bin/bash
set -e  # Exit on any error

echo "[$(date)] Starting CorvinOS v0.2..."

# PHASE 1: Pre-flight (< 5 sec)
echo "[$(date)] Phase 1: Pre-flight checks..."
corvin preflight check
# Output: ✓ All checks passed

# PHASE 2: Bootstrap (< 10 sec)
# This runs the boot tripwire (verifies audit chain)
echo "[$(date)] Phase 2: Bootstrapping..."
systemctl start corvin-service
sleep 5

# PHASE 3: Health check (< 30 sec)
echo "[$(date)] Phase 3: Health check..."
for i in {1..30}; do
  if curl -f http://localhost:8765/health >/dev/null 2>&1; then
    echo "✓ Service healthy at startup attempt $i"
    break
  fi
  if [ $i -eq 30 ]; then
    echo "✗ Service failed to start after 30 attempts"
    systemctl status corvin-service
    exit 1
  fi
  sleep 1
done

# PHASE 4: Subsystem verification (< 30 sec)
echo "[$(date)] Phase 4: Subsystem verification..."
corvin status all
# Expected: 13/13 subsystems HEALTHY

# PHASE 5: Final verification (< 10 sec)
echo "[$(date)] Phase 5: Final verification..."
corvin test smoke
# Output: ✓ All smoke tests passed

echo "[$(date)] ✓ Startup complete! Service ready for traffic."
```

### Monitoring Startup

```bash
# Watch the startup process in real-time
watch -n 1 'corvin status all && echo "---" && tail -3 ~/.corvin/tenants/_default/audit.jsonl'

# Expected progression:
# T+0s:   [boot] starting
# T+2s:   [boot] tripwire: audit chain valid
# T+5s:   [health_monitor] online
# T+8s:   [context_bridge] online
# T+10s:  [orchestrator] online
# ...
# T+25s:  All 13 subsystems online
# T+30s:  Smoke test passed, ready
```

### Troubleshooting Startup

| Issue | Symptom | Fix |
|-------|---------|-----|
| **Slow startup (>60s)** | Taking too long | Check CPU usage, might be indexing audit trail |
| **Audit chain failed** | `bootstrap_tripwire_failed` error | DO NOT RESTART. Escalate to maintainer. |
| **Subsystem hung** | One subsystem not coming online | `corvin restart-subsystem <name>` |
| **Service won't start** | `systemctl start` fails | Check logs: `journalctl -u corvin-service -n 50` |
| **Memory spike during startup** | Memory jumps to >1GB | Probably loading large learning models. Monitor. |

---

## Runbook 2: Graceful Shutdown (10 minutes)

### When to Use
- Planned maintenance
- OS-level updates
- Database migrations
- Config file changes (requires restart)

### Pre-Shutdown Checklist

```bash
# 1. Inform on-call team
# → Slack: "@team Shutting down corvin-service for maintenance in 5 min"

# 2. Drain active tasks (wait for completion)
echo "Waiting for active tasks to complete..."
watch -n 5 'corvin metrics query "corvin_active_tasks_total"'
# Wait until this shows 0, or wait max 5 minutes

# 3. Verify no new tasks are being submitted
# → Contact frontend team: "Please redirect new requests away"

# 4. Create checkpoint (save state)
echo "Creating checkpoint..."
corvin checkpoint create
# Output: Checkpoint saved to ~/.corvin/tenants/_default/checkpoints/ckpt-20260823-144500.json
```

### Shutdown Sequence

```bash
#!/bin/bash
set -e

echo "[$(date)] Starting graceful shutdown..."

# PHASE 1: Drain tasks (wait for in-flight to complete)
echo "[$(date)] Phase 1: Draining active tasks..."
MAX_WAIT=300  # 5 minutes
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  ACTIVE=$(corvin metrics query 'corvin_active_tasks_total' 2>/dev/null || echo 0)
  if [ "$ACTIVE" = "0" ] || [ -z "$ACTIVE" ]; then
    echo "✓ No active tasks"
    break
  fi
  echo "Waiting for $ACTIVE tasks to complete... ($ELAPSED/$MAX_WAIT sec)"
  sleep 5
  ELAPSED=$((ELAPSED + 5))
done

# PHASE 2: Stop accepting new requests
echo "[$(date)] Phase 2: Stopping request acceptance..."
corvin config set spec.accept_requests=false
sleep 2

# PHASE 3: Stop the service
echo "[$(date)] Phase 3: Stopping CorvinOS service..."
systemctl stop corvin-service
sleep 3

# PHASE 4: Verify stopped
echo "[$(date)] Phase 4: Verifying shutdown..."
if ! pgrep -f "corvin-service" >/dev/null; then
  echo "✓ Service stopped cleanly"
else
  echo "✗ Service still running, force-killing..."
  pkill -9 -f "corvin-service"
  sleep 2
fi

# PHASE 5: Final verification
echo "[$(date)] Phase 5: Final verification..."
if curl -f http://localhost:8765/health >/dev/null 2>&1; then
  echo "✗ ERROR: Service still responding after shutdown!"
  exit 1
else
  echo "✓ Service completely stopped"
fi

echo "[$(date)] ✓ Shutdown complete!"
```

### Monitoring Shutdown

```bash
# Watch shutdown progress
tail -f ~/.corvin/tenants/_default/audit.jsonl | grep -E "(shutdown|stopped)"

# Expected log sequence:
# 2026-08-23T14:45:00Z [INFO] shutdown_initiated
# 2026-08-23T14:45:05Z [INFO] draining_tasks: 0 active
# 2026-08-23T14:45:10Z [INFO] stopping_request_acceptance
# 2026-08-23T14:45:15Z [INFO] stopping_services
# 2026-08-23T14:45:20Z [INFO] shutdown_complete
```

### Troubleshooting Shutdown

| Issue | Symptom | Fix |
|-------|---------|-----|
| **Tasks won't drain** | Still >0 active after 5 min | Force stop: `systemctl stop -n 10 corvin-service` |
| **Service won't stop** | Process still running | `pkill -9 -f "corvin-service"` (last resort) |
| **Checkpoint save fails** | Permission denied | Check: `ls -la ~/.corvin/tenants/_default/` |
| **Can't reconnect** | Service stops then auto-restarts | Check systemd config: `systemctl status corvin-service` |

---

## Runbook 3: Configuration Changes (8 minutes)

### When to Use
- Enable/disable features
- Adjust subsystem timeouts
- Change logging levels
- Update resource limits

### Configuration File Locations

```bash
# 1. Global config (applies to all tenants)
~/.corvin/global/corvin.yaml

# 2. Per-tenant config (overrides global)
~/.corvin/tenants/_default/corvin.yaml

# 3. Local overrides (developer only)
~/.corvin/tenants/_default/corvin.local.yaml
```

### Safe Config Change Procedure

```bash
#!/bin/bash
set -e

echo "Step 1: Backup current config"
cp ~/.corvin/tenants/_default/corvin.yaml \
   ~/.corvin/tenants/_default/corvin.yaml.backup-$(date +%s)

echo "Step 2: Apply new config"
# Option A: Edit file directly
nano ~/.corvin/tenants/_default/corvin.yaml

# Option B: Use CLI (safer, validates syntax)
corvin config set spec.features.per_stage_token_budgeting=true
corvin config set spec.subsystem_timeouts.cost_controller=60
corvin config set spec.logging_level=DEBUG

echo "Step 3: Validate syntax"
corvin config validate
# Output: ✓ Config valid (no syntax errors)

echo "Step 4: Check diffs"
diff -u ~/.corvin/tenants/_default/corvin.yaml.backup-* \
         ~/.corvin/tenants/_default/corvin.yaml

echo "Step 5: Apply config (no restart needed)"
corvin config reload
# Output: ✓ Config reloaded successfully

echo "Step 6: Verify change took effect"
corvin config get spec.features.per_stage_token_budgeting
# Output: true ✓

echo "✓ Config change applied!"
```

### Common Configuration Changes

#### Change 1: Enable a Feature Flag

```bash
# Example: Enable token budgeting for canary
corvin config set spec.features.per_stage_token_budgeting=true

# Verify
corvin config get spec.features.per_stage_token_budgeting
# Output: true
```

#### Change 2: Adjust Subsystem Timeout

```bash
# Example: Cost controller taking too long (increase timeout from 30s to 60s)
corvin config set spec.subsystem_timeouts.cost_controller=60

# Verify
corvin config get spec.subsystem_timeouts.cost_controller
# Output: 60
```

#### Change 3: Change Logging Level

```bash
# Development: DEBUG logging
corvin config set spec.logging_level=DEBUG

# Production: INFO logging (less verbose)
corvin config set spec.logging_level=INFO

# Verify
tail -f ~/.corvin/tenants/_default/audit.jsonl | head -1
# Should see [DEBUG] messages if DEBUG is set
```

### Rollback a Config Change

```bash
# If change causes problems, revert
cp ~/.corvin/tenants/_default/corvin.yaml.backup-XXXXXXXX \
   ~/.corvin/tenants/_default/corvin.yaml

# Reload config
corvin config reload

# Verify reverted
corvin config get <key>
```

---

## Runbook 4: Backup & Restore (12 minutes)

### When to Use
- Daily automated backups
- Before major upgrades
- After incident (preserve evidence)
- Compliance/audit requirements

### Backup Procedure

```bash
#!/bin/bash
set -e

BACKUP_DIR=$HOME/backups/corvin/$(date +%Y%m%d-%H%M%S)
mkdir -p $BACKUP_DIR

echo "[$(date)] Starting backup to: $BACKUP_DIR"

# 1. Backup audit trail (most critical)
echo "Backing up audit trail..."
cp -r ~/.corvin/tenants/_default/audit.jsonl \
      $BACKUP_DIR/audit.jsonl
chmod 600 $BACKUP_DIR/audit.jsonl

# 2. Backup learning database
echo "Backing up learning engine database..."
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  ".dump" > $BACKUP_DIR/learning_engine.db.sql

# 3. Backup session state
echo "Backing up session state..."
cp -r ~/.corvin/tenants/_default/sessions/ \
      $BACKUP_DIR/sessions/

# 4. Backup config
echo "Backing up configuration..."
cp ~/.corvin/tenants/_default/corvin.yaml \
   $BACKUP_DIR/corvin.yaml

# 5. Create manifest
cat > $BACKUP_DIR/MANIFEST.txt << EOF
Backup Date: $(date -Iseconds)
Version: v0.2
Tenant: _default
Files:
  - audit.jsonl (hash-chained event log)
  - learning_engine.db.sql (SQL dump)
  - sessions/ (checkpoints, state)
  - corvin.yaml (configuration)
EOF

# 6. Compress backup
echo "Compressing backup..."
tar -czf $BACKUP_DIR.tar.gz $BACKUP_DIR/
rm -rf $BACKUP_DIR
ls -lh $BACKUP_DIR.tar.gz

echo "[$(date)] ✓ Backup complete: $BACKUP_DIR.tar.gz"
```

### Restore Procedure (Backup Recovery)

```bash
#!/bin/bash
set -e

BACKUP_FILE=$1  # e.g., ~/backups/corvin/20260823-144500.tar.gz

if [ ! -f "$BACKUP_FILE" ]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE"
  exit 1
fi

echo "[$(date)] Starting restore from: $BACKUP_FILE"

# 1. Extract backup
echo "Extracting backup..."
RESTORE_DIR=$(mktemp -d)
tar -xzf $BACKUP_FILE -C $RESTORE_DIR

# 2. Verify manifest
echo "Verifying backup integrity..."
cat $RESTORE_DIR/*/MANIFEST.txt

# 3. Stop service
echo "Stopping service..."
systemctl stop corvin-service
sleep 3

# 4. Restore audit trail
echo "Restoring audit trail..."
cp $RESTORE_DIR/*/audit.jsonl \
   ~/.corvin/tenants/_default/audit.jsonl.new
mv ~/.corvin/tenants/_default/audit.jsonl.new \
   ~/.corvin/tenants/_default/audit.jsonl
chmod 600 ~/.corvin/tenants/_default/audit.jsonl

# 5. Restore learning database
echo "Restoring learning engine database..."
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  < $RESTORE_DIR/*/learning_engine.db.sql

# 6. Restore sessions
echo "Restoring session state..."
rm -rf ~/.corvin/tenants/_default/sessions/
cp -r $RESTORE_DIR/*/sessions/ \
      ~/.corvin/tenants/_default/sessions/

# 7. Verify audit chain
echo "Verifying audit chain integrity..."
corvin audit verify
# Must output: "Chain integrity: VALID"

# 8. Restart service
echo "Restarting service..."
systemctl start corvin-service
sleep 5

# 9. Final check
corvin health check
# Output: ✓ All systems healthy

echo "[$(date)] ✓ Restore complete!"
rm -rf $RESTORE_DIR
```

### Daily Automated Backups

```bash
# Create cron job for daily backups
crontab -e

# Add this line:
0 2 * * * /opt/corvin/scripts/backup.sh > /var/log/corvin-backup.log 2>&1

# Verify
crontab -l | grep backup
```

---

## Runbook 5: Deployment (Canary → Full) (5 minutes)

### Stage 1: Canary Deployment (10% Traffic)

```bash
#!/bin/bash
set -e

echo "[$(date)] Stage 1: Canary Deployment (10%)"

# 1. Pre-deployment checklist
echo "Pre-deployment checks..."
./scripts/pre-deployment-checklist.sh
# Output: All 7 gates ✓

# 2. Deploy to staging first
echo "Deploying to staging..."
git fetch origin v0.2-rc1
git checkout v0.2-rc1
poetry install

# 3. Run tests in staging
poetry run pytest tests/ -m integration
# Output: All tests passed ✓

# 4. Deploy to production (canary 10%)
echo "Deploying to production (10% canary)..."
kubectl set image deployment/corvin-gateway \
  corvin-gateway=corvin:v0.2-rc1

# 5. Monitor health
echo "Monitoring canary health for 24 hours..."
watch -n 30 'kubectl rollout status deployment/corvin-gateway'

# 6. Collect metrics
echo "Collecting metrics..."
corvin metrics snapshot > canary-metrics-24h.json
```

### Stage 2: Expand to 25%

```bash
# After canary passes, expand
kubectl patch deployment corvin-gateway \
  -p '{"spec":{"replicas":2}}'

# Monitor another 24 hours
```

### Stage 3: Full Rollout (100%)

```bash
# After all stages pass
kubectl set image deployment/corvin-gateway \
  corvin-gateway=corvin:v0.2 --record

# Final verification
kubectl rollout status deployment/corvin-gateway
```

---

## Summary Checklist

| Operation | Duration | Risk | Recovery |
|-----------|----------|------|----------|
| **Startup** | <30s | Low | Manual restart |
| **Shutdown** | <30s | Very Low | Restart |
| **Config Change** | <5s | Low | Rollback backup |
| **Backup** | <5 min | None | Restore from backup |
| **Restore** | <10 min | Medium | Escalate to maintainer |
| **Deployment** | 5–72h | Medium | Rollback script |

---

**Next Module:** [Hands-on Lab Module](MODULE-5-HANDS-ON-LAB.md) (60 min)  
**Time Spent:** 45 minutes  
**Status:** Ready to manage production ✅
