"""Vibe Security Dashboard routes (Finding #9)."""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('security_decisions', __name__, url_prefix='/api/security')


@bp.route('/decisions', methods=['GET'])
def list_decisions():
    """Query security decisions with filtering."""
    actor = request.args.get('actor')
    action = request.args.get('action')
    outcome = request.args.get('outcome')
    pii_detected = request.args.get('pii_detected') == 'true'
    hours = int(request.args.get('hours', 24))
    limit = int(request.args.get('limit', 100))

    # Simplified: return mock data (Phase 1)
    decisions = [
        {
            'request_id': f'req_{i:05d}',
            'actor': actor or 'user_123',
            'action': action or 'list_sessions',
            'resource': 'chat_session',
            'outcome': outcome or 'granted',
            'deny_reason': None,
            'context_sources': 3,
            'pii_findings': 0,
            'timestamp': 1693478400.0 + i * 60,
            'decision_hash': f'hash_{i:05d}',
        }
        for i in range(min(limit, 10))
    ]

    return jsonify(decisions)


@bp.route('/summary', methods=['GET'])
def security_summary():
    """Return high-level security posture."""
    hours = int(request.args.get('hours', 24))

    # Simplified: return mock summary
    return jsonify({
        'total_turns_24h': 1234,
        'capability_granted': 1210,
        'capability_denied': 24,
        'pii_detected': 3,
        'validation_failed': 2,
        'deny_rate_pct': 1.95,
    })


@bp.route('/decisions/<decision_hash>', methods=['GET'])
def get_decision(decision_hash):
    """Get full decision details by hash."""
    # Simplified: return mock decision
    return jsonify({
        'request_id': 'req_00001',
        'actor': 'user_123',
        'action': 'list_sessions',
        'resource': 'chat_session',
        'timestamp': 1693478400.0,
        'gates': [
            {'gate': 'capability', 'passed': True, 'reason': 'granted'},
            {'gate': 'validation', 'passed': True, 'reason': 'valid'},
            {'gate': 'pii_detection', 'passed': True, 'reason': 'no_pii'},
            {'gate': 'context_engineering', 'passed': True, 'reason': 'ok'},
            {'gate': 'audit_recording', 'passed': True, 'reason': 'recorded'},
        ],
        'context_sources': 3,
        'pii_findings': 0,
        'decision_hash': decision_hash,
    })
