#!/usr/bin/env python3
"""Live test of Token Metrics API with simple HTTP server."""

import json
import sqlite3
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler for token metrics API."""

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            # Get data from database
            db_path = Path.home() / ".corvin" / "token_metrics.db"
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as turn_count,
                    SUM(total_tokens) as total_tokens,
                    SUM(baseline_tokens) as baseline_tokens,
                    SUM(baseline_tokens - total_tokens) as savings_tokens,
                    AVG(savings_percent) as avg_savings_percent
                FROM token_metrics
                WHERE session_id = 'current'
            """)
            row = cursor.fetchone()
            conn.close()

            if row:
                turn_count, total_tokens, baseline_tokens, savings_tokens, avg_savings = row

                # Calculate cost (assuming $0.0084 per 1k tokens - Claude Opus pricing)
                cost_per_1k = 0.0084
                baseline_cost = (baseline_tokens / 1000 * cost_per_1k) if baseline_tokens else 0
                actual_cost = (total_tokens / 1000 * cost_per_1k) if total_tokens else 0
                savings_cost = baseline_cost - actual_cost

                response = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "session_id": "current",
                    "summary": {
                        "turn_count": turn_count,
                        "total_tokens": total_tokens,
                        "baseline_tokens": baseline_tokens,
                        "savings_tokens": savings_tokens,
                        "savings_percent": round(avg_savings_percent, 1),
                        "estimated_baseline_cost": round(baseline_cost, 2),
                        "estimated_actual_cost": round(actual_cost, 2),
                        "estimated_savings": round(savings_cost, 2),
                    }
                }
                self.wfile.write(json.dumps(response, indent=2).encode())
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()

            html = """
<!DOCTYPE html>
<html>
<head>
    <title>Token Metrics Dashboard</title>
    <style>
        body { font-family: monospace; margin: 20px; background: #0a0e27; color: #e0e0e0; }
        .container { max-width: 800px; margin: 0 auto; }
        .metric { background: #1a1f3a; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #0ea5e9; }
        .value { font-size: 24px; font-weight: bold; color: #0ea5e9; }
        .label { color: #888; font-size: 12px; margin-top: 5px; }
        .success { color: #10b981; font-weight: bold; }
        h1 { color: #0ea5e9; }
    </style>
</head>
<body>
    <div class="container">
        <h1>✅ Token Metrics Live Dashboard</h1>
        <div id="metrics">Loading...</div>
    </div>
    <script>
        async function loadMetrics() {
            const resp = await fetch('/metrics');
            const data = await resp.json();
            const m = data.summary;

            document.getElementById('metrics').innerHTML = `
                <div class="metric">
                    <div class="label">Session</div>
                    <div class="value">${data.session_id}</div>
                </div>

                <div class="metric">
                    <div class="label">Turns Processed</div>
                    <div class="value">${m.turn_count}</div>
                </div>

                <div class="metric">
                    <div class="label">Total Tokens Used</div>
                    <div class="value">${m.total_tokens.toLocaleString()}</div>
                    <div class="label">vs Baseline: ${m.baseline_tokens.toLocaleString()}</div>
                </div>

                <div class="metric">
                    <div class="label">Tokens Saved</div>
                    <div class="value">${m.savings_tokens.toLocaleString()}</div>
                    <div class="label">That's <span class="success">${m.savings_percent}%</span> fewer tokens!</div>
                </div>

                <div class="metric">
                    <div class="label">Estimated Cost Savings</div>
                    <div class="value success">$${m.estimated_savings.toFixed(2)}</div>
                    <div class="label">Actual: $${m.estimated_actual_cost.toFixed(2)} vs Baseline: $${m.estimated_baseline_cost.toFixed(2)}</div>
                </div>

                <div class="metric" style="border-left-color: #10b981;">
                    <div class="label">Last Updated</div>
                    <div class="value" style="color: #10b981;">${new Date(data.timestamp).toLocaleString()}</div>
                </div>
            `;
        }

        loadMetrics();
        setInterval(loadMetrics, 5000);
    </script>
</body>
</html>
            """
            self.wfile.write(html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def main():
    """Start the metrics server."""
    print("🚀 Starting Token Metrics Live Test Server\n")
    print("📊 Open http://localhost:9090 in your browser")
    print("📊 API endpoint: http://localhost:9090/metrics\n")

    server = HTTPServer(("localhost", 9090), MetricsHandler)
    print("✓ Server running on http://localhost:9090")
    print("✓ Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✓ Server stopped")


if __name__ == "__main__":
    main()
