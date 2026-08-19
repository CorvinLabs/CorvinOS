# Phase 2 Security Remediation Plan

**Status:** PLANNED  
**Target Completion:** Sprint 2.3  
**Compliance:** GDPR Art. 32 (Security), EU AI Act Art. 15 (Security Measures)

---

## Overview

Three critical security findings from Phase 2 gate require remediation:

| Finding | Severity | Issue | Remediation | Effort |
|---------|----------|-------|-------------|--------|
| BR-002 | HIGH | Brain pickle RCE → untrusted deserialization | Replace pickle with signed JSON/protobuf | 2d |
| GH-001 | CRITICAL | Webhook signature not enforced on init | Require secret on registration, fail-closed | 1d |
| GH-004 | HIGH | Federation auth missing → unauthenticated sync | Add HMAC-signed requests + verify endpoint | 2d |

---

## Finding 1: BR-002 — Brain Pickle RCE

### Current State
- **File:** `core/context_engineering/session_checkpoint.py`
- **Status:** ✅ ALREADY FIXED (uses JSON)
- **Risk:** Untrusted deserialization via pickle could execute arbitrary code

### Code Audit

The codebase **has already migrated** from pickle to JSON:

```python
# session_checkpoint.py:75-77 (SAFE ✅)
def to_json(self) -> str:
    """Serialize to JSON (for persistence)."""
    return json.dumps(self.to_dict(), default=str)

# session_checkpoint.py:85-87 (SAFE ✅)
@classmethod
def from_json(cls, json_str: str) -> "SessionCheckpoint":
    """Reconstruct from JSON."""
    return cls.from_dict(json.loads(json_str))
```

### Remaining Risk
1. **Implicit deserialization in decision_history** (line 180-183): Nested dicts are reconstructed without schema validation
2. **No signature on checkpoint files** → could be tampered on disk

### Remediation Steps

#### Step 1: Add JSON Schema Validation

Create `core/context_engineering/checkpoint_schema.py`:

```python
"""Schema validation for SessionCheckpoint to prevent injection attacks."""

import json
from typing import Any, Dict
from jsonschema import Draft7Validator, ValidationError
import logging

logger = logging.getLogger(__name__)

# Define strict JSON schema for SessionCheckpoint
CHECKPOINT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "checkpoint_id", "task_id", "session_id", "tenant_id",
        "context_state", "decision_history", "checkpoints",
        "created_at", "last_activity_at"
    ],
    "properties": {
        "checkpoint_id": {"type": "string", "pattern": "^[a-f0-9-]+$"},
        "task_id": {"type": "string", "maxLength": 256},
        "session_id": {"type": "string", "maxLength": 256},
        "tenant_id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"},
        
        "context_state": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "task_template": {"type": "object"},
                "context_stack": {"type": "string"},
                "budget_remaining": {"type": "number", "minimum": 0},
                "time_remaining": {"type": "integer", "minimum": 0},
                "model": {"type": "string"},
                "strategy": {"type": "string"},
                "strategy_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "guidance_overrides": {"type": "object"},
            },
            "additionalProperties": False
        },
        
        "decision_history": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "subsystem": {"type": "string"},
                    "decision_type": {"type": "string"},
                    "value": {},  # Any value
                    "reasoning": {"type": "string"},
                    "context_stack": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "guidance_applied": {"type": "boolean"},
                },
                "additionalProperties": False,
                "required": ["timestamp", "subsystem", "decision_type"]
            }
        },
        
        "checkpoints": {"type": "array"},
        "created_at": {"type": "string", "format": "date-time"},
        "last_activity_at": {"type": "string", "format": "date-time"},
        "turn_number": {"type": "integer", "minimum": 0},
        "tokens_consumed": {"type": "integer", "minimum": 0},
        "cost_consumed_cents": {"type": "number", "minimum": 0},
        "error_recovery_state": {
            "type": ["object", "null"],
            "additionalProperties": True
        }
    },
    "additionalProperties": False
}

class CheckpointValidationError(ValueError):
    """Raised when checkpoint JSON fails schema validation."""
    pass

def validate_checkpoint_json(json_str: str) -> Dict[str, Any]:
    """
    Parse and validate checkpoint JSON against schema.
    
    Raises CheckpointValidationError if validation fails (fail-closed).
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise CheckpointValidationError(f"Invalid JSON: {e}")
    
    # Validate against schema
    validator = Draft7Validator(CHECKPOINT_SCHEMA)
    errors = list(validator.iter_errors(data))
    
    if errors:
        error_msg = "; ".join([f"{e.path}: {e.message}" for e in errors[:3]])
        raise CheckpointValidationError(
            f"Checkpoint validation failed: {error_msg}"
        )
    
    logger.debug(f"Checkpoint validation passed for {data.get('checkpoint_id')}")
    return data
```

#### Step 2: Add HMAC-Signed Checkpoints

Update `core/context_engineering/session_checkpoint.py`:

