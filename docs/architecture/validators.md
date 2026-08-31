# Input Validator Factory — ADR-0296

**Status:** Implemented  
**Date:** 2026-08-11

## Overview

The Input Validator Factory is a centralized, pluggable validation system that ensures **all user input is validated before reaching business logic** with **fail-closed** behavior. Every validation decision is tenant-isolated and audit-logged.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Flask Route / CLI Handler / Bridge Message         │
├─────────────────────────────────────────────────────┤
│  Input Validation (ValidatorFactory.validate)       │
│    ├─ Unknown validator? → 400 Bad Request          │
│    ├─ Invalid input? → 400 Bad Request + audit      │
│    └─ Valid? → continue to business logic           │
├─────────────────────────────────────────────────────┤
│  Pipeline (Capability Gate + Audit)                 │
├─────────────────────────────────────────────────────┤
│  Business Logic                                      │
└─────────────────────────────────────────────────────┘
```

## Core Components

### ValidatorFactory

Central registry for all validators (built-in + custom + composite).

```python
from core.validators import FACTORY, validate

# Validate using global factory
result = FACTORY.validate("email", user_email, tenant_id="default")
if not result.is_valid:
    audit_log(action="input_validation_failed", reason=result.error_message)
    return jsonify({"error": result.error_code}), 400

# Or use convenience function
result = validate("email", user_email, tenant_id="default")
```

### ValidationResult

Immutable result type with all validation outcomes.

```python
@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    value: Any = None
    error_message: Optional[str] = None  # Only if is_valid=False
    error_code: Optional[str] = None      # Non-specific (no data leakage)
```

## Built-in Validators

### String Validator

```python
result = validate(
    "string",
    value,
    tenant_id="tenant_id",
    min_length=1,
    max_length=10000,
    pattern=r"^[a-z]+$",  # Optional regex
)
```

### Integer Validator

```python
result = validate(
    "integer",
    value,
    tenant_id="tenant_id",
    min_value=0,
    max_value=100,
)
```

### Email Validator

```python
result = validate("email", user_email, tenant_id="tenant_id")
```

### URL Validator

```python
result = validate(
    "url",
    webhook_url,
    tenant_id="tenant_id",
    allowed_schemes=["https"],  # Optional
)
```

### Peer ID Validator

Used for A2A protocol peer identifiers: alphanumeric + underscore, 1–64 chars.

```python
result = validate("peer_id", instance_id, tenant_id="tenant_id")
```

### Flag ID Validator

Used for feature flag names: lowercase alphanumeric + underscore, 3–48 chars.

```python
result = validate("flag_id", flag_name, tenant_id="tenant_id")
```

### UUID Validator

Validates UUID v4 format.

```python
result = validate("uuid", session_id, tenant_id="tenant_id")
```

## Composite Validators

### AND Validator

All validators must pass.

```python
from core.validators import AndValidator

email_and_string = AndValidator([
    validate_string,
    validate_email,
])
FACTORY.register_composite("email_confirmed", email_and_string)

result = FACTORY.validate("email_confirmed", value, tenant_id="tenant_id")
```

### OR Validator

At least one validator must pass.

```python
from core.validators import OrValidator

phone_or_email = OrValidator([
    validate_email,
    lambda v, *, tenant_id: validate_string(v, tenant_id=tenant_id, pattern=r"^\+?[0-9]{10,}$"),
])
FACTORY.register_composite("contact_info", phone_or_email)

result = FACTORY.validate("contact_info", value, tenant_id="tenant_id")
```

### NOT Validator

Negates validator result.

```python
from core.validators import NotValidator

not_admin = NotValidator(lambda v, *, tenant_id: validate_string(v, tenant_id=tenant_id) if v == "admin" else ValidationResult(is_valid=False, error_message="", error_code=""))
FACTORY.register_composite("non_admin_id", not_admin)
```

## Custom Validators

Register custom validators for domain-specific logic.

```python
from core.validators import FACTORY, ValidationResult

def validate_username(
    value: Any,
    *,
    tenant_id: str,
    reserved_names: list[str] = None,
) -> ValidationResult:
    """Custom username validator."""
    if not isinstance(value, str):
        return ValidationResult(
            is_valid=False,
            error_message="Username must be string",
            error_code="invalid_type",
        )
    
    if reserved_names and value in reserved_names:
        return ValidationResult(
            is_valid=False,
            error_message="Username is reserved",
            error_code="reserved_name",
        )
    
    if not re.match(r"^[a-z0-9_]{3,32}$", value):
        return ValidationResult(
            is_valid=False,
            error_message="Username format invalid",
            error_code="invalid_format",
        )
    
    return ValidationResult(is_valid=True, value=value)

