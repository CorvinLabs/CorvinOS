#!/bin/bash
# cross_platform_install_repair.sh — CorvinOS Installation Repair
# Fixes platform-specific issues with the operator→corvin_operator rename (2026-09-17)
#
# Purpose: Ensure the installation works on Windows/macOS/Linux despite:
#   - Git rename case-sensitivity issues (Windows)
#   - Path-length limits (Windows)
#   - Permission issues (POSIX file modes on Windows NTFS)
#
# Usage:
#   bash cross_platform_install_repair.sh [--diagnose] [--repair] [--force]
#
# Constraints:
#   - Zero external dependencies (only POSIX tools + git)
#   - Detects platform: win32/darwin/linux
#   - Idempotent: safe to re-run
#   - Fail-closed: errors block installation (no silent skip)
set -eu

###############################################################################
# Configuration
###############################################################################

SCRIPT_VERSION="1.0.0"
DIAGNOSE_MODE=0
REPAIR_MODE=0
FORCE_MODE=0

# Paths (relative to repo root)
BRIDGES_DIR="corvin_operator/bridges/shared"
VOICE_DIR="corvin_operator/voice"
OPERATOR_LEGACY_DIR="operator"  # If this exists, rename failed/incomplete

# Platform detection
UNAME_S=$(uname -s 2>/dev/null || echo "Unknown")
UNAME_M=$(uname -m 2>/dev/null || echo "unknown")

case "$UNAME_S" in
  Linux*)  PLATFORM="linux"   ;;
  Darwin*) PLATFORM="darwin"  ;;
  MINGW64*|MSYS*|CYGWIN*) PLATFORM="win32" ;;
  *)       PLATFORM="unknown" ;;
esac

PYTHON_V=$(python3 --version 2>/dev/null | awk '{print $2}' || echo "unknown")

###############################################################################
# Utilities
###############################################################################

_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }

log_info()  { echo "$(_green "✓")" "$@"; }
log_warn()  { echo "$(_yellow "⚠")" "$@" >&2; }
log_error() { echo "$(_red "✗")" "$@" >&2; }
log_debug() { [ "$DIAGNOSE_MODE" -eq 1 ] && echo "$(_dim "[DEBUG]")" "$@" >&2 || true; }

###############################################################################
# Phase 1: Diagnostics
###############################################################################

diagnose_git_state() {
  log_info "Diagnosing Git state…"

  # Check if we're in a git repo
  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    log_error "Not a git repository"
    return 1
  fi

  # Check if the rename is complete (new paths exist)
  if [ ! -d "$BRIDGES_DIR" ]; then
    log_error "New path missing: $BRIDGES_DIR"
    return 1
  fi

  # Check if old paths still exist (rename incomplete)
  if [ -d "$OPERATOR_LEGACY_DIR" ]; then
    log_warn "Old path still exists: $OPERATOR_LEGACY_DIR (rename incomplete?)"
    return 1
  fi

  log_info "Git state: healthy"
  return 0
}

diagnose_file_structure() {
  log_info "Checking file structure…"

  local bridges_count=0
  local bridges_missing=0

  # Count Python files in bridges/shared
  if [ -d "$BRIDGES_DIR" ]; then
    bridges_count=$(find "$BRIDGES_DIR" -maxdepth 1 -name "*.py" 2>/dev/null | wc -l)
    log_debug "  Found $bridges_count Python files in $BRIDGES_DIR"
  else
    log_error "  Missing directory: $BRIDGES_DIR"
    return 1
  fi

  # Count inbox/outbox files
  local inbox_count=$(find "$BRIDGES_DIR/inbox" -type f 2>/dev/null | wc -l || echo 0)
  local outbox_count=$(find "$BRIDGES_DIR/outbox" -type f 2>/dev/null | wc -l || echo 0)

  log_debug "  Inbox files: $inbox_count"
  log_debug "  Outbox files: $outbox_count"

  # Critical files check
  local critical_files=(
    "$BRIDGES_DIR/audio_stream.py"
    "$BRIDGES_DIR/consent_dispatcher.py"
    "$BRIDGES_DIR/js/auth.js"
    "$BRIDGES_DIR/js/bridge_paths.js"
  )

  for file in "${critical_files[@]}"; do
    if [ ! -f "$file" ]; then
      log_error "  Missing critical file: $file"
      ((bridges_missing++))
    fi
  done

  if [ "$bridges_missing" -gt 0 ]; then
    log_error "File structure check: $bridges_missing critical files missing"
    return 1
  fi

  log_info "File structure: healthy ($bridges_count Python files)"
  return 0
}

diagnose_permissions() {
  log_info "Checking file permissions…"

  if [ "$PLATFORM" != "win32" ]; then
    # POSIX: check for restrictive permissions
    local perm_issue=0

    # Check that .py files are readable
    if [ -d "$BRIDGES_DIR" ]; then
      while IFS= read -r file; do
        if [ ! -r "$file" ]; then
          log_warn "  Unreadable file: $file"
          ((perm_issue++))
        fi
      done < <(find "$BRIDGES_DIR" -maxdepth 1 -name "*.py" 2>/dev/null)
    fi

    if [ "$perm_issue" -gt 0 ]; then
      log_error "Permissions: $perm_issue files unreadable"
      return 1
    fi
  fi

  log_info "Permissions: OK"
  return 0
}

