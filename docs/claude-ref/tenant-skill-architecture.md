# Tenant-Skill-Architecture Reference (ADR-0114, ADR-0174)

## Overview

The Tenant-Skill-Architecture provides:

1. **Tenant-scoped execution context** — Skills execute in isolation, with all data bound to a specific tenant
2. **Tenant data isolation** — No cross-tenant leakage; fail-closed security model
3. **Skill versioning + immutable rollback** — Full version history with atomic, reversible rollbacks
4. **Persistent state per tenant** — Skill state is persisted, chained, and auditable

## Architecture

### Core Components

#### 1. TenantSkillExecutionContext

Immutable context for a single skill execution.

```python
context = TenantSkillExecutionContext(
    tenant_id="_default",           # Validated tenant ID
    skill_id="os.router",           # Skill identifier
    version="1.0.0",                # Active version
    execution_id="exec-123",        # Unique execution ID
    task_id="task-456",             # Parent task ID
    input_data={"request": "..."},  # Input to skill
)

# Verify isolation (fail-closed)
if not context.verify_isolation():
    raise RuntimeError("Context tampered")

# Mark success/failure
context.mark_success(output_data={"decision": "..."})
context.mark_failure(error="Timeout exceeded")

# Generate audit event (GDPR Art. 30)
event = context.to_audit_event()
```

**Invariants:**
- Context is immutable after creation (except for explicit mark_success/mark_failure)
- Isolation hash prevents tampering
- All state changes are auditable
- Fail-closed on any isolation violation

#### 2. TenantSkillVersionManager

Manages skill version history and rollback per tenant.

```python
# Create manager (one per skill per tenant)
version_mgr = TenantSkillVersionManager(
    tenant_id="_default",
    skill_id="os.router",
)

# Register new version
contract = SkillContract(...)
config = {"threshold": 0.7}
success, msg = version_mgr.register_version(
    version="1.2.3",
    contract=contract,
    config_data=config,
    created_by="system",
)

# Rollback to previous version (atomic)
success, msg = version_mgr.rollback_to_version(
    target_version="1.0.0",
    reason="Bug in 1.2.3",
)

# Get active version
active = version_mgr.get_active_version()

# List all versions
versions = version_mgr.list_versions()
```

**Version States:**
- `ACTIVE` — Currently in use
- `ROLLBACK_READY` — Available for rollback
- `DEPRECATED` — No longer used
- `ARCHIVED` — Read-only, not deployable

**Invariants:**
- Only ONE version can be ACTIVE per skill per tenant
- Rollback is atomic (all-or-nothing)
- Version history is immutable (append-only)
- Rollback checksum verifies data integrity
- Previous version automatically transitions to ROLLBACK_READY

#### 3. TenantSkillStateManager

Manages persistent skill state per tenant.

```python
# Create manager (one per skill per tenant)
state_mgr = TenantSkillStateManager(
    tenant_id="_default",
    skill_id="os.router",
)

# Save immutable state snapshot
state = state_mgr.save_state(
    version="1.0.0",
    state_data={
        "learned_threshold": 0.75,
        "confidence": 0.95,
    },
)

# Get current state
current = state_mgr.get_current_state()

# Recover state at specific version
state_v1 = state_mgr.get_state_at_version("1.0.0")

# Get full history (immutable chain)
history = state_mgr.get_state_history()

# Verify chain integrity (GDPR Art. 32)
success, msg = state_mgr.verify_chain_integrity()
```

**State Chain Properties:**
- Immutable (new snapshots, never mutations)
- Hash-chained for integrity verification
- One state per save (no updates, only appends)
- Per-tenant isolation (validate on access)
- Recoverable from audit trail

#### 4. TenantSkillArchitecture

Main orchestrator for tenant-scoped skill management.

```python
# Create architecture instance for a tenant
arch = TenantSkillArchitecture("_default")

# Register skill version
arch.register_skill_version(
    skill_id="os.router",
    version="1.0.0",
    contract=contract,
    config_data={},
    created_by="system",
)

# Create execution context
context = arch.create_execution_context(
    skill_id="os.router",
    task_id="task-123",
    input_data={"request": "classify"},
)

# Execute skill with timeout and isolation
success, output, event = await arch.execute_skill(
    context=context,
    skill_fn=async_skill_function,
    timeout_seconds=30.0,
)

# Rollback to previous version
success, msg = arch.rollback_skill_version(
    skill_id="os.router",
    target_version="1.0.0",
    reason="Critical bug",
)

# Verify tenant isolation
success, msg = arch.verify_isolation()
```

