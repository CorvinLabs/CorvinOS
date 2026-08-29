# OPERATOR WORKFLOW
## End-to-End Plugin Installation & Management Guide

**Document Version:** 1.0  
**Status:** USER GUIDE  
**Last Updated:** 2026-08-29

---

## QUICK START (5 MINUTES)

### Install Your First Marketplace Plugin

```bash
# 1. List available Tier 1 plugins
corvin plugin list --tier 1

# 2. Install slack-notifier
corvin plugin install slack-notifier

# 3. Configure (replace with your webhook URL)
nano ~/.corvin/plugins/installed/slack-notifier/config.yaml

# 4. Enable the plugin
corvin plugin enable slack-notifier

# 5. Verify it's working
corvin plugin status slack-notifier

# 6. (Optional) Test with a sample event
corvin plugin test slack-notifier --event audit.event
```

Expected output after step 5:
```
Plugin: slack-notifier
Version: 1.0.0
Installed: ~/.corvin/plugins/installed/slack-notifier/
Enabled: yes
Last enabled: 2026-08-29 15:30:00
Health: ✅ OK
Last activity: 2026-08-29 15:30:15 (5 events forwarded)
```

---

## PART I: DISCOVERING PLUGINS

### 1.1 List All Available Plugins

```bash
corvin plugin list

# Output:
# Available Plugins (Tier 1 - Bundled)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ✓ slack-notifier (v1.0.0)
#   Forward audit events to Slack via webhook
#   Category: notification
#   Status: not installed
#
# ✓ data-transform-json-csv (v1.0.0)
#   Convert between JSON and CSV formats
#   Category: utility
#   Status: not installed
#
# ✓ audit-backend-json (v1.0.0)
#   JSON file-based audit backend
#   Category: infrastructure
#   Status: not installed
#
# [... 4 more plugins]
```

### 1.2 Filter by Category

```bash
# Show only notification plugins
corvin plugin list --category notification

# Show only backend plugins
corvin plugin list --category backend

# Show only installed plugins
corvin plugin list --installed

# Show only enabled plugins
corvin plugin list --enabled
```

### 1.3 Get Details About a Plugin

```bash
corvin plugin info slack-notifier

# Output:
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Slack Notifier (v1.0.0)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# Description:
#   Forward audit events to Slack via webhook
#
# Author: Corvin Labs <support@corvin-labs.com>
# License: Apache-2.0
# Homepage: https://github.com/corvin-labs/Corvin-Marketplace
#
# Category: notification
# Boot Layer: bundled
# Origin: builtin
# Tags: notification, integration, audit
#
# Requirements:
#   Python >= 3.9
#   requests >= 2.28.0
#   slack-sdk >= 3.20.0
#
# Permissions:
#   Network egress: allowed (Slack webhook)
#   PII risk: medium
#   Data locality: external
#
# Required Configuration:
#   webhook_url (string) — Slack webhook URL
#
# Optional Configuration:
#   notification_events (array) — Event types to forward (default: ["audit.event"])
#   batch_size (integer) — Batch size (default: 10)
#   timeout_seconds (integer) — HTTP timeout (default: 30)
```

### 1.4 Search for Plugins

```bash
# Search by name
corvin plugin search slack

# Search by keyword
corvin plugin search notification

# Output:
# Search Results: 3 matching plugins
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# slack-notifier (v1.0.0)
#   Forward audit events to Slack via webhook
#
# [2 more results]
```

---

## PART II: INSTALLATION

### 2.1 Install from Marketplace

```bash
# Install the latest version
corvin plugin install slack-notifier

# Install a specific version
corvin plugin install slack-notifier@1.0.0

# Install multiple plugins at once
corvin plugin install slack-notifier data-transform-json-csv audit-backend-json
```

### 2.2 Install from Local Directory

If you have plugin code on your machine:

```bash
corvin plugin install ./path/to/slack-notifier/

# Or from a tarball
corvin plugin install ./slack-notifier-1.0.0.tar.gz
```

### 2.3 Verify Installation

```bash
# Check if plugin is installed
ls ~/.corvin/plugins/installed/slack-notifier/

# Expected files:
# manifest.yaml
# plugin.py
# config.yaml (if already configured)
# requirements.txt
```

### 2.4 Handle Installation Errors

**Error: "Plugin not found"**
```bash
# Check spelling
corvin plugin list | grep -i slack

# Make sure you're online
ping github.com
```

