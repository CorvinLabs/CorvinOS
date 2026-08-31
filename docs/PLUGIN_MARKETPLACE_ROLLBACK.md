# Plugin Marketplace Rollback Procedures

**Version:** 1.0.0  
**Date:** 2026-08-30  
**Status:** Production Ready

## Overview

This document describes rollback procedures for the CorvinOS Plugin Marketplace. Rollback enables recovery from failed installations, corrupted configurations, or incompatible plugin versions.

## Table of Contents

1. [How Rollback Works](#how-rollback-works)
2. [Automatic Rollback](#automatic-rollback)
3. [Manual Rollback](#manual-rollback)
4. [Version Downgrade](#version-downgrade)
5. [Snapshot Management](#snapshot-management)
6. [Testing Rollback](#testing-rollback)
7. [Recovery Scenarios](#recovery-scenarios)

---

## How Rollback Works

### Snapshot-Based Recovery

CorvinOS creates **snapshots** at key points in a plugin's lifecycle:

| Event | Snapshot Type | Created Before/After |
|-------|---------------|----------------------|
| User clicks "Install" | `pre-install` | Before download/setup |
| Installation succeeds | `post-install` | After successful setup |
| Configuration saved | `config-snapshot` | Before config is applied |
| Plugin updated | `pre-update` | Before updating to new version |

Each snapshot contains:
- **Manifest** — Plugin definition (unchanged usually)
- **Configuration** — User's config values (secrets masked)
- **Metadata** — Installation timestamp, version, status

### Rollback Workflow

```
Pre-Install Snapshot Created
         ↓
    Download Plugin
         ↓
    Validate Manifest
         ↓
   [Installation Error?]
         ├─→ YES: Automatic Rollback Triggered
         │        └─→ Restore Pre-Install Snapshot
         │            └─→ Plugin Uninstalled
         │            └─→ Error Logged
         │
         └─→ NO: Continue Installation
              ↓
          Setup Plugin
              ↓
         Post-Install Snapshot Created
              ↓
         Installation Complete
```

---

## Automatic Rollback

### When It Triggers

Automatic rollback occurs if:

1. **Download fails** — Network error, file corrupted
2. **Manifest validation fails** — Invalid schema, missing required fields
3. **Dependency resolution fails** — Required plugin not found
4. **Sandbox setup fails** — Resource limits misconfigured
5. **Plugin startup fails** — Entry point not found, syntax error

### What Gets Restored

When automatic rollback runs:
- ✓ Plugin is completely uninstalled
- ✓ Configuration files are preserved (backed up separately)
- ✓ Audit trail records the failure and rollback
- ✗ No manual intervention needed

### Example: Automatic Rollback on Network Error

```
User clicks: Install github-integration v2.0.0

[Plugin Marketplace] → [GitHub API] ✗ (Connection Timeout)
    Pre-Install Snapshot Created ✓
    Download Attempted ✗
    Automatic Rollback Triggered ✓
      └─ Restore Pre-Install State
      └─ Plugin remains uninstalled
    Error Logged ✓

User sees: "Installation failed: Network timeout. Please check your connection."
```

---

## Manual Rollback

### Scenario: Rollback Due to Incompatibility

If a newly-installed plugin version doesn't work well:

**Step 1: List available snapshots**
```bash
corvin plugins:snapshots github-integration
```

Output:
```
Snapshot ID              Type            Date/Time           Status
github-int-snap-001     pre-install     2026-08-30 10:00    ✓ Available
github-int-snap-002     post-install    2026-08-30 10:15    ✓ Available
github-int-snap-003     pre-update      2026-08-30 15:30    ✓ Available
```

**Step 2: Choose a snapshot to restore**

In this example, we want to go back to before the update, so we restore `github-int-snap-003` (pre-update):

```bash
corvin plugins:restore github-integration --snapshot github-int-snap-003
```

**Step 3: Verify restoration**

```bash
corvin plugins:show github-integration
# Version: 1.9.2 (restored from snapshot)
# Status: Enabled
# Last update: N/A (restored)
```

**Step 4: Test the restored version**

```bash
# Run integration tests to verify
corvin plugins:test github-integration

# Check logs for any errors
corvin plugins:logs github-integration --tail 50
```

---

## Version Downgrade

### Downgrading to a Specific Version

If you need an older version of a plugin:

**Step 1: Check available versions**
```bash
corvin plugins:versions github-integration
```

Output:
```
Version    Release Date    Status
2.0.0      2026-08-30      Latest (Installed)
1.9.2      2026-08-25      Stable
1.9.1      2026-08-20      Stable
1.9.0      2026-08-15      Legacy
```

**Step 2: Uninstall current version**
```bash
corvin plugins:uninstall github-integration
# Configuration backed up automatically
```

**Step 3: Install older version**
```bash
corvin plugins:install github-integration@1.9.2
# Restores last-saved configuration automatically
```

### Downgrade with Configuration Preservation

CorvinOS automatically backs up configurations:

```bash
corvin plugins:install github-integration@1.9.2 --restore-config
# ✓ Installed version 1.9.2
# ✓ Restored configuration from previous installation
# ✓ Backup kept at ~/.corvin/plugins/backups/
```

---

## Snapshot Management

### Viewing Snapshot Details

```bash
corvin plugins:snapshot-info github-integration --snapshot github-int-snap-003
```

Output:
```
Snapshot: github-int-snap-003
Created: 2026-08-30 15:30:00 UTC
Type: pre-update
Reason: User initiated upgrade to v2.0.0

Contents:
  Manifest: ✓ Included
  Config: ✓ Included (secrets masked)
  Metadata: ✓ Included

Size: 45 KB
Compression: gzip

Rollback Available: Yes
```

### Cleaning Up Old Snapshots

CorvinOS keeps 10 recent snapshots per plugin by default. Manually clean up:

```bash
corvin plugins:cleanup-snapshots github-integration --keep 5
# Deleted 5 snapshots
# Remaining: 5 (most recent)
```

To keep all snapshots (not recommended for long-term installs):
```bash
corvin plugins:cleanup-snapshots github-integration --keep 999
```

### Exporting Snapshots

For backup or analysis:

```bash
corvin plugins:export-snapshot github-integration --snapshot github-int-snap-003 \
  --output /tmp/github-integration-snapshot.tar.gz

# Extract if needed
tar -xzf /tmp/github-integration-snapshot.tar.gz -C /tmp/snapshot-contents
```

---

## Testing Rollback

### Scenario 1: Test a Plugin Install + Rollback

```bash
# Step 1: Create a pre-install snapshot manually
corvin plugins:snapshot github-integration pre-install "Testing rollback"
SNAPSHOT_ID=$(corvin plugins:snapshots github-integration | head -1 | awk '{print $1}')

# Step 2: Install the plugin
corvin plugins:install new-experimental-plugin

# Step 3: If there's an issue, restore
corvin plugins:restore github-integration --snapshot $SNAPSHOT_ID

# Step 4: Verify restore worked
corvin plugins:show github-integration
```

### Scenario 2: Simulate Installation Failure

```bash
# Use dry-run to test without actually installing
corvin plugins:install untested-plugin --dry-run

# If dry-run fails, installation would fail too
# Real installation is blocked

# Check dry-run diagnostics
corvin plugins:diagnose untested-plugin
```

### Scenario 3: Chain Rollback

If rollback itself fails (rare):

```bash
# Check snapshots
corvin plugins:snapshots github-integration

# Try an older snapshot
corvin plugins:restore github-integration --snapshot github-int-snap-001 --force

# If that fails, fall back to manual recovery
sudo systemctl restart corvin-console
corvin plugins:disable github-integration
```

---

## Recovery Scenarios

### Scenario A: Plugin Crash After Update

**Symptoms:**
- Plugin stops responding
- CorvinOS console becomes slow
- Plugin logs show errors

**Recovery:**

```bash
# 1. Disable the plugin immediately
corvin plugins:disable github-integration

# 2. Check what changed recently
corvin audit:search --plugin-id github-integration --limit 10

# 3. If recent update caused it, rollback to previous version
corvin plugins:restore github-integration --snapshot github-int-snap-003

# 4. Re-enable and test
corvin plugins:enable github-integration
corvin plugins:test github-integration
```

### Scenario B: Configuration Corruption

**Symptoms:**
- Plugin won't start due to config error
- Error message mentions "invalid configuration"

**Recovery:**

```bash
# 1. Check config backup
ls -l ~/.corvin/plugins/backups/github-integration-*.yaml

# 2. Restore from backup
corvin plugins:restore github-integration --backup \
  ~/.corvin/plugins/backups/github-integration-2026-08-29-15-30.yaml

# 3. Verify config is valid
corvin plugins:validate-config github-integration

# 4. Restart plugin
corvin plugins:restart github-integration
```

### Scenario C: Multiple Plugin Conflicts After Install

**Symptoms:**
- New plugin conflicts with existing plugin
- Error: "Plugin conflict: old-plugin"

**Recovery:**

```bash
# 1. Automatic rollback should have triggered
# 2. Verify old plugin still works
corvin plugins:show old-plugin
# Should show it's installed and enabled

# 3. If new plugin was somehow partially installed:
corvin plugins:uninstall new-plugin --force

# 4. Check conflict rules and resolve:
# Either: keep old-plugin, find alternative new-plugin
# Or: uninstall old-plugin, then install new-plugin
```

### Scenario D: Rollback Fails (Emergency Recovery)

If rollback itself encounters an error:

```bash
# Step 1: Disable all plugins to stabilize system
corvin plugins:disable-all

# Step 2: Verify CorvinOS is stable
corvin status
# Should show all core systems running

# Step 3: Manually inspect plugin state
ls -la ~/.corvin/plugins/installed/

# Step 4: Manually delete problematic plugin
rm -rf ~/.corvin/plugins/installed/problematic-plugin

# Step 5: Re-enable working plugins one at a time
corvin plugins:enable github-integration
corvin test
corvin plugins:enable other-plugin
corvin test
```

---

## Troubleshooting Rollback

### "Snapshot Not Found"

```
Error: Snapshot github-int-snap-999 not found
```

**Solution:**
1. List available snapshots: `corvin plugins:snapshots github-integration`
2. Use a snapshot ID from the list
3. If no snapshots exist, install without rollback capability

### "Rollback Failed: Permission Denied"

```
Error: Permission denied writing to ~/.corvin/plugins/
```

**Solution:**
1. Check file permissions: `ls -ld ~/.corvin/plugins/`
2. Ensure you have write access
3. Run with `sudo` if needed: `sudo corvin plugins:restore ...`

### "Configuration Restore Failed"

```
Error: Could not restore configuration: file not found
```

**Solution:**
1. Backup may have been deleted
2. Try restoring from snapshot instead of backup
3. Re-configure plugin manually if needed

---

## Best Practices

### 1. Test Before Updating

```bash
# Use dry-run
corvin plugins:install --version 2.0.0 github-integration --dry-run

# If successful, then install for real
corvin plugins:install --version 2.0.0 github-integration
```

### 2. Keep Recent Backups

```bash
# Backup your config before risky changes
cp ~/.corvin/plugins/config/github-integration.yaml \
   ~/.corvin/plugins/backups/github-integration.manual-backup.yaml
```

### 3. Monitor Rollback Events

```bash
# Check for any recent rollbacks
corvin audit:search --event "plugin.rollback" --limit 20
```

### 4. Schedule Snapshot Cleanup

```bash
# Weekly cleanup of old snapshots
# Add to crontab:
0 2 * * 0 corvin plugins:cleanup-snapshots --keep 10
```

---

## Rollback SLA

| Scenario | Target Time | Typical Time |
|----------|------------|--------------|
| Automatic rollback on install failure | <30s | 5-15s |
| Manual rollback from snapshot | <2m | 30-60s |
| Emergency disable-all | <10s | 3-5s |
| Full recovery (all plugins) | <10m | 3-5m |

---

## Support

**For rollback issues:**
- `corvin plugins:diagnose` — Run system diagnostics
- `corvin audit:export` — Export logs for support
- Contact: support@corvin.io

---

**Learn More:**
- [User Guide](PLUGIN_MARKETPLACE_USER_GUIDE.md)
- [Operator Guide](PLUGIN_MARKETPLACE_OPERATOR_GUIDE.md)