## Tenant Isolation (GDPR Art. 5, 6, 32)

### Isolation Boundaries

1. **Execution Context**
   - Each context is bound to exactly one tenant
   - Isolation hash prevents tampering (fail-closed)
   - Version/state managers are per-tenant instances

2. **Persistent State**
   - State stored in `~/.corvin/tenants/<tenant_id>/skill-forge/skills/<skill_id>/`
   - No cross-tenant path construction
   - All paths validated with `validate_tenant_id()` (fail-closed)

3. **Version Management**
   - Version history per tenant+skill combination
   - No global version registry (prevents cross-tenant access)
   - Rollback is scoped to tenant

4. **Manager Instances**
   - TenantSkillArchitecture.get_version_manager() creates per-tenant managers
   - TenantSkillArchitecture.get_state_manager() creates per-tenant managers
   - Cross-tenant swapping detected and prevented

### Fail-Closed Security

Every operation fails closed on isolation violations:

```python
# Validation: fail if tenant_id is invalid
validate_tenant_id(tenant_id)  # Raises ValueError on invalid

# Context verification: fail if tampered
if not context.verify_isolation():
    raise RuntimeError(f"Context tampered (tenant={tenant_id})")

# Manager isolation: fail if swapped
for skill_id, mgr in self._version_managers.items():
    if mgr.tenant_id != self.tenant_id:
        raise RuntimeError(f"Manager isolation violation for {skill_id}")

# State chain: fail if hash doesn't match
if state.previous_state_hash != previous.state_hash:
    return False, "State chain broken"
```

## Version Management & Rollback

### Versioning Workflow

1. **Register New Version**
   ```python
   arch.register_skill_version(
       skill_id="os.router",
       version="1.1.0",
       contract=contract,
       config_data=config,
       created_by="system",
   )
   ```
   - New version becomes ACTIVE
   - Previous active version becomes ROLLBACK_READY
   - Checksum computed for integrity verification

2. **Execute with Active Version**
   - Execution context automatically uses active version
   - No manual version selection required

3. **Rollback to Previous Version**
   ```python
   arch.rollback_skill_version(
       skill_id="os.router",
       target_version="1.0.0",
       reason="Critical bug in 1.1.0",
   )
   ```
   - Atomic operation (all or nothing)
   - Checksum verified before rollback
   - Previous version marked DEPRECATED

### Immutability & Atomicity

- Version registration is atomic (all fields written together)
- Rollback is atomic (version changed, history updated, state saved)
- History is append-only (never deleted or reordered)
- Checksum protects against data corruption

## Persistent State

### State Snapshots

Each skill maintains an immutable chain of state snapshots:

```
State Chain:
v1.0.0: counter=1, hash=abc123, prev_hash=null
v1.0.0: counter=2, hash=def456, prev_hash=abc123
v1.0.1: counter=3, hash=ghi789, prev_hash=def456
```

### Hash-Chaining

Each state snapshot includes:
- `state_hash` — SHA256 of current state
- `previous_state_hash` — SHA256 of previous state

Enables integrity verification:
```python
success, msg = state_mgr.verify_chain_integrity()
# Checks that each state.previous_state_hash == previous_state.state_hash
```

### Recovery from Version Rollback

When rolling back skill version:
1. State manager keeps all snapshots (never deleted)
2. New version uses state from its last execution
3. Full history available for recovery

```python
# Recover state from a specific version
state = state_mgr.get_state_at_version("1.0.0")
# Returns most recent state for that version
```

## Audit Trail Integration

### Audit Events

Each skill execution generates an audit event:

```json
{
  "type": "skill_executed",
  "tenant_id": "_default",
  "skill_id": "os.router",
  "version": "1.0.0",
  "execution_id": "exec-123",
  "task_id": "task-456",
  "status": "success",
  "started_at": "2026-09-19T12:00:00Z",
  "completed_at": "2026-09-19T12:00:00.042Z",
  "duration_ms": 42,
  "input_hash": "sha256(...)",
  "output_hash": "sha256(...)"
}
```

**GDPR Compliance (Art. 30, 32):**
- All events hash-chained in tenant audit.jsonl
- No PII in events (hashes only)
- Events are immutable (append-only)
- Separate event per execution (no aggregation)

