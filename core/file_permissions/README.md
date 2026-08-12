# File Permission Hardener — ADR-0295

Fine-grained file-write protection with fail-closed semantics, tenant isolation, and audit trail integration.

## Overview

The FilePermissionManager provides a second layer of access control complementary to L10 Path-Gate. While L10 protects against direct Claude Code tool calls, FilePermissionManager can be used by business logic to enforce fine-grained permission policies at the application level.

**Key Properties:**
- **Fail-closed**: all operations denied by default unless explicitly allowed
- **Tenant-scoped**: keyword-only `tenant_id` parameter enforces GDPR Art. 32 isolation
- **Audit-integrated**: every check logged with decision and reason
- **Security-first**: deny rules take priority over allow rules
- **Thread-safe**: concurrent rule updates protected by RWLock

## Features

### Permission Matrix

Four operation types are supported:
- `PermissionType.READ` — read file contents
- `PermissionType.WRITE` — write/modify file
- `PermissionType.DELETE` — delete file
- `PermissionType.EXECUTE` — execute file

### Default Protected Paths

These paths are protected by default and cannot be written to or deleted:
- `~/.corvin/audit.jsonl` — hash-chained audit log (immutable)
- `~/.config/corvin-voice/secrets.json` — credential vault
- `~/.corvin/license/**` — license state and tokens
- `~/.corvin/instance_*.pem` — Ed25519 instance keys (ADR-0145)
- `~/.corvin/instance_cert.jwt` — instance binding certificate

### Permission Rules

Rules are immutable dataclass objects:

```python
@dataclass(frozen=True)
class PermissionRule:
    path_pattern: str          # glob pattern or absolute path
    permission: PermissionType  # read/write/delete/execute
    allow: bool                # True to allow, False to deny
    inherit: bool = True       # Apply to children recursively
    description: str = ""      # Reason for the rule
```

**Pattern Matching:**
- Direct path: `/exact/path/to/file`
- Single-level wildcard: `/app/*.json`
- Recursive wildcard: `/logs/**/*.log`
- Directory inheritance: `/data/` with `inherit=True` applies to all children

### Deny Priority

Deny rules take priority over allow rules for security:

```python
manager.allow_path("/app/data.json", PermissionType.WRITE)
manager.deny_path("/app/data.json", PermissionType.WRITE)

# Deny wins — PermissionDenied is raised
manager.check_permission("/app/data.json", PermissionType.WRITE)
```

## Usage

### Basic Usage

```python
from core.file_permissions import FilePermissionManager, PermissionType

# Create a manager (one per tenant)
manager = FilePermissionManager(
    tenant_id="my_tenant",  # keyword-only, required
    corvin_home=Path.home() / ".corvin"  # optional
)

# Add a whitelist rule
manager.allow_path(
    "/app/config/*.json",
    PermissionType.WRITE,
    description="allow configuration writes"
)

# Check permission (raises PermissionDenied if not allowed)
try:
    manager.check_permission(
        "/app/config/settings.json",
        PermissionType.WRITE
    )
except PermissionDenied as e:
    print(f"Access denied: {e.reason}")
```

### With Audit Trail

```python
from core.audit.chain import AuditChain

# Wire up the audit chain
audit_chain = AuditChain(Path.home() / ".corvin" / "audit.jsonl")

manager = FilePermissionManager(
    tenant_id="my_tenant",
    audit_logger=audit_chain  # all checks logged
)

# Every check is now logged to the audit chain
manager.check_permission(path, PermissionType.WRITE)
```

### Tenant Management

```python
# Get or create a manager for a tenant
from core.file_permissions.manager import get_permission_manager

mgr = get_permission_manager(
    tenant_id="tenant_a",
    corvin_home=Path.home() / ".corvin"
)

# Managers are cached per tenant
mgr2 = get_permission_manager(tenant_id="tenant_a")
assert mgr is mgr2  # Same instance
```

### Permission Inheritance

```python
# Allow writes to directory and all children
manager.allow_path(
    "/app/src",
    PermissionType.WRITE,
    inherit=True  # applies to /app/src/**, /app/src/subdir/file, etc.
)

# Allow only the exact path (not children)
manager.allow_path(
    "/app/config.json",
    PermissionType.WRITE,
    inherit=False  # only /app/config.json, not /app/config.json.bak
)
```

### Complex Scenarios

```python
# Allow all .json files
manager.allow_path("/app/**/*.json", PermissionType.WRITE)

# Except secrets.json (deny takes priority)
manager.deny_path("/app/secrets.json", PermissionType.WRITE)

# Check configuration file (allowed)
manager.check_permission("/app/config.json", PermissionType.WRITE)  # OK

# Check secrets file (denied)
manager.check_permission("/app/secrets.json", PermissionType.WRITE)  # PermissionDenied
```

## Integration with L10 Path-Gate

L10 Path-Gate and FilePermissionManager are complementary:

