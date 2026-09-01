# Error Healing

## Overview

Error Healing monitors errors, categorizes them, and triggers automatic recovery strategies. Integrates with the Self-Repair Engine (27) to attempt healing without human intervention.

**Why it matters:**
- Reduces mean-time-to-recovery (MTTR)
- Distinguishes recoverable from critical errors
- Enables autonomous resilience
- Feeds learning loops with error patterns

## Architecture

```
Error Event ──▶ Error Classifier ──▶ Recovery Selector ──▶ Recovery Executor
                      │                                          │
                      └──────────────────────────────────────────┘
                                    ▼
                            Healing Trace Logger
                           (Audit + Learning)
```

## Usage

```python
from buildin.observability.error_healing import ErrorHealing

healer = ErrorHealing(tenant_id="default")
await healer.initialize()

# Report an error
error = {
    "type": "context_loss",
    "severity": "high",
    "message": "Context checkpoint missing",
    "traceback": "..."
}
await healer.report_error(error)

# Get healing status
status = await healer.get_healing_status()
print(f"Recovery attempted: {status['recovery_attempted']}")
print(f"Success: {status['recovery_success']}")
```

## Performance Metrics

| Metric | Target |
|--------|--------|
| Error Classification | <10ms |
| Recovery Strategy Selection | <50ms |
| Recovery Execution | <1s |
| Healing Success Rate | >80% |

## Testing

```bash
pytest tests/e2e_error_healing.py -v
```

## Compliance

**GDPR Art. 30:** Error events logged  
**EU AI Act Art. 50:** Recovery actions documented

## Related Plugins

- **Self-Repair Engine (27):** Executes recovery strategies
- **Autonomy Status Tracker (21):** Provides context
- **Healing Traces (11):** Audit logging

## ADR Reference

See ADR-0525 for architectural decisions.

## Metadata

- **Version:** 1.0.0
- **License:** Apache-2.0
- **Maintainer:** CorvinOS Core Team
- **Boot Layer:** bundled
- **Tier:** buildin
- **Category:** observability/error-handling
