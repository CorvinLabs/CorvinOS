# Event Ordering Contract for Brain Subsystem Hub — ADR-0358 (DAY 1)

**Status:** Design Document (Spike Verification)  
**Date:** 2026-08-17  
**Context:** ADR-0358 Event Ordering § Design Details  
**Conclusion:** FIFO Sequential Processing Selected

---

## Executive Summary

The Brain subsystem Hub must guarantee deterministic event ordering to prevent race conditions and state corruption. This document evaluates two design options and selects the **FIFO Sequential** approach for Week 1 implementation.

**Selected Design:** Sequential FIFO event processing (atomic, deterministic, fail-safe)

---

## Problem Statement

When multiple Brain subsystems emit events concurrently:

```
LoopEngineer: "strategy_succeeded"
CostController: "budget_updated"      ← Concurrent emission
HealthMonitor: "error_detected"
```

Questions:
1. In what order do subsystems see these events?
2. Can CostController read a stale budget from before LoopEngineer's strategy completed?
3. If HealthMonitor reads error state while CostController is updating budget, do we get corruption?

The contract must define: **what ordering guarantee does the Hub provide?**

---

## Option A: FIFO Sequential Processing (SELECTED)

### Design

```python
class ContextBus:
    """Event bus with sequential (FIFO) processing."""

    def __init__(self):
        self.event_queue = asyncio.Queue()  # FIFO
        self.worker_task = None
        self._subscribers: dict[str, list] = {}  # event_type → [callbacks]

    async def start(self):
        """Start the event processing worker."""
        self.worker_task = asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        """Process events sequentially (FIFO order)."""
        while True:
            event_type, payload = await self.event_queue.get()
            
            # SEQUENTIAL: process one event at a time
            for callback in self._subscribers.get(event_type, []):
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(payload)  # Await completion
                    else:
                        callback(payload)
                except Exception as e:
                    logger.error(f"Callback failed for {event_type}: {e}")
                    # Continue with next callback (don't cascade)

    async def emit(self, event_type: str, payload: Any) -> None:
        """Emit an event (non-blocking).
        
        Events are queued and processed in FIFO order by the worker.
        """
        await self.event_queue.put((event_type, payload))

    def subscribe(self, event_type: str, callback):
        """Subscribe to an event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
```

### Contract Guarantee

**All events are processed in strict FIFO order.** If event A is emitted before event B, all subscribers see A's effects before B is processed.

```
Timeline:
  T0: LoopEngineer emits "strategy_succeeded"
  T1: LoopEngineer.emit() returns (queued, not processed yet)
  T2: CostController emits "budget_updated"
  T3: HealthMonitor emits "error_detected"
  
  Processing (sequential):
  T4: strategy_succeeded callback runs (LoopEngineer.on_success)
       → updates subsystem state
       → returns
  T5: budget_updated callback runs
       → reads latest strategy success state
       → updates cost model
  T6: error_detected callback runs
       → reads latest cost state
       → logs error with full context
```

### Pros

✅ **Deterministic:** Order is predictable and repeatable  
✅ **Safe:** No concurrent access to shared state (single worker thread)  
✅ **Simple:** No locks, atomics, or synchronization primitives  
✅ **Debuggable:** Trace shows exact event order  
✅ **Fail-closed:** If one callback crashes, others still run (error logged)  

### Cons

⚠️ **Latency:** Slow callbacks block the queue  
⚠️ **Throughput:** Can't leverage multiple cores for event processing

### Mitigation

