#!/usr/bin/env python3
"""
Export ADR-0274 measurement data to Corvin-Website stats JSON

Reads aggregated measurement data and writes to:
  /home/shumway/projects/Corvin-Website/api/v1/telemetry/measurements/latest.json

This is called by Railway every hour to sync Week 6 data.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from api_server import MeasurementReader

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def export_measurements_to_website(
    measurements_dir: Path = None,
    output_dir: Path = None,
) -> bool:
    """
    Export measurement stats to website JSON format.

    Args:
        measurements_dir: Directory with measurement queue files
        output_dir: Output directory for website JSON

    Returns:
        True if successful, False otherwise
    """
    if measurements_dir is None:
        measurements_dir = Path.home() / ".corvin" / "measurement"

    if output_dir is None:
        output_dir = Path.home() / "projects" / "Corvin-Website" / "api" / "v1" / "telemetry" / "measurements"

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        reader = MeasurementReader(measurements_dir)

        # Read latest 7 days of measurements
        records = reader.get_latest_records(days=7)

        # Compute stats for each track
        stats = {
            "adr_0270_uncertainty": reader.compute_adr_0270_stats(records["predictions"]),
            "adr_0271_feedback": reader.compute_adr_0271_stats(records["feedback"]),
            "adr_0272_preferences": reader.compute_adr_0272_stats(records["preferences"]),
            "adr_0273_budget": reader.compute_adr_0273_stats(records["budget"]),
        }

        # Build output JSON
        output = {
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0",
            "source": "CorvinOS ADR-0274 Week 6 Measurement Phase",
            "measurement_phase": {
                "status": "LIVE",
                "start_date": "2026-08-08",
                "end_date": "2026-08-17",
                "days_remaining": 7,
            },
            "record_counts": {
                "predictions": len(records["predictions"]),
                "feedback": len(records["feedback"]),
                "user_choices": len(records["preferences"]),
                "budget_allocations": len(records["budget"]),
            },
            "stats": stats,
            "summary": {
                "total_records": (
                    len(records["predictions"])
                    + len(records["feedback"])
                    + len(records["preferences"])
                    + len(records["budget"])
                ),
                "tracks_active": 4,
                "measurement_quality": "PRODUCTION",
            },
        }

        # Write to JSON file
        output_file = output_dir / "latest.json"
        with open(output_file, "w") as f:
            json.dump(output, f, indent=2)

        logger.info(f"✓ Exported measurements to {output_file}")

        # Also write individual track files
        for track_name, track_data in [
            ("predictions", records["predictions"]),
            ("feedback", records["feedback"]),
            ("user_choices", records["preferences"]),
            ("budget_allocations", records["budget"]),
        ]:
            track_file = output_dir / f"{track_name}.json"
            with open(track_file, "w") as f:
                json.dump({
                    "timestamp": datetime.utcnow().isoformat(),
                    "track": track_name,
                    "count": len(track_data),
                    "recent": track_data[:100],  # Latest 100 records
                }, f, indent=2)

        logger.info(f"✓ Exported {len(records['predictions'])} predictions")
        logger.info(f"✓ Exported {len(records['feedback'])} feedback records")
        logger.info(f"✓ Exported {len(records['preferences'])} user choices")
        logger.info(f"✓ Exported {len(records['budget'])} budget allocations")

        return True

    except Exception as e:
        logger.error(f"✗ Export failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = export_measurements_to_website()
    exit(0 if success else 1)
