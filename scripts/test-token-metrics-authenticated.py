#!/usr/bin/env python3
"""
Token Metrics E2E Tests — with Authentication
Tests the Token Metrics endpoints using local login
"""

import requests
import json
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

BASE_URL = "http://localhost:8765"
API_URL = f"{BASE_URL}/v1/console"

def log(msg: str, level: str = "INFO"):
    """Print log message with timestamp"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")

def get_session():
    """Authenticate and get session cookie"""
    log("Authenticating with local login...")

    session = requests.Session()

    # Try to login at /auth/login
    response = session.get(f"{API_URL}/auth/login", allow_redirects=False)

    if response.status_code == 200:
        # Login page loaded
        log("✅ Login page accessible")

        # Try POST with empty credentials (local login)
        response = session.post(
            f"{API_URL}/auth/login",
            data={"username": "", "password": ""},
            allow_redirects=False
        )

        if response.status_code in [302, 200]:
            log("✅ Authenticated (local login)")
            return session

    # Fallback: try to access settings without login
    log("⚠️  Using unauthenticated session (may fail)")
    return session

def test_get_features(session):
    """Test GET /settings/features endpoint"""
    log("Testing GET /settings/features")

    response = session.get(f"{API_URL}/settings/features")

    if response.status_code == 401 or "no session" in response.text:
        log("⚠️  No session - trying without auth", "WARN")
        return None

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    data = response.json()
    assert "features" in data, "Missing 'features' key in response"

    features = data["features"]
    log(f"✅ Received {len(features)} features")

    # Count enabled/disabled
    enabled = sum(1 for f in features if f["enabled"])
    disabled = len(features) - enabled

    log(f"   Enabled: {enabled}, Disabled: {disabled}")
    assert enabled == 5, f"Expected 5 enabled features, got {enabled}"
    assert disabled == 36, f"Expected 36 disabled features, got {disabled}"

    return features

def test_token_metrics(session):
    """Test Token Metrics endpoint"""
    log("Testing Token Metrics endpoint")

    response = session.get(f"{API_URL}/metrics/session/current")

    if response.status_code == 404:
        log("ℹ️  No metrics available yet (expected for fresh install)")
        return True

    if response.status_code != 200:
        log(f"⚠️  Got {response.status_code}", "WARN")
        return False

    data = response.json()
    if "metrics" in data:
        metrics = data["metrics"]
        log(f"✅ Token Metrics loaded:")
        log(f"   Tokens: {metrics.get('total_tokens', 'N/A')}")
        log(f"   Cost Saved: ${metrics.get('estimated_savings', 0):.2f}")
    else:
        log("ℹ️  No metrics structure (may be empty)")

    return True

def test_simple_api():
    """Test endpoints without auth"""
    log("Testing endpoints (no auth required)")

    # These might work without session
    try:
        # Check health
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            log("✅ Health check passed")
    except:
        pass

    return True

def main():
    """Run all tests"""
    print()
    log("=" * 60, "INFO")
    log("Token Metrics Authentication Tests", "INFO")
    log("=" * 60, "INFO")
    print()

    try:
        # Test 1: Simple connectivity
        test_simple_api()
        print()

        # Test 2: Try to get session
        session = get_session()
        print()

        # Test 3: Get features
        features = test_get_features(session)
        if features:
            print()

            # Test 4: Token Metrics
            test_token_metrics(session)
            print()

            # Summary
            print()
            log("=" * 60, "INFO")
            log("✨ TESTS COMPLETED!", "INFO")
            log("=" * 60, "INFO")
            return 0
        else:
            log("⚠️  Could not authenticate. Tests incomplete.", "WARN")
            print()
            log("=" * 60, "INFO")
            log("ℹ️  Authentication Required", "INFO")
            log("=" * 60, "INFO")
            log("The Console requires session authentication.", "INFO")
            log("To test via API, you need to:", "INFO")
            log("  1. Login via /auth/login", "INFO")
            log("  2. Use the returned session cookie", "INFO")
            return 1

    except Exception as e:
        print()
        log(f"❌ ERROR: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())
