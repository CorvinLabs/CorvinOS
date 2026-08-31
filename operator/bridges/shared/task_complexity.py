"""task_complexity.py — Conservative heuristic classifier deciding
whether a user task is "complex enough" to be worth routing through the
AWP-DAG (Phase 3 dispatcher) instead of a single-turn Claude / Codex call.

Design contract (mirrors the auto-routing heuristic in router.py):

  * **False-negative is fine** — the assistant fallback handles missed
    multi-step tasks without surprise. The user just gets a normal
    single-turn reply.
  * **False-positive is bad** — routing a one-line "git status" through a
    DAG-Engine would burn tokens and add latency for zero benefit.
  * Pure-Python, no LLM call, no I/O. Sub-millisecond per call.

Five signals are scored independently, then summed against a threshold:

  1. Sequential-step markers   ("erst …, dann …", "first …, then …",
                                 "afterwards", "anschließend", "im Anschluss")
  2. Enumerated lists          ("1. … 2. …", "- step a …- step b …",
                                 numbered/bulleted lists with ≥ 3 items)
  3. Parallel / DAG markers    ("in parallel", "parallel", "gleichzeitig",
                                 "DAG", "pipeline", "workflow", "fan out")
  4. Multi-verb hint           ≥ 3 verbs in the imperative + length > 200
  5. Explicit complexity hint  ("step by step", "Schritt für Schritt",
                                 "multi-step", "komplexe Aufgabe")

Threshold: ≥ 2 signals → complex. One signal alone is too weak (single
list could be a short shopping note); two signals overlap means the user
genuinely structured the task.

Public API:
    is_complex_task(text: str, *, lang: str | None = None) -> bool
        True iff task crosses the threshold.

    score_task(text: str, *, lang: str | None = None) -> dict
        Detailed signal breakdown for diagnostics + audit.
"""
from __future__ import annotations

import re

# ----- pattern banks (DE + EN, lower-cased input assumed) -----------------

_SEQUENTIAL_PATTERNS = [
    r"\berst\b.{0,80}?\bdann\b",
    r"\bzuerst\b.{0,80}?\b(danach|dann|anschlie(?:ss|ß)end)\b",
    r"\bfirst\b.{0,80}?\b(then|after that|afterwards|next)\b",
    r"\bnachdem\b",
    r"\bafter (?:you|that|we|the)\b",
    r"\bnachdem du\b",
    r"\bim anschluss\b",
]

_ENUMERATION_PATTERNS = [
    r"(?:^|\s)1[.)]\s.{2,}\s+2[.)]\s",     # "1. ... 2. ..."
    r"(?:^|\s)-\s.{2,}\n\s*-\s.{2,}\n\s*-\s",  # three dash-bullets
    r"(?:^|\s)\*\s.{2,}\n\s*\*\s.{2,}\n\s*\*\s",  # three star-bullets
    r"(?:^|\s)a[.)]\s.{2,}\s+b[.)]\s.{2,}\s+c[.)]",
]

_PARALLEL_PATTERNS = [
    r"\b(in\s+parallel|parallel|simultaneously)\b",
    r"\b(gleichzeitig|parallel|nebenher)\b",
    r"\b(d\.?a\.?g|workflow|pipeline|fan[-\s]?out|map[-\s]?reduce)\b",
    r"\borchestrier\w*\b",
    r"\borchestrate\b",
    r"\bdistribute\s+across\b",
]

_COMPLEXITY_HINT_PATTERNS = [
    r"\bstep[-\s]by[-\s]step\b",
    r"\bschritt[-\s]?f(?:ü|u)r[-\s]?schritt\b",
    r"\bmulti[-\s]step\b",
    r"\b(complex|komplexe?|umfangreich(?:e|er|es)?)\s+(task|aufgabe|workflow)\b",
    r"\bnacheinander\b",
    r"\bin (?:der )?reihenfolge\b",
    r"\bbreak\s+(?:this|it)\s+(?:down|into)\b",
    r"\bzerleg(?:en|e)\s+in\s+(?:teil|sub|unter)?(?:schritte|aufgaben)\b",
]

