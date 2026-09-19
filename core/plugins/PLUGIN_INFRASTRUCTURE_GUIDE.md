# Plugin Supporting Infrastructure Guide

This guide covers the four supporting infrastructure modules for production-ready plugin system:

1. **Audit Logging** — Hash-chained event recording
2. **Security** — Capability-based access control and sandboxing
3. **Metrics & Observability** — Comprehensive telemetry
4. **Error Handling** — Graceful degradation and isolation

All modules are designed for production deployment with GDPR/compliance compliance, fail-closed semantics, and comprehensive audit trails.

## Module Overview

### 1. Audit Logging (`audit_logging.py`)

Records all plugin lifecycle events to the core hash-chained audit trail.

**Key Components:**
- `PluginEventType`: Enum of 19 event types (load, execute, capability register, health check, etc.)
- `PluginAuditEvent`: Immutable event record (frozen dataclass)
- `PluginAuditLogger`: Thread-safe logger that emits to core audit trail

**Event Categories:**
- Lifecycle: `INSTALLED`, `UNINSTALLED`, `LOADED`, `UNLOADED`
- Execution: `EXECUTED`, `EXECUTION_FAILED`, `EXECUTION_SLOW`, `EXECUTION_TIMEOUT`
- Capabilities: `CAPABILITY_REGISTERED`, `CAPABILITY_UNREGISTERED`
- Configuration: `CONFIG_CHANGED`
- Health: `HEALTH_CHECK`, `HEALTH_DEGRADED`, `HEALTH_RECOVERED`
- Security: `PERMISSION_DENIED`, `SANDBOX_VIOLATION`
- State: `ENABLED`, `DISABLED`, `CIRCUIT_OPENED`, `CIRCUIT_CLOSED`

**Usage:**

```python
from corvin_plugins.audit_logging import PluginAuditLogger
from audit import audit_event  # Core audit function (hash-chained)

# Create logger for a tenant
logger = PluginAuditLogger(
    audit_emit=audit_event,
    tenant_id="default"
)

# Log plugin load
logger.log_loaded(
    plugin_id="my_plugin",
    version="1.0.0",
    boot_layer="bundled",
    plugin_type="audit_backend",
    lom="bootstrap.py:42",
    duration_ms=12.5,
)

# Log execution
logger.log_executed(
    plugin_id="my_plugin",
    version="1.0.0",
    capability="audit_write",
    duration_ms=5.0,
    lom="plugin.py:99",
    call_count=1,
)

# Log failure
logger.log_execution_failed(
    plugin_id="my_plugin",
    version="1.0.0",
    capability="audit_write",
    duration_ms=50.0,
    error_type="ValueError",
    error_message="Invalid config",
    lom="plugin.py:99",
)
```

**Design Principles:**
- **Audit-first**: Events are written to core chain BEFORE side effects
- **Immutable**: Events are frozen dataclasses; cannot be modified
- **Tenant-scoped**: Every event includes tenant_id; queries are filtered
- **Line-of-moral-responsibility**: Every event includes call site (lom)
- **Non-blocking**: Failures to audit are logged but never raised

### 2. Security (`security.py`)

Fine-grained security controls: capability-based access control, permissions, sandbox constraints.

**Key Components:**
- `PluginCapability`: 20+ capabilities (grouped by domain: CORE, DATA, SYSTEM, AUDIT, ADMIN, INTEGRATION)
- `PluginSecurityPolicy`: Immutable per-plugin security policy
- `SecurablePluginGate`: Runtime security enforcement gate
- `PluginSecurityContext`: Registry of policies for all plugins

**Capability Domains:**
- **CORE**: `CORE_PLUGIN_API`, `CORE_REGISTRY`
- **DATA**: `DATA_READ_USER_CONTEXT`, `DATA_READ_CONVERSATION_HISTORY`, `DATA_WRITE_STORAGE`, `DATA_MODIFY_CONFIG`
- **SYSTEM**: `SYSTEM_FILE_READ`, `SYSTEM_FILE_WRITE`, `SYSTEM_SUBPROCESS`, `SYSTEM_NETWORK`, `SYSTEM_TIME`
- **AUDIT**: `AUDIT_READ`, `AUDIT_WRITE`
- **ADMIN**: `ADMIN_CONFIG`, `ADMIN_PLUGIN_MANAGE`, `ADMIN_TENANT_MANAGE`
- **INTEGRATION**: `INTEGRATION_NOTIFY`, `INTEGRATION_EXTERNAL_API`

