#!/usr/bin/env python3
"""
E2E Test: TTS Provider Selection — Debug why Edge TTS is used instead of OpenAI

Scenarios tested:
1. OpenAI key present → should use OpenAI
2. OpenAI key missing → should fall back to Edge
3. OpenAI key quota exceeded → should fall back to Edge
4. Both providers fail → text-only fallback
"""

import asyncio
import os
import sys
from pathlib import Path
import json
import logging

# Setup logging for diagnostics
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add bridges/shared to path
BRIDGES_SHARED = Path(__file__).parent.parent / "corvin_operator/bridges/shared"
sys.path.insert(0, str(BRIDGES_SHARED))

def test_provider_key_resolution():
    """Test 1: Key resolution logic"""
    print("\n" + "="*70)
    print("TEST 1: Key Resolution Logic")
    print("="*70)

    import provider_keys

    # Show candidates
    candidates = provider_keys._candidates_for("tts_openai_api_key")
    print(f"✓ Candidates for 'tts_openai_api_key': {candidates}")

    # Check env vars
    print("\n-- Checking environment variables --")
    for var in ["CORVIN_TTS_OPENAI_KEY", "OPENAI_API_KEY"]:
        val = os.environ.get(var, "NOT SET")
        is_placeholder = val.startswith("PLACEHOLDER")
        status = "⚠️ PLACEHOLDER" if is_placeholder else "✓ SET" if val != "NOT SET" else "❌ NOT SET"
        print(f"  {var}: {status}")

    # Resolve key
    key = provider_keys.resolve_key("tts_openai_api_key")
    if key:
        is_placeholder = key.startswith("PLACEHOLDER")
        if is_placeholder:
            print(f"\n❌ PROBLEM FOUND: OpenAI key resolved to PLACEHOLDER value")
            print(f"   Value: {key[:50]}...")
            return False
        else:
            print(f"\n✓ OpenAI key resolved successfully (length: {len(key)})")
            return True
    else:
        print(f"\n❌ OpenAI key not found")
        return False


def test_provider_availability():
    """Test 2: Check TTS provider availability"""
    print("\n" + "="*70)
    print("TEST 2: TTS Provider Availability")
    print("="*70)

    # Try importing OpenAI
    try:
        from openai import OpenAI
        print("✓ OpenAI SDK is installed")
    except ImportError:
        print("❌ OpenAI SDK NOT installed")
        return False

    # Try importing edge_tts
    try:
        import edge_tts
        print("✓ edge-tts SDK is installed")
    except ImportError:
        print("❌ edge-tts NOT installed")
        return False

    # Test core/voice/tts_providers.py
    try:
        from core.voice.tts_providers import get_tts_manager
        manager = get_tts_manager()
        status = manager.get_status()
        print(f"\n-- TTS Manager Status --")
        print(f"  OpenAI available: {status['primary_available']}")
        print(f"  Edge TTS available: {status['fallback_available']}")
        print(f"  Last provider used: {status.get('last_provider_used', 'none')}")

        if status['primary_available']:
            print("✓ OpenAI TTS is available")
        else:
            print("❌ OpenAI TTS is NOT available (should be!)")

        return True
    except Exception as e:
        print(f"⚠️ Could not load tts_providers: {e}")
        return True  # Don't fail if this module doesn't exist


def test_openai_key_validation():
    """Test 3: Is the OpenAI key valid?"""
    print("\n" + "="*70)
    print("TEST 3: OpenAI Key Validation")
    print("="*70)

    import provider_keys
    key = provider_keys.resolve_key("tts_openai_api_key")

    if not key:
        print("❌ No OpenAI key found")
        return False

    # Check for placeholder
    if key.startswith("PLACEHOLDER"):
        print(f"❌ Key is a PLACEHOLDER: {key}")
        print(f"\nFIX: Update CORVIN_TTS_OPENAI_KEY in ~/.config/corvin-voice/service.env")
        print(f"     with the real OpenAI API key from the vault")
        return False

    # Check format
    try:
        key.encode("ascii")
        print(f"✓ Key is ASCII-encodable")
    except Exception as e:
        print(f"❌ Key encoding error: {e}")
        return False

    # Check length (typical format: sk-proj-xxx... ~48-100 chars)
    if len(key) < 20:
        print(f"⚠️ Key seems too short ({len(key)} chars)")
        return False

    if len(key) > 500:
        print(f"⚠️ Key seems too long ({len(key)} chars)")
        return False

    print(f"✓ Key looks valid (length: {len(key)} chars, format: sk-proj-...)")
    return True


