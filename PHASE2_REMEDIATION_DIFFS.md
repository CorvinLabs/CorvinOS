# Phase 2 Security Remediation — Code Diffs

**Document:** Concrete code changes for existing files  
**Status:** Ready to apply

---

## File 1: `core/context_engineering/session_checkpoint.py`

### Change: Add Schema Validation on Load

**Location:** In `load_checkpoint()` method, after checkpoint is loaded from JSON

```python
# BEFORE (lines 257-259):
with open(latest_path) as f:
    checkpoint = SessionCheckpoint.from_json(f.read())

# AFTER:
from .checkpoint_schema import validate_checkpoint_json, CheckpointValidationError
with open(latest_path) as f:
    json_str = f.read()
    try:
        # Validate JSON against schema (fail-closed)
        validate_checkpoint_json(json_str)
        checkpoint = SessionCheckpoint.from_json(json_str)
    except CheckpointValidationError as e:
        raise CheckpointNotFoundError(
            f"Checkpoint validation failed: {e}"
        ) from e
```

**For history loading (lines 268-273):**

```python
# BEFORE:
with open(history_path) as f:
    for line in f:
        cp = SessionCheckpoint.from_json(line)
        if cp.checkpoint_id == checkpoint_id:
            checkpoint = cp
            break

# AFTER:
from .checkpoint_schema import validate_checkpoint_json, CheckpointValidationError
with open(history_path) as f:
    for line in f:
        try:
            validate_checkpoint_json(line)  # Validate before deserializing
            cp = SessionCheckpoint.from_json(line)
            if cp.checkpoint_id == checkpoint_id:
                checkpoint = cp
                break
        except CheckpointValidationError as e:
            logger.warning(f"Skipping invalid checkpoint entry: {e}")
            continue
```

### Effort: 15 minutes

---

## File 2: `core/console/corvin_console/routes/github_webhooks.py`

### Change 1: Require Secret on Registration (GH-001a)

**Location:** In `register_webhook()` function, after parsing JSON

```python
# BEFORE (line 213):
data = request.get_json() or {}
token = data.get('token', '')
webhook_secret = data.get('webhook_secret', '')

# AFTER:
data = request.get_json() or {}
token = data.get('token', '')
webhook_secret = data.get('webhook_secret', '').strip()

# GH-001a: FAIL-CLOSED — require secret (32+ chars)
MIN_SECRET_LENGTH = 32
if not webhook_secret or len(webhook_secret) < MIN_SECRET_LENGTH:
    logger.warning(
        f'Webhook registration rejected: secret missing or too short '
        f'(min_length={MIN_SECRET_LENGTH})'
    )
    return jsonify({
        'success': False,
        'error': f'webhook_secret is required (minimum {MIN_SECRET_LENGTH} characters)',
        'min_length': MIN_SECRET_LENGTH,
    }), 400
```

### Change 2: Return Secret Fingerprint (GH-001b)

**Location:** In `register_webhook()` function, after successful registration

```python
# BEFORE (lines 275-282):
return jsonify({
    'success': True,
    'webhook_id': webhook_info.get('id'),
    'url': webhook_data['config']['url'],
    'events': webhook_data['events'],
    'active': webhook_info.get('active'),
    'timestamp': datetime.utcnow().isoformat(),
}), 200

# AFTER:
import hashlib

# Generate fingerprint to confirm secret was stored
secret_fingerprint = (
    'sha256=' + hashlib.sha256(webhook_secret.encode()).hexdigest()[:16]
)

# Add metadata to config
github_cfg['webhook_secret_set_at'] = datetime.utcnow().isoformat()

return jsonify({
    'success': True,
    'webhook_id': webhook_info.get('id'),
    'secret_fingerprint': secret_fingerprint,  # ← NEW: confirm secret
    'url': webhook_data['config']['url'],
    'events': webhook_data['events'],
    'active': webhook_info.get('active'),
    'message': 'Webhook registered successfully. Secret is required for all incoming webhooks.',
    'timestamp': datetime.utcnow().isoformat(),
}), 200
```

### Change 3: Enforce Signature Verification (GH-001c)

**Location:** In `handle_github_webhook()` function, before signature check