**Usage:**

```python
from corvin_plugins.security import (
    PluginSecurityPolicy,
    PluginCapability,
    SecurablePluginGate,
)
from audit import audit_event

# Define policy (at load time)
policy = PluginSecurityPolicy(
    plugin_id="my_audit_backend",
    boot_layer="bundled",
    capabilities=frozenset([
        PluginCapability.AUDIT_WRITE.value,
        PluginCapability.SYSTEM_FILE_WRITE.value,
    ]),
    max_file_write_size=50 * 1024 * 1024,  # 50 MB limit
    allowed_write_paths=frozenset([
        "/home/user/.corvin/audit/**",
    ]),
)

# Create security gate
gate = SecurablePluginGate(
    policy=policy,
    audit_emit=audit_event,
    tenant_id="default",
)

# Check capabilities (raises PermissionDeniedError if denied)
gate.check_capability(PluginCapability.AUDIT_WRITE.value)

# Check file constraints
gate.check_file_write(
    file_path="/home/user/.corvin/audit/events.jsonl",
    file_size=1024,
    lom="plugin.py:42",
)

# Check network access
gate.check_network_access("api.anthropic.com", lom="plugin.py:99")

# Get violation count
violations = gate.violation_count()
```

**Enforcement Rules:**
- **Capability check**: Denies operation if capability not in policy
- **File size limit**: Prevents reading/writing files larger than policy limits
- **Path constraints**: Restricts file access to allowed patterns (glob-based)
- **Network domain restrictions**: Only allows access to allowlisted domains
- **Memory limits**: Optional memory ceiling (for resource-constrained plugins)
- **Encryption key access**: Opt-in capability for security-sensitive operations

**Fail-Closed Design:**
- All checks raise immediately with audit trail
- No silent failures; no "warn and continue"
- Violations are permanent in audit chain
- No privileged mode or override mechanism

### 3. Metrics & Observability (`metrics.py`)

Comprehensive plugin telemetry for dashboards, alerting, and performance analysis.

**Key Components:**
- `ExecutionStats`: Per-capability statistics (latency, throughput, error rates)
- `PluginMetrics`: Aggregated metrics for one plugin
- `PluginMetricsCollector`: Thread-safe collector with Prometheus export

**Metrics Collected:**
- **Execution**: call count, success/failure counts, latency (min/max/avg), error types
- **Lifecycle**: load count, load failures, health check results
- **Security**: permission denials, sandbox violations
- **Circuit breaker**: open count, consecutive failures, state transitions
- **Audit**: events emitted

**Usage:**

```python
from corvin_plugins.metrics import get_metrics_collector

collector = get_metrics_collector()

# Record plugin load
collector.record_load(
    plugin_id="my_plugin",
    version="1.0.0",
    boot_layer="bundled",
    duration_ms=42.5,
)

# Record execution
collector.record_execution(
    plugin_id="my_plugin",
    capability="audit_write",
    duration_ms=5.0,
    success=True,
)

# Record failure
collector.record_execution(
    plugin_id="my_plugin",
    capability="audit_write",
    duration_ms=50.0,
    success=False,
    error_type="ValueError",
)

# Record health check
collector.record_health_check(
    plugin_id="my_plugin",
    ok=True,
)

# Record security events
collector.record_permission_denied("my_plugin")
collector.record_sandbox_violation("my_plugin")

# Export as Prometheus metrics
prometheus_text = collector.render_prometheus()
# Returns:
# corvin_plugin_loads_total{plugin="my_plugin"} 1
# corvin_plugin_calls_total{plugin="my_plugin",capability="audit_write"} 2
# corvin_plugin_call_success_total{plugin="my_plugin",capability="audit_write"} 1
# ...

# Get structured data for API
metrics = collector.get_metrics("my_plugin")
print(metrics.to_dict())
# {
#   "plugin_id": "my_plugin",
#   "version": "1.0.0",
#   "capabilities": {
#     "audit_write": {
#       "call_count": 2,
#       "success_count": 1,
#       "failure_count": 1,
#       "avg_duration_ms": 27.5,
#       "error_rate_pct": 50.0,
#     }
#   },
#   ...
# }
```