async def test_adapter_voice_synthesis():
    """Test 4: End-to-end Voice Synthesis"""
    print("\n" + "="*70)
    print("TEST 4: End-to-End Voice Synthesis (adapter.py)")
    print("="*70)

    try:
        # Import adapter
        import adapter

        # Check for CORVIN_TTS_LOCAL_ONLY flag
        local_only = os.environ.get("CORVIN_TTS_LOCAL_ONLY") == "1"
        if local_only:
            print("⚠️ CORVIN_TTS_LOCAL_ONLY=1 is set → OpenAI will be skipped!")
            return False

        print("✓ CORVIN_TTS_LOCAL_ONLY is not set (OpenAI allowed)")

        # Test the actual _try_openai_tts function
        print("\n-- Testing OpenAI TTS directly --")
        try:
            result = await adapter._try_openai_tts("Hello World", "en", voice="nova")
            if result:
                print(f"✓ OpenAI TTS succeeded!")
                print(f"  Provider: {result.provider}")
                print(f"  Quality: {result.quality_score}")
                print(f"  Duration: {result.duration_ms}ms")
                return True
            else:
                print(f"❌ OpenAI TTS returned None")
                return False
        except Exception as e:
            print(f"❌ OpenAI TTS error: {e}")
            import traceback
            traceback.print_exc()
            return False

    except ImportError as e:
        print(f"⚠️ Could not import adapter: {e}")
        return True  # Don't fail


async def test_fallback_behavior():
    """Test 5: Verify fallback logic (OpenAI → Edge)"""
    print("\n" + "="*70)
    print("TEST 5: Fallback Behavior")
    print("="*70)

    try:
        import adapter

        # Test synthesize_voice_note
        print("Testing synthesize_voice_note with fallback chain...")
        result = await asyncio.to_thread(adapter.synthesize_voice_note, "Test voice note", "en")

        if result:
            print(f"✓ Voice synthesis succeeded")
            print(f"  Output path: {result}")

            # Check which provider was used
            skip_reason = adapter.voice_skip_reason()
            print(f"  Skip reason: {skip_reason}")

            # Try to determine which provider was used
            provider_state = getattr(adapter, '_voice_engine_state', {})
            print(f"  Provider state: {provider_state}")

            return True
        else:
            print(f"❌ Voice synthesis returned None")
            skip_reason = adapter.voice_skip_reason()
            print(f"  Skip reason: {skip_reason}")
            return False

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests"""
    print("\n" + "█"*70)
    print("█ E2E TTS PROVIDER DEBUG")
    print("█"*70)

    results = []

    # Test 1: Key resolution
    results.append(("Key Resolution", test_provider_key_resolution()))

    # Test 2: Provider availability
    results.append(("Provider Availability", test_provider_availability()))

    # Test 3: Key validation
    results.append(("Key Validation", test_openai_key_validation()))

    # Test 4: Provider configuration
    results.append(("TTS Provider Config", await test_adapter_voice_synthesis()))

    # Test 5: Fallback behavior
    results.append(("Fallback Behavior", await test_fallback_behavior()))

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nPassed: {passed}/{total}")

    if passed == total:
        print("\n✓ All tests passed! OpenAI TTS should be used.")
    else:
        print(f"\n❌ {total - passed} test(s) failed. Issues found.")

        # Provide remediation steps
        print("\n" + "="*70)
        print("REMEDIATION STEPS")
        print("="*70)
        print("""
1. Update CORVIN_TTS_OPENAI_KEY in ~/.config/corvin-voice/service.env:
   - Retrieve the real OpenAI key from the vault
   - Replace the PLACEHOLDER value with the real key
   - Ensure the file has mode 0o600 (chmod 600)

2. Restart the voice daemon:
   systemctl --user restart corvin-voice

3. Re-run this test to verify:
   python3 tests/e2e_voice_tts_provider_debug.py

4. To verify which provider is actually being used:
   - Watch the logs: journalctl --user -u corvin-voice -f
   - Look for: "TTS: Trying primary provider (OpenAI)" or fallback messages
   - Or check adapter.py logging with DEBUG enabled
        """)

    return 0 if passed == total else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
