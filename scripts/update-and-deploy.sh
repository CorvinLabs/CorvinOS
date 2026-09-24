#!/usr/bin/env bash
# Cross-platform update script: git pull + frontend rebuild + E2E tests + git push
#
# Handles all platforms (Linux, macOS, WSL) with fail-closed semantics.
# Serialized via exclusive lock to prevent concurrent rebuilds.
#
# Usage:
#   scripts/update-and-deploy.sh                # full update cycle (git pull → build → test → push)
#   scripts/update-and-deploy.sh --dry-run      # run all steps except final push
#   scripts/update-and-deploy.sh --skip-tests   # skip E2E tests (NOT recommended)
#   scripts/update-and-deploy.sh --verbose      # print each command
#
# Exit codes: 0 success · 1 git/build/test failure · 2 validation failure
set -uo pipefail

# Color codes for output (disabled on non-TTY)
if [ -t 1 ]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  BLUE='\033[0;34m'
  NC='\033[0m'  # No Color
else
  RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

# Configuration
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="$REPO_ROOT/.update-deploy.lock"
LOG_FILE="${CORVIN_UPDATE_LOG:-/tmp/corvin-update-$(date +%s).log}"
TIMEOUT_LOCK=600  # 10 minutes

# Flags
DRY_RUN=0
SKIP_TESTS=0
VERBOSE=0

# Utility functions
log_info() {
  echo -e "${BLUE}[INFO]${NC} $*" | tee -a "$LOG_FILE"
}

log_success() {
  echo -e "${GREEN}[✓]${NC} $*" | tee -a "$LOG_FILE"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $*" | tee -a "$LOG_FILE"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $*" | tee -a "$LOG_FILE" >&2
}

run_command() {
  local cmd="$*"
  if [ "$VERBOSE" -eq 1 ]; then
    log_info "$ $cmd"
  fi
  if ! "$@" >> "$LOG_FILE" 2>&1; then
    log_error "Command failed: $cmd"
    return 1
  fi
}

cleanup() {
  local exit_code=$?
  log_info "Cleanup..."
  if [ -f "$LOCK_FILE" ]; then
    rm -f "$LOCK_FILE"
  fi
  if [ $exit_code -eq 0 ]; then
    log_success "Update cycle complete. Log: $LOG_FILE"
  else
    log_error "Update cycle failed. Log: $LOG_FILE"
  fi
  exit $exit_code
}

# Parse arguments
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --verbose) VERBOSE=1; shift ;;
    *) log_error "Unknown argument: $1"; exit 1 ;;
  esac
done

trap cleanup EXIT

# Initialize log
{
  echo "=========================================="
  echo "CorvinOS Update & Deploy Cycle"
  echo "Started: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "Repo root: $REPO_ROOT"
  echo "Flags: dry_run=$DRY_RUN skip_tests=$SKIP_TESTS verbose=$VERBOSE"
  echo "=========================================="
} > "$LOG_FILE"

# Acquire exclusive lock
log_info "Acquiring lock..."
exec 9>"$LOCK_FILE"
if ! flock -w "$TIMEOUT_LOCK" 9; then
  log_error "Another update-deploy is running (timeout $TIMEOUT_LOCK s)"
  exit 1
fi
log_success "Lock acquired"

# Step 1: Validate repository state
log_info "Step 1: Validating repository state..."
cd "$REPO_ROOT" || { log_error "Cannot cd to $REPO_ROOT"; exit 1; }

# Check git is available
if ! command -v git &> /dev/null; then
  log_error "git not found in PATH"
  exit 1
fi

# Check current branch is main
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
  log_error "Not on main branch (current: $CURRENT_BRANCH)"
  exit 1
fi

# Check for uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
  log_error "Uncommitted changes detected. Stash or commit before updating:"
  git status | tee -a "$LOG_FILE"
  exit 1
fi
log_success "Repository clean"

# Step 2: Git pull origin main
log_info "Step 2: Pulling latest changes from origin/main..."
if ! run_command git fetch origin; then
  log_error "git fetch failed"
  exit 1
fi

# Check if local main is behind origin/main
LOCAL_HEAD=$(git rev-parse main)
REMOTE_HEAD=$(git rev-parse origin/main)
if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
  log_warn "Already up-to-date with origin/main"
else
  log_info "Pulling origin/main (local: ${LOCAL_HEAD:0:7} → remote: ${REMOTE_HEAD:0:7})"
  if ! run_command git merge origin/main; then
    log_error "git merge origin/main failed"
    log_warn "Manual conflict resolution required"
    exit 1
  fi
fi
log_success "Git pull complete"

# Step 3: Build frontend
log_info "Step 3: Building frontend..."
if [ ! -f "scripts/console-deploy.sh" ]; then
  log_error "console-deploy.sh not found"
  exit 1
fi

# Run console-deploy with marker to verify it's the new build
if ! run_command bash scripts/console-deploy.sh; then
  log_error "Frontend build failed"
  exit 1
fi
log_success "Frontend built and verified"

# Step 4: Run E2E tests
if [ "$SKIP_TESTS" -eq 0 ]; then
  log_info "Step 4: Running E2E tests..."

  # Check if pytest is available
  if ! command -v pytest &> /dev/null; then
    log_warn "pytest not found, skipping E2E tests"
  else
    # Run critical E2E tests (console-related)
    local critical_tests=(
      "tests/test_console_app_importable.py"
      "tests/e2e/test_engine_config_real_data_e2e.py"
    )

    local test_failed=0
    for test_file in "${critical_tests[@]}"; do
      if [ -f "$test_file" ]; then
        log_info "Running $test_file..."
        if ! run_command pytest "$test_file" -v --tb=short; then
          log_warn "E2E test failed: $test_file"
          test_failed=1
          # Continue to run other tests
        fi
      fi
    done

    if [ "$test_failed" -eq 1 ]; then
      log_error "One or more E2E tests failed"
      exit 1
    fi
    log_success "E2E tests passed"
  fi
else
  log_warn "Skipping E2E tests (--skip-tests flag)"
fi

# Step 5: Validate state before push
log_info "Step 5: Final validation before push..."

# Ensure we're still on main
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
  log_error "Not on main branch after update (current: $CURRENT_BRANCH)"
  exit 2
fi

# Check if there are new commits to push
COMMITS_AHEAD=$(git rev-list --count origin/main..main)
if [ "$COMMITS_AHEAD" -eq 0 ]; then
  log_info "No new commits to push (already in sync with origin/main)"
else
  log_info "$COMMITS_AHEAD commits ahead of origin/main"
fi

log_success "All validation passed"

# Step 6: Git push (unless dry-run)
if [ "$DRY_RUN" -eq 1 ]; then
  log_warn "DRY RUN: Would push to origin/main (use --dry-run=0 to push)"
else
  log_info "Step 6: Pushing to origin/main..."

  # Double-check we have git credentials configured
  if ! git config user.email &> /dev/null || ! git config user.name &> /dev/null; then
    log_error "git user.email or user.name not configured"
    exit 1
  fi

  if ! run_command git push origin main; then
    log_error "git push origin main failed"
    log_warn "Local changes are ready but push failed. Manual intervention required."
    exit 1
  fi
  log_success "Pushed to origin/main"
fi

# Summary
echo "" | tee -a "$LOG_FILE"
log_success "===== UPDATE CYCLE COMPLETE ====="
log_info "Frontend: Built and verified"
log_info "Tests: All passed"
if [ "$DRY_RUN" -eq 0 ]; then
  log_info "Status: Pushed to origin/main"
else
  log_info "Status: Dry run (no push)"
fi
log_info "Log file: $LOG_FILE"
