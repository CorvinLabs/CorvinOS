#!/bin/bash
# Remove plugin sources from CorvinOS (now in Corvin-Marketplace repo)

set -e

echo "🧹 Cleaning up CorvinOS plugin sources..."
echo ""

# 1. Remove plugin provider implementations (keep framework)
echo "1️⃣ Removing plugin provider implementations..."
PROVIDERS_DIR="core/plugins/corvin_plugins/providers"

if [ -d "$PROVIDERS_DIR" ]; then
    # List files to remove
    PROVIDER_FILES=(
        "audit_backend.py"
        "data_connector.py"
        "notification_backend.py"
        "recall_backend.py"
        "router_backend.py"
        "stt_provider.py"
        "summary_provider.py"
        "user_backend.py"
    )

    for file in "${PROVIDER_FILES[@]}"; do
        if [ -f "$PROVIDERS_DIR/$file" ]; then
            rm -v "$PROVIDERS_DIR/$file" || echo "  ⚠️ Failed to remove $file"
        fi
    done

    # Remove __pycache__
    [ -d "$PROVIDERS_DIR/__pycache__" ] && rm -rf "$PROVIDERS_DIR/__pycache__"

    echo "   ✅ Provider implementations removed"
fi

# 2. Remove marketplace folder (entirely in Marketplace repo now)
echo ""
echo "2️⃣ Removing operator/marketplace folder..."
if [ -d "operator/marketplace" ]; then
    rm -rf "operator/marketplace"
    echo "   ✅ Removed operator/marketplace (584 KB, 41 files)"
fi

# 3. Check that plugin framework still exists
echo ""
echo "3️⃣ Verifying plugin framework remains..."
FRAMEWORK_FILES=(
    "core/plugins/corvin_plugins/registry.py"
    "core/plugins/corvin_plugins/loader.py"
    "core/plugins/corvin_plugins/manifest.py"
)

MISSING=0
for file in "${FRAMEWORK_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "   ✅ $file"
    else
        echo "   ❌ MISSING: $file (framework broken!)"
        MISSING=$((MISSING + 1))
    fi
done

if [ $MISSING -gt 0 ]; then
    echo ""
    echo "❌ Framework validation failed! $MISSING files missing."
    exit 1
fi

echo ""
echo "✅ Cleanup complete"
echo ""
echo "Summary:"
echo "  - Removed 8 plugin provider implementations"
echo "  - Removed operator/marketplace (now in Corvin-Marketplace repo)"
echo "  - Plugin framework intact"
echo ""
echo "CorvinOS repo is now slim:"
echo "  - Core OS code only"
echo "  - Plugin framework"
echo "  - Console"
echo ""
echo "All plugins live in: https://github.com/CorvinLabs/Corvin-Marketplace"