**Dashboard Integration:**
Metrics are exported to:
- **Prometheus**: Via `render_prometheus()` — standard text format for scraping
- **API responses**: Via `to_dict()` — JSON for console/UI
- **Health dashboard**: Per-capability execution stats, error rates, latency percentiles
- **Alerting**: Threshold breach detection (circuit opens, health failures, error rate spikes)

### 4. Error Handling (`error_handling.py`)

Graceful error handling with isolation, fallbacks, and automatic recovery.

**Key Components:**
- `PluginCallGuard`: Safe execution wrapper (sync and async)
- `ErrorContext`: Error context and severity classification
- `ErrorSeverity`: Three levels (RECOVERABLE, DEGRADED, CRITICAL)
- `GracefulDegradation`: Degradation strategies per error type

**Usage:**

```python
from corvin_plugins.error_handling import make_call_guard, ErrorSeverity
from corvin_plugins.metrics import get_metrics_collector
from audit import audit_event

collector = get_metrics_collector()

def on_error(error_context):
    """Record error in metrics."""
    collector.record_execution(
        plugin_id=error_context.plugin_id,
        capability=error_context.capability,
        duration_ms=error_context.duration_ms,
        success=False,
        error_type=error_context.error_type,
    )

# Create guard for a plugin
guard = make_call_guard(
    plugin_id="my_plugin",
    tenant_id="default",
    audit_emit=audit_event,
    on_error=on_error,
)

# Execute plugin with fallback
result = guard.call(
    capability="audit_write",
    fn=lambda: plugin.write_audit_event(event),
    fallback={"status": "error"},
    timeout_s=5.0,
    operation="write_audit",
)
# If plugin.write_audit_event() fails or times out,
# result = {"status": "error"} (fallback)
# Error is logged, audited, and metrics updated

# Async execution
import asyncio

async_result = await guard.call_async(
    capability="fetch_data",
    fn=lambda: plugin.fetch_data_async(url),
    fallback=[],
    timeout_s=10.0,
    operation="async_fetch",
)
```

**Error Classification:**
- **RECOVERABLE**: Timeout, connection errors, temporary failures (can be retried)
- **DEGRADED**: Logic errors, constraint violations (degrades service)
- **CRITICAL**: Out of memory, import errors, system failures (fail-fast)

**Degradation Strategies:**
- **drop_copy**: For secondary sinks (audit fanout, notifications) — drop the copy
- **use_default**: For capability providers — use built-in fallback
- **deny**: For auth/access plugins — fail-closed (deny)
- **skip**: For optional plugins — skip and continue

**Circuit Breaker Integration:**
Errors trigger the circuit breaker (ADR-0233):
1. **Consecutive failures** increment counter
2. **Threshold reached** opens circuit (fail-fast for 30s)
3. **Cooldown expires** admits one probe (half-open)
4. **Probe succeeds** closes circuit; **fails** reopens

## Integration with Bootstrap

Integrate these modules into `bootstrap.py`:

```python
from corvin_plugins.audit_logging import PluginAuditLogger
from corvin_plugins.security import PluginSecurityContext
from corvin_plugins.metrics import get_metrics_collector
from corvin_plugins.error_handling import make_call_guard

# During bootstrap_tenant() or bootstrap_declared():

# Create audit logger
audit_logger = PluginAuditLogger(
    audit_emit=_default_audit_emit(tenant_id),
    tenant_id=tenant_id,
)

# Create security context
security_context = PluginSecurityContext()

# Get global metrics collector
metrics_collector = get_metrics_collector()

# Define security policy for this plugin
policy = PluginSecurityPolicy(
    plugin_id=plugin_record.id,
    boot_layer=plugin_record.boot_layer,
    capabilities=frozenset(get_plugin_capabilities(plugin_record)),
)
security_context.register_policy(policy)

# Create call guard for error handling
call_guard = make_call_guard(
    plugin_id=plugin_record.id,
    tenant_id=tenant_id,
    audit_emit=_default_audit_emit(tenant_id),
    on_error=lambda ctx: metrics_collector.record_execution(
        plugin_id=ctx.plugin_id,
        capability=ctx.capability,
        duration_ms=ctx.duration_ms,
        success=False,
        error_type=ctx.error_type,
    ),
)

# When loading plugin:
try:
    start = time.time()
    plugin_instance = loader.load_plugin(plugin_record, ctx)
    duration = (time.time() - start) * 1000
    
    audit_logger.log_loaded(
        plugin_id=plugin_record.id,
        version=plugin_record.version,
        boot_layer=plugin_record.boot_layer,
        plugin_type=plugin_record.plugin_type,
        lom=inspect.currentframe().f_code.co_filename + ":" + str(inspect.currentframe().f_lineno),
        duration_ms=duration,
    )
    
    metrics_collector.record_load(
        plugin_id=plugin_record.id,
        version=plugin_record.version,
        boot_layer=plugin_record.boot_layer,
        duration_ms=duration,
    )
    
except Exception as exc:
    audit_logger.log_load_failed(
        plugin_id=plugin_record.id,
        version=plugin_record.version,
        boot_layer=plugin_record.boot_layer,
        reason=str(exc),
        error_type=type(exc).__name__,
        lom=...,
    )
    # Skip plugin, continue with next
```

