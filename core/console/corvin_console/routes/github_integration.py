"""GitHub Integration for Cross-Device-Learning Sync.

Provides:
- URL validation (format check)
- GitHub API connectivity check (repo exists, accessible)
- Sync status monitoring (last sync, status, errors)
- Persistent config storage (tenant.corvin.yaml)
- Background sync worker management
"""

from flask import Blueprint, request, jsonify, g
from pathlib import Path
import json
import re
from datetime import datetime
from typing import Optional, Tuple
import requests
import logging

logger = logging.getLogger(__name__)

github_bp = Blueprint('github_integration', __name__, url_prefix='/api/console/github')

# Import sync worker (lazy import to avoid circular dependencies)
_sync_worker = None


def get_tenant_path() -> Path:
    """Get tenant home from authenticated session (GDPR Art. 32)."""
    # In real implementation, extract from request context/session
    # For now, support both explicit tenant_id and fallback to session
    tenant_id = request.args.get('tenant_id') or getattr(g, 'tenant_id', '_default')
    return Path.home() / '.corvin' / 'tenants' / tenant_id


TENANT_PATH = Path.home() / '.corvin' / 'tenants' / '_default'  # Fallback
GITHUB_API_BASE = 'https://api.github.com'
GITHUB_REPO_PATTERN = r'^https://github\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9_.-]+)/?$'


class GitHubError(Exception):
    """GitHub-related errors."""
    pass


