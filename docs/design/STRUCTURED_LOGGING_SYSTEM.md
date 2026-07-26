# Structured Logging System for CorvinOS Compartmentalization

**Date:** 2026-07-26  
**Status:** Design Specification (Ready to Implement in Phase 1)  
**Related to:** ADR-0XXX (Compartmentalization System)

---

## Overview: The Logging Stack

```
Layer 4 (System)  ← NerveFiber + Dashboards + Alerting
                      ↑
Layer 3 (Feature) ← Structured Logs (correlation IDs, context)
                      ↑
Layer 2 (Plugin)  ← Plugin-level structured logging
                      ↑
Layer 1 (Code)    ← Application logs (component methods)
```

**Key principle:** Single log stream, rich structured data, human-friendly filtering.

---

## Layer 1: Code-Level Logging (In Every Component)

### Structured Logger Interface (Python)

```python
# core/logging/structured_logger.py

from dataclasses import asdict
from typing import Any
import json
import logging

class CorvinLogger:
    """Structured logger with tenant/correlation/component context."""
    
    def __init__(self, component: str, plugin_id: str | None = None):
        self.component = component
        self.plugin_id = plugin_id
        self._logger = logging.getLogger(component)
    
    def error(
        self,
        message: str,
        *,
        error_code: str,
        context: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        memory_mb: int | None = None,
    ):
        """Log error with structured fields."""
        event = {
            "level": "ERROR",
            "component": self.component,
            "plugin_id": self.plugin_id,
            "message": message,
            "error_code": error_code,  # No PII, just error type
            "context": context or {},
            "duration_ms": duration_ms,
            "memory_mb": memory_mb,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        self._emit(event)
    
    def warn(self, message: str, *, error_code: str = "", context: dict | None = None):
        event = {
            "level": "WARN",
            "component": self.component,
            "plugin_id": self.plugin_id,
            "message": message,
            "error_code": error_code,
            "context": context or {},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        self._emit(event)
    
    def info(self, message: str, *, context: dict | None = None):
        event = {
            "level": "INFO",
            "component": self.component,
            "plugin_id": self.plugin_id,
            "message": message,
            "context": context or {},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        self._emit(event)
    
    def debug(self, message: str, *, context: dict | None = None):
        """Debug logs only if DEBUG=1 env var."""
        event = {
            "level": "DEBUG",
            "component": self.component,
            "plugin_id": self.plugin_id,
            "message": message,
            "context": context or {},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        self._emit(event)
    
    def _emit(self, event: dict):
        """Emit to standard logging + structured log stream."""
        # Add context from current request (if available)
        if hasattr(_thread_local, "correlation_id"):
            event["correlation_id"] = _thread_local.correlation_id
        if hasattr(_thread_local, "tenant_id"):
            event["tenant_id"] = _thread_local.tenant_id
        
        # Emit as JSON to stdout (picked up by log aggregator)
        print(json.dumps(event))
        
        # Also log to standard Python logger for local debugging
        self._logger.log(
            getattr(logging, event["level"]),
            json.dumps(event)
        )
```

### Thread-Local Context (For Correlation IDs)

```python
# core/logging/context.py

import threading
from contextlib import contextmanager

_thread_local = threading.local()

def set_correlation_id(correlation_id: str):
    """Set correlation ID for this request."""
    _thread_local.correlation_id = correlation_id

def set_tenant_id(tenant_id: str):
    """Set tenant ID for this request."""
    _thread_local.tenant_id = tenant_id

@contextmanager
def request_context(correlation_id: str, tenant_id: str = "_default"):
    """Context manager for request handling."""
    old_cid = getattr(_thread_local, "correlation_id", None)
    old_tid = getattr(_thread_local, "tenant_id", None)
    
    _thread_local.correlation_id = correlation_id
    _thread_local.tenant_id = tenant_id
    
    try:
        yield
    finally:
        if old_cid:
            _thread_local.correlation_id = old_cid
        if old_tid:
            _thread_local.tenant_id = old_tid
```

### Usage in Plugins

```python
# core/plugins/corvin_plugins/providers/audit_backend.py

from corvin_logging import CorvinLogger

class DefaultAuditBackendPlugin(Plugin):
    def __init__(self):
        self.logger = CorvinLogger("audit-backend", "audit-compliance/1.0.0")
    
    def log_event(self, event_type: str, details: dict, **kwargs):
        """Log audit event with structured fields."""
        try:
            # ... process event ...
            self.logger.info(
                "Audit event logged successfully",
                context={
                    "event_type": event_type,
                    "event_id": event_id,
                    "hash_chain_verified": True,
                }
            )
        except Exception as e:
            self.logger.error(
                "Failed to log audit event",
                error_code=type(e).__name__,  # e.g., "IOError", "ValueError"
                context={
                    "event_type": event_type,
                    "exception_type": type(e).__name__,
                },
                duration_ms=elapsed_ms,
            )
            raise
```

---

## Layer 2: Plugin-Level Lifecycle Logging

