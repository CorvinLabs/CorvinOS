"""Language resolution module.

Single source of truth for language priority:
  1. Profile Setting (display_language)
  2. Input Language (detected from user text)
  3. Response Language (detected from Claude's response)
  4. System Default ("en")

Provides:
  - LanguageResolver: Priority-based language resolution
  - detect_language(): Detect language from text
  - LanguageContext: Immutable result of language resolution
"""

from .language_resolver import (
    LanguageResolver,
    LanguageContext,
    LanguageSource,
    detect_language,
)

__all__ = [
    "LanguageResolver",
    "LanguageContext",
    "LanguageSource",
    "detect_language",
]
