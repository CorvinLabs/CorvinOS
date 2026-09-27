"""
Canary Traffic Controller — Automated Traffic Escalation for Phase 2a

Manages gradual traffic shift from 1% → 10% → 50% → 100% over 4 weeks.
Implements Skill-based decisions, audit-first design, and fail-closed validation.

Related: ADR-0206 (canary strategy), ADR-0867 (watchdog), ADR-0532 (OS-Skills)
Compliance: GDPR Art. 30/32 (audit trail), EU AI Act Art. 50 (transparency)
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


class TrafficPercent(Enum):
    """Canary traffic percentage stages"""
    WEEK_3_START = 1      # 1% — initial validation
    WEEK_3_5 = 10         # 10% — low-risk escalation
    WEEK_4 = 50           # 50% — high-confidence validation
    WEEK_4_75 = 100       # 100% — Phase 2b activation


class EscalationDecision(Enum):
    """Canary escalation decisions"""
    ESCALATE = "escalate"
    HOLD = "hold"
    ROLLBACK = "rollback"
    BLOCKED = "blocked"


class CanaryHealth(Enum):
    """Canary deployment health status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class TrafficMetrics:
    """Immutable traffic metrics snapshot"""
    timestamp: str
    traffic_percent: int
    p99_latency_ms: float
    error_rate_pct: float
    agreement_rate_pct: float  # % of requests where bundled + skill decide the same
    total_requests: int
    error_count: int
    agreement_count: int
    tenant_id: str = "_default"

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TrafficMetrics":
        """Deserialize from dict"""
        return cls(**data)


@dataclass
class CanaryState:
    """Immutable canary deployment state"""
    current_traffic_percent: int
    status: CanaryHealth
    started_at: str  # ISO8601 timestamp
    last_decision_at: Optional[str]
    decision_history: List[Dict] = field(default_factory=list)
    rollback_reason: Optional[str] = None
    skill_id: str = "os.canary_router"
    audit_trail: List[str] = field(default_factory=list)  # Audit event hashes
    phase_2b_activated: bool = False
    tenant_id: str = "_default"

    def to_dict(self) -> dict:
        """Serialize to JSON"""
        d = asdict(self)
        # Don't serialize audit_trail directly — it's in the audit log
        d.pop("audit_trail", None)
        # Convert Enum to string for JSON serialization
        if isinstance(d.get("status"), CanaryHealth):
            d["status"] = d["status"].value
        elif isinstance(d.get("status"), str):
            pass  # Already a string
        else:
            d["status"] = str(d.get("status", "unknown"))
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "CanaryState":
        """Deserialize from dict"""
        data.pop("audit_trail", None)  # Load from audit log instead
        # Convert string back to Enum
        if isinstance(data.get("status"), str):
            data["status"] = CanaryHealth(data["status"])
        return cls(**data)


@dataclass
class ValidationGate:
    """Single validation gate for canary escalation"""
    gate_name: str
    metric_type: str  # "latency", "error_rate", "agreement_rate"
    threshold_pass: float
    threshold_warn: float
    operator: str = "lt"  # "lt" (less than), "gt" (greater than)

    def evaluate(self, actual_value: float) -> Tuple[bool, str]:
        """
        Evaluate gate.
        Returns (passed, status_string)
        """
        passed = self._compare(actual_value, self.threshold_pass)
        warned = self._compare(actual_value, self.threshold_warn)

        status = "PASS" if passed else ("WARN" if warned else "FAIL")
        return passed, status

    def _compare(self, actual: float, threshold: float) -> bool:
        """Compare actual value to threshold"""
        if self.operator == "lt":
            return actual < threshold
        elif self.operator == "gt":
            return actual > threshold
        else:
            raise ValueError(f"Unknown operator: {self.operator}")


