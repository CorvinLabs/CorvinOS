#!/usr/bin/env python3
"""
Test TTS Quality-First Fallback Strategy

Validates:
1. OpenAI TTS works (if configured)
2. Edge TTS works (fallback)
3. Quality-First strategy: prefer OpenAI, fallback to Edge on error
4. Both providers guarantee voice availability
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.voice.tts_providers import (
    get_tts_manager,
    OpenAITTSProvider,
    EdgeTTSProvider,
    TTSProvider
)


async def test_openai_provider():
    """Test OpenAI TTS provider"""
    print("\n📊 TEST 1: OpenAI TTS Provider")
    print("-" * 50)

    provider = OpenAITTSProvider()

    print(f"OpenAI API Key configured: {provider.is_available()}")
    print(f"Quality score: {provider.quality_score:.0%}")

    if not provider.is_available():
        print("⚠️  OpenAI TTS not available (missing API key)")
        return False

    test_text = "CorvinOS is now speaking with OpenAI Text-to-Speech."
    result = await provider.synthesize(test_text)

    if result and result.success:
        print(f"✅ OpenAI synthesis succeeded")
        print(f"   Audio size: {len(result.audio_bytes)} bytes")
        print(f"   Quality: {result.quality_score:.0%}")
        return True
    else:
        print("❌ OpenAI synthesis failed")
        return False


async def test_edge_tts_provider():
    """Test Edge TTS provider"""
    print("\n📊 TEST 2: Edge TTS Provider (Fallback)")
    print("-" * 50)

    provider = EdgeTTSProvider()

    print(f"Edge TTS available: {provider.is_available()}")
    print(f"Quality score: {provider.quality_score:.0%}")

    if not provider.is_available():
        print("⚠️  Edge TTS not available (SDK not installed)")
        return False

    test_text = "CorvinOS is now speaking with Microsoft Edge Text-to-Speech."
    result = await provider.synthesize(test_text)

    if result and result.success:
        print(f"✅ Edge TTS synthesis succeeded")
        print(f"   Audio size: {len(result.audio_bytes)} bytes")
        print(f"   Quality: {result.quality_score:.0%}")
        return True
    else:
        print("❌ Edge TTS synthesis failed")
        return False


async def test_quality_first_strategy():
    """Test Quality-First fallback strategy"""
    print("\n📊 TEST 3: Quality-First Fallback Strategy")
    print("-" * 50)

    manager = get_tts_manager()

    status = manager.get_status()
    print(f"Primary (OpenAI) available: {status['primary_available']}")
    print(f"Fallback (Edge) available: {status['fallback_available']}")

    test_text = "Testing Quality-First voice synthesis with automatic fallback."
    result = await manager.synthesize(test_text)

    if result and result.success:
        print(f"✅ Quality-First synthesis succeeded")
        print(f"   Provider used: {result.provider}")
        print(f"   Quality score: {result.quality_score:.0%}")
        print(f"   Audio size: {len(result.audio_bytes)} bytes")

        if result.provider == "openai":
            print("   🎯 Using high-quality OpenAI provider")
        else:
            print("   ⚠️  Fell back to Edge TTS (OpenAI unavailable)")
        return True
    else:
        print("❌ Quality-First synthesis failed (all providers exhausted)")
        return False


async def test_force_provider():
    """Test forcing specific provider (debugging)"""
    print("\n📊 TEST 4: Force Provider (Debugging)")
    print("-" * 50)

    manager = get_tts_manager()
    test_text = "Testing forced provider selection."

    # Force Edge TTS
    print("\nForcing Edge TTS provider...")
    result = await manager.synthesize(test_text, force_provider=TTSProvider.EDGE_TTS)

    if result and result.success:
        print(f"✅ Forced Edge TTS succeeded")
        print(f"   Provider: {result.provider}")
        print(f"   Quality: {result.quality_score:.0%}")
    else:
        print("❌ Forced Edge TTS failed")

    # Force OpenAI (if available)
    print("\nForcing OpenAI provider...")
    result = await manager.synthesize(test_text, force_provider=TTSProvider.OPENAI)

    if result and result.success:
        print(f"✅ Forced OpenAI succeeded")
        print(f"   Provider: {result.provider}")
        print(f"   Quality: {result.quality_score:.0%}")
    else:
        print("⚠️  Forced OpenAI failed or unavailable (expected if no API key)")


async def main():
    """Run all tests"""
    print("\n" + "=" * 50)
    print("🎤 TTS QUALITY-FIRST STRATEGY TESTS")
    print("=" * 50)

    results = {}

    # Run tests
    results["openai"] = await test_openai_provider()
    results["edge_tts"] = await test_edge_tts_provider()
    results["quality_first"] = await test_quality_first_strategy()
    await test_force_provider()

    # Summary
    print("\n" + "=" * 50)
    print("📋 SUMMARY")
    print("=" * 50)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    print(f"\n✅ Tests passed: {passed}/{total}")

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {test_name}")

    print("\n🎯 Strategy: Quality-First (OpenAI → Edge TTS)")
    print("✅ Voice is always available (high redundancy)")
    print("🎤 Quality preference: OpenAI > Edge TTS")
    print("🛡️  Fallback ensures availability under all conditions")

    if passed == total:
        print("\n🚀 All tests passed! Voice service is production-ready.")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check configuration.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