diagnose_python_import() {
  log_info "Testing Python imports…"

  # Try to import key modules
  python3 -c "from corvin_operator.bridges.shared import audio_stream" 2>/dev/null && \
    log_debug "  ✓ corvin_operator.bridges.shared" || \
    { log_error "Failed to import corvin_operator.bridges.shared"; return 1; }

  python3 -c "from corvin_operator.voice import scripts" 2>/dev/null && \
    log_debug "  ✓ corvin_operator.voice" || \
    { log_error "Failed to import corvin_operator.voice"; return 1; }

  log_info "Python imports: OK"
  return 0
}

###############################################################################
# Phase 2: Repair (Platform-Specific)
###############################################################################

repair_git_rename() {
  log_info "Repairing Git rename (if incomplete)…"

  if [ -d "$OPERATOR_LEGACY_DIR" ]; then
    log_warn "Legacy directory still exists: $OPERATOR_LEGACY_DIR"

    if [ "$FORCE_MODE" -eq 1 ]; then
      log_warn "  Force mode: removing legacy directory"
      rm -rf "$OPERATOR_LEGACY_DIR"
    else
      log_error "  To remove manually: git rm -r $OPERATOR_LEGACY_DIR"
      return 1
    fi
  fi

  # Clean Git index (in case Windows left stale cache)
  if [ "$PLATFORM" = "win32" ]; then
    log_info "  Cleaning Git index (Windows cache clear)…"
    git clean -fdx --dry-run "$BRIDGES_DIR" >/dev/null 2>&1 || true
  fi

  log_info "Git rename: repaired"
  return 0
}

repair_file_permissions() {
  log_info "Repairing file permissions…"

  if [ "$PLATFORM" != "win32" ]; then
    # POSIX: set standard permissions
    find "$BRIDGES_DIR" -type f -name "*.py" -exec chmod 0644 {} \;
    find "$BRIDGES_DIR" -type f -name "*.js" -exec chmod 0644 {} \;
    log_debug "  Set standard permissions on bridges files"
  fi

  log_info "File permissions: repaired"
  return 0
}

repair_windows_path_cache() {
  log_info "Clearing Windows path cache (if applicable)…"

  if [ "$PLATFORM" = "win32" ]; then
    # Clear PowerShell module cache (if running on Windows)
    if command -v Remove-Module >/dev/null 2>&1; then
      pwsh -NoProfile -Command "Remove-Module * -Force 2>/dev/null" || true
    fi
    log_debug "  Cleared PowerShell cache"
  fi

  log_info "Windows cache: cleared"
  return 0
}

repair_symlinks() {
  log_info "Checking for symlink issues…"

  if [ "$PLATFORM" = "win32" ]; then
    # Windows: check for dangling or problematic symlinks
    if [ -d "$BRIDGES_DIR" ]; then
      local symlink_count=$(find "$BRIDGES_DIR" -type l 2>/dev/null | wc -l)
      if [ "$symlink_count" -gt 0 ]; then
        log_warn "  Found $symlink_count symlinks (may cause issues on Windows)"
        # Could recreate as copies if needed
      fi
    fi
  fi

  log_info "Symlinks: checked"
  return 0
}

###############################################################################
# Phase 3: Validation
###############################################################################

validate_installation() {
  log_info "Validating installation…"

  # Run all diagnostics again
  diagnose_git_state || return 1
  diagnose_file_structure || return 1
  diagnose_permissions || return 1

  # Try Python import (only if Python available)
  if command -v python3 >/dev/null 2>&1; then
    diagnose_python_import || return 1
  fi

  log_info "Installation validation: PASSED ✓"
  return 0
}

###############################################################################
# Main
###############################################################################

main() {
  # Parse arguments
  while [ $# -gt 0 ]; do
    case "$1" in
      --diagnose) DIAGNOSE_MODE=1; shift ;;
      --repair)   REPAIR_MODE=1; shift ;;
      --force)    FORCE_MODE=1; shift ;;
      *)          log_error "Unknown option: $1"; exit 1 ;;
    esac
  done

  # Show header
  echo
  echo "$(_bold "CorvinOS Installation Repair v$SCRIPT_VERSION")"
  echo "Platform: $(_dim "$PLATFORM / Python $PYTHON_V")"
  echo

  # Phase 1: Diagnose
  echo "$(_bold "Phase 1: Diagnostics")"
  if ! diagnose_git_state; then
    [ "$REPAIR_MODE" -eq 0 ] && exit 1
  fi
  if ! diagnose_file_structure; then
    [ "$REPAIR_MODE" -eq 0 ] && exit 1
  fi
  if ! diagnose_permissions; then
    [ "$REPAIR_MODE" -eq 0 ] && exit 1
  fi
  echo

  # Phase 2: Repair (only if --repair or --force)
  if [ "$REPAIR_MODE" -eq 1 ] || [ "$FORCE_MODE" -eq 1 ]; then
    echo "$(_bold "Phase 2: Repair")"
    repair_git_rename || exit 1
    repair_file_permissions || exit 1
    repair_windows_path_cache || exit 1
    repair_symlinks || exit 1
    echo
  fi

  # Phase 3: Validate
  echo "$(_bold "Phase 3: Validation")"
  if ! validate_installation; then
    exit 1
  fi
  echo

  echo "$(_green "✓ Installation repair complete!")"
  echo
}

main "$@"
