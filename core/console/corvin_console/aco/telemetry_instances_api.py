"""Telemetry Instances API — Live World Map data aggregation.

Endpoint: GET /api/v1/telemetry/instances/live
Returns JSON with active instance counts per country, continent, activity metrics.

This module handles aggregation of instance-level telemetry (country_code, continent,
timezone_offset, last_seen timestamp, activity_rate) into a live stats view for the
public world map dashboard. No PII, only closed enums and aggregated metrics.

Rate limit: 60-second cache; WebSocket or poll-based refresh supported.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Activity window definitions (based on last_ping timestamp)
ACTIVITY_WINDOW_NOW = 3600  # 1 hour = "active right now"
ACTIVITY_WINDOW_TODAY = 86400  # 24 hours
ACTIVITY_WINDOW_WEEK = 7 * 86400  # 7 days
ACTIVITY_WINDOW_MONTH = 30 * 86400  # 30 days

# Cache TTL
STATS_CACHE_TTL_S = 60

# Continent-to-name mapping
CONTINENT_NAMES = {
    "Africa": "Africa",
    "Americas": "Americas (North & South)",
    "Asia": "Asia-Pacific",
    "Europe": "Europe",
    "Oceania": "Oceania",
    "Unknown": "Unknown",
}


class InstanceStatsAggregator:
    """Aggregates live instance telemetry per country/continent."""

    def __init__(self, telemetry_db_path: Optional[Path] = None):
        """Initialize with path to telemetry instance database.

        Args:
            telemetry_db_path: Path to .jsonl or .db file with instance records.
                               If None, uses in-memory aggregation.
        """
        self.db_path = telemetry_db_path
        self.cache: dict[str, Any] = {}
        self.cache_timestamp = 0.0
        self.instances: dict[str, dict[str, Any]] = {}  # instance_id -> metadata

    def load_instances(self, records: list[dict[str, Any]]) -> None:
        """Load instance records (from ping telemetry).

        Each record should have:
        - instance_id: unique identifier (uuid4)
        - country_code: ISO 3166-1 alpha-2
        - continent: Africa|Americas|Asia|Europe|Oceania|Unknown
        - timezone_offset: seconds from UTC
        - timestamp: when this ping was received
        """
        self.instances = {}
        for rec in records:
            try:
                iid = rec.get("instance_id", "")
                if not iid:
                    continue
                self.instances[iid] = {
                    "country_code": rec.get("country_code", "XX"),
                    "continent": rec.get("continent", "Unknown"),
                    "timezone_offset": rec.get("timezone_offset", 0),
                    "timestamp": rec.get("timestamp", 0),
                    "last_activity": rec.get("timestamp", 0),
                }
            except (KeyError, TypeError, ValueError):
                pass  # Skip malformed records

    def aggregate(self) -> dict[str, Any]:
        """Aggregate instances into per-country stats.

        Returns JSON structure:
        {
            "timestamp": 1234567890,
            "total_active": 12345,
            "total_active_now": 2345,
            "total_active_today": 5678,
            "total_active_week": 8901,
            "total_active_month": 10234,
            "continents": {
                "Europe": {
                    "name": "Europe",
                    "count": 5000,
                    "activity_pct": 75,
                    "countries": {...}
                }
            },
            "countries": {
                "DE": {
                    "name": "Germany",
                    "count": 1234,
                    "continent": "Europe",
                    "activity_pct": 78,
                    "activity_now": 234,
                    "activity_today": 567,
                },
                "US": {...}
            }
        }
        """
        now = datetime.now(timezone.utc).timestamp()
        cutoff_now = now - ACTIVITY_WINDOW_NOW
        cutoff_today = now - ACTIVITY_WINDOW_TODAY
        cutoff_week = now - ACTIVITY_WINDOW_WEEK
        cutoff_month = now - ACTIVITY_WINDOW_MONTH

        # Aggregate per country
        country_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "count_now": 0,
                "count_today": 0,
                "count_week": 0,
                "count_month": 0,
                "continents": [],
            }
        )

        for iid, meta in self.instances.items():
            cc = meta["country_code"]
            ts = meta.get("last_activity", 0)

            country_stats[cc]["count"] += 1
            if ts > cutoff_month:
                country_stats[cc]["count_month"] += 1
            if ts > cutoff_week:
                country_stats[cc]["count_week"] += 1
            if ts > cutoff_today:
                country_stats[cc]["count_today"] += 1
            if ts > cutoff_now:
                country_stats[cc]["count_now"] += 1

            continent = meta.get("continent", "Unknown")
            if continent not in country_stats[cc]["continents"]:
                country_stats[cc]["continents"].append(continent)

        # Aggregate per continent
        continent_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "count_now": 0,
                "count_today": 0,
                "count_week": 0,
                "count_month": 0,
                "countries": {},
            }
        )

        for cc, stats in country_stats.items():
            continent = stats["continents"][0] if stats["continents"] else "Unknown"
            continent_stats[continent]["count"] += stats["count"]
            continent_stats[continent]["count_now"] += stats["count_now"]
            continent_stats[continent]["count_today"] += stats["count_today"]
            continent_stats[continent]["count_week"] += stats["count_week"]
            continent_stats[continent]["count_month"] += stats["count_month"]
            continent_stats[continent]["countries"][cc] = {
                "name": self._country_name(cc),
                "count": stats["count"],
                "activity_now": stats["count_now"],
                "activity_today": stats["count_today"],
                "activity_week": stats["count_week"],
                "activity_month": stats["count_month"],
                "activity_pct": int(
                    100.0 * (stats["count_today"] / max(1, stats["count"]))
                ),
            }

        # Calculate total active
        total = sum(s["count"] for s in country_stats.values())
        total_now = sum(s["count_now"] for s in country_stats.values())
        total_today = sum(s["count_today"] for s in country_stats.values())
        total_week = sum(s["count_week"] for s in country_stats.values())
        total_month = sum(s["count_month"] for s in country_stats.values())

        # Build final response
        result = {
            "timestamp": int(now),
            "total_active": total,
            "total_active_now": total_now,
            "total_active_today": total_today,
            "total_active_week": total_week,
            "total_active_month": total_month,
            "continents": {},
            "countries": {},
        }

        # Add continent stats
        for continent, stats in sorted(continent_stats.items()):
            result["continents"][continent] = {
                "name": CONTINENT_NAMES.get(continent, continent),
                "count": stats["count"],
                "activity_now": stats["count_now"],
                "activity_today": stats["count_today"],
                "activity_pct": int(
                    100.0 * (stats["count_today"] / max(1, stats["count"]))
                ),
                "countries": stats["countries"],
            }

        # Add country stats at top level
        for cc, cstats in sorted(continent_stats.items()):
            for cc2, c2stats in cstats["countries"].items():
                result["countries"][cc2] = c2stats
                result["countries"][cc2]["continent"] = cc

        return result

    def get_cached_stats(self) -> dict[str, Any]:
        """Get cached stats if fresh, otherwise aggregate and cache."""
        now = time.time()
        if now - self.cache_timestamp < STATS_CACHE_TTL_S and self.cache:
            return self.cache

        self.cache = self.aggregate()
        self.cache_timestamp = now
        return self.cache

    @staticmethod
    def _country_name(code: str) -> str:
        """Map ISO 3166-1 alpha-2 to country name."""
        names = {
            "DE": "Germany",
            "US": "United States",
            "GB": "United Kingdom",
            "FR": "France",
            "ES": "Spain",
            "IT": "Italy",
            "NL": "Netherlands",
            "BE": "Belgium",
            "SE": "Sweden",
            "CH": "Switzerland",
            "AT": "Austria",
            "PL": "Poland",
            "CZ": "Czechia",
            "BR": "Brazil",
            "CA": "Canada",
            "AU": "Australia",
            "JP": "Japan",
            "CN": "China",
            "IN": "India",
            "SG": "Singapore",
            "NZ": "New Zealand",
            "RU": "Russia",
            "MX": "Mexico",
            "KR": "South Korea",
            "TR": "Turkey",
            "AR": "Argentina",
            "ZA": "South Africa",
            "XX": "Unknown/VPN",
        }
        return names.get(code, code)


def load_telemetry_instances_from_file(path: Path) -> list[dict[str, Any]]:
    """Load telemetry instances from a .jsonl file.

    Expected format (one JSON object per line):
    {"instance_id": "...", "country_code": "DE", "continent": "Europe", ...}

    Returns list of records (empty if file doesn't exist).
    """
    records = []
    if not path.exists():
        return records

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError:
                    pass  # Skip malformed lines
    except (OSError, IOError):
        pass

    return records


def create_fastapi_route(app: Any) -> None:
    """Create FastAPI route for /api/v1/telemetry/instances/live.

    Add this to your FastAPI application:
        from telemetry_instances_api import create_fastapi_route
        create_fastapi_route(app)
    """
    try:
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse
    except ImportError:
        logger.error("FastAPI not installed; skipping route creation")
        return

    aggregator = InstanceStatsAggregator()

    @app.get("/api/v1/telemetry/instances/live", tags=["telemetry"])
    async def get_live_instances() -> JSONResponse:
        """Get live instance counts per country/continent.

        Returns aggregated, anonymized stats suitable for public dashboard.
        Updated every 60 seconds (cached).
        """
        try:
            # Load latest telemetry records
            # This is a stub — you would load from your actual telemetry store
            # (e.g., database, time-series DB, or aggregation endpoint)
            aggregator.instances = {}  # Would be populated from real data source

            stats = aggregator.get_cached_stats()
            return JSONResponse(content=stats)
        except Exception as e:
            logger.error("Failed to get live instances: %s", e)
            raise HTTPException(status_code=500, detail="Internal server error")


def create_flask_route(app: Any) -> None:
    """Create Flask route for /api/v1/telemetry/instances/live.

    Add this to your Flask application:
        from telemetry_instances_api import create_flask_route
        create_flask_route(app)
    """
    try:
        from flask import jsonify
    except ImportError:
        logger.error("Flask not installed; skipping route creation")
        return

    aggregator = InstanceStatsAggregator()

    @app.route("/api/v1/telemetry/instances/live", methods=["GET"])
    def get_live_instances() -> Any:
        """Get live instance counts per country/continent."""
        try:
            aggregator.instances = {}  # Would be populated from real data source
            stats = aggregator.get_cached_stats()
            return jsonify(stats)
        except Exception as e:
            logger.error("Failed to get live instances: %s", e)
            return jsonify({"error": "Internal server error"}), 500


# Example: standalone HTTP server
if __name__ == "__main__":
    import http.server
    import socketserver
    from urllib.parse import urlparse

    aggregator = InstanceStatsAggregator()

    class TelemHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/v1/telemetry/instances/live":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                stats = aggregator.get_cached_stats()
                self.wfile.write(json.dumps(stats).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

    with socketserver.TCPServer(("", 8000), TelemHandler) as httpd:
        print("Telemetry API running on http://localhost:8000/api/v1/telemetry/instances/live")
        httpd.serve_forever()
