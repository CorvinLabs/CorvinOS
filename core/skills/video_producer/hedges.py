"""One hedge-phrase detector for Maestro and the Asset Analyzer.

A hedge marks a claim the narration does not stand behind ("I think",
"vielleicht"). Matching is on whole words: substring matching blocked
"The AI thinks before it acts" ("i think") and "improbably" ("probably").
Phrases that are ordinary in technical prose ("could be", "might be")
are deliberately absent — a FAIL here stops the job.
"""
from __future__ import annotations

import re

_HEDGES = (
    # English
    "i believe", "i think", "i guess", "maybe", "probably", "allegedly",
    "supposedly", "it seems",
    # German
    "ich glaube", "ich denke", "vielleicht", "vermutlich", "angeblich",
    "wahrscheinlich", "möglicherweise",
)
_HEDGE_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(h) for h in _HEDGES) + r")(?!\w)", re.IGNORECASE)


def find_hedge(text: str) -> str | None:
    """The first hedge phrase in text, or None."""
    m = _HEDGE_RE.search(text or "")
    return m.group(0) if m else None
