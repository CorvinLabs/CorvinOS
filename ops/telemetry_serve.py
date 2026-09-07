#!/usr/bin/env python3
"""
CorvinOS Live Telemetry Dashboard — Standalone Server
Phase 5 | 2026-08-31

Quick Start:
  python3 telemetry_serve.py                    # Start on localhost:8080
  python3 telemetry_serve.py --port 9000        # Custom port
  python3 telemetry_serve.py --host 0.0.0.0     # Listen on all interfaces

Then open: http://localhost:8080/stats
"""

from __future__ import annotations

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import threading
import time

# Add repo to path
sys.path.insert(0, str(Path(__file__).parent))

from core.telemetry.central_aggregator import TelemetryAggregator

def main():
    parser = argparse.ArgumentParser(
        description="CorvinOS Live Telemetry Dashboard Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 telemetry_serve.py                 # Start on 127.0.0.1:8080
  python3 telemetry_serve.py --host 0.0.0.0  # Listen on all interfaces
  python3 telemetry_serve.py --port 9000     # Custom port

Then open: http://localhost:8080/stats
        """
    )
    parser.add_argument('--host', default='127.0.0.1', help='Host to listen on (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=8080, help='Port to listen on (default: 8080)')
    parser.add_argument('--demo', action='store_true', default=True, help='Use demo data')
    parser.add_argument('--no-demo', action='store_true', help='Use real aggregation (wait for submissions)')

    args = parser.parse_args()

    aggregator = TelemetryAggregator()

    if args.demo:
        # Load demo data
        print("📊 Using demo data (3 worldwide instances)")
        demo_instances = [
            {
                'instance_id': 'corvin-us-east-1-abc123',
                'hostname': 'New York Instance',
                'location': '40.7128,-74.0060',
                'turn_count': 1250,
                'total_tokens': 12500,
                'savings_percent': 28.5,
                'uptime': '42h 15m',
                'version': '0.10.109'
            },
            {
                'instance_id': 'corvin-eu-west-1-def456',
                'hostname': 'London Instance',
                'location': '51.5074,-0.1278',
                'turn_count': 980,
                'total_tokens': 9800,
                'savings_percent': 25.2,
                'uptime': '38h 42m',
                'version': '0.10.109'
            },
            {
                'instance_id': 'corvin-ap-southeast-1-ghi789',
                'hostname': 'Sydney Instance',
                'location': '-33.8688,151.2093',
                'turn_count': 756,
                'total_tokens': 7560,
                'savings_percent': 22.8,
                'uptime': '31h 58m',
                'version': '0.10.109'
            }
        ]

        for inst in demo_instances:
            aggregator.register_instance(
                instance_id=inst['instance_id'],
                hostname=inst['hostname'],
                location=inst['location'],
                tenant_id='_default'
            )
            aggregator.submit_telemetry({
                'instance_id': inst['instance_id'],
                'turn_count': inst['turn_count'],
                'total_tokens': inst['total_tokens'],
                'savings_percent': inst['savings_percent'],
            })

    # Try to start Flask server
    try:
        from flask import Flask, jsonify, request, send_file
        from flask_cors import CORS
        from core.telemetry.api_server import TelemetryAPIServer

        server = TelemetryAPIServer(
            aggregator=aggregator,
            html_path=Path(__file__).parent / 'docs' / 'stats.html',
            host=args.host,
            port=args.port
        )

        print("\n" + "="*70)
        print("⚡ CorvinOS Telemetry API Server")
        print("="*70)
        print(f"\n📊 Dashboard:  http://{args.host}:{args.port}/stats")
        print(f"🔌 API:        http://{args.host}:{args.port}/api/metrics/stats")
        print(f"📡 Submit:     POST http://{args.host}:{args.port}/api/telemetry/submit")
        print(f"📋 Instances:  http://{args.host}:{args.port}/api/telemetry/instances")
        print(f"🩺 Health:     http://{args.host}:{args.port}/health")
        print("\n💡 Press Ctrl+C to stop")
        print("="*70 + "\n")

        server.run(debug=False)

    except ImportError:
        # Flask not available — serve demo HTML + JSON API via HTTP.server
        print("\n⚠️  Flask not available. Starting basic HTTP server...")
        print("   Note: This server is read-only (no submissions accepted)")
        print("   To enable full functionality, install: pip install flask flask-cors\n")

        start_basic_server(aggregator, args.host, args.port)


def start_basic_server(aggregator: TelemetryAggregator, host: str, port: int) -> None:
    """Start a basic HTTP server without Flask (fallback)."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs
    import json

    class TelemetryHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            """Handle GET requests."""
            parsed_path = urlparse(self.path)
            path = parsed_path.path
            query = parse_qs(parsed_path.query)

            if path == '/':
                self.send_redirect('/stats')
            elif path == '/stats':
                self.send_html_dashboard()
            elif path == '/api/metrics/stats':
                tenant_id = query.get('tenant_id', ['_default'])[0]
                self.send_json(aggregator.get_cluster_stats(tenant_id).to_dict())
            elif path == '/api/telemetry/instances':
                tenant_id = query.get('tenant_id', ['_default'])[0]
                instances = aggregator.get_active_instances(tenant_id)
                self.send_json({'instances': instances})
            elif path == '/health':
                self.send_json({
                    'status': 'ok',
                    'active_instances': len(aggregator.get_active_instances('_default')),
                    'subscribers': aggregator.get_active_subscribers()
                })
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            """Handle POST requests."""
            if self.path == '/api/telemetry/submit':
                try:
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = self.rfile.read(content_length)
                    data = json.loads(body)
                    aggregator.submit_telemetry(data)
                    self.send_json({'status': 'ok'})
                except Exception as e:
                    self.send_json({'error': str(e)}, status=400)
            else:
                self.send_error(404)

        def send_html_dashboard(self) -> None:
            """Serve dashboard HTML."""
            html_path = Path(__file__).parent / 'docs' / 'stats.html'
            if html_path.exists():
                with open(html_path) as f:
                    html = f.read()
                html = html.replace(
                    "'https://api.corvin-labs.com/api/metrics/stats'",
                    f"'http://{host}:{port}/api/metrics/stats'"
                )
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(html.encode())
            else:
                self.send_error(404, "Dashboard not found")

        def send_json(self, data: Dict[str, Any], status: int = 200) -> None:
            """Send JSON response."""
            self.send_response(status)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def send_redirect(self, location: str) -> None:
            """Redirect to another path."""
            self.send_response(302)
            self.send_header('Location', location)
            self.end_headers()

        def log_message(self, format_str: str, *args) -> None:
            """Suppress default logging."""
            pass

    # Start server
    server_address = (host, port)
    httpd = HTTPServer(server_address, TelemetryHandler)

    print("\n" + "="*70)
    print("⚡ CorvinOS Telemetry HTTP Server (Basic)")
    print("="*70)
    print(f"\n📊 Dashboard:  http://{host}:{port}/stats")
    print(f"🔌 API:        http://{host}:{port}/api/metrics/stats")
    print(f"📋 Instances:  http://{host}:{port}/api/telemetry/instances")
    print(f"🩺 Health:     http://{host}:{port}/health")
    print("\n💡 Press Ctrl+C to stop")
    print("="*70 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n✋ Shutting down...")
        httpd.shutdown()


if __name__ == '__main__':
    main()
