# Consolidation Layer — Self-Healing & Error Recovery

**Phase 4 ADRs:** ADR-0332, ADR-0333  
**Files:** `core/consolidation/self_healing.py`, `core/consolidation/error_recovery.py`  
**Tests:** 46 total (18 self-healing + 22 error-recovery + 6 integration)

## Overview

The consolidation layer provides two-layer resilience patterns for handling transient failures, preventing cascading failures, and enabling graceful degradation:

- **Layer 1 (Tactical):** Single-concern, composable patterns (backoff, circuit breaker, degradation)
- **Layer 2 (Strategic):** Orchestration patterns combining Layer 1 (retry logic, fallback strategies, state rollback)

## Layer 1: Tactical Patterns

### ExponentialBackoff

Implements exponential backoff with jitter to prevent thundering herd problems.

```python
from core.consolidation.self_healing import ExponentialBackoff, BackoffConfig

config = BackoffConfig(
    initial_delay_sec=0.1,
    multiplier=2.0,
    max_delay_sec=30.0,
    max_attempts=5,
    jitter=True,
)
backoff = ExponentialBackoff(config)

# Get delays: 0.1s, 0.2s, 0.4s, 0.8s, 1.6s (with jitter applied)
for _ in range(5):
    delay = backoff.next_delay()
    await asyncio.sleep(delay)
```

**Parameters:**
- `initial_delay_sec`: Starting delay (default 0.1s)
- `multiplier`: Exponential growth factor (default 2.0)
- `max_delay_sec`: Ceiling on delay (default 30s)
- `max_attempts`: Max number of retries (default 5)
- `jitter`: Add ±10% random variance (default True)

### CircuitBreaker

Prevents cascading failures by failing fast when a service is degraded.

```python
from core.consolidation.self_healing import CircuitBreaker, CircuitBreakerConfig

config = CircuitBreakerConfig(
    failure_threshold=5,      # Open after 5 failures
    success_threshold=2,      # Close after 2 successes in HALF_OPEN
    timeout_sec=60.0,         # Wait 60s before attempting recovery
)
breaker = CircuitBreaker(config)

# Use in a call
if breaker.is_closed():
    try:
        result = await call_service()
        breaker.record_success()
    except Exception as exc:
        breaker.record_failure()
        raise
else:
    # Circuit is OPEN; fail fast
    raise CircuitBreakerOpenError()
```

**State Machine:**
- **CLOSED:** Service is healthy; calls allowed
- **OPEN:** Service is failing; calls rejected immediately
- **HALF_OPEN:** Testing recovery after timeout; limited calls allowed

### GracefulDegradation

Serves reduced functionality (e.g., cached, stale data) instead of failing.

```python
from core.consolidation.self_healing import GracefulDegradation, DegradationConfig

config = DegradationConfig(
    enabled=True,
    fallback_value={"cached": True, "timestamp": "2026-08-26"},
    degrade_message="Service degraded, serving cached response",
)
degradation = GracefulDegradation(config)

try:
    result = await expensive_operation()
    degradation.mark_success()
except Exception:
    if degradation.should_degrade():
        result = degradation.get_fallback()
    else:
        raise
```

## Layer 2: Strategic Orchestration

### ErrorClassifier

Routes errors to appropriate recovery strategy.

```python
from core.consolidation.error_recovery import ErrorClassifier, ErrorClass

classifier = ErrorClassifier()

# Classify errors
if classifier.classify(exc) == ErrorClass.TRANSIENT:
    # Retry this; it's temporary
    pass
elif classifier.classify(exc) == ErrorClass.PERMANENT:
    # Don't retry; it's permanent
    raise
```

**Builtin Classifications:**
- **Transient:** `asyncio.TimeoutError`, `ConnectionError`, `TimeoutError`
- **Permanent:** `ValueError`, `KeyError`, `AttributeError`
- **Unknown:** Treated as transient by default

### RetryLogic

Retry wrapper with exponential backoff and error classification.

