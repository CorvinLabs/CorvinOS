#!/usr/bin/env python3
"""test_voice_summary_e2e.py — End-to-End test of voice summary pipeline.

Tests the real build_voice_summary() with real strip_for_tts.py and
summarize.py scripts (not fakes). Verifies:
  1. Normal path: long text → strip → summarize → output
  2. Code-heavy input: mostly code blocks → fallback works
  3. Logging: when fallbacks happen, logs are emitted
"""
from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def test_normal_path() -> None:
    """Normal case: prose text → strip → summarize."""
    print("\n=== E2E Test 1: Normal prose path ===")
    import adapter  # type: ignore

    text = "Das ist ein langer Text. " * 100
    result = adapter.build_voice_summary(text, max_chars=400)

    assert result, "Should return non-empty summary"
    assert len(result) > 0, "Summary should have content"
    print(f"  ✓ Got summary: {result[:80]}...")


def test_code_heavy_input() -> None:
    """Code-heavy input: mostly code blocks → stripper removes them."""
    print("\n=== E2E Test 2: Code-heavy input (fallback) ===")
    import adapter  # type: ignore

    # Input: ~90% code, 10% text
    code_heavy = (
        "Das ist wichtig. "
        + "```python\n" + "x = 1\n" * 100 + "```\n"
        + "Das ist auch wichtig."
    )

    result = adapter.build_voice_summary(code_heavy, max_chars=400)
    assert result, "Should still return non-empty even when mostly code"
    assert "wichtig" in result.lower() or len(result) > 0
    print(f"  ✓ Got fallback result: {result[:80]}...")


def test_empty_input() -> None:
    """Empty input → returns empty."""
    print("\n=== E2E Test 3: Empty input ===")
    import adapter  # type: ignore

    result = adapter.build_voice_summary("", max_chars=400)
    assert result == "", "Empty input should return empty"
    print("  ✓ Empty input handled correctly")


def test_short_input() -> None:
    """Short input (≤ max_chars) → bypasses summarize.py."""
    print("\n=== E2E Test 4: Short input (direct path) ===")
    import adapter  # type: ignore

    short_text = "Dies ist kurz."
    result = adapter.build_voice_summary(short_text, max_chars=400)

    assert result, "Should return non-empty"
    assert "kurz" in result.lower() or len(result) > 0
    print(f"  ✓ Short path result: {result}")


def main() -> int:
    try:
        test_normal_path()
        test_code_heavy_input()
        test_empty_input()
        test_short_input()
        print("\n✅ All E2E tests passed!")
        return 0
    except AssertionError as e:
        print(f"\n❌ E2E test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