- Keep callbacks fast (<100ms)
- Move slow I/O to background tasks (emit event, don't await I/O)
- Use asyncio for scalability (one worker can handle many events via await)

---

## Option B: Concurrent Handlers + Atomic State (NOT SELECTED)

### Design

```python
class ContextBusAtomic:
    """Event bus with concurrent handlers and atomic state updates."""

    def __init__(self):
        self.event_queue = asyncio.Queue()
        self.workers = [asyncio.create_task(self._worker()) for _ in range(4)]
        self.state_lock = asyncio.Lock()  # Protects shared state

    async def _worker(self):
        """Worker: process events concurrently."""
        while True:
            event_type, payload = await self.event_queue.get()
            try:
                async with self.state_lock:
                    for callback in self._subscribers.get(event_type, []):
                        if asyncio.iscoroutinefunction(callback):
                            await callback(payload)
                        else:
                            callback(payload)
            except Exception as e:
                logger.error(f"Callback failed: {e}")

    async def emit(self, event_type: str, payload: Any) -> None:
        """Emit an event (fire-and-forget)."""
        await self.event_queue.put((event_type, payload))
```

### Contract Guarantee

**Events are processed concurrently, but critical sections are atomic.** State updates hold the lock, but handler interleaving is unordered.

### Pros

✅ **Higher throughput:** Multiple workers can handle events in parallel  
✅ **Lower latency:** Slow event doesn't block others  

### Cons

❌ **Non-deterministic:** Event order is unpredictable  
❌ **Complex:** Requires careful lock analysis  
❌ **Race-prone:** Easy to miss a critical section  
❌ **Debugging hard:** Flaky tests, intermittent failures  
❌ **Deadlock risk:** Multiple locks → potential circular waits  

---

## Recommendation: FIFO Sequential (Option A)

### Why FIFO Wins for Brain

1. **13 subsystems, not 1000:** Throughput isn't the bottleneck
2. **Events are metadata, not data:** Tiny payloads, fast processing
3. **Correctness > speed:** Brain must be reliable, not fast
4. **Debuggability:** Linear event trace makes diagnosis easy
5. **Maintenance:** No locks to reason about

### Implementation Schedule (Week 1)

**ContextBus (ADR-0347 update):**
```python
class SubsystemHub:
    def __init__(self, max_event_queue_size=10000):
        self.event_bus = ContextBus(max_queue_size=max_event_queue_size)
        self.subsystems: dict[str, Subsystem] = {}

    async def run_forever(self, poll_interval_s=5.0):
        await self.event_bus.start()  # Start event worker
        while not self.stop_requested:
            # Poll subsystems, emit events
            await asyncio.sleep(poll_interval_s)

    async def emit_event(self, event_type: str, payload: dict):
        await self.event_bus.emit(event_type, payload)
```

**Event types (ADR-0347, LoopEngineer):**
```
strategy_started
strategy_succeeded
strategy_failed
strategy_escalated
budget_updated
error_detected
health_check_passed
health_check_failed
```

---

## Test Coverage (Week 1)

```python
class TestEventOrdering:
    @pytest.mark.asyncio
    async def test_fifo_order_preserved(self):
        """Events processed in emission order."""
        # Emit A, B, C → observe callbacks in order

    @pytest.mark.asyncio
    async def test_callback_crash_doesnt_cascade(self):
        """One failed callback doesn't block others."""
        # A callback raises → next callbacks still run

    @pytest.mark.asyncio
    async def test_state_consistency_under_load(self):
        """Concurrent emissions don't corrupt state."""
        # 1000 events, 13 subsystems, no races
```

---

## Edge Cases

### Queue Full

If `max_queue_size` exceeded:
- **Action:** Log warning, emit event anyway (unbounded queue)
- **Reason:** Better to process late than lose events entirely
- **Monitor:** Alert ops if queue grows beyond 1000

### Callback Timeout

If callback takes >5 seconds:
- **Action:** Continue processing (don't cancel)
- **Reason:** Slow subsystem shouldn't starve others
- **Monitor:** Warn in logs, track slow callback duration

### Worker Task Crashed

If event worker crashes:
- **Action:** `run_forever()` detects and restarts
- **Reason:** Hub must be resilient
- **Test:** Inject exception, verify restart

---

## Migration Path

**Phase 1 (Week 1):** Implement ContextBus with FIFO  
**Phase 2 (Week 2):** Wire into all 8 subsystems  
**Phase 3 (Week 3):** Add metrics (queue depth, callback latency)  
**Phase 4 (Week 5):** E2E test with real workloads

---

## Conclusion

**FIFO Sequential Processing (Option A)** is selected for ADR-0358 Event Ordering.

**Ready for Week 1 implementation.**

✅ **Pass Criteria Met:**
- [x] Contract is clear (FIFO, deterministic)
- [x] Implementation is straightforward
- [x] Edge cases documented
- [x] Test plan defined
- [x] No unforeseen complexity
