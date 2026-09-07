"""Test LanguageResolver: language priority, detection, fallback."""

import pytest

from core.language.language_resolver import (
    LanguageResolver,
    LanguageContext,
    LanguageSource,
    detect_language,
)


class TestDetectLanguage:
    """Test language detection from text."""

    # ── Script-level detection (CJK, Arabic, etc.) ──
    def test_detect_chinese_simplified(self):
        """Chinese characters → zh-Hans."""
        assert detect_language("这是一个测试。") == "zh-Hans"

    def test_detect_chinese_traditional(self):
        """Traditional Chinese → zh-Hans."""
        assert detect_language("這是一個測試。") == "zh-Hans"

    def test_detect_japanese_hiragana(self):
        """Hiragana → ja."""
        assert detect_language("これはテストです。") == "ja"

    def test_detect_japanese_katakana(self):
        """Katakana → ja."""
        assert detect_language("テストです。") == "ja"

    def test_detect_korean(self):
        """Korean Hangul → ko."""
        assert detect_language("이것은 테스트입니다.") == "ko"

    def test_detect_arabic(self):
        """Arabic script → ar."""
        assert detect_language("هذا اختبار.") == "ar"

    def test_detect_hebrew(self):
        """Hebrew script → he."""
        assert detect_language("זהו בדיקה.") == "he"

    def test_detect_cyrillic(self):
        """Cyrillic (Russian) → ru."""
        assert detect_language("Это тест.") == "ru"

    def test_detect_thai(self):
        """Thai script → th."""
        assert detect_language("นี่คือการทดสอบ") == "th"

    def test_detect_devanagari(self):
        """Devanagari (Hindi) → hi."""
        assert detect_language("यह एक परीक्षण है।") == "hi"

    # ── German-specific characters ──
    def test_detect_german_umlaut(self):
        """German characters (ä ö ü ß) → de."""
        assert detect_language("Überprüfung der Größe") == "de"

    def test_detect_german_single_umlaut(self):
        """Even one German umlaut with more chars → de."""
        assert detect_language("Das ist schön") == "de"

    def test_detect_german_multiple_umlauts(self):
        """Multiple German chars → de."""
        assert detect_language("äöüß äöüß") == "de"

    # ── Word-level detection (German vs English) ──
    def test_detect_german_words(self):
        """German words → de."""
        text = "Die Datei ist in der Bibliothek. Das ist wichtig und sehr nützlich."
        assert detect_language(text) == "de"

    def test_detect_english_words(self):
        """English words → en."""
        text = "The file is in the library. This is important and very useful."
        assert detect_language(text) == "en"

    def test_detect_german_common_words(self):
        """German common words (die, der, das, und, ist) → de."""
        text = "Die Datei und die Ordner sind in der Schublade."
        assert detect_language(text) == "de"

    def test_detect_english_common_words(self):
        """English common words (the, and, is, in, of) → en."""
        text = "The file and the folders are in the drawer."
        assert detect_language(text) == "en"

    # ── Fallback ──
    def test_detect_empty_string(self):
        """Empty string → fallback."""
        assert detect_language("") == "en"
        assert detect_language("", fallback="de") == "de"

    def test_detect_very_short_text(self):
        """Very short text (< 10 chars) → fallback."""
        assert detect_language("Hi") == "en"
        assert detect_language("OK") == "en"

    def test_detect_ambiguous_neutral(self):
        """Ambiguous/neutral text → fallback."""
        assert detect_language("123 abc xyz") == "en"
        assert detect_language("=== test ===") == "en"


