"""Audit Trail API Routes.

Provides access to audit logs and verification.
"""

from flask import Blueprint, jsonify
from pathlib import Path
import json
from datetime import datetime

audit_bp = Blueprint('audit', __name__, url_prefix='/api/console/audit')

TENANT_PATH = Path.home() / '.corvin' / 'tenants' / '_default'


@audit_bp.route('/summary', methods=['GET'])
def audit_summary():
    """
    Get audit summary for recent period.

    GET /api/console/audit/summary?days=7

    Returns: {
        "total_events": int,
        "events_by_type": { "event_type": count, ... },
        "period_days": int,
        "generated_at": ISO timestamp,
    }
    """
    from ..audit_integration import get_audit_summary

    days = int(__import__('flask').request.args.get('days', 7))
    summary = get_audit_summary(days=days)

    return jsonify({
        **summary,
        'generated_at': datetime.utcnow().isoformat(),
    }), 200


@audit_bp.route('/verify', methods=['POST'])
def verify_chain():
    """
    Verify audit chain integrity.

    POST /api/console/audit/verify

    Returns: {
        "valid": bool,
        "errors": [str],
        "verified_at": ISO timestamp,
    }
    """
    from ..audit_integration import AuditLogger

    logger = AuditLogger()
    valid, errors = logger.verify_chain()

    return jsonify({
        'valid': valid,
        'errors': errors,
        'error_count': len(errors),
        'verified_at': datetime.utcnow().isoformat(),
    }), 200 if valid else 400


@audit_bp.route('/events', methods=['GET'])
def get_events():
    """
    Get recent audit events with pagination.

    GET /api/console/audit/events?limit=50&offset=0

    Returns: {
        "events": [ ... ],
        "total": int,
        "limit": int,
        "offset": int,
    }
    """
    audit_file = TENANT_PATH / 'audit.jsonl'

    if not audit_file.exists():
        return jsonify({
            'events': [],
            'total': 0,
            'limit': 50,
            'offset': 0,
        }), 200

    limit = int(__import__('flask').request.args.get('limit', 50))
    offset = int(__import__('flask').request.args.get('offset', 0))

    events = []
    total = 0

    try:
        with open(audit_file, 'r') as f:
            for i, line in enumerate(f):
                if line.strip():
                    total += 1

        # Read with offset
        with open(audit_file, 'r') as f:
            for i, line in enumerate(f):
                if line.strip():
                    if i >= offset and len(events) < limit:
                        events.append(json.loads(line))

    except Exception as e:
        return jsonify({
            'error': str(e),
        }), 500

    return jsonify({
        'events': events,
        'total': total,
        'limit': limit,
        'offset': offset,
    }), 200


@audit_bp.route('/events/search', methods=['POST'])
def search_events():
    """
    Search audit events by type/action/subject.

    POST /api/console/audit/events/search
    Body: {
        "event_type": "github_integration" | null,
        "action": "connected" | null,
        "subject_contains": "github/owner/repo" | null,
        "limit": 50,
    }

    Returns: {
        "results": [ ... ],
        "count": int,
    }
    """
    data = __import__('flask').request.get_json() or {}
    event_type = data.get('event_type')
    action = data.get('action')
    subject_contains = data.get('subject_contains')
    limit = data.get('limit', 50)

    audit_file = TENANT_PATH / 'audit.jsonl'

    if not audit_file.exists():
        return jsonify({'results': [], 'count': 0}), 200

    results = []

    try:
        with open(audit_file, 'r') as f:
            for line in f:
                if not line.strip():
                    continue

                event = json.loads(line)

                # Apply filters
                if event_type and event.get('event_type') != event_type:
                    continue
                if action and event.get('action') != action:
                    continue
                if subject_contains and subject_contains not in event.get('subject', ''):
                    continue

                results.append(event)

                if len(results) >= limit:
                    break

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({
        'results': results,
        'count': len(results),
    }), 200


@audit_bp.route('/stats', methods=['GET'])
def audit_stats():
    """
    Get audit statistics.

    GET /api/console/audit/stats

    Returns: {
        "total_events": int,
        "chain_valid": bool,
        "events_by_type": { ... },
        "time_range": { "first": ISO, "last": ISO },
    }
    """
    from ..audit_integration import AuditLogger

    audit_file = TENANT_PATH / 'audit.jsonl'

    if not audit_file.exists():
        return jsonify({
            'total_events': 0,
            'chain_valid': True,
            'events_by_type': {},
            'time_range': None,
        }), 200

    logger = AuditLogger()
    valid, _ = logger.verify_chain()

    events_by_type = {}
    time_range = {'first': None, 'last': None}
    total_events = 0

    try:
        with open(audit_file, 'r') as f:
            for line in f:
                if line.strip():
                    event = json.loads(line)
                    total_events += 1

                    event_type = event.get('event_type', 'unknown')
                    events_by_type[event_type] = events_by_type.get(event_type, 0) + 1

                    timestamp = event.get('timestamp')
                    if timestamp:
                        if time_range['first'] is None:
                            time_range['first'] = timestamp
                        time_range['last'] = timestamp

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({
        'total_events': total_events,
        'chain_valid': valid,
        'events_by_type': events_by_type,
        'time_range': time_range if time_range['first'] else None,
    }), 200