| Layer | Scope | Mechanism | Purpose |
|-------|-------|-----------|---------|
| L10 | Claude Code tool calls | Hook that inspects Write/Edit/Bash calls | Prevent direct tampering via Claude's own tool API |
| FilePermissionManager | Business logic | Direct API call in application code | Enforce fine-grained policies at application level |

Both protect audit logs, vaults, and licenses, but at different perimeters:
- L10 catches attempts via Claude's tools (e.g., `Write` tool, `Bash` redirects)
- FilePermissionManager catches attempts via Python code that calls `check_permission()`

Together they provide defense in depth (ADR-0295).

## Audit Trail

Every permission check is logged to the audit chain if an audit logger is provided:

```json
{
  "event_type": "file_permission_check",
  "actor": "tenant:my_tenant",
  "action": "write",
  "resource": "/path/to/file.txt",
  "result": "success|failure",
  "timestamp": "2026-08-12T10:30:45Z",
  "details": {
    "tenant_id": "my_tenant",
    "operation": "write",
    "allowed": true|false,
    "reason": "human-readable reason"
  },
  "prior_hash": "sha256...",
  "self_hash": "sha256..."
}
```

The audit log is hash-chained (GDPR Art. 30) — tampering is detected immediately by `voice-audit verify`.

## Error Handling

```python
from core.file_permissions import PermissionDenied

try:
    manager.check_permission(path, PermissionType.WRITE)
except PermissionDenied as e:
    # e.path — the denied path (string)
    # e.operation — operation type (PermissionType)
    # e.reason — human-readable reason
    print(f"Cannot {e.operation.value} {e.path}: {e.reason}")
```

**PermissionDenied attributes:**
- `path: str` — the file path that was denied
- `operation: PermissionType` — the operation that was attempted
- `reason: str` — human-readable explanation of why it was denied

## Statistics

```python
stats = manager.get_stats()
# {
#     "tenant_id": "my_tenant",
#     "corvin_home": "/home/user/.corvin",
#     "custom_rules": {"allow": 5, "deny": 2, "total": 7},
#     "protected_paths": {"write": 8, "delete": 8, "execute": 0}
# }
```

## Feature Flag

The FilePermissionManager is controlled by a feature flag:

```yaml
# tenant.corvin.yaml
spec:
  features:
    file_permissions_enabled: true  # default: false
```

Or via the Console Settings → Features panel (default OFF).

When the flag is OFF, permission checks still work for code that calls `check_permission()` directly, but the feature is not enabled at the platform level.

## Compliance

**GDPR Art. 32 (Access Control):**
- Keyword-only `tenant_id` parameter enforces tenant isolation
- Every operation logged to hash-chained audit trail
- Fail-closed: default deny unless explicitly allowed

**GDPR Art. 30 (Records of Processing):**
- All permission checks audit-logged
- Hash-chain detects tampering
- Retention enforced by ADR-0319

**GDPR Art. 6 (Lawful Basis):**
- Legitimate interest (L10 Path-Gate protection)
- Fail-closed default-deny model

## Testing

Run unit tests:
```bash
uv run pytest core/file_permissions/tests/test_manager.py -v
```

Run E2E integration tests:
```bash
uv run pytest core/file_permissions/tests/test_e2e_integration.py -v
```

Run all tests:
```bash
uv run pytest core/file_permissions/tests/ -v
```

**Test coverage:**
- 41 unit tests: permission matrix, fail-closed, rules, inheritance, isolation
- 12 E2E tests: audit integration, real files, tenant isolation, L10 composition

## Thread Safety

The FilePermissionManager uses an RWLock (`threading.RLock`) to protect concurrent rule updates:

```python
import threading

# Safe to call from multiple threads
for i in range(100):
    manager.allow_path(f"/app/file_{i}.txt", PermissionType.WRITE)

# Permission checks are also thread-safe
manager.check_permission("/app/file_0.txt", PermissionType.WRITE)
```

## Known Limitations

1. **Pattern matching is static** — glob patterns are evaluated at check time, not stored as regex. This is intentional for security (no eval, no regex DoS).

2. **No runtime revocation** — once a tenant's manager is created, its rule set is permanent. To change rules, restart the process or create a new tenant. This is a feature, not a bug (deny tampering).

3. **Symlinks are resolved** — symlinks are followed to their targets during permission checks. A symlink to a protected file is also protected.

4. **Path normalization** — all paths are resolved to their absolute form, so relative paths, `.`, `..`, and symlinks are normalized before checking.

## See Also

- [L10 Path-Gate documentation](../../../docs/claude-ref/layer-10-path-gate.md) — shell-level write protection
- [ADR-0295](../../../docs/adr/0295-file-permission-hardener.md) — architectural decision record
- [GDPR compliance baseline](../../../docs/claude-ref/compliance-baseline.md) — regulatory requirements
