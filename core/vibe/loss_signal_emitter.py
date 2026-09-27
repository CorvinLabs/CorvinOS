"""
Loss Signal Emitter — Real-time alert generation for production monitoring.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — the only
importer is core/console/routes/learning_dashboard.py, which no app mounts.

Detects and emits four critical loss signals:
  1. Latency regression (p99 spike >20%) → escalate or rollback
  2. Confidence decline (7-day slope <-0.05) → flag retraining
  3. Feedback negative (<70% thumbs up) → investigate sentiment
  4. A/B test regression (CI crosses zero) → halt + revert

Audit-first design: every signal emission is logged to the core audit chain.
Fail-closed: if audit fails, RuntimeError is raised; signal is not emitted.

References: ADR-0722 (loss signals), ADR-0232 (audit).
"""

from dataclasses import dataclass, field, asdict
from collections import deque
from typing import Optional, Dict, List, Any, Protocol, Deque
from datetime import datetime, timedelta
from enum import Enum
import math
import logging

_MAX_SIGNALS = 10_000

logger = logging.getLogger(__name__)


class SignalSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class LossSignal:
    """Base class for all loss signals."""
    signal_type: str
    timestamp: str
    severity: SignalSeverity
    metric_name: str
    current_value: float
    threshold: float
    deviation_pct: float  # How much over threshold (%)
    tenant_id: str
    recommendation: str

    # Audit chain
    hash: Optional[str] = None
    prev_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to audit-serializable dict."""
        return {
            "signal_type": self.signal_type,
            "timestamp": self.timestamp,
            "severity": self.severity.value,
            "metric_name": self.metric_name,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "deviation_pct": self.deviation_pct,
            "tenant_id": self.tenant_id,
            "recommendation": self.recommendation,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
        }


@dataclass
class LatencySignal(LossSignal):
    """Latency regression signal (p99 spike >20%)."""
    p99_baseline: float = 0.0  # Baseline p99 in ms
    p99_current: float = 0.0   # Current p99 in ms
    sample_size: int = 0       # Number of samples in window


@dataclass
class ConfidenceSignal(LossSignal):
    """Confidence decline signal (7-day slope <-0.05)."""
    slope_7d: float = 0.0              # 7-day slope
    confidence_current: float = 0.0    # Current confidence (0-1)
    confidence_target: float = 0.9     # Target confidence
    samples: int = 0                   # Number of observations


@dataclass
class FeedbackSignal(LossSignal):
    """Negative feedback signal (<70% thumbs up)."""
    thumbs_up_pct: float = 0.0    # Percentage of positive feedback
    thumbs_down_pct: float = 0.0  # Percentage of negative feedback
    thumbs_neutral_pct: float = 0.0  # Percentage of neutral feedback
    total_samples: int = 0        # Total feedback count
    sentiment_trend: str = ""     # "declining" | "stable" | "improving"


@dataclass
class ABTestSignal(LossSignal):
    """A/B test regression signal (CI crosses zero)."""
    control_mean: float = 0.0     # Control variant mean
    variant_mean: float = 0.0     # Variant mean
    ci_lower: float = 0.0         # Confidence interval lower bound
    ci_upper: float = 0.0         # Confidence interval upper bound
    ci_crosses_zero: bool = False # True if CI crosses zero
    recommendation: str = "halt_variant_halt_revert"


class AuditBackend(Protocol):
    """Audit chain interface."""
    def write_event(self, event: Dict[str, Any]) -> Optional[str]: ...
    def last_hash(self) -> str: ...


class LossSignalEmitter:
    """Emits production loss signals with audit trail integration."""

    def __init__(self, audit_backend: Optional[AuditBackend] = None):
        """Initialize emitter.

        Args:
            audit_backend: Audit chain backend. If None, uses NoOpAudit (fail-closed for tests).
        """
        self.audit_backend = audit_backend or _default_audit()
        # Bounded: an in-process view, never an unbounded store.
        self.signals: Deque[LossSignal] = deque(maxlen=_MAX_SIGNALS)
        self.alerts_by_type: Dict[str, Deque[LossSignal]] = {
            k: deque(maxlen=_MAX_SIGNALS) for k in ("latency", "confidence", "feedback", "ab_test")
        }

    def emit_latency_signal(
        self,
        tenant_id: str,
        p99_baseline: float,
        p99_current: float,
        threshold_pct: float = 20.0,
    ) -> Optional[LatencySignal]:
        """Detect and emit latency regression signal.

        Args:
            tenant_id: Tenant ID
            p99_baseline: Baseline p99 latency (ms)
            p99_current: Current p99 latency (ms)
            threshold_pct: Deviation threshold (default 20%)

        Returns:
            LatencySignal if regression detected, None otherwise.
        """
        if p99_baseline <= 0:
            return None

        deviation_pct = ((p99_current - p99_baseline) / p99_baseline) * 100

        if deviation_pct >= threshold_pct:
            signal = LatencySignal(
                signal_type="latency_regression",
                timestamp=datetime.utcnow().isoformat() + "Z",
                severity=SignalSeverity.CRITICAL if deviation_pct >= 50 else SignalSeverity.WARNING,
                metric_name="p99_latency_ms",
                current_value=p99_current,
                threshold=p99_baseline * (1 + threshold_pct / 100),
                deviation_pct=deviation_pct,
                tenant_id=tenant_id,
                p99_baseline=p99_baseline,
                p99_current=p99_current,
                recommendation="escalate_or_rollback",
            )
            return self._register_signal(signal)

        return None

    def emit_confidence_signal(
        self,
        tenant_id: str,
        confidence_timeseries: List[float],
        threshold_slope: float = -0.05,
        target_confidence: float = 0.90,
    ) -> Optional[ConfidenceSignal]:
        """Detect and emit confidence decline signal.

        Computes 7-day slope and flags if declining below threshold.

        Args:
            tenant_id: Tenant ID
            confidence_timeseries: List of confidence values (7+ samples)
            threshold_slope: Decline threshold (default -0.05 per day)
            target_confidence: Target confidence level (default 0.90)

        Returns:
            ConfidenceSignal if decline detected, None otherwise.
        """
        if len(confidence_timeseries) < 7:
            return None

        # Compute linear regression slope (7-day window)
        n = len(confidence_timeseries)
        x = list(range(n))
        y = confidence_timeseries

        x_mean = sum(x) / n
        y_mean = sum(y) / n

        numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return None

        slope = numerator / denominator

        # Flag if slope is below threshold (declining) AND current confidence is below target
        current_confidence = confidence_timeseries[-1]

        if slope < threshold_slope and current_confidence < target_confidence:
            signal = ConfidenceSignal(
                signal_type="confidence_decline",
                timestamp=datetime.utcnow().isoformat() + "Z",
                severity=SignalSeverity.WARNING,
                metric_name="confidence_7day_slope",
                current_value=slope,
                threshold=threshold_slope,
                deviation_pct=abs(slope - threshold_slope) * 100,
                tenant_id=tenant_id,
                slope_7d=slope,
                confidence_current=current_confidence,
                confidence_target=target_confidence,
                samples=n,
                recommendation="flag_retraining",
            )
            return self._register_signal(signal)

        return None

    def emit_feedback_signal(
        self,
        tenant_id: str,
        thumbs_up: int,
        thumbs_down: int,
        thumbs_neutral: int = 0,
        threshold_pct: float = 70.0,
    ) -> Optional[FeedbackSignal]:
        """Detect and emit negative feedback signal.

        Args:
            tenant_id: Tenant ID
            thumbs_up: Count of positive feedback
            thumbs_down: Count of negative feedback
            thumbs_neutral: Count of neutral feedback (default 0)
            threshold_pct: Positive feedback threshold (default 70%)

        Returns:
            FeedbackSignal if negative trend detected, None otherwise.
        """
        total = thumbs_up + thumbs_down + thumbs_neutral
        if total == 0:
            return None

        thumbs_up_pct = (thumbs_up / total) * 100

        if thumbs_up_pct < threshold_pct:
            thumbs_down_pct = (thumbs_down / total) * 100
            thumbs_neutral_pct = (thumbs_neutral / total) * 100

            # Determine trend direction
            sentiment_trend = "declining" if thumbs_down_pct > 20 else "stable"

            signal = FeedbackSignal(
                signal_type="feedback_negative",
                timestamp=datetime.utcnow().isoformat() + "Z",
                severity=SignalSeverity.WARNING,
                metric_name="thumbs_up_percentage",
                current_value=thumbs_up_pct,
                threshold=threshold_pct,
                deviation_pct=threshold_pct - thumbs_up_pct,
                tenant_id=tenant_id,
                thumbs_up_pct=thumbs_up_pct,
                thumbs_down_pct=thumbs_down_pct,
                thumbs_neutral_pct=thumbs_neutral_pct,
                total_samples=total,
                sentiment_trend=sentiment_trend,
                recommendation="investigate_sentiment",
            )
            return self._register_signal(signal)

        return None

    def emit_ab_test_signal(
        self,
        tenant_id: str,
        control_mean: float,
        variant_mean: float,
        ci_lower: float,
        ci_upper: float,
    ) -> Optional[ABTestSignal]:
        """Detect and emit A/B test regression signal.

        Signals when confidence interval crosses zero (no significant difference).

        Args:
            tenant_id: Tenant ID
            control_mean: Control variant mean
            variant_mean: Variant mean
            ci_lower: Confidence interval lower bound
            ci_upper: Confidence interval upper bound

        Returns:
            ABTestSignal if regression detected (CI crosses zero), None otherwise.
        """
        ci_crosses_zero = ci_lower <= 0 <= ci_upper

        if ci_crosses_zero:
            signal = ABTestSignal(
                signal_type="ab_test_regression",
                timestamp=datetime.utcnow().isoformat() + "Z",
                severity=SignalSeverity.CRITICAL,
                metric_name="ab_test_ci",
                current_value=variant_mean - control_mean,
                threshold=0.0,  # Threshold is zero (no effect)
                deviation_pct=0.0,  # Not applicable for CI regression
                tenant_id=tenant_id,
                control_mean=control_mean,
                variant_mean=variant_mean,
                ci_lower=ci_lower,
                ci_upper=ci_upper,
                ci_crosses_zero=True,
                recommendation="halt_variant_halt_revert",
            )
            return self._register_signal(signal)

        return None

    def _register_signal(self, signal: LossSignal) -> LossSignal:
        """Register signal, emit audit event, return signal.

        Fail-closed: if audit write fails, RuntimeError is raised.
        """
        # Write to audit chain FIRST (fail-closed)
        prev_hash = self.audit_backend.last_hash()
        audit_event = {
            "event_type": f"loss_signal_{signal.signal_type}",
            "timestamp": signal.timestamp,
            "tenant_id": signal.tenant_id,
            "severity": signal.severity.value,
            "metric_name": signal.metric_name,
            "current_value": signal.current_value,
            "threshold": signal.threshold,
            "deviation_pct": signal.deviation_pct,
            "recommendation": signal.recommendation,
            "prev_hash": prev_hash,
        }

        try:
            new_hash = self.audit_backend.write_event(audit_event)
            if not new_hash:
                raise RuntimeError(f"Audit write failed for {signal.signal_type}")

            signal.hash = new_hash
            signal.prev_hash = prev_hash
        except Exception as e:
            logger.error(f"Audit write failed for {signal.signal_type}: {e}")
            raise RuntimeError(f"Loss signal audit write failed: {e}") from e

        # Register in memory
        self.signals.append(signal)
        signal_key = signal.signal_type.split("_")[0]  # "latency", "confidence", etc.
        if signal_key in self.alerts_by_type:
            self.alerts_by_type[signal_key].append(signal)

        logger.warning(
            f"LOSS SIGNAL: {signal.signal_type} ({signal.severity.value}) "
            f"tenant={signal.tenant_id} metric={signal.metric_name} "
            f"current={signal.current_value:.2f} threshold={signal.threshold:.2f} "
            f"deviation={signal.deviation_pct:.1f}% recommendation={signal.recommendation}"
        )

        return signal

    def get_recent_signals(self, minutes: int = 60, *, tenant_id: str) -> List[LossSignal]:
        """Get signals from last N minutes.

        Args:
            minutes: Time window in minutes (default 60)

        Returns:
            List of signals within the window, newest first.
        """
        from datetime import timezone
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        recent = [
            s for s in self.signals
            if s.tenant_id == tenant_id
            and datetime.fromisoformat(s.timestamp.replace("Z", "+00:00")) > cutoff
        ]
        return sorted(recent, key=lambda s: s.timestamp, reverse=True)

    def get_signals_by_type(self, signal_type: str, *, tenant_id: str) -> List[LossSignal]:
        """Get all signals of a specific type.

        Args:
            signal_type: "latency" | "confidence" | "feedback" | "ab_test"

        Returns:
            List of signals (newest first).
        """
        if signal_type not in self.alerts_by_type:
            return []

        signals = [s for s in self.alerts_by_type[signal_type] if s.tenant_id == tenant_id]
        return sorted(signals, key=lambda s: s.timestamp, reverse=True)

    def get_critical_signals(self, *, tenant_id: str) -> List[LossSignal]:
        """Get all critical-severity signals.

        Returns:
            List of critical signals (newest first).
        """
        critical = [
            s for s in self.signals
            if s.tenant_id == tenant_id and s.severity == SignalSeverity.CRITICAL
        ]
        return sorted(critical, key=lambda s: s.timestamp, reverse=True)


def _default_audit():
    """THE tenant chain (forge writer). The former ``_NoOpAudit`` default
    returned a fabricated hash and wrote nothing."""
    from core.vibe._chain_audit import ForgeChainAudit

    return ForgeChainAudit()


# Singleton instance (thread-safe lazy initialization)
_emitter_instance: Optional[LossSignalEmitter] = None


def get_emitter(audit_backend: Optional[AuditBackend] = None) -> LossSignalEmitter:
    """Get or create loss signal emitter singleton.

    Args:
        audit_backend: Audit backend (used only on first call)

    Returns:
        LossSignalEmitter instance.
    """
    global _emitter_instance
    if _emitter_instance is None:
        _emitter_instance = LossSignalEmitter(audit_backend)
    return _emitter_instance


def reset_emitter() -> None:
    """Reset singleton (testing only)."""
    global _emitter_instance
    _emitter_instance = None
