"""Shared stage helpers (moved from pipeline.py so stages + runner share them
without a cycle). Behaviour identical to the pre-P-A build_brief (ADR-0280 parity).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def confidence_tier(score: float) -> str:
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def avg(scores: list) -> float:
    return sum(scores) / len(scores) if scores else 0.0


def task_adapter(task: str) -> Any:
    """EnrichedTask-shaped view the stages read (.normalized.summary / .key_terms)."""
    words = [w.strip(".,!?;:()[]\"'").lower() for w in task.split() if len(w) > 3]
    return SimpleNamespace(
        normalized=SimpleNamespace(summary=task),
        key_terms=words[:12],
        validated=SimpleNamespace(task=task),
        task_complexity="moderate",
    )


_BLOCKER_SIGNALS = (
    "must not", "must-not", "fail-closed", "fail closed", "load-bearing",
    "load bearing", "blocker", "constraint", "deprecated", "do not", "don't",
    "never ", "irreversible", "locked", "blocked", "breaking change",
)


def scan_blockers(brief: Any) -> list:
    out: list[str] = []
    mc = getattr(brief, "memory_context", None)
    for m in (getattr(mc, "matches", []) if mc else []):
        hay = f"{getattr(m, 'title', '')} {getattr(m, 'content_preview', '')}".lower()
        if any(sig in hay for sig in _BLOCKER_SIGNALS):
            out.append(getattr(m, "title", None) or getattr(m, "filename", "?"))
    for d in (getattr(brief, "related_decisions", None) or []):
        if any(sig in (getattr(d, "title", "") or "").lower() for sig in _BLOCKER_SIGNALS):
            out.append(getattr(d, "decision_id", "?"))
    seen: set = set()
    return [x for x in out if not (x in seen or seen.add(x))][:5]
