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

    def extract_learning_narratives(self, days: int = 7) -> List[Dict]:
        """
        Extract what was learned in natural language.

        Returns:
            List of narrative insights about improvements, patterns, concepts.
        """
        narratives = []
        records = self.get_recent_records(days=days)

        # Narrative 1: Major context improvements
        ranking = self.compute_context_ranking(records)
        if ranking:
            for ctx in ranking[:3]:
                if ctx["accuracy"] >= 0.85:
                    narratives.append({
                        "type": "milestone",
                        "icon": "🎓",
                        "title": f"Meistery: {ctx['id']}",
                        "description": f"Context '{ctx['id']}' reached {ctx['accuracy']*100:.0f}% accuracy — now a trusted mentor",
                        "importance": "high",
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                elif ctx["feedback_pct"] >= 75:
                    narratives.append({
                        "type": "feedback_win",
                        "icon": "✅",
                        "title": f"Learning Signal: {ctx['id']}",
                        "description": f"Context '{ctx['id']}' has {ctx['feedback_pct']:.0f}% helpful feedback — strong learning signal",
                        "importance": "medium",
                        "timestamp": datetime.utcnow().isoformat(),
                    })

        # Narrative 2: Task type diversity
        task_types = set(c.get("task_type") for c in records["choices"])
        if len(task_types) >= 3:
            narratives.append({
                "type": "diversity",
                "icon": "🌈",
                "title": f"Versatility Gained",
                "description": f"You've worked with {len(task_types)} different task types: {', '.join(sorted(task_types))}",
                "importance": "medium",
                "timestamp": datetime.utcnow().isoformat(),
            })

        # Narrative 3: Accuracy trend
        if records["predictions"]:
            recent_acc = self.compute_accuracy(records["predictions"][:20])
            older_acc = self.compute_accuracy(records["predictions"][-20:]) if len(records["predictions"]) > 20 else 0.5
            improvement = (recent_acc - older_acc) * 100
            if improvement > 5:
                narratives.append({
                    "type": "trend_up",
                    "icon": "📈",
                    "title": "Accuracy Improving",
                    "description": f"Your prediction accuracy improved by {improvement:.1f}% over the measurement period",
                    "importance": "high",
                    "timestamp": datetime.utcnow().isoformat(),
                })

        # Narrative 4: Efficiency pattern
        if records["budget"]:
            avg_match = sum(b.get("match_score", 0.5) for b in records["budget"]) / len(records["budget"])
            if avg_match >= 0.80:
                narratives.append({
                    "type": "efficiency",
                    "icon": "⚡",
                    "title": "Budget Alignment",
                    "description": f"Your complexity-budget match score is {avg_match*100:.0f}% — excellent resource allocation",
                    "importance": "medium",
                    "timestamp": datetime.utcnow().isoformat(),
                })

        # Narrative 5: Learning velocity
        helpful_count = len([f for f in records["feedback"] if f.get("feedback_impact") == "helpful"])
        if helpful_count > 5:
            velocity = (helpful_count / max(1, len(records["feedback"]))) * 100
            narratives.append({
                "type": "learning_velocity",
                "icon": "🚀",
                "title": "Fast Learning",
                "description": f"{velocity:.0f}% of feedback was helpful — you're learning quickly",
                "importance": "high",
                "timestamp": datetime.utcnow().isoformat(),
            })

        return sorted(narratives, key=lambda x: x["importance"] == "high", reverse=True)

    def get_dimension_insights(self, days: int = 7) -> List[Dict]:
        """
        Analyze each dimension (Accuracy, Learning, Variety, Efficiency).

        Returns:
            List of insights about what changed in each dimension.
        """
        records = self.get_recent_records(days=days)
        score, components = self.compute_talent_score(records)

        # Compare to previous period
        yesterday = self.get_recent_records(days=1)
        yesterday_score, yesterday_comp = self.compute_talent_score(yesterday)

        insights = []

        # Accuracy insight
        acc_change = components["accuracy"] - yesterday_comp["accuracy"]
        insights.append({
            "dimension": "Accuracy",
            "icon": "🎯",
            "current": round(components["accuracy"] * 100, 1),
            "change": round(acc_change * 100, 1),
            "status": "up" if acc_change > 0 else "down" if acc_change < 0 else "stable",
            "narrative": f"Prediction accuracy is {components['accuracy']*100:.0f}%",
            "analysis": "How well your predictions match reality"
        })

        # Learning Rate insight
        lr_change = components["learning_rate"] - yesterday_comp["learning_rate"]
        insights.append({
            "dimension": "Learning Rate",
            "icon": "📚",
            "current": round(components["learning_rate"] * 100, 1),
            "change": round(lr_change * 100, 1),
            "status": "up" if lr_change > 0 else "down" if lr_change < 0 else "stable",
            "narrative": f"Learning velocity is {components['learning_rate']*100:.0f}% of feedback is helpful",
            "analysis": "How fast you learn from feedback"
        })

        # Variety insight
        var_change = components["variety"] - yesterday_comp["variety"]
        insights.append({
            "dimension": "Variety",
            "icon": "🌈",
            "current": round(components["variety"] * 100, 1),
            "change": round(var_change * 100, 1),
            "status": "up" if var_change > 0 else "down" if var_change < 0 else "stable",
            "narrative": f"You're working with {int(components['variety']*10)} different task types",
            "analysis": "Breadth of skills and task diversity"
        })

        # Efficiency insight
        eff_change = components["efficiency"] - yesterday_comp["efficiency"]
        insights.append({
            "dimension": "Efficiency",
            "icon": "⚡",
            "current": round(components["efficiency"] * 100, 1),
            "change": round(eff_change * 100, 1),
            "status": "up" if eff_change > 0 else "down" if eff_change < 0 else "stable",
            "narrative": f"Resource allocation match is {components['efficiency']*100:.0f}%",
            "analysis": "How well you match budget to task complexity"
        })

        return insights

    def get_milestone_badges(self, days: int = 7) -> List[Dict]:
        """
        Identify concepts/skills that have been mastered (milestones).

        Returns:
            List of badge achievements.
        """
        records = self.get_recent_records(days=days)
        ranking = self.compute_context_ranking(records)
        badges = []

        # Milestone: High accuracy context (MVP status)
        for ctx in ranking:
            if ctx["accuracy"] >= 0.90:
                badges.append({
                    "badge": "🏆",
                    "title": "Context Master",
                    "description": f"'{ctx['id']}' achieved 90%+ accuracy",
                    "context": ctx["id"],
                    "level": "elite",
                    "achievement_date": datetime.utcnow().isoformat(),
                })
                break

        # Milestone: High accuracy context (Strong)
        for ctx in ranking:
            if 0.80 <= ctx["accuracy"] < 0.90:
                badges.append({
                    "badge": "🥈",
                    "title": "Strong Performer",
                    "description": f"'{ctx['id']}' has solid {ctx['accuracy']*100:.0f}% accuracy",
                    "context": ctx["id"],
                    "level": "advanced",
                    "achievement_date": datetime.utcnow().isoformat(),
                })
                break

        # Milestone: Task type mastery
        task_types = {}
        for choice in records["choices"]:
            tt = choice.get("task_type", "unknown")
            task_types[tt] = task_types.get(tt, 0) + 1

        for task_type, count in sorted(task_types.items(), key=lambda x: x[1], reverse=True)[:3]:
            if count >= 5:
                badges.append({
                    "badge": "🎓",
                    "title": f"{task_type.title()} Expert",
                    "description": f"Completed {count} {task_type} tasks",
                    "context": task_type,
                    "level": "expert",
                    "achievement_date": datetime.utcnow().isoformat(),
                })

        # Milestone: Learning velocity
        helpful = len([f for f in records["feedback"] if f.get("feedback_impact") == "helpful"])
        if helpful >= 10:
            badges.append({
                "badge": "🚀",
                "title": "Fast Learner",
                "description": f"{helpful} helpful feedback interactions",
                "context": "feedback",
                "level": "advanced",
                "achievement_date": datetime.utcnow().isoformat(),
            })

        return badges

    def get_improvement_story(self, days: int = 7) -> Dict:
        """
        Generate a narrative story of how you've improved.

        Returns:
            Dict with title, chapters (timeline), and overall summary.
        """
        daily = self.get_daily_breakdown(days=days)
        records = self.get_recent_records(days=days)
        ranking = self.compute_context_ranking(records)

        # Calculate changes
        if len(daily) >= 2:
            first_score = daily[0]["score"]
            last_score = daily[-1]["score"]
            score_change = last_score - first_score
        else:
            first_score = 5.0
            last_score = 5.0
            score_change = 0

        # Build story chapters (one per day)
        chapters = []
        for i, day in enumerate(daily):
            if i == 0:
                phase = "Foundation"
            elif score_change > 0 and i > len(daily) * 0.5:
                phase = "Acceleration"
            elif score_change < 0 and i > len(daily) * 0.5:
                phase = "Recalibration"
            else:
                phase = "Growth"

            chapters.append({
                "date": day["date"],
                "phase": phase,
                "score": day["score"],
                "accuracy": day["accuracy"],
                "learning_rate": day["learning_rate"],
                "variety": day["variety"],
                "efficiency": day["efficiency"],
            })

        # Overall summary
        summary = f"Over the past {days} days, your system "
        if score_change > 1:
            summary += f"improved significantly by {abs(score_change):.1f} points (from {first_score:.1f} to {last_score:.1f})"
        elif score_change > 0:
            summary += f"showed steady improvement of {score_change:.1f} points"
        elif score_change < -1:
            summary += f"shifted focus and recalibrated by {abs(score_change):.1f} points"
        else:
            summary += f"maintained consistent performance at {last_score:.1f}"

        summary += ". "
        if ranking and ranking[0]["accuracy"] >= 0.85:
            summary += f"Your top context '{ranking[0]['id']}' is performing exceptionally well."

        return {
            "title": "Your Learning Journey",
            "summary": summary,
            "duration_days": days,
            "score_start": round(first_score, 1),
            "score_end": round(last_score, 1),
            "score_change": round(score_change, 1),
            "trend": "improving" if score_change > 0 else "recalibrating" if score_change < -0.5 else "stable",
            "chapters": chapters,
        }

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
