"""
Phase 4 Monitoring: Prometheus Metrics, Alerting, SLO Tracking.

Tracks:
1. Skill generation count, latency, success rate
2. Convergence time, weight variance, oscillation detection
3. Feedback signals count, feedback→action ratio
4. User satisfaction (derived from feedback)
5. Learning daemon health (uptime, event latency)

All metrics exposed via Prometheus for Grafana visualization.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


# ============================================================================
# METRIC DATA STRUCTURES
# ============================================================================

@dataclass
class SkillGenerationMetrics:
    """Metrics for skill generation."""
    total_count: int = 0
    success_count: int = 0
    avg_duration_ms: float = 0.0
    p99_duration_ms: float = 0.0
    failure_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    avg_quality_score: float = 0.0


@dataclass
class ConvergenceMetrics:
    """Metrics for learning daemon convergence."""
    converged_count: int = 0
    avg_convergence_time_samples: int = 0
    oscillation_detected: bool = False
    max_weight_variance: float = 0.0
    stalled_skills: List[str] = field(default_factory=list)


@dataclass
class FeedbackMetrics:
    """Metrics for user feedback."""
    total_count: int = 0
    avg_signal: float = 0.0
    distribution: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    feedback_to_action_ratio: float = 0.0


@dataclass
class DaemonHealthMetrics:
    """Metrics for daemon health."""
    uptime_hours: float = 0.0
    events_processed: int = 0
    avg_event_latency_ms: float = 0.0
    last_event_timestamp: Optional[str] = None
    restarts_count: int = 0
    error_count: int = 0


# ============================================================================
# MONITORING SERVICE
# ============================================================================

class MonitoringService:
    """Centralized monitoring and alerting."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id

        # Metrics storage
        self.skill_metrics = SkillGenerationMetrics()
        self.convergence_metrics = ConvergenceMetrics()
        self.feedback_metrics = FeedbackMetrics()
        self.daemon_health = DaemonHealthMetrics()

        # Time series data (for SLO tracking)
        self.skill_duration_history: List[float] = []
        self.weight_variance_history: List[float] = []
        self.feedback_signal_history: List[float] = []
        self.convergence_time_history: List[int] = []  # samples to convergence

        # Alert thresholds (tunable)
        self.alerts = {
            "convergence_stalled": {"threshold_days": 7, "triggered": False},
            "weight_oscillation": {"threshold_variance": 0.05, "triggered": False},
            "feedback_sparse": {"threshold_min_count": 5, "triggered": False},
            "daemon_unhealthy": {"threshold_restart_count": 3, "triggered": False},
        }

        self.start_time = datetime.utcnow()

    # ========================================================================
    # SKILL GENERATION METRICS
    # ========================================================================

    def record_skill_generation(
        self,
        skill_id: str,
        duration_ms: float,
        success: bool,
        quality_score: float,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Record skill generation event."""
        self.skill_metrics.total_count += 1

        if success:
            self.skill_metrics.success_count += 1
            self.skill_metrics.avg_quality_score = (
                (self.skill_metrics.avg_quality_score * (self.skill_metrics.success_count - 1) + quality_score)
                / self.skill_metrics.success_count
            )
        else:
            if failure_reason:
                self.skill_metrics.failure_reasons[failure_reason] += 1

        # Update latency metrics
        self.skill_duration_history.append(duration_ms)
        self._update_latency_percentiles()

        logger.info(f"Recorded skill generation: {skill_id} (success={success}, duration={duration_ms:.1f}ms)")

    def _update_latency_percentiles(self) -> None:
        """Update P99 latency from history."""
        if not self.skill_duration_history:
            return

        sorted_durations = sorted(self.skill_duration_history)
        p99_idx = int(len(sorted_durations) * 0.99)
        self.skill_metrics.p99_duration_ms = sorted_durations[p99_idx]

        avg_duration = sum(sorted_durations) / len(sorted_durations)
        self.skill_metrics.avg_duration_ms = avg_duration

    def get_skill_success_rate(self) -> float:
        """Compute success rate."""
        if self.skill_metrics.total_count == 0:
            return 1.0

        return self.skill_metrics.success_count / self.skill_metrics.total_count

    # ========================================================================
    # CONVERGENCE METRICS
    # ========================================================================

    def record_convergence(self, samples_to_convergence: int, final_variance: float) -> None:
        """Record convergence event."""
        self.convergence_metrics.converged_count += 1
        self.convergence_time_history.append(samples_to_convergence)

        # Update average
        self.convergence_metrics.avg_convergence_time_samples = (
            sum(self.convergence_time_history) / len(self.convergence_time_history)
        )

        # Track variance
        self.weight_variance_history.append(final_variance)
        self.convergence_metrics.max_weight_variance = max(self.weight_variance_history)

        logger.info(
            f"Convergence recorded: {samples_to_convergence} samples, variance={final_variance:.4f}"
        )

    def detect_oscillation(self, variance_history: List[float], window_size: int = 10) -> bool:
        """Detect weight oscillation (variance too high over window)."""
        if len(variance_history) < window_size:
            return False

        recent = variance_history[-window_size:]
        avg_variance = sum(recent) / len(recent)

        if avg_variance > self.alerts["weight_oscillation"]["threshold_variance"]:
            self.convergence_metrics.oscillation_detected = True
            self.alerts["weight_oscillation"]["triggered"] = True
            logger.warning(f"Weight oscillation detected: variance={avg_variance:.4f}")
            return True

        return False

    # ========================================================================
    # FEEDBACK METRICS
    # ========================================================================

    def record_feedback(self, signal: float, dimension: str) -> None:
        """Record user feedback signal."""
        self.feedback_metrics.total_count += 1
        self.feedback_signal_history.append(signal)

        # Update average signal
        self.feedback_metrics.avg_signal = (
            sum(self.feedback_signal_history) / len(self.feedback_signal_history)
        )

        # Track distribution
        if signal > 0.5:
            self.feedback_metrics.distribution["positive"] += 1
        elif signal < -0.5:
            self.feedback_metrics.distribution["negative"] += 1
        else:
            self.feedback_metrics.distribution["neutral"] += 1

        # Check for sparse feedback
        if self.feedback_metrics.total_count < self.alerts["feedback_sparse"]["threshold_min_count"]:
            self.alerts["feedback_sparse"]["triggered"] = True

        logger.info(f"Feedback recorded: signal={signal:.2f}, dimension={dimension}")

    def get_feedback_distribution(self) -> Dict[str, float]:
        """Get feedback distribution as percentages."""
        total = sum(self.feedback_metrics.distribution.values())
        if total == 0:
            return {}

        return {
            k: v / total for k, v in self.feedback_metrics.distribution.items()
        }

    # ========================================================================
    # DAEMON HEALTH METRICS
    # ========================================================================

    def record_event_processed(self, event_type: str, latency_ms: float) -> None:
        """Record daemon event processing."""
        self.daemon_health.events_processed += 1
        self.daemon_health.last_event_timestamp = datetime.utcnow().isoformat()

        # Update latency
        old_avg = self.daemon_health.avg_event_latency_ms
        new_count = self.daemon_health.events_processed
        self.daemon_health.avg_event_latency_ms = (
            (old_avg * (new_count - 1) + latency_ms) / new_count
        )

        logger.debug(f"Event processed: type={event_type}, latency={latency_ms:.2f}ms")

    def record_daemon_restart(self) -> None:
        """Record daemon restart."""
        self.daemon_health.restarts_count += 1

        if self.daemon_health.restarts_count > self.alerts["daemon_unhealthy"]["threshold_restart_count"]:
            self.alerts["daemon_unhealthy"]["triggered"] = True
            logger.warning(f"Daemon unhealthy: {self.daemon_health.restarts_count} restarts detected")

    def record_daemon_error(self, error_msg: str) -> None:
        """Record daemon error."""
        self.daemon_health.error_count += 1
        logger.error(f"Daemon error: {error_msg}")

    def get_daemon_uptime_hours(self) -> float:
        """Get daemon uptime in hours."""
        elapsed = datetime.utcnow() - self.start_time
        return elapsed.total_seconds() / 3600

    # ========================================================================
    # SLO TRACKING
    # ========================================================================

    def check_slo_compliance(self) -> Dict[str, bool]:
        """Check compliance with SLOs."""
        results = {}

        # SLO 1: Skill generation P99 latency < 5s
        results["skill_p99_latency"] = self.skill_metrics.p99_duration_ms < 5000

        # SLO 2: Success rate > 90%
        results["success_rate"] = self.get_skill_success_rate() > 0.90

        # SLO 3: Convergence within 500 samples
        results["convergence_time"] = (
            self.convergence_metrics.avg_convergence_time_samples < 500
            if self.convergence_metrics.converged_count > 0
            else True
        )

        # SLO 4: No oscillation detected
        results["no_oscillation"] = not self.convergence_metrics.oscillation_detected

        # SLO 5: Daemon uptime > 99% (assuming 7 days of operation)
        expected_uptime = 7 * 24  # hours
        actual_uptime = self.get_daemon_uptime_hours()
        results["daemon_availability"] = (actual_uptime / expected_uptime) > 0.99

        return results

    # ========================================================================
    # ALERTING
    # ========================================================================

    def check_alerts(self) -> List[str]:
        """Check for active alerts."""
        active_alerts = []

        for alert_name, alert_config in self.alerts.items():
            if alert_config.get("triggered"):
                active_alerts.append(alert_name)

        return active_alerts

    def get_alert_status(self) -> Dict[str, Any]:
        """Get detailed alert status."""
        return {
            alert_name: {
                "triggered": alert_config.get("triggered"),
                "threshold": alert_config.get("threshold_*", "N/A"),
                "current_value": self._get_alert_current_value(alert_name),
            }
            for alert_name, alert_config in self.alerts.items()
        }

    def _get_alert_current_value(self, alert_name: str) -> Any:
        """Get current value for alert."""
        if alert_name == "convergence_stalled":
            return f"{self.convergence_metrics.avg_convergence_time_samples:.0f} samples"
        elif alert_name == "weight_oscillation":
            return f"{self.convergence_metrics.max_weight_variance:.4f}"
        elif alert_name == "feedback_sparse":
            return f"{self.feedback_metrics.total_count} signals"
        elif alert_name == "daemon_unhealthy":
            return f"{self.daemon_health.restarts_count} restarts"
        else:
            return "N/A"

    # ========================================================================
    # METRICS EXPORT (Prometheus format)
    # ========================================================================

    def export_prometheus_metrics(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = [
            "# HELP corvin_skill_generation_total Total number of skill generations",
            "# TYPE corvin_skill_generation_total counter",
            f"corvin_skill_generation_total{{tenant=\"{self.tenant_id}\"}} {self.skill_metrics.total_count}",
            "",
            "# HELP corvin_skill_generation_success_total Successful skill generations",
            "# TYPE corvin_skill_generation_success_total counter",
            f"corvin_skill_generation_success_total{{tenant=\"{self.tenant_id}\"}} {self.skill_metrics.success_count}",
            "",
            "# HELP corvin_skill_generation_latency_p99 P99 latency for skill generation",
            "# TYPE corvin_skill_generation_latency_p99 gauge",
            f"corvin_skill_generation_latency_p99{{tenant=\"{self.tenant_id}\"}} {self.skill_metrics.p99_duration_ms}",
            "",
            "# HELP corvin_skill_quality_avg Average quality score",
            "# TYPE corvin_skill_quality_avg gauge",
            f"corvin_skill_quality_avg{{tenant=\"{self.tenant_id}\"}} {self.skill_metrics.avg_quality_score}",
            "",
            "# HELP corvin_convergence_count Total convergences detected",
            "# TYPE corvin_convergence_count counter",
            f"corvin_convergence_count{{tenant=\"{self.tenant_id}\"}} {self.convergence_metrics.converged_count}",
            "",
            "# HELP corvin_convergence_samples_avg Average samples to convergence",
            "# TYPE corvin_convergence_samples_avg gauge",
            f"corvin_convergence_samples_avg{{tenant=\"{self.tenant_id}\"}} {self.convergence_metrics.avg_convergence_time_samples}",
            "",
            "# HELP corvin_feedback_total Total feedback signals",
            "# TYPE corvin_feedback_total counter",
            f"corvin_feedback_total{{tenant=\"{self.tenant_id}\"}} {self.feedback_metrics.total_count}",
            "",
            "# HELP corvin_feedback_avg_signal Average feedback signal",
            "# TYPE corvin_feedback_avg_signal gauge",
            f"corvin_feedback_avg_signal{{tenant=\"{self.tenant_id}\"}} {self.feedback_metrics.avg_signal}",
            "",
            "# HELP corvin_daemon_events_processed Total events processed by daemon",
            "# TYPE corvin_daemon_events_processed counter",
            f"corvin_daemon_events_processed{{tenant=\"{self.tenant_id}\"}} {self.daemon_health.events_processed}",
            "",
            "# HELP corvin_daemon_uptime_hours Daemon uptime in hours",
            "# TYPE corvin_daemon_uptime_hours gauge",
            f"corvin_daemon_uptime_hours{{tenant=\"{self.tenant_id}\"}} {self.get_daemon_uptime_hours()}",
        ]

        return "\n".join(lines)

    # ========================================================================
    # SUMMARY & REPORTING
    # ========================================================================

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics."""
        return {
            "tenant_id": self.tenant_id,
            "timestamp": datetime.utcnow().isoformat(),
            "skill_metrics": {
                "total_count": self.skill_metrics.total_count,
                "success_count": self.skill_metrics.success_count,
                "success_rate": self.get_skill_success_rate(),
                "avg_duration_ms": self.skill_metrics.avg_duration_ms,
                "p99_duration_ms": self.skill_metrics.p99_duration_ms,
                "avg_quality_score": self.skill_metrics.avg_quality_score,
            },
            "convergence_metrics": {
                "converged_count": self.convergence_metrics.converged_count,
                "avg_convergence_time_samples": self.convergence_metrics.avg_convergence_time_samples,
                "oscillation_detected": self.convergence_metrics.oscillation_detected,
                "max_weight_variance": self.convergence_metrics.max_weight_variance,
            },
            "feedback_metrics": {
                "total_count": self.feedback_metrics.total_count,
                "avg_signal": self.feedback_metrics.avg_signal,
                "distribution": self.get_feedback_distribution(),
            },
            "daemon_health": {
                "events_processed": self.daemon_health.events_processed,
                "avg_event_latency_ms": self.daemon_health.avg_event_latency_ms,
                "uptime_hours": self.get_daemon_uptime_hours(),
                "restarts_count": self.daemon_health.restarts_count,
                "error_count": self.daemon_health.error_count,
            },
            "slo_status": self.check_slo_compliance(),
            "active_alerts": self.check_alerts(),
        }