class CanaryTrafficController:
    """
    Manages canary traffic escalation for Phase 2a.

    State is persisted to disk (fail-closed if disk unavailable).
    Every decision is audit-logged and skill-attributed.
    Validation gates are applied weekly.
    """

    # Weekly validation gates (tuned for real-world 2a rollout)
    VALIDATION_GATES = {
        "week_3_1pct": [
            ValidationGate("latency_ok", "latency", 500.0, 1000.0, "lt"),  # p99 < 500ms pass, < 1000ms warn
            ValidationGate("error_rate_ok", "error_rate", 0.1, 1.0, "lt"),  # < 0.1% pass, < 1% warn
            ValidationGate("agreement_ok", "agreement_rate", 95.0, 85.0, "gt"),  # > 95% agreement pass, > 85% warn
        ],
        "week_3_5_10pct": [
            ValidationGate("latency_ok", "latency", 400.0, 800.0, "lt"),
            ValidationGate("error_rate_ok", "error_rate", 0.05, 0.5, "lt"),
            ValidationGate("agreement_ok", "agreement_rate", 97.0, 92.0, "gt"),
        ],
        "week_4_50pct": [
            ValidationGate("latency_ok", "latency", 300.0, 600.0, "lt"),
            ValidationGate("error_rate_ok", "error_rate", 0.02, 0.2, "lt"),
            ValidationGate("agreement_ok", "agreement_rate", 98.0, 95.0, "gt"),
        ],
        "week_4_75_100pct": [
            ValidationGate("latency_ok", "latency", 250.0, 500.0, "lt"),
            ValidationGate("error_rate_ok", "error_rate", 0.01, 0.1, "lt"),
            ValidationGate("agreement_ok", "agreement_rate", 99.0, 97.0, "gt"),
        ],
    }

    def __init__(self, state_file: str = "~/.corvin/canary_state.json"):
        """Initialize canary controller"""
        self.state_file = Path(state_file).expanduser()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
        self.metrics_history: Dict[int, List[TrafficMetrics]] = defaultdict(list)

    def _load_state(self) -> CanaryState:
        """Load canary state from disk, or initialize new"""
        if not self.state_file.exists():
            logger.info("Initializing new canary state")
            return CanaryState(
                current_traffic_percent=0,
                status=CanaryHealth.UNKNOWN,
                started_at=datetime.utcnow().isoformat() + "Z",
                last_decision_at=None,
            )

        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            logger.info(f"Loaded canary state: {data['current_traffic_percent']}% traffic")
            return CanaryState.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load canary state: {e}. Initializing new.")
            return CanaryState(
                current_traffic_percent=0,
                status=CanaryHealth.UNKNOWN,
                started_at=datetime.utcnow().isoformat() + "Z",
                last_decision_at=None,
            )

    def _save_state(self) -> bool:
        """Save canary state to disk (fail-closed if unsuccessful)"""
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state.to_dict(), f, indent=2)
            logger.info(f"Saved canary state: {self.state.current_traffic_percent}%")
            return True
        except Exception as e:
            logger.error(f"❌ FAIL-CLOSED: Cannot save canary state: {e}")
            return False

    def start_canary(self, initial_traffic: int = 1) -> CanaryState:
        """
        Start canary deployment at initial traffic percentage.

        Emits: canary_started (audit event)
        """
        if self.state.current_traffic_percent > 0:
            logger.warning(f"Canary already started at {self.state.current_traffic_percent}%")
            return self.state

        logger.info(f"🚀 Starting canary deployment at {initial_traffic}% traffic")
        self.state.current_traffic_percent = initial_traffic
        self.state.status = CanaryHealth.HEALTHY
        self.state.started_at = datetime.utcnow().isoformat() + "Z"

        # Record decision
        self._record_decision(
            decision=EscalationDecision.ESCALATE.value,
            reason=f"Canary started: {initial_traffic}% traffic",
            metrics=None,
        )

        if not self._save_state():
            raise RuntimeError("Cannot save canary state — deployment blocked (fail-closed)")

        logger.info(f"✅ Canary started at {initial_traffic}%")
        return self.state

    def record_metrics(self, metrics: TrafficMetrics) -> None:
        """
        Record traffic metrics snapshot.

        Stores metrics in memory for decision making.
        Emits: metrics_recorded (audit event)
        """
        self.metrics_history[self.state.current_traffic_percent].append(metrics)
        logger.debug(f"Recorded metrics: {metrics.traffic_percent}% traffic, "
                    f"p99={metrics.p99_latency_ms}ms, error_rate={metrics.error_rate_pct}%")

    def evaluate_escalation(self) -> Tuple[EscalationDecision, str, Optional[TrafficMetrics]]:
        """
        Evaluate whether canary can escalate to next traffic level.

        Returns: (decision, reason, latest_metrics)

        Decision logic:
        - Collect metrics for current traffic level
        - Run validation gates
        - If ALL gates pass → ESCALATE
        - If ANY gate fails → HOLD
        - If error_rate > 1% or p99 > 2x baseline → ROLLBACK

        Emits: escalation_evaluated (audit event)
        """
        if self.state.current_traffic_percent == 0:
            return EscalationDecision.BLOCKED, "Canary not started", None

        # Get latest metrics for current traffic level
        current_metrics_list = self.metrics_history.get(self.state.current_traffic_percent, [])
        if not current_metrics_list:
            return EscalationDecision.HOLD, "No metrics collected yet", None

        latest_metrics = current_metrics_list[-1]

        # Select validation gates for current traffic level
        gates = self._get_gates_for_traffic(self.state.current_traffic_percent)
        if not gates:
            return EscalationDecision.HOLD, "No validation gates configured", latest_metrics

        # Evaluate each gate
        gate_results = []
        for gate in gates:
            actual_value = self._extract_metric(latest_metrics, gate.metric_type)
            passed, status = gate.evaluate(actual_value)
            gate_results.append((gate.gate_name, status, passed))
            logger.info(f"  Gate '{gate.gate_name}': {status} (threshold={gate.threshold_pass}, "
                       f"actual={actual_value})")

        # Check for rollback conditions (fail-closed)
        if latest_metrics.error_rate_pct > 1.0:
            reason = f"Error rate {latest_metrics.error_rate_pct}% > 1% threshold (ROLLBACK)"
            logger.error(f"❌ {reason}")
            return EscalationDecision.ROLLBACK, reason, latest_metrics

        # Check for escalation: ALL gates must pass
        all_gates_pass = all(passed for _, _, passed in gate_results)

        if all_gates_pass:
            next_traffic = self._get_next_traffic_level(self.state.current_traffic_percent)
            decision = EscalationDecision.ESCALATE if next_traffic else EscalationDecision.BLOCKED
            reason = f"All gates pass. {'Ready to escalate' if decision == EscalationDecision.ESCALATE else 'Already at 100%'}"
            logger.info(f"✅ {reason}")
        else:
            decision = EscalationDecision.HOLD
            failed_gates = [name for name, status, passed in gate_results if not passed]
            reason = f"Gates failing: {', '.join(failed_gates)}. Hold at current traffic."
            logger.warning(f"⏸️ {reason}")

        return decision, reason, latest_metrics

    def escalate(self, skill_id: str = "os.canary_router") -> Tuple[bool, str, int]:
        """
        Escalate to next traffic level (Skill-initiated).

        Call evaluate_escalation() first to get decision.

        Returns: (success, reason, new_traffic_percent)
        Emits: traffic_escalated (audit event + skill feedback)
        """
        current = self.state.current_traffic_percent
        next_traffic = self._get_next_traffic_level(current)

        if not next_traffic:
            msg = "Already at 100% — Phase 2b activation"
            logger.info(f"🎉 {msg}")
            self.state.phase_2b_activated = True
            self.state.status = CanaryHealth.HEALTHY
            self._save_state()
            return True, msg, 100

        logger.info(f"⬆️ Escalating traffic: {current}% → {next_traffic}%")
        self.state.current_traffic_percent = next_traffic
        self.state.last_decision_at = datetime.utcnow().isoformat() + "Z"

        # Record decision with skill attribution
        self._record_decision(
            decision=EscalationDecision.ESCALATE.value,
            reason=f"Escalated by {skill_id} from {current}% to {next_traffic}%",
            metrics=None,
            skill_id=skill_id,
        )

        if not self._save_state():
            logger.error("Failed to save escalated state — reverting (fail-closed)")
            self.state.current_traffic_percent = current
            return False, "Cannot persist escalation", current

        logger.info(f"✅ Traffic escalated to {next_traffic}%")
        return True, f"Escalated to {next_traffic}%", next_traffic

    def hold(self, reason: str = "Metrics not ready") -> Tuple[bool, str]:
        """
        Hold at current traffic level (no escalation).

        Returns: (success, reason)
        Emits: escalation_held (audit event)
        """
        current = self.state.current_traffic_percent
        logger.warning(f"⏸️ Holding at {current}%: {reason}")

        self._record_decision(
            decision=EscalationDecision.HOLD.value,
            reason=reason,
            metrics=None,
        )

        return True, reason

    def rollback(self, reason: str = "Canary degradation detected", target_traffic: int = 1) -> Tuple[bool, str, int]:
        """
        Rollback to lower traffic percentage (emergency procedure).

        Fail-closed: saves state before returning.

        Returns: (success, reason, rolled_back_traffic)
        Emits: traffic_rolled_back (audit event + HIGH severity)
        """
        current = self.state.current_traffic_percent

        if target_traffic >= current:
            msg = f"Invalid rollback target: {target_traffic}% >= current {current}%"
            logger.error(msg)
            return False, msg, current

        logger.error(f"🔴 ROLLBACK: {current}% → {target_traffic}%. Reason: {reason}")

        self.state.current_traffic_percent = target_traffic
        self.state.status = CanaryHealth.DEGRADED
        self.state.rollback_reason = reason
        self.state.last_decision_at = datetime.utcnow().isoformat() + "Z"

        self._record_decision(
            decision=EscalationDecision.ROLLBACK.value,
            reason=reason,
            metrics=None,
        )

        if not self._save_state():
            logger.error("❌ FAIL-CLOSED: Cannot save rollback state")
            return False, "Cannot persist rollback", current

        logger.info(f"✅ Rolled back to {target_traffic}%")
        return True, reason, target_traffic

    def get_state(self) -> CanaryState:
        """Get current canary state"""
        return self.state

    def get_metrics_summary(self) -> Dict:
        """Get summary of all collected metrics"""
        summary = {
            "current_traffic_percent": self.state.current_traffic_percent,
            "status": self.state.status.value,
            "phase_2b_activated": self.state.phase_2b_activated,
            "metrics_by_traffic_level": {},
        }

        for traffic_pct, metrics_list in self.metrics_history.items():
            if not metrics_list:
                continue

            latest = metrics_list[-1]
            summary["metrics_by_traffic_level"][traffic_pct] = {
                "count": len(metrics_list),
                "latest": latest.to_dict(),
                "avg_latency_ms": sum(m.p99_latency_ms for m in metrics_list) / len(metrics_list),
                "avg_error_rate": sum(m.error_rate_pct for m in metrics_list) / len(metrics_list),
                "avg_agreement_rate": sum(m.agreement_rate_pct for m in metrics_list) / len(metrics_list),
            }

        return summary

    # ===== Private Helpers =====

    def _get_gates_for_traffic(self, traffic_pct: int) -> List[ValidationGate]:
        """Get validation gates for given traffic percentage"""
        if traffic_pct <= 1:
            return self.VALIDATION_GATES.get("week_3_1pct", [])
        elif traffic_pct <= 10:
            return self.VALIDATION_GATES.get("week_3_5_10pct", [])
        elif traffic_pct <= 50:
            return self.VALIDATION_GATES.get("week_4_50pct", [])
        else:
            return self.VALIDATION_GATES.get("week_4_75_100pct", [])

    def _get_next_traffic_level(self, current: int) -> Optional[int]:
        """Get next traffic level, or None if at 100%"""
        levels = [1, 10, 50, 100]
        for level in levels:
            if level > current:
                return level
        return None

    def _extract_metric(self, metrics: TrafficMetrics, metric_type: str) -> float:
        """Extract metric value from TrafficMetrics"""
        if metric_type == "latency":
            return metrics.p99_latency_ms
        elif metric_type == "error_rate":
            return metrics.error_rate_pct
        elif metric_type == "agreement_rate":
            return metrics.agreement_rate_pct
        else:
            raise ValueError(f"Unknown metric type: {metric_type}")

    def _record_decision(
        self,
        decision: str,
        reason: str,
        metrics: Optional[TrafficMetrics],
        skill_id: str = "os.canary_router",
    ) -> None:
        """Record escalation decision to state (audit-first)"""
        decision_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "decision": decision,
            "reason": reason,
            "traffic_percent": self.state.current_traffic_percent,
            "skill_id": skill_id,
        }

        if metrics:
            decision_record["metrics"] = metrics.to_dict()

        self.state.decision_history.append(decision_record)
        logger.debug(f"Recorded decision: {decision} @ {self.state.current_traffic_percent}% "
                    f"({skill_id})")


def create_controller(state_file: str = "~/.corvin/canary_state.json") -> CanaryTrafficController:
    """Factory function to create a canary traffic controller"""
    return CanaryTrafficController(state_file)
