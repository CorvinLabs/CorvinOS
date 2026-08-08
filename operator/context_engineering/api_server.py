"""
ADR-0274 Telemetry API Server

Serves K=8 aggregator measurement data as JSON endpoints:
- /api/v1/measurements/latest (all 4 tracks)
- /api/v1/measurements/predictions (ADR-0270)
- /api/v1/measurements/feedback (ADR-0271)
- /api/v1/measurements/preferences (ADR-0272)
- /api/v1/measurements/budget (ADR-0273)
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from flask import Flask, jsonify, request
from flask_cors import CORS
from talent_score import get_talent_calculator

logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)


class MeasurementReader:
    """Read and aggregate K=8 measurement data from queue files."""

    def __init__(self, queue_root: Path = None):
        if queue_root is None:
            queue_root = Path.home() / ".corvin" / "measurement"
        self.queue_root = queue_root

    def read_jsonl_file(self, filepath: Path, limit: Optional[int] = None) -> List[Dict]:
        """Read JSONL file (latest records first)."""
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
                            logger.warning(f"Skipped invalid JSON in {filepath}")
        except Exception as e:
            logger.error(f"Error reading {filepath}: {e}")

        # Reverse to get latest first
        return list(reversed(records))[:limit] if limit else list(reversed(records))

    def get_latest_records(self, days: int = 7) -> Dict:
        """Aggregate latest measurement records from past N days."""
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "days_lookback": days,
            "predictions": [],
            "feedback": [],
            "preferences": [],
            "budget": [],
        }

        # Search for measurement files
        try:
            for days_ago in range(days):
                date = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                date_dir = self.queue_root / date

                if not date_dir.exists():
                    continue

                # Read each measurement file
                for track_name in ["predictions", "feedback", "user_choices", "budget_allocations"]:
                    file_map = {
                        "predictions": "predictions.jsonl",
                        "feedback": "feedback.jsonl",
                        "user_choices": "user_choices.jsonl",
                        "budget": "budget_allocations.jsonl",
                    }
                    filepath = date_dir / file_map[track_name]
                    records = self.read_jsonl_file(filepath, limit=100)
                    result[track_name].extend(records)

        except Exception as e:
            logger.error(f"Error aggregating measurements: {e}")

        return result

    def compute_adr_0270_stats(self, predictions: List[Dict]) -> Dict:
        """ADR-0270: Compute confidence accuracy metrics."""
        if not predictions:
            return {"count": 0, "avg_confidence": 0.0, "avg_outcome": 0.0, "accuracy": 0.0}

        avg_conf = sum(p.get("confidence_pred", 0.0) for p in predictions) / len(predictions)
        avg_outcome = sum(p.get("outcome_actual", 0.0) for p in predictions) / len(predictions)

        # Accuracy: how close predictions match outcomes
        diffs = [
            abs(p.get("confidence_pred", 0.0) - p.get("outcome_actual", 0.0))
            for p in predictions
        ]
        accuracy = 1.0 - (sum(diffs) / len(diffs)) if diffs else 0.0

        return {
            "count": len(predictions),
            "avg_confidence": round(avg_conf, 3),
            "avg_outcome": round(avg_outcome, 3),
            "accuracy": round(accuracy, 3),
            "contexts_tracked": len(set(p.get("context_id") for p in predictions)),
        }

    def compute_adr_0271_stats(self, feedback: List[Dict]) -> Dict:
        """ADR-0271: Compute Bayesian learning metrics."""
        if not feedback:
            return {"count": 0, "avg_delta": 0.0, "helpful_pct": 0.0}

        deltas = [f.get("score_after", 0.0) - f.get("score_before", 0.0) for f in feedback]
        helpful = len([f for f in feedback if f.get("feedback_impact") == "helpful"])

        return {
            "count": len(feedback),
            "avg_delta": round(sum(deltas) / len(deltas), 3),
            "helpful_pct": round((helpful / len(feedback)) * 100, 1),
            "learning_events": len([f for f in feedback if abs(sum(deltas) / len(feedback)) > 0.01]),
        }

    def compute_adr_0272_stats(self, choices: List[Dict]) -> Dict:
        """ADR-0272: Compute user preference patterns."""
        if not choices:
            return {"count": 0, "pragmatic_pct": 0.0, "task_types": {}}

        pragmatic = len([c for c in choices if c.get("decision_style") == "pragmatic"])
        task_types = {}
        for c in choices:
            tt = c.get("task_type", "unknown")
            task_types[tt] = task_types.get(tt, 0) + 1

        return {
            "count": len(choices),
            "pragmatic_pct": round((pragmatic / len(choices)) * 100, 1),
            "task_types": task_types,
            "unique_users": len(set(c.get("user_id") for c in choices)),
        }

    def compute_adr_0273_stats(self, budget: List[Dict]) -> Dict:
        """ADR-0273: Compute attention budget allocation metrics."""
        if not budget:
            return {"count": 0, "avg_match": 0.0, "critical_pct": 0.0}

        critical = len([b for b in budget if b.get("budget_allocated") == "critical"])
        matches = [b.get("match_score", 0.5) for b in budget]

        return {
            "count": len(budget),
            "critical_pct": round((critical / len(budget)) * 100, 1),
            "avg_match": round(sum(matches) / len(matches), 3),
            "total_tokens": sum(b.get("tokens_used", 0) for b in budget),
        }


# Global reader
reader = MeasurementReader()


@app.route("/api/v1/measurements/latest", methods=["GET"])
def get_latest_measurements():
    """Get all 4 measurement tracks (latest)."""
    days = request.args.get("days", 7, type=int)
    records = reader.get_latest_records(days=days)

    # Compute stats for each track
    stats = {
        "adr_0270_uncertainty": reader.compute_adr_0270_stats(records["predictions"]),
        "adr_0271_feedback": reader.compute_adr_0271_stats(records["feedback"]),
        "adr_0272_preferences": reader.compute_adr_0272_stats(records["preferences"]),
        "adr_0273_budget": reader.compute_adr_0273_stats(records["budget"]),
    }

    return jsonify({
        "timestamp": records["timestamp"],
        "days_lookback": days,
        "stats": stats,
        "record_counts": {
            "predictions": len(records["predictions"]),
            "feedback": len(records["feedback"]),
            "user_choices": len(records["preferences"]),
            "budget_allocations": len(records["budget"]),
        },
    })


@app.route("/api/v1/measurements/predictions", methods=["GET"])
def get_predictions():
    """ADR-0270: Confidence predictions vs actual outcomes."""
    records = reader.get_latest_records(days=7)
    predictions = records["predictions"][:100]

    return jsonify({
        "timestamp": datetime.utcnow().isoformat(),
        "track": "ADR-0270 Uncertainty Quantification",
        "count": len(predictions),
        "stats": reader.compute_adr_0270_stats(predictions),
        "recent": predictions[:20],  # Latest 20
    })


@app.route("/api/v1/measurements/feedback", methods=["GET"])
def get_feedback():
    """ADR-0271: Bayesian learning feedback loop."""
    records = reader.get_latest_records(days=7)
    feedback = records["feedback"][:100]

    return jsonify({
        "timestamp": datetime.utcnow().isoformat(),
        "track": "ADR-0271 Outcome Feedback Loop",
        "count": len(feedback),
        "stats": reader.compute_adr_0271_stats(feedback),
        "recent": feedback[:20],
    })


@app.route("/api/v1/measurements/preferences", methods=["GET"])
def get_preferences():
    """ADR-0272: User decision style preferences."""
    records = reader.get_latest_records(days=7)
    choices = records["preferences"][:100]

    return jsonify({
        "timestamp": datetime.utcnow().isoformat(),
        "track": "ADR-0272 User Preferences",
        "count": len(choices),
        "stats": reader.compute_adr_0272_stats(choices),
        "recent": choices[:20],
    })


@app.route("/api/v1/measurements/budget", methods=["GET"])
def get_budget():
    """ADR-0273: Attention budget allocation patterns."""
    records = reader.get_latest_records(days=7)
    budget = records["budget"][:100]

    return jsonify({
        "timestamp": datetime.utcnow().isoformat(),
        "track": "ADR-0273 Attention Budget",
        "count": len(budget),
        "stats": reader.compute_adr_0273_stats(budget),
        "recent": budget[:20],
    })


@app.route("/api/v1/talent/score", methods=["GET"])
def get_talent_score():
    """Get Your Talent Score (CONCEPT-0003)."""
    days = request.args.get("days", 7, type=int)
    try:
        calculator = get_talent_calculator()
        report = calculator.generate_talent_report(days=days)
        return jsonify(report)
    except Exception as e:
        logger.error(f"Talent score computation failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/talent/ranking", methods=["GET"])
def get_talent_ranking():
    """Get context ranking from Your Talent."""
    days = request.args.get("days", 7, type=int)
    try:
        calculator = get_talent_calculator()
        records = calculator.get_recent_records(days=days)
        ranking = calculator.compute_context_ranking(records)
        return jsonify({
            "timestamp": datetime.utcnow().isoformat(),
            "ranking": ranking,
            "count": len(ranking),
        })
    except Exception as e:
        logger.error(f"Context ranking failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/talent/events", methods=["GET"])
def get_talent_events():
    """Get learning events timeline."""
    days = request.args.get("days", 7, type=int)
    try:
        calculator = get_talent_calculator()
        records = calculator.get_recent_records(days=days)
        events = calculator.compute_learning_events(records)
        return jsonify({
            "timestamp": datetime.utcnow().isoformat(),
            "events": events,
            "count": len(events),
        })
    except Exception as e:
        logger.error(f"Learning events failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "ADR-0274 API"}), 200


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=5000, debug=True)
