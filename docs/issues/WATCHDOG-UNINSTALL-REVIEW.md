# Code Review: feature/watchdog-and-uninstall Branch

**Status:** ⚠️ **Ready to merge WITH MINOR FIXES** (do not merge as-is)

**Branch:** `feature/watchdog-and-uninstall`  
**Target:** `main`  
**Changes:** setup.sh, uninstall.sh, bridge.sh, watchdog.sh, .gitignore  

---

## Summary

This branch adds two critical features:

1. **Watchdog Service** — Heals inactive/unresponsive bridge services automatically (every 60s)
2. **Uninstall Script** — Cleanly removes Corvin from a system with user prompts

**Overall quality: Good.** Both features are well-designed and follow existing patterns. However, there are **2 alignment issues** and **1 missing piece** that must be fixed before merge.

---

## Issues Found

### ❌ CRITICAL: Watchdog Installation Removed from setup.sh

**Problem:** The watchdog timer installation code was **completely removed** from setup.sh in this branch.

**In the old setup.sh (main):**
```bash
# Watchdog: heals services that ended up inactive (manual stop & forget) or
# active-but-unresponsive (HTTP /status fails). 
WD_SVC_TPL="$BRIDGES_DIR/shared/systemd/corvin-voice-bridge-watchdog.service"
WD_TMR_TPL="$BRIDGES_DIR/shared/systemd/corvin-voice-bridge-watchdog.timer"
...
if [[ -f "$WD_SVC_TPL" && -f "$WD_TMR_TPL" ]]; then
  mkdir -p "$USER_UNIT_DIR"
  _pr_esc="$(printf '%s' "$PLUGIN_DIR" | sed 's/[\/&]/\\&/g')"
  _wd_svc_out="$USER_UNIT_DIR/corvin-voice-bridge-watchdog.service"
  sed "s/__PLUGIN_ROOT__/$_pr_esc/g" "$WD_SVC_TPL" > "$_wd_svc_out"
  ...
  if systemctl --user enable --now corvin-voice-bridge-watchdog.timer >/dev/null 2>&1; then
    ok "Watchdog timer enabled (re-checks every 60s, heals dead services)"
  fi
fi
```

**In the new setup.sh (this branch):**
```bash
# ← Completely gone!
```

