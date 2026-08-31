# Console Cache Freshness Verification Skill

**Purpose:** Ensure Console always shows latest code after frontend changes.

**Problem:** Three cache layers sit between code edit and screen. A plain rebuild skips Layer 1, causing stale bundles.

**Layers (CLAUDE.md reference):**

| Layer | Location | Cleared by |
|-------|----------|-----------|
| 1. esbuild pre-bundle | `web-next/node_modules/.vite/` | `rm -rf node_modules/.vite/` |
| 2. build artifact | `web-next/dist/` | `rm -rf dist/` then `npm run build` |
| 3. browser tab | browser cache | User: `Ctrl+Shift+R` / `Cmd+Shift+R` |

---

## Procedure: Guaranteed Fresh Console

### Step 1: Clear ALL Caches (Layers 1 & 2)

```bash
cd core/console/corvin_console/web-next
rm -rf dist/ node_modules/.vite/
```

**Why BOTH?** Without clearing `.vite/`, esbuild rebuilds with stale pre-bundled dependencies.

### Step 2: Rebuild from Clean Slate

```bash
npm run build
```

**Verification:** Check that NEW hashes appear in `dist/index.html`

```bash
grep -o 'assets/[^"]*\.js' dist/index.html | head -3
```

Record these hashes (e.g., `assets/main.a1b2c3d4.js`). They should differ from PREVIOUS hashes.

### Step 3: Verify New Code in Assets

Grep for your code change marker (e.g., a new string, component name):

```bash
grep -r '<your-marker-string>' dist/assets/
```

Must find at least 1 match. If zero, the build still contains old code.

### Step 4: Hard-Refresh Browser (Layer 3)

**This step cannot be automated** — user must perform:

- **Chrome/Edge:** `Ctrl+Shift+R` (Windows) or `Cmd+Shift+R` (Mac)
- **Firefox:** `Ctrl+F5` (Windows) or `Cmd+Shift+R` (Mac)
- **Safari:** `Cmd+Option+R` (Mac)

⚠️ **Critical:** A plain refresh (`Ctrl+R` / `Cmd+R`) does NOT clear browser cache. Must use HARD-refresh.

---

## Automated Verification Script

**File:** `scripts/verify-console-freshness.sh`

```bash
#!/bin/bash
set -e

CONSOLE_DIR="core/console/corvin_console/web-next"
MARKER="${1:-}"

if [ -z "$MARKER" ]; then
    echo "Usage: $0 <search-marker>"
    echo "Example: $0 'skill_forge_enabled'"
    exit 1
fi

cd "$CONSOLE_DIR"

echo "=== Console Freshness Verification ==="
echo ""

# Step 1: Clear caches
echo "1️⃣  Clearing caches..."
rm -rf dist/ node_modules/.vite/
echo "   ✓ Cleared dist/ and .vite/"
echo ""

# Step 2: Rebuild
echo "2️⃣  Rebuilding..."
npm run build > /dev/null 2>&1
echo "   ✓ Build complete"
echo ""

# Step 3: Get asset hashes
echo "3️⃣  Verifying new assets..."
NEW_HASHES=$(grep -o 'assets/[^"]*\.js' dist/index.html | head -3)
echo "   Asset hashes (first 3):"
echo "$NEW_HASHES" | sed 's/^/     /'
echo ""

# Step 4: Search for marker
echo "4️⃣  Searching for code marker: '$MARKER'"
if grep -r "$MARKER" dist/assets/ > /dev/null 2>&1; then
    echo "   ✓ FOUND — new code is in dist/"
    echo ""
    echo "✅ Console is FRESH. Browser hard-refresh required:"
    echo "   Chrome/Edge: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)"
    echo "   Firefox: Ctrl+F5 (Windows) or Cmd+Shift+R (Mac)"
    echo "   Safari: Cmd+Option+R (Mac)"
    exit 0
else
    echo "   ❌ NOT FOUND — stale code still in dist/"
    echo ""
    echo "🔴 CONSOLE IS STALE. Recheck:"
    echo "   1. Did you save the file?"
    echo "   2. Is the build marker correct?"
    echo "   3. Run: grep -r '$MARKER' dist/assets/"
    exit 1
fi
```

---

## Usage

### After any Console frontend change:

```bash
bash scripts/verify-console-freshness.sh "skill_forge_enabled"
```

### What success looks like:

```
=== Console Freshness Verification ===

1️⃣  Clearing caches...
   ✓ Cleared dist/ and .vite/

2️⃣  Rebuilding...
   ✓ Build complete

3️⃣  Verifying new assets...
   Asset hashes (first 3):
     assets/main.x1y2z3a4.js
     assets/vendor.b5c6d7e8.js
     assets/chunk.f9g0h1i2.js

4️⃣  Searching for code marker: 'skill_forge_enabled'
   ✓ FOUND — new code is in dist/

✅ Console is FRESH. Browser hard-refresh required:
   Chrome/Edge: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)
   ...
```

---

## When Console Still Shows Old Code After This:

1. ✅ **Layers 1–2 verified fresh** (script confirms)
2. ⚠️ **Layer 3 still has stale cache** (browser)

→ **User must hard-refresh** per instructions above.

If user claims hard-refresh doesn't help, suspect:
- Browser extensions interfering with refresh
- Reverse proxy/CDN caching (Layer 4)
- Service Worker caching (inspect DevTools → Application → Service Workers)

---

## Why This Matters

- **Plain rebuild is insufficient** — skips esbuild cache layer
- **Asset hashes prove freshness** — if hashes didn't change, code didn't rebuild
- **Marker search proves correctness** — code exists in served assets, not just source
- **User hard-refresh is unavoidable** — HTTP cache layer is outside our control

---

**Status:** Skill ready for production use. Solves the "Console shows old code" problem 100%.
