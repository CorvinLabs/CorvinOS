# Plugin Infrastructure Architecture

## System Design

CorvinOS plugin infrastructure implements a **defense-in-depth security model** with **audit-first design** and **graceful degradation** for fault tolerance.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Plugin Bootstrap (bootstrap.py)                                      │
│ ┌────────────────────────────────────────────────────────────────┐  │
│ │ 1. Load plugin from manifest                                   │  │
│ │ 2. Create PluginAuditLogger (audit_logging.py)                │  │
│ │ 3. Create PluginSecurityPolicy & gate (security.py)          │  │
│ │ 4. Create PluginCallGuard (error_handling.py)                │  │
│ │ 5. Wrap plugin.on_load() with guard                          │  │
│ │ 6. Register in provider registry                              │  │
│ │ 7. Record metrics (metrics.py)                                │  │
│ └────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Plugin Runtime (plugin execution)                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Caller → PluginCallGuard.call()                                   │
│           ├─ Security Gate check (capability, file, network)       │
│           │  └─ Raise PermissionDeniedError or SandboxViolation   │
│           │     (audit trail + metrics)                            │
│           ├─ Execute plugin.method() with timeout                 │
│           ├─ On success:                                           │
│           │  ├─ Audit event (PluginAuditLogger.log_executed)      │
│           │  ├─ Record metrics (record_execution, success=True)    │
│           │  └─ Return result                                      │
│           ├─ On exception:                                         │
│           │  ├─ Classify error (ErrorSeverity)                    │
│           │  ├─ Audit event (PluginAuditLogger.log_execution_failed)
│           │  ├─ Record metrics (record_execution, success=False)   │
│           │  ├─ Call error callback (on_error)                    │
│           │  └─ Return fallback (never raises)                     │
│           └─ Circuit breaker: track consecutive failures           │
│              └─ If threshold hit: open circuit (fail-fast)         │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Audit Trail (audit.py - hash-chained core)                         │
├─────────────────────────────────────────────────────────────────────┤
│ Every event appended (immutable, tenant-scoped):                   │
│                                                                      │
│ {                                                                    │
│   "timestamp": "2026-09-19T12:00:00Z",                            │
│   "event_type": "plugin.executed",                                 │
│   "plugin_id": "audit_backend",                                   │
│   "tenant_id": "default",                                          │
│   "capability": "audit_write",                                     │
│   "duration_ms": 5.0,                                              │
│   "status": "success",                                             │
│   "lom": "plugin.py:42",                                           │
│   "hash": "sha256(...)",                                           │
│   "prev_hash": "sha256(...)",                                      │
│   "audit_cert": "RFC-3161 timestamp"                              │
│ }                                                                    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Metrics Store (metrics.py - in-memory)                              │
├─────────────────────────────────────────────────────────────────────┤
│ Per-plugin aggregation:                                            │
│  - call_count, success_count, failure_count                        │
│  - latency: min, max, avg                                          │
│  - error types (last_error_type, last_error_at)                    │
│  - health checks (ok, failures)                                    │
│  - circuit breaker state                                           │
│  - security violations                                             │
│                                                                      │
│ Export: Prometheus text format + JSON API                         │
└─────────────────────────────────────────────────────────────────────┘
```

## Layered Security Model

### Layer 1: Capability-Based Access Control

Plugins declare required capabilities at load time; runtime checks enforce them.

```
Policy (immutable at load)
│
├─ CORE_PLUGIN_API: access to registry, context
├─ DATA_READ_USER_CONTEXT: read user session data
├─ DATA_WRITE_STORAGE: write to plugin storage
├─ SYSTEM_FILE_READ/WRITE: file operations
├─ SYSTEM_NETWORK: external network access
├─ AUDIT_READ/WRITE: audit trail access
├─ ADMIN_*: administrative operations
└─ INTEGRATION_*: third-party integrations
```

**Enforcement**:
```python
gate.check_capability("audit_write")  # Raises PermissionDeniedError if denied
```

### Layer 2: Resource Constraints

Hard limits on plugin resource consumption.

```
Per-Plugin Limits
├─ max_file_read_size: e.g., 10 MB
├─ max_file_write_size: e.g., 50 MB
├─ max_subprocess_runtime: e.g., 30s
├─ max_memory_mb: e.g., 256 MB
└─ allowed_domains: network allowlist

