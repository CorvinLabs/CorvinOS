# CorvinOS Upgrade Guide: v0.5 → v1.0

**For upgrading from CorvinOS v0.5 (baseline) to v1.0 (stable release)**

**Duration:** 15 minutes (automated), <1 minute per rollback  
**Downtime:** <2 minutes  
**Data loss risk:** Zero (tested)

---

## Table of Contents

1. [Pre-Upgrade Checklist](#pre-upgrade-checklist)
2. [Upgrade Path (v0.5 → v1.0)](#upgrade-path)
3. [Per-Release Upgrade Notes](#per-release-upgrade-notes)
4. [Data Migration](#data-migration)
5. [Rollback Procedures](#rollback-procedures)
6. [Troubleshooting](#troubleshooting)

---

## Pre-Upgrade Checklist

Before upgrading, verify:

- [ ] **Backup:** Run `corvinOS backup create` (saves all settings, data, plugins)
- [ ] **Internet:** Connected to reliable network (upgrade ~200MB download)
- [ ] **Disk space:** ≥500MB free (includes new features)
- [ ] **Current version:** Run `corvinOS --version` (should be v0.5.x)
- [ ] **No pending tasks:** Complete or queue offline tasks before upgrade
- [ ] **Read this guide:** You're reading it now ✓

---

## Upgrade Path

### Quick Start (Automated)

```bash
corvinOS update check
corvinOS update install

# Wait for completion (~10 minutes)
corvinOS status
# Output: "CorvinOS v1.0.0 running"
```

### What Gets Upgraded

| Component | v0.5 | v1.0 | Status |
|---|---|---|---|
| Brain core | 13 subsystems | 13 subsystems | ✓ Compatible |
| Operator Modeling | None | Full v0.6 | ✓ New (opt-in) |
| Plugins | Basic | Marketplace (v0.7) | ✓ New (opt-in) |
| Offline Mode | None | Full v0.8 | ✓ New (opt-in) |
| Dashboard | Basic | Advanced (v0.9) | ✓ Enhanced |
| Audit trail | v0.5 schema | v1.0 schema | ✓ Migrated |
| Database | SQLite v3.33 | SQLite v3.44 | ✓ Upgraded |
| UI | v0.5 React | v0.5 React | ✓ No breaking changes |

**Key:** All new features ship with feature flags OFF by default. Operator experience unchanged until you enable them.

---

## Per-Release Upgrade Notes

### v0.5 → v0.6 (Operator Modeling)

**What's new:**
- Decision audit (captures your choices)
- Operator fingerprinting (learns your style)
- Task suggestions (optional, OFF by default)
- What-If replay (counterfactual analysis)

**What changes for you:**
- Zero breaking changes
- New "Learning" section in Settings (can ignore)
- Optional annotation box after decisions (dismiss if not interested)

**Data impact:**
- New table: `operator_fingerprints` (empty until you enable)
- New table: `decision_audits` (captures decisions going forward)
- Existing data: Untouched

**Rollback cost:** <1 minute (delete v0.6 tables)

---

### v0.6 → v0.7 (Plugin Ecosystem)

**What's new:**
- Plugin marketplace (50+ community plugins)
- Sandboxing (plugins isolated, safe)
- Plugin ratings + governance

**What changes for you:**
- Zero breaking changes
- New "Marketplace" section in Settings
- Brain suggests plugins (only if installed)

**Data impact:**
- New table: `plugins_installed`
- New table: `plugin_ratings`
- Existing data: Untouched

**Rollback cost:** <1 minute (uninstall plugins, delete tables)

---

### v0.7 → v0.8 (Offline Mode)

**What's new:**
- Work without internet (Llama 2 7B local fallback)
- Operation queue (reliable offline task queuing)
- CRDT state merge (sync when back online)

**What changes for you:**
- Optional local LLM installation (~4GB download)
- New "Offline" section in Settings
- Queue visible on Dashboard (if offline)

**Data impact:**
- New table: `operation_queue`
- New directory: `~/.corvin/cache/llm/` (4GB local model)
- Existing data: Untouched

**Network impact:**
- Download ~4GB for local LLM (can disable in Settings)
- No automatic download (opt-in)

**Rollback cost:** <1 minute (delete v0.8 tables + cache)

---

### v0.8 → v0.9 (Real-Time Dashboard)

**What's new:**
- Live Brain monitoring (13 subsystems)
- Real-time decision stream
- Pause/resume/redirect tasks
- Cost tracker

**What changes for you:**
- Zero breaking changes
- New "Dashboard" tab in Console (you might love it!)
- WebSocket connection for real-time updates

**Data impact:**
- New table: `dashboard_events`
- Existing data: Untouched

**Rollback cost:** <1 minute (disable Dashboard)

---

### v0.9 → v1.0 (Production Release)

**What's new:**
- Hardening (security + performance)
- Complete documentation
- Enterprise support
- Stable API (no breaking changes for 6 months)

**What changes for you:**
- Zero breaking changes
- Cleaner UI (some minor tweaks)
- Faster performance (optimized)

**Data impact:**
- No new tables
- Audit trail schema unchanged
- Existing data: Untouched

**Rollback cost:** <1 minute (restore v0.9 binary)

---

## Data Migration

### Automatic Migration

**During upgrade, CorvinOS automatically:**

1. Backs up existing database
2. Runs migration scripts (per release)
3. Validates data integrity
4. Enables new features (OFF by default)
5. Starts v1.0

**Time:** ~5 minutes (happens automatically)

**If migration fails:** CorvinOS rolls back to v0.5 (keeps backup)

### Manual Data Export

**To export data before upgrade:**

```bash
corvinOS export /tmp/corvinOS-backup.tar.gz
# Includes: settings, decisions, fingerprints, plugins, audit trail
```

**To import after downgrade:**

```bash
corvinOS import /tmp/corvinOS-backup.tar.gz
```

### What's Migrated

| Data | v0.5 | v0.6 | v0.7 | v0.8 | v0.9 | v1.0 |
|---|---|---|---|---|---|---|
| **Settings** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Audit trail** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Brain cache** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Decision history** | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Operator fingerprint** | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Plugin data** | ✗ | ✗ | ✓ | ✓ | ✓ | ✓ |
| **Offline queue** | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ |
| **Dashboard state** | ✗ | ✗ | ✗ | ✗ | ✓ | ✓ |

**Note:** Data is NOT backfilled. New features build from Day 1 post-upgrade.

---

## Rollback Procedures

### Rollback to v0.5 (Emergency Only)

**If v1.0 is broken:**

```bash
# Automated rollback
corvinOS downgrade v0.5

# Or manual rollback
corvinOS stop
cd ~/.corvin/backups
tar -xzf backup-v0.5-latest.tar.gz -C ~/.corvin
corvinOS start
```

**Verify:**
```bash
corvinOS status
# Should show: "CorvinOS v0.5.x running"
```

**What you lose:**
- Any decisions made in v0.6–v1.0 (post-upgrade)
- Any plugins installed
- Any offline queue

**Timeline:** <1 minute

### Rollback to v0.6 (from v1.0)

**If v0.7–v1.0 features cause issues:**

```bash
corvinOS downgrade v0.6

# Then disable v0.7–v1.0 features manually
vim ~/.corvin/config.yaml
# Set: plugins_enabled: false, offline_mode: false, dashboard_enabled: false
```

### Per-Release Disable (Without Downgrade)

**If you just want to turn off a feature:**

```bash
# Edit config
vim ~/.corvin/config.yaml

# Disable specific features
spec:
  features:
    operator_modeling_suggestions: false   # v0.6 suggestions
    plugins_enabled: false                 # v0.7 plugins
    offline_mode: false                    # v0.8 offline
    dashboard_enabled: false               # v0.9 dashboard

# Restart
corvinOS restart
```

---

## Troubleshooting

### Upgrade fails (error during migration)

**Symptom:** "Migration failed, rolling back..."

**Diagnosis:**
```bash
corvinOS logs | grep -i error
```

**Fix:**
1. Free up disk space (≥500MB)
2. Restart CorvinOS: `corvinOS restart`
3. Retry upgrade: `corvinOS update install`

**If still fails:**
- Rollback: `corvinOS downgrade v0.5`
- Contact support: support@corvinOS.io

---

### Downgrade gets stuck

**Symptom:** "Downgrading..." for >5 minutes

**Fix:**
```bash
# Kill stuck process
pkill -f corvinOS

# Manual downgrade
cd ~/.corvin/backups
ls -la  # Find latest backup
tar -xzf backup-v0.5-20261025.tar.gz -C ~/.corvin --overwrite
corvinOS start
```

---

### Operator fingerprint disappeared after upgrade

**Symptom:** Learning → Your Talent shows "No data yet"

**Explanation:** Fingerprint not backfilled from v0.5. Starts fresh in v0.6.

**Normal behavior:** Fingerprint rebuilds as you use CorvinOS (takes ~50 decisions, 1–2 weeks).

**To force rebuild:**
```bash
corvinOS learning reset  # Clears old data, starts fresh
```

---

### Offline mode won't download local LLM

**Symptom:** "Error downloading Llama 2 7B"

**Diagnosis:**
- Check disk space: `df -h` (need 4GB+)
- Check internet: `ping 8.8.8.8`
- Check download speed: Should be >1Mbps

**Fix:**
```bash
# Retry download
corvinOS offline install-llm --verbose

# Or manual download (if auto fails)
corvinOS offline install-llm --source=https://mirrors.example.com/llama-2-7b-q4.gguf
```

---

### Performance degraded after upgrade

**Symptom:** Tasks now take longer than before

**Diagnosis:**
```bash
corvinOS status performance
# Check: CPU, Memory, Disk I/O
```

**Possible causes:**
- Learning system running in background (new in v0.6)
- Dashboard event streaming (new in v0.9)
- Plugins loaded (new in v0.7)

**Fix:**
1. Disable unnecessary features: `vim ~/.corvin/config.yaml`
2. Clear cache: `corvinOS cache clear`
3. Restart: `corvinOS restart`

---

## Breaking Changes (v0.5 → v1.0)

**None for operators.** All breaking changes are:
- Opt-in (behind feature flags)
- Backwards compatible (v0.5 behavior available)
- Documented

### For Plugin Developers

- **v0.7+:** Old plugin API v1 deprecated (v2 required)
- **v0.8+:** Sandbox rules tightened (some plugins may need updates)
- **v0.9+:** Dashboard WebSocket API stabilized (no breaking changes after)

---

## Timeline

| Version | Release Date | Support Until | Upgrade Time |
|---|---|---|---|
| v0.5 | 2026-06-01 | 2026-10-01 | N/A |
| v0.6 | 2026-09-15 | 2026-12-15 | 10 min |
| v0.7 | 2026-10-13 | 2026-12-31 | 5 min |
| v0.8 | 2026-11-24 | 2027-01-31 | 10 min |
| v0.9 | 2026-12-22 | 2027-03-31 | 3 min |
| **v1.0** | **2027-01-05** | **2027-06-30** | **3 min** |

**Support timeline:**
- Each version supported for ~3 months after next release
- Security patches: 6 months minimum
- v1.0 LTS (Long-Term Support) through 2027-06-30

---

## FAQ

### Q: Do I have to upgrade?

**A:** No, v0.5 is supported through 2026-10-01. After that, security patches stop.

**Recommendation:** Upgrade to v1.0 by 2026-10-01 (easy, automated).

### Q: Will my data be lost?

**A:** No. CorvinOS creates automatic backups before each upgrade.

**Verify:**
```bash
ls -la ~/.corvin/backups/
```

### Q: How long is downtime?

**A:** <2 minutes total (automatic).

**During upgrade:** CorvinOS unavailable for ~1 minute.

### Q: Can I skip versions (v0.5 → v0.8)?

**A:** No, must follow path: v0.5 → v0.6 → v0.7 → v0.8 → v0.9 → v1.0.

**Reason:** Data migrations must run sequentially.

### Q: What if I need to downgrade?

**A:** Easy, under 1 minute:

```bash
corvinOS downgrade v0.5  # Back to any prior version
```

**Rollback cost:** You keep post-upgrade data, but v0.6+ features disabled.

---

## Support

**Having issues?** Three options:

1. **In-app:** Console → Help → Report upgrade issue
2. **Discord:** [corvinOS Discord #support](https://discord.gg/corvinOS)
3. **Email:** upgrade-support@corvinOS.io

**Include:** Your version, error message, OS, logs (`corvinOS logs --export`).

---

**Last Updated:** 2027-01-05  
**For:** v0.5 → v1.0 upgrade path  
**Status:** Tested, stable, safe

