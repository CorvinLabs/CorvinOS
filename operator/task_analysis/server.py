"""HTTP server for TaskEngine with Prometheus metrics export."""

import argparse
import json
import logging
import sys
import time
from typing import Dict, Any

from .engine import TaskEngine, EngineError
from .normalizer import InsufficientTaskInfo
from .metrics import TaskMetrics

try:
    from prometheus_client import make_wsgi_app, CollectorRegistry
    from wsgiref.simple_server import make_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


logger = logging.getLogger(__name__)


class TaskEngineServer:
    """Simple HTTP server for TaskEngine routing."""

    def __init__(self, host: str = "localhost", port: int = 8765, enable_metrics: bool = True):
        """Initialize server.

        Args:
            host: Bind address.
            port: Bind port.
            enable_metrics: Enable Prometheus metrics export.
        """
        self.host = host
        self.port = port
        self.engine = TaskEngine()
        self.metrics = TaskMetrics() if enable_metrics else None
        self.enable_metrics = enable_metrics and PROMETHEUS_AVAILABLE

    def health(self, environ: Dict, start_response) -> list:
        """Health check endpoint."""
        response = {"status": "healthy", "timestamp": time.time()}
        body = json.dumps(response).encode("utf-8")

        start_response("200 OK", [("Content-Type", "application/json")])
        return [body]

    def analyze(self, environ: Dict, start_response) -> list:
        """Analyze task endpoint.

        POST /analyze
        Body: {"raw_task": "..."}

        Returns: {
            "decision_target": "native|acs|tde",
            "carve_out_reason": "...",
            "confidence": 0.85,
            "model_recommendation": "haiku|opus",
            "task_complexity": 0.62,
            "estimated_cost_usd": 0.05
        }
        """
        try:
            # Read request body
            content_length = int(environ.get("CONTENT_LENGTH", 0))
            body = environ["wsgi.input"].read(content_length).decode("utf-8")
            data = json.loads(body) if body else {}

            raw_task = data.get("raw_task")
            if not raw_task:
                start_response("400 Bad Request", [("Content-Type", "application/json")])
                return [json.dumps({"error": "Missing raw_task"}).encode("utf-8")]

            # Measure latency
            start = time.perf_counter()

            try:
                # Route task through engine
                result = self.engine.route_task(raw_task)

                elapsed_ms = (time.perf_counter() - start) * 1000

                response = {
                    "status": "success",
                    "decision_target": result.decision_target.value,
                    "carve_out_reason": result.carve_out_reason,
                    "confidence": result.confidence,
                    "model_recommendation": result.model_recommendation,
                    "task_complexity": result.task_complexity,
                    "estimated_cost_usd": result.estimated_cost_usd,
                    "latency_ms": elapsed_ms,
                }

                body = json.dumps(response).encode("utf-8")
                start_response("200 OK", [("Content-Type", "application/json")])
                return [body]

            except InsufficientTaskInfo as e:
                start_response("400 Bad Request", [("Content-Type", "application/json")])
                return [json.dumps({"error": str(e), "code": "insufficient_task_info"}).encode("utf-8")]

            except EngineError as e:
                start_response("500 Internal Server Error", [("Content-Type", "application/json")])
                return [json.dumps({"error": str(e), "code": "engine_error"}).encode("utf-8")]

        except json.JSONDecodeError:
            start_response("400 Bad Request", [("Content-Type", "application/json")])
            return [json.dumps({"error": "Invalid JSON"}).encode("utf-8")]

        except Exception as e:
            logger.exception("Unexpected error in /analyze")
            start_response("500 Internal Server Error", [("Content-Type", "application/json")])
            return [json.dumps({"error": str(e)}).encode("utf-8")]

    def metrics(self, environ: Dict, start_response) -> list:
        """Prometheus metrics endpoint."""
        if not self.enable_metrics:
            start_response("404 Not Found", [])
            return [b"Prometheus not available"]

        # Export Prometheus metrics
        try:
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

            body = generate_latest()
            start_response("200 OK", [("Content-Type", CONTENT_TYPE_LATEST)])
            return [body]
        except Exception as e:
            logger.exception("Error exporting metrics")
            start_response("500 Internal Server Error", [])
            return [str(e).encode("utf-8")]

    def application(self, environ: Dict, start_response) -> list:
        """WSGI application router."""
        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET")

        if path == "/health":
            return self.health(environ, start_response)
        elif path == "/analyze" and method == "POST":
            return self.analyze(environ, start_response)
        elif path == "/metrics":
            return self.metrics(environ, start_response)
        else:
            start_response("404 Not Found", [("Content-Type", "application/json")])
            return [json.dumps({"error": "Not found"}).encode("utf-8")]

    def run(self) -> None:
        """Start server."""
        print(f"Starting TaskEngine server on {self.host}:{self.port}")
        print(f"  Endpoints:")
        print(f"    GET  /health       — Health check")
        print(f"    POST /analyze      — Route a task")
        print(f"    GET  /metrics      — Prometheus metrics")
        print()
        print(f"  Prometheus metrics: {'✅ ENABLED' if self.enable_metrics else '❌ DISABLED'}")
        print()

        httpd = make_server(self.host, self.port, self.application)
        print(f"✅ Server ready. Press Ctrl+C to stop.")
        httpd.serve_forever()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="TaskEngine HTTP server")
    parser.add_argument("--host", default="localhost", help="Bind address (default: localhost)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    parser.add_argument(
        "--no-metrics", action="store_true", help="Disable Prometheus metrics export"
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO)")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        server = TaskEngineServer(
            host=args.host, port=args.port, enable_metrics=not args.no_metrics
        )
        server.run()
    except KeyboardInterrupt:
        print("\n✅ Server stopped.")
        sys.exit(0)
    except Exception as e:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()