**Error: "Dependency missing"**
```bash
# Install Python dependencies (usually automatic)
pip install -r ~/.corvin/plugins/installed/slack-notifier/requirements.txt
```

**Error: "Permission denied"**
```bash
# Make sure ~/.corvin/ is writable
chmod -R u+w ~/.corvin/
```

---

## PART III: CONFIGURATION

### 3.1 Required vs. Optional Settings

Each plugin has a settings schema that defines which fields are required:

```bash
corvin plugin info slack-notifier --schema

# Output shows:
# Required configuration:
# - webhook_url (string): Slack webhook URL
#
# Optional configuration:
# - notification_events (array): default ["audit.event"]
# - batch_size (integer): default 10
# - timeout_seconds (integer): default 30
```

### 3.2 Edit Configuration

After installation, edit the plugin's config file:

```bash
nano ~/.corvin/plugins/installed/slack-notifier/config.yaml
```

**Example configuration:**
```yaml
# Slack Notifier Configuration

# REQUIRED: Slack webhook URL
# Get this from: https://api.slack.com/apps/YOUR_APP_ID/incoming-webhooks/
webhook_url: "https://hooks.slack.com/services/XXXX/YYYY/ZZZZ"

# OPTIONAL: Which events to forward to Slack
# Available: audit.event, error.critical, plugin.enabled, plugin.disabled
notification_events:
  - audit.event
  - error.critical

# OPTIONAL: Batch notifications together to reduce API calls
batch_size: 10

# OPTIONAL: HTTP timeout for Slack API calls (in seconds)
timeout_seconds: 30

# OPTIONAL: Message format (slack or text)
message_format: "slack"

# OPTIONAL: Include timestamp in messages
include_timestamp: true
```

### 3.3 Validate Configuration

```bash
# Validate without enabling
corvin plugin validate slack-notifier

# Output:
# ✅ Configuration valid
#   webhook_url: https://hooks.slack.com/services/...
#   notification_events: ["audit.event", "error.critical"]
#   batch_size: 10
#   timeout_seconds: 30
```

If validation fails:
```bash
# Get specific error message
corvin plugin validate slack-notifier --verbose

# Output might be:
# ❌ Configuration invalid
#   webhook_url: Invalid URL format. Expected: https://hooks.slack.com/...
```

### 3.4 Reset to Default Configuration

```bash
corvin plugin reset-config slack-notifier

# This overwrites your config.yaml with defaults
# You'll need to reconfigure
```

---

## PART IV: ENABLING & DISABLING

### 4.1 Enable a Plugin

Enable means the plugin will be loaded and run:

```bash
corvin plugin enable slack-notifier

# Output:
# ✓ Enabling slack-notifier...
# ✓ Reloading plugin registry...
# ✓ Running health_check...
# ✓ slack-notifier is now active
# ✓ Audit event logged: plugin.enabled
```

### 4.2 Disable a Plugin

Disable means the plugin stays installed but is not loaded:

```bash
corvin plugin disable slack-notifier

# Output:
# ✓ Disabling slack-notifier...
# ✓ Unregistering from notification system...
# ✓ slack-notifier is now inactive
# ✓ Audit event logged: plugin.disabled
```

**When to disable:**
- Troubleshooting an issue
- Taking a plugin offline temporarily
- Testing a different plugin

### 4.3 Check Plugin Status

```bash
corvin plugin status slack-notifier

# Output:
# ┌─ slack-notifier ──────────────────────────────────────┐
# │                                                        │
# │ Version: 1.0.0                                        │
# │ Status: enabled                                       │
# │ Installed: /home/user/.corvin/plugins/installed/      │
# │ Last enabled: 2026-08-29 15:30:00                     │
# │ Health check: ✅ HEALTHY                              │
# │ Activity (last 24h): 147 events processed             │
# │                                                        │
# └────────────────────────────────────────────────────────┘
```

---

## PART V: TESTING

### 5.1 Run a Health Check

```bash
corvin plugin health slack-notifier

# Output:
# Checking health of slack-notifier...
# ✅ Plugin initialized
# ✅ Configuration valid
# ✅ Webhook URL reachable
# ✅ Webhook returned 200 OK
# Overall status: HEALTHY
```

### 5.2 Send a Test Event

