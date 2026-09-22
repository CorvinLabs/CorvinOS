"""Stream 2: Dashboard Metrics (Stories 14-16 — Charts for console UI).

Computes three dashboard metrics:
  14. Confidence Trend (line chart, skill confidence over time)
  15. Feedback Volume (bar chart, feedback count by priority P0-P3)
  16. Optimizer Metrics (tuning events, config deltas, convergence %)
"""

import logging
import json
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timezone, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class DashboardMetrics:
    """Compute dashboard metrics for learning loop."""

    def __init__(
        self,
        learning_home: Path,
        feedback_home: Path,
        convergence_home: Path,
        tenant_id: str,
    ):
        """Initialize dashboard.

        Args:
            learning_home: Learning module home
            feedback_home: Feedback home
            convergence_home: Convergence tracking home
            tenant_id: Tenant scope
        """
        if not tenant_id:
            raise ValueError("tenant_id required")

        self.learning_home = Path(learning_home)
        self.feedback_home = Path(feedback_home)
        self.convergence_home = Path(convergence_home)
        self.tenant_id = tenant_id

    def get_confidence_trend(
        self,
        skill_id: str,
        days: int = 7,
    ) -> List[Dict]:
        """Story 14: Get confidence trend for skill (line chart data).

        Args:
            skill_id: Skill ID
            days: Number of days to return

        Returns:
            List of {"date": "2026-09-22", "confidence": 0.85, "success_rate": 0.92}
        """
        try:
            metrics_dir = self.convergence_home / self.tenant_id / "skill_metrics"
            if not metrics_dir.exists():
                return []

            # Get last N days of metrics
            trend = []
            for i in range(days, 0, -1):
                date = (datetime.now(timezone.utc).date() - timedelta(days=i)).isoformat()
                metric_file = metrics_dir / f"{skill_id}_{date}.json"

                if metric_file.exists():
                    try:
                        with open(metric_file, "r") as f:
                            metric_data = json.load(f)
                            trend.append({
                                "date": date,
                                "confidence": metric_data.get("success_rate", 0.5),
                                "success_rate": metric_data.get("success_rate", 0.5),
                                "error_rate": metric_data.get("error_rate", 0.1),
                            })
                    except Exception as e:
                        logger.error(f"read_metric_error: {metric_file.name}: {e}")

            return trend

        except Exception as e:
            logger.error(f"get_confidence_trend_error: {skill_id}: {e}")
            return []

    def get_feedback_volume_by_priority(self) -> Dict[str, int]:
        """Story 15: Get feedback volume by priority (bar chart data).

        Returns:
            {"P0": 3, "P1": 12, "P2": 45, "P3": 89}
        """
        try:
            triaged_dir = self.feedback_home / self.tenant_id / "triaged"
            if not triaged_dir.exists():
                return {"P0": 0, "P1": 0, "P2": 0, "P3": 0}

            volume = defaultdict(int)

            # Count triaged feedback by priority
            for triaged_file in triaged_dir.glob("*.json"):
                try:
                    with open(triaged_file, "r") as f:
                        triaged_data = json.load(f)
                        priority = triaged_data.get("priority", "p3").upper()
                        volume[priority] += 1
                except Exception as e:
                    logger.error(f"read_triaged_error: {triaged_file.name}: {e}")

            return {
                "P0": volume.get("P0", 0),
                "P1": volume.get("P1", 0),
                "P2": volume.get("P2", 0),
                "P3": volume.get("P3", 0),
            }

        except Exception as e:
            logger.error(f"get_feedback_volume_error: {e}")
            return {"P0": 0, "P1": 0, "P2": 0, "P3": 0}

    def get_optimizer_metrics(self) -> Dict:
        """Story 16: Get optimizer metrics (tuning events, deltas, convergence %).

        Returns:
            {
              "total_tuning_events": 42,
              "config_deltas_applied": 12,
              "converged_skills": 5,
              "total_skills": 8,
              "convergence_percentage": 62.5,
              "last_tuning_event": "2026-09-22T14:32:10Z",
            }
        """
        try:
            deltas_dir = self.learning_home / self.tenant_id / "config_deltas"
            convergence_dir = self.convergence_home / self.tenant_id / "convergence_signals"

            # Count config deltas
            deltas = list(deltas_dir.glob("*.json")) if deltas_dir.exists() else []
            num_deltas = len(deltas)

            # Get last tuning timestamp
            last_tuning = None
            if deltas:
                try:
                    with open(max(deltas, key=lambda f: f.stat().st_mtime), "r") as f:
                        delta_data = json.load(f)
                        last_tuning = delta_data.get("timestamp")
                except Exception:
                    pass

            # Count converged skills
            if convergence_dir.exists():
                converged_skills = len(list(convergence_dir.glob("*_converged.json")))
            else:
                converged_skills = 0

            # Estimate total skills (rough heuristic: skills with metrics)
            metrics_dir = self.convergence_home / self.tenant_id / "skill_metrics"
            if metrics_dir.exists():
                # Extract unique skill_id from metric files
                skills = set()
                for metric_file in metrics_dir.glob("*.json"):
                    skill_id = metric_file.stem.rsplit("_", 1)[0]  # "skill_id_date" → "skill_id"
                    skills.add(skill_id)
                total_skills = len(skills)
            else:
                total_skills = max(converged_skills, 1)

            convergence_pct = (converged_skills / total_skills * 100) if total_skills > 0 else 0

            return {
                "total_tuning_events": num_deltas,
                "config_deltas_applied": num_deltas,
                "converged_skills": converged_skills,
                "total_skills": total_skills,
                "convergence_percentage": round(convergence_pct, 1),
                "last_tuning_event": last_tuning or "never",
            }

        except Exception as e:
            logger.error(f"get_optimizer_metrics_error: {e}")
            return {
                "total_tuning_events": 0,
                "config_deltas_applied": 0,
                "converged_skills": 0,
                "total_skills": 0,
                "convergence_percentage": 0,
                "last_tuning_event": "never",
            }

    def get_dashboard_data(self, skill_id: Optional[str] = None) -> Dict:
        """Get all dashboard data (for console UI).

        Returns:
            {
              "confidence_trends": [{...}],
              "feedback_volume": {"P0": 3, ...},
              "optimizer_metrics": {...},
            }
        """
        return {
            "confidence_trends": (
                self.get_confidence_trend(skill_id, days=7) if skill_id else []
            ),
            "feedback_volume": self.get_feedback_volume_by_priority(),
            "optimizer_metrics": self.get_optimizer_metrics(),
            "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        }


__all__ = ["DashboardMetrics"]
