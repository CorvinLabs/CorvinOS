# CorvinOS Systemd Services & Timers

Automated services for compliance and operational tasks.

## Services

### corvin-registry-snapshot.service / .timer

**Purpose:** Commit Task Registry snapshots to git (GDPR Art. 30, 32 audit trail)

**How it works:**
1. `corvin-task-registry-sync.timer` fires daily (03:00 UTC)
2. Registry synced to `~/.corvin/task_registry.json`
3. `corvin-registry-snapshot.service` runs (via timer dependency)
4. Snapshot copied to `docs/reference/task_registry_snapshots/<YYYYMMDD-HHMMSS>.json`
5. Committed to git with metadata (task count, timestamp, audit trail note)

**Installation:**

```bash
# Copy service + timer to systemd user directory
mkdir -p ~/.config/systemd/user/
cp systemd/corvin-registry-snapshot.service ~/.config/systemd/user/
cp systemd/corvin-registry-snapshot.timer ~/.config/systemd/user/

# Enable and start
systemctl --user daemon-reload
systemctl --user enable corvin-registry-snapshot.timer
systemctl --user start corvin-registry-snapshot.timer

# Verify
systemctl --user status corvin-registry-snapshot.timer
journalctl --user -u corvin-registry-snapshot.service -n 10
```

**Compliance:**
- **GDPR Art. 30:** Audit trail of task registry state (who/what/when)
- **GDPR Art. 32:** Git history provides immutable, timestamped snapshots
- **ADR-0864:** Snapshots are git-tracked for compliance reporting

**Testing:**

```bash
# Manual trigger (one-off)
systemctl --user start corvin-registry-snapshot.service

# Check latest snapshot
ls -la docs/reference/task_registry_snapshots/ | tail -5

# Verify latest is valid JSON + has tasks
jq '.tasks | length' docs/reference/task_registry_snapshots/$(ls docs/reference/task_registry_snapshots | tail -1)

# Check git history
git log --oneline --grep='Task Registry Snapshot' | head -5
```

## References

- **ADR-0864:** Git-Tracked Registry Snapshots
- **MEMORY.md:** Task Registry snapshot automation
- **compliance-baseline.md:** Audit trail requirements
