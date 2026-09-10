#!/bin/bash
# ADR-XXXX: Console Frontend Change Verification
#
# Atomic build + verification for console frontend changes.
# Ensures new code is actually bundled and served, not cached.
#
# USAGE:
#   ./scripts/console-verify-frontend-change.sh --marker "NewPanelName" --location "src/pages/new-panel.tsx"
#
# EXIT CODES:
#   0 = Success: new code bundled and served, ready for browser hard-refresh
#   1 = Failure: build incomplete or caches not cleared
#   2 = Usage error

set -euo pipefail

# Defaults
MARKER=""
LOCATION=""
CONSOLE_DIR="core/console/corvin_console/web-next"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8765}"
VERBOSE=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --marker)
            MARKER="$2"
            shift 2
            ;;
        --location)
            LOCATION="$2"
            shift 2
            ;;
        --backend-url)
            BACKEND_URL="$2"
            shift 2
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 --marker <string> --location <path> [--backend-url <url>] [--verbose]"
            exit 2
            ;;
    esac
done

if [[ -z "$MARKER" ]]; then
    log_fail "No marker string provided. Use --marker 'UniqueString'"
    exit 2
fi

if [[ -z "$LOCATION" ]]; then
    log_fail "No source location provided. Use --location 'src/pages/example.tsx'"
    exit 2
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Console Frontend Change Verification"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Marker:          $MARKER"
echo "Location:        $LOCATION"
echo "Console Dir:     $CONSOLE_DIR"
echo "Backend URL:     $BACKEND_URL"
echo ""

# ============================================================================
# STEP 1: Verify source file contains marker
# ============================================================================

log_step "1. Verify marker exists in source file..."

if [[ ! -f "$LOCATION" ]]; then
    log_fail "Source file not found: $LOCATION"
    exit 1
fi

if ! grep -q "$MARKER" "$LOCATION"; then
    log_fail "Marker '$MARKER' not found in $LOCATION"
    echo "Expected to find: $MARKER"
    echo "File content excerpt:"
    head -20 "$LOCATION" | sed 's/^/  /'
    exit 1
fi

log_pass "Marker found in source file"

# ============================================================================
# STEP 2: Atomic build (clear both caches)
# ============================================================================

log_step "2. Clearing build caches (atomic)..."

cd "$CONSOLE_DIR"

if [[ -d "dist" ]]; then
    log_warn "Deleting dist/"
    rm -rf dist/ || {
        log_fail "Failed to delete dist/"
        exit 1
    }
else
    log_warn "dist/ already missing"
fi

if [[ -d "node_modules/.vite" ]]; then
    log_warn "Deleting node_modules/.vite/"
    rm -rf node_modules/.vite/ || {
        log_fail "Failed to delete node_modules/.vite/"
        exit 1
    }
else
    log_warn "node_modules/.vite/ already missing"
fi

log_pass "Caches cleared"

# ============================================================================
# STEP 3: Build
# ============================================================================

log_step "3. Building..."

if ! npm run build > /tmp/console_build.log 2>&1; then
    log_fail "Build failed"
    tail -50 /tmp/console_build.log
    exit 1
fi

log_pass "Build succeeded"

# ============================================================================
# STEP 4: Verify marker in bundled assets
# ============================================================================

log_step "4. Verifying marker in bundled assets..."

MARKER_FOUND=false
for asset in dist/assets/*.js; do
    if [[ -f "$asset" ]] && grep -q "$MARKER" "$asset"; then
        MARKER_FOUND=true
        log_pass "Marker found in bundled asset: $(basename "$asset")"
        ASSET_NAME=$(basename "$asset")
        break
    fi
done

if [[ "$MARKER_FOUND" == "false" ]]; then
    log_fail "Marker '$MARKER' NOT found in any bundled asset"
    echo ""
    echo "Bundled assets:"
    ls -lh dist/assets/*.js 2>/dev/null | sed 's/^/  /'
    echo ""
    echo "This suggests the build succeeded but the new source was never bundled."
    echo "Possible causes:"
    echo "  1. Source file was not modified (no changes detected)"
    echo "  2. Bundler issue (esbuild, vite)"
    echo "  3. Build output different from expected"
    exit 1
fi

# ============================================================================
# STEP 5: Verify backend is serving new hashes
# ============================================================================

log_step "5. Verifying backend serves new bundle hashes..."

DEPLOYED_HASH=$(curl -s \
    -H "Cache-Control: no-cache" \
    --url "$BACKEND_URL/console/" \
    | grep -oP 'assets/index-[A-Za-z0-9_-]+\.js' \
    | head -1 \
    || echo "")

if [[ -z "$DEPLOYED_HASH" ]]; then
    log_fail "Could not extract bundle hash from backend"
    echo "Backend URL: $BACKEND_URL/console/"
    echo "Response:"
    curl -s -H "Cache-Control: no-cache" --url "$BACKEND_URL/console/" | head -20
    exit 1
fi

log_pass "Backend serving bundle: $DEPLOYED_HASH"

# ============================================================================
# STEP 6: Verify deployed hash matches built hash
# ============================================================================

log_step "6. Verifying deployed hash matches built bundle..."

cd "$CONSOLE_DIR"

BUILT_HASH=$(ls -1 dist/assets/index-*.js | grep -oP 'index-[A-Za-z0-9_-]+\.js' | head -1)

if [[ "$BUILT_HASH" != "$DEPLOYED_HASH" ]]; then
    log_warn "Hash mismatch!"
    echo "Built:     $BUILT_HASH"
    echo "Deployed:  $DEPLOYED_HASH"
    echo ""
    echo "This usually means the backend wasn't restarted after the build."
    echo "Restarting backend..."

    # Try to restart backend (optional, may not always work)
    if command -v systemctl &> /dev/null; then
        systemctl --user restart corvin-console.service 2>/dev/null || {
            log_warn "Could not restart backend via systemctl"
        }
    fi

    # Re-check
    sleep 1
    DEPLOYED_HASH_NEW=$(curl -s \
        -H "Cache-Control: no-cache" \
        --url "$BACKEND_URL/console/" \
        | grep -oP 'assets/index-[A-Za-z0-9_-]+\.js' \
        | head -1 \
        || echo "")

    if [[ "$BUILT_HASH" == "$DEPLOYED_HASH_NEW" ]]; then
        log_pass "Hash now matches after backend restart"
        DEPLOYED_HASH="$DEPLOYED_HASH_NEW"
    else
        log_fail "Hash still doesn't match after restart"
        echo "Built:     $BUILT_HASH"
        echo "Deployed:  $DEPLOYED_HASH_NEW"
        echo ""
        echo "Manual fix: restart the backend and re-run this script"
        exit 1
    fi
fi

log_pass "Deployed hash matches built bundle"

# ============================================================================
# STEP 7: Final summary
# ============================================================================

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✅ VERIFICATION PASSED"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Summary:"
echo "  ✓ Marker '$MARKER' found in source"
echo "  ✓ Caches cleared (dist/, .vite/)"
echo "  ✓ Build completed successfully"
echo "  ✓ Marker found in bundled assets"
echo "  ✓ Backend serving new bundle"
echo "  ✓ Bundle hash verified"
echo ""
echo "New bundle hash: $DEPLOYED_HASH"
echo ""
echo "Next step: Hard-refresh your browser (Ctrl+Shift+R) to load the new code"
echo ""

exit 0
