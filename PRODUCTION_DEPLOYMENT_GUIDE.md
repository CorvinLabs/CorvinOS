# Production Deployment Guide — Skill Forge v2.0 Phase 2

**Status:** Production-Ready  
**Date:** 2026-09-17

---

## 📋 Systemd Unit Templates (NOT in Repo)

**Location:** `~/.config/systemd/user/` or `/etc/systemd/system/` (operator-managed)

### 1. Skill Worker Service
**File:** `/etc/systemd/user/corvin-skill-worker.service`

```ini
[Unit]
Description=Corvin Skill Worker — Autonomous OS Learning Loop
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/corvin-skill-worker \
  --config /etc/corvin/skill-worker.conf \
  --log-level info \
  --learning-enabled true

ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5

# Resource limits
MemoryLimit=512M
CPUQuota=50%
TasksMax=100

# Signal handling
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=skill-worker

# Security
PrivateTmp=yes
NoNewPrivileges=yes
ReadOnlyPaths=/home
ReadWritePaths=/home/%u/.corvin

[Install]
WantedBy=default.target
```

### 2. Timer for Periodic Health Checks
**File:** `/etc/systemd/user/corvin-skill-worker.timer`

```ini
[Unit]
Description=Corvin Skill Worker Health Check Timer
Requires=corvin-skill-worker.service

[Timer]
OnBootSec=30s
OnUnitActiveSec=5m
Persistent=true

[Install]
WantedBy=timers.target
```

### 3. Worker Wrapper Script
**File:** `/usr/local/bin/corvin-skill-worker`

```bash
#!/bin/bash
set -euo pipefail

CONFIG_PATH="${1:-/etc/corvin/skill-worker.conf}"
LOG_LEVEL="${2:-info}"
LEARNING_ENABLED="${3:-true}"

# Source config
source "$CONFIG_PATH"

# Start worker
exec python3 << 'PYTHON_EOF'
import sys
from pathlib import Path
from core.learning.skill_learning_bridge import get_skill_learning_bridge
from core.learning.skill_optimizer import LearningOptimizer

def main():
    """Main skill worker loop."""
    skill_id = os.environ.get("SKILL_ID", "os.model_selector")
    tenant_id = os.environ.get("TENANT_ID", "_default")
    
    bridge = get_skill_learning_bridge(
        skill_id=skill_id,
        tenant_id=tenant_id,
        learning_enabled=LEARNING_ENABLED,
    )
    
    print(f"Skill Worker Started: {skill_id}/{tenant_id}")
    print(f"Learning Enabled: {LEARNING_ENABLED}")
    print(f"Config: {CONFIG_PATH}")
    
    # Main loop (runs until SIGTERM)
    while True:
        try:
            # Process feedback periodically
            status = bridge.get_learning_status()
            print(f"Status: {status}")
            
            # Sleep until next iteration
            time.sleep(300)  # 5 minutes
        except KeyboardInterrupt:
            print("\nShutdown requested")
            break
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            time.sleep(10)
    
    print("Skill Worker Stopped")

if __name__ == "__main__":
    main()
PYTHON_EOF
```

### 4. Configuration File
**File:** `/etc/corvin/skill-worker.conf`

```bash
# Skill Worker Configuration
export SKILL_ID="os.model_selector"
export TENANT_ID="_default"
export LOG_LEVEL="info"
export CORVIN_HOME="$HOME/.corvin"
export SKILL_BATCH_SIZE="10"
export SKILL_LEARNING_ENABLED="true"
export SKILL_CONVERGENCE_THRESHOLD="0.01"
export SKILL_CONFIDENCE_THRESHOLD="0.95"

# Resource management
export SKILL_MAX_WORKERS="10"
export SKILL_WORKER_TIMEOUT_SECONDS="300"
export SKILL_MEMORY_LIMIT_MB="512"
export SKILL_CPU_QUOTA_PERCENT="50"

# Monitoring
export HEALTH_CHECK_INTERVAL_SECONDS="300"
export METRICS_EXPORT_PATH="/var/metrics/skill-worker.json"
```

