"""
Your Talent Score Calculator

Computes the Talent Score (0–10) from ADR-0274 measurement data.
This is the core metric that drives the "Your Talent" Console feature.

Formula:
  Talent Score = 50% * accuracy + 20% * learning_rate + 15% * variety + 15% * efficiency
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)


class TalentScoreCalculator:
    """Compute talent score and context rankings from measurement data."""

    def __init__(self, queue_root: Path = None):
        if queue_root is None:
            queue_root = Path.home() / ".corvin" / "measurement"
        self.queue_root = queue_root

    def read_jsonl_file(self, filepath: Path, limit: Optional[int] = None) -> List[Dict]:
        """Read JSONL file, latest first."""
        records = []
        if not filepath.exists():
            return records

        try:
            with open(filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.warning(f"Error reading {filepath}: {e}")

        return list(reversed(records))[:limit] if limit else list(reversed(records))

    def get_recent_records(self, days: int = 7) -> Dict:
        """Aggregate records from past N days."""
        result = {
            "predictions": [],
            "feedback": [],
            "choices": [],
            "budget": [],
        }

        try:
            for d in range(days):
                date = (datetime.utcnow() - timedelta(days=d)).strftime("%Y-%m-%d")
                date_dir = self.queue_root / date

                if not date_dir.exists():
                    continue

                result["predictions"].extend(
                    self.read_jsonl_file(date_dir / "predictions.jsonl")
                )
                result["feedback"].extend(
                    self.read_jsonl_file(date_dir / "feedback.jsonl")
                )
                result["choices"].extend(
                    self.read_jsonl_file(date_dir / "user_choices.jsonl")
                )
                result["budget"].extend(
                    self.read_jsonl_file(date_dir / "budget_allocations.jsonl")
                )
        except Exception as e:
            logger.error(f"Error aggregating records: {e}")

        return result

    def compute_accuracy(self, predictions: List[Dict]) -> float:
        """ADR-0270: Accuracy = 1.0 - avg(|pred - actual|)"""
        if not predictions:
            return 0.5

        diffs = [
            abs(p.get("confidence_pred", 0.0) - p.get("outcome_actual", 0.0))
            for p in predictions
        ]
        return 1.0 - (sum(diffs) / len(diffs))

    def compute_learning_rate(self, feedback: List[Dict]) -> float:
        """ADR-0271: Learning Rate = helpful % (0.0–1.0)"""
        if not feedback:
            return 0.5

        helpful = len([f for f in feedback if f.get("feedback_impact") == "helpful"])
        return helpful / len(feedback)

    def compute_variety(self, choices: List[Dict]) -> float:
        """ADR-0272: Variety = unique task types / 10 (normalized to 0–1)"""
        if not choices:
            return 0.5

        task_types = len(set(c.get("task_type") for c in choices))
        # Assume max 10 unique task types; normalize
        return min(1.0, task_types / 10.0)

    def compute_efficiency(self, budget: List[Dict]) -> float:
        """ADR-0273: Efficiency = avg(match_score)"""
        if not budget:
            return 0.5

        matches = [b.get("match_score", 0.5) for b in budget]
        return sum(matches) / len(matches)

    def compute_talent_score(self, records: Dict) -> Tuple[float, Dict]:
        """
        Main calculation.

        Returns:
            (score: float 0–10, components: dict of raw metrics)
        """
        accuracy = self.compute_accuracy(records["predictions"])
        learning = self.compute_learning_rate(records["feedback"])
        variety = self.compute_variety(records["choices"])
        efficiency = self.compute_efficiency(records["budget"])

        # Weighted average: 50% + 20% + 15% + 15% = 100%
        talent_score = (
            0.50 * accuracy +
            0.20 * learning +
            0.15 * variety +
            0.15 * efficiency
        )

        # Scale to 0–10
        talent_score_10 = talent_score * 10.0

        components = {
            "accuracy": accuracy,
            "learning_rate": learning,
            "variety": variety,
            "efficiency": efficiency,
            "weighted_score": talent_score,
            "talent_score_10": talent_score_10,
        }

        return talent_score_10, components

    def compute_context_ranking(self, records: Dict) -> List[Dict]:
        """
        Rank contexts by their accuracy and feedback.

        Returns:
            List of ranked contexts with stats.
        """
        # Build context stats
        context_stats = {}

        # From predictions
        for pred in records["predictions"]:
            ctx_id = pred.get("context_id", "unknown")
            if ctx_id not in context_stats:
                context_stats[ctx_id] = {
                    "id": ctx_id,
                    "accuracy": 0.0,
                    "usage": 0,
                    "feedback_good": 0,
                    "feedback_total": 0,
                    "match_scores": [],
                }

            # Accuracy for this prediction
            acc = 1.0 - abs(
                pred.get("confidence_pred", 0.0) - pred.get("outcome_actual", 0.0)
            )
            context_stats[ctx_id]["accuracy"] = (
                (context_stats[ctx_id]["accuracy"] * context_stats[ctx_id]["usage"] + acc)
                / (context_stats[ctx_id]["usage"] + 1)
            )
            context_stats[ctx_id]["usage"] += 1

        # From feedback
        for fb in records["feedback"]:
            ctx_id = fb.get("context_id", "unknown")
            if ctx_id not in context_stats:
                context_stats[ctx_id] = {
                    "id": ctx_id,
                    "accuracy": 0.75,
                    "usage": 0,
                    "feedback_good": 0,
                    "feedback_total": 0,
                    "match_scores": [],
                }

            is_good = fb.get("feedback_impact") == "helpful"
            context_stats[ctx_id]["feedback_good"] += int(is_good)
            context_stats[ctx_id]["feedback_total"] += 1

        # From budget
        for budget in records["budget"]:
            match_score = budget.get("match_score", 0.5)
            # Rough mapping: contexts mentioned in budget_allocated correlate with scoring

        # Sort by accuracy (primary) + feedback (secondary)
        ranked = sorted(
            context_stats.values(),
            key=lambda x: (
                x["accuracy"],
                -(x["feedback_good"] / max(1, x["feedback_total"])),
            ),
            reverse=True,
        )

        # Add rank badges and status
        for rank, ctx in enumerate(ranked, 1):
            if rank == 1:
                ctx["rank"] = 1
                ctx["medal"] = "🏆"
                ctx["status"] = "MENTOR"
            elif rank == 2:
                ctx["rank"] = 2
                ctx["medal"] = "🥈"
                ctx["status"] = "STRONG"
            elif rank == 3:
                ctx["rank"] = 3
                ctx["medal"] = "🥉"
                ctx["status"] = "SOLID"
            elif ctx["accuracy"] < 0.75:
                ctx["rank"] = rank
                ctx["medal"] = "⚠️"
                ctx["status"] = "NEEDS_TRAINING"
            elif ctx["accuracy"] < 0.70:
                ctx["rank"] = rank
                ctx["medal"] = "🚨"
                ctx["status"] = "STRUGGLING"
            else:
                ctx["rank"] = rank
                ctx["medal"] = "📌"
                ctx["status"] = "ACTIVE"

            # Compute feedback percentage
            ctx["feedback_pct"] = (
                100.0 * ctx["feedback_good"] / max(1, ctx["feedback_total"])
            )

        return ranked

    def compute_learning_events(self, records: Dict) -> List[Dict]:
        """
        Extract significant learning events from the timeline.

        Returns:
            List of events with timestamp, title, description.
        """
        events = []

        # Event 1: Major accuracy improvements
        if records["predictions"]:
            recent_acc = self.compute_accuracy(records["predictions"][:10])
            older_acc = self.compute_accuracy(records["predictions"][10:20])
            if recent_acc > older_acc + 0.05:  # 5% jump
                events.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "type": "accuracy_jump",
                    "title": "Confidence Accuracy Improved",
                    "description": f"+{(recent_acc - older_acc) * 100:.1f}% jump in prediction accuracy",
                    "badge": "↗ Trend Reversal",
                })

        # Event 2: Helpful feedback concentration
        if records["feedback"]:
            helpful = len([f for f in records["feedback"][:10] if f.get("feedback_impact") == "helpful"])
            if helpful >= 7:
                events.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "type": "high_feedback",
                    "title": "Strong Feedback Signal",
                    "description": f"7+ helpful feedback records this hour",
                    "badge": "✅ Learning Accelerated",
                })

        # Event 3: Context milestone
        rankings = self.compute_context_ranking(records)
        if rankings and rankings[0]["accuracy"] >= 0.90:
            events.append({
                "timestamp": datetime.utcnow().isoformat(),
                "type": "milestone",
                "title": f"🎉 {rankings[0]['id']} Reached MVP Status",
                "description": f"Accuracy {rankings[0]['accuracy']*100:.0f}% — context is now a trusted mentor",
                "badge": "🏆 MVP Status",
            })

        # Event 4: Struggling context warning
        if rankings and len(rankings) > 1:
            struggling = [r for r in rankings if r["accuracy"] < 0.75]
            if struggling:
                events.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "type": "warning",
                    "title": f"⚠️ {struggling[0]['id']} Needs Training",
                    "description": f"Accuracy dropped below 75% ({struggling[0]['accuracy']*100:.0f}%)",
                    "badge": "⚠️ Attention Needed",
                })

        # Event 5: Budget efficiency
        if records["budget"]:
            avg_match = sum(b.get("match_score", 0.5) for b in records["budget"]) / len(records["budget"])
            if avg_match >= 0.85:
                events.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "type": "efficiency",
                    "title": "Budget-Complexity Alignment Tuned",
                    "description": f"Match score {avg_match*100:.0f}% — allocation well-calibrated",
                    "badge": "✅ System Tuned",
                })

        return sorted(events, key=lambda e: e["timestamp"], reverse=True)

    def get_daily_breakdown(self, days: int = 7) -> List[Dict]:
        """
        Get daily score breakdown for chart visualization.

        Returns:
            List of dicts with date, score, and components for each day.
        """
        daily_data = []

        for d in range(days, 0, -1):  # Reverse order (oldest first)
            date = (datetime.utcnow() - timedelta(days=d)).strftime("%Y-%m-%d")
            date_dir = self.queue_root / date

            if not date_dir.exists():
                # Add placeholder for missing day
                daily_data.append({
                    "date": date,
                    "score": 5.0,
                    "accuracy": 0.5,
                    "learning_rate": 0.5,
                    "variety": 0.5,
                    "efficiency": 0.5,
                    "record_count": 0,
                })
                continue

            # Read records for this day only
            day_records = {
                "predictions": self.read_jsonl_file(date_dir / "predictions.jsonl"),
                "feedback": self.read_jsonl_file(date_dir / "feedback.jsonl"),
                "choices": self.read_jsonl_file(date_dir / "user_choices.jsonl"),
                "budget": self.read_jsonl_file(date_dir / "budget_allocations.jsonl"),
            }

            # Compute metrics for this day
            score, components = self.compute_talent_score(day_records)
            record_count = (
                len(day_records["predictions"]) +
                len(day_records["feedback"]) +
                len(day_records["choices"]) +
                len(day_records["budget"])
            )

            daily_data.append({
                "date": date,
                "score": round(score, 1),
                "accuracy": round(components["accuracy"], 2),
                "learning_rate": round(components["learning_rate"], 2),
                "variety": round(components["variety"], 2),
                "efficiency": round(components["efficiency"], 2),
                "record_count": record_count,
            })

        return daily_data

    def get_task_type_performance(self, days: int = 7) -> List[Dict]:
        """
        Get performance by task type.

        Returns:
            List of dicts with task_type and performance metrics.
        """
        task_performance = {}
        records = self.get_recent_records(days=days)

        for choice in records["choices"]:
            task_type = choice.get("task_type", "unknown")
            if task_type not in task_performance:
                task_performance[task_type] = {
                    "type": task_type,
                    "count": 0,
                    "accuracy_sum": 0.0,
                    "feedback_good": 0,
                    "feedback_total": 0,
                    "efficiency_sum": 0.0,
                }

            task_performance[task_type]["count"] += 1

        # Correlate with feedback and predictions
        for fb in records["feedback"]:
            task_type = fb.get("task_type", "unknown")
            if task_type in task_performance:
                is_good = fb.get("feedback_impact") == "helpful"
                task_performance[task_type]["feedback_good"] += int(is_good)
                task_performance[task_type]["feedback_total"] += 1

        # Add efficiency data
        for budget in records["budget"]:
            task_type = budget.get("task_type", "unknown")
            if task_type in task_performance:
                task_performance[task_type]["efficiency_sum"] += budget.get("match_score", 0.5)

        # Compute final metrics
        result = []
        for task_type, stats in task_performance.items():
            avg_accuracy = (
                stats["accuracy_sum"] / max(1, stats["count"])
                if stats["count"] > 0 else 0.5
            )
            feedback_pct = (
                100.0 * stats["feedback_good"] / max(1, stats["feedback_total"])
                if stats["feedback_total"] > 0 else 50.0
            )
            avg_efficiency = (
                stats["efficiency_sum"] / max(1, stats["count"])
                if stats["count"] > 0 else 0.5
            )

            result.append({
                "type": task_type,
                "count": stats["count"],
                "accuracy": round(avg_accuracy, 2),
                "feedback_percentage": round(feedback_pct, 1),
                "efficiency": round(avg_efficiency, 2),
            })

        return sorted(result, key=lambda x: x["count"], reverse=True)

    def get_component_correlation(self, days: int = 7) -> Dict:
        """
        Analyze correlation between components.

        Returns:
            Dict with scatter plot data for accuracy vs efficiency.
        """
        records = self.get_recent_records(days=days)

        # Correlation: each prediction/budget pair
        points = []
        for i, pred in enumerate(records["predictions"][:50]):  # Sample max 50
            accuracy = 1.0 - abs(
                pred.get("confidence_pred", 0.0) - pred.get("outcome_actual", 0.0)
            )
            efficiency = 0.75  # Default

            if i < len(records["budget"]):
                efficiency = records["budget"][i].get("match_score", 0.75)

            points.append({
                "accuracy": round(accuracy, 2),
                "efficiency": round(efficiency, 2),
            })

        return {"points": points}

    def generate_talent_report(self, days: int = 7) -> Dict:
        """
        Generate complete talent report for Console.

        Returns:
            {
              "score": 8.2,
              "trend": "+1.7",
              "components": {...},
              "ranking": [...],
              "events": [...],
              "timestamp": "2026-08-08T..."
            }
        """
        records = self.get_recent_records(days=days)
        score, components = self.compute_talent_score(records)
        ranking = self.compute_context_ranking(records)
        events = self.compute_learning_events(records)

        # Estimate trend (compare to yesterday)
        yesterday_records = self.get_recent_records(days=1)
        yesterday_score, _ = self.compute_talent_score(yesterday_records)
        trend = score - yesterday_score

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "talent_score": round(score, 1),
            "trend": round(trend, 1),
            "components": components,
            "ranking": ranking[:20],  # Top 20 contexts
            "events": events[:10],  # Top 10 events
            "record_counts": {
                "predictions": len(records["predictions"]),
                "feedback": len(records["feedback"]),
                "choices": len(records["choices"]),
                "budget": len(records["budget"]),
            },
        }


# Global instance
_calculator = None


def get_talent_calculator() -> TalentScoreCalculator:
    """Get or create global calculator."""
    global _calculator
    if _calculator is None:
        _calculator = TalentScoreCalculator()
    return _calculator


def compute_talent_report(days: int = 7) -> Dict:
    """Convenience function for API."""
    return get_talent_calculator().generate_talent_report(days=days)