class TestLanguageResolver:
    """Test LanguageResolver priority logic."""

    # ── Profile Priority (1) ──
    def test_profile_priority_german(self):
        """Profile = 'de' → always return 'de', even with English input."""
        resolver = LanguageResolver(profile_lang="de")
        ctx = resolver.resolve(
            user_text="Hello, this is English",
            response_text="This is also English"
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.PROFILE
        assert ctx.confidence == 1.0
        assert ctx.profile_set is True

    def test_profile_priority_english(self):
        """Profile = 'en' → always return 'en', even with German input."""
        resolver = LanguageResolver(profile_lang="en")
        ctx = resolver.resolve(
            user_text="Das ist Deutsch",
            response_text="Auch Deutsch"
        )
        assert ctx.resolved_lang == "en"
        assert ctx.source == LanguageSource.PROFILE
        assert ctx.confidence == 1.0

    def test_profile_priority_chinese(self):
        """Profile = 'zh-Hans' → respect normalization."""
        resolver = LanguageResolver(profile_lang="zh")  # Should normalize to zh-Hans
        ctx = resolver.resolve(user_text="anything", response_text="")
        assert ctx.resolved_lang == "zh-Hans"
        assert ctx.source == LanguageSource.PROFILE

    # ── Input Priority (2) ──
    def test_input_priority_german(self):
        """No profile, user input German → resolve to 'de'."""
        resolver = LanguageResolver(profile_lang=None)
        ctx = resolver.resolve(
            user_text="Das ist ein deutscher Text.",
            response_text=""
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.INPUT
        assert ctx.confidence == 0.95
        assert ctx.profile_set is False

    def test_input_priority_english(self):
        """No profile, user input English → resolve to 'en'."""
        resolver = LanguageResolver(profile_lang=None)
        ctx = resolver.resolve(
            user_text="This is English text.",
            response_text=""
        )
        assert ctx.resolved_lang == "en"
        assert ctx.source == LanguageSource.INPUT

    def test_input_priority_chinese(self):
        """No profile, user input Chinese → resolve to 'zh-Hans'."""
        resolver = LanguageResolver(profile_lang=None)
        ctx = resolver.resolve(
            user_text="这是中文文本。",
            response_text=""
        )
        assert ctx.resolved_lang == "zh-Hans"
        assert ctx.source == LanguageSource.INPUT

    # ── Response Priority (3) ──
    def test_response_priority(self):
        """No profile/input, response German → resolve to 'de'."""
        resolver = LanguageResolver(profile_lang=None)
        ctx = resolver.resolve(
            user_text="123 test",  # Ambiguous input
            response_text="Das ist eine Antwort auf Deutsch."
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.RESPONSE
        assert ctx.confidence == 0.85

    # ── System Default (4) ──
    def test_system_default(self):
        """Nothing detectable → system default."""
        resolver = LanguageResolver(profile_lang=None, system_default="en")
        ctx = resolver.resolve(
            user_text="",
            response_text=""
        )
        assert ctx.resolved_lang == "en"
        assert ctx.source == LanguageSource.SYSTEM
        assert ctx.confidence == 0.5

    def test_system_default_custom(self):
        """Custom system default."""
        resolver = LanguageResolver(profile_lang=None, system_default="de")
        ctx = resolver.resolve(user_text="", response_text="")
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.SYSTEM

    # ── Force Input Detection (Override Profile) ──
    def test_force_input_detection_overrides_profile(self):
        """force_input_detection=True → ignore profile, use input."""
        resolver = LanguageResolver(profile_lang="en")
        ctx = resolver.resolve(
            user_text="Das ist Deutsch",
            response_text="",
            force_input_detection=True
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.INPUT
        assert ctx.profile_set is False

    # ── Profile Handling Edge Cases ──
    def test_profile_empty_string(self):
        """Profile = '' (empty) → treat as None, use input."""
        resolver = LanguageResolver(profile_lang="")
        ctx = resolver.resolve(
            user_text="Das ist Deutsch",
            response_text=""
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.INPUT
        assert ctx.profile_set is False

    def test_profile_whitespace_only(self):
        """Profile = '   ' (whitespace) → treat as None."""
        resolver = LanguageResolver(profile_lang="   ")
        ctx = resolver.resolve(
            user_text="Das ist Deutsch",
            response_text=""
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.INPUT

    def test_profile_case_normalization(self):
        """Profile = 'DE' (uppercase) → normalize to 'de'."""
        resolver = LanguageResolver(profile_lang="DE")
        ctx = resolver.resolve(user_text="", response_text="")
        assert ctx.resolved_lang == "de"

    # ── Convenience Method ──
    def test_resolve_for_tts(self):
        """resolve_for_tts() returns just the language code."""
        resolver = LanguageResolver(profile_lang="de")
        lang = resolver.resolve_for_tts("anything", "")
        assert lang == "de"
        assert isinstance(lang, str)

    # ── Realistic Scenarios ──
    def test_scenario_profile_deutsch_user_english_input(self):
        """Realistic: Profile='de', user types English, Claude responds English.
        Expected: TTS speaks DEUTSCH (profile wins).
        """
        resolver = LanguageResolver(profile_lang="de")
        ctx = resolver.resolve(
            user_text="Hello, check this code",
            response_text="Here's what I found..."
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.PROFILE

    def test_scenario_no_profile_user_deutsch_input(self):
        """Realistic: No profile, user types German.
        Expected: TTS speaks DEUTSCH (input detected).
        """
        resolver = LanguageResolver(profile_lang=None)
        ctx = resolver.resolve(
            user_text="Überprüf diesen Code",
            response_text="Hier sind die Probleme..."
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.INPUT

    def test_scenario_no_profile_ambiguous_input_deutsch_response(self):
        """Realistic: No profile, ambiguous input, Claude responds German.
        Expected: TTS speaks DEUTSCH (response detected).
        """
        resolver = LanguageResolver(profile_lang=None)
        ctx = resolver.resolve(
            user_text="123 test",  # Ambiguous
            response_text="Das ist eine Antwort."
        )
        assert ctx.resolved_lang == "de"
        assert ctx.source == LanguageSource.RESPONSE

    def test_scenario_context_immutability(self):
        """LanguageContext is frozen (immutable)."""
        ctx = LanguageContext(
            resolved_lang="de",
            source=LanguageSource.PROFILE,
            confidence=1.0,
            profile_set=True
        )
        # Should raise AttributeError on attempt to modify
        with pytest.raises(AttributeError):
            ctx.resolved_lang = "en"


class TestLanguageContextRepr:
    """Test LanguageContext string representation."""

    def test_repr_profile(self):
        """LanguageContext repr is readable."""
        ctx = LanguageContext(
            resolved_lang="de",
            source=LanguageSource.PROFILE,
            confidence=1.0,
            profile_set=True
        )
        r = repr(ctx)
        assert "de" in r
        assert "profile" in r
        assert "1.00" in r

    def test_repr_input(self):
        ctx = LanguageContext(
            resolved_lang="en",
            source=LanguageSource.INPUT,
            confidence=0.95,
            profile_set=False
        )
        r = repr(ctx)
        assert "en" in r
        assert "input" in r
        assert "0.95" in r