# Verbs that count toward signal 4 (multi-verb hint). Lowercased.
_VERB_LEMMAS_DE = {
    "schreib", "lese", "lies", "speicher", "lade", "lad", "öffne", "schließ",
    "erstell", "erzeuge", "generier", "baue", "bau", "test", "prüf",
    "analysier", "untersuch", "vergleich", "summier", "fass", "übersetz",
    "deploy", "publish", "comment", "kommentier", "review", "refactor",
    "merg", "branch", "commit", "push", "pull", "schick", "send",
}
_VERB_LEMMAS_EN = {
    "write", "read", "save", "load", "open", "close", "create", "generate",
    "build", "test", "check", "analyse", "analyze", "examine", "compare",
    "summarise", "summarize", "translate", "deploy", "publish", "comment",
    "review", "refactor", "merge", "branch", "commit", "push", "pull",
    "send", "fetch", "download", "upload", "transform", "extract",
    "filter", "aggregate", "compute", "calculate", "validate",
}


def _count_verbs(text: str) -> int:
    """Count distinct verb-lemmas in text. Lower-bound — we miss some
    inflections but the threshold is conservative anyway."""
    seen: set[str] = set()
    words = re.findall(r"[a-zäöüß]+", text)
    for w in words:
        for lem in (_VERB_LEMMAS_DE | _VERB_LEMMAS_EN):
            if w.startswith(lem):
                seen.add(lem)
                break
    return len(seen)


def score_task(text: str, *, lang: str | None = None) -> dict:
    """Return per-signal hits with weights.

    Weighting (chosen so that the threshold ≥ 2 means "two soft hints
    OR one strong explicit marker"):

      - parallel        : weight 2 (very explicit — DAG/pipeline/parallel)
      - complexity_hint : weight 2 (user said "step by step" etc.)
      - sequential      : weight 1
      - enumeration     : weight 1
      - multi_verb      : weight 1 (≥ 3 distinct known verbs AND > 60 chars)
    """
    if not text or not text.strip():
        return {"complex": False, "score": 0, "signals": {}, "reason": "empty"}

    needle = text.lower()

    sig_seq = any(re.search(p, needle, re.IGNORECASE) for p in _SEQUENTIAL_PATTERNS)
    sig_enum = any(re.search(p, needle, re.IGNORECASE | re.MULTILINE)
                   for p in _ENUMERATION_PATTERNS)
    sig_parallel = any(re.search(p, needle, re.IGNORECASE) for p in _PARALLEL_PATTERNS)
    sig_hint = any(re.search(p, needle, re.IGNORECASE) for p in _COMPLEXITY_HINT_PATTERNS)

    verb_count = _count_verbs(needle)
    sig_verbs = (verb_count >= 3) and (len(text) > 60)

    signals = {
        "sequential": sig_seq,
        "enumeration": sig_enum,
        "parallel": sig_parallel,
        "complexity_hint": sig_hint,
        "multi_verb": sig_verbs,
    }
    weights = {
        "sequential": 1,
        "enumeration": 1,
        "parallel": 2,
        "complexity_hint": 2,
        "multi_verb": 1,
    }
    score = sum(weights[k] for k, v in signals.items() if v)
    complex_flag = score >= 2

    reason_bits = [f"{k}({weights[k]})" for k, v in signals.items() if v]
    reason = "+".join(reason_bits) if reason_bits else "no signals"

    return {
        "complex": complex_flag,
        "score": score,
        "signals": signals,
        "verb_count": verb_count,
        "text_len": len(text),
        "reason": reason,
    }


def is_complex_task(text: str, *, lang: str | None = None) -> bool:
    """Convenience wrapper around score_task(). Two-or-more signals → True."""
    return score_task(text, lang=lang)["complex"]