Path Constraints
├─ allowed_read_paths: glob patterns
└─ allowed_write_paths: glob patterns
```

**Enforcement**:
```python
gate.check_file_write(file_path, file_size)  # Raises SandboxViolationError if exceeded
gate.check_network_access(domain)            # Raises SandboxViolationError if denied
```

### Layer 3: Execution Isolation

Errors caught at boundary; never escape to caller.

```
try:
    result = plugin.method()
    audit_event("plugin.executed", ...)
except Exception as exc:
    audit_event("plugin.execution_failed", ...)
    return fallback
```

### Layer 4: Circuit Breaker

Automatic fail-fast after consecutive failures.

```
Closed ──[3 failures]──> Open ──[30s cooldown]──> Half-Open
 │                                                   │
 └──[success]────────────────────────────────────────┘
                                  (probe succeeds)
```

## Audit Trail Design

### Immutability

Events are frozen dataclasses; cannot be modified after creation.

```python
@dataclass(frozen=True)
class PluginAuditEvent:
    event_type: str
    plugin_id: str
    tenant_id: str
    timestamp: float
    version: str
    lom: str  # Line of Moral Responsibility
    # ... other fields
```

### Tenant Isolation

Every event includes `tenant_id`; queries are filtered by it.

```python
# Audit query
events = audit_store.query(
    tenant_id="default",
    event_type="plugin.executed",
)
# Returns only events for tenant "default"
```

### Hash-Chaining

Each event is cryptographically linked to the previous one.

```
Event N-1: hash=abc123
Event N:   event_data, prev_hash=abc123, hash=def456
Event N+1: event_data, prev_hash=def456, hash=ghi789
```

Verification:
```bash
# Verify chain integrity
python3 scripts/verify_audit_chain.py --tenant=_default
# Output: ✅ Chain height 1000, all hashes verified, 0 gaps
```

### Line of Moral Responsibility (LoM)

Every event includes the call site that triggered it.

```
"lom": "core/plugins/corvin_plugins/bootstrap.py:42"
       "core/plugins/corvin_plugins/providers/audit_backend.py:99"
       "tests/test_plugin_lifecycle.py:123"
```

Used to answer: "Which code made this happen?"

## Error Classification

### RECOVERABLE

**Examples**: Timeout, connection error, temporary failure

**Properties**:
- Can be retried
- Circuit breaker allows retry after cooldown
- Degradation: drop secondary copy (for sinks)

**Handling**:
```python
guard.call(fn, fallback=default_value, timeout_s=5.0)
# Returns fallback, no exception
```

### DEGRADED

**Examples**: Logic error, constraint violation

**Properties**:
- Service reduced but functional
- May recover with operator intervention
- Degradation: use built-in fallback

**Handling**:
```python
# Use default provider, skip optional plugin
return default_handler(request)
```

### CRITICAL

**Examples**: Out of memory, import error, system failure

**Properties**:
- Cannot recover
- Must fail-fast and alert operator
- Degradation: deny access (fail-closed)

**Handling**:
```python
raise InternalServerError("critical plugin failure")  # Don't hide it
```

## Integration Patterns

### Pattern 1: Capability-Protected Audit Write

```python
# Plugin wants to write audit event
gate.check_capability(PluginCapability.AUDIT_WRITE.value)  # Must have capability