**Impact:** Fresh installs from this branch will NOT have the watchdog running. Existing systems updating to this branch will keep their watchdog (because uninstall.sh doesn't remove it), but new installs are broken.

**Fix Required:**
- Restore the watchdog installation block from main into the new setup.sh
- Ensure it runs in the final "Start the bridge" section (Step 9/9)
- Update placeholder substitution: check if it should be `__PLUGIN_ROOT__` or `__BRIDGES_DIR__`

**Watch for:** The watchdog service template uses `__BRIDGES_DIR__` as the placeholder (line 7 in watchdog.service):
```
ExecStart=/usr/bin/env bash __BRIDGES_DIR__/watchdog.sh
```

But the old setup.sh substitutes `__PLUGIN_ROOT__`:
```bash
sed "s/__PLUGIN_ROOT__/$_pr_esc/g"
```

→ **Needs clarification:** Should the placeholder be `__BRIDGES_DIR__` or `__PLUGIN_ROOT__`? The watchdog needs to know where `watchdog.sh` lives.

---

### ⚠️ ISSUE #2: Docker Setup Not Covered in uninstall.sh

**Problem:** The uninstall script only handles **systemd setup** (`~/.corvin`, `~/.config/systemd/user/`). It does NOT handle **Docker setup** (`/opt/corvin`).

**Context:** Corvin now supports two deployment modes:
1. **Systemd (user-level):** `~/.corvin`, `~/.config/`, `~/.config/systemd/user/` ← uninstall.sh covers this
2. **Docker (system-level):** `/opt/corvin`, `corvin-compose.service` (system systemd) ← uninstall.sh does NOT cover this

**Current uninstall.sh handles:**
- Systemd user units (`~/.config/systemd/user/`)
- Config dir (`~/.config/corvin-voice/`)
- Data dirs (`~/.corvin`, `~/.corvinOS`)
- Claude Code plugins

**Missing from uninstall.sh:**
- Stopping/disabling the Docker Compose service (`/etc/systemd/system/corvin-compose.service`)
- Removing Docker containers (`docker-compose down`, container prune)
- Removing persistent bind-mount state (`/opt/corvin/home/`)
- Removing the entire `/opt/corvin/` directory structure
- Guidance on Docker-specific cleanup (images, networks)

**Why this matters:**
- A user who ran the Docker bootstrap (ops/bootstrap/install.sh) will have files at `/opt/corvin/` that persist after running uninstall.sh
- They'll get leftovers: stale containers, networks, volumes
- Removing just the systemd units leaves `/opt/corvin/` untouched (correct for safety), but uninstall.sh should at least warn about this

**Fix Required:**
Add a new step (Step 6) to uninstall.sh:

```bash
# ─── 6. Docker Compose setup (if present) ─────────────────────────────────
step "6 / 5   Remove Docker Compose setup (if installed)"

DOCKER_SETUP_DIR="/opt/corvin"
DOCKER_SYSTEMD_UNIT="/etc/systemd/system/corvin-compose.service"

if [[ -d "$DOCKER_SETUP_DIR" ]] || [[ -f "$DOCKER_SYSTEMD_UNIT" ]]; then
  echo
  warn "Docker Compose setup detected: $DOCKER_SETUP_DIR"
  
  # Stop the service
  if [[ -f "$DOCKER_SYSTEMD_UNIT" ]]; then
    sudo systemctl stop corvin-compose 2>/dev/null || true
    sudo systemctl disable corvin-compose 2>/dev/null || true
    ask "Delete $DOCKER_SYSTEMD_UNIT? [Y/n] "
    read -r yn ||: ; yn="${yn:-Y}"
    if [[ ! "$yn" =~ ^[Nn]$ ]]; then
      sudo rm -f "$DOCKER_SYSTEMD_UNIT"
      sudo systemctl daemon-reload
      ok "Removed $DOCKER_SYSTEMD_UNIT"
      REMOVED+=("systemd unit: $DOCKER_SYSTEMD_UNIT")
    fi
  fi
  
  # Clean up Docker
  ask "Stop and remove Docker containers + networks? [Y/n] "
  read -r yn ||: ; yn="${yn:-Y}"
  if [[ ! "$yn" =~ ^[Nn]$ ]]; then
    if [[ -f "$DOCKER_SETUP_DIR/docker-compose.yml" ]]; then
      (cd "$DOCKER_SETUP_DIR" && docker compose down -v 2>/dev/null) && ok "Docker cleanup done" || warn "Docker cleanup partial"
      REMOVED+=("Docker containers + networks")
    fi
  fi
  
  # Remove /opt/corvin
  ask "Delete entire Docker data dir ($DOCKER_SETUP_DIR)? [y/N] "
  read -r yn ||: ; yn="${yn:-N}"
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    sudo rm -rf "$DOCKER_SETUP_DIR"
    ok "Removed $DOCKER_SETUP_DIR"
    REMOVED+=("Docker data dir: $DOCKER_SETUP_DIR")
  else
    info "Keeping $DOCKER_SETUP_DIR — manual cleanup:"
    info "  sudo rm -rf $DOCKER_SETUP_DIR"
    info "  docker system prune -a  # remove unused images"
    SKIPPED+=("Docker data dir ($DOCKER_SETUP_DIR)")
  fi
else
  ok "No Docker setup detected."
fi
```

**Or (simpler approach):** Keep uninstall.sh systemd-only but add a note:

```bash
info "If you used Docker (ops/bootstrap/install.sh):"
info "  sudo systemctl stop corvin-compose"
info "  sudo docker compose -f /opt/corvin/docker-compose.yml down -v"
info "  sudo rm -rf /opt/corvin /etc/systemd/system/corvin-compose.service"
```

---

### ⚠️ ISSUE #3: Plugin Name Inconsistency (voice@corvin-voice-local vs voice@corvin-local)

**Problem:** Plugin registration uses two different naming schemes inconsistently.

**In setup.sh (new version):**
```bash
if claude plugin list 2>/dev/null | grep -q "voice@corvin-local"; then
  ok "Voice plugin already registered"
else
  _install_plugin "voice@corvin-local" "Voice plugin"
fi
```

**In uninstall.sh:**
```bash
echo "$_installed_plugins" | grep -q "voice@corvin-local"  && _has_voice=1
echo "$_installed_plugins" | grep -q "cowork@corvin-local" && _has_cowork=1
```

**In old setup.sh (still on main, for reference):**
```bash
if claude plugin list 2>/dev/null | grep -q "voice@corvin-voice-local"; then
```

**Check:** The marketplace name in the new setup.sh is:
```bash
claude plugin marketplace add "$REPO_ROOT" >/tmp/Corvin-marketplace.log 2>&1 || true
# (no explicit marketplace name, uses directory name)
```

**Question:** What's the actual marketplace name? Is it `corvin-voice-local`, `corvin-local`, or something else?

**Fix:** Verify:
1. What does `claude plugin marketplace add "$REPO_ROOT"` actually register as?
2. Is it `corvin-local` (new, following the rebrand) or `corvin-voice-local` (old)?
3. Update both setup.sh and uninstall.sh to use the **same** name consistently

---

## Detailed Section Review

### setup.sh Changes ✅ (mostly good, minor issues)

| Change | Assessment | Note |
|--------|-----------|------|
| BRIDGES_DIR path fix | ✅ Good | Changed from `$PLUGIN_DIR/bridges` to `$REPO_ROOT/operator/bridges` — correct |
| Claude Code login flow | ✅ Good | Improved: detects `.credentials.json` instead of interactive prompt. Safe fallback |
| Step numbering | ✅ Good | Updated from 8 steps to 9 steps (added login as separate step) |
| Plugin marketplace/plugin name | ⚠️ Watch | Uses `corvin-local` (new rebrand name). Verify it matches actual marketplace. |
| Watchdog installation | ❌ Missing | **CRITICAL:** Code removed, not restored. Must restore. |
| Language fixes | ✅ Good | English standardization (German→English comments, formatting) |

---

### uninstall.sh (New File) ✅ (solid, just missing Docker)

| Section | Assessment | Note |
|---------|-----------|------|
| **Structure** | ✅ Excellent | 5 logical steps, user prompts at each, tracking removed/skipped |
| **Systemd removal** | ✅ Complete | Finds + removes all units, disables them cleanly, reloads daemon |
| **Service files** | ✅ Good | Checks both direct files and symlinks in `default.target.wants/` |
| **Config cleanup** | ✅ Good | Prompts before deleting API keys, size warning, graceful skip |
| **Data cleanup** | ✅ Good | Handles both `~/.corvin` and `~/.corvinOS` (backward compat), warns about size |
| **Plugin cleanup** | ✅ Good | Detects `claude` CLI, lists installed plugins, uninstalls + removes cache |
| **Docker support** | ❌ Missing | No Docker cleanup (system-level `/opt/corvin`) |
| **Idempotency** | ✅ Good | Safe to re-run, checks before acting |
| **Summary** | ✅ Nice touch | Lists what was removed vs. skipped |

---

### watchdog.sh (New File) ✅ (well-designed)

| Aspect | Assessment | Note |
|--------|-----------|------|
| **Functionality** | ✅ Solid | Handles three failure modes: inactive→start, HTTP fail→restart, warmup timeout |
| **Safety** | ✅ Good | Warmup delay prevents flapping, fail threshold prevents false restarts |
| **State tracking** | ✅ Good | Simple file-based state (`~/.cache/corvin-voice/watchdog-state`) |
| **Logging** | ✅ Good | Outputs to systemd journal, clear messages |
| **Service targets** | ✅ Reasonable | Checks adapter + whatsapp/discord/telegram (skips telegram) |
| **Language** | ⚠️ Outdated | Comments still in German, should match English standardization in setup.sh |

**Minor:** Update watchdog.sh comments to English for consistency:

```bash
# Before (German)
# Wed periodisch via systemd-timer called. Prüft je Service: ...

# After (English)
# Called periodically via systemd timer. Checks each service:
# 1) If enabled but inactive → start
# 2) If HTTP /status fails → restart after FAIL_THRESHOLD consecutive fails
# 3) Warmup period (WARMUP_SEC) prevents flapping on startup
```

---

### bridge.sh Changes ✅ (minimal, correct)

Only change: Added watchdog units to `ALL_UNITS` array:
```bash
UNIT_WATCHDOG_SVC="corvin-voice-bridge-watchdog.service"
UNIT_WATCHDOG_TIMER="corvin-voice-bridge-watchdog.timer"
```

**Assessment:** ✅ Correct. These units are now managed alongside other systemd units.

---

### watchdog.timer ✅ (new file, correct)

Standard systemd timer pattern:
- `OnBootSec=5min` — wait 5 min after boot before first run
- `OnUnitActiveSec=60s` — run every 60 seconds
- `Persistent=true` — catch up on missed runs after reboots

**Assessment:** ✅ Good. Reasonable defaults.

---

### .gitignore Changes ✅

Assuming standard additions for generated/local state. Not reviewed in detail.

---

## Merge Checklist

Before merging to main, **DO NOT merge yet**. Fix these first:

- [ ] **CRITICAL:** Restore watchdog installation code from main into new setup.sh (Step 9)
- [ ] **CRITICAL:** Verify placeholder name in watchdog.service (`__PLUGIN_ROOT__` vs `__BRIDGES_DIR__`) and ensure setup.sh uses the correct one
- [ ] **Important:** Add Docker Compose cleanup to uninstall.sh (or at minimum, document manual cleanup steps)
- [ ] **Nice-to-have:** Update watchdog.sh comments from German to English
- [ ] **Nice-to-have:** Verify plugin marketplace name is consistent everywhere (`corvin-local`)
- [ ] **QA:** Test on a fresh system:
  - Run `bash setup.sh` → watchdog should be installed and timer enabled
  - Run `bash uninstall.sh` → all services + files should be cleanly removed
  - Verify audit chain is not affected by uninstall (audit files should remain if user keeps them)

---

## Alignment with Existing Patterns

### ✅ Follows Conventions
- **Idempotent:** Both setup.sh and uninstall.sh can be re-run safely
- **Prompts first:** All destructive actions ask for confirmation, with sensible defaults
- **Status tracking:** Removed/skipped items tracked and summarized
- **Logging:** Uses existing ok/warn/info/fail helpers
- **Systemd:** Follows existing unit management pattern
- **Language:** English (with one German file remaining in watchdog.sh)

### ⚠️ Potential Issues
- **Docker missing:** Uninstall doesn't handle the new Docker deployment mode
- **Rebrand consistency:** Plugin name uses `corvin-local` (good) but should verify this works end-to-end
- **Placeholder confusion:** Watchdog.service uses `__BRIDGES_DIR__` but old setup.sh code used `__PLUGIN_ROOT__` — need to reconcile

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Fresh installs without watchdog | HIGH | Restore watchdog installation code before merge |
| Docker deployments can't uninstall cleanly | MEDIUM | Add Docker cleanup to uninstall.sh, or document manual steps |
| Plugin name mismatch at runtime | LOW | Verify marketplace name, test on fresh system |
| Watchdog script outdated (German comments) | VERY LOW | Cosmetic, fix later or in follow-up |

---

## Recommendation

**Status: ✅ Ready to merge AFTER fixes**

This is a well-designed branch adding two critical features (watchdog service + uninstall script). The code quality is high and follows existing patterns.

**Before merging:**
1. Restore the watchdog installation code from main
2. Clarify and fix the placeholder name issue
3. Add Docker cleanup to uninstall.sh
4. Test on a fresh system (setup → watchdog enabled → uninstall → clean removal)

**After merge:**
- Monitor for any uninstall-related issues from users
- Follow up with English translation of watchdog.sh comments
- Consider extending uninstall.sh to handle future deployment modes (Kubernetes, etc.)

---

## Files Changed Summary

| File | Type | Status | Notes |
|------|------|--------|-------|
| setup.sh | Modified | ⚠️ Has issues | Missing watchdog installation, needs verification |
| uninstall.sh | New | ✅ Good | Missing Docker cleanup |
| bridge.sh | Modified | ✅ OK | Just added watchdog units to array |
| watchdog.sh | New | ✅ Good | Comments in German, minor issue |
| watchdog.timer | New | ✅ OK | Standard systemd timer |
| watchdog.service | New | ⚠️ Placeholder issue | Needs verification (is it `__PLUGIN_ROOT__` or `__BRIDGES_DIR__`?) |
| .gitignore | Modified | ✅ OK | Not reviewed in detail |

---

## Next Steps for Maintainer

1. **Request changes** on this branch for the three issues above
2. Once fixed, run QA test suite
3. Merge to main
4. Announce watchdog feature in changelog