```python
# BEFORE (lines 89-103):
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

# AFTER (GH-001c: FAIL-CLOSED):
signature = request.headers.get('X-Hub-Signature-256', '')
secret = get_webhook_secret()

# Fail-closed: ALWAYS verify if secret is configured
if not secret:
    # No secret = webhook not properly registered
    logger.warning(
        f'Webhook received without configured secret '
        f'(remote_ip={request.remote_addr})'
    )
    return jsonify({
        'success': False,
        'error': 'Webhook not configured. Register via /api/console/github/webhook/register',
        'code': 'WEBHOOK_NOT_REGISTERED',
    }), 403  # Changed from 401 to 403 (Forbidden vs Unauthorized)

# Verify signature (fail-closed)
if not verify_webhook_signature(payload_body, signature, secret):
    logger.warning(
        f'Invalid webhook signature received '
        f'(remote_ip={request.remote_addr})'
    )
    # Optional: audit log security event (ADR-0232)
    return jsonify({
        'success': False,
        'error': 'Invalid signature',
        'code': 'SIGNATURE_VERIFICATION_FAILED',
    }), 401
```

### Effort: 30 minutes

---

## File 3: `core/console/corvin_console/routes/federation_model.py`

### Change 1: Use Secure Token Manager (GH-004a)

**Location:** In `CrossInstanceSync.__init__()` method

```python
# BEFORE (lines 273-288):
def __init__(self, tenant_id: str = "_default", federation_token: str = ""):
    self.tenant_id = tenant_id
    self.federation_token = federation_token or self._generate_token()
    self.registry = FederationRegistry(tenant_id)
    self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id

def _generate_token(self) -> str:
    """Generate or load federation token."""
    # In production: load from secure storage
    token_file = self.tenant_path / 'federation-token.secure'
    if token_file.exists():
        return token_file.read_text().strip()
    # Fallback: generate (should be replaced with real auth)
    import secrets
    token = secrets.token_urlsafe(32)
    return token

# AFTER (GH-004a: Use FederationTokenManager):
def __init__(self, tenant_id: str = "_default"):
    self.tenant_id = tenant_id
    self.registry = FederationRegistry(tenant_id)
    self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id
    
    # GH-004a: Use secure token manager
    from .federation_token_manager import FederationTokenManager
    self.token_manager = FederationTokenManager(tenant_id)

@property
def federation_token(self) -> str:
    """Get current federation token (ensures valid)."""
    token = self.token_manager.get_or_generate_token()
    
    # Check if token is expired
    if self.token_manager.is_token_expired():
        logger.warning("Federation token expired, rotating")
        token = self.token_manager.rotate_token()
    
    return token
```

**Remove the old _generate_token() method entirely.**

### Change 2: Sign Push Requests (GH-004b)

**Location:** In `push_skills_to_peers()` method, update headers

```python
# BEFORE (lines 304-309):
headers = {
    "Authorization": f"Bearer {self.federation_token}",
    "X-Tenant-ID": self.tenant_id,
    "X-Source-Instance": "local",
    "Content-Type": "application/json"
}

# AFTER (GH-004b: Add request signature):
import hmac
import hashlib

# Sign the request (deterministic JSON for reproducible signature)
payload_json = json.dumps(
    {
        "tenant_id": self.tenant_id,
        "skills": skills,
        "source_instance": "local",
        "timestamp": datetime.utcnow().isoformat(),
    },
    sort_keys=True
)

request_signature = hmac.new(
    self.federation_token.encode(),
    payload_json.encode(),
    hashlib.sha256
).hexdigest()

headers = {
    "Authorization": f"Bearer {self.federation_token}",
    "X-Request-Signature": f"sha256={request_signature}",  # ← NEW
    "X-Tenant-ID": self.tenant_id,
    "X-Source-Instance": "local",
    "Content-Type": "application/json",
    "User-Agent": "Corvin/0.2-rc1",
}

# Update POST call to use payload_json
response = requests.post(
    f"{replica.url}/v1/federation/skills/sync",
    data=payload_json,  # Changed from json=
    headers=headers,
    timeout=10,
    verify=True,  # ← Add explicit HTTPS verification
)
```

### Change 3: Add Auth to Pull Requests (GH-004c)

**Location:** In `pull_skills_from_peers()` method, add headers

