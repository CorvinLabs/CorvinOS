# Plugin Marketplace User Guide

**Version:** 1.0.0  
**Date:** 2026-08-30  
**Status:** Production Ready

## Overview

The CorvinOS Plugin Marketplace is a centralized hub for discovering, installing, and managing community and verified plugins. This guide walks you through the key features and how to use them safely.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Browsing the Marketplace](#browsing-the-marketplace)
3. [Installing Plugins](#installing-plugins)
4. [Managing Plugins](#managing-plugins)
5. [Troubleshooting](#troubleshooting)
6. [FAQ](#faq)

---

## Getting Started

### Accessing the Marketplace

1. Open CorvinOS Console
2. Navigate to **Settings → Marketplace**
3. You'll see the plugin index with featured plugins

### Plugin Information

Each plugin displays:
- **Name & Version** — What the plugin is called and its current version
- **Rating** — Community rating (1-5 stars) and number of reviews
- **Category** — Type of plugin (Security, Performance, Integration, etc.)
- **Download Count** — How many users have installed it
- **Author** — Who built the plugin
- **Description** — What the plugin does

---

## Browsing the Marketplace

### Searching for Plugins

**By Keyword:**
1. Enter keywords in the search box (e.g., "GitHub", "database", "auth")
2. Results update as you type
3. Click on a plugin to see full details

**By Category:**
1. Use the **Category** filter on the left
2. Options: Authentication, Performance, Security, Database, Integration, UI, Analytics, Tooling
3. Click a category to show only plugins in that category

**By Popularity:**
- Plugins are sorted by rating and download count by default
- Higher-rated plugins appear first

### Viewing Plugin Details

Click on a plugin card to see:
- Full description (markdown-formatted)
- Author information and contact
- Rating breakdown (how many 5-stars, 4-stars, etc.)
- Recent reviews and comments
- Installation requirements
- Permissions required
- Repository link (if available)

---

## Installing Plugins

### Before You Install

**Check the Plugin's Permissions:**
Each plugin lists what it needs access to (storage, network, etc.). Review these carefully before installing.

**Read Recent Reviews:**
Look at the latest reviews to see if other users have encountered issues.

### Installation Steps

1. **Find the Plugin** — Search or browse the marketplace
2. **Click "Install"** — Button appears on the plugin card or detail page
3. **Review Permissions** — A dialog shows what the plugin needs access to
4. **Accept or Reject** — Click "Grant Permissions" to proceed, or "Cancel" to abort
5. **Installation Progress** — A progress indicator shows download and setup status
6. **Confirmation** — You'll see "Installation Complete" when done

### After Installation

The plugin appears in **Settings → Installed Plugins** with:
- **Status** — Enabled or Disabled
- **Version** — Currently installed version
- **Last Updated** — When this version was released
- **Enable/Disable Toggle** — Turn it on or off without uninstalling

---

## Managing Plugins

### Enabling and Disabling Plugins

1. Go to **Settings → Installed Plugins**
2. Find the plugin you want to manage
3. **To Disable:** Click the toggle switch or "Disable" button
   - Plugin stops running immediately
   - Configuration is preserved
4. **To Enable:** Click the toggle or "Enable" button
   - Plugin resumes running

### Uninstalling Plugins

1. Go to **Settings → Installed Plugins**
2. Click the plugin's card
3. Click "Uninstall" button
4. Confirm when prompted
5. Wait for uninstallation to complete

**After Uninstalling:**
- Plugin is completely removed
- Configuration data is backed up (can be restored)
- Resources are freed up

### Viewing Plugin Activity

1. Go to **Settings → Installed Plugins**
2. Click a plugin to see its details
3. **Activity Log** shows:
   - When it was installed/updated
   - When permissions were granted
   - Recent errors (if any)

---

## Troubleshooting

### Plugin Installation Failed

**Error Message:** "Network error: Could not reach GitHub API"

**Solutions:**
1. Check your internet connection
2. Wait a few minutes and try again (the service may be temporarily down)
3. If you're on a restricted network, ask your IT admin to whitelist GitHub API endpoints

---

**Error Message:** "Plugin manifest is invalid"

**Solutions:**
1. The plugin may be corrupted or incomplete
2. Try installing a different version if available
3. Report the issue to the plugin author (use the "Report Bug" link on the plugin's page)

---

**Error Message:** "Plugin requires 'auth-plugin' which is not installed"

**Solutions:**
1. The plugin depends on another plugin that you haven't installed yet
2. Install the required plugin first (the error message tells you which one)
3. Then retry installing the original plugin

---

**Error Message:** "Plugin was not installed: you denied required permissions"

**Solutions:**
1. You rejected permissions during installation
2. If you trust the plugin, reinstall it and grant the necessary permissions
3. If you're unsure about a permission, contact your administrator

---

### Plugin Performance Issues

**Plugin is using too much CPU/memory:**
1. Disable the plugin: **Settings → Installed Plugins → Toggle Off**
2. Check if the issue goes away
3. If so, the plugin may have a resource leak — contact the author

---

**Plugin keeps crashing:**
1. Try disabling and re-enabling it
2. Check for plugin updates (may be a bug fix)
3. Review the plugin's Activity Log for error details
4. Report the crash to the plugin author with the error message

---

### Rollback to Previous Version

If a plugin update causes problems:

1. Go to **Settings → Installed Plugins**
2. Click the problematic plugin
3. Look for **Version History** or **Rollback** option
4. Select a previous version and click "Restore"
5. The plugin will be downgraded to that version

---

## FAQ

**Q: Is it safe to install community plugins?**

A: Community plugins are community-contributed and audited by our security team. We review each plugin for obvious security issues before listing it. However, use your judgment — if a plugin requires unusual permissions (like network access when not needed), you can reject it or report concerns to the author.

**Q: What permissions should I watch out for?**

A: Be cautious with:
- **network.http / network.https** — Plugin can make web requests
- **filesystem.write** — Plugin can modify files
- **process.exec** — Plugin can run system commands

These aren't bad by themselves, but they should match what the plugin claims to do. For example, a "GitHub Integration" plugin *should* need network access, but a "Local File Organizer" shouldn't.

**Q: Can I disable built-in plugins?**

A: Most built-in plugins can be disabled without harming CorvinOS. Security-critical plugins (marked "Compliance" layer) cannot be disabled, as they're essential for data protection and audit compliance.

**Q: What happens to my plugin data when I uninstall?**

A: Your plugin configuration is backed up automatically. If you reinstall the same plugin, you can restore your previous configuration. If you're certain you won't use it again, you can delete the backup from **Settings → Plugin Backups**.

**Q: How often are plugins updated?**

A: That depends on the plugin author. Popular plugins are usually updated regularly. You'll see a notification when updates are available. We recommend installing updates promptly for security and bug-fix patches.

**Q: Can I request a plugin?**

A: Yes! Visit the **Plugin Requests** section in the marketplace to suggest plugins you'd like to see. The community votes on requests, and popular ones often get built by plugin developers.

**Q: What's the rating system?**

A: Plugins are rated 1-5 stars by users who've installed them. A plugin with fewer than 10 reviews is still building a track record. Plugins below 2 stars may be removed from the marketplace if they accumulate negative reviews.

---

## Support

- **Plugin Issues:** Contact the plugin author (link on plugin detail page)
- **Marketplace Bugs:** Report to CorvinOS Support (Settings → Support)
- **Security Concerns:** Report to security@corvin.io

---

**Learn More:**
- [Plugin Developer Guide](PLUGIN_MARKETPLACE_DEVELOPER_GUIDE.md) — For plugin authors
- [Operator Guide](PLUGIN_MARKETPLACE_OPERATOR_GUIDE.md) — For administrators
- [Security Checklist](PLUGIN_MARKETPLACE_SECURITY_CHECKLIST.md) — For security teams
