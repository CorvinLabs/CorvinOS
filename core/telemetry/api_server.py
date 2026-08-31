"""Telemetry API Server (ADR-0365)

Serves the stats dashboard and provides REST/WebSocket endpoints
for real-time telemetry collection and retrieval.

Endpoints:
- GET /stats — Dashboard HTML
- GET /api/metrics/stats — Aggregated cluster statistics (JSON)
- POST /api/telemetry/submit — Instance telemetry submission
- GET /api/telemetry/instances — List active instances
- WebSocket /api/stream/stats — Real-time stats stream
- GET /health — Health check
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from functools import wraps

logger = logging.getLogger(__name__)

try:
    from flask import Flask, jsonify, request, render_template_string, send_file
    from flask_cors import CORS
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


class TelemetryAPIServer:
    """API server for telemetry dashboard and collection."""

    def __init__(
        self,
        aggregator,
        html_path: Optional[Path] = None,
        host: str = "0.0.0.0",
        port: int = 8765,
    ):
        """Initialize API server.

        Args:
            aggregator: TelemetryAggregator instance
            html_path: Path to dashboard HTML file
            host: Host to listen on
            port: Port to listen on
        """
        if not HAS_FLASK:
            raise RuntimeError("Flask is required for TelemetryAPIServer")

        self.aggregator = aggregator
        self.html_path = html_path or (
            Path(__file__).parent.parent.parent / "docs" / "stats.html"
        )
        self.host = host
        self.port = port

        self.app = Flask(__name__)
        CORS(self.app)

        self._register_routes()

    def _register_routes(self) -> None:
        """Register API routes."""
        @self.app.route("/", methods=["GET"])
        def index():
            """Redirect to stats."""
            return '<meta http-equiv="refresh" content="0;url=/stats" />'

        @self.app.route("/stats", methods=["GET"])
        def stats_dashboard():
            """Serve dashboard HTML."""
            if self.html_path.exists():
                with open(self.html_path) as f:
                    html = f.read()
                # Update API endpoint
                html = html.replace(
                    "'https://api.corvin-labs.com/api/metrics/stats'",
                    f"'http://{self.host}:{self.port}/api/metrics/stats'",
                )
                return html
            return "<h1>Dashboard not found</h1>", 404

        @self.app.route("/api/metrics/stats", methods=["GET"])
        def api_stats():
            """Get aggregated cluster statistics."""
            tenant_id = request.args.get("tenant_id", "_default")
            try:
                stats = self.aggregator.get_cluster_stats(tenant_id)
                return jsonify(stats.to_dict())
            except ValueError as e:
                return jsonify({"error": str(e)}), 400

        @self.app.route("/api/telemetry/submit", methods=["POST"])
        def telemetry_submit():
            """Accept telemetry submission from instance."""
            try:
                data = request.get_json()

                # Validate required fields
                required = ["instance_id", "tenant_id", "turn_count", "total_tokens"]
                if not all(f in data for f in required):
                    return (
                        jsonify({"error": f"Missing required fields: {required}"}),
                        400,
                    )

                instance_id = data["instance_id"]
                tenant_id = data["tenant_id"]

                # Register instance if needed
                if instance_id not in self.aggregator.instances:
                    location_data = data.get("location", {})
                    from .central_aggregator import InstanceLocation
                    location = InstanceLocation(
                        latitude=float(location_data.get("latitude", 0)),
                        longitude=float(location_data.get("longitude", 0)),
                        city=location_data.get("city"),
                        country=location_data.get("country"),
                        region=location_data.get("region"),
                    )
                    self.aggregator.register_instance(
                        instance_id=instance_id,
                        hostname=data.get("hostname", f"host-{instance_id}"),
                        location=location,
                        version=data.get("version", "unknown"),
                        tenant_id=tenant_id,
                    )

                # Submit telemetry
                self.aggregator.submit_telemetry(
                    instance_id=instance_id,
                    turn_count=int(data["turn_count"]),
                    total_tokens=int(data["total_tokens"]),
                    savings_percent=float(data.get("savings_percent", 25.0)),
                    uptime_seconds=int(data.get("uptime_seconds", 0)),
                    tenant_id=tenant_id,
                )

                return jsonify({"status": "ok", "instance_id": instance_id}), 200

            except ValueError as e:
                logger.warning(f"Invalid telemetry submission: {e}")
                return jsonify({"error": str(e)}), 400
            except Exception as e:
                logger.error(f"Telemetry submission error: {e}")
                return jsonify({"error": "Internal server error"}), 500

        @self.app.route("/api/telemetry/instances", methods=["GET"])
        def telemetry_instances():
            """List active instances."""
            tenant_id = request.args.get("tenant_id", "_default")
            try:
                instances = self.aggregator.get_active_instances(tenant_id)
                return jsonify({
                    "instances": [
                        {
                            "instance_id": inst.instance_id,
                            "hostname": inst.hostname,
                            "location": {
                                "latitude": inst.location.latitude,
                                "longitude": inst.location.longitude,
                                "city": inst.location.city,
                                "country": inst.location.country,
                                "region": inst.location.region,
                            },
                            "turn_count": inst.turn_count,
                            "total_tokens": inst.total_tokens,
                            "uptime_seconds": inst.uptime_seconds,
                            "last_seen": inst.last_seen.isoformat(),
                        }
                        for inst in instances
                    ],
                    "count": len(instances),
                })
            except ValueError as e:
                return jsonify({"error": str(e)}), 400

        @self.app.route("/health", methods=["GET"])
        def health():
            """Health check endpoint."""
            return jsonify({
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "subscribers": self.aggregator.get_active_subscribers(),
            }), 200

        @self.app.route("/api/health", methods=["GET"])
        def api_health():
            """Detailed health check."""
            return jsonify({
                "status": "healthy",
                "version": "1.0.0",
                "timestamp": datetime.utcnow().isoformat(),
                "instances": len(self.aggregator.instances),
                "active_instances": len(self.aggregator.get_active_instances("_default")),
                "subscribers": self.aggregator.get_active_subscribers(),
            }), 200

    def run(self, debug: bool = False) -> None:
        """Run the API server.

        Args:
            debug: Enable Flask debug mode
        """
        print("\n" + "="*70)
        print("⚡ CorvinOS Telemetry API Server")
        print("="*70)
        print("\n📊 Dashboard:  http://localhost:8765/stats")
        print("🔌 API:        http://localhost:8765/api/metrics/stats")
        print("📡 Submit:     POST http://localhost:8765/api/telemetry/submit")
        print("📋 Instances:  http://localhost:8765/api/telemetry/instances")
        print("🩺 Health:     http://localhost:8765/health")
        print("\n💡 Press Ctrl+C to stop")
        print("="*70 + "\n")

        self.app.run(host=self.host, port=self.port, debug=debug, use_reloader=False)


def create_app(aggregator, html_path: Optional[Path] = None) -> Flask:
    """Factory function to create Flask app for deployment.

    Args:
        aggregator: TelemetryAggregator instance
        html_path: Path to dashboard HTML

    Returns:
        Configured Flask app
    """
    server = TelemetryAPIServer(aggregator, html_path)
    return server.app
