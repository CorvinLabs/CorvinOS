"""GitHub Webhook Handler for Event-Driven Sync.

Receives push/pull_request events from GitHub and triggers immediate sync.

Webhook Setup:
1. GitHub Repo Settings → Webhooks
2. Payload URL: https://your-domain.com/api/console/github/webhook
3. Events: push, pull_request
4. Secret: Store in tenant config as webhook_secret
"""

from flask import Blueprint, request, jsonify
import hashlib
import hmac
import json
import logging
from datetime import datetime
from pathlib import Path
from core.endpoints.k1_decorators import k1_flask

logger = logging.getLogger(__name__)

webhook_bp = Blueprint('github_webhooks', __name__, url_prefix='/api/console/github')

TENANT_PATH = Path.home() / '.corvin' / 'tenants' / '_default'


def verify_webhook_signature(payload_body: bytes, signature: str, secret: str) -> bool:
    """
    Verify GitHub webhook signature.

    GitHub sends: X-Hub-Signature-256: sha256=<hash>
    """
    if not secret:
        logger.warning('No webhook secret configured')
        return False

    expected_signature = (
        'sha256=' + hmac.new(
            secret.encode(),
            payload_body,
            hashlib.sha256
        ).hexdigest()
    )

    return hmac.compare_digest(signature, expected_signature)


def get_webhook_secret() -> str:
    """Load webhook secret from config."""
    config_file = TENANT_PATH / 'config' / 'github-config.json'

    if not config_file.exists():
        return ''

    try:
        with open(config_file) as f:
            config = json.load(f)
        return config.get('github', {}).get('webhook_secret', '')
    except:
        return ''


@webhook_bp.route('/webhook', methods=['POST'])
@k1_flask()
def handle_github_webhook():
    """
    Handle GitHub webhook events.

    POST /api/console/github/webhook

    Triggered by:
    - push events (code pushed)
    - pull_request events (PR opened/synchronized)
    - release events (new version)

    Returns: {
        "success": bool,
        "event_type": str,
        "action": str,
        "message": str,
        "sync_triggered": bool,
    }
    """
    payload_body = request.get_data()

    # Verify webhook signature (MUST verify if secret is configured)
    signature = request.headers.get('X-Hub-Signature-256', '')
    secret = get_webhook_secret()

    if secret:
        # Secret is configured - signature verification is MANDATORY
        if not verify_webhook_signature(payload_body, signature, secret):
            logger.warning('Invalid webhook signature')
            return jsonify({
                'success': False,
                'error': 'Invalid signature',
            }), 401
    else:
        # No secret configured - warn and reject for security
        logger.warning('Webhook received without configured secret - rejecting')
        return jsonify({
            'success': False,
            'error': 'Webhook secret not configured. Configure via /api/console/github/webhook/register',
        }), 401

    try:
        payload = json.loads(payload_body)
    except json.JSONDecodeError:
        return jsonify({
            'success': False,
            'error': 'Invalid JSON',
        }), 400

    # Parse event
    event_type = request.headers.get('X-GitHub-Event', 'unknown')
    action = payload.get('action', 'unknown')

    logger.info(f'GitHub Webhook: {event_type} / {action}')

    # Emit event to sync worker
    from .github_integration import get_sync_worker
    worker = get_sync_worker()

    sync_triggered = False
    result_message = None

    if event_type == 'push':
        # Code pushed - sync immediately
        sync_triggered = True
        result_message = f"Push detected on {payload.get('ref', 'unknown')}"

        worker.emit('webhook_triggered', {
            'event': 'push',
            'branch': payload.get('ref'),
            'commits': len(payload.get('commits', [])),
            'pusher': payload.get('pusher', {}).get('name', 'unknown'),
        })

    elif event_type == 'pull_request':
        # PR opened/synchronized - sync to check conflicts
        if action in ('opened', 'synchronize', 'closed'):
            sync_triggered = True
            result_message = f"Pull request {action}"

            worker.emit('webhook_triggered', {
                'event': 'pull_request',
                'action': action,
                'pr_number': payload.get('pull_request', {}).get('number'),
                'title': payload.get('pull_request', {}).get('title'),
            })

    elif event_type == 'release':
        # Release created - update local version
        sync_triggered = True
        result_message = f"Release {action}: {payload.get('release', {}).get('tag_name')}"

        worker.emit('webhook_triggered', {
            'event': 'release',
            'action': action,
            'tag': payload.get('release', {}).get('tag_name'),
        })

    elif event_type == 'ping':
        # Webhook test event
        return jsonify({
            'success': True,
            'event_type': 'ping',
            'message': 'Webhook connected successfully',
            'timestamp': datetime.utcnow().isoformat(),
        }), 200

    else:
        # Unknown event
        logger.debug(f'Ignoring event type: {event_type}')
        result_message = f'Event type not handled: {event_type}'

    # Trigger sync cycle if needed
    if sync_triggered and worker.running:
        # Queue immediate sync (don't block webhook response)
        import threading
        threading.Thread(target=worker._sync_cycle, daemon=True).start()

    return jsonify({
        'success': True,
        'event_type': event_type,
        'action': action,
        'message': result_message,
        'sync_triggered': sync_triggered,
        'timestamp': datetime.utcnow().isoformat(),
    }), 200


