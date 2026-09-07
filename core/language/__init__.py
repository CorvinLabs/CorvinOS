"""Language resolution + output translation module.

Two layers:
  1. Input Resolution (LanguageResolver):
     - Profile Setting (display_language) — CANONICAL
     - Input Language (detected from user text)
     - Response Language (detected from Claude's response)
     - System Default ("en")

  2. Output Translation (OutputTranslator):
     - Skills always output English (internal)
     - OutputTranslator converts to nutzer's language (external)
     - Fail-closed: fallback to English if translation unavailable

Provides:
  - LanguageResolver: Priority-based language resolution
  - detect_language(): Detect language from text
  - LanguageContext: Immutable result of language resolution
  - OutputTranslator: Translate skill outputs to nutzer's language
  - LanguageOutputBoundary: Enforce skills (EN) → nutzer (any) boundary
"""

from .language_resolver import (
    LanguageResolver,
    LanguageContext,
    LanguageSource,
    detect_language,
)
from .output_translator import (
    OutputTranslator,
    TranslationMethod,
    TranslationRequest,
    LanguageOutputBoundary,
)

__all__ = [
    "LanguageResolver",
    "LanguageContext",
    "LanguageSource",
    "detect_language",
    "OutputTranslator",
    "TranslationMethod",
    "TranslationRequest",
    "LanguageOutputBoundary",
]
