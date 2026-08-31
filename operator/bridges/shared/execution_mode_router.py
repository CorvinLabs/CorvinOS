#!/usr/bin/env python3
"""execution_mode_router.py — the Execution-Mode Router (EMR) for the bridge.

ADR-0127. A second routing dimension, orthogonal to persona selection
(``router.py``): given the final prompt, decide *how* the task should run —
which execution substrate fits it best.

    single   — one synchronous LLM turn (the default; anything ambiguous)
    loop     — recurring / self-paced driver ("every 30 min", "until X")
    workflow — multi-agent fan-out (ACS) ("thoroughly", "audit the repo")
    compute  — out-of-LLM-loop Agentic Compute (L25): parameter sweeps /
               optimisation reducible to (tool, param_grid, loss_metric)

Public API:

    classify(prompt, *, persona=None, min_confidence=0.5) -> dict

returns::

    {"mode": "<single|loop|workflow|compute>",
     "confidence": <0.0-1.0>,
     "why": "<short reason>",
     "params": {...}}        # mode-specific hints (never executed here)

Design mirrors ``router.py`` exactly: a Tier-1 heuristic (regex, ~0 ms, no
API) is the only backend implemented here (ADR-0127 M1). An optional Tier-2
LLM classifier (``EMR_ALLOW_LLM=1``) is a later milestone; this module must
not pull in the anthropic SDK (CI AST lint, same rule as router.py / L25).

The router only *classifies*. It never schedules a loop, never spawns a
workflow, and never calls ``compute_run`` — the gating policy and dispatch
live in the adapter (ADR-0127 §2). ``mode == "single"`` is the fail-safe
default for anything that does not clearly match a richer substrate.

    EMR_FAKE=1 + EMR_FAKE_RESULT='<json>'   — deterministic test override.
"""

from __future__ import annotations

import json
import os
import re

# Modes, weakest → strongest claim on the task.
MODE_SINGLE = "single"
MODE_LOOP = "loop"
MODE_WORKFLOW = "workflow"
MODE_COMPUTE = "compute"

ALL_MODES = (MODE_SINGLE, MODE_LOOP, MODE_WORKFLOW, MODE_COMPUTE)

# ── Heuristic patterns (DE + EN) ──────────────────────────────────────────
#
# Each entry is (compiled_regex, weight). A prompt's score for a mode is the
# sum of the weights of the patterns it matches, capped at 1.0. Patterns are
# deliberately conservative: a false negative degrades safely to `single`,
# while a false `workflow`/`compute` positive only ever *proposes* (never
# auto-starts) per the ADR-0127 gating policy.

# Weights ≥ MIN (0.5) are "strong" — a single match classifies. Weights
# below MIN are "soft" — a bare verb like "überwache" / "optimiere" must
# co-occur with another signal to clear the confidence floor, so innocent
# prompts ("optimiere meinen Text", "überwache kurz den Fortschritt") stay
# `single`. (Review: ADR-0127 M1 false-positive hardening.)
_LOOP_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\balle\s+\d+\s*(?:sek|sekunden|min|minuten|std|stunden|h|m|s)\b", re.I), 0.8),
    (re.compile(r"\bevery\s+\d+\s*(?:sec|second|min|minute|hour|h|m|s)s?\b", re.I), 0.8),
    (re.compile(r"\bevery\s+(?:few\s+)?(?:second|minute|hour|day)s?\b", re.I), 0.65),
    (re.compile(r"\bregelmä(?:ß|ss)ig\b", re.I), 0.55),
    (re.compile(r"\bbis\s+.+\b(?:fertig|erledigt|gr(?:ü|ue)n|done|passes|bestanden)\b", re.I), 0.55),
    (re.compile(r"\bständig\b|\bdauerhaft\b|\bperiodisch\b", re.I), 0.55),
    (re.compile(r"\bkeep\s+(?:trying|retrying|running|polling|checking|watching|going|looping|monitoring)\b", re.I), 0.55),
    (re.compile(r"\b(?:poll|polling)\b|\bin\s+a\s+loop\b|\bin\s+(?:einer\s+)?schleife\b", re.I), 0.55),
    # Soft — need a co-signal to win.
    (re.compile(r"\b(?:über|ueber)wach(?:e|en|t)?\b", re.I), 0.4),
    (re.compile(r"\bbeobachte(?:t|n)?\b", re.I), 0.4),
    (re.compile(r"\bwiederhol(?:e|t|en)?\b", re.I), 0.4),
    (re.compile(r"\b(?:watch|monitor)\b", re.I), 0.4),
]

