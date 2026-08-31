# Plugin Marketplace Operator Guide

**Version:** 1.0.0  
**Date:** 2026-08-30  
**Status:** Production Ready

## Overview

This guide is for system administrators and operators who manage CorvinOS deployments with plugins enabled.

## Table of Contents

1. [Managing Plugin Installation](#managing-plugin-installation)
2. [Monitoring Plugin Activity](#monitoring-plugin-activity)
3. [Troubleshooting Installation Failures](#troubleshooting-installation-failures)
4. [Security Auditing](#security-auditing)
5. [Resource Management](#resource-management)
6. [Rollback Procedures](#rollback-procedures)

---

## Managing Plugin Installation

### Permitting or Blocking Plugins

CorvinOS administrators can control which plugins are allowed to install:

**To Block a Plugin:**

1. Edit your tenant configuration: `~/.corvin/tenant.corvin.yaml`
2. Add a blocked list:
   ```yaml
   plugins:
     blocked:
       - untrusted-plugin-id
       - experimental-feature-x
   ```
3. Restart CorvinOS or reload config
4. Any attempts to install blocked plugins will be rejected

**To Create an Allowlist (Whitelist-only mode):**

```yaml
plugins:
  mode: allowlist           # Only listed plugins can install
  allowed:
    - github-integration
    - slack-notifier
    - storage-sync
```

### Bulk Plugin Management

**Export installed plugins:**
```bash
corvin plugins:export > installed-plugins.json
```

**Restore to another CorvinOS instance:**
```bash
corvin plugins:import --file installed-plugins.json
```

**List all installed plugins:**
```bash
corvin plugins:list --format table --show permissions,version,author
```

---

## Monitoring Plugin Activity

### Viewing Plugin Logs

Plugin activities are logged to the audit trail (encrypted hash-chain):

**View recent plugin events:**
```bash
corvin audit:search --entity "plugin" --limit 100 | head -50
```

**View events for a specific plugin:**
```bash
corvin audit:search --plugin-id github-integration --limit 50
```

**View failed installations:**
```bash
corvin audit:search --event "plugin.install.failed" --limit 20
```

### Plugin Metrics

CorvinOS tracks plugin health metrics:

**View plugin statistics:**
```bash
corvin plugins:metrics
```

Output shows:
- Total plugins installed
- Number of enabled vs. disabled
- Total permissions granted
- Recent errors or warnings

**Detailed plugin metrics:**
```bash
corvin plugins:metrics --plugin-id github-integration
```

Shows:
- Uptime percentage
- CPU/Memory usage (if sandboxed)
- Last activity timestamp
- Error rate
- Configuration change history

### Setting Up Alerts

Configure alerts for plugin failures:

```yaml
# In ~/.corvin/alerting.yaml
alerts:
  - name: plugin-install-failure
    condition: |
      event.type == "plugin.install.failed"
    action: email
    recipients:
      - ops-team@example.com
    threshold: 1  # Alert on first failure

  - name: plugin-high-memory
    condition: |
      plugin.memory_mb > 1024
    action: slack
    channel: "#alerts"
    threshold: 1
```

---

## Troubleshooting Installation Failures

### Common Installation Errors

**Error: "Network error: Could not reach GitHub API"**

**Cause:** Firewall or network configuration blocks GitHub API access.

**Solution:**
1. Verify network connectivity: `ping api.github.com`
2. Check firewall rules allow `api.github.com:443`
3. If using an HTTP proxy, configure it: `corvin config set http.proxy <url>`
4. Retry installation after fixing network

---

**Error: "Plugin manifest is invalid"**

**Cause:** Plugin's `manifest.yaml` doesn't conform to schema.

**Solution:**
1. Check plugin's GitHub repository for issues
2. See if a newer version is available (may have a fix)
3. Report the issue to the plugin author
4. Consider using a different plugin if this persists

---

**Error: "Plugin version 1.5.0 requires CorvinOS >=2.0.0 (you have 0.7.0)"**

**Cause:** Plugin requires a newer CorvinOS version than installed.

**Solution:**
1. Upgrade CorvinOS: `corvin system:upgrade`
2. Or: Use an older plugin version compatible with your CorvinOS version
3. Check plugin's GitHub releases for version history

---

**Error: "Plugin requires 'database-connector' (version 3.0.0) which is not installed"**

**Cause:** Missing dependency plugin.

**Solution:**
1. Install the dependency first: `corvin plugins:install database-connector@3.0.0`
2. Then retry installing the original plugin

---

### Diagnostic Commands

**Validate plugin manifest locally:**
```bash
corvin plugins:validate --manifest /path/to/manifest.yaml
```

**Check plugin compatibility:**
```bash
corvin plugins:check-compatibility github-integration
# Reports: ✓ Compatible with CorvinOS 0.7.2
#          ✓ All dependencies installed
#          ⚠ Requires network.https permission
```

**Simulate plugin installation (dry-run):**
```bash
corvin plugins:install github-integration --dry-run
# Shows what would be installed without actually installing
```

---

## Security Auditing

### Permission Review

Regularly audit plugin permissions to ensure they're appropriate:

**List all plugins and their permissions:**
```bash
corvin plugins:list --show permissions
```

**Generate permission report:**
```bash
corvin audit:report --type "plugin-permissions" --output /tmp/plugin-perms.json
```

**Review high-risk permissions:**
- `network.http` / `network.https` — Can make web requests
- `filesystem.write` — Can modify files
- `process.exec` — Can run system commands

If a plugin uses risky permissions unexpectedly, disable it and investigate.

### Audit Trail Verification

CorvinOS maintains a cryptographically signed audit trail. Verify its integrity:

**Verify audit trail is intact:**
```bash
corvin audit:verify
# Output: ✓ Audit chain verified (entries: 45892, integrity: OK)
```

**Export audit logs for security review:**
```bash
corvin audit:export --since "2026-08-01" --until "2026-08-30" > audit-aug.jsonl
```

**Search for specific events:**
```bash
corvin audit:search --entity "plugin" --event "config.changed" --limit 100
```

### Configuration Secrets Protection

CorvinOS automatically masks secrets in audit logs (API keys, tokens, passwords are hashed, never exposed).

**Verify secrets are masked:**
```bash
# This should find 0 results (no raw secrets)
grep -E 'api[_-]?key|token|password' ~/.corvin/audit.jsonl
```

If you see raw secrets, that's a security issue — contact the CorvinOS team.

---

## Resource Management

### Monitoring Resource Usage

Plugins run in sandboxes with CPU and memory limits. Monitor their usage:

**Show real-time plugin resource usage:**
```bash
corvin plugins:monitor --live
```

**Show resource limits for a plugin:**
```bash
corvin plugins:show github-integration
# Sandbox CPU Limit: 25%
# Sandbox Memory Limit: 512 MB
# Sandbox Timeout: 5 minutes
```

### Adjusting Resource Limits

If a plugin needs more resources, adjust its limits:

```yaml
# In ~/.corvin/tenant.corvin.yaml
plugins:
  overrides:
    github-integration:
      sandbox:
        cpu_limit_percent: 50      # Increased from 25%
        memory_limit_mb: 1024      # Increased from 512 MB
        timeout_seconds: 600       # Increased from 300s
```

Restart the plugin after changing limits:
```bash
corvin plugins:restart github-integration
```

### Quota Management

Set quotas to limit total plugin resource usage:

```yaml
# In ~/.corvin/tenant.corvin.yaml
plugins:
  quotas:
    total_memory_mb: 2048          # All plugins combined
    total_cpu_percent: 100         # All plugins combined
    concurrent_operations: 10      # Max ops running at once
```

---

## Rollback Procedures

### Automatic Rollback

If a plugin installation fails, CorvinOS automatically attempts rollback:

1. **Pre-install snapshot** is created before installation starts
2. **If installation fails**, rollback runs automatically
3. **System restores** to pre-install state
4. **Error details** are logged for investigation

You'll see: `"Plugin rollback initiated due to: <reason>"`

### Manual Rollback

If needed, manually rollback a plugin to a previous version:

**List available snapshots for a plugin:**
```bash
corvin plugins:snapshots github-integration
# Snapshot ID              Type            Timestamp
# github-int-snap-abc123   pre-install     2026-08-30 10:15:00
# github-int-snap-def456   post-install    2026-08-30 10:20:00
# github-int-snap-ghi789   pre-install     2026-08-29 14:30:00
```

**Restore from a specific snapshot:**
```bash
corvin plugins:restore github-integration --snapshot github-int-snap-abc123
# ✓ Plugin restored to pre-install state
# ✓ Configuration backed up at ~/.corvin/plugins/backups/github-integration-2026-08-30-10-45.yaml
```

### Snapshot Retention

CorvinOS keeps the most recent 10 snapshots per plugin. Older snapshots are automatically deleted.

**Manual cleanup:**
```bash
corvin plugins:cleanup-snapshots github-integration --keep 5
# Deleted 5 old snapshots
```

### Disaster Recovery

If a plugin breaks your CorvinOS installation:

1. **Disable all plugins:**
   ```bash
   corvin plugins:disable-all
   ```

2. **Verify CorvinOS starts:**
   ```bash
   corvin status
   # Should show all systems running
   ```

3. **Enable plugins one by one and test:**
   ```bash
   corvin plugins:enable github-integration
   corvin test  # Run system tests
   ```

4. **If one plugin breaks everything:**
   ```bash
   corvin plugins:uninstall problematic-plugin
   corvin audit:search --plugin-id problematic-plugin --output /tmp/debug.json
   ```

---

## Configuration Backups

### Automatic Backups

CorvinOS backs up plugin configurations before updates:

**List configuration backups:**
```bash
ls -lh ~/.corvin/plugins/backups/
# github-integration-2026-08-30-10-45.yaml
# github-integration-2026-08-29-15-30.yaml
# ...
```

**Restore a configuration backup:**
```bash
corvin plugins:config-restore github-integration --backup github-integration-2026-08-29-15-30.yaml
# ✓ Configuration restored
```

### Manual Backup

Before making config changes:
```bash
cp ~/.corvin/plugins/config/github-integration.yaml ~/.corvin/plugins/config/github-integration.backup
# Make changes
# If something breaks:
cp ~/.corvin/plugins/config/github-integration.backup ~/.corvin/plugins/config/github-integration.yaml
```

---

## Key Metrics to Monitor

**Plugin Health Dashboard should track:**

| Metric | Alert Threshold | Action |
|--------|-----------------|--------|
| Installation Failures | >3 in 1 hour | Investigate plugin/network |
| Plugin Crashes | >5 per day | Disable plugin, check logs |
| Permission Denials | >10 per day | Review if permissions make sense |
| Resource Exhaustion | CPU >90% | Increase limit or disable plugin |
| Auth Errors | Any | Review credentials/tokens in config |

---

## Support & Escalation

**For plugin-specific issues:**
- Contact the plugin author via GitHub Issues

**For CorvinOS plugin system issues:**
- CorvinOS Support: support@corvin.io
- Include: `corvin plugins:export`, `corvin audit:export --limit 100`

**For security concerns:**
- security@corvin.io
- Include: description of concern, audit log snippets (redacted if needed)

---

**Learn More:**
- [User Guide](PLUGIN_MARKETPLACE_USER_GUIDE.md) — For end users
- [Developer Guide](PLUGIN_MARKETPLACE_DEVELOPER_GUIDE.md) — For plugin authors
- [Rollback Procedures](PLUGIN_MARKETPLACE_ROLLBACK.md) — Detailed recovery steps