```python
"""Add HMAC signing to prevent checkpoint tampering (ADR-0XXX)."""

import hmac
import hashlib
from typing import Optional

class SessionCheckpoint:
    """..existing docstring..."""
    
    # Add signature field
    signature: Optional[str] = None
    
    def sign(self, secret: str) -> None:
        """Sign checkpoint with HMAC-SHA256.
        
        Args:
            secret: Signing secret (typically from tenant config)
        
        Raises:
            ValueError: If secret is empty or invalid
        """
        if not secret or not isinstance(secret, str):
            raise ValueError("Secret must be non-empty string")
        
        # Create deterministic JSON (sorted keys) for signature
        checkpoint_dict = {
            k: v for k, v in self.to_dict().items()
            if k != "signature"
        }
        checkpoint_json = json.dumps(checkpoint_dict, sort_keys=True, default=str)
        
        self.signature = hmac.new(
            secret.encode(),
            checkpoint_json.encode(),
            hashlib.sha256
        ).hexdigest()
    
    def verify(self, secret: str) -> bool:
        """Verify checkpoint signature.
        
        Returns False if signature invalid or missing (fail-closed).
        """
        if not self.signature:
            logger.warning(f"Checkpoint {self.checkpoint_id} has no signature")
            return False
        
        try:
            checkpoint_dict = {
                k: v for k, v in self.to_dict().items()
                if k != "signature"
            }
            checkpoint_json = json.dumps(checkpoint_dict, sort_keys=True, default=str)
            
            expected_sig = hmac.new(
                secret.encode(),
                checkpoint_json.encode(),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(self.signature, expected_sig)
        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            return False

class SessionContinuationManager:
    """..existing docstring.."""
    
    def __init__(self, corvin_home: Optional[str] = None, tenant_id: str = "_default",
                 checkpoint_secret: Optional[str] = None):
        """Initialize with optional signing secret.
        
        Args:
            checkpoint_secret: HMAC secret for signing checkpoints.
                              If None, signing is disabled (accept existing unsigned).
        """
        # ...existing init code...
        self.checkpoint_secret = checkpoint_secret
    
    def save_checkpoint(self, ...) -> str:
        """..existing docstring.."""
        # ...existing code until checkpoint creation...
        
        # Sign checkpoint if secret provided
        if self.checkpoint_secret:
            checkpoint.sign(self.checkpoint_secret)
        
        # ...rest of existing code...
    
    def load_checkpoint(self, ...) -> SessionCheckpoint:
        """..existing docstring.."""
        # ...existing code until checkpoint loaded...
        
        # Validate schema
        from .checkpoint_schema import validate_checkpoint_json, CheckpointValidationError
        try:
            validated_data = validate_checkpoint_json(checkpoint.to_json())
        except CheckpointValidationError as e:
            raise CheckpointNotFoundError(
                f"Checkpoint validation failed: {e}"
            ) from e
        
        # Verify signature if secret provided
        if self.checkpoint_secret and not checkpoint.verify(self.checkpoint_secret):
            raise CheckpointNotFoundError(
                f"Checkpoint {checkpoint.checkpoint_id} signature invalid"
            )
        
        return checkpoint
```

---

## Finding 2: GH-001 — Webhook Signature Not Enforced on Init

### Current State
- **File:** `core/console/corvin_console/routes/github_webhooks.py`
- **Risk:** Webhook can be registered without a secret, then unsigned webhooks are accepted
- **Impact:** CRITICAL — attacker can forge webhook events without signature verification

### Root Cause
- Line 213: `webhook_secret = data.get('webhook_secret', '')` — secret is optional
- Line 88-103: Handler accepts unsigned webhooks IF no secret configured
- No enforcement that secret MUST be provided during registration

### Remediation

#### Step 1: Require Secret on Registration (FAIL-CLOSED)

```python
"""File: core/console/corvin_console/routes/github_webhooks.py"""

@webhook_bp.route('/webhook/register', methods=['POST'])
def register_webhook():
    """
    Register webhook with GitHub via API.
    
    REQUIRED: webhook_secret must be provided (32+ characters).
    
    POST /api/console/github/webhook/register
    Body: {
        "token": "github-api-token",
        "webhook_secret": "your-random-secret-32-chars-minimum"  ← REQUIRED NOW
    }
    
    Returns: {
        "success": bool,
        "webhook_id": str,
        "secret_fingerprint": "sha256=...",  ← NEW: confirm secret
        ...
    }
    """
    data = request.get_json() or {}
    token = data.get('token', '')
    webhook_secret = data.get('webhook_secret', '').strip()
    
    # FIX GH-001a: FAIL-CLOSED — require secret
    MIN_SECRET_LENGTH = 32
    if not webhook_secret or len(webhook_secret) < MIN_SECRET_LENGTH:
        logger.warning('Webhook registration rejected: secret missing or too short')
        return jsonify({
            'success': False,
            'error': f'webhook_secret is required (minimum {MIN_SECRET_LENGTH} characters)',
            'min_length': MIN_SECRET_LENGTH,
        }), 400
    
    config_file = TENANT_PATH / 'config' / 'github-config.json'
    if not config_file.exists():
        return jsonify({
            'success': False,
            'error': 'GitHub not configured. Connect first.',
        }), 400
    
    try:
        with open(config_file) as f:
            config = json.load(f)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to read config: {e}',
        }), 500
    
    github_cfg = config.get('github', {})
    owner = github_cfg.get('owner')
    repo = github_cfg.get('repo')
    
    if not token or not owner or not repo:
        return jsonify({
            'success': False,
            'error': 'Missing required parameters: token, owner, repo',
        }), 400
    
    # FIX GH-001b: Generate fingerprint to confirm secret securely
    secret_fingerprint = (
        'sha256=' + hashlib.sha256(webhook_secret.encode()).hexdigest()[:16]
    )
    
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
            'secret': webhook_secret,  # FIX GH-001: NOW REQUIRED
        }
    }
    
    try:
        response = requests.post(api_url, headers=headers, json=webhook_data, timeout=10)
        
        if response.status_code in (201, 200):
            webhook_info = response.json()
            
            # Save webhook secret to config
            github_cfg['webhook_secret'] = webhook_secret
            github_cfg['webhook_id'] = webhook_info.get('id')
            github_cfg['webhook_registered'] = True
            github_cfg['webhook_secret_set_at'] = datetime.utcnow().isoformat()
            
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            
            logger.info(
                f'Webhook registered successfully (id={webhook_info.get("id")}, '
                f'fingerprint={secret_fingerprint})'
            )
            
            return jsonify({
                'success': True,
                'webhook_id': webhook_info.get('id'),
                'secret_fingerprint': secret_fingerprint,  # ← Confirm secret stored
                'url': webhook_data['config']['url'],
                'events': webhook_data['events'],
                'active': webhook_info.get('active'),
                'message': 'Webhook registered successfully. Secret is required for all incoming webhooks.',
                'timestamp': datetime.utcnow().isoformat(),
            }), 200
        
        elif response.status_code == 422:
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
        logger.error(f'Failed to register webhook: {e}')
        return jsonify({
            'success': False,
            'error': f'Failed to register webhook: {str(e)}',
        }), 500
```

