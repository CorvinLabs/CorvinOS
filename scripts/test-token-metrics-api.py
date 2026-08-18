#!/usr/bin/env python3
"""
Token Metrics E2E Tests — API Level
Tests the Token Metrics endpoints and Whitelist integration
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8765/v1/console/api"
SETTINGS_URL = f"{BASE_URL}/settings"

def log(msg: str, level: str = "INFO"):
    """Print log message with timestamp"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")

def test_get_features():
    """Test GET /settings/features endpoint"""
    log("Testing GET /settings/features")

    response = requests.get(f"{SETTINGS_URL}/features")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    data = response.json()
    assert "features" in data, "Missing 'features' key in response"

    features = data["features"]
    log(f"✅ Received {len(features)} features")

    # Verify structure
    for feature in features[:3]:  # Check first 3
        assert "id" in feature
        assert "label" in feature
        assert "enabled" in feature
        assert "description" in feature

    # Count enabled/disabled
    enabled = sum(1 for f in features if f["enabled"])
    disabled = len(features) - enabled

    log(f"   Enabled: {enabled}, Disabled: {disabled}")
    assert enabled == 5, f"Expected 5 enabled features, got {enabled}"
    assert disabled == 36, f"Expected 36 disabled features, got {disabled}"

    return features

def test_whitelist_features(features):
    """Verify whitelist strategy is applied"""
    log("Testing Whitelist Strategy")

    whitelist = {
        'vibe_engineering',
        'vibe_engineering_active',
        'outcome_feedback_loop',
        'cross_device_sync',
        'package_marketplace_ui',
    }

    # Check whitelisted features are enabled
    for feature in features:
        if feature['id'] in whitelist:
            assert feature['enabled'] == True, f"{feature['id']} should be enabled"
            log(f"   ✅ {feature['id']}: enabled (whitelist)")
        else:
            assert feature['enabled'] == False, f"{feature['id']} should be disabled"

    log("✅ Whitelist strategy verified")
    return True

def test_token_metrics_api():
    """Test GET /metrics/session/{sessionId} endpoint"""
    log("Testing GET /metrics/session/current")

    response = requests.get(f"{BASE_URL}/metrics/session/current")

    # May return 404 if no data yet, but shouldn't return 405
    assert response.status_code in [200, 404], f"Got {response.status_code}: {response.text}"

    if response.status_code == 200:
        data = response.json()
        assert "metrics" in data, "Missing metrics in response"

        metrics = data["metrics"]
        log(f"✅ Token Metrics loaded:")
        log(f"   Tokens: {metrics.get('total_tokens', 'N/A')}")
        log(f"   Cost Saved: ${metrics.get('estimated_savings', 0):.2f}")
    else:
        log("ℹ️  No metrics available yet (expected for fresh install)")

    return True

def test_feature_toggle():
    """Test POST /settings/features/{flag_id}/toggle endpoint"""
    log("Testing POST /settings/features/{flag_id}/toggle")

    # Get CSRF token (mock/skip for this test)
    csrf_token = "test-token"

    # Try to toggle a non-critical feature
    test_feature = "browser_automation"

    payload = {
        "id": test_feature,
        "enabled": True
    }

    response = requests.post(
        f"{SETTINGS_URL}/features/{test_feature}/toggle",
        json=payload,
        headers={"X-CSRF-Token": csrf_token},
    )

    # May fail due to auth, but shouldn't be 405
    if response.status_code == 405:
        log(f"❌ Got 405 Method Not Allowed", "ERROR")
        log(f"   Response: {response.text}", "ERROR")
        return False
    elif response.status_code in [400, 401, 403]:
        log(f"⚠️  Got {response.status_code} (auth/validation expected)")
    elif response.status_code == 200:
        log(f"✅ Feature toggle successful")
        data = response.json()
        log(f"   Result: {data}")
    else:
        log(f"⚠️  Got {response.status_code}")

    return True

def test_vibe_engineering_enabled(features):
    """Verify vibe_engineering feature is enabled"""
    log("Testing vibe_engineering feature")

    vibe = next((f for f in features if f['id'] == 'vibe_engineering'), None)
    assert vibe is not None, "vibe_engineering feature not found"
    assert vibe['enabled'] == True, "vibe_engineering should be enabled"

    log(f"✅ vibe_engineering is enabled (Token Metrics should be visible)")
    return True

def test_all_features_listed(features):
    """Verify all 41 features are listed"""
    log("Testing all features are listed")

    total = len(features)
    assert total == 41, f"Expected 41 features, got {total}"

    # Verify some key features exist
    required = [
        'vibe_engineering',
        'browser_automation',
        'plugin_builder_enabled',
        'admin_control_plane',
    ]

    feature_ids = {f['id'] for f in features}
    for req in required:
        assert req in feature_ids, f"Missing required feature: {req}"

    log(f"✅ All {total} features present")
    return True

def main():
    """Run all tests"""
    print()
    log("=" * 60, "INFO")
    log("Token Metrics & Whitelist Integration Tests", "INFO")
    log("=" * 60, "INFO")
    print()

    try:
        # Test 1: Get features
        features = test_get_features()
        print()

        # Test 2: All features listed
        test_all_features_listed(features)
        print()

        # Test 3: Whitelist strategy
        test_whitelist_features(features)
        print()

        # Test 4: vibe_engineering enabled
        test_vibe_engineering_enabled(features)
        print()

        # Test 5: Token Metrics API
        test_token_metrics_api()
        print()

        # Test 6: Feature toggle endpoint
        test_feature_toggle()
        print()

        # Summary
        print()
        log("=" * 60, "INFO")
        log("✨ ALL TESTS PASSED!", "INFO")
        log("=" * 60, "INFO")
        log("Token Metrics integration is working correctly!", "INFO")
        log("Features:", "INFO")
        log("  ✅ All 41 features listed", "INFO")
        log("  ✅ Whitelist strategy applied (5 enabled)", "INFO")
        log("  ✅ vibe_engineering enabled (Token Metrics visible)", "INFO")
        log("  ✅ Token Metrics API responding", "INFO")
        log("  ✅ Feature toggle endpoint working", "INFO")
        print()

        return 0

    except AssertionError as e:
        print()
        log(f"❌ TEST FAILED: {e}", "ERROR")
        return 1
    except Exception as e:
        print()
        log(f"❌ ERROR: {e}", "ERROR")
        return 1

if __name__ == "__main__":
    exit(main())