### Accessing Audit Trail

```python
# From execution context
event = context.to_audit_event()

# Write to audit chain (external)
from core.skills.skill_audit import emit_skill_audit
emit_skill_audit(tenant_id, event)
```

## Compliance Notes

### GDPR Art. 5 (Integrity)

- Tenant isolation is fail-closed
- Validation on every tenant_id access
- Path traversal prevention (regex validation)

### GDPR Art. 6 (Lawfulness)

- Execution context captures consent context
- Every execution auditable (for consent verification)
- Learning signals properly scoped to tenant

### GDPR Art. 30, 32 (Records & Security)

- Audit events hash-chained per tenant
- State chain integrity verifiable
- Version history immutable and auditable
- No silent state mutations

## Error Handling

### Isolation Violations

```python
# Fail-closed on tampered context
if not context.verify_isolation():
    raise RuntimeError(f"Execution context tampered (tenant={self.tenant_id})")

# Fail-closed on cross-tenant access
if mgr.tenant_id != self.tenant_id:
    raise RuntimeError(f"Manager isolation violation for {skill_id}")
```

### Version Rollback Failures

```python
success, msg = arch.rollback_skill_version(skill_id, version)

# Common reasons for failure:
# - "Version not found"
# - "Version is not rollbackable (state=archived)"
# - "Rollback checksum mismatch for X (data corruption)"
```

### State Chain Corruption

```python
success, msg = state_mgr.verify_chain_integrity()

# Failure indicates:
# - State chain broken (previous_hash mismatch)
# - Data corruption (checksum failed)
# - Manual tampering detected
```

## Best Practices

### 1. Always Validate Tenant ID

```python
from core.tenants import validate_tenant_id

try:
    tenant_id = validate_tenant_id(user_input)
except ValueError as e:
    # Reject untrusted input
    return error_response(f"Invalid tenant: {e}")
```

### 2. Use Architecture as Single Entry Point

```python
# Good: Use architecture
arch = TenantSkillArchitecture(tenant_id)
context = arch.create_execution_context(...)

# Bad: Direct manager access
mgr = TenantSkillVersionManager(...)  # Bypasses isolation checks
```

### 3. Always Verify Isolation

```python
success, msg = arch.verify_isolation()
if not success:
    log.error(f"Tenant isolation broken: {msg}")
    raise RuntimeError("Isolation violation")
```

### 4. Check Rollback Success

```python
success, msg = arch.rollback_skill_version(...)
if not success:
    log.error(f"Rollback failed: {msg}")
    # Handle failure (alert operator, fall back to previous version)
```

### 5. Verify State Chain Integrity Regularly

```python
# Periodic check (e.g., daily)
success, msg = state_mgr.verify_chain_integrity()
if not success:
    # Log integrity violation
    alert_operator(f"State chain corrupted: {msg}")
```

## Storage Layout

```
~/.corvin/tenants/<tenant_id>/
├── skill-forge/
│   └── skills/
│       ├── os.router/
│       │   ├── versions.json      # Version history (immutable append-only)
│       │   └── state.jsonl        # State snapshots (immutable JSONL chain)
│       └── skill.id/
│           ├── versions.json
│           └── state.jsonl
└── global/
    └── audit.jsonl                # Tenant audit trail (hash-chained)
```

## Testing

### Unit Tests

```bash
pytest tests/skills/test_tenant_skill_architecture.py -v
```

Key test classes:
- `TestTenantValidation` — Tenant ID validation
- `TestExecutionContext` — Context isolation and tampering
- `TestVersionManager` — Version registration and rollback
- `TestStateManager` — State persistence and chaining
- `TestTenantSkillArchitecture` — Main architecture
- `TestTenantIsolationSecurity` — Cross-tenant prevention
- `TestVersionRollbackImmutability` — Atomic rollback

### Integration Tests

```bash
pytest tests/skills/test_tenant_skill_e2e.py -v
```

Covers:
- Multi-tenant execution (same skill, different tenants)
- State isolation (tenant A's state not visible to tenant B)
- Version rollback under load
- Audit trail completeness

## Related ADRs

- **ADR-0114** — Tenant-scoped skill execution (this architecture)
- **ADR-0174** — Skill versioning and rollback
- **ADR-0007** — Multi-tenant axis (tenant path resolution)
- **ADR-0232** — Audit chain integrity (boot tripwire)
- **ADR-0250** — Tenant scope boundaries
