"""Weight Convergence Detection — detect when a skill's outcome signal stabilises.

NOT WIRED (verified 2026-09-16): nothing in production constructs this class.
The only references are this file and its tests. It is kept because the
console needs a convergence answer per skill, but do NOT describe it as a
live mechanism until a call site exists — see CLAUDE.md § E2E Wiring Proof.

Reads the SAME store every producer writes to, ``event_store.EventStore``,
whose ``query_events`` is SYNCHRONOUS and filters on an ``EventType`` enum.
The previous version awaited that call and compared ``event_type`` to the
string ``"outcome"``, then ran ``statistics.mean`` over ``event.signal`` —
which is a dict (``{task_id, status, success, exit_code, duration_ms, ...}``)
for every real outcome on disk, never a float. It would have raised on its
first call with production data.
"""

from __future__ import annotations

import statistics
from typing import Any, Optional

from core.learning.learning_events import EventType

# Below this many outcomes a rate is one data point wearing a percentage
# (ADR-0763), so convergence is withheld and the reason is reported.
MIN_SAMPLES = 10
DEFAULT_THRESHOLD = 0.80
WINDOW = 50


def _success_series(events: list[Any]) -> list[float]:
    """1.0 per succeeded outcome, 0.0 per failed one.

    ``signal`` is the dict ``outcome_sink.emit_task_outcome`` writes. A record
    carrying neither key is skipped rather than counted as a success — an
    unreadable outcome is not evidence of a good one.
    """
    series: list[float] = []
    for event in events:
        signal = getattr(event, "signal", None)
        if not isinstance(signal, dict):
            continue
        if "success" in signal:
            series.append(1.0 if signal["success"] else 0.0)
        elif "status" in signal:
            series.append(1.0 if signal["status"] == "completed" else 0.0)
    return series


class WeightConvergence:
    """Detect convergence of a skill's outcome success rate."""

    def __init__(self, event_store: Any) -> None:
        self.event_store = event_store

    def _outcomes(self, skill_id: str, tenant_id: str) -> list[Any]:
        return self.event_store.query_events(
            tenant_id=tenant_id,
            event_type=EventType.OUTCOME,
            skill_id=skill_id,
            limit=WINDOW,
            newest_first=True,
        )

    def detect_convergence(
        self,
        skill_id: str,
        tenant_id: str,
        confidence_threshold: float = DEFAULT_THRESHOLD,
    ) -> bool:
        """True when the last ``WINDOW`` outcomes hold a mean success rate at
        or above ``confidence_threshold``, over at least ``MIN_SAMPLES``."""
        series = _success_series(self._outcomes(skill_id, tenant_id))
        if len(series) < MIN_SAMPLES:
            return False
        return statistics.mean(series) >= confidence_threshold

    def get_convergence_stats(self, skill_id: str, tenant_id: str) -> dict:
        """Convergence detail for the console.

        Always carries ``n_events`` and ``reason`` so a withheld verdict is
        never rendered as a converged one.
        """
        series = _success_series(self._outcomes(skill_id, tenant_id))
        if len(series) < MIN_SAMPLES:
            return {
                "skill_id": skill_id,
                "n_events": len(series),
                "mean_confidence": None,
                "std_dev": None,
                "is_converged": False,
                "reason": f"insufficient samples ({len(series)} < {MIN_SAMPLES})",
            }
        mean = statistics.mean(series)
        return {
            "skill_id": skill_id,
            "n_events": len(series),
            "mean_confidence": mean,
            "std_dev": statistics.stdev(series) if len(series) > 1 else 0.0,
            "is_converged": mean >= DEFAULT_THRESHOLD,
            "reason": "",
        }
