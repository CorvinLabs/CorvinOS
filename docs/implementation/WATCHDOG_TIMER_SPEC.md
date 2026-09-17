# Watchdog Timer Installation Specification (ADR-0867)

**Date:** 2026-09-17  
**Status:** PROPOSED  

## Overview

Watchdog Timer Installation ensures CorvinOS daemon processes are health-checked post-installation and automatically restarted if they crash (ADR-0867).

## Components

### 1. Health Check Probe
- **Location:** `scripts/watchdog-health-check.sh`
- **Endpoints checked:**
  - `http://localhost:8765/v1/console/healthz` (CRITICAL)
  - `http://localhost:8765/v1/gateway/healthz` (CRITICAL)  
  - `http://localhost:8765/v1/notification/healthz` (non-critical)

### 2. Systemd Service Watchdog
- **Configuration:** `Restart=on-failure`, `WatchdogSec=30s`, `RestartSec=5s`
- **Behavior:** Auto-restart on crash, systemd kills hung processes after 30s

### 3. Alert Service
- **Location:** `~/.config/systemd/user/corvin-alert.service`
- **Handler:** `/usr/local/bin/corvin-watchdog-alert`

### 4. Tripwire Enhancement
- **Function:** `assert_daemon_health()` in `core/compliance/corvin_compliance_reports/tripwire.py`
- **Behavior:** Fail-closed if 5+ restarts detected in last hour

## Audit Trail Events

- `health_check_passed` — post-install verification success
- `health_check_failed` — daemon crash detected
- `daemon_auto_restart_triggered` — systemd Restart= fired
- `daemon_restart_loop_detected` — crash loop alert

## Manual Testing

```bash
# Verify health check works
bash scripts/watchdog-health-check.sh

# Simulate daemon crash
killall corvinos-serve

# Watch auto-restart (systemd handles this)
journalctl --user -u corvin-console.service -f
```

---

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