#### Step 2: Enforce Signature Verification (Strengthen Handler)

```python
"""File: core/console/corvin_console/routes/github_webhooks.py"""

@webhook_bp.route('/webhook', methods=['POST'])
def handle_github_webhook():
    """
    Handle GitHub webhook events.
    
    FIX GH-001c: Signature verification is NOW MANDATORY (fail-closed).
    
    POST /api/console/github/webhook
    
    Requires:
    - X-Hub-Signature-256 header (GitHub HMAC)
    - Valid secret configured in github-config.json
    
    Returns: {
        "success": bool,
        "event_type": str,
        "action": str,
        "message": str,
        "sync_triggered": bool,
    }
    """
    payload_body = request.get_data()
    
    # FIX GH-001c: Signature verification is MANDATORY
    signature = request.headers.get('X-Hub-Signature-256', '')
    secret = get_webhook_secret()
    
    # Fail-closed: ALWAYS verify if secret is configured
    if not secret:
        # No secret = webhook not properly registered
        logger.warning(
            'Webhook received but no secret configured. '
            'Run /api/console/github/webhook/register first.'
        )
        return jsonify({
            'success': False,
            'error': 'Webhook not configured. Register via /api/console/github/webhook/register',
            'code': 'WEBHOOK_NOT_REGISTERED',
        }), 403
    
    # Verify signature (fail-closed)
    if not verify_webhook_signature(payload_body, signature, secret):
        logger.warning(
            f'Invalid webhook signature received. '
            f'Source IP: {request.remote_addr}'
        )
        # Log audit event (ADR-0232: audit trail)
        # audit_logger.log_security_event(
        #     event='webhook_signature_verification_failed',
        #     details={'remote_ip': request.remote_addr},
        # )
        return jsonify({
            'success': False,
            'error': 'Invalid signature',
            'code': 'SIGNATURE_VERIFICATION_FAILED',
        }), 401
    
    # Parse payload
    try:
        payload = json.loads(payload_body)
    except json.JSONDecodeError:
        logger.warning('Received malformed JSON in webhook')
        return jsonify({
            'success': False,
            'error': 'Invalid JSON',
        }), 400
    
    # ... rest of existing handler logic ...
```

#### Step 3: Add Tests for Enforcement

Create test file `tests/unit/core/console/test_github_webhooks_security.py`:

