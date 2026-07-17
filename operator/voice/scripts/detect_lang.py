#!/usr/bin/env python3
"""Detect German vs English in a text without external dependencies.

Usage:
    detect_lang.py [--default de|en] [text]
    echo "..." | detect_lang.py

Prints "de" or "en" on stdout. Heuristic: count occurrences of common
function words for each language; whichever scores higher wins. Falls
back to --default (or "de") on a tie or empty input.

This is intentionally tiny — it does not need to be perfect, only
"good enough to pick a TTS voice." For mixed-language text, picks the
dominant language.
"""

from __future__ import annotations

import argparse
import re
import sys

# Top function words. Kept intentionally short — adding many makes the
# detector slower without improving accuracy on de/en (the binary case).
DE = {
    "und", "der", "die", "das", "ist", "nicht", "ein", "eine", "den", "dem",
    "des", "im", "mit", "auf", "von", "zu", "sich", "auch", "wie", "war",
    "sind", "werden", "wird", "haben", "hat", "kann", "noch", "nur", "aber",
    "oder", "wenn", "weil", "doch", "schon", "über", "für", "bei", "nach",
    "ich", "du", "er", "sie", "wir", "ihr", "es", "wurde", "worden", "ja",
    "nein", "sehr", "viel", "viele", "einen", "einer", "einem", "eines",
    "alle", "alles", "kein", "keine",
}

EN = {
    "the", "and", "is", "of", "to", "in", "that", "it", "for", "with",
    "on", "as", "are", "was", "be", "this", "have", "has", "had", "not",
    "but", "or", "if", "when", "you", "we", "they", "i", "he", "she",
    "do", "does", "did", "an", "a", "by", "at", "from", "which", "what",
    "no", "yes", "very", "much", "many", "some", "any", "all", "none",
    "would", "could", "should", "will", "shall",
    # "done" — not a function word, but THE marker of short English status
    # answers ("Done. All tests pass."), which otherwise score too few hits
    # to clear detect_confident()'s one-sided bar (added 2026-07-17). No
    # German collision.
    "done",
}

WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")

# Words that occur as ordinary tokens in BOTH languages even though each is
# listed on only ONE side above: "was" (EN past tense / DE "what"), "in" and
# "an" (prepositions in both), one-letter "a" (EN article / a plain label like
# "Datei A" in German), "will" (EN auxiliary / DE "ich will"). Counting them
# one-sided flips short German sentences to English: score("Was war in Datei
# A los?") came back de=1/en=3, so a de-pinned user got an English voice AND
# an English base prompt for a plainly German question (found 2026-07-17).
# NOT included: "die"/"war"/"hat" — they read as content words (verb/nouns)
# in English, not function words, so their one-sided German count is safe
# and neutralising them would only erode the German margin.
BILINGUAL = {"was", "in", "an", "a", "will"}

_UMLAUT_RE = re.compile(r"[äöüÄÖÜß]")

# Confidence thresholds for detect_confident() when BOTH sides have hits:
# the winner must lead by at least MARGIN AND have at least MIN_HITS
# absolute evidence. Below either bar the caller's static fallback
# (profile pin / --default) is a better answer than a guess made from two
# or three function words. When the losing side has ZERO hits the bar
# drops to _CONFIDENT_ONE_SIDED_MIN — see detect_confident().
_CONFIDENT_MARGIN = 2
_CONFIDENT_MIN_HITS = 3
_CONFIDENT_ONE_SIDED_MIN = 2