```bash
# Send a test audit event
corvin plugin test slack-notifier --event audit.event

# Output:
# Sending test event to slack-notifier...
# ✓ Event queued
# ✓ Event processed
# ✓ Notification sent to Slack
#
# Message sent:
# 🔔 Plugin Health Check
#    Time: 2026-08-29 15:31:00
#    Event: audit.event
#    Plugin: slack-notifier
```

### 5.3 View Recent Activity

```bash
# Show last 10 events processed
corvin plugin log slack-notifier --lines 10

# Output:
# slack-notifier Activity Log
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 15:31:05  ✓  audit.event — Message sent to Slack (12ms)
# 15:31:02  ✓  audit.event — Message sent to Slack (14ms)
# 15:30:59  ✗  error.critical — Webhook timeout (5000ms)
# 15:30:58  ✓  audit.event — Message sent to Slack (13ms)
# 15:30:55  ✓  audit.event — Message sent to Slack (11ms)
# [...]
```

---

## PART VI: UNINSTALLATION

### 6.1 Uninstall a Plugin

Uninstall completely removes the plugin:

```bash
corvin plugin uninstall slack-notifier

# Output:
# Uninstalling slack-notifier...
# ✓ Disabling plugin
# ✓ Removing configuration
# ✓ Deleting plugin files
# ✓ Cleanup complete
# ✓ Audit event logged: plugin.uninstalled
```

### 6.2 Backup Before Uninstall

```bash
# Backup configuration in case you want to reinstall later
cp -r ~/.corvin/plugins/installed/slack-notifier ~/backups/slack-notifier-backup-2026-08-29

# Now safe to uninstall
corvin plugin uninstall slack-notifier
```

### 6.3 Restore from Backup

```bash
# If you want to reinstall with the same configuration
corvin plugin install slack-notifier
cp ~/backups/slack-notifier-backup-2026-08-29/config.yaml \
   ~/.corvin/plugins/installed/slack-notifier/config.yaml
corvin plugin enable slack-notifier
```

---

## PART VII: UPGRADING & DOWNGRADING

### 7.1 Check for Updates

```bash
# Check if newer version is available
corvin plugin updates

# Output:
# Available Updates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# slack-notifier: v1.0.0 → v1.1.0 available
#   + Fixed webhook timeout issues
#   + Added batch_size optimization
#   - Breaking: notification_events now requires at least one event
#
# data-transform-json-csv: up to date (v1.0.0)
```

### 7.2 Upgrade a Plugin

```bash
# Upgrade to the latest version
corvin plugin upgrade slack-notifier

# Output:
# Upgrading slack-notifier from v1.0.0 to v1.1.0...
# ✓ Downloading v1.1.0...
# ✓ Verifying integrity...
# ✓ Backing up current version...
# ✓ Installing new version...
# ✓ Validating configuration...
# ✓ Running health check...
# ✓ Upgrade complete
# ⚠️  Breaking change: notification_events now requires at least one event
#
# Current config is still valid ✓
```

### 7.3 Downgrade to Previous Version

```bash
# If there's an issue with the new version, downgrade
corvin plugin install slack-notifier@1.0.0

# Output:
# Installing slack-notifier v1.0.0 (from v1.1.0)...
# ✓ Downloading v1.0.0...
# ✓ Backing up current version...
# ✓ Installing previous version...
# ✓ Downgrade complete
#
# Previous version restored. Your config is still valid.
```

---

## PART VIII: TROUBLESHOOTING

### 8.1 Plugin Won't Enable

**Symptom:** `corvin plugin enable slack-notifier` fails

**Diagnosis:**
```bash
# Check health
corvin plugin health slack-notifier

# Check logs
corvin plugin log slack-notifier --lines 20

# Check configuration
corvin plugin validate slack-notifier --verbose
```

**Common causes & fixes:**

| Issue | Fix |
|---|---|
| Missing webhook URL | `nano ~/.corvin/plugins/installed/slack-notifier/config.yaml` and add webhook_url |
| Invalid webhook URL format | Make sure it starts with `https://hooks.slack.com/` |
| Python dependencies missing | `pip install -r ~/.corvin/plugins/installed/slack-notifier/requirements.txt` |
| Permission error | `chmod -R u+w ~/.corvin/plugins/` |

### 8.2 Webhook Timeout

**Symptom:** "Webhook timeout" errors in logs