_WORKFLOW_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\bgr(?:ü|ue)ndlich\b", re.I), 0.55),
    (re.compile(r"\bumfassend\b|\bvollst(?:ä|ae)ndig\b", re.I), 0.4),
    (re.compile(r"\bthoroughl?y\b|\bcomprehensive(?:ly)?\b|\bexhaustive\b", re.I), 0.55),
    (re.compile(r"\bvergleiche?\s+(?:mehrere|verschiedene|\d+)\b", re.I), 0.6),
    (re.compile(r"\bcompare\s+(?:multiple|several|\d+)\s+\w+", re.I), 0.6),
    # "audit" only as an action/target — not the noun in "Audit-Chain"/"Audit-Log".
    (re.compile(r"\bauditiere\b", re.I), 0.55),
    (re.compile(r"\baudit\s+(?:the|das|den|der|my|mein\w*|unser\w*|all|every|jede[ns]?)\b", re.I), 0.55),
    (re.compile(r"\b(?:security|code|compliance|full|complete)[\s-]+audit\b", re.I), 0.55),
    (re.compile(r"\b(?:den\s+)?ganzen?\s+(?:ordner|repo(?:sitory)?|codebase|projekt)\b", re.I), 0.6),
    (re.compile(r"\b(?:the\s+)?(?:whole|entire)\s+(?:repo(?:sitory)?|codebase|project|folder|dir)\b", re.I), 0.6),
    (re.compile(r"\bmigrier(?:e|en|t)?\b|\bmigrat(?:e|ion)\b", re.I), 0.5),
    (re.compile(r"\bfan[\s-]*out\b|\bfan\b[\w\s]{0,12}\bout\b", re.I), 0.55),
    (re.compile(r"\b(?:parallele?\s+agenten|multi-?agent|(?:multiple|several|many)\s+agents?|across\s+\w+\s+agents?)\b", re.I), 0.6),
    (re.compile(r"\bjede\s+(?:datei|funktion|seite)\b|\bevery\s+(?:file|function|page)\b", re.I), 0.5),
]

_COMPUTE_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    # Strong — domain-specific terms that alone justify a compute proposal.
    (re.compile(r"\bparameter[\s-]*(?:sweep|suche|search|grid)\b", re.I), 0.7),
    (re.compile(r"\bgrid[\s-]*search\b|\bbayes(?:ian|ianisch)?\b|\bhyperparameter\b", re.I), 0.7),
    (re.compile(r"\b(?:random|grid|bayesian)\s+search\b", re.I), 0.6),
    (re.compile(r"\bloss[\s-]*(?:metric|funktion|function)\b|\bverlustfunktion\b", re.I), 0.6),
    (re.compile(r"\bbeste[rn]?\s+(?:konfiguration|hyperparameter|parameter)\b", re.I), 0.6),
    # Soft — common verbs that need a co-signal ("optimiere meinen Text" must
    # NOT become compute; "optimiere die Parameter per grid" should).
    (re.compile(r"\boptimier(?:e|en|ung)?\b|\boptimi[sz]e\b|\boptimi[sz]ation\b", re.I), 0.35),
    (re.compile(r"\bminimier(?:e|en)?\b|\bmaximier(?:e|en)?\b|\bminimi[sz]e\b|\bmaximi[sz]e\b", re.I), 0.35),
    (re.compile(r"\bfind\s+the\s+best\s+\w+\s+(?:for|of|to)\b", re.I), 0.4),
    (re.compile(r"\bsweep\b", re.I), 0.4),
    (re.compile(r"\bbeste[rn]?\s+(?:einstellung|schwelle|threshold|wert(?:e)?)\b", re.I), 0.4),
    (re.compile(r"\b(?:über|across|over)\s+(?:alle|all|den|the)\s+\w+\s+(?:werte|values|kombination)", re.I), 0.45),
    (re.compile(r"\bconverge(?:nce)?\b|\bkonvergen(?:z|ce)\b", re.I), 0.4),
]

_MODE_PATTERNS: dict[str, list[tuple[re.Pattern[str], float]]] = {
    MODE_LOOP: _LOOP_PATTERNS,
    MODE_WORKFLOW: _WORKFLOW_PATTERNS,
    MODE_COMPUTE: _COMPUTE_PATTERNS,
}

# ── Interval extraction for loop mode ─────────────────────────────────────

_UNIT_SECONDS = {
    "s": 1, "sek": 1, "sekunde": 1, "sekunden": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "minute": 60, "minuten": 60, "minutes": 60,
    "h": 3600, "std": 3600, "stunde": 3600, "stunden": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "tag": 86400, "tage": 86400, "day": 86400, "days": 86400,
}
_INTERVAL_RE = re.compile(
    r"\b(?:alle|every|each)\s+(\d+)\s*"
    r"(s|sek\w*|m|min\w*|h|std|stunden?|hours?|minutes?|seconds?|d|tage?|days?)\b",
    re.I,
)
# Clamp identical to ScheduleWakeup / ADR-0127 §3 (60 s … 3600 s).
LOOP_MIN_INTERVAL_S = 60
LOOP_MAX_INTERVAL_S = 3600


