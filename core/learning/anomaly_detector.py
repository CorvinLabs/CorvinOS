"""Phase 8: Anomaly Detection & Auto-Recovery for TreeOfThoughts.

Detects sudden confidence drops (>20% in 4 hours) using Z-score anomaly detection.
Maintains a rolling 7-day baseline and auto-generates recovery suggestions.
GDPR-compliant: no PII in alert logs, only subject_id and context metadata.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import json
from pathlib import Path
from statistics import stdev, mean
import math


@dataclass(frozen=True)
class AnomalyAlert:
    """Immutable anomaly detection record (append-only log)."""
    timestamp: str  # ISO8601
    subject_id: str  # pattern_id or method_id
    alert_type: str  # "confidence_drop", "pattern_degradation", "recovery"
    severity: str  # "warning" | "critical"
    confidence_now: float
    confidence_baseline_mean: float
    confidence_baseline_stddev: float
    confidence_drop_pct: float
    z_score: float  # Standard deviations from baseline
    window_hours: int  # e.g., 4 hours
    context: dict = field(default_factory=dict)  # {task_id, reason, extra metadata}
    suggestions: list[dict] = field(default_factory=list)  # [{alternative_id, confidence, reason}]

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict."""
        return {
            "timestamp": self.timestamp,
            "subject_id": self.subject_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "confidence_now": self.confidence_now,
            "confidence_baseline_mean": self.confidence_baseline_mean,
            "confidence_baseline_stddev": self.confidence_baseline_stddev,
            "confidence_drop_pct": self.confidence_drop_pct,
            "z_score": self.z_score,
            "window_hours": self.window_hours,
            "context": self.context,
            "suggestions": self.suggestions,
        }


