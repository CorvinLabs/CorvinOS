"""Language Priority Resolver — Single source of truth for turn language.

ADR-0643 (or similar): Language Priority Architecture
  1. Profile Setting (display_language) — CANONICAL, if set
  2. Input Language (user text) — OVERRIDE, if no profile
  3. Response Language (Claude's answer) — FALLBACK
  4. System Default ("en") — last resort

This module centralizes language resolution across:
  - TTS (Voice Summary playback language)
  - Output formatting (Deutsch vs English)
  - ACP Skills context injection
  - Summary Generator language
  - Audit trail (what language did we resolve for this turn?)

Never flip mid-session: resolution happens ONCE per turn, stored in metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class LanguageSource(str, Enum):
    """Where the language decision came from."""
    PROFILE = "profile"          # display_language setting
    INPUT = "input"              # Detected from user text
    RESPONSE = "response"         # Detected from Claude's response
    SYSTEM = "system"             # Fallback default


@dataclass(frozen=True)
class LanguageContext:
    """Immutable language decision for one turn.

    Used across TTS, Summary Generator, ACP, Audit trail.
    """
    resolved_lang: str
    """BCP-47 language code: "de", "en", "zh-Hans", "ja", etc."""

    source: LanguageSource
    """Where this decision came from (profile/input/response/system)."""

    confidence: float
    """Confidence score [0.0, 1.0]. Profile=1.0, Input=0.95, Response=0.85, System=0.5."""

    profile_set: bool
    """Was profile.display_language actually set? (vs. resolved from input)."""

    def __repr__(self) -> str:
        return (
            f"LanguageContext(lang={self.resolved_lang!r}, "
            f"source={self.source.value!r}, confidence={self.confidence:.2f}, "
            f"profile_set={self.profile_set})"
        )


def _detect_script_language(text: str) -> Optional[str]:
    """Detect language from script alone (CJK, Arabic, Cyrillic, etc.).

    Returns BCP-47 code or None if ambiguous/Latin script.
    One character is enough for definitive detection.
    """
    if not text:
        return None

    # Chinese (Simplified + Traditional)
    if re.search(r'[一-鿿㐀-䶿]', text):
        # Hiragana/Katakana = Japanese, not Chinese
        if re.search(r'[぀-ゟ゠-ヿ]', text):
            return "ja"
        return "zh-Hans"  # Default to Simplified (backend normalizes)

    # Korean Hangul
    if re.search(r'[가-힯ᄀ-ᇿ]', text):
        return "ko"

    # Japanese hiragana/katakana (even without kanji)
    if re.search(r'[぀-ゟ゠-ヿ]', text):
        return "ja"

    # Arabic
    if re.search(r'[؀-ۿ]', text):
        return "ar"

    # Hebrew
    if re.search(r'[֐-׿]', text):
        return "he"

    # Cyrillic (Russian, Ukrainian, Serbian, etc.)
    if re.search(r'[Ѐ-ӿ]', text):
        return "ru"  # Default to Russian

    # Greek
    if re.search(r'[Ͱ-Ͽ]', text):
        return "el"

    # Thai
    if re.search(r'[฀-๿]', text):
        return "th"

    # Devanagari (Hindi, Sanskrit)
    if re.search(r'[ऀ-ॿ]', text):
        return "hi"

    return None


# Function-word inventories used for Latin-script scoring.
_GERMAN_WORDS_RE = re.compile(
    r"\b(die|der|das|und|in|ist|zu|bei|mit|auf|vom|den|des|"
    r"ein|eine|einen|einem|keinen|jede|jeden|jedem|"
    r"nicht|sie|er|es|ich|wir|du|mein|dein|sein|ihr|unser)\b"
)
_ENGLISH_WORDS_RE = re.compile(
    r"\b(the|and|or|is|in|to|of|for|with|be|at|by|from|"
    r"not|but|this|that|a|an|as|have|has|do|does|can|"
    r"will|would|should|could|may|might|must|shall|get|make)\b"
)

# Minimum number of function-word hits before word-level scoring may decide.
# Two is the smallest count that can still express dominance over the other
# language ("Das ist ..." / "This is ..."), and short turns are the common
# case in chat — a floor of three silently sent every short German sentence
# to the English fallback.
_MIN_WORD_HITS = 2

# Below this length there is not enough text for word-level scoring to mean
# anything. Diacritic evidence is checked BEFORE this gate, because it is
# decisive at any length.
_MIN_SCORING_LENGTH = 10


def _detect_latin_language(text: str) -> Optional[str]:
    """Detect language from Latin text (German, English, French, etc.).

    Uses character and word-level heuristics.
    Returns BCP-47 code or None if too ambiguous.
    """
    if not text:
        return None

    # Sample ~300 chars for scoring
    sample = text[:300].lower()

    german_chars = len(re.findall(r"[äöüß]", sample))
    german_words = len(_GERMAN_WORDS_RE.findall(sample))
    english_words = len(_ENGLISH_WORDS_RE.findall(sample))

    # German diacritics are strong evidence and are weighed BEFORE the
    # minimum-length gate: "äöüß" is decisive in four characters, while word
    # scoring genuinely needs a sentence. A single umlaut is enough — but only
    # while English function words do not outnumber German ones, so that an
    # English sentence carrying a loanword or proper noun ("The Zürich office
    # is open") is not misread as German.
    if german_chars >= 1 and german_words >= english_words:
        return "de"

    if len(text) < _MIN_SCORING_LENGTH:
        return None

    # If German words dominate, it's German
    if german_words > english_words and german_words >= _MIN_WORD_HITS:
        return "de"

    # If English words dominate, it's English
    if english_words > german_words and english_words >= _MIN_WORD_HITS:
        return "en"

    # French
    if re.search(r"\b(le|la|les|de|du|un|une|et|est|que|qui|avec)\b", sample):
        return "fr"

    # Spanish
    if re.search(r"\b(el|la|los|las|de|un|una|y|es|que|por|con)\b", sample):
        return "es"

    # Italian
    if re.search(r"\b(il|la|di|da|un|una|e|è|per|che|con)\b", sample):
        return "it"

    return None


def detect_language(text: str, fallback: str = "en") -> str:
    """Detect language from text.

    Priority:
      1. Script-level (CJK, Arabic, Hebrew, Cyrillic, etc.) — definitive
      2. German-specific chars (ä ö ü ß) — high confidence
      3. Word-level scoring (German vs English) — medium confidence
      4. Fallback — when ambiguous

    Returns BCP-47 code.
    """
    if not text:
        return fallback

    # Script-level detection (definitive)
    script_lang = _detect_script_language(text)
    if script_lang:
        return script_lang

    # Latin-based language detection
    latin_lang = _detect_latin_language(text)
    if latin_lang:
        return latin_lang

    # Ambiguous or neutral text
    return fallback


class LanguageResolver:
    """Single source of truth for language resolution.

    Priority order:
      1. Profile setting (display_language) — CANONICAL, if set
      2. Input language (user text) — OVERRIDE, if no profile
      3. Response language (Claude's answer) — FALLBACK
      4. System default ("en") — last resort

    Once resolved, the language should be CACHED and reused for the entire turn
    (TTS, Summary Generator, ACP context, audit trail).
    """

    def __init__(self, profile_lang: Optional[str], system_default: str = "en"):
        """Initialize resolver with profile setting.

        Args:
            profile_lang: User's display_language setting (e.g., "de", "en", "zh-Hans").
                         If set, this is the CANONICAL language for the session.
            system_default: Fallback language when detection fails (default: "en").
        """
        self.profile_lang = profile_lang
        self.system_default = system_default
        # Normalize profile lang (backend sometimes does "zh" → "zh-Hans")
        if self.profile_lang and self.profile_lang.strip():
            self.profile_lang = self._normalize_lang(self.profile_lang.strip())
        else:
            self.profile_lang = None

    @staticmethod
    def _normalize_lang(lang: str) -> str:
        """Normalize BCP-47 codes.

        Examples:
          "de" → "de"
          "en" → "en"
          "zh" → "zh-Hans"  (backend convention)
          "pt" → "pt"
        """
        if lang == "zh":
            return "zh-Hans"
        return lang.lower().strip()

    def resolve(
        self,
        user_text: str,
        response_text: str = "",
        force_input_detection: bool = False
    ) -> LanguageContext:
        """Resolve language in priority order.

        Args:
            user_text: The user's input (for input-language detection).
            response_text: Claude's response (for response-language fallback).
            force_input_detection: If True, ignore profile and detect from input.
                                  (Rare; used for override testing.)

        Returns:
            LanguageContext with resolved language, source, and confidence.
        """

        # 1. Profile is CANONICAL
        if self.profile_lang and not force_input_detection:
            return LanguageContext(
                resolved_lang=self.profile_lang,
                source=LanguageSource.PROFILE,
                confidence=1.0,
                profile_set=True
            )

        # 2. Input language (user text)
        input_lang = detect_language(user_text, fallback="")
        if input_lang:
            return LanguageContext(
                resolved_lang=input_lang,
                source=LanguageSource.INPUT,
                confidence=0.95,
                profile_set=False
            )

        # 3. Response language (Claude's answer) — fallback
        response_lang = detect_language(response_text, fallback="")
        if response_lang:
            return LanguageContext(
                resolved_lang=response_lang,
                source=LanguageSource.RESPONSE,
                confidence=0.85,
                profile_set=False
            )

        # 4. System default
        return LanguageContext(
            resolved_lang=self.system_default,
            source=LanguageSource.SYSTEM,
            confidence=0.5,
            profile_set=False
        )

    def resolve_for_tts(
        self,
        user_text: str,
        response_text: str
    ) -> str:
        """Convenience: resolve and return just the language code.

        Used by TTS playback, Summary Generator, etc.
        """
        ctx = self.resolve(user_text, response_text)
        return ctx.resolved_lang