def _extract_interval_s(prompt: str) -> int | None:
    """Return an explicit recurrence interval in seconds, or None (= dynamic).

    Clamped to [LOOP_MIN_INTERVAL_S, LOOP_MAX_INTERVAL_S]. Sub-minute spoken
    intervals round up to the 60 s floor; > 1 h rounds down to the ceiling.
    """
    m = _INTERVAL_RE.search(prompt)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    # Normalise the unit token to a known key (longest-prefix match).
    secs_per = None
    for key, val in sorted(_UNIT_SECONDS.items(), key=lambda kv: -len(kv[0])):
        if unit.startswith(key):
            secs_per = val
            break
    if secs_per is None:
        return None
    total = n * secs_per
    return max(LOOP_MIN_INTERVAL_S, min(LOOP_MAX_INTERVAL_S, total))


def _score(prompt: str, patterns: list[tuple[re.Pattern[str], float]]) -> tuple[float, list[str]]:
    """Sum matched pattern weights (capped 1.0); return (score, matched_srcs)."""
    total = 0.0
    matched: list[str] = []
    for rx, weight in patterns:
        if rx.search(prompt):
            total += weight
            matched.append(rx.pattern)
    return min(1.0, total), matched


def _fake_result() -> dict | None:
    if os.environ.get("EMR_FAKE") != "1":
        return None
    raw = os.environ.get("EMR_FAKE_RESULT", "")
    if not raw:
        return {"mode": MODE_SINGLE, "confidence": 1.0, "why": "EMR_FAKE", "params": {}}
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return {"mode": MODE_SINGLE, "confidence": 0.0, "why": "EMR_FAKE bad json", "params": {}}
    d.setdefault("mode", MODE_SINGLE)
    d.setdefault("confidence", 1.0)
    d.setdefault("why", "EMR_FAKE")
    d.setdefault("params", {})
    return d


def classify(prompt: str, *, persona: str | None = None,
             min_confidence: float = 0.5) -> dict:
    """Classify a prompt into an execution mode (ADR-0127 M1, Tier-1 only).

    Always returns a dict (never None) — the fail-safe is
    ``{"mode": "single", ...}``. ``min_confidence`` is the floor a richer
    mode must clear to win; below it the result is ``single``.
    """
    fake = _fake_result()
    if fake is not None:
        return fake

    # Contract: always return a dict. Coerce non-str defensively (the
    # docstring promises never to raise on odd input).
    text = (prompt if isinstance(prompt, str) else "").strip()
    result_params: dict = {}
    if not text:
        return {"mode": MODE_SINGLE, "confidence": 1.0,
                "why": "empty prompt", "params": result_params}

    scores: dict[str, float] = {}
    whys: dict[str, list[str]] = {}
    for mode, patterns in _MODE_PATTERNS.items():
        sc, matched = _score(text, patterns)
        if sc > 0:
            scores[mode] = sc
            whys[mode] = matched

    if not scores:
        return {"mode": MODE_SINGLE, "confidence": 1.0,
                "why": "no mode pattern matched", "params": result_params}

    # Highest score wins; ties broken by mode strength (compute > workflow >
    # loop) because a compute/workflow shape is a stronger, costlier claim
    # the operator should see proposed over a mere recurrence hint.
    _strength = {MODE_COMPUTE: 3, MODE_WORKFLOW: 2, MODE_LOOP: 1}
    best_mode = max(scores, key=lambda m: (scores[m], _strength[m]))
    best_score = scores[best_mode]

    if best_score < min_confidence:
        # Fall back to single. `confidence` is always the confidence in the
        # RETURNED mode (single here) — not an inverted score. The rejected
        # richer-mode signal is surfaced separately so the info isn't lost.
        return {"mode": MODE_SINGLE, "confidence": 0.5,
                "why": f"weak signal for {best_mode} ({best_score:.2f} < {min_confidence}) → single",
                "params": {"rejected_mode": best_mode,
                           "rejected_score": round(best_score, 3)}}

    # Mode-specific parameter hints (never executed here — pure hints).
    if best_mode == MODE_LOOP:
        interval = _extract_interval_s(text)
        result_params["interval_s"] = interval          # None ⇒ dynamic/self-paced
        result_params["explicit_recurrence"] = interval is not None
    elif best_mode == MODE_COMPUTE:
        # A *sketch* the OS-turn LLM may use when calling compute_run; the
        # bridge never executes it. Surfaces the detected strategy hint.
        strat = "grid"
        if re.search(r"\bbayes", text, re.I):
            strat = "bayesian"
        elif re.search(r"\brandom\b|\bzuf(?:ä|ae)llig\b", text, re.I):
            strat = "random"
        result_params["strategy_hint"] = strat
    elif best_mode == MODE_WORKFLOW:
        result_params["dimensions_hint"] = len(whys.get(MODE_WORKFLOW, []))

    return {
        "mode": best_mode,
        "confidence": round(best_score, 3),
        "why": "matched: " + ", ".join(whys.get(best_mode, [])[:3]),
        "params": result_params,
    }