def validate_github_url(url: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Validate GitHub URL format and return (valid, owner, repo, error).

    Args:
        url: GitHub URL (e.g., https://github.com/owner/repo)

    Returns:
        (valid: bool, owner: str|None, repo: str|None, error: str|None)
    """
    url = url.strip()

    # Check format
    match = re.match(GITHUB_REPO_PATTERN, url)
    if not match:
        return False, None, None, "Invalid GitHub URL format. Expected: https://github.com/owner/repo"

    owner, repo = match.groups()

    # Validate characters (GitHub allows alphanumeric, dash, underscore, dot)
    if not re.match(r'^[a-zA-Z0-9_-]+$', owner):
        return False, None, None, f"Invalid owner name: {owner}"

    if not re.match(r'^[a-zA-Z0-9_.-]+$', repo):
        return False, None, None, f"Invalid repo name: {repo}"

    return True, owner, repo, None


def check_github_connectivity(owner: str, repo: str, token: Optional[str] = None) -> Tuple[bool, dict]:
    """
    Check GitHub repository accessibility via API.

    Args:
        owner: Repository owner
        repo: Repository name
        token: Optional GitHub API token for higher rate limits

    Returns:
        (accessible: bool, details: dict)
        Details includes: {status, error, rate_limit, last_checked}
    """
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'CorvinOS-Tenant-Sync/1.0'
    }

    if token:
        headers['Authorization'] = f'token {token}'

    try:
        # Check repository metadata
        url = f'{GITHUB_API_BASE}/repos/{owner}/{repo}'
        response = requests.get(url, headers=headers, timeout=10)

        result = {
            'status': 'unknown',
            'error': None,
            'http_code': response.status_code,
            'rate_limit': response.headers.get('X-RateLimit-Remaining', 'unknown'),
            'last_checked': datetime.utcnow().isoformat(),
            'repo_exists': False,
            'repo_name': None,
            'repo_url': None,
            'repo_private': None,
            'repo_description': None,
        }

        if response.status_code == 200:
            data = response.json()
            result['status'] = 'connected'
            result['repo_exists'] = True
            result['repo_name'] = data.get('full_name')
            result['repo_url'] = data.get('html_url')
            result['repo_private'] = data.get('private', False)
            result['repo_description'] = data.get('description')
            return True, result

        elif response.status_code == 404:
            result['status'] = 'not_found'
            result['error'] = f"Repository '{owner}/{repo}' not found (404)"
            return False, result

        elif response.status_code == 401:
            result['status'] = 'unauthorized'
            result['error'] = "GitHub authentication failed (401). Invalid or expired token."
            return False, result

        elif response.status_code == 403:
            result['status'] = 'forbidden'
            result['error'] = "Access forbidden (403). Check repository permissions or rate limits."
            return False, result

        else:
            result['status'] = 'error'
            result['error'] = f"GitHub API error {response.status_code}: {response.reason}"
            return False, result

    except requests.Timeout:
        return False, {
            'status': 'timeout',
            'error': "GitHub API request timed out (10s)",
            'last_checked': datetime.utcnow().isoformat(),
        }

    except requests.ConnectionError as e:
        return False, {
            'status': 'connection_error',
            'error': f"Failed to connect to GitHub: {str(e)}",
            'last_checked': datetime.utcnow().isoformat(),
        }

    except Exception as e:
        return False, {
            'status': 'unknown_error',
            'error': f"Unexpected error: {str(e)}",
            'last_checked': datetime.utcnow().isoformat(),
        }


def save_github_config(owner: str, repo: str, token: Optional[str] = None, auto_sync: bool = True) -> Path:
    """
    Save GitHub configuration to tenant config.

    Args:
        owner: Repository owner
        repo: Repository name
        token: Optional GitHub API token
        auto_sync: Enable automatic sync

    Returns:
        Path to saved config file
    """
    config_dir = TENANT_PATH / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / 'github-config.json'

    config = {
        'github': {
            'url': f'https://github.com/{owner}/{repo}',
            'owner': owner,
            'repo': repo,
            'auto_sync': auto_sync,
            'last_verified': datetime.utcnow().isoformat(),
        }
    }

    # Only store token if provided (in separate secure file)
    if token:
        token_file = config_dir / '.github-token'
        token_file.write_text(token)
        token_file.chmod(0o600)  # Read/write for owner only

    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)

    return config_file


def load_github_config() -> dict:
    """Load GitHub configuration from tenant config."""
    config_file = TENANT_PATH / 'config' / 'github-config.json'

    if not config_file.exists():
        return {}

    with open(config_file) as f:
        return json.load(f)


def get_sync_status() -> dict:
    """Get current sync status."""
    config = load_github_config()

    if not config.get('github'):
        return {'connected': False, 'configured': False}

    github_cfg = config['github']
    status_file = TENANT_PATH / 'config' / '.sync-status'

    status = {
        'connected': True,
        'configured': True,
        'owner': github_cfg.get('owner'),
        'repo': github_cfg.get('repo'),
        'url': github_cfg.get('url'),
        'auto_sync': github_cfg.get('auto_sync', False),
        'last_verified': github_cfg.get('last_verified'),
        'last_sync': None,
        'sync_status': 'unknown',
        'sync_error': None,
    }

    # Load sync status if it exists
    if status_file.exists():
        try:
            with open(status_file) as f:
                sync_data = json.load(f)
            status.update(sync_data)
        except:
            pass

    return status


# Routes

@github_bp.route('/verify', methods=['POST'])
def verify_connection():
    """
    Verify GitHub repository connection.

    POST /api/console/github/verify
    Body: {"url": "https://github.com/owner/repo", "token": "optional-github-token"}

    Returns: {
        "connected": bool,
        "details": {
            "status": str,
            "error": str|null,
            "repo_exists": bool,
            "repo_name": str|null,
            ...
        }
    }
    """
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    token = data.get('token', '').strip() or None

    if not url:
        return jsonify({
            'connected': False,
            'details': {
                'status': 'invalid_input',
                'error': 'GitHub URL is required'
            }
        }), 400

    # Validate URL format
    valid, owner, repo, error = validate_github_url(url)
    if not valid:
        return jsonify({
            'connected': False,
            'details': {
                'status': 'invalid_url',
                'error': error
            }
        }), 400

    # Check connectivity
    accessible, details = check_github_connectivity(owner, repo, token)

    if accessible:
        # Save config on successful connection
        save_github_config(owner, repo, token, auto_sync=True)

    return jsonify({
        'connected': accessible,
        'details': details
    }), 200 if accessible else 422


@github_bp.route('/status', methods=['GET'])
def get_status():
    """
    Get current GitHub sync status.

    GET /api/console/github/status

    Returns: {
        "connected": bool,
        "configured": bool,
        "owner": str|null,
        "repo": str|null,
        "url": str|null,
        "auto_sync": bool,
        "last_verified": str|null,
        "last_sync": str|null,
        "sync_status": str,
        "sync_error": str|null
    }
    """
    status = get_sync_status()
    return jsonify(status), 200


@github_bp.route('/config', methods=['GET'])
def get_config():
    """
    Get GitHub configuration (without sensitive data).

    GET /api/console/github/config
    """
    config = load_github_config()

    if not config.get('github'):
        return jsonify({'configured': False}), 404

    github_cfg = config['github']
    return jsonify({
        'configured': True,
        'url': github_cfg.get('url'),
        'owner': github_cfg.get('owner'),
        'repo': github_cfg.get('repo'),
        'auto_sync': github_cfg.get('auto_sync', False),
        'last_verified': github_cfg.get('last_verified'),
    }), 200


@github_bp.route('/config', methods=['DELETE'])
def disconnect():
    """
    Disconnect from GitHub (remove configuration).

    DELETE /api/console/github/config
    """
    config_file = TENANT_PATH / 'config' / 'github-config.json'
    token_file = TENANT_PATH / 'config' / '.github-token'

    try:
        if config_file.exists():
            config_file.unlink()
        if token_file.exists():
            token_file.unlink()

        # Stop sync worker if running
        try:
            from ..sync_worker import stop_sync_worker
            stop_sync_worker()
        except:
            pass

        return jsonify({
            'success': True,
            'message': 'GitHub configuration removed'
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Sync Worker Management

def get_sync_worker():
    """Get or create sync worker instance."""
    global _sync_worker
    if _sync_worker is None:
        from ..sync_worker import SyncWorker
        _sync_worker = SyncWorker(interval_seconds=300)
    return _sync_worker


def start_sync_worker():
    """Start sync worker in background."""
    worker = get_sync_worker()
    if not worker.running:
        worker.start()
        logger.info('Sync worker started')
    return worker