# Plugin calls audit_emit (guard wraps it)
result = guard.call(
    capability="audit_write",
    fn=lambda: audit_emit("plugin.event", details),
    fallback=None,
    timeout_s=1.0,
)
# If denied: PermissionDeniedError + audit event
# If fails: logs error, returns fallback, never raises
# If succeeds: records metric
```

### Pattern 2: Health Check with Metrics

```python
# Periodic health check from collector
def health_check_and_record():
    start = time.time()
    try:
        health = guard.call(
            capability="core_api",
            fn=lambda: plugin.health_check(),
            fallback=HealthStatus(ok=False, message="timeout or error"),
            timeout_s=2.0,
        )
        duration = (time.time() - start) * 1000
        
        collector.record_health_check(
            plugin_id=plugin_id,
            ok=health.ok,
        )
        
        if not health.ok:
            audit_logger.log_health_degraded(...)
    
    except Exception:
        pass  # Already handled by guard, never raised
```

### Pattern 3: File Operations with Constraints

```python
# Plugin wants to read file
gate.check_capability(PluginCapability.SYSTEM_FILE_READ.value)
gate.check_file_read(
    file_path="/home/user/.corvin/config.yaml",
    file_size=4096,
    lom="plugin.py:42",
)

# If denied or violates constraints:
# → SandboxViolationError + audit event
# → recorded in metrics

# If allowed:
# → continue with file read
# → log_executed() records success
```

### Pattern 4: Graceful Network Fallback

```python
# Plugin wants to call external API
result = guard.call(
    capability="integration_external_api",
    fn=lambda: plugin.call_external_api(url),
    fallback={"cached": True, "data": [...]},  # Use cached data
    timeout_s=5.0,
)
# If fails: returns cached data instead of raising
# User sees cached data; no interruption
# Operator sees error in metrics/audit
```

## Extensibility Points

### Adding New Capability

1. Add to `PluginCapability` enum
2. Document capability in PLUGIN_INFRASTRUCTURE_GUIDE.md
3. Create gate check method (e.g., `gate.check_capability("new_cap")`)
4. Use in security policy

### Adding New Metrics

1. Add recording method to `PluginMetricsCollector`
2. Add Prometheus export in `render_prometheus()`
3. Call from relevant component (bootstrap, guard, etc.)

### Adding New Audit Event

1. Add to `PluginEventType` enum
2. Add logging method to `PluginAuditLogger` (e.g., `log_new_event()`)
3. Call from appropriate component

### Adding New Degradation Strategy

1. Add case to `GracefulDegradation.apply_degradation()`
2. Document in PLUGIN_INFRASTRUCTURE_GUIDE.md
3. Use in error handling callbacks

## Testing Strategy

### Unit Tests

Test individual components in isolation:
- Event immutability and validation
- Policy enforcement
- Metrics aggregation
- Error handling and fallbacks

### Integration Tests

Test component interactions:
- Full plugin lifecycle (load → execute → health check → unload)
- Error flow (violation → audit → metrics)
- Capability checking with metrics recording

### Scenario Tests (Future)

End-to-end scenarios:
- Plugin timeout recovery (circuit breaker open → half-open → closed)
- Security violation and remediation
- Multi-plugin failure cascade prevention
- Metrics validation against audit trail

## Performance Optimization

### Audit Logging

```
Core write (on critical path)
    ├─ Hash-chain append (≈0.1ms)
    └─ Return immediately

Fanout emit (off critical path)
    ├─ Hand-off to queue (≈0.01ms)
    └─ Return immediately
```

### Security Checks

```
Capability check:   frozenset lookup      ≈0.05ms
File constraint:    size comparison       ≈0.05ms
Path pattern:       glob matching         ≈0.1ms
Network domain:     frozenset lookup      ≈0.05ms
```

### Metrics

```
Record execution:   dict update           ≈0.02ms
Render Prometheus:  string formatting     ≈1ms per 100 plugins
```

### Guard Overhead

```
Successful call:    try/except            ≈0.01ms
Exception handling: classify + audit      ≈0.1ms
```

**Total per-call overhead**: ≈0.1ms (1/10000 of typical plugin call)

## References

- **ADR-0233**: Plugin system architecture and lifecycle
- **ADR-0701**: Plugin security gates (capability model)
- **ADR-0680/0681/0682**: Observability and dual-write OTEL
- **GDPR Art. 30/32**: Record-keeping and security
- **EU AI Act**: Transparency and documentation
