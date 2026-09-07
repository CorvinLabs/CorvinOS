"""Test OutputTranslator: Skills (EN) → Nutzer (any language)."""

import pytest

from core.language.output_translator import (
    OutputTranslator,
    TranslationMethod,
    TranslationRequest,
    LanguageOutputBoundary,
)
from core.language.language_resolver import LanguageContext, LanguageSource


class TestOutputTranslator:
    """Test translation logic."""

    # ── No Translation Needed (langs match) ──
    def test_no_translation_english_to_english(self):
        """English → English = no-op."""
        translator = OutputTranslator(target_lang="en")
        result = translator.translate_output(
            text="Task completed successfully",
            source_lang="en"
        )
        assert result.translated_text == "Task completed successfully"
        assert result.method == TranslationMethod.NONE
        assert result.confidence == 1.0

    def test_no_translation_german_to_german(self):
        """German → German = no-op."""
        translator = OutputTranslator(target_lang="de")
        result = translator.translate_output(
            text="Aufgabe erfolgreich abgeschlossen",
            source_lang="de"
        )
        assert result.translated_text == "Aufgabe erfolgreich abgeschlossen"
        assert result.method == TranslationMethod.NONE
        assert result.confidence == 1.0

    # ── LLM Translation Available ──
    def test_llm_translation_english_to_german(self):
        """English → German via LLM."""
        def mock_translator(req: TranslationRequest) -> str:
            translations = {
                "Task completed successfully": "Aufgabe erfolgreich abgeschlossen",
                "Error occurred": "Fehler aufgetreten",
                "Processing request": "Anfrage wird verarbeitet",
            }
            return translations.get(req.source_text, req.source_text)

        translator = OutputTranslator(
            target_lang="de",
            llm_translate_fn=mock_translator
        )
        result = translator.translate_output(
            text="Task completed successfully",
            source_lang="en"
        )
        assert result.translated_text == "Aufgabe erfolgreich abgeschlossen"
        assert result.method == TranslationMethod.LLM_TRANSLATE
        assert result.confidence == 0.95

    def test_llm_translation_english_to_chinese(self):
        """English → Chinese via LLM."""
        def mock_translator(req: TranslationRequest) -> str:
            translations = {
                "Hello": "你好",
                "Good morning": "早上好",
            }
            return translations.get(req.source_text, req.source_text)

        translator = OutputTranslator(
            target_lang="zh-Hans",
            llm_translate_fn=mock_translator
        )
        result = translator.translate_output(
            text="Hello",
            source_lang="en"
        )
        assert result.translated_text == "你好"
        assert result.method == TranslationMethod.LLM_TRANSLATE

    # ── LLM Translation Failed (Fallback) ──
    def test_llm_translation_failed_fallback_to_source(self):
        """LLM crashes → fallback to English (fail-closed)."""
        def failing_translator(req: TranslationRequest) -> str:
            raise RuntimeError("LLM unavailable")

        translator = OutputTranslator(
            target_lang="de",
            llm_translate_fn=failing_translator
        )
        result = translator.translate_output(
            text="Task completed",
            source_lang="en"
        )
        assert result.translated_text == "Task completed"  # Fallback to source
        assert result.method == TranslationMethod.SKIP_UNSUPPORTED
        assert result.confidence == 0.5  # Lower confidence (suboptimal)

    # ── No LLM Available ──
    def test_no_translation_function_available(self):
        """No LLM translator available → no-op, return English."""
        translator = OutputTranslator(
            target_lang="de",
            llm_translate_fn=None
        )
        result = translator.translate_output(
            text="Task completed",
            source_lang="en"
        )
        assert result.translated_text == "Task completed"  # English fallback
        assert result.method == TranslationMethod.SKIP_UNSUPPORTED
        assert result.confidence == 0.5

    # ── Convenience: should_translate ──
    def test_should_translate_needed(self):
        """should_translate = True when translation is needed."""
        translator = OutputTranslator(
            target_lang="de",
            llm_translate_fn=lambda req: "translated"
        )
        assert translator.should_translate(source_lang="en") is True

    def test_should_translate_not_needed(self):
        """should_translate = False when langs match."""
        translator = OutputTranslator(target_lang="en")
        assert translator.should_translate(source_lang="en") is False

    def test_should_translate_no_function(self):
        """should_translate = False when no translator available."""
        translator = OutputTranslator(
            target_lang="de",
            llm_translate_fn=None
        )
        assert translator.should_translate(source_lang="en") is False


class TestLanguageOutputBoundary:
    """Test the Skills (EN) → Nutzer (any) boundary."""

    def test_boundary_english_profile(self):
        """English profile → no translation needed."""
        ctx = LanguageContext(
            resolved_lang="en",
            source=LanguageSource.PROFILE,
            confidence=1.0,
            profile_set=True
        )
        boundary = LanguageOutputBoundary(ctx)
        result = boundary.process_skill_output(
            "Task completed",
            skill_name="sample_skill"
        )
        assert result == "Task completed"

    def test_boundary_german_profile_with_translator(self):
        """German profile → translation applied."""
        def mock_translator(req: TranslationRequest) -> str:
            translations = {
                "Task completed": "Aufgabe abgeschlossen",
            }
            return translations.get(req.source_text, req.source_text)

        ctx = LanguageContext(
            resolved_lang="de",
            source=LanguageSource.PROFILE,
            confidence=1.0,
            profile_set=True
        )
        boundary = LanguageOutputBoundary(ctx)
        boundary.set_translator(mock_translator)

        result = boundary.process_skill_output(
            "Task completed",
            skill_name="sample_skill"
        )
        assert result == "Aufgabe abgeschlossen"

    def test_boundary_immutability(self):
        """LanguageOutputBoundary is immutable."""
        ctx = LanguageContext(
            resolved_lang="en",
            source=LanguageSource.PROFILE,
            confidence=1.0,
            profile_set=True
        )
        boundary = LanguageOutputBoundary(ctx)
        # Should not raise, language_context is frozen
        assert boundary.language_context.resolved_lang == "en"


class TestTranslationRequest:
    """Test TranslationRequest structure."""

    def test_request_immutable(self):
        """TranslationRequest is frozen."""
        req = TranslationRequest(
            source_text="Hello",
            source_lang="en",
            target_lang="de"
        )
        with pytest.raises(AttributeError):
            req.source_text = "Hi"

    def test_request_with_context(self):
        """TranslationRequest can carry context."""
        req = TranslationRequest(
            source_text="Error",
            source_lang="en",
            target_lang="de",
            context="error_message"
        )
        assert req.context == "error_message"


class TestTranslationResult:
    """Test TranslationResult structure."""

    def test_result_immutable(self):
        """TranslationResult is frozen."""
        from core.language.output_translator import TranslationResult
        result = TranslationResult(
            translated_text="Hallo",
            method=TranslationMethod.LLM_TRANSLATE,
            source_lang="en",
            target_lang="de",
            confidence=0.95
        )
        with pytest.raises(AttributeError):
            result.confidence = 0.5
