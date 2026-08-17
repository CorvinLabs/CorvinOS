#!/usr/bin/env python3
"""Mock API for CorvinOS Stats Dashboard testing."""

from flask import Flask, jsonify, request
from datetime import datetime
import random

app = Flask(__name__)

# Mock instance data
INSTANCES = [
    {
        "instance_id": "prod-us-east-1",
        "hostname": "prod.us-east-1.corvin.local",
        "location": "40.7128,-74.0060",  # New York
        "turn_count": 1250,
        "total_tokens": 12500,
        "savings_percent": 28.5,
    },
    {
        "instance_id": "prod-eu-west-1",
        "hostname": "prod.eu-west-1.corvin.local",
        "location": "51.5074,-0.1278",  # London
        "turn_count": 980,
        "total_tokens": 9800,
        "savings_percent": 25.2,
    },
    {
        "instance_id": "prod-ap-southeast-1",
        "hostname": "prod.ap-southeast-1.corvin.local",
        "location": "-33.8688,151.2093",  # Sydney
        "turn_count": 756,
        "total_tokens": 7560,
        "savings_percent": 22.8,
    },
]

@app.route('/api/metrics/stats', methods=['GET'])
def get_stats():
    """Get cluster-wide stats."""
    # Add some randomness to simulate live updates
    instances = []
    for inst in INSTANCES:
        inst_copy = inst.copy()
        # Simulate turn count increasing
        inst_copy['turn_count'] += random.randint(0, 5)
        inst_copy['total_tokens'] += random.randint(0, 50)
        instances.append(inst_copy)

    total_turns = sum(i['turn_count'] for i in instances)
    total_tokens = sum(i['total_tokens'] for i in instances)
    avg_savings = sum(i['savings_percent'] for i in instances) / len(instances)

    return jsonify({
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
    }), 200, {'Access-Control-Allow-Origin': '*'}

@app.route('/api/metrics/session/<session_id>', methods=['GET'])
def get_session_metrics(session_id):
    """Get metrics for a specific session."""
    return jsonify({
        "session_id": session_id,
        "timestamp": datetime.utcnow().isoformat(),
        "summary": {
            "session_id": session_id,
            "timestamp": datetime.utcnow().isoformat(),
            "turn_count": random.randint(10, 100),
            "total_tokens": random.randint(1000, 10000),
            "baseline_tokens": random.randint(1500, 15000),
            "savings_tokens": random.randint(200, 5000),
            "savings_percent": round(random.uniform(15, 40), 1),
            "avg_tokens_per_turn": random.randint(8, 20),
            "is_significant": True,
            "confidence": round(random.uniform(0.7, 0.99), 2),
        },
        "turns": [
            {
                "turn_id": f"turn_{i:04d}",
                "input_tokens": random.randint(100, 500),
                "output_tokens": random.randint(200, 800),
                "total_tokens": random.randint(300, 1000),
                "savings_percent": round(random.uniform(15, 40), 1),
                "task_type": random.choice(["code", "research", "analysis", "writing"]),
                "outcome_quality": random.choice(["excellent", "good", "fair"]),
                "latency_ms": random.uniform(500, 2000),
            }
            for i in range(10)
        ],
        "by_task_type": {
            "code": {"turns": 5, "total_tokens": 2500, "savings_percent": 30.2},
            "research": {"turns": 3, "total_tokens": 1800, "savings_percent": 25.1},
            "analysis": {"turns": 2, "total_tokens": 800, "savings_percent": 18.5},
        },
        "subsystems": {
            "confidence": {"count": 8, "total_tokens": 1600},
            "cache": {"count": 7, "total_tokens": 1100},
            "skills": {"count": 6, "total_tokens": 900},
            "learning": {"count": 4, "total_tokens": 400},
        },
    }), 200, {'Access-Control-Allow-Origin': '*'}

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return 'OK\n', 200

@app.route('/metrics', methods=['GET'])
def metrics():
    """Prometheus metrics endpoint."""
    return """# HELP corvinos_stats_requests Total requests
# TYPE corvinos_stats_requests counter
corvinos_stats_requests{endpoint="/api/metrics/stats"} 1234
corvinos_stats_requests{endpoint="/api/metrics/session"} 5678

# HELP corvinos_instance_count Number of active instances
# TYPE corvinos_instance_count gauge
corvinos_instance_count 3

# HELP corvinos_total_tokens Total tokens consumed
# TYPE corvinos_total_tokens gauge
corvinos_total_tokens 29860
""", 200, {'Content-Type': 'text/plain'}

@app.route('/', methods=['GET'])
def root():
    """Root redirect."""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>CorvinOS Stats API</title>
        <style>
            body { font-family: monospace; margin: 2rem; background: #0d1117; color: #e6edf3; }
            h1 { color: #79c0ff; }
            code { background: #161b22; padding: 0.5rem; border-radius: 4px; }
            a { color: #79c0ff; }
        </style>
    </head>
    <body>
        <h1>⚡ CorvinOS Stats API</h1>
        <p>Mock API for testing stats dashboard</p>
        <h2>Endpoints</h2>
        <ul>
            <li><code><a href="/api/metrics/stats">GET /api/metrics/stats</a></code> — Cluster stats</li>
            <li><code>GET /api/metrics/session/{id}</code> — Session metrics</li>
            <li><code><a href="/health">GET /health</a></code> — Health check</li>
            <li><code><a href="/metrics">GET /metrics</a></code> — Prometheus metrics</li>
        </ul>
        <p>Dashboard: <code><a href="http://localhost/stats">http://localhost/stats</a></code></p>
    </body>
    </html>
    ''', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
