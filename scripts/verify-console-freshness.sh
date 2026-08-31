#!/bin/bash
# Console Cache Freshness Verification Script
# Purpose: Ensure Console frontend changes are reflected (all 3 cache layers)
# Usage: bash scripts/verify-console-freshness.sh <code-marker>
# Example: bash scripts/verify-console-freshness.sh "skill_forge_enabled"

set -e

CONSOLE_DIR="core/console/corvin_console/web-next"
MARKER="${1:-}"

if [ -z "$MARKER" ]; then
    echo "Usage: $0 <search-marker>"
    echo ""
    echo "Find a unique string from your code change and pass it:"
    echo "  Example: $0 'skill_forge_enabled'"
    echo "  Example: $0 'NewComponentName'"
    exit 1
fi

if [ ! -d "$CONSOLE_DIR" ]; then
    echo "❌ Console directory not found: $CONSOLE_DIR"
    exit 1
fi

cd "$CONSOLE_DIR"

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║       Console Cache Freshness Verification (All 3 Layers)      ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "Marker: '$MARKER'"
echo ""

# Step 1: Clear Layer 1 (esbuild pre-bundle cache)
echo "1️⃣  Layer 1 — Clearing esbuild pre-bundle cache..."
if [ -d "node_modules/.vite/" ]; then
    rm -rf node_modules/.vite/
    echo "   ✓ Cleared node_modules/.vite/"
else
    echo "   ℹ️  node_modules/.vite/ not present (already clean)"
fi
echo ""

# Step 2: Clear Layer 2 (build artifacts)
echo "2️⃣  Layer 2 — Clearing build artifacts..."
if [ -d "dist/" ]; then
    rm -rf dist/
    echo "   ✓ Cleared dist/"
else
    echo "   ℹ️  dist/ not present (already clean)"
fi
echo ""

# Step 3: Rebuild from clean slate
echo "3️⃣  Rebuilding from clean slate..."
if npm run build > /tmp/console-build.log 2>&1; then
    echo "   ✓ Build succeeded"
else
    echo "   ❌ Build failed!"
    echo ""
    echo "Build log:"
    tail -20 /tmp/console-build.log
    exit 1
fi
echo ""

# Step 4: Verify new asset hashes
echo "4️⃣  Verifying new asset hashes..."
if [ ! -f "dist/index.html" ]; then
    echo "   ❌ dist/index.html not found after build!"
    exit 1
fi

NEW_HASHES=$(grep -o 'assets/[^"]*\.js' dist/index.html | head -3)
if [ -z "$NEW_HASHES" ]; then
    echo "   ⚠️  No .js assets found in index.html"
else
    echo "   Asset hashes (first 3):"
    echo "$NEW_HASHES" | sed 's/^/     /'
fi
echo ""

# Step 5: Search for code marker in bundled assets
echo "5️⃣  Searching for code marker in bundled assets..."
echo "   Looking for: '$MARKER'"
if grep -r "$MARKER" dist/assets/ > /dev/null 2>&1; then
    echo "   ✓ FOUND — new code is in dist/assets/"
    echo ""

    # Show which asset file contains the marker
    FOUND_IN=$(grep -l "$MARKER" dist/assets/* 2>/dev/null | head -1)
    if [ -n "$FOUND_IN" ]; then
        ASSET_NAME=$(basename "$FOUND_IN")
        echo "   Found in: $ASSET_NAME"
    fi
    echo ""

    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║                    ✅ CONSOLE IS FRESH                         ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Layers 1–2 cleared and verified. Layer 3 requires BROWSER hard-refresh:"
    echo ""
    echo "  Chrome/Edge (Windows):  Ctrl+Shift+R"
    echo "  Chrome/Edge (Mac):      Cmd+Shift+R"
    echo "  Firefox (Windows):      Ctrl+F5"
    echo "  Firefox (Mac):          Cmd+Shift+R"
    echo "  Safari (Mac):           Cmd+Option+R"
    echo ""
    echo "⚠️  IMPORTANT: Use HARD-refresh, not plain refresh (Ctrl+R / Cmd+R)"
    echo ""
    exit 0
else
    echo "   ❌ NOT FOUND — stale code still in dist/"
    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║                    🔴 CONSOLE IS STALE                         ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Did you save the file before running this script?"
    echo "  2. Is the marker string correct? (exact case-sensitive match)"
    echo "  3. Is the marker in the .tsx/.ts/.jsx/.js file (not in comments)?"
    echo ""
    echo "Debugging: grep -r '$MARKER' dist/assets/"
    echo ""
    exit 1
fi