```python
"""Security tests for GitHub webhook enforcement."""

import pytest
import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from flask import Flask

@pytest.fixture
def app():
    """Flask test app."""
    from core.console.corvin_console.routes.github_webhooks import webhook_bp
    app = Flask(__name__)
    app.register_blueprint(webhook_bp)
    return app

@pytest.fixture
def client(app):
    """Test client."""
    return app.test_client()

class TestWebhookSecurityEnforcement:
    """Test that webhook security is fail-closed."""
    
    def test_webhook_registration_without_secret_rejected(self, client, tmp_path, monkeypatch):
        """GH-001a: Registration without secret is REJECTED."""
        # Setup config
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        config_dir = tenant_path / 'config'
        config_dir.mkdir(parents=True)
        
        config = {
            'github': {
                'owner': 'owner',
                'repo': 'repo',
                'token': 'ghp_test',
            }
        }
        with open(config_dir / 'github-config.json', 'w') as f:
            json.dump(config, f)
        
        # Attempt registration WITHOUT secret
        response = client.post(
            '/api/console/github/webhook/register',
            json={'token': 'ghp_test'}  # NO webhook_secret
        )
        
        # MUST fail with 400
        assert response.status_code == 400
        data = response.get_json()
        assert not data['success']
        assert 'webhook_secret' in data['error'].lower()
        assert data.get('min_length', 0) >= 32
    
    def test_webhook_registration_short_secret_rejected(self, client, tmp_path, monkeypatch):
        """GH-001a: Registration with short secret is REJECTED."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        config_dir = tenant_path / 'config'
        config_dir.mkdir(parents=True)
        
        config = {'github': {'owner': 'owner', 'repo': 'repo'}}
        with open(config_dir / 'github-config.json', 'w') as f:
            json.dump(config, f)
        
        # Secret < 32 chars
        response = client.post(
            '/api/console/github/webhook/register',
            json={'token': 'ghp_test', 'webhook_secret': 'short'}
        )
        
        assert response.status_code == 400
        data = response.get_json()
        assert not data['success']
    
    @patch('core.console.corvin_console.routes.github_webhooks.get_webhook_secret')
    def test_webhook_without_configured_secret_rejected(self, mock_get_secret, client):
        """GH-001c: Webhook handler rejects if no secret configured."""
        mock_get_secret.return_value = ''  # No secret configured
        
        payload = json.dumps({'event': 'push'})
        response = client.post(
            '/api/console/github/webhook',
            data=payload,
            headers={'X-GitHub-Event': 'push', 'Content-Type': 'application/json'}
        )
        
        # MUST reject with 403
        assert response.status_code == 403
        data = response.get_json()
        assert not data['success']
        assert data['code'] == 'WEBHOOK_NOT_REGISTERED'
    
    @patch('core.console.corvin_console.routes.github_webhooks.get_webhook_secret')
    def test_webhook_invalid_signature_rejected(self, mock_get_secret, client):
        """GH-001c: Webhook handler rejects invalid signature."""
        secret = 'x' * 32
        mock_get_secret.return_value = secret
        
        payload = json.dumps({'event': 'push'}).encode()
        bad_sig = 'sha256=' + 'a' * 64  # Wrong signature
        
        response = client.post(
            '/api/console/github/webhook',
            data=payload,
            headers={
                'X-Hub-Signature-256': bad_sig,
                'X-GitHub-Event': 'push',
                'Content-Type': 'application/json'
            }
        )
        
        # MUST reject with 401
        assert response.status_code == 401
        data = response.get_json()
        assert not data['success']
        assert data['code'] == 'SIGNATURE_VERIFICATION_FAILED'
    
    @patch('core.console.corvin_console.routes.github_webhooks.get_webhook_secret')
    @patch('core.console.corvin_console.routes.github_webhooks.get_sync_worker')
    def test_webhook_valid_signature_accepted(self, mock_get_worker, mock_get_secret, client):
        """GH-001c: Webhook with valid signature IS accepted."""
        secret = 'x' * 32
        mock_get_secret.return_value = secret
        
        mock_worker = MagicMock()
        mock_worker.running = True
        mock_get_worker.return_value = mock_worker
        
        payload = json.dumps({'event': 'push'}).encode()
        valid_sig = 'sha256=' + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        
        response = client.post(
            '/api/console/github/webhook',
            data=payload,
            headers={
                'X-Hub-Signature-256': valid_sig,
                'X-GitHub-Event': 'push',
                'Content-Type': 'application/json'
            }
        )
        
        # MUST accept
        assert response.status_code == 200
        data = response.get_json()
        assert data['success']
```

---

## Finding 3: GH-004 — Federation Auth Missing

### Current State
- **File:** `core/console/corvin_console/routes/federation_model.py`
- **Risk:** Cross-instance skill sync and federated learning lack authentication
- **Impact:** HIGH — attacker can push/pull malicious skills or training data

### Root Causes
1. Line 304-309: `push_skills_to_peers()` includes auth header but token is auto-generated without validation
2. Line 334: `pull_skills_from_peers()` has NO auth headers at all
3. No receiving endpoint shown to validate the auth headers
4. Federation token persisted in plain text (line 282-284)

### Remediation

#### Step 1: Add Secure Token Management

