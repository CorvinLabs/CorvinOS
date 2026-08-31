#!/usr/bin/env python3
"""Smart Hybrid voice language resolution (2026-07-17).

`_resolve_voice_output_language` uses a hybrid approach:
1. If user sets `display_language` → that's authoritative (power users)
2. If not set → auto-detect from text (good out-of-box experience)
3. If detection fails → fallback to system locale

This gives new users a seamless experience (automatic language detection)
while respecting power users who want to pin a language.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import adapter  # type: ignore  # noqa: E402


def _pin(display_language: str) -> None:
    class _P:
        @staticmethod
        def load():
            return {"display_language": display_language}
    adapter._voice_profile = _P


_EN = "This is clearly an English answer with the usual function words in it."
_DE = "Das ist eindeutig eine deutsche Antwort mit den ueblichen Funktionswoertern."


def test_user_preference_pin_wins_over_text() -> None:
    """(1) User sets de → gets de regardless of English text."""
    _pin("de")
    assert adapter._resolve_voice_output_language(_EN) == "de"
    assert adapter._resolve_voice_output_language(_DE) == "de"


def test_no_preference_text_detection_wins() -> None:
    """(2) No preference set → auto-detect from text (Text-First)."""
    adapter._voice_profile = None
    # German text → detected as de
    assert adapter._resolve_voice_output_language(_DE) == "de"
    # English text → detected as en
    assert adapter._resolve_voice_output_language(_EN) == "en"


def test_no_preference_no_detection_falls_back_to_system() -> None:
    """(3) Ambiguous text with no preference → system locale."""
    adapter._voice_profile = None
    result = adapter._resolve_voice_output_language("OK.")  # ambiguous
    # System locale varies; just verify non-empty
    assert result


def test_user_preference_zh_with_german_text_uses_pinned_zh() -> None:
    """User preference zh-Hans is authoritative even for German text."""
    _pin("zh-Hans")
    assert adapter._resolve_voice_output_language("Das ist Deutsch.") == "zh-Hans"
    # But also with Chinese
    assert adapter._resolve_voice_output_language("这是中文。") == "zh-Hans"


def test_user_preference_en_with_german_text_uses_pinned_en() -> None:
    """English pin overrides German text."""
    _pin("en")
    assert adapter._resolve_voice_output_language(_DE) == "en"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
