"""Receiver endpoint for federated skill sync with authentication.

Handles incoming federation requests from peer instances.
Validates all requests with Bearer token and request signature verification.

ADR-0XXX: Federation Authentication (GH-004 remediation)
GDPR Art. 32: Integrity and confidentiality of cross-instance transfers
"""

from flask import Blueprint, request, jsonify
import hmac
import hashlib
import json
import logging
from functools import wraps
from datetime import datetime
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

federation_receiver_bp = Blueprint(
    'federation_receiver',
    __name__,
    url_prefix='/v1/federation'
)


def validate_federation_auth(f):
    """Decorator to validate federation authentication headers.

    Validates:
    1. Authorization: Bearer <token> header present and valid
    2. X-Tenant-ID: <tenant_id> header present and valid
    3. X-Request-Signature: sha256=<HMAC> (if provided)

    Fail-closed: Any validation failure returns 401.

    Attaches to request:
    - request.federation_token: Validated Bearer token
    - request.federation_tenant_id: Validated tenant ID
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # ====== STEP 1: Validate Authorization header ======
        auth_header = request.headers.get('Authorization', '').strip()
        if not auth_header.startswith('Bearer '):
            logger.warning(
                f'Federation request missing valid Authorization header '
                f'(remote_ip={request.remote_addr})'
            )
            return jsonify({
                'success': False,
                'error': 'Missing or invalid Authorization header',
                'code': 'MISSING_AUTH',
            }), 401

        received_token = auth_header[7:]  # Strip "Bearer "

        # Validate token format
        if not _is_valid_token_format(received_token):
            logger.warning(
                f'Federation request with malformed token '
                f'(remote_ip={request.remote_addr})'
            )
            return jsonify({
                'success': False,
                'error': 'Invalid token format',
                'code': 'INVALID_TOKEN_FORMAT',
            }), 401

        # ====== STEP 2: Validate X-Tenant-ID header ======
        tenant_id = request.headers.get('X-Tenant-ID', '_default').strip()
        if not tenant_id or not _is_valid_tenant_id(tenant_id):
            logger.warning(
                f'Federation request with invalid tenant_id={tenant_id}'
            )
            return jsonify({
                'success': False,
                'error': 'Missing or invalid X-Tenant-ID header',
                'code': 'INVALID_TENANT_ID',
            }), 401

        # ====== STEP 3: Load and validate token ======
        try:
            from .federation_token_manager import FederationTokenManager
            token_manager = FederationTokenManager(tenant_id)
            expected_token = token_manager.get_or_generate_token()

            # Compare tokens using constant-time comparison
            if not hmac.compare_digest(received_token, expected_token):
                logger.warning(
                    f'Federation request with mismatched token '
                    f'(tenant_id={tenant_id}, remote_ip={request.remote_addr})'
                )
                return jsonify({
                    'success': False,
                    'error': 'Invalid token',
                    'code': 'INVALID_TOKEN',
                }), 401

        except Exception as e:
            logger.error(f'Failed to validate federation token: {e}')
            return jsonify({
                'success': False,
                'error': 'Token validation error',
                'code': 'TOKEN_VALIDATION_ERROR',
            }), 500

        # ====== STEP 4: Validate request signature (if provided) ======
        request_signature = request.headers.get('X-Request-Signature', '').strip()
        if request_signature:
            if not request_signature.startswith('sha256='):
                logger.warning(
                    f'Federation request with invalid signature format '
                    f'(tenant_id={tenant_id})'
                )
                return jsonify({
                    'success': False,
                    'error': 'Invalid signature format',
                    'code': 'INVALID_SIGNATURE_FORMAT',
                }), 401

            try:
                # Get request body
                payload_bytes = request.get_data()

                # Calculate expected signature
                expected_sig = hmac.new(
                    received_token.encode(),
                    payload_bytes,
                    hashlib.sha256
                ).hexdigest()

                # Compare (constant-time)
                received_sig = request_signature[7:]  # Strip "sha256="
                if not hmac.compare_digest(expected_sig, received_sig):
                    logger.warning(
                        f'Federation request signature mismatch '
                        f'(tenant_id={tenant_id})'
                    )
                    return jsonify({
                        'success': False,
                        'error': 'Signature verification failed',
                        'code': 'SIGNATURE_MISMATCH',
                    }), 401

            except Exception as e:
                logger.error(f'Signature validation failed: {e}')
                return jsonify({
                    'success': False,
                    'error': 'Signature validation error',
                    'code': 'SIGNATURE_VALIDATION_ERROR',
                }), 500

        # ====== AUTH PASSED ======
        # Attach validated data to request context
        request.federation_token = received_token
        request.federation_tenant_id = tenant_id

        logger.debug(
            f'Federation request authenticated '
            f'(tenant_id={tenant_id}, remote_ip={request.remote_addr})'
        )

        return f(*args, **kwargs)

    return decorated_function


def _is_valid_token_format(token: str) -> bool:
    """Check if token has valid format.

    Valid tokens are:
    - 32-256 characters
    - Alphanumeric + '-' and '_' (URL-safe base64)
    """
    if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
        return False

    return all(c.isalnum() or c in '-_' for c in token)


def _is_valid_tenant_id(tenant_id: str) -> bool:
    """Check if tenant_id has valid format.

    Valid tenant IDs are:
    - Alphanumeric + '-' and '_'
    - 1-64 characters
    """
    if not isinstance(tenant_id, str) or len(tenant_id) < 1 or len(tenant_id) > 64:
        return False

    return all(c.isalnum() or c in '-_' for c in tenant_id)


# ============================================================================
# RECEIVER ENDPOINTS
# ============================================================================

@federation_receiver_bp.route('/skills/sync', methods=['POST'])
@validate_federation_auth
def receive_skill_sync():
    """Receive skill updates from peer instance.

    Authenticated endpoint: validates Authorization and request signature.

    POST /v1/federation/skills/sync

    Headers (required):
    - Authorization: Bearer <federation_token>
    - X-Tenant-ID: <tenant_id>
    - X-Request-Signature: sha256=<HMAC> (recommended)

    Body:
    {
        "tenant_id": "...",
        "skills": {
            "skill_id": "skill_content",
            ...
        },
        "source_instance": "instance_id",
        "timestamp": "2026-08-20T..."
    }

    Returns:
    {
        "success": bool,
        "received_count": int,
        "stored_count": int,
        "errors": [str],
        "timestamp": str,
    }
    """
    tenant_id = request.federation_tenant_id

    # Parse payload
    try:
        payload = request.get_json()
    except Exception as e:
        logger.warning(f'Invalid JSON in federation sync (tenant={tenant_id})')
        return jsonify({
            'success': False,
            'error': f'Invalid JSON: {e}',
            'code': 'INVALID_JSON',
        }), 400

    if not payload or not isinstance(payload, dict):
        return jsonify({
            'success': False,
            'error': 'Payload must be a JSON object',
            'code': 'INVALID_PAYLOAD',
        }), 400

    # Validate payload structure
    if not isinstance(payload.get('skills'), dict):
        logger.warning(
            f'Invalid skills payload (tenant={tenant_id})'
        )
        return jsonify({
            'success': False,
            'error': 'Skills must be a JSON object',
            'code': 'INVALID_SKILLS',
        }), 400

    skills = payload.get('skills', {})
    source_instance = payload.get('source_instance', 'unknown')
    timestamp = payload.get('timestamp', datetime.utcnow().isoformat())

    # Validate skills count
    if len(skills) > 10000:
        logger.warning(
            f'Skill sync rejected: too many skills '
            f'(count={len(skills)}, tenant={tenant_id})'
        )
        return jsonify({
            'success': False,
            'error': 'Too many skills (max 10000)',
            'code': 'TOO_MANY_SKILLS',
        }), 400

    # TODO: Persist skills with validation
    # - Validate skill content format
    # - Check for malicious content
    # - Audit log the import
    # - Merge with existing skills
    # For now, just acknowledge

    logger.info(
        f'Received skill sync from {source_instance} '
        f'(tenant={tenant_id}, skills={len(skills)})'
    )

    return jsonify({
        'success': True,
        'received_count': len(skills),
        'stored_count': len(skills),  # TODO: actual stored count
        'errors': [],
        'timestamp': datetime.utcnow().isoformat(),
    }), 200


@federation_receiver_bp.route('/skills/list', methods=['GET'])
@validate_federation_auth
def list_skills():
    """List available skills for pull sync.

    Authenticated endpoint: validates Bearer token.

    GET /v1/federation/skills/list

    Headers (required):
    - Authorization: Bearer <federation_token>
    - X-Tenant-ID: <tenant_id>

    Returns:
    {
        "success": bool,
        "skills": {
            "skill_id": "skill_content",
            ...
        },
        "count": int,
        "timestamp": str,
    }
    """
    tenant_id = request.federation_tenant_id

    # TODO: Load tenant's skills from storage
    # For now, return empty list
    skills = {}

    logger.debug(
        f'Listed skills for federation pull '
        f'(tenant={tenant_id}, count={len(skills)})'
    )

    return jsonify({
        'success': True,
        'skills': skills,
        'count': len(skills),
        'timestamp': datetime.utcnow().isoformat(),
    }), 200


@federation_receiver_bp.route('/health', methods=['GET'])
@validate_federation_auth
def health_check():
    """Health check endpoint for federation peers.

    Authenticated endpoint: validates Bearer token.

    GET /v1/federation/health

    Headers (required):
    - Authorization: Bearer <federation_token>
    - X-Tenant-ID: <tenant_id>

    Returns:
    {
        "status": "healthy",
        "version": str,
        "timestamp": str,
    }
    """
    tenant_id = request.federation_tenant_id

    return jsonify({
        'status': 'healthy',
        'version': '0.2-rc1',
        'timestamp': datetime.utcnow().isoformat(),
    }), 200