**Solution:**
```bash
# Increase timeout in configuration
nano ~/.corvin/plugins/installed/slack-notifier/config.yaml

# Change:
timeout_seconds: 30  # Default
# To:
timeout_seconds: 60  # Longer timeout

# Reload plugin
corvin plugin disable slack-notifier
corvin plugin enable slack-notifier
```

### 8.3 Events Not Being Forwarded

**Symptom:** Events are generated but not sent to Slack

**Diagnosis:**
```bash
# Check which events are configured to forward
corvin plugin info slack-notifier --schema | grep notification_events

# Test with a specific event type
corvin plugin test slack-notifier --event audit.event --verbose

# Check activity log
corvin plugin log slack-notifier | head -20
```

**Common causes:**
- `notification_events` doesn't include the event type you're testing
- Plugin is disabled
- Webhook URL is wrong
- Slack workspace is not accessible

### 8.4 Plugin Crashes

**Symptom:** Plugin health check shows UNHEALTHY

```bash
# Get detailed error
corvin plugin health slack-notifier --verbose

# Check system logs
journalctl -u corvin-service -n 50

# Try restarting the plugin
corvin plugin disable slack-notifier
corvin plugin enable slack-notifier
```

If still broken:
```bash
# Check if there's a newer version
corvin plugin info slack-notifier

# Try upgrading
corvin plugin upgrade slack-notifier

# Or downgrade to previous known-good version
corvin plugin install slack-notifier@1.0.0
```

### 8.5 Get Help

```bash
# Show help for any command
corvin plugin --help
corvin plugin install --help
corvin plugin enable --help

# Or consult documentation
# - Plugin docs: ~/.corvin/plugins/installed/slack-notifier/README.md
# - Marketplace docs: https://github.com/corvin-labs/Corvin-Marketplace
```

---

## PART IX: ADVANCED USAGE

### 9.1 Multiple Instances of Same Plugin

Not supported. Each plugin can only be installed once.

To use multiple Slack webhooks, edit the configuration:
```yaml
webhook_url: "https://hooks.slack.com/services/..."
# Add custom field for multiple webhooks (if plugin supports it):
# webhooks:
#   - name: "ops-channel"
#     url: "https://hooks.slack.com/..."
#   - name: "alerts-channel"
#     url: "https://hooks.slack.com/..."
```

### 9.2 Batch Operations

```bash
# Install multiple plugins at once
corvin plugin install slack-notifier data-transform-json-csv audit-backend-json

# Enable multiple plugins
corvin plugin enable slack-notifier data-transform-json-csv

# Check status of all plugins
corvin plugin list --enabled
```

### 9.3 Audit Trail

All plugin operations are logged in the audit trail:

```bash
# View plugin-related audit events
corvin audit list --filter 'event_type ~ "plugin\."'

# Example output:
# 2026-08-29T15:31:00Z  plugin.installed    slack-notifier v1.0.0
# 2026-08-29T15:31:05Z  plugin.enabled      slack-notifier
# 2026-08-29T15:31:10Z  plugin.health_check slack-notifier HEALTHY
# 2026-08-29T15:31:15Z  plugin.activity     slack-notifier 5 events processed
```

---

## PART X: OPERATOR CHECKLIST

### First-Time Setup

- [ ] Reviewed available plugins: `corvin plugin list`
- [ ] Selected plugin: `corvin plugin info <plugin-name>`
- [ ] Installed plugin: `corvin plugin install <plugin-name>`
- [ ] Located configuration: `~/.corvin/plugins/installed/<plugin-name>/config.yaml`
- [ ] Updated configuration with required settings
- [ ] Validated configuration: `corvin plugin validate <plugin-name>`
- [ ] Enabled plugin: `corvin plugin enable <plugin-name>`
- [ ] Verified status: `corvin plugin status <plugin-name>`
- [ ] Tested with sample event: `corvin plugin test <plugin-name>`
- [ ] Confirmed in audit trail: `corvin audit list --filter 'plugin'`

### Ongoing Management

- [ ] Monthly: Check for plugin updates: `corvin plugin updates`
- [ ] Weekly: Review plugin logs: `corvin plugin log <plugin-name>`
- [ ] Weekly: Check plugin health: `corvin plugin health <plugin-name>`
- [ ] As needed: Backup configuration before upgrades
- [ ] As needed: Disable/enable for troubleshooting

---

**Document owner:** Operations Team  
**Status:** READY FOR PRODUCTION