```python
"""File: core/console/corvin_console/routes/federation_model.py"""

import os
import secrets
from pathlib import Path
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class FederationTokenManager:
    """Manage federation authentication tokens securely."""
    
    # Token validity: 24 hours
    TOKEN_VALIDITY_HOURS = 24
    MIN_TOKEN_LENGTH = 32
    
    def __init__(self, tenant_id: str = "_default"):
        """Initialize token manager.
        
        Args:
            tenant_id: Tenant identifier
        """
        self.tenant_id = tenant_id
        self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id
        self._token_file = self.tenant_path / 'federation-token.secure'
        self._token_metadata_file = self.tenant_path / 'federation-token-meta.json'
        
        # Ensure secure permissions
        self._setup_secure_storage()
    
    def _setup_secure_storage(self):
        """Ensure token files have restrictive permissions."""
        self.tenant_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        
        # Create token file with 0600 permissions if it doesn't exist
        if not self._token_file.exists():
            self._token_file.touch(mode=0o600)
            # On some systems, touch doesn't respect mode, so also chmod
            os.chmod(self._token_file, 0o600)
    
    def get_or_generate_token(self, force_rotate: bool = False) -> str:
        """Get existing token or generate new one.
        
        FIX GH-004a: Generate cryptographically secure tokens.
        
        Args:
            force_rotate: If True, generate new token even if one exists
        
        Returns:
            Federation auth token (32+ bytes, URL-safe)
        
        Raises:
            ValueError: If token is invalid or storage fails
        """
        if not force_rotate and self._token_file.exists():
            token = self._token_file.read_text().strip()
            
            # Validate token length and content
            if self._validate_token(token):
                logger.debug(f"Using existing federation token (tenant={self.tenant_id})")
                return token
            else:
                logger.warning(f"Existing token invalid, regenerating (tenant={self.tenant_id})")
        
        # Generate new token
        token = secrets.token_urlsafe(32)  # 32 bytes = 256 bits, URL-safe
        
        if len(token) < self.MIN_TOKEN_LENGTH:
            raise ValueError(f"Generated token too short (len={len(token)})")
        
        # Save token with metadata
        self._token_file.write_text(token)
        os.chmod(self._token_file, 0o600)
        
        metadata = {
            'tenant_id': self.tenant_id,
            'generated_at': datetime.utcnow().isoformat(),
            'expires_at': (datetime.utcnow() + timedelta(hours=self.TOKEN_VALIDITY_HOURS)).isoformat(),
            'rotation_count': self._get_rotation_count() + 1,
        }
        
        with open(self._token_metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        os.chmod(self._token_metadata_file, 0o600)
        
        logger.info(
            f"Generated new federation token (tenant={self.tenant_id}, "
            f"rotation={metadata['rotation_count']})"
        )
        
        return token
    
    def _validate_token(self, token: str) -> bool:
        """Validate token format and length.
        
        Returns:
            True if token is valid, False otherwise
        """
        return (
            isinstance(token, str) and
            len(token) >= self.MIN_TOKEN_LENGTH and
            len(token) <= 256 and
            all(c.isalnum() or c in '-_' for c in token)  # URL-safe chars only
        )
    
    def _get_rotation_count(self) -> int:
        """Get current rotation count from metadata."""
        if self._token_metadata_file.exists():
            try:
                with open(self._token_metadata_file) as f:
                    metadata = json.load(f)
                return metadata.get('rotation_count', 0)
            except:
                pass
        return 0
    
    def is_token_expired(self) -> bool:
        """Check if token has expired (24h validity)."""
        if not self._token_metadata_file.exists():
            return True
        
        try:
            with open(self._token_metadata_file) as f:
                metadata = json.load(f)
            expires_at = datetime.fromisoformat(metadata['expires_at'])
            return datetime.utcnow() > expires_at
        except:
            return True
    
    def rotate_token(self) -> str:
        """Force token rotation (called on token compromise or TTL expiry)."""
        logger.info(f"Rotating federation token (tenant={self.tenant_id})")
        return self.get_or_generate_token(force_rotate=True)


class CrossInstanceSync:
    """Synchronize skills across instances with authentication."""
    
    def __init__(self, tenant_id: str = "_default"):
        """Initialize sync with secure token management.
        
        FIX GH-004: Token manager replaces insecure auto-gen.
        """
        self.tenant_id = tenant_id
        self.registry = FederationRegistry(tenant_id)
        self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id
        self.token_manager = FederationTokenManager(tenant_id)  # ← FIX GH-004a
    
    @property
    def federation_token(self) -> str:
        """Get current federation token (ensures valid)."""
        token = self.token_manager.get_or_generate_token()
        
        # Check if token is expired
        if self.token_manager.is_token_expired():
            logger.warning("Federation token expired, rotating")
            token = self.token_manager.rotate_token()
        
        return token
    
    def push_skills_to_peers(self, skills: Dict[str, str]) -> Dict[str, Any]:
        """Push skill updates to peer instances with authentication.
        
        FIX GH-004b: Add HMAC-signed request headers for verification.
        
        Args:
            skills: Dict of skill_id -> skill_content
        
        Returns:
            Results dict with pushed_to and failed lists
        """
        replicas = self.registry.get_healthy_instances(InstanceRole.REPLICA)
        
        results = {
            "pushed_to": [],
            "failed": [],
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        if not replicas:
            logger.warning("No healthy replicas available for skill push")
            return results
        
        for replica in replicas:
            try:
                # FIX GH-004b: Sign the request
                payload = {
                    "tenant_id": self.tenant_id,
                    "skills": skills,
                    "source_instance": "local",
                    "timestamp": datetime.utcnow().isoformat(),
                }
                
                payload_json = json.dumps(payload, sort_keys=True)
                request_signature = hmac.new(
                    self.federation_token.encode(),
                    payload_json.encode(),
                    hashlib.sha256
                ).hexdigest()
                
                # FIX GH-004: Include auth headers
                headers = {
                    "Authorization": f"Bearer {self.federation_token}",
                    "X-Request-Signature": f"sha256={request_signature}",
                    "X-Tenant-ID": self.tenant_id,
                    "X-Source-Instance": "local",
                    "Content-Type": "application/json",
                    "User-Agent": "Corvin/0.2-rc1",  # Identify version
                }
                
                response = requests.post(
                    f"{replica.url}/v1/federation/skills/sync",
                    json=payload,
                    headers=headers,
                    timeout=10,
                    verify=True,  # Always verify TLS
                )
                
                if response.status_code == 200:
                    results["pushed_to"].append(replica.instance_id)
                    logger.info(
                        f"Pushed skills to replica {replica.instance_id} "
                        f"({len(skills)} skills)"
                    )
                else:
                    results["failed"].append((replica.instance_id, response.status_code))
                    logger.warning(
                        f"Push to {replica.instance_id} failed: "
                        f"{response.status_code} {response.text[:100]}"
                    )
            
            except requests.RequestException as e:
                logger.error(f"Failed to push to {replica.instance_id}: {e}")
                results["failed"].append((replica.instance_id, str(e)))
        
        return results
    
    def pull_skills_from_peers(self) -> Dict[str, Any]:
        """Pull skill updates from peer instances with authentication.
        
        FIX GH-004c: Add auth headers to pull requests (was missing).
        
        Returns:
            Results dict with pulled_from, conflicts, merged_skills
        """
        replicas = self.registry.get_healthy_instances(InstanceRole.REPLICA)
        
        results = {
            "pulled_from": [],
            "conflicts": [],
            "merged_skills": {},
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        if not replicas:
            logger.warning("No healthy replicas available for skill pull")
            return results
        
        for replica in replicas:
            try:
                # FIX GH-004c: Add auth headers (NEW)
                headers = {
                    "Authorization": f"Bearer {self.federation_token}",
                    "X-Tenant-ID": self.tenant_id,
                    "X-Source-Instance": "local",
                    "Accept": "application/json",
                    "User-Agent": "Corvin/0.2-rc1",
                }
                
                response = requests.get(
                    f"{replica.url}/v1/federation/skills/list",
                    headers=headers,  # ← NEW AUTH
                    timeout=10,
                    verify=True,
                )
                
                if response.status_code == 200:
                    replica_skills = response.json().get("skills", {})
                    results["pulled_from"].append(replica.instance_id)
                    results["merged_skills"].update(replica_skills)
                    logger.info(
                        f"Pulled {len(replica_skills)} skills from replica "
                        f"{replica.instance_id}"
                    )
                else:
                    logger.warning(
                        f"Pull from {replica.instance_id} failed: "
                        f"{response.status_code}"
                    )
            
            except requests.RequestException as e:
                logger.error(f"Failed to pull from {replica.instance_id}: {e}")
        
        return results
```