### Plugin Lifecycle Events (Auto-logged by Plugin System)

```python
# core/plugins/corvin_plugins/registry.py (updated)

class PluginRegistry:
    def register(self, plugin: CorvinPlugin, ctx: PluginContext) -> None:
        """Register plugin and auto-log lifecycle event."""
        try:
            self._plugins[plugin.plugin_id] = plugin
            plugin.on_load(ctx)
            
            # Auto-log plugin loaded event
            ctx.log_event(
                event_type="plugin.loaded",
                details={
                    "plugin_id": plugin.plugin_id,
                    "plugin_type": plugin.plugin_type,
                    "version": plugin.version,
                    "display_name": plugin.display_name,
                },
                component="plugin-registry",
            )
        except Exception:
            # Auto-log plugin load failure
            ctx.log_event(
                event_type="plugin.load_failed",
                details={
                    "plugin_id": plugin.plugin_id,
                    "error_code": type(e).__name__,  # No stack trace (PII risk)
                },
                component="plugin-registry",
            )
            raise
    
    def health_check_all(self) -> dict[str, HealthStatus]:
        """Poll all plugins and log health status."""
        results = {}
        for pid, plugin in self._plugins.items():
            try:
                status = plugin.health_check()
                results[pid] = status
                
                # Log unhealthy plugins
                if not status.ok:
                    ctx.log_event(
                        event_type="plugin.unhealthy",
                        details={
                            "plugin_id": pid,
                            "health_message": status.message,
                            "consecutive_failures": self._failure_count.get(pid, 1),
                        },
                        component="plugin-registry",
                    )
            except Exception as e:
                # Log health-check failure
                ctx.log_event(
                    event_type="plugin.health_check_failed",
                    details={
                        "plugin_id": pid,
                        "error_code": type(e).__name__,
                    },
                    component="plugin-registry",
                )
        return results
```

---

## Layer 3: Feature-Level Correlation

### Correlation ID Injection (FastAPI Middleware)

```python
# core/api/middleware.py

from fastapi import Request
import uuid
from corvin_logging import set_correlation_id, set_tenant_id

@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    """Inject correlation ID and tenant ID for this request."""
    
    # Generate or extract correlation ID
    cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    
    # Extract tenant ID (from JWT or URL)
    tenant_id = request.headers.get("X-Tenant-ID", "_default")
    
    # Set context for all logging in this request
    set_correlation_id(cid)
    set_tenant_id(tenant_id)
    
    response = await call_next(request)
    
    # Add correlation ID to response headers
    response.headers["X-Correlation-ID"] = cid
    
    return response
```

### Example Trace (Single Request, Multiple Plugins)

```
Request: POST /api/audit-events
Correlation ID: req-abc123def456
Tenant ID: company-1

Timeline:
  10:30:45.001 → API Router (received request)
  10:30:45.010 → User Backend (authenticate request)
  10:30:45.020 → Audit Backend (prepare audit event)
  10:30:45.025 → Audit Backend (verify hash-chain)
  10:30:45.030 → Audit Backend (write to file)
  10:30:45.031 → API Router (return 200)

All logs have correlation_id: "req-abc123def456"
All logs have tenant_id: "company-1"

Ops can search: {correlation_id="req-abc123def456"}
→ See entire flow for this one request
```

---

## Layer 4: System-Level Aggregation (NerveFiber)

### Prometheus Metrics Export

```python
# core/telemetry/metrics.py

from prometheus_client import Counter, Histogram, Gauge

# Per-plugin metrics
plugin_load_total = Counter(
    'corvin_plugin_load_total',
    'Total plugin loads',
    ['plugin_id', 'status']  # status: success, failure
)

plugin_health_check_failures = Counter(
    'corvin_plugin_health_check_failures_total',
    'Total health check failures',
    ['plugin_id']
)

plugin_method_duration_seconds = Histogram(
    'corvin_plugin_method_duration_seconds',
    'Plugin method execution time',
    ['plugin_id', 'method_name'],
    buckets=[0.001, 0.01, 0.1, 1.0, 10.0]
)

plugin_memory_bytes = Gauge(
    'corvin_plugin_memory_bytes',
    'Plugin memory usage',
    ['plugin_id']
)

# System-level metrics
healing_actions_total = Counter(
    'corvin_healing_actions_total',
    'Total healing actions',
    ['plugin_id', 'action_type']  # action_type: circuit_break, soft_restart, disable
)

mttr_seconds = Histogram(
    'corvin_mttr_seconds',
    'Mean time to recovery for healed plugins',
    ['plugin_id'],
    buckets=[1, 5, 30, 60, 300, 1800]
)
```

### Grafana Dashboard Queries

**Plugin Health Dashboard:**
```
# Current health status
max by (plugin_id) (corvin_plugin_health_status)

# Error rate per plugin
rate(corvin_plugin_errors_total[5m])

# Healing actions per hour
increase(corvin_healing_actions_total[1h])

# MTTR trend
avg_over_time(corvin_mttr_seconds[7d])
```

---