```python
# BEFORE (lines 334-345):
def pull_skills_from_peers(self) -> Dict[str, Any]:
    """Pull skill updates from peer instances."""
    
    # Collect latest skills from all healthy replicas
    # Apply merge strategy (quorum-based or eventual consistency)
    
    return {
        "pulled_from": [],
        "conflicts": [],
        "merged_skills": {},
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

# AFTER (GH-004c: Actually implement pull with auth):
def pull_skills_from_peers(self) -> Dict[str, Any]:
    """Pull skill updates from peer instances with authentication.
    
    FIX GH-004c: Add auth headers to pull requests (was missing).
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
            # GH-004c: Add auth headers (NEW)
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

### Effort: 45 minutes

---

## File 4: Update Flask App to Register Federation Receiver

**Location:** In the Flask app initialization (e.g., `app.py` or main app file)

```python
# Add federation receiver endpoints
from core.console.corvin_console.routes.federation_receiver import federation_receiver_bp

app.register_blueprint(federation_receiver_bp)
```

### Effort: 5 minutes

---

## Summary of Changes

| File | Changes | Tests | Effort |
|------|---------|-------|--------|
| session_checkpoint.py | Add schema validation on load | 3 cases | 15 min |
| github_webhooks.py | Require secret, enforce verification, return fingerprint | 5 cases | 30 min |
| federation_model.py | Use token manager, sign push, auth pull | 6 cases | 45 min |
| app.py | Register federation receiver | N/A | 5 min |
| NEW: checkpoint_schema.py | Full file (PROVIDED) | 10+ cases | - |
| NEW: federation_token_manager.py | Full file (PROVIDED) | 8+ cases | - |
| NEW: federation_receiver.py | Full file (PROVIDED) | 8+ cases | - |

**Total Effort:** ~2 hours code changes + 3 hours testing + 2 hours validation = **7 hours**

---

## Testing Checklist

### BR-002 (Checkpoint Schema)
- [ ] Invalid JSON rejected (CheckpointValidationError)
- [ ] Unknown fields rejected
- [ ] Type mismatches rejected
- [ ] Size violations rejected
- [ ] Valid checkpoint accepted
- [ ] History loading skips invalid entries (logs warning)

### GH-001 (Webhook Security)
- [ ] Registration without secret → 400 (error message shows min length)
- [ ] Registration with short secret → 400
- [ ] Webhook without configured secret → 403
- [ ] Webhook with no signature header → 401
- [ ] Webhook with invalid signature → 401
- [ ] Webhook with valid signature → 200 (accepted)
- [ ] Response includes secret fingerprint (for operator verification)

### GH-004 (Federation Auth)
- [ ] Token manager generates 32+ byte tokens
- [ ] Token file has 0600 permissions
- [ ] Valid token is reused (not regenerated)
- [ ] push_skills includes Authorization header
- [ ] push_skills includes X-Request-Signature header
- [ ] pull_skills includes Authorization header (NEW)
- [ ] Receiver rejects missing Authorization → 401
- [ ] Receiver rejects invalid token → 401
- [ ] Receiver rejects invalid signature → 401
- [ ] Receiver accepts valid auth → 200
- [ ] Receiver validates JSON schema
- [ ] Receiver logs security events

---

## Rollback Plan

If issues arise during rollout:

### BR-002 Rollback
- Remove schema validation call (keep imports)
- Checkpoints continue to work without validation
- No data loss

### GH-001 Rollback
- Revert to optional secret on registration
- Webhook signature verification becomes optional (not recommended)
- **Not recommended** — keep the fix in place

### GH-004 Rollback
- Revert federation_model.py changes
- Federation sync will fail (no token)
- Operator must provide federation_token parameter
- Receiver endpoints return 401 until reverted

**Recommendation:** Use feature flags for GH-001 and GH-004 to allow gradual rollout without full rollback.

---

## Deployment Checklist

- [ ] Code review: All three findings' code changes reviewed
- [ ] Unit tests: All tests in provided test files pass
- [ ] Integration tests: End-to-end federation sync tested
- [ ] Security audit: OWASP checklist passed
- [ ] Performance tests: No latency regression
- [ ] Compliance review: GDPR + EU AI Act verified
- [ ] Documentation: README updated with new security requirements
- [ ] Migration guide: Existing webhook configurations documented
- [ ] Canary rollout: 10% traffic for 48 hours
- [ ] Full rollout: 100% traffic after canary validation
