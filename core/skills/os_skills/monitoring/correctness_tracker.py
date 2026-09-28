"""L5 routing correctness — judged on OBSERVABLE outcomes (ADR-2092 G1/G4).

The earlier tracker compared the Skill's route with a ``ground_truth`` engine ("which
engine would have been right"). That value is counterfactual — only one engine runs
per turn — so nothing in production could ever supply it, and the tracker never had a
sample. This version judges what is observable: did the turn the router served
succeed? Turns routed by the Skill (``source == "skill"``) are compared with turns
routed by the bundled rule (``source == "bundled"``) over the same window.

Pure functions over ledger rows; no process state, so every process reading the same
tenant ledger reaches the same verdict.
"""
from __future__ import annotations

import dataclasses
from typing import Iterable, Optional

#: Neither side is judged below this many joined outcomes.
MIN_SAMPLES = 100
#: Skill-routed success may trail bundled-routed success by at most this much.
MAX_DROP = 0.02
#: Only the most recent outcomes per source count.
WINDOW = 1000


@dataclasses.dataclass(frozen=True)
class CorrectnessMetrics:
    skill_n: int
    skill_success_rate: Optional[float]
    bundled_n: int
    bundled_success_rate: Optional[float]
    drop: Optional[float]
    judgeable: bool


def compute_metrics(joined_rows: Iterable[dict], *, window: int = WINDOW,
                    min_samples: int = MIN_SAMPLES) -> CorrectnessMetrics:
    rows = sorted(
        (r for r in joined_rows if r.get("outcome") is not None),
        key=lambda r: r.get("ts", 0.0),
    )
    per: dict[str, list[bool]] = {"skill": [], "bundled": []}
    for r in rows:
        src = r.get("source")
        if src in per:
            per[src].append(bool(r["outcome"].get("ok")))
    skill = per["skill"][-window:]
    bundled = per["bundled"][-window:]
    s_rate = (sum(skill) / len(skill)) if skill else None
    b_rate = (sum(bundled) / len(bundled)) if bundled else None
    judgeable = len(skill) >= min_samples and len(bundled) >= min_samples
    drop = (b_rate - s_rate) if (s_rate is not None and b_rate is not None) else None
    return CorrectnessMetrics(
        skill_n=len(skill),
        skill_success_rate=s_rate,
        bundled_n=len(bundled),
        bundled_success_rate=b_rate,
        drop=drop,
        judgeable=judgeable,
    )


def should_rollback(metrics: CorrectnessMetrics, *, max_drop: float = MAX_DROP) -> bool:
    """True only on a JUDGEABLE window where the Skill trails by more than ``max_drop``."""
    return bool(metrics.judgeable and metrics.drop is not None and metrics.drop > max_drop)