# Register
FACTORY.register("username", validate_username)

# Use
result = FACTORY.validate(
    "username",
    username,
    tenant_id="default",
    reserved_names=["admin", "root"],
)
```

## Flask Integration Example

```python
from flask import request, jsonify
from core.validators import validate
from core.decorators import requires_auth_capability

@bp.route("/api/webhook", methods=["POST"])
@requires_auth_capability("write_webhook")
def create_webhook():
    # Validate URL
    url = request.json.get("url")
    result = validate("url", url, tenant_id=g.tenant_id)
    if not result.is_valid:
        return jsonify({"error": result.error_code}), 400
    
    # Validate name
    name = request.json.get("name")
    result = validate("string", name, tenant_id=g.tenant_id, max_length=100)
    if not result.is_valid:
        return jsonify({"error": result.error_code}), 400
    
    # Proceed with business logic
    return create_webhook_impl(url, name)
```

## Tenant Isolation

All validators enforce tenant isolation via **keyword-only `tenant_id` parameter**:

```python
# This works
result = validate("email", value, tenant_id="tenant_1")

# This fails (tenant_id must be keyword)
# result = validate("email", value, "tenant_1")  # TypeError
```

This ensures:
- Every validation is tied to a specific tenant (GDPR Art. 5)
- No cross-tenant data leakage
- Audit logs capture tenant context

## Fail-Closed Behavior

The factory **always fails closed** — invalid input never passes silently:

```python
# Unknown validator → rejects
result = validate("unknown_type", value, tenant_id="default")
# → is_valid=False, error_code="unknown_validator"

# Invalid input → rejects
result = validate("email", "not-an-email", tenant_id="default")
# → is_valid=False, error_code="invalid_email"

# Validation error → rejects (never raises)
result = validate("string", value, tenant_id="default", pattern=r"[invalid(")
# → is_valid=False, error_code="validation_error"
```

## Error Messages (Security)

Error messages **never leak input data**:

```python
result = validate("email", "user@INVALID", tenant_id="default")
print(result.error_message)
# "Invalid email format"  ✓ Generic, safe

print(result.error_code)
# "invalid_email"  ✓ Non-specific code
```

## Feature Flag

The validator factory ships **dark by default** (flag: `validator_factory_enabled`):

```yaml
# tenant.corvin.yaml
spec:
  features:
    validator_factory_enabled: false  # Off by default
```

Toggle in Console → Settings → Features once operator opts in.

## Testing

### Unit Tests (72 tests)

- `tests/test_validator_factory.py` — validator behavior, composition, registration

### E2E Integration Tests (15 tests)

- `tests/test_validator_factory_e2e.py` — feature flag, pipeline, real use cases

Run all tests:

```bash
uv run pytest tests/test_validator_factory*.py -v
```

## Performance

- **Validation cost:** < 1ms per validation (built-in validators)
- **Registry lookup:** O(1) hash table
- **Memory:** ~5KB for factory + registry

## Audit Logging

All validation failures are audit-logged:

```python
from core.pipeline import DualGatePipeline

if not result.is_valid:
    pipeline.record_audit(
        event_type="input_validation_failed",
        actor=user_id,
        action="create_webhook",
        resource=webhook_id,
        result="failure",
        tenant_id=tenant_id,
        details={"reason": result.error_code},
    )
    return jsonify({"error": result.error_code}), 400
```

## Migration Path

1. **Phase 1 (now):** Factory shipped dark, ready for opt-in
2. **Phase 2:** Integrate into high-risk entry points (auth, config, delegation)
3. **Phase 3:** Expand to all entry points
4. **Phase 4:** Consider making default-on (after operator feedback)

## References

- **ADR:** ADR-0296 (Input Validator Factory)
- **Depends on:** ADR-0300 (Dual-Gate Pipeline), ADR-0302 (Persona Capability Axis)
- **Related:** ADR-0294 (Auth Decorator Layer), ADR-0297 (Audit Chain)
- **Compliance:** GDPR Art. 5, 6, 32 (data integrity, consent, security)
