#!/usr/bin/env python3
"""
Manual test suite for ADR-0511 Production Readiness.
Validates: Schema, Index Generation, Plugin Count, API Routes.
"""

import json
from pathlib import Path
import sys

def test_index_generation():
    """✅ Test 1: Index file exists and is valid JSON."""
    index_path = Path("operator/marketplace/index/plugins.json")
    assert index_path.exists(), f"Index not found: {index_path}"

    with open(index_path) as f:
        index = json.load(f)

    assert index["plugin_count"] >= 25, f"Plugin count {index['plugin_count']} < 25"
    assert "by_category" in index, "Missing by_category"
    assert "by_tier" in index, "Missing by_tier"

    print(f"✅ Index Valid: {index['plugin_count']} plugins, {len(index['by_category'])} categories")
    return True

def test_plugin_count():
    """✅ Test 2: All expected plugins present."""
    index_path = Path("operator/marketplace/index/plugins.json")
    with open(index_path) as f:
        index = json.load(f)

    assert index["plugin_count"] == 27, f"Expected 27, got {index['plugin_count']}"
    print(f"✅ Plugin Count: 27/27")
    return True

def test_categories():
    """✅ Test 3: All 5 categories present."""
    index_path = Path("operator/marketplace/index/plugins.json")
    with open(index_path) as f:
        index = json.load(f)

    expected_cats = {"memory", "security_compliance", "integration", "data_processing", "observability"}
    actual_cats = set(index["by_category"].keys())
    assert expected_cats == actual_cats, f"Category mismatch. Expected {expected_cats}, got {actual_cats}"

    for cat, plugins in index["by_category"].items():
        print(f"  • {cat}: {len(plugins)} plugins")

    print(f"✅ Categories: All 5 present")
    return True

def test_schema_compliance():
    """✅ Test 4: Sample plugins conform to schema."""
    index_path = Path("operator/marketplace/index/plugins.json")
    with open(index_path) as f:
        index = json.load(f)

    plugins = index.get("plugins", [])
    assert len(plugins) > 0, "No plugins in index"

    # Check first plugin
    p = plugins[0]
    required_fields = ["id", "type", "name", "version", "author", "license", "tier", "category", "description"]
    for field in required_fields:
        assert field in p, f"Missing field '{field}' in plugin {p.get('id')}"

    # Validate tier
    assert p["tier"] in ["buildin", "contributor"], f"Invalid tier: {p['tier']}"

    # Validate category
    valid_cats = {"memory", "security_compliance", "integration", "data_processing", "observability"}
    assert p["category"] in valid_cats, f"Invalid category: {p['category']}"

    print(f"✅ Schema Compliance: Sample plugin '{p['name']}' valid")
    return True

def test_marketplace_py_routes():
    """✅ Test 5: marketplace.py has ADR-0511 routes only."""
    mp_path = Path("core/console/corvin_console/routes/marketplace.py")
    with open(mp_path) as f:
        content = f.read()

    # Expected routes
    expected_routes = [
        "@router.get(\"/plugins\")",
        "@router.get(\"/plugins/{plugin_id}\")",
        "@router.get(\"/stats\")",
        "@router.post(\"/reload\")",
    ]

    for route in expected_routes:
        assert route in content, f"Missing route: {route}"

    # Should NOT have legacy endpoints
    legacy_patterns = [
        "def marketplace_search",
        "def marketplace_install",
        "def marketplace_uninstall",
        "PluginMarketplace()",
    ]

    for pattern in legacy_patterns:
        assert pattern not in content, f"Legacy code still present: {pattern}"

    lines = len(content.split("\n"))
    assert lines < 200, f"marketplace.py too large: {lines} lines (expected <200)"

    print(f"✅ marketplace.py Routes: Only ADR-0511 routes ({lines} lines)")
    return True

def test_plugin_manifests():
    """✅ Test 6: All plugin.json files valid."""
    plugins_dir = Path("operator/marketplace/plugins/buildin")
    plugin_files = list(plugins_dir.rglob("plugin.json"))

    assert len(plugin_files) == 27, f"Expected 27 plugin.json files, found {len(plugin_files)}"

    for pfile in plugin_files:
        with open(pfile) as f:
            plugin = json.load(f)

        # Minimal validation
        assert "id" in plugin, f"Missing 'id' in {pfile}"
        assert "category" in plugin, f"Missing 'category' in {pfile}"
        assert plugin["tier"] == "buildin", f"Wrong tier in {pfile}"

    print(f"✅ Plugin Manifests: All 27 valid")
    return True

def main():
    """Run all tests."""
    print("=" * 60)
    print("ADR-0511 PRODUCTION READINESS TEST SUITE")
    print("=" * 60)

    tests = [
        ("Index Generation", test_index_generation),
        ("Plugin Count", test_plugin_count),
        ("Categories", test_categories),
        ("Schema Compliance", test_schema_compliance),
        ("marketplace.py Routes", test_marketplace_py_routes),
        ("Plugin Manifests", test_plugin_manifests),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test_name}: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {test_name}: {type(e).__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed == 0:
        print("\n🎉 ALL TESTS PASSED — PRODUCTION READY")
        return 0
    else:
        print(f"\n⚠️  {failed} TEST(S) FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