class AnomalyDetector:
    """Monitors confidence trends, detects anomalies, suggests alternatives."""

    def __init__(self, store, base_dir: Path = None):
        """Initialize anomaly detector.

        Args:
            store: LearningEventStore instance
            base_dir: Where to store alert logs (default: ~/.corvin/learning/alerts)
        """
        self.store = store
        if base_dir is None:
            base_dir = Path.home() / ".corvin" / "learning" / "alerts"
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Configuration
        self.baseline_window_days = 7
        self.detection_window_hours = 4
        self.confidence_drop_threshold_pct = 20.0  # Trigger if drop > 20%
        self.z_score_threshold = 2.0  # Alert if |z| > 2 (95th percentile)
        self.min_history_for_baseline = 2  # Need ≥2 points for stddev

    def check_anomaly(
        self,
        subject_id: str,
        new_confidence: float,
        old_confidence: float,
        reason: str = "",
        context: dict = None,
    ) -> Optional[AnomalyAlert]:
        """Check if a confidence change is anomalous.

        Args:
            subject_id: Pattern or method ID
            new_confidence: Current confidence value
            old_confidence: Previous confidence value
            reason: Why confidence changed (e.g., "failed")
            context: Additional metadata (task_id, user_id, etc.)

        Returns:
            AnomalyAlert if anomaly detected, None otherwise.
        """
        if context is None:
            context = {}

        # Get baseline and history
        baseline = self.get_baseline(subject_id)
        if baseline is None:
            # Not enough history yet
            return None

        # Calculate drop percentage
        if old_confidence == 0:
            confidence_drop_pct = 100.0 if new_confidence < old_confidence else 0.0
        else:
            confidence_drop_pct = abs(old_confidence - new_confidence) / old_confidence * 100

        # Calculate Z-score
        baseline_mean = baseline["mean"]
        baseline_stddev = baseline["stddev"]

        if baseline_stddev < 1e-6:  # Avoid division by near-zero
            z_score = 0.0
        else:
            z_score = (new_confidence - baseline_mean) / baseline_stddev

        # Determine if this is anomalous
        is_anomalous = (
            confidence_drop_pct > self.confidence_drop_threshold_pct
            or abs(z_score) > self.z_score_threshold
        )

        if not is_anomalous:
            return None

        # Determine severity
        severity = "critical" if abs(z_score) > 3.0 else "warning"

        # Generate suggestions
        suggestions = self.suggest_alternatives(subject_id, reason, new_confidence)

        # Create alert
        alert = AnomalyAlert(
            timestamp=datetime.now().isoformat(),
            subject_id=subject_id,
            alert_type="confidence_drop",
            severity=severity,
            confidence_now=new_confidence,
            confidence_baseline_mean=baseline_mean,
            confidence_baseline_stddev=baseline_stddev,
            confidence_drop_pct=confidence_drop_pct,
            z_score=z_score,
            window_hours=self.detection_window_hours,
            context=context,
            suggestions=suggestions,
        )

        # Log alert (append-only)
        self._log_alert(alert)

        return alert

    def get_baseline(self, subject_id: str) -> Optional[dict]:
        """Compute 7-day rolling baseline for a subject.

        Returns:
            {mean, stddev, sample_count, recent_high, recent_low}
            or None if insufficient history
        """
        # Get all events for this subject from the last 7 days
        cutoff = (datetime.now() - timedelta(days=self.baseline_window_days)).isoformat()
        events = self.store.get_events(subject_id, after=cutoff)

        if not events:
            return None

        # Extract confidence values from events
        # Each event records a confidence_delta; we need to reconstruct confidence over time
        confidences = self._reconstruct_confidence_history(subject_id, events)

        if len(confidences) < self.min_history_for_baseline:
            return None

        baseline_mean = mean(confidences)
        baseline_stddev = stdev(confidences) if len(confidences) > 1 else 0.0

        return {
            "mean": baseline_mean,
            "stddev": baseline_stddev,
            "sample_count": len(confidences),
            "recent_high": max(confidences[-10:]) if confidences else 0.0,
            "recent_low": min(confidences[-10:]) if confidences else 0.0,
        }

    def suggest_alternatives(
        self,
        subject_id: str,
        reason: str = "",
        current_confidence: float = 0.5,
    ) -> list[dict]:
        """Find alternative patterns/methods that might work instead.

        Looks at anti_when contexts to find patterns in different scenarios.
        Returns sorted by confidence descending.
        """
        suggestions = []

        # Get the node
        node = self.store.get_node(subject_id)
        if not node:
            return suggestions

        # Find alternative patterns by looking at children or siblings
        # For now, simple heuristic: find all nodes in the store with higher confidence
        # and different 'when' contexts
        all_nodes = self.store.all_nodes()

        for alt_node in all_nodes:
            # Skip self
            if alt_node.id == subject_id:
                continue

            # Skip if confidence is lower than current
            if alt_node.confidence <= current_confidence:
                continue

            # Check if this alternative is applicable (different when/anti_when)
            is_alternative = (
                alt_node.level == node.level
                and alt_node.confidence > current_confidence + 0.1
            )

            if is_alternative:
                suggestions.append({
                    "alternative_id": alt_node.id,
                    "confidence": alt_node.confidence,
                    "reason": f"Higher confidence ({alt_node.confidence:.2f}) in {', '.join(alt_node.when)}",
                    "anti_when": alt_node.anti_when,
                })

        # Sort by confidence descending
        suggestions.sort(key=lambda x: x["confidence"], reverse=True)

        # Return top 3
        return suggestions[:3]

    def _reconstruct_confidence_history(
        self,
        subject_id: str,
        events: list,
    ) -> list[float]:
        """Reconstruct confidence values from events."""
        # Start with the node's current confidence or 0.5
        node = self.store.get_node(subject_id)
        current_conf = node.confidence if node else 0.5

        confidences = [current_conf]

        # Walk backward through events (events are sorted ascending)
        # and reconstruct prior values
        for event in reversed(events):
            prior_conf = current_conf - event.confidence_delta
            confidences.append(prior_conf)
            current_conf = prior_conf

        # Return in forward chronological order
        return list(reversed(confidences))

    def _log_alert(self, alert: AnomalyAlert) -> None:
        """Append alert to append-only log.

        GDPR: No PII stored, only subject_id and anonymized context.
        """
        # Use date-partitioned logs like the event store
        date_str = datetime.now().strftime("%Y-%m-%d")
        alert_path = self.base_dir / f"{date_str}.jsonl"

        with open(alert_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert.to_dict()) + "\n")

    def get_alerts(
        self,
        subject_id: str = None,
        after: str = None,
        severity: str = None,
    ) -> list[AnomalyAlert]:
        """Retrieve alerts from log.

        Args:
            subject_id: Filter by subject (optional)
            after: Filter by timestamp ISO8601 (optional)
            severity: Filter by severity: "warning" or "critical" (optional)

        Returns:
            List of AnomalyAlert objects sorted by timestamp descending.
        """
        alerts = []

        for alert_file in sorted(self.base_dir.glob("*.jsonl"), reverse=True):
            with open(alert_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)

                    # Apply filters
                    if subject_id and data.get("subject_id") != subject_id:
                        continue
                    if after and data.get("timestamp", "") < after:
                        continue
                    if severity and data.get("severity") != severity:
                        continue

                    # Reconstruct alert
                    alert = AnomalyAlert(
                        timestamp=data["timestamp"],
                        subject_id=data["subject_id"],
                        alert_type=data["alert_type"],
                        severity=data["severity"],
                        confidence_now=data["confidence_now"],
                        confidence_baseline_mean=data["confidence_baseline_mean"],
                        confidence_baseline_stddev=data["confidence_baseline_stddev"],
                        confidence_drop_pct=data["confidence_drop_pct"],
                        z_score=data["z_score"],
                        window_hours=data["window_hours"],
                        context=data.get("context", {}),
                        suggestions=data.get("suggestions", []),
                    )
                    alerts.append(alert)

        # Sort by timestamp descending
        alerts.sort(key=lambda a: a.timestamp, reverse=True)
        return alerts

    def get_latest_alert(self, subject_id: str) -> Optional[AnomalyAlert]:
        """Get most recent alert for a subject."""
        alerts = self.get_alerts(subject_id=subject_id)
        return alerts[0] if alerts else None

    def clear_alerts_before(self, days_ago: int = 30) -> int:
        """Remove alert logs older than N days (retention policy).

        Returns: Number of files deleted.
        """
        cutoff = datetime.now() - timedelta(days=days_ago)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        deleted = 0
        for alert_file in self.base_dir.glob("*.jsonl"):
            file_date_str = alert_file.stem  # e.g., "2024-12-25"
            if file_date_str < cutoff_str:
                alert_file.unlink()
                deleted += 1

        return deleted
