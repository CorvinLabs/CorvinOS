"""ulo_metadata.py — Structural metadata extractor for ULO compliance (ADR-0163 M2).

Extracts structural properties from a response text using pure-Python regex
analysis — no LLM call, no network.  The resulting :class:`ResponseMetadata`
dict is what the ULO compliance checker receives.  The raw response text is
NEVER passed downstream; only the metadata dict is used.

GDPR Art. 5 boundary: this module converts text → metadata in-process.
Callers must not persist or log the input text.
Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass


# ── Language detection ────────────────────────────────────────────────────

_DE_TRIGRAMS: frozenset[str] = frozenset({
    "und", "die", "der", "das", "ist", "ich", "Sie", "sie", "ein", "eine",
    "von", "mit", "auf", "für", "aber", "auch", "sind", "haben", "nicht",
    "dass", "kann", "wird", "wenn", "des", "dem", "den", "war", "hat",
    "sein", "wie", "nach", "mehr", "oder", "aus", "an", "am", "als",
    "zum", "zur", "im", "bei", "uns", "nur", "noch", "hier",
})
_EN_TRIGRAMS: frozenset[str] = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "her", "was", "one", "our", "out", "had", "have", "that", "with",
    "this", "from", "they", "will", "been", "said", "who", "what", "when",
    "your", "which", "their", "would", "there", "make", "like", "into",
    "time", "look", "also", "than", "then", "some", "could", "these",
})
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")


def detect_language(text: str) -> str:
    """Return 'de', 'en', or 'unknown' based on token overlap heuristic."""
    words = _WORD_RE.findall(text[:2000])
    if not words:
        return "unknown"
    lower = [w.lower() for w in words]
    de_hits = sum(1 for w in lower if w in _DE_TRIGRAMS)
    en_hits = sum(1 for w in lower if w in _EN_TRIGRAMS)
    total = len(lower)
    de_ratio = de_hits / total
    en_ratio = en_hits / total
    if de_ratio > 0.04 and de_ratio > en_ratio * 1.5:
        return "de"
    if en_ratio > 0.04 and en_ratio > de_ratio * 1.5:
        return "en"
    if de_ratio > 0.02 and de_ratio > en_ratio:
        return "de"
    if en_ratio > 0.02:
        return "en"
    return "unknown"


# ── Structure patterns ────────────────────────────────────────────────────

_CODE_FENCE_RE  = re.compile(r"^```", re.MULTILINE)
_TABLE_ROW_RE   = re.compile(r"^\|.+\|", re.MULTILINE)
_TABLE_SEP_RE   = re.compile(r"^\|[-| :]+\|", re.MULTILINE)
_HEADING_RE     = re.compile(r"^#{1,6}\s", re.MULTILINE)
_BULLET_RE      = re.compile(r"^[ \t]*[-*+]\s", re.MULTILINE)
_NUMBERED_RE    = re.compile(r"^[ \t]*\d+\.\s", re.MULTILINE)
_BLANK_LINE_RE  = re.compile(r"\n\s*\n")


def _leading_element(text: str) -> str:
    """Return the type of the first substantive element."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("```"):
            return "code"
        if re.match(r"^#{1,6}\s", stripped):
            return "heading"
        if re.match(r"^[-*+]\s", stripped):
            return "list"
        if re.match(r"^\d+\.\s", stripped):
            return "list"
        if re.match(r"^\|.+\|", stripped):
            return "table"
        return "paragraph"
    return "other"


# ── Dataclass ─────────────────────────────────────────────────────────────

@dataclass
class ResponseMetadata:
    detected_language: str      # 'de' | 'en' | 'unknown'
    char_count:        int
    word_count:        int
    paragraph_count:   int
    has_code_block:    bool
    has_table:         bool
    has_bullet_list:   bool
    has_numbered_list: bool
    has_heading:       bool
    leading_element:   str      # 'heading'|'paragraph'|'list'|'code'|'table'|'other'

    def to_dict(self) -> dict:
        return asdict(self)


def extract(text: str) -> ResponseMetadata:
    """Extract structural metadata from a response string."""
    if not text:
        return ResponseMetadata(
            detected_language="unknown", char_count=0, word_count=0,
            paragraph_count=0, has_code_block=False, has_table=False,
            has_bullet_list=False, has_numbered_list=False,
            has_heading=False, leading_element="other",
        )

    paragraphs = [p.strip() for p in _BLANK_LINE_RE.split(text) if p.strip()]

    return ResponseMetadata(
        detected_language=detect_language(text),
        char_count=len(text),
        word_count=len(text.split()),
        paragraph_count=len(paragraphs),
        has_code_block=bool(_CODE_FENCE_RE.search(text)),
        has_table=bool(_TABLE_ROW_RE.search(text) and _TABLE_SEP_RE.search(text)),
        has_bullet_list=bool(_BULLET_RE.search(text)),
        has_numbered_list=bool(_NUMBERED_RE.search(text)),
        has_heading=bool(_HEADING_RE.search(text)),
        leading_element=_leading_element(text),
    )


__all__ = ["ResponseMetadata", "extract", "detect_language"]