## Console Integration

Expose metrics and security information in the console:

```python
# core/console/corvin_console/routes/plugin_infrastructure.py

from corvin_plugins.metrics import get_metrics_collector
from corvin_plugins.security import PluginSecurityContext

@router.get("/v1/console/plugins/{plugin_id}/metrics")
async def get_plugin_metrics(plugin_id: str):
    """Get metrics for a plugin."""
    collector = get_metrics_collector()
    metrics = collector.get_metrics(plugin_id)
    
    if metrics is None:
        return {"error": "plugin not found"}
    
    return metrics.to_dict()

@router.get("/v1/console/plugins/metrics/prometheus")
async def get_prometheus_metrics():
    """Export all metrics in Prometheus format."""
    collector = get_metrics_collector()
    return Response(
        content=collector.render_prometheus(),
        media_type="text/plain; charset=utf-8",
    )

@router.get("/v1/console/plugins/{plugin_id}/security")
async def get_plugin_security(plugin_id: str):
    """Get security policy for a plugin."""
    context = PluginSecurityContext()  # Get from context
    policy = context.get_policy(plugin_id)
    
    if policy is None:
        return {"error": "no security policy"}
    
    return {
        "plugin_id": policy.plugin_id,
        "boot_layer": policy.boot_layer,
        "capabilities": list(policy.capabilities),
        "max_file_read_size": policy.max_file_read_size,
        "max_file_write_size": policy.max_file_write_size,
        "allowed_domains": list(policy.allowed_domains),
    }
```

## Audit Chain Verification

Verify audit events are recorded:

```bash
# Query audit chain for plugin events
grep '"plugin_id"' ~/.corvin/tenants/_default/global/forge/audit.jsonl | \
  jq 'select(.event_type | startswith("plugin.")) | {event_type, plugin_id, timestamp}'

# Output:
# {
#   "event_type": "plugin.loaded",
#   "plugin_id": "my_plugin",
#   "timestamp": "2026-09-19T12:00:00Z"
# }
# ...

# Verify chain integrity
python3 scripts/verify_audit_chain.py --tenant=_default
# Output: ✅ Chain height 1000, all hashes verified, 0 gaps
```

## Testing

Comprehensive test suite in `tests/plugins/test_plugin_infrastructure.py`:

```bash
pytest tests/plugins/test_plugin_infrastructure.py -v
```

Tests cover:
- Event immutability and validation
- Audit trail recording and tenant isolation
- Security policy enforcement
- Permission and sandbox constraint checking
- Metrics collection and aggregation
- Error handling and graceful degradation
- Integration across all modules

## Performance Characteristics

- **Audit logging**: ~0.1ms per event (async hand-off)
- **Security checks**: ~0.05ms per capability check
- **Metrics recording**: ~0.02ms per metric
- **Error handling**: ~0.1ms per plugin call (guard overhead)
- **Memory**: ~10KB per plugin (policy + metrics)

## Compliance Notes

- **GDPR Art. 30/32**: Every audit event is immutable and hash-chained
- **GDPR Art. 5**: Events are tenant-scoped; no cross-tenant leakage
- **Fail-closed design**: Denials are immediate with audit trail; no silent failures
- **No opt-out**: Security enforcement cannot be disabled; no compliance-off mode
- **Audit-first**: Core chain write commits before side effects

## References

- ADR-0233: Plugin lifecycle and audit integration
- ADR-0701: Plugin security gates and capability model
- ADR-0680/0681/0682: Observability and OTEL dual-write
- ADR-0314: Learning infrastructure (error feedback loop)
- audit.py: Core hash-chained audit writer
