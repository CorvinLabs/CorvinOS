# Quick Start: Upgrade to CorvinOS 2026-09-20

**Duration:** 5–10 minutes  
**Downtime:** ~5 min (systemd service restart)  
**Difficulty:** Easy (single command)

---

## 📋 Pre-Upgrade Checklist

Before upgrading, verify your system is ready:

```bash
# 1. Check current version
corvin version
# Output should be: CorvinOS 2026-09-01 (or earlier)

# 2. Verify CorvinOS is running
systemctl --user status corvin-webui
# Output should be: ● corvin-webui.service - Loaded
# If not running, start it: systemctl --user start corvin-webui

# 3. Backup current configuration & secrets
corvin backup --config --secrets
# Output: Backup saved to ~/.corvin/backup/TIMESTAMP/

# 4. Backup credential files (just in case)
tar czf ~/credential_files_backup.tar.gz \
  ~/.env \
  ~/.config/corvin-voice/service.env \
  ~/.config/corvin-voice/secrets.json 2>/dev/null || true
# Note: May show errors if files don't exist — that's OK

# 5. Review release notes (optional but recommended)
cat /home/shumway/projects/CorvinOS/RELEASE_NOTES_2026-09-20.md | head -50
```

---

## 🚀 Upgrade Steps

### Option A: Upgrade from Package (Recommended)

```bash
# 1. Perform dry-run (preview what will change)
corvin upgrade --release=2026-09-20 --dry-run

# 2. Review the dry-run output
# It should show:
#   - New services to install
#   - Config files to update
#   - Plugins/dependencies to add

# 3. Apply the upgrade
corvin upgrade --release=2026-09-20

# 4. Watch for completion
# Output will show:
#   ✅ Downloaded release 2026-09-20
#   ✅ Extracted to /tmp/corvin_release_2026-09-20/
#   ✅ Updated configuration
#   ✅ Installed new services
#   ✅ Upgrade complete
```

### Option B: Upgrade from Source (Advanced)

If you prefer to build from source:

```bash
# 1. Navigate to repo
cd /home/shumway/projects/CorvinOS

# 2. Fetch latest code
git fetch origin
git checkout origin/main

# 3. Install/upgrade
pip install -e .

# 4. Restart services
systemctl --user restart corvin-webui
systemctl --user restart corvin-console-watch
```

---

## ✅ Verify Upgrade

After upgrade completes, verify everything works:

```bash
# 1. Check version
corvin version
# Output should be: CorvinOS 2026-09-20 (Production-Ready)

# 2. Run health check
corvin health-check
# Expected output:
#   ✅ Console:          127.0.0.1:8765 (responsive)
#   ✅ Audit chain:      ~/.corvin/audit.jsonl (integrity OK)
#   ✅ Plugin system:    20 plugins loaded
#   ✅ Learning loop:    Event store operational
#   ✅ Credential daemon: Running (non-blocking)

# 3. Verify credential daemon started
systemctl --user status corvin-secret-rotation.service
# Output should be: ● corvin-secret-rotation.service - Loaded

# 4. Check audit trail (should show new credential inventory event)
grep "credential_rotation_baseline" ~/.corvin/audit.jsonl | tail -1 | jq .
# Output: {"event_type": "credential_rotation_baseline", "timestamp": "...", ...}

# 5. Open console in browser and check for errors
open http://127.0.0.1:8765/console
# OR if no X11:
curl -s http://127.0.0.1:8765/console | head -20
```

---

## 🔧 Post-Upgrade Configuration (Optional)

### Enable New Credential Monitoring

By default, the credential daemon runs as a non-blocking service. To customize:

```bash
# Edit tenant configuration
nano ~/.corvin/tenant.corvin.yaml

# Add or update the credential rotation section:
spec:
  secret_rotation:
    enabled: true                      # Enable monitoring
    check_interval_days: 7             # How often to verify credentials
    rotation_interval_days: 90         # Phase 2 rotation timeline
    backup_location: ~/.corvin/backup/credentials/  # Backup path
```

Save and restart:
```bash
systemctl --user restart corvin-secret-rotation.service
```

### Install Knowledge Graph (Optional)

Knowledge Graph is now available as a marketplace plugin. To install:

```bash
# Install from marketplace
corvin plugin install corvin-knowledge --tier=contributor

# Verify installation
corvin plugin list | grep knowledge

# Access in console
# Navigate to: http://127.0.0.1:8765/console/knowledge-graph
```

---

## ⚠️ If Upgrade Fails

### Issue: "corvin command not found"

**Solution:** Reinstall the package:
```bash
pip install --upgrade /home/shumway/projects/CorvinOS/
```

### Issue: "Audit chain verification failed"

**Solution:** This is a critical failure and indicates data corruption. Rollback immediately:
```bash
corvin rollback --release=2026-09-01
```