```python
from core.consolidation.error_recovery import RetryLogic, BackoffConfig

retry = RetryLogic(
    config=BackoffConfig(initial_delay_sec=0.1, max_attempts=5),
)

async def operation():
    # Call external API
    return await api.fetch()

result = await retry.call_with_retry(operation)
stats = retry.get_stats()
print(f"Success rate: {stats['success_rate']:.1%}")
```

**Behavior:**
- Retries transient errors with exponential backoff
- Fails immediately on permanent errors
- Returns result on success or raises exception on exhaustion

### StateRollback

Checkpoint/restore mechanism for idempotent operations.

```python
from core.consolidation.error_recovery import StateRollback

rollback = StateRollback(max_checkpoints=10)

# Save state before operation
original_state = {"version": 1, "count": 0}
ckpt = rollback.save_checkpoint(original_state, operation_id="op_001")

try:
    # Perform operation (may fail)
    new_state = await modify_state(original_state)
    rollback.commit_checkpoint(ckpt)
    return new_state
except Exception:
    # Restore to checkpoint on failure
    restored = rollback.restore_checkpoint(ckpt)
    return restored
```

### FallbackStrategy

Primary → fallback path selection with circuit breaker awareness.

```python
from core.consolidation.error_recovery import FallbackStrategy, FallbackConfig

config = FallbackConfig(
    name="api_with_fallback",
    fail_open=True,             # Degrade on error
    fallback_value={"cached": True},
    max_attempts=3,
)
strategy = FallbackStrategy(config, circuit_breaker)

result = await strategy.call_with_fallback(
    primary=lambda: api.fetch(),
    fallback=lambda: cache.get(),
)
```

**Behavior:**
- Calls primary with retry logic
- Falls back if primary retries exhausted
- If circuit is OPEN, uses fallback immediately
- `fail_open=True`: Returns fallback_value on all paths
- `fail_open=False`: Propagates errors, doesn't degrade

## Integration Example

Combining all patterns:

```python
from core.consolidation.self_healing import ExponentialBackoff, CircuitBreaker
from core.consolidation.error_recovery import (
    RetryLogic, FallbackStrategy, StateRollback, ErrorClassifier
)

# Setup
backoff_config = BackoffConfig(initial_delay_sec=0.1, max_attempts=3)
backoff = ExponentialBackoff(backoff_config)

breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5))

rollback = StateRollback()

strategy = FallbackStrategy(
    config=FallbackConfig(fail_open=True),
    circuit_breaker=breaker,
)

# Usage: save checkpoint, call with retry + fallback, restore on error
state = {"counter": 0}
ckpt = rollback.save_checkpoint(state, operation_id="complex_op")

try:
    result = await strategy.call_with_fallback(
        primary=lambda: external_api.process(state),
        fallback=lambda: cache.get_backup(),
    )
    rollback.commit_checkpoint(ckpt)
except Exception:
    restored_state = rollback.restore_checkpoint(ckpt)
    raise
```

## Compliance

**GDPR Art. 32:** Resilience and availability.

- Circuit breaker prevents cascading failures
- Exponential backoff prevents service overload
- Graceful degradation ensures user experience under load
- State rollback enables recovery from transient failures
- Error classification prevents retry storms

## Testing

Run all tests:

```bash
cd /home/shumway/projects/CorvinOS
python3 -m pytest tests/unit/test_self_healing.py -v
python3 -m pytest tests/unit/test_error_recovery.py -v
python3 tests/unit/test_consolidation_integration.py
```

Or run integration tests directly:

```bash
python3 tests/unit/test_consolidation_integration.py
```

**Coverage:** 46 tests, all scenarios from initialization to complex multi-pattern integration.

## Future Extensions

- **L35 Network Egress Lockdown:** Integrate with allowed/forbidden host lists
- **L36 GDPR Art. 17 Erasure:** State rollback for safe deletion
- **Async Context:** Propagate tenant_id through ContextVar during recovery
- **Metrics Export:** Publish circuit breaker state, retry rates to monitoring system