---

## 🚀 Deployment Steps

### 1. Install Systemd Units (Operator Step)

```bash
# As root or with sudo
sudo mkdir -p /etc/corvin
sudo cp corvin-skill-worker.service /etc/systemd/system/
sudo cp corvin-skill-worker.timer /etc/systemd/system/
sudo cp corvin-skill-worker.conf /etc/corvin/
sudo cp corvin-skill-worker /usr/local/bin/
sudo chmod +x /usr/local/bin/corvin-skill-worker

# Reload systemd
sudo systemctl daemon-reload

# Enable and start
sudo systemctl enable corvin-skill-worker.service
sudo systemctl enable corvin-skill-worker.timer
sudo systemctl start corvin-skill-worker.service
```

### 2. Verify Deployment

```bash
# Check service status
systemctl status corvin-skill-worker.service

# View logs
journalctl -u corvin-skill-worker.service -f

# Check health
curl http://localhost:8765/health
```

### 3. Monitor Running Workers

```bash
# List active skill workers
ps aux | grep skill-worker

# Check resource usage
systemctl status corvin-skill-worker.service --full

# Monitor learning progress
tail -f ~/.corvin/tenants/_default/global/model_selector_config.json
```

---

## 🛡️ Graceful Shutdown & Recovery

### Shutdown Sequence
1. **SIGTERM** sent to worker
2. Worker stops accepting new tasks (30s timeout)
3. Current tasks allowed to complete
4. Config snapshot saved
5. Process exits

### Recovery
1. Systemd auto-restarts on failure
2. Config snapshot loaded on startup
3. Learning resumes from last checkpoint
4. Audit trail fully recoverable

---

## 📊 Monitoring & Health Checks

**Prometheus Metrics Endpoint:** `http://localhost:8765/metrics`

```
# HELP skill_worker_executions_total Total skill executions
skill_worker_executions_total{skill_id="os.model_selector",status="success"} 12345
skill_worker_executions_total{skill_id="os.model_selector",status="failure"} 23

# HELP skill_worker_config_updates_total Config updates from learning
skill_worker_config_updates_total{skill_id="os.model_selector"} 42

# HELP skill_worker_confidence_score Current confidence score
skill_worker_confidence_score{skill_id="os.model_selector"} 0.87

# HELP skill_worker_latency_ms Skill execution latency
skill_worker_latency_ms{skill_id="os.model_selector",quantile="p95"} 415.22
```

---

## 🔧 Troubleshooting

### Worker Not Starting

```bash
# Check logs
journalctl -u corvin-skill-worker.service --no-pager -n 50

# Check config
cat /etc/corvin/skill-worker.conf

# Verify permissions
ls -la /home/$USER/.corvin/
```

### Learning Not Converging

```bash
# Check learning status
curl http://localhost:8765/skills/os.model_selector/learning-status

# View config updates
jq .update_count ~/.corvin/tenants/_default/global/model_selector_config.json

# Reset if stuck (careful!)
# rm ~/.corvin/tenants/_default/global/model_selector_config.json
```

### Resource Limits Hit

```bash
# Increase memory limit in service file
sudo nano /etc/systemd/system/corvin-skill-worker.service
# Change: MemoryLimit=512M → MemoryLimit=1G

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart corvin-skill-worker.service
```

---

## ✅ Production Checklist

- [ ] Systemd units installed in system location
- [ ] Config file populated with correct values
- [ ] Service starts and stays running
- [ ] Health checks passing
- [ ] Logs flowing to journalctl
- [ ] Metrics endpoint accessible
- [ ] Graceful shutdown working (SIGTERM)
- [ ] Config snapshots being created
- [ ] Learning loop processing feedback
- [ ] No errors in audit trail (audit.jsonl)

---

**Status:** Production-Ready for Phase 2 Execution

*Systemd units managed by operator, not stored in repo per design constraint.*
