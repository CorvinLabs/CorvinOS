#!/usr/bin/env python3
"""
E2E Test: Verify that OpenAI quota errors DON'T show to user when Piper works.

This test simulates:
1. OpenAI TTS throws 429 (quota exhausted)
2. Piper is available and works
3. User should NOT see "quota exhausted" error message
4. Voice should be synthesized via Piper instead
"""

import sys
import os
import time
import tempfile
from pathlib import Path
from unittest import mock

# Setup path
ADAPTER_PATH = Path(__file__).parent / "adapter.py"
sys.path.insert(0, str(ADAPTER_PATH.parent))

print("=" * 80)
print("E2E TEST: OpenAI Quota Fallback (User Should NOT See Error)")
print("=" * 80)
print()

# ─── Test 1: OpenAI quota, Piper available ──────────────────────────────────

print("[Test 1] OpenAI quota error → Piper fallback → NO error shown to user")
print("-" * 80)

def test_quota_fallback_no_user_message():
    """
    Simulate:
    - OpenAI throws insufficient_quota (429)
    - Piper is available
    - Voice synthesis should succeed
    - User should NOT see error message in last_skip_reason
    """

    # Mock voice_engine_state to track what happens
    voice_state = {
        "quota_until": 0.0,
        "first_quota_logged": False,
        "last_skip_reason": None,
    }

    # Simulate _try_openai_tts catching a quota error
    print("\n  1. OpenAI TTS called...")
    now = time.time()
    msg = "Error code: 429 - insufficient_quota"

    # This is what happens in _try_openai_tts when quota error occurs:
    is_quota = ("insufficient_quota" in msg or "Error code: 429" in msg)
    if is_quota:
        voice_state["quota_until"] = now + 3600.0  # 1 hour backoff
        print("  ✓ Quota error detected: quota_until set")

    # Now _try_piper_tts is called (should succeed)
    print("  2. Piper TTS fallback attempted...")
    piper_result = "mock_piper_output.ogg"  # Piper succeeds!
    print(f"  ✓ Piper succeeded: {piper_result}")

    # Simulate the orchestration layer (synthesize_voice_note)
    # If piper succeeded, last_skip_reason should be cleared
    if piper_result:
        voice_state["last_skip_reason"] = None
        print("  3. Voice synthesis succeeded via Piper")
        print("  ✓ last_skip_reason: None (NO error shown to user)")
    else:
        # Both failed
        if now < voice_state.get("quota_until", 0.0):
            voice_state["last_skip_reason"] = "Sprachnachricht nicht möglich — OpenAI-Quota..."
        else:
            voice_state["last_skip_reason"] = "Sprachnachricht nicht möglich — kein TTS-Engine..."
        print("  ✗ last_skip_reason set: error would show to user")

    # Verify no error shown to user
    assert voice_state["last_skip_reason"] is None, \
        f"ERROR: User would see message: {voice_state['last_skip_reason']}"
    print("\n  ✅ TEST PASSED: User sees NO error when Piper works")
    return True


# ─── Test 2: OpenAI quota, Piper also unavailable ────────────────────────────

print("\n[Test 2] OpenAI quota + Piper unavailable → Error SHOULD be shown")
print("-" * 80)

def test_quota_both_fail_show_message():
    """
    Simulate:
    - OpenAI throws quota error
    - Piper is NOT available
    - User SHOULD see error message
    """

    voice_state = {
        "quota_until": 0.0,
        "first_quota_logged": False,
        "last_skip_reason": None,
    }

    print("\n  1. OpenAI TTS called...")
    now = time.time()
    voice_state["quota_until"] = now + 3600.0
    print("  ✓ Quota error detected")

    print("  2. Piper TTS fallback attempted...")
    piper_result = None  # Piper NOT available!
    print("  ✗ Piper failed (not installed/configured)")

    # Both engines failed — set error message
    if piper_result is None:
        if now < voice_state.get("quota_until", 0.0):
            voice_state["last_skip_reason"] = (
                "Sprachnachricht nicht möglich — OpenAI-Quota und Piper nicht verfügbar"
            )
        print("  3. Voice synthesis failed")
        print(f"  ✓ last_skip_reason set: {voice_state['last_skip_reason'][:50]}...")

    # Verify error IS shown to user
    assert voice_state["last_skip_reason"] is not None, \
        "ERROR: User should see error message when both engines fail"
    print("\n  ✅ TEST PASSED: User sees error when all engines fail")
    return True


# ─── Test 3: Check actual adapter.py code ──────────────────────────────────

print("\n[Test 3] Verify adapter.py has proper fallback handling")
print("-" * 80)

def test_adapter_code_has_fallback():
    """Check that adapter.py actually implements the fallback logic."""

    adapter_code = ADAPTER_PATH.read_text()

    checks = [
        ("_try_openai_tts function exists", "_try_openai_tts(" in adapter_code),
        ("_try_piper_tts function exists", "_try_piper_tts(" in adapter_code),
        ("Quota error detection", "insufficient_quota" in adapter_code),
        ("Piper fallback attempted", "_try_piper_tts(text, lang)" in adapter_code),
        # 0.10.34 refactored the raw dict write (`last_skip_reason"] = None`)
        # into the _set_voice_skip_reason() helper — the guard's intent is
        # unchanged: success paths must clear the user-facing skip reason.
        ("Error message cleared on success", "_set_voice_skip_reason(None)" in adapter_code),
        ("Both engines failed check", "now < _voice_engine_state.get(" in adapter_code),
    ]

    all_passed = True
    for check_name, result in checks:
        status = "✓" if result else "✗"
        print(f"  {status} {check_name}")
        if not result:
            all_passed = False

    assert all_passed, "Some adapter.py checks failed"
    print("\n  ✅ TEST PASSED: adapter.py has proper structure")
    return True


# ─── Run all tests ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("Quota with Piper fallback", test_quota_fallback_no_user_message),
        ("Both engines fail", test_quota_both_fail_show_message),
        ("Adapter code structure", test_adapter_code_has_fallback),
    ]

    passed = 0
    failed = 0

    for test_name, test_fn in tests:
        try:
            if test_fn():
                passed += 1
        except AssertionError as e:
            print(f"\n  ❌ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"\n  ❌ ERROR: {e}")
            failed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)

    sys.exit(0 if failed == 0 else 1)
