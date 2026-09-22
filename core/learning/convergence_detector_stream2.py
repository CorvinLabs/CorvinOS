"""Stream 2: Convergence Detector (Story 13 — Stop tuning at plateau).

Tracks skill improvement over time.
Detects when improvement plateaus (<1% improvement for 3 days).
Signals to stop tuning.
"""

import logging
import json
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class ConvergenceDetector:
    """Detect when skill improvement has plateaued."""

    # Convergence criteria: improvement <1% for N consecutive days
    IMPROVEMENT_THRESHOLD = 0.01  # 1%
    PLATEAU_DAYS = 3

    def __init__(self, convergence_home: Path, tenant_id: str):
        """Initialize detector.

        Args:
            convergence_home: Root directory for convergence tracking
            tenant_id: Tenant scope
        """
        if not tenant_id:
            raise ValueError("tenant_id required")

        self.convergence_home = Path(convergence_home)
        self.tenant_id = tenant_id
        self.metrics_dir = self.convergence_home / tenant_id / "skill_metrics"
        self.convergence_dir = self.convergence_home / tenant_id / "convergence_signals"

        # Create directories
        for d in [self.metrics_dir, self.convergence_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def record_metric(
        self,
        skill_id: str,
        success_rate: float,
        error_rate: float,
    ) -> None:
        """Record daily metric for a skill.

        Args:
            skill_id: Skill ID
            success_rate: Daily success rate (0-1)
            error_rate: Daily error rate (0-1)
        """
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            metric_file = self.metrics_dir / f"{skill_id}_{today}.json"

            metric_data = {
                "skill_id": skill_id,
                "date": today,
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "success_rate": success_rate,
                "error_rate": error_rate,
            }

            with open(metric_file, "w") as f:
                json.dump(metric_data, f, indent=2)

            logger.debug(f"metric_recorded: {skill_id}: {today}: success_rate={success_rate:.1%}")

        except Exception as e:
            logger.error(f"record_metric_error: {skill_id}: {e}")

    def check_convergence(self, skill_id: str) -> bool:
        """Check if skill has converged (plateau detected).

        Returns:
            True if convergence detected (improvement <1% for 3+ days)
        """
        try:
            # Get last N days of metrics
            metric_files = sorted(self.metrics_dir.glob(f"{skill_id}_*.json"))
            if len(metric_files) < self.PLATEAU_DAYS:
                # Need at least PLATEAU_DAYS of data
                return False

            # Read last PLATEAU_DAYS metrics
            recent_metrics = []
            for metric_file in metric_files[-self.PLATEAU_DAYS:]:
                try:
                    with open(metric_file, "r") as f:
                        metric_data = json.load(f)
                        recent_metrics.append(metric_data)
                except Exception as e:
                    logger.error(f"read_metric_error: {metric_file.name}: {e}")
                    continue

            if len(recent_metrics) < self.PLATEAU_DAYS:
                return False

            # Check if improvement is <1% across all days
            success_rates = [m["success_rate"] for m in recent_metrics]
            improvements = []

            for i in range(1, len(success_rates)):
                improvement = (success_rates[i] - success_rates[i-1]) / (success_rates[i-1] + 0.001)
                improvements.append(improvement)

            # If all improvements are <1%, convergence detected
            converged = all(imp < self.IMPROVEMENT_THRESHOLD for imp in improvements)

            if converged:
                logger.info(
                    f"convergence_detected: {skill_id}: "
                    f"improvements = {[f'{i:.2%}' for i in improvements]}"
                )

            return converged

        except Exception as e:
            logger.error(f"check_convergence_error: {skill_id}: {e}")
            return False

    def record_convergence_signal(
        self,
        skill_id: str,
        reason: str = "improvement_plateau",
    ) -> None:
        """Record convergence signal (tuning should stop).

        Args:
            skill_id: Skill ID
            reason: Why convergence was detected
        """
        try:
            signal_data = {
                "skill_id": skill_id,
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "reason": reason,
                "action": "stop_tuning",
            }

            signal_file = self.convergence_dir / f"{skill_id}_converged.json"
            with open(signal_file, "w") as f:
                json.dump(signal_data, f, indent=2)

            logger.info(f"convergence_signal_recorded: {skill_id}: {reason}")

        except Exception as e:
            logger.error(f"record_signal_error: {skill_id}: {e}")

    def has_converged(self, skill_id: str) -> bool:
        """Check if a skill has been marked as converged."""
        signal_file = self.convergence_dir / f"{skill_id}_converged.json"
        return signal_file.exists()

    def get_convergence_status(self, skill_id: str) -> Dict:
        """Get convergence status for a skill."""
        try:
            # Get recent metrics
            metric_files = sorted(self.metrics_dir.glob(f"{skill_id}_*.json"))[-7:]  # Last 7 days
            metrics = []

            for metric_file in metric_files:
                try:
                    with open(metric_file, "r") as f:
                        metrics.append(json.load(f))
                except Exception:
                    continue

            has_converged = self.has_converged(skill_id)
            check_converged = self.check_convergence(skill_id)

            return {
                "skill_id": skill_id,
                "has_converged_signal": has_converged,
                "check_converged": check_converged,
                "recent_metrics": metrics,
                "days_tracked": len(metrics),
            }

        except Exception as e:
            logger.error(f"get_status_error: {skill_id}: {e}")
            return {"skill_id": skill_id, "error": str(e)}


__all__ = ["ConvergenceDetector"]