## Implementation Checklist (Phase 1)

### Core Components
- [ ] CorvinLogger class (structured output)
- [ ] Thread-local context manager (correlation IDs)
- [ ] Plugin lifecycle logging (on_load, on_unload, health_check)
- [ ] FastAPI middleware (correlation ID injection)
- [ ] Logging unit tests (15+ tests)

### Integration
- [ ] Update all plugin implementations to use CorvinLogger
- [ ] Update registry to auto-log lifecycle events
- [ ] Update audit trail to use structured logging
- [ ] Update tests to validate correlation IDs

### Deployment
- [ ] Stdout JSON format (parsed by Loki/ELK)
- [ ] Local debugging with Python logging
- [ ] Prometheus metrics export
- [ ] No breaking changes to existing logs

### Documentation
- [ ] "How to Debug Using Structured Logs" guide
- [ ] Example Grafana queries
- [ ] Correlation ID usage guide

---

## Example: Full Request Flow with Logs

### Request: Authenticate User

**User sends:**
```
POST /api/auth/login
{
  "username": "user@example.com",
  "password": "..."
}
```

**Log stream (same correlation_id):**

```json
{
  "timestamp": "2026-08-15T10:30:45.001Z",
  "level": "INFO",
  "component": "api-router",
  "plugin_id": null,
  "tenant_id": "company-1",
  "correlation_id": "req-abc123",
  "message": "POST /api/auth/login received",
  "context": {"method": "POST", "path": "/api/auth/login"}
}
```

```json
{
  "timestamp": "2026-08-15T10:30:45.015Z",
  "level": "INFO",
  "component": "user-backend",
  "plugin_id": "user-management-local/1.0.0",
  "tenant_id": "company-1",
  "correlation_id": "req-abc123",
  "message": "User authentication attempted",
  "context": {"username_hash": "abc123", "provider": "local"},
  "duration_ms": 14
}
```

```json
{
  "timestamp": "2026-08-15T10:30:45.025Z",
  "level": "INFO",
  "component": "audit-backend",
  "plugin_id": "audit-compliance/1.0.0",
  "tenant_id": "company-1",
  "correlation_id": "req-abc123",
  "message": "Audit event logged",
  "context": {"event_type": "auth.login_success", "user_id_hash": "xyz789"},
  "duration_ms": 10
}
```

```json
{
  "timestamp": "2026-08-15T10:30:45.030Z",
  "level": "INFO",
  "component": "api-router",
  "plugin_id": null,
  "tenant_id": "company-1",
  "correlation_id": "req-abc123",
  "message": "POST /api/auth/login completed",
  "context": {"status_code": 200, "response_ms": 29}
}
```

**Operator query:**
```
{correlation_id="req-abc123"}
```

**Result:** Full trace of the request across all components. Latency per component visible. No PII.

---

## Privacy & Security

### Guaranteed No PII

Every log is scrubbed:
```python
def _assert_safe(event: dict) -> dict:
    """Ensure no PII in event."""
    BANNED_PATTERNS = [
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
        r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
        r'\b\d{16}\b',  # Credit card
    ]
    
    # Check all string values in event
    for value in _flatten_dict(event).values():
        if isinstance(value, str):
            for pattern in BANNED_PATTERNS:
                if re.search(pattern, value):
                    raise ValueError(f"PII detected in log: {pattern}")
    
    return event
```

### Per-Tenant Isolation

```python
# Every log has tenant_id
# Dashboard query: {tenant_id="company-1"}
# Never mixes tenants

# Alerts honor tenant boundaries
# "Plugin X is unhealthy" only shows X's logs, not all plugins
```

---

## Operational Queries (Examples)

### Debugging a User-Reported Issue
```
{correlation_id="user-provided-request-id"}
```
Shows exact flow for that user's request.

### Finding Root Cause of Incident
```
{level="ERROR"} | stats count() by error_code | sort count() desc
```
Shows most common errors.

### Plugin Performance
```
{component="audit-backend"} | stats avg(duration_ms), max(duration_ms) by operation
```
Shows which operations are slow.

### Healing Success Rate
```
{event_type="plugin.healing_action"} 
| stats count() by plugin_id, healing_action
| stats (healing action count / total failures)
```
Measures which healing strategies work.

---

## Performance Impact (Target: <5%)

- Structured logging overhead: ~1-2ms per operation (50 ns per field)
- Correlation ID injection: <1ms (header parsing)
- JSON serialization: ~1-2ms (depends on context size)
- Network I/O: 0ms (stdout buffered)

**Total overhead: ~3-5ms per request.** Acceptable.

---

## Next Steps (Phase 1 Implementation)

1. Implement CorvinLogger (100 LOC)
2. Add thread-local context (50 LOC)
3. Update plugin registry (100 LOC)
4. Add FastAPI middleware (50 LOC)
5. Update all plugins to use CorvinLogger (500 LOC)
6. Write 20+ unit tests
7. Document operational queries
8. Deploy to staging + measure overhead

**Estimate: 1 engineer, 2-3 weeks**