Then contact support: security@corvinos.dev

### Issue: "Credential daemon won't start"

**Solution:** Check logs:
```bash
journalctl --user -u corvin-secret-rotation.service -n 50
```

If you see permission errors, fix file mode:
```bash
chmod 0600 ~/.env
chmod 0600 ~/.config/corvin-voice/service.env
chmod 0600 ~/.config/corvin-voice/secrets.json
```

Restart daemon:
```bash
systemctl --user restart corvin-secret-rotation.service
```

### Issue: "Console won't start (404)"

**Solution:** Check for import errors:
```bash
pytest tests/test_console_app_importable.py -v 2>&1 | head -50
```

If you see a failure, note the error module and contact support.

---

## 🔄 Rollback (If Needed)

If you encounter issues after upgrade, rollback to the previous version:

```bash
# 1. Check available versions
corvin rollback --list
# Output shows: 2026-09-01 (previous), 2026-09-20 (current)

# 2. Rollback to previous version
corvin rollback --release=2026-09-01

# 3. Verify rollback
corvin version
# Output: CorvinOS 2026-09-01

# 4. Restart services
systemctl --user restart corvin-webui
```

**Important:** Rollback preserves the audit trail. All events created during 2026-09-20 remain (immutable, hash-chained). Only code is rolled back.

---

## 📊 What's New in This Release

**Key Features:**
- 🔐 **Credential Rotation Phase 1:** Automated inventory + monitoring
- 🔄 **Secret Rotation Daemon:** Real-time credential health checks
- 📦 **Skill Forge v2.0:** ZIP packaging for marketplace distribution
- 📊 **Knowledge Graph:** Marketplace integration (opt-in contributor plugin)
- 🔔 **Notifications:** Self-delegated tasks now notify immediately (Discord)
- 👷 **Worker Monitor:** Health tracking + performance analysis

For full details, see [RELEASE_NOTES_2026-09-20.md](./RELEASE_NOTES_2026-09-20.md).

---

## 🎯 What to Monitor After Upgrade

After upgrade completes, keep an eye on these for 24 hours:

### 1. Audit Trail

```bash
# Monitor for any errors
watch 'grep -c "credential_rotation\|rotation_failed\|rotation_error" ~/.corvin/audit.jsonl'
# Should show increasing count as daemon runs, but no errors
```

### 2. Systemd Services

```bash
# Check all CorvinOS services are running
systemctl --user list-units | grep corvin
# Should show: ✓ corvin-webui.service (active)
#             ✓ corvin-console-watch.service (active)
#             ✓ corvin-secret-rotation.service (active)
```

### 3. Console Responsiveness

```bash
# Benchmark console latency
for i in {1..5}; do
  time curl -s http://127.0.0.1:8765/console >/dev/null
done
# Should be <500ms per request
```

### 4. Disk Space (for audit backups)

```bash
du -sh ~/.corvin/backup/
# Should be <1GB for first 24h
```

---

## 📞 Need Help?

### Common Questions

**Q: Can I skip the dry-run?**  
A: You can, but we don't recommend it. The dry-run shows exactly what will change without actually applying changes.

**Q: Will the upgrade interrupt my tasks?**  
A: The upgrade itself won't interrupt tasks, but you'll need to restart systemd services (5 min downtime). Reschedule long-running tasks to avoid this window.

**Q: Can I upgrade without downtime?**  
A: Not in this release. Phase 5 will add zero-downtime upgrade (rolling restart). For now, plan a 5-min maintenance window.

**Q: What if I don't want to upgrade?**  
A: That's fine. CorvinOS 2026-09-01 is still supported. We recommend upgrading within 2 weeks to get security fixes.

### Support Channels

- **GitHub Issues:** https://github.com/CorvinLabs/CorvinOS/issues
- **Discussions:** https://github.com/CorvinLabs/CorvinOS/discussions
- **Security:** security@corvinos.dev
- **Discord:** https://discord.gg/corvinOS

---

## 📋 Upgrade Checklist (to keep for your records)

```
[ ] Backed up configuration (corvin backup --config)
[ ] Backed up secrets (corvin backup --secrets)
[ ] Reviewed release notes
[ ] Ran dry-run (corvin upgrade --dry-run)
[ ] Applied upgrade (corvin upgrade --release=2026-09-20)
[ ] Verified version (corvin version)
[ ] Passed health check (corvin health-check)
[ ] Verified credential daemon running
[ ] Checked audit trail (no errors)
[ ] Tested console (http://127.0.0.1:8765)
[ ] Monitored for 1 hour (no issues)
```

---

**Upgrade completed successfully!** 🎉

Your CorvinOS installation is now running version 2026-09-20 with full credential monitoring, Skill Forge v2.0, and Knowledge Graph integration.

For questions or issues, see the support channels above.