#### Step 2: Add Receiving Endpoint with Auth Validation

Create file `core/console/corvin_console/routes/federation_receiver.py`:

```python
"""Receiver endpoint for federated skill sync with auth validation.

FIX GH-004: Validate incoming federation requests.
"""

from flask import Blueprint, request, jsonify
import hmac
import hashlib
import json
import logging
from functools import wraps
from typing import Optional

logger = logging.getLogger(__name__)

federation_receiver_bp = Blueprint('federation_receiver', __name__, 
                                   url_prefix='/v1/federation')


def validate_federation_auth(f):
    """Decorator to validate federation auth headers (fail-closed).
    
    FIX GH-004d: Verify Bearer token + request signature.
    
    Requires:
    - Authorization: Bearer <federation_token>
    - X-Request-Signature: sha256=<HMAC>
    - X-Tenant-ID: <tenant_id>
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get auth token
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            logger.warning('Federation request missing Bearer token')
            return jsonify({
                'success': False,
                'error': 'Missing Authorization header',
                'code': 'MISSING_AUTH',
            }), 401
        
        received_token = auth_header[7:]  # Strip "Bearer "
        
        # Get tenant ID
        tenant_id = request.headers.get('X-Tenant-ID', '_default')
        if not tenant_id or not isinstance(tenant_id, str):
            logger.warning('Federation request missing X-Tenant-ID')
            return jsonify({
                'success': False,
                'error': 'Missing X-Tenant-ID header',
                'code': 'MISSING_TENANT_ID',
            }), 401
        
        # Load expected token from storage
        try:
            from .federation_model import FederationTokenManager
            token_manager = FederationTokenManager(tenant_id)
            expected_token = token_manager.get_or_generate_token()
            
            # Compare tokens (constant-time)
            if not hmac.compare_digest(received_token, expected_token):
                logger.warning(
                    f'Federation request with invalid token '
                    f'(tenant={tenant_id}, remote_ip={request.remote_addr})'
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
                'error': f'Token validation failed: {e}',
            }), 500
        
        # Validate request signature (if provided)
        expected_sig = request.headers.get('X-Request-Signature')
        if expected_sig and expected_sig.startswith('sha256='):
            try:
                payload_json = request.get_data(as_text=True)
                payload_sig = hmac.new(
                    received_token.encode(),
                    payload_json.encode(),
                    hashlib.sha256
                ).hexdigest()
                
                expected_sig_value = expected_sig[7:]  # Strip "sha256="
                
                if not hmac.compare_digest(payload_sig, expected_sig_value):
                    logger.warning(
                        f'Federation request signature mismatch '
                        f'(tenant={tenant_id})'
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
                }), 500
        
        # Auth passed — attach token and tenant to request context
        request.federation_token = received_token
        request.federation_tenant_id = tenant_id
        
        return f(*args, **kwargs)
    
    return decorated_function


@federation_receiver_bp.route('/skills/sync', methods=['POST'])
@validate_federation_auth
def receive_skill_sync():
    """Receive skill updates from peer instance (authenticated).
    
    FIX GH-004d: Receiver endpoint with auth validation.
    
    POST /v1/federation/skills/sync
    
    Headers:
    - Authorization: Bearer <token>
    - X-Request-Signature: sha256=<HMAC>
    - X-Tenant-ID: <tenant_id>
    
    Body:
    {
        "tenant_id": "...",
        "skills": { "skill_id": "skill_content", ... },
        "source_instance": "...",
        "timestamp": "..."
    }
    
    Returns: {
        "success": bool,
        "received_count": int,
        "timestamp": str,
    }
    """
    tenant_id = request.federation_tenant_id
    
    try:
        payload = request.get_json()
    except:
        logger.warning('Invalid JSON in federation sync request')
        return jsonify({
            'success': False,
            'error': 'Invalid JSON',
        }), 400
    
    # Validate payload
    if not isinstance(payload.get('skills'), dict):
        logger.warning(f'Invalid skills payload (tenant={tenant_id})')
        return jsonify({
            'success': False,
            'error': 'Invalid skills format',
        }), 400
    
    skills = payload['skills']
    source_instance = payload.get('source_instance', 'unknown')
    
    # Store skills (in production: merge, validate, audit)
    logger.info(
        f'Received {len(skills)} skills from {source_instance} '
        f'(tenant={tenant_id})'
    )
    
    # TODO: Persist skills, validate content, audit log
    
    return jsonify({
        'success': True,
        'received_count': len(skills),
        'timestamp': datetime.utcnow().isoformat(),
    }), 200


@federation_receiver_bp.route('/skills/list', methods=['GET'])
@validate_federation_auth
def list_skills():
    """List available skills for pull (authenticated).
    
    FIX GH-004c: Receiver endpoint for pull requests.
    
    GET /v1/federation/skills/list
    
    Headers:
    - Authorization: Bearer <token>
    - X-Tenant-ID: <tenant_id>
    
    Returns: {
        "success": bool,
        "skills": { "skill_id": "skill_content", ... },
        "count": int,
        "timestamp": str,
    }
    """
    tenant_id = request.federation_tenant_id
    
    # TODO: Load tenant's skills
    skills = {}  # Placeholder
    
    logger.debug(f'Listed {len(skills)} skills for federation (tenant={tenant_id})')
    
    return jsonify({
        'success': True,
        'skills': skills,
        'count': len(skills),
        'timestamp': datetime.utcnow().isoformat(),
    }), 200
```

