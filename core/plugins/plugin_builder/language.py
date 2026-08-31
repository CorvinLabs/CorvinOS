"""Session-pinned language detection (ADR-0262).

Deterministic and keyword-based, matching this package's existing philosophy
(``classifier.py``'s docstring: "Keyword-scored, not ML"). Only two languages
are supported today — ``"de"`` and ``"en"`` — because that is what
``voice_summary_smart.polish_for_audio`` actually branches on; a language this
module could detect but the voice layer cannot render would be a detection
that lies about what happens next.

Detected **once**, from the first free-text the caller supplies, and pinned
for the rest of the interview session — ADR-0262 is explicit that the
question prompts, generated docs and the checkpoint summary must not flip
language mid-session. :class:`LanguagePin` is that pin.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Case-insensitive whole-word stopwords. Short, high-signal, low false-positive
#: risk — deliberately excludes words that are also common English tokens (e.g.
#: German "in", "an", "man") to keep the two lists non-overlapping.
_DE_WORDS = frozenset({
    "der", "die", "das", "und", "ist", "nicht", "ich", "wir", "für", "mit",
    "auf", "von", "zu", "ein", "eine", "einen", "einem", "einer", "brauche",
    "möchte", "moechte", "will", "kann", "soll", "sollte", "wenn", "aber",
    "auch", "sich", "sind", "wird", "werden", "über", "ueber", "hier",
    "plugin", "idee", "ja", "nein", "bitte", "danke",
})
_EN_WORDS = frozenset({
    "the", "and", "is", "not", "i", "we", "for", "with", "on", "from", "to",
    "a", "an", "need", "want", "can", "should", "if", "but", "also", "are",
    "will", "be", "about", "here", "idea", "plugin", "of", "my",
})

_WORD_RE = re.compile(r"[a-zäöüß]+", re.IGNORECASE)
_DE_CHAR_RE = re.compile(r"[äöüßÄÖÜ]")

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("de", "en")


def detect_language(text: str) -> str:
    """Best-effort ``"de"`` or ``"en"`` from free text. Never raises.

    Ties, empty input, or a text with no recognizable stopword from either
    list fall back to :data:`DEFAULT_LANGUAGE` — silently guessing wrong for
    a session's whole language is worse than defaulting to the language the
    rest of this codebase's prose is already written in (CLAUDE.md: "All
    repository content: English").
    """
    if not text:
        return DEFAULT_LANGUAGE
    if _DE_CHAR_RE.search(text):
        # An umlaut or eszett essentially never appears in English prose —
        # treat it as a strong, immediate signal rather than one vote among
        # many stopword hits.
        return "de"
    words = {w.lower() for w in _WORD_RE.findall(text)}
    de_hits = len(words & _DE_WORDS)
    en_hits = len(words & _EN_WORDS)
    if de_hits > en_hits:
        return "de"
    if en_hits > de_hits:
        return "en"
    return DEFAULT_LANGUAGE


@dataclass
class LanguagePin:
    """Detect once, then hold — the session-scoped language contract ADR-0262
    requires. ``resolve()`` is idempotent after the first non-empty call."""

    _language: str | None = field(default=None, repr=False)

    @property
    def language(self) -> str | None:
        """The pinned language, or ``None`` before the first ``resolve()``."""
        return self._language

    def resolve(self, text: str) -> str:
        """Pin from ``text`` on first call; return the pinned value on every
        later call regardless of what ``text`` is passed then."""
        if self._language is None:
            self._language = detect_language(text)
        return self._language


__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "LanguagePin",
    "detect_language",
]
