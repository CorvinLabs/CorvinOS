"""Output Translation Layer — Language boundary between Skills (EN) and Nutzer (any).

Phase 3: Summary Generator, ACP Skills, Output Formatting

Architecture:
  Skills always run in English (LLM optimal) ← internal
  Output to nutzer gets translated if needed ← external

Example:
  1. Skill (English): "Task completed successfully"
  2. Profile Language: "de" (German)
  3. Translator: "Task completed..." → "Aufgabe erfolgreich abgeschlossen"
  4. User sees: German output

This layer sits AFTER skill execution, BEFORE delivery to nutzer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable

from .language_resolver import LanguageContext, LanguageSource


class TranslationMethod(str, Enum):
    """How translation happens (if at all)."""
    NONE = "none"              # No translation needed (lang already matches)
    LLM_TRANSLATE = "llm"      # Use Claude to translate
    CACHE_LOOKUP = "cache"     # Pre-translated (if available)
    SKIP_UNSUPPORTED = "skip"  # Language pair not yet supported


@dataclass(frozen=True)
class TranslationRequest:
    """Immutable translation request."""
    source_text: str
    target_lang: str
    source_lang: str = "en"  # Skills always English
    context: Optional[str] = None  # "skill_output" | "summary" | etc.


@dataclass(frozen=True)
class TranslationResult:
    """Immutable translation result."""
    translated_text: str
    method: TranslationMethod
    source_lang: str
    target_lang: str
    confidence: float  # 0.0–1.0 (1.0 = no translation needed)


class OutputTranslator:
    """Translate skill outputs from English to nutzer's language.

    Load-bearing rules:
      1. Skills ALWAYS output English (internal invariant)
      2. Nutzer sees their language (external guarantee)
      3. No translation = no-op (fast path for English nutzer)
      4. Failed translation = fallback to English (fail-closed)
    """

    def __init__(
        self,
        target_lang: str,
        llm_translate_fn: Optional[Callable[[TranslationRequest], str]] = None
    ):
        """Initialize translator.

        Args:
            target_lang: BCP-47 code ("de", "en", "zh-Hans", etc.)
            llm_translate_fn: Optional LLM-based translator.
                             If None, translation is unavailable (no-op).
        """
        self.target_lang = target_lang
        self.llm_translate_fn = llm_translate_fn

    def translate_output(
        self,
        text: str,
        source_lang: str = "en",
        context: Optional[str] = None
    ) -> TranslationResult:
        """Translate output from source lang to target lang.

        Args:
            text: Text to translate (e.g., skill output, summary)
            source_lang: Source language (default: "en" for skills)
            context: Optional context ("skill_output", "summary", etc.)

        Returns:
            TranslationResult with translated text + metadata.
        """

        # 1. No translation needed — langs match
        if source_lang == self.target_lang:
            return TranslationResult(
                translated_text=text,
                method=TranslationMethod.NONE,
                source_lang=source_lang,
                target_lang=self.target_lang,
                confidence=1.0  # Already correct language
            )

        # 2. LLM translation available
        if self.llm_translate_fn:
            try:
                req = TranslationRequest(
                    source_text=text,
                    source_lang=source_lang,
                    target_lang=self.target_lang,
                    context=context
                )
                translated = self.llm_translate_fn(req)
                return TranslationResult(
                    translated_text=translated,
                    method=TranslationMethod.LLM_TRANSLATE,
                    source_lang=source_lang,
                    target_lang=self.target_lang,
                    confidence=0.95  # LLM translation is good but not perfect
                )
            except Exception:
                # LLM translation failed → fallback to source (fail-closed)
                return TranslationResult(
                    translated_text=text,
                    method=TranslationMethod.SKIP_UNSUPPORTED,
                    source_lang=source_lang,
                    target_lang=self.target_lang,
                    confidence=0.5  # Fallback to English (suboptimal)
                )

        # 3. No translation available
        return TranslationResult(
            translated_text=text,
            method=TranslationMethod.SKIP_UNSUPPORTED,
            source_lang=source_lang,
            target_lang=self.target_lang,
            confidence=0.5
        )

    def should_translate(self, source_lang: str = "en") -> bool:
        """Quick check: is translation needed?"""
        return source_lang != self.target_lang and self.llm_translate_fn is not None


class LanguageOutputBoundary:
    """Enforce language boundary: Skills (EN) → Nutzer (any language).

    This is the contract between the internal system and external output.
    Used by Summary Generator, ACP Skills, Console Output Rendering, etc.
    """

    def __init__(self, language_context: LanguageContext):
        """Initialize boundary from language resolution context.

        Args:
            language_context: From LanguageResolver.resolve()
                            Contains nutzer's resolved language.
        """
        self.target_lang = language_context.resolved_lang
        self.language_context = language_context
        self.translator = OutputTranslator(
            target_lang=self.target_lang,
            llm_translate_fn=None  # Will be wired by chat_runtime
        )

    def set_translator(self, fn: Callable[[TranslationRequest], str]) -> None:
        """Wire up LLM-based translator (from chat_runtime)."""
        self.translator.llm_translate_fn = fn

    def process_skill_output(self, text: str, skill_name: str = "") -> str:
        """Process skill output (English) → nutzer's language.

        Args:
            text: Skill output (always English)
            skill_name: Optional skill name (for audit/debug)

        Returns:
            Translated text (or English fallback if translation failed).
        """
        result = self.translator.translate_output(
            text,
            source_lang="en",
            context=f"skill_output:{skill_name}" if skill_name else "skill_output"
        )
        # TODO: Audit-log the translation decision
        return result.translated_text
