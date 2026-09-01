# Heartbeat Monitor

## Overview

Heartbeat Monitor tracks session liveness via periodic heartbeat signals. Detects stalled sessions, enables presence awareness, and feeds into session lifecycle management.

**Why it matters:**
- Detects hung/stalled sessions early
- Enables presence notifications for multi-session scenarios
- Informs session eviction decisions
- Provides liveness proof for compliance

## Usage

```python
from buildin.observability.heartbeat_monitor import HeartbeatMonitor

monitor = HeartbeatMonitor(tenant_id="default")
await monitor.initialize()

# Emit heartbeat
await monitor.emit_heartbeat("sess_abc123", {"status": "active"})

# Check liveness
is_alive = await monitor.is_session_alive("sess_abc123")
```

## Performance Metrics

| Metric | Target |
|--------|--------|
| Heartbeat Ingestion | <1ms |
| Liveness Check | <5ms |
| Stall Detection | <5s |

## Testing

```bash
pytest tests/e2e_heartbeat_monitor.py -v
```

## ADR Reference

See ADR-0526 for architectural decisions.

## Metadata

- **Version:** 1.0.0
- **License:** Apache-2.0
- **Maintainer:** CorvinOS Core Team
- **Boot Layer:** bundled
- **Tier:** buildin
- **Category:** observability/liveness
