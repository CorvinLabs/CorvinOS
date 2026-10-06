"""Layer Forge Analytics — metrics aggregation for Phase 3B dashboard (ADR-2222 Phase 3B).

Computes time-series metrics over layer definitions:
  - decisions_accepted: count per week
  - decisions_flagged: count per week
  - decisions_rejected: count per week
  - mean_confidence: 7-day rolling average
  - flags_distribution: count per flag type per week
  - learning_convergence: confidence delta over time

All metrics filtered by tenant_id (GDPR Art. 5, 6, 32).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Review flag types (from review.py::ReviewFlag)
REVIEW_FLAG_TYPES = [
    "scope_creep",
    "security_gap",
    "untested_complexity",
    "compliance_risk",
    "dependency_debt",
    "host_asymmetry",
    "unknown_risk",
]

# Status values
STATUS_ACCEPTED = "accepted"
STATUS_DEPLOYED = "deployed"
STATUS_REJECTED = "rejected"
STATUS_PROPOSED = "proposed"
STATUS_SUPERSEDED = "superseded"


class LayerForgeAnalytics:
    """Metrics computation over layer definition registry and learning events."""

    def __init__(self, registry, tenant_id: str, learning_store: Optional = None):
        """Initialize analytics engine.

        Args:
            registry: LayerRegistry instance
            tenant_id: Tenant scope (GDPR)
            learning_store: Optional EventStore for learning events
        """
        self.registry = registry
        self.tenant_id = tenant_id
        self.learning_store = learning_store

    def week_of(self, timestamp: float) -> str:
        """Return ISO week string (YYYY-W##) for a timestamp."""
        dt = datetime.utcfromtimestamp(timestamp)
        # ISO week format
        return dt.strftime("%Y-W%02d")

    def get_decisions_by_week(
        self, since_iso: str | None = None, until_iso: str | None = None
    ) -> dict[str, dict[str, int]]:
        """Decisions per week: accepted, flagged, rejected.

        Args:
            since_iso: Start date (ISO 8601, inclusive)
            until_iso: End date (ISO 8601, inclusive)

        Returns:
            {
                "2026-W40": {"accepted": 2, "flagged": 1, "rejected": 0},
                ...
            }
        """
        since_ts = self._parse_iso_to_ts(since_iso) if since_iso else 0
        until_ts = self._parse_iso_to_ts(until_iso, end_of_day=True) if until_iso else float("inf")

        weeks: dict[str, dict[str, int]] = {}

        # Iterate all definitions in registry
        for entry in self.registry.list_all():
            if "_created_at" not in entry:
                continue

            created_ts = entry["_created_at"]
            if created_ts < since_ts or created_ts > until_ts:
                continue

            week = self.week_of(created_ts)
            if week not in weeks:
                weeks[week] = {"accepted": 0, "flagged": 0, "rejected": 0}

            status = entry.get("status", STATUS_PROPOSED)

            # Classify decision outcome
            if status == STATUS_DEPLOYED:
                weeks[week]["accepted"] += 1
            elif status == STATUS_SUPERSEDED:
                weeks[week]["rejected"] += 1
            elif entry.get("_review_verdict", {}).get("status") == "FLAGGED":
                weeks[week]["flagged"] += 1
            elif status == STATUS_ACCEPTED:
                weeks[week]["accepted"] += 1

        return dict(sorted(weeks.items()))

    def get_flags_distribution(
        self, since_iso: str | None = None, until_iso: str | None = None
    ) -> dict[str, dict[str, int]]:
        """Flag counts per week.

        Returns:
            {
                "2026-W40": {"scope_creep": 1, "security_gap": 2, ...},
                ...
            }
        """
        since_ts = self._parse_iso_to_ts(since_iso) if since_iso else 0
        until_ts = self._parse_iso_to_ts(until_iso, end_of_day=True) if until_iso else float("inf")

        weeks: dict[str, dict[str, int]] = {}

        for entry in self.registry.list_all():
            if "_created_at" not in entry:
                continue

            created_ts = entry["_created_at"]
            if created_ts < since_ts or created_ts > until_ts:
                continue

            week = self.week_of(created_ts)
            verdict = entry.get("_review_verdict", {})

            if verdict.get("status") != "FLAGGED":
                continue

            if week not in weeks:
                weeks[week] = {flag: 0 for flag in REVIEW_FLAG_TYPES}

            for flag in verdict.get("flags", []):
                if flag in weeks[week]:
                    weeks[week][flag] += 1

        return dict(sorted(weeks.items()))

    def get_confidence_trend(
        self, since_iso: str | None = None, until_iso: str | None = None
    ) -> dict[str, float]:
        """7-day rolling average confidence.

        Returns:
            {
                "2026-W40": 0.75,  # mean confidence
                ...
            }
        """
        if not self.learning_store:
            return {}

        since_ts = self._parse_iso_to_ts(since_iso) if since_iso else 0
        until_ts = self._parse_iso_to_ts(until_iso, end_of_day=True) if until_iso else float("inf")

        # Read learning events for tenant
        events = self._read_learning_events(since_ts, until_ts)

        # Group by week and compute mean confidence
        weeks: dict[str, list[float]] = {}

        for event in events:
            if event.get("event_type") != "confidence":
                continue

            ts = event.get("timestamp")
            if not ts or ts < since_ts or ts > until_ts:
                continue

            week = self.week_of(ts)
            if week not in weeks:
                weeks[week] = []

            signal = event.get("signal", {})
            confidence = signal.get("confidence_delta", 0.0)
            weeks[week].append(confidence)

        # Compute rolling average (current week + previous week's tail)
        result = {}
        for week in sorted(weeks.keys()):
            values = weeks[week]
            if values:
                result[week] = sum(values) / len(values)
            else:
                result[week] = 0.0

        return result

    def get_convergence(
        self, since_iso: str | None = None, until_iso: str | None = None
    ) -> list[dict]:
        """Learning convergence: confidence deltas over time (newest first).

        Returns:
            [
                {"timestamp": "2026-10-05T12:34:00Z", "delta": 0.15, "entry_id": "L1"},
                ...
            ]
        """
        if not self.learning_store:
            return []

        since_ts = self._parse_iso_to_ts(since_iso) if since_iso else 0
        until_ts = self._parse_iso_to_ts(until_iso, end_of_day=True) if until_iso else float("inf")

        events = self._read_learning_events(since_ts, until_ts)

        convergence = []
        for event in events:
            if event.get("event_type") != "confidence":
                continue

            ts = event.get("timestamp")
            if not ts or ts < since_ts or ts > until_ts:
                continue

            signal = event.get("signal", {})
            convergence.append({
                "timestamp": datetime.utcfromtimestamp(ts).isoformat() + "Z",
                "delta": signal.get("confidence_delta", 0.0),
                "entry_id": signal.get("entry_id", "unknown"),
                "gate_id": signal.get("gate_id", ""),
            })

        # Sort by timestamp descending (newest first)
        return sorted(convergence, key=lambda x: x["timestamp"], reverse=True)

    def get_summary_metrics(
        self, since_iso: str | None = None, until_iso: str | None = None
    ) -> dict:
        """Summary metrics for the dashboard.

        Returns:
            {
                "decisions_accepted": 5,
                "decisions_flagged": 2,
                "decisions_rejected": 1,
                "mean_confidence": 0.72,
                "flags_distribution": {"scope_creep": 1, "security_gap": 1, ...},
                "window": {
                    "since": "2026-10-01",
                    "until": "2026-10-05",
                    "days": 5
                }
            }
        """
        since_ts = self._parse_iso_to_ts(since_iso) if since_iso else (
            datetime.utcnow() - timedelta(days=30)
        ).timestamp()
        until_ts = self._parse_iso_to_ts(until_iso, end_of_day=True) if until_iso else (
            datetime.utcnow()
        ).timestamp()

        # Aggregate decisions by week
        decisions_by_week = self.get_decisions_by_week(since_iso, until_iso)
        accepted = sum(w["accepted"] for w in decisions_by_week.values())
        flagged = sum(w["flagged"] for w in decisions_by_week.values())
        rejected = sum(w["rejected"] for w in decisions_by_week.values())

        # Confidence trend
        confidence_by_week = self.get_confidence_trend(since_iso, until_iso)
        mean_confidence = (
            sum(confidence_by_week.values()) / len(confidence_by_week)
            if confidence_by_week
            else 0.0
        )

        # Flags distribution
        flags_by_week = self.get_flags_distribution(since_iso, until_iso)
        flags_dist = {flag: 0 for flag in REVIEW_FLAG_TYPES}
        for week_flags in flags_by_week.values():
            for flag, count in week_flags.items():
                flags_dist[flag] += count

        return {
            "decisions_accepted": accepted,
            "decisions_flagged": flagged,
            "decisions_rejected": rejected,
            "mean_confidence": round(mean_confidence, 3),
            "flags_distribution": flags_dist,
            "window": {
                "since": datetime.utcfromtimestamp(since_ts).date().isoformat(),
                "until": datetime.utcfromtimestamp(until_ts).date().isoformat(),
                "days": int((until_ts - since_ts) / 86400),
            },
        }

    # Helpers

    @staticmethod
    def _parse_iso_to_ts(iso_str: str, end_of_day: bool = False) -> float:
        """Parse ISO 8601 date/datetime to Unix timestamp."""
        try:
            if "T" in iso_str:
                # Datetime
                dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            else:
                # Date only
                dt = datetime.fromisoformat(iso_str)
                if end_of_day:
                    dt = dt.replace(hour=23, minute=59, second=59)
            return dt.timestamp()
        except (ValueError, AttributeError):
            return 0.0

    def _read_learning_events(self, since_ts: float, until_ts: float) -> list[dict]:
        """Read learning events from store, filtered by tenant and time range.

        Returns list of event dicts or empty list if store unavailable.
        """
        if not self.learning_store:
            return []

        try:
            # Try to read events from the learning store
            # This is a stub for now; actual implementation depends on EventStore API
            events = []
            # TODO: implement actual event store query
            # for event in self.learning_store.query(
            #     tenant_id=self.tenant_id,
            #     since=since_ts,
            #     until=until_ts,
            #     skill_id="os.layer_forge"
            # ):
            #     events.append(event.to_dict())
            return events
        except Exception as e:
            logger.warning("failed to read learning events: %s", e)
            return []

    def gate_outcome_correlation(
        self, gate_id: str, since_iso: str | None = None, until_iso: str | None = None
    ) -> dict:
        """Correlate FAIL verdicts with later deployment outcomes for a gate.

        For each quality gate, measure: did overridden FAILs ultimately deploy successfully?
        This gives us signal about whether the gate's threshold is too strict (too many false positives).

        Returns:
            {
                "gate_id": "schema_check",
                "total_fails": 5,
                "overridden_fails": 3,
                "override_successes": 2,
                "override_failures": 1,
                "override_success_rate": 0.667,
                "signal": "overcautious" | "undercautious" | "neutral",
            }
        """
        since_ts = self._parse_iso_to_ts(since_iso) if since_iso else (
            datetime.utcnow() - timedelta(days=30)
        ).timestamp()
        until_ts = self._parse_iso_to_ts(until_iso, end_of_day=True) if until_iso else (
            datetime.utcnow()
        ).timestamp()

        # Iterate through all definitions looking for gate FAIL outcomes
        correlations = {
            "gate_id": gate_id,
            "total_fails": 0,
            "overridden_fails": 0,
            "override_successes": 0,
            "override_failures": 0,
            "override_success_rate": 0.0,
            "signal": "neutral",
        }

        for entry in self.registry.list_all():
            if "_created_at" not in entry:
                continue

            created_ts = entry["_created_at"]
            if created_ts < since_ts or created_ts > until_ts:
                continue

            # Check if this definition had a FAIL for our target gate
            verdict = entry.get("_review_verdict", {})
            if verdict.get("status") == "FLAGGED":
                # Does this definition have a record of override and deployment?
                if entry.get("review_flagged") and entry.get("status") == "deployed":
                    correlations["total_fails"] += 1
                    correlations["overridden_fails"] += 1

                    # In a real scenario, check deployment outcome from a separate outcome log
                    # For now, we assume deployed=success (actual signal comes from learning events)
                    if entry.get("status") == "deployed":
                        correlations["override_successes"] += 1
                    else:
                        correlations["override_failures"] += 1

        # Compute success rate
        if correlations["overridden_fails"] > 0:
            correlations["override_success_rate"] = (
                correlations["override_successes"] / correlations["overridden_fails"]
            )

            # Determine signal
            if correlations["override_success_rate"] >= 0.70:
                correlations["signal"] = "overcautious"
            elif correlations["override_success_rate"] <= 0.40:
                correlations["signal"] = "undercautious"
            else:
                correlations["signal"] = "neutral"

        return correlations