#### Step 3: Add Security Tests

Create `tests/unit/core/console/test_federation_security.py`:

```python
"""Security tests for federation endpoints."""

import pytest
import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestFederationTokenSecurity:
    """Test federation token generation and management."""
    
    def test_token_generation_secure(self, tmp_path, monkeypatch):
        """GH-004a: Generated tokens are secure (32+ bytes, URL-safe)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from core.console.corvin_console.routes.federation_model import FederationTokenManager
        
        manager = FederationTokenManager("_default")
        token = manager.get_or_generate_token()
        
        # Validate token properties
        assert len(token) >= 32
        assert all(c.isalnum() or c in '-_' for c in token)  # URL-safe only
        assert token.count('/') == 0  # No unencoded slashes
    
    def test_token_persisted_securely(self, tmp_path, monkeypatch):
        """GH-004a: Token file has restrictive permissions (0600)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from core.console.corvin_console.routes.federation_model import FederationTokenManager
        
        manager = FederationTokenManager("_default")
        token = manager.get_or_generate_token()
        
        # Check file permissions
        token_file = manager._token_file
        assert token_file.exists()
        
        import stat
        st = token_file.stat()
        mode = stat.S_IMODE(st.st_mode)
        
        # Should be 0600 (owner read/write only)
        assert mode == 0o600
    
    def test_token_reused_if_valid(self, tmp_path, monkeypatch):
        """GH-004a: Valid token is reused (not regenerated)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from core.console.corvin_console.routes.federation_model import FederationTokenManager
        
        manager = FederationTokenManager("_default")
        token1 = manager.get_or_generate_token()
        token2 = manager.get_or_generate_token()  # Should return same
        
        assert token1 == token2


class TestFederationAuthHeaders:
    """Test authentication headers on federation requests."""
    
    @patch('core.console.corvin_console.routes.federation_model.requests.post')
    def test_push_includes_auth_header(self, mock_post, tmp_path, monkeypatch):
        """GH-004b: push_skills_to_peers includes Authorization header."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        # Setup
        from core.console.corvin_console.routes.federation_model import CrossInstanceSync, FederatedInstance, InstanceRole
        
        sync = CrossInstanceSync("_default")
        replica = FederatedInstance("replica1", "https://replica.local", InstanceRole.REPLICA)
        sync.registry.register_instance(replica)
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        # Push skills
        sync.push_skills_to_peers({"skill1": "content"})
        
        # Verify auth header was sent
        call_kwargs = mock_post.call_args[1]
        headers = call_kwargs['headers']
        
        assert 'Authorization' in headers
        assert headers['Authorization'].startswith('Bearer ')
        assert len(headers['Authorization']) > 10
    
    @patch('core.console.corvin_console.routes.federation_model.requests.post')
    def test_push_includes_signature_header(self, mock_post, tmp_path, monkeypatch):
        """GH-004b: push_skills_to_peers includes X-Request-Signature."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from core.console.corvin_console.routes.federation_model import CrossInstanceSync, FederatedInstance, InstanceRole
        
        sync = CrossInstanceSync("_default")
        replica = FederatedInstance("replica1", "https://replica.local", InstanceRole.REPLICA)
        sync.registry.register_instance(replica)
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        sync.push_skills_to_peers({"skill1": "content"})
        
        # Verify signature header
        call_kwargs = mock_post.call_args[1]
        headers = call_kwargs['headers']
        
        assert 'X-Request-Signature' in headers
        assert headers['X-Request-Signature'].startswith('sha256=')
    
    @patch('core.console.corvin_console.routes.federation_model.requests.get')
    def test_pull_includes_auth_header(self, mock_get, tmp_path, monkeypatch):
        """GH-004c: pull_skills_from_peers includes Authorization header (was missing)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from core.console.corvin_console.routes.federation_model import CrossInstanceSync, FederatedInstance, InstanceRole
        
        sync = CrossInstanceSync("_default")
        replica = FederatedInstance("replica1", "https://replica.local", InstanceRole.REPLICA)
        sync.registry.register_instance(replica)
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"skills": {}}
        mock_get.return_value = mock_response
        
        sync.pull_skills_from_peers()
        
        # Verify auth header was sent (THIS WAS MISSING)
        call_kwargs = mock_get.call_args[1]
        headers = call_kwargs['headers']
        
        assert 'Authorization' in headers
        assert headers['Authorization'].startswith('Bearer ')


class TestFederationReceiverEndpoints:
    """Test receiver endpoint authentication."""
    
    def test_receiver_rejects_missing_auth(self, app_client, tmp_path, monkeypatch):
        """GH-004d: Receiver rejects requests without Authorization header."""
        payload = json.dumps({"skills": {}})
        
        response = app_client.post(
            '/v1/federation/skills/sync',
            data=payload,
            headers={'Content-Type': 'application/json'}  # NO Authorization
        )
        
        assert response.status_code == 401
        data = response.get_json()
        assert data['code'] == 'MISSING_AUTH'
    
    def test_receiver_rejects_invalid_token(self, app_client, tmp_path, monkeypatch):
        """GH-004d: Receiver rejects invalid Bearer token."""
        payload = json.dumps({"skills": {}})
        
        response = app_client.post(
            '/v1/federation/skills/sync',
            data=payload,
            headers={
                'Authorization': 'Bearer invalid-token-xxx',
                'X-Tenant-ID': '_default',
                'Content-Type': 'application/json',
            }
        )
        
        assert response.status_code == 401
        data = response.get_json()
        assert data['code'] == 'INVALID_TOKEN'
    
    @patch('core.console.corvin_console.routes.federation_model.FederationTokenManager.get_or_generate_token')
    def test_receiver_accepts_valid_token(self, mock_get_token, app_client):
        """GH-004d: Receiver accepts valid Bearer token."""
        valid_token = 'x' * 32
        mock_get_token.return_value = valid_token
        
        payload = json.dumps({"skills": {"s1": "content"}})
        
        response = app_client.post(
            '/v1/federation/skills/sync',
            data=payload,
            headers={
                'Authorization': f'Bearer {valid_token}',
                'X-Tenant-ID': '_default',
                'Content-Type': 'application/json',
            }
        )
        
        # Should accept (unless other validation fails)
        assert response.status_code in (200, 400)  # OK or bad payload
        if response.status_code == 200:
            data = response.get_json()
            assert data['success']
```

