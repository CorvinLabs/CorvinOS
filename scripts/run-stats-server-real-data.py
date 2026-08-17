#!/usr/bin/env python3
"""
CorvinOS Stats Dashboard Server — Real Data Edition
Loads actual instance metrics from ~/.corvin/
"""

import os
import sys
import json
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    from flask import Flask, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse


def load_real_metrics():
    """Load real instance metrics from CorvinOS data stores."""
    instances = {}
    home = Path.home()

    # Try 1: Load from instance registry JSON
    registry_file = home / '.corvin' / 'instances.json'
    if registry_file.exists():
        try:
            with open(registry_file) as f:
                data = json.load(f)
            if 'instances' in data:
                for inst in data['instances']:
                    instances[inst['instance_id']] = inst
        except Exception as e:
            print(f"Warning: Could not load instances.json: {e}", file=sys.stderr)

    # Try 2: Parse audit.jsonl for turn/token counts
    audit_file = home / '.corvin' / 'audit.jsonl'
    if audit_file.exists():
        try:
            with open(audit_file) as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        iid = event.get('instance_id', 'unknown')

                        if iid not in instances:
                            instances[iid] = {
                                'instance_id': iid,
                                'hostname': event.get('hostname', f'host-{iid}'),
                                'location': event.get('location', '0,0'),
                                'turn_count': 0,
                                'total_tokens': 0,
                                'savings_percent': 25.0,
                            }

                        # Aggregate metrics
                        instances[iid]['turn_count'] = instances[iid].get('turn_count', 0) + 1
                        tokens = event.get('tokens_used', event.get('output_tokens', 0))
                        instances[iid]['total_tokens'] = instances[iid].get('total_tokens', 0) + tokens
                    except:
                        pass
        except Exception as e:
            print(f"Warning: Could not parse audit.jsonl: {e}", file=sys.stderr)

    # Try 3: Query SQLite token_metrics if it exists
    metrics_db = home / '.corvin' / 'token_metrics.db'
    if metrics_db.exists():
        try:
            conn = sqlite3.connect(metrics_db)
            cursor = conn.cursor()

            # Get aggregated metrics per instance
            cursor.execute("""
                SELECT instance_id, COUNT(*) as turn_count, SUM(total_tokens) as total_tokens
                FROM token_metrics
                GROUP BY instance_id
            """)

            for instance_id, turn_count, total_tokens in cursor.fetchall():
                if instance_id not in instances:
                    instances[instance_id] = {
                        'instance_id': instance_id,
                        'hostname': f'instance-{instance_id}',
                        'location': '0,0',
                        'turn_count': 0,
                        'total_tokens': 0,
                        'savings_percent': 25.0,
                    }
                instances[instance_id]['turn_count'] = turn_count or 0
                instances[instance_id]['total_tokens'] = total_tokens or 0

            conn.close()
        except Exception as e:
            print(f"Warning: Could not query token_metrics.db: {e}", file=sys.stderr)

    # Fallback: If no real data, show what we have
    if not instances:
        print("ℹ️  No instance data found. Showing demo data.", file=sys.stderr)
        instances = {
            'demo-1': {
                'instance_id': 'demo-1',
                'hostname': 'demo-instance-1',
                'location': '40.7128,-74.0060',
                'turn_count': 100,
                'total_tokens': 10000,
                'savings_percent': 25.0,
            }
        }

    return list(instances.values())


def get_stats():
    """Generate cluster statistics from real data."""
    instances = load_real_metrics()

    total_turns = sum(i.get('turn_count', 0) for i in instances)
    total_tokens = sum(i.get('total_tokens', 0) for i in instances)
    avg_tokens = total_tokens // total_turns if total_turns > 0 else 0

    savings = [i.get('savings_percent', 0) for i in instances]
    avg_savings = sum(savings) / len(savings) if savings else 0

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "cluster": {
            "instance_count": len(instances),
            "total_turns": total_turns,
            "total_tokens": total_tokens,
            "avg_tokens_per_turn": avg_tokens,
            "avg_savings_percent": round(avg_savings, 1),
            "instances": instances,
        },
        "summary": {
            "instance_count": len(instances),
            "total_turns": total_turns,
            "total_tokens": total_tokens,
            "avg_tokens_per_turn": avg_tokens,
            "avg_savings_percent": round(avg_savings, 1),
        },
    }


if HAS_FLASK:
    app = Flask(__name__)

    @app.route('/', methods=['GET'])
    def index():
        return '<meta http-equiv="refresh" content="0;url=/stats" />'

    @app.route('/stats', methods=['GET'])
    def stats_dashboard():
        html_path = Path(__file__).parent.parent / 'docs' / 'stats.html'
        if html_path.exists():
            with open(html_path) as f:
                html = f.read()
            html = html.replace(
                "'https://api.corvin-labs.com/api/metrics/stats'",
                "'http://localhost:8080/api/metrics/stats'"
            )
            return html
        return '<h1>Dashboard not found</h1>', 404

    @app.route('/api/metrics/stats', methods=['GET'])
    def api_stats():
        return jsonify(get_stats())

    @app.route('/health', methods=['GET'])
    def health():
        return 'OK\n', 200

    if __name__ == '__main__':
        print("\n" + "="*70)
        print("⚡ CorvinOS Stats Dashboard — Real Data Edition")
        print("="*70)
        print("\n📊 Dashboard:  http://localhost:8080/stats")
        print("🔌 API:        http://localhost:8080/api/metrics/stats")
        print("🩺 Health:     http://localhost:8080/health")
        print("\n📂 Loading real data from:")
        print("   ~/.corvin/instances.json")
        print("   ~/.corvin/audit.jsonl")
        print("   ~/.corvin/token_metrics.db")
        print("\n💡 Press Ctrl+C to stop")
        print("="*70 + "\n")
        app.run(host='0.0.0.0', port=8080, debug=False)

else:
    class StatsHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path

            if path in ('/', '/stats'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

                html_path = Path(__file__).parent.parent / 'docs' / 'stats.html'
                if html_path.exists():
                    with open(html_path, 'rb') as f:
                        html = f.read().decode('utf-8')
                    html = html.replace(
                        "'https://api.corvin-labs.com/api/metrics/stats'",
                        "'http://localhost:8080/api/metrics/stats'"
                    )
                    self.wfile.write(html.encode('utf-8'))
                else:
                    self.wfile.write(b'<h1>Dashboard not found</h1>')

            elif path == '/api/metrics/stats':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                stats = get_stats()
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
                self.end_headers()
                self.wfile.write(b'Not found\n')

        def log_message(self, format, *args):
            pass

    server = HTTPServer(('0.0.0.0', 8080), StatsHandler)
    print("\n" + "="*70)
    print("⚡ CorvinOS Stats Dashboard — Real Data Edition (Pure Python)")
    print("="*70)
    print("\n📊 Dashboard:  http://localhost:8080/stats")
    print("🔌 API:        http://localhost:8080/api/metrics/stats")
    print("🩺 Health:     http://localhost:8080/health")
    print("\n📂 Loading real data from:")
    print("   ~/.corvin/instances.json")
    print("   ~/.corvin/audit.jsonl")
    print("   ~/.corvin/token_metrics.db")
    print("\n💡 Press Ctrl+C to stop")
    print("="*70 + "\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server stopped")
        sys.exit(0)