@webhook_bp.route('/webhook/register', methods=['POST'])
@k1_flask()
def register_webhook():
    """
    Register webhook with GitHub via API.

    POST /api/console/github/webhook/register
    Body: {
        "token": "github-api-token",
        "webhook_secret": "optional-secret-for-verification"
    }

    Returns: {
        "success": bool,
        "webhook_id": str,
        "url": str,
        "events": [str],
        "active": bool,
    }
    """
    data = request.get_json() or {}
    token = data.get('token', '')
    webhook_secret = data.get('webhook_secret', '')

    config_file = TENANT_PATH / 'config' / 'github-config.json'
    if not config_file.exists():
        return jsonify({
            'success': False,
            'error': 'GitHub not configured. Connect first.',
        }), 400

    with open(config_file) as f:
        config = json.load(f)

    github_cfg = config.get('github', {})
    owner = github_cfg.get('owner')
    repo = github_cfg.get('repo')

    if not token or not owner or not repo:
        return jsonify({
            'success': False,
            'error': 'Missing required parameters',
        }), 400

    # Create webhook via GitHub API
    import requests

    webhook_url = 'https://api.corvin-labs.com/api/console/github/webhook'
    api_url = f'https://api.github.com/repos/{owner}/{repo}/hooks'

    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
    }

    webhook_data = {
        'name': 'web',
        'active': True,
        'events': ['push', 'pull_request', 'release'],
        'config': {
            'url': webhook_url,
            'content_type': 'json',
            'insecure_ssl': '0',  # Require HTTPS
        }
    }

    if webhook_secret:
        webhook_data['config']['secret'] = webhook_secret

    try:
        response = requests.post(api_url, headers=headers, json=webhook_data, timeout=10)

        if response.status_code in (201, 200):
            webhook_info = response.json()

            # Save webhook secret to config
            if webhook_secret:
                github_cfg['webhook_secret'] = webhook_secret
                github_cfg['webhook_id'] = webhook_info.get('id')
                github_cfg['webhook_registered'] = True

                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=2)

            return jsonify({
                'success': True,
                'webhook_id': webhook_info.get('id'),
                'url': webhook_data['config']['url'],
                'events': webhook_data['events'],
                'active': webhook_info.get('active'),
                'timestamp': datetime.utcnow().isoformat(),
            }), 200

        elif response.status_code == 422:
            # Webhook might already exist
            return jsonify({
                'success': False,
                'error': 'Webhook already exists or validation failed',
                'details': response.json(),
            }), 422

        else:
            return jsonify({
                'success': False,
                'error': f'GitHub API error {response.status_code}',
            }), response.status_code

    except requests.RequestException as e:
        return jsonify({
            'success': False,
            'error': f'Failed to register webhook: {str(e)}',
        }), 500


@webhook_bp.route('/webhook/status', methods=['GET'])
@k1_flask()
def webhook_status():
    """Get webhook registration status."""
    config_file = TENANT_PATH / 'config' / 'github-config.json'

    if not config_file.exists():
        return jsonify({
            'registered': False,
            'webhook_id': None,
        }), 404

    try:
        with open(config_file) as f:
            config = json.load(f)

        github_cfg = config.get('github', {})

        return jsonify({
            'registered': github_cfg.get('webhook_registered', False),
            'webhook_id': github_cfg.get('webhook_id'),
            'has_secret': bool(github_cfg.get('webhook_secret')),
            'events': ['push', 'pull_request', 'release'],
            'url': 'https://api.corvin-labs.com/api/console/github/webhook',
        }), 200

    except:
        return jsonify({
            'registered': False,
            'error': 'Failed to read config',
        }), 500


@webhook_bp.route('/webhook/test', methods=['POST'])
@k1_flask()
def test_webhook():
    """
    Send test webhook event (for debugging).

    POST /api/console/github/webhook/test
    Body: {
        "event_type": "push" | "pull_request" | "release",
        "secret": "webhook_secret (if needed)"
    }
    """
    data = request.get_json() or {}
    event_type = data.get('event_type', 'ping')
    secret = data.get('secret', '')

    # Build test payload
    test_payloads = {
        'ping': {'zen': 'Design for failure.', 'hook_id': 12345},
        'push': {
            'ref': 'refs/heads/main',
            'commits': [{'id': 'abc123', 'message': 'Test commit'}],
            'pusher': {'name': 'test-user'},
        },
        'pull_request': {
            'action': 'opened',
            'pull_request': {
                'number': 1,
                'title': 'Test PR',
                'base': {'ref': 'main'},
            },
        },
        'release': {
            'action': 'published',
            'release': {'tag_name': 'v1.0.0'},
        },
    }

    payload = test_payloads.get(event_type, test_payloads['ping'])
    payload_json = json.dumps(payload)

    # Sign if secret provided
    signature = ''
    if secret:
        signature = 'sha256=' + hmac.new(
            secret.encode(),
            payload_json.encode(),
            hashlib.sha256
        ).hexdigest()

    # Make request to webhook
    import requests

    headers = {
        'X-GitHub-Event': event_type,
        'X-Hub-Signature-256': signature,
        'Content-Type': 'application/json',
    }

    webhook_url = request.base_url.replace('/test', '')

    try:
        response = requests.post(
            webhook_url,
            data=payload_json,
            headers=headers,
            timeout=5
        )

        return jsonify({
            'success': response.status_code == 200,
            'status_code': response.status_code,
            'response': response.json() if response.status_code == 200 else response.text,
        }), response.status_code

    except requests.RequestException as e:
        return jsonify({
            'success': False,
            'error': str(e),
        }), 500