# Markdown code carriers. Code is keyword soup in ENGLISH by construction
# (if/not/for/in/is/and/with/the ...), so a German answer that embeds a
# Python block scored a CONFIDENT "en" — the umlaut boost had no chance
# against a dozen keyword hits (found 2026-07-17, adversarial round).
# Fenced blocks (``` ... ``` — a dangling opener swallows to end-of-text,
# deliberately: unterminated code must not be scored either) and inline
# spans (`...`) are dropped before scoring. Same normalisation idea as
# strip_for_tts.py's markdown pass, replicated minimally so this module
# stays dependency-free.
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?(?:```|\Z)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _strip_code(text: str) -> str:
    """Remove fenced + inline markdown code before language scoring."""
    text = _CODE_FENCE_RE.sub(" ", text)
    return _INLINE_CODE_RE.sub(" ", text)


def score(text: str) -> tuple[int, int]:
    de_count = 0
    en_count = 0
    for w in WORD_RE.findall(text.lower()):
        if w in DE:
            de_count += 1
        if w in EN:
            en_count += 1
    return de_count, en_count


def detect_confident(text: str) -> str | None:
    """de/en detection with a confidence margin — ``None`` when unsure.

    Unlike :func:`detect` (which always answers, falling back to a default),
    this variant is for callers that have a BETTER fallback than a guess —
    e.g. adapter.py's per-turn voice-language override, whose fallback is the
    user's static profile pin. Three differences from the plain score() vote
    (all motivated by the 2026-07-17 finding that short German questions
    flipped a de-pinned user to an English voice):

    1. BILINGUAL overlap words count for BOTH sides — they still add
       absolute evidence but can never create a margin on their own.
    2. Umlauts/eszett add a strong German-only boost (same tiebreak
       :func:`detect` already applies — English text never contains them).
    3. Markdown code (fences + inline spans) is stripped first — code is
       English-keyword soup and flipped German answers to a confident "en".
    4. With MIXED evidence the winner must lead by ≥ _CONFIDENT_MARGIN
       AND have ≥ _CONFIDENT_MIN_HITS absolute hits. With ONE-SIDED
       evidence (the loser at exactly 0) ≥ _CONFIDENT_ONE_SIDED_MIN hits
       suffice — "Done. All tests pass." has no German signal to weigh
       against, and demanding 3 hits sent it to the (possibly German)
       pin. Not 1 hit: single shared function words would misfire on
       third Latin languages (nl/da/... texts often contain exactly one
       of "is"/"die"-class tokens). Anything weaker returns None.
    """
    text = _strip_code(text)
    de_count = 0
    en_count = 0
    for w in WORD_RE.findall(text.lower()):
        if w in BILINGUAL:
            de_count += 1
            en_count += 1
            continue
        if w in DE:
            de_count += 1
        if w in EN:
            en_count += 1
    if de_count == 0 and en_count == 0:
        return None
    if _UMLAUT_RE.search(text):
        de_count += 2
    # One-sided evidence: nothing on the other side to confuse the vote.
    # (Umlauts alone cannot reach this branch for "de" — zero word hits on
    # both sides already returned None above — but they do BLOCK the "en"
    # branch, which is correct: umlauts are a German-only signal.)
    if en_count == 0:
        return "de" if de_count >= _CONFIDENT_ONE_SIDED_MIN else None
    if de_count == 0:
        return "en" if en_count >= _CONFIDENT_ONE_SIDED_MIN else None
    if de_count >= en_count + _CONFIDENT_MARGIN and de_count >= _CONFIDENT_MIN_HITS:
        return "de"
    if en_count >= de_count + _CONFIDENT_MARGIN and en_count >= _CONFIDENT_MIN_HITS:
        return "en"
    return None


def detect(text: str, default: str = "de") -> str:
    de_count, en_count = score(text)
    if de_count == 0 and en_count == 0:
        return default
    # Treat umlauts/eszett as a strong German signal even if word counts tie.
    if re.search(r"[äöüÄÖÜß]", text):
        de_count += 2
    if de_count > en_count:
        return "de"
    if en_count > de_count:
        return "en"
    return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--default", default="de", choices=["de", "en"])
    ap.add_argument("text", nargs="*", help="Text to analyze; if omitted, read stdin")
    args = ap.parse_args()

    text = " ".join(args.text) if args.text else sys.stdin.read()
    print(detect(text, args.default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