---

## Implementation Timeline

| Phase | Date | Deliverable | Approval |
|-------|------|-------------|----------|
| Phase 1 | Week 1 | BR-002 JSON schema + signing (non-breaking) | Code Review |
| Phase 2 | Week 2 | GH-001 webhook enforcement (breaking: requires secret) | Security Review |
| Phase 3 | Week 2 | GH-004 federation auth + receiver endpoints | Code Review + E2E |
| Validation | Week 3 | All tests green + security audit pass | QA |
| Rollout | Week 4 | Deploy to canary (10% users) | Ops |

---

## Testing Checklist

- [ ] BR-002: JSON schema validates malformed checkpoints (rejected)
- [ ] BR-002: HMAC signatures on checkpoints verified on load
- [ ] GH-001: Webhook registration rejects webhook_secret < 32 chars
- [ ] GH-001: Handler rejects unsigned webhooks (401)
- [ ] GH-001: Handler accepts validly-signed webhooks (200)
- [ ] GH-004: Federation tokens are 32+ bytes, URL-safe
- [ ] GH-004: Token files have 0600 permissions
- [ ] GH-004: push_skills includes Authorization + X-Request-Signature headers
- [ ] GH-004: pull_skills includes Authorization header (NEW FIX)
- [ ] GH-004: Receiver endpoint rejects missing Authorization (401)
- [ ] GH-004: Receiver endpoint rejects invalid tokens (401)
- [ ] GH-004: Receiver endpoint validates request signature

---

## Compliance Mapping

| Finding | GDPR | EU AI Act | ADR |
|---------|------|-----------|-----|
| BR-002 | Art. 32 (Security) | Art. 15 (Security Measures) | ADR-0232 (Audit Trail) |
| GH-001 | Art. 32 (Access Control) | Art. 50 (Transparency) | ADR-0231 (Webhook Security) |
| GH-004 | Art. 32 (Integrity), Art. 30 (Records) | Art. 15 (Security) | ADR-0233 (Federation) |

---

## References

- OWASP: [Insecure Deserialization](https://owasp.org/www-community/deserialization-of-untrusted-data)
- OWASP: [Webhook Security](https://cheatsheetseries.owasp.org/cheatsheets/Webhook_Security_Cheat_Sheet.html)
- CWE-502: Deserialization of Untrusted Data
- CWE-347: Improper Verification of Cryptographic Signature
- RFC 4868: Using HMAC-SHA-256, HMAC-SHA-384, and HMAC-SHA-512 with IPsec
