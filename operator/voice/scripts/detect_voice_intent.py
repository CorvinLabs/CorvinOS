#!/usr/bin/env python3
"""detect_voice_intent.py — read user prompt from stdin, print one of:
  full      user wants the whole answer read aloud, no summarisation
  summary   user explicitly asked for a summary (even if the text is short)
  (empty)   no override; pipeline uses its default mode

Used by stop_hook.sh to flip voice behaviour per turn based on what the user
said in *this* turn. Slash-commands set the persistent default; this detector
overrides it for one turn only.

Patterns are deliberately loose — false-positive cost is low (you get full
read-aloud once; just say it differently next turn). False-negative cost is
the bug we are fixing (Voice always cuts research output).
"""
from __future__ import annotations

import re
import sys

# Each pattern is a single regex; matched case-insensitive against the user
# prompt. Order matters: FULL beats SUMMARY when both match (because "lies
# das vollständig vor" should win over an incidental "kurz").

FULL_PATTERNS = [
    # German
    r"lies(?:\s+\w+){0,4}\s+(?:vollst[äa]ndig|komplett|w[öo]rtlich|alles|im\s+ganzen|in\s+voller\s+l[äa]nge|am\s+st[üu]ck)\s+vor",
    r"\b(?:vollst[äa]ndig|komplett|w[öo]rtlich|am\s+st[üu]ck)\s+vorlesen\b",
    r"\bvoll\s+vorlesen\b",
    r"lies\s+(?:mir\s+|uns\s+)?(?:das\s+ganze|alles|den\s+ganzen|die\s+ganze)\b",
    r"\b(?:ohne\s+k[üu]rzung|ohne\s+zusammenfassung|nicht\s+zusammenfassen|keine\s+zusammenfassung)\b",
    r"\bnichts\s+(?:auslassen|wegk[üu]rzen|k[üu]rzen)\b",
    # English
    r"\bread\s+(?:it|this|everything|the\s+(?:whole|entire)\s+\w+)?\s*(?:in\s+full|completely|verbatim|fully|aloud\s+in\s+full|all\s+of\s+(?:it|this))\b",
    r"\bread\s+(?:the\s+)?(?:whole|entire|full)\s+thing\b",
    r"\bno\s+summary\b",
    r"\bdon'?t\s+(?:summari[sz]e|cut)\b",
    r"\bverbatim\b",
]

SUMMARY_PATTERNS = [
    # German
    r"\bfass(?:e)?\s+(?:\w+\s+){0,3}zusammen\b",
    r"\bzusammenfass(?:ung|en)\b",
    r"\bkurzfassung\b",
    r"\bin\s+kurz(?:em)?\b",
    r"\bin\s+k[üu]rze\b",
    r"\bnur\s+(?:kurz|knapp|in\s+kurz)\b",
    # English
    r"\bsummari[sz]e\s+(?:it|this|the\s+\w+)?\b",
    r"\b(?:short|quick)\s+version\b",
    r"\bin\s+short\b",
    r"\btl;?dr\b",
]


def detect(text: str) -> str:
    if not text or not text.strip():
        return ""
    flags = re.IGNORECASE
    for pat in FULL_PATTERNS:
        if re.search(pat, text, flags):
            return "full"
    for pat in SUMMARY_PATTERNS:
        if re.search(pat, text, flags):
            return "summary"
    return ""


def main() -> int:
    text = sys.stdin.read()
    out = detect(text)
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
