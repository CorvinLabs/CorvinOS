#!/usr/bin/env python3
"""
Standalone Stats Dashboard Server
Serves the dashboard HTML + Mock API on localhost:8080
No Docker required - pure Python!
"""

import os
import sys
import json
import random
from datetime import datetime
from pathlib import Path

# Try to import Flask, fallback to http.server
try:
    from flask import Flask, jsonify, send_file
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs


def get_mock_stats():
    """Generate mock cluster statistics."""
    instances = [
        {
            "instance_id": "prod-us-east-1",
            "hostname": "prod.us-east-1.corvin.local",
            "location": "40.7128,-74.0060",  # New York
            "turn_count": 1250 + random.randint(0, 10),
            "total_tokens": 12500 + random.randint(0, 100),
            "savings_percent": 28.5,
        },
        {
            "instance_id": "prod-eu-west-1",
            "hostname": "prod.eu-west-1.corvin.local",
            "location": "51.5074,-0.1278",  # London
            "turn_count": 980 + random.randint(0, 10),
            "total_tokens": 9800 + random.randint(0, 100),
            "savings_percent": 25.2,
        },
        {
            "instance_id": "prod-ap-southeast-1",
            "hostname": "prod.ap-southeast-1.corvin.local",
            "location": "-33.8688,151.2093",  # Sydney
            "turn_count": 756 + random.randint(0, 10),
            "total_tokens": 7560 + random.randint(0, 100),
            "savings_percent": 22.8,
        },
    ]

    total_turns = sum(i['turn_count'] for i in instances)
    total_tokens = sum(i['total_tokens'] for i in instances)
    avg_savings = sum(i['savings_percent'] for i in instances) / len(instances)

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "cluster": {
            "instance_count": len(instances),
            "total_turns": total_turns,
            "total_tokens": total_tokens,
            "avg_tokens_per_turn": total_tokens // total_turns if total_turns > 0 else 0,
            "avg_savings_percent": round(avg_savings, 1),
            "instances": instances,
        },
        "summary": {
            "instance_count": len(instances),
            "total_turns": total_turns,
            "total_tokens": total_tokens,
            "avg_tokens_per_turn": total_tokens // total_turns if total_turns > 0 else 0,
            "avg_savings_percent": round(avg_savings, 1),
        },
    }


if HAS_FLASK:
    """Flask implementation (if available)"""
    app = Flask(__name__)

    @app.route('/', methods=['GET'])
    def index():
        """Redirect to stats."""
        return '<meta http-equiv="refresh" content="0;url=/stats" />'

    @app.route('/stats', methods=['GET'])
    def stats_dashboard():
        """Serve stats dashboard HTML."""
        html_path = Path(__file__).parent.parent / 'docs' / 'stats.html'
        if html_path.exists():
            with open(html_path) as f:
                html = f.read()
            # Update API endpoint in HTML to point to localhost
            html = html.replace(
                "'https://api.corvin-labs.com/api/metrics/stats'",
                "'http://localhost:8080/api/metrics/stats'"
            )
            return html
        return '<h1>Stats Dashboard HTML not found</h1>', 404

    @app.route('/api/metrics/stats', methods=['GET'])
    def api_stats():
        """Get cluster stats."""
        return jsonify(get_mock_stats())

    @app.route('/health', methods=['GET'])
    def health():
        """Health check."""
        return 'OK\n', 200

    def run_flask():
        """Run Flask server."""
        print("\n" + "="*60)
        print("⚡ CorvinOS Stats Dashboard (Flask Server)")
        print("="*60)
        print("\n📊 Dashboard:  http://localhost:8080/stats")
        print("🔌 API:        http://localhost:8080/api/metrics/stats")
        print("🩺 Health:     http://localhost:8080/health")
        print("\n💡 Press Ctrl+C to stop the server")
        print("="*60 + "\n")

        app.run(host='0.0.0.0', port=8080, debug=True, use_reloader=False)

else:
    """Fallback: Pure http.server implementation (Python stdlib only)"""

    class StatsHandler(BaseHTTPRequestHandler):
        """HTTP request handler for stats dashboard."""

        def do_GET(self):
            """Handle GET requests."""
            parsed_path = urlparse(self.path)
            path = parsed_path.path

            if path == '/' or path == '/stats':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()

                html_path = Path(__file__).parent.parent / 'docs' / 'stats.html'
                if html_path.exists():
                    with open(html_path, 'rb') as f:
                        html_bytes = f.read()
                    html_str = html_bytes.decode('utf-8')
                    html_str = html_str.replace(
                        "'https://api.corvin-labs.com/api/metrics/stats'",
                        "'http://localhost:8080/api/metrics/stats'"
                    )
                    self.wfile.write(html_str.encode('utf-8'))
                else:
                    self.wfile.write(b'<h1>Stats Dashboard HTML not found</h1>')

            elif path == '/api/metrics/stats':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'max-age=5')
                self.end_headers()
                stats = get_mock_stats()
                self.wfile.write(json.dumps(stats, indent=2).encode('utf-8'))

            elif path == '/health':
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'OK\n')

            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'Not found\n')

        def log_message(self, format, *args):
            """Suppress logging."""
            pass

    def run_stdlib():
        """Run stdlib http.server."""
        server = HTTPServer(('0.0.0.0', 8080), StatsHandler)
        print("\n" + "="*60)
        print("⚡ CorvinOS Stats Dashboard (Pure Python Server)")
        print("="*60)
        print("\n📊 Dashboard:  http://localhost:8080/stats")
        print("🔌 API:        http://localhost:8080/api/metrics/stats")
        print("🩺 Health:     http://localhost:8080/health")
        print("\n💡 Press Ctrl+C to stop the server")
        print("="*60 + "\n")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n\n👋 Server stopped")
            sys.exit(0)


def main():
    """Main entry point."""
    if HAS_FLASK:
        run_flask()
    else:
        run_stdlib()


if __name__ == '__main__':
    main()
