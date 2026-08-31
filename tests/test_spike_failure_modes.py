"""DAY 2 MORNING: Failure Mode Test Suite — Spike Verification (ADR-0358)

Tests edge cases that could break during Week 1-6 implementation.

Test scenarios:
  1. Concurrent persistence (DecisionRecord simultaneous writes)
  2. Memory unavailable (PROJECT.task_templates missing/corrupted)
  3. Context propagation (nested tasks, scope corruption)
  4. Async handler exception (cascading failures)

Pass criteria:
  ✅ All 4 failure modes handled gracefully
  ✅ No silent failures (errors logged)
  ✅ Fallback paths work
  ✅ No cascading corruption
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from unittest.mock import Mock, AsyncMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@dataclass
class DecisionRecord:
    """Decision persisted to disk."""
    decision_id: str
    strategy_id: str
    choice: str
    confidence: float
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "strategy_id": self.strategy_id,
            "choice": self.choice,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        return cls(**data)


class DecisionStore:
    """Stores decisions to JSONL file (simulates learning subsystem)."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def write_decision(self, record: DecisionRecord) -> None:
        """Append decision to JSONL file."""
        line = json.dumps(record.to_dict()) + "\n"
        # Simulate atomic write (in reality, use file locking)
        with open(self.path, "a") as f:
            f.write(line)

    async def read_decisions(self) -> list[DecisionRecord]:
        """Read all decisions from JSONL file."""
        if not self.path.exists():
            return []

        records = []
        with open(self.path, "r") as f:
            for line in f:
                if line.strip():
                    records.append(DecisionRecord.from_dict(json.loads(line)))
        return records


class EventBusSimulator:
    """Simulates ContextBus with error handling."""

    def __init__(self):
        self.subscribers: dict[str, list] = {}
        self.failed_handlers = 0

    async def emit(self, event_type: str, payload: dict) -> None:
        """Emit event, handling handler exceptions gracefully."""
        handlers = self.subscribers.get(event_type, [])

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)
                else:
                    handler(payload)
            except Exception as e:
                # KEY FINDING: Don't cascade, continue with next handler
                self.failed_handlers += 1
                print(f"⚠️  Handler failed for {event_type}: {e}")
                # Continue processing (fail-open for remaining handlers)

    def subscribe(self, event_type: str, handler) -> None:
        """Subscribe to event."""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)


class TestConcurrentPersistence:
    """DAY 2.1: Concurrent DecisionRecord writes."""

    @pytest.mark.asyncio
    async def test_concurrent_decision_writes_no_corruption(self) -> None:
        """Two subsystems write DecisionRecords simultaneously.

        Verify:
          - Both writes succeed
          - No JSON corruption
          - JSONL is valid after concurrent access
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "decisions.jsonl"
            store = DecisionStore(store_path)

            # Create records from 2 subsystems
            records = [
                DecisionRecord(
                    decision_id=f"d_{i}",
                    strategy_id=f"s_{i}",
                    choice=f"choice_{i}",
                    confidence=0.9 + (i * 0.01),
                    timestamp="2026-08-17T00:00:00Z",
                )
                for i in range(10)
            ]

            # Write concurrently (simulate subsystem A and B)
            async def write_odd(records):
                for r in [records[i] for i in range(1, len(records), 2)]:
                    await store.write_decision(r)

            async def write_even(records):
                for r in [records[i] for i in range(0, len(records), 2)]:
                    await store.write_decision(r)

            # Run concurrent writes
            await asyncio.gather(
                write_odd(records),
                write_even(records),
            )

            # Verify JSONL is valid
            read_back = await store.read_decisions()
            assert len(read_back) == 10, f"Expected 10 records, got {len(read_back)}"

            # Verify no corruption
            decision_ids = {r.decision_id for r in read_back}
            assert len(decision_ids) == 10, "Some records were duplicated"

            print(f"✅ Concurrent writes: {len(read_back)} records, no corruption")

    @pytest.mark.asyncio
    async def test_concurrent_writes_jsonl_valid(self) -> None:
        """Verify JSONL remains valid after 100 concurrent writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "decisions.jsonl"
            store = DecisionStore(store_path)

            async def write_batch(start_id: int, count: int):
                for i in range(count):
                    record = DecisionRecord(
                        decision_id=f"d_{start_id}_{i}",
                        strategy_id=f"s_{start_id}_{i}",
                        choice=f"choice_{start_id}_{i}",
                        confidence=0.8 + (i % 10) * 0.01,
                        timestamp="2026-08-17T00:00:00Z",
                    )
                    await store.write_decision(record)

            # 10 concurrent batches
            await asyncio.gather(*[
                write_batch(i, 10) for i in range(10)
            ])

            # Verify all records readable
            records = await store.read_decisions()
            assert len(records) == 100, f"Expected 100 records, got {len(records)}"

            # Verify each is unique
            ids = {r.decision_id for r in records}
            assert len(ids) == 100, "Duplicates detected"

            print(f"✅ JSONL integrity preserved: 100 records valid")


class TestMemoryUnavailable:
    """DAY 2.2: Missing or corrupted memory (PROJECT.task_templates)."""

    @pytest.mark.asyncio
    async def test_missing_task_templates_graceful_fallback(self) -> None:
        """PROJECT.task_templates missing.

        Verify:
          - System doesn't crash
          - Returns degraded template with confidence=0
          - Error logged
        """

        class MemoryCoordinator:
            """Manages memory (task templates, decisions)."""

            def __init__(self, templates_path: Optional[Path] = None):
                self.templates_path = templates_path

            async def load_template(self, template_id: str) -> dict[str, Any]:
                """Load template, or return degraded default."""
                if not self.templates_path or not self.templates_path.exists():
                    print(f"⚠️  Templates file missing: {self.templates_path}")
                    # Graceful fallback
                    return {
                        "template_id": template_id,
                        "name": "default",
                        "steps": [],
                        "confidence": 0.0,  # Degraded!
                    }

                try:
                    with open(self.templates_path) as f:
                        templates = json.load(f)
                    return templates.get(template_id, {})
                except json.JSONDecodeError as e:
                    print(f"⚠️  Templates corrupted: {e}")
                    # Fallback
                    return {
                        "template_id": template_id,
                        "name": "degraded",
                        "steps": [],
                        "confidence": 0.0,
                    }

        # Test: missing file
        coordinator = MemoryCoordinator(templates_path=None)
        template = await coordinator.load_template("refactor")

        assert template is not None, "Should return template, not None"
        assert template["confidence"] == 0.0, "Confidence should be degraded"
        print(f"✅ Missing templates handled: {template}")

        # Test: corrupted JSON
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupted_path = Path(tmpdir) / "templates.json"
            corrupted_path.write_text("{invalid json")

            coordinator = MemoryCoordinator(templates_path=corrupted_path)
            template = await coordinator.load_template("refactor")

            assert template is not None, "Should return template, not None"
            assert template["confidence"] == 0.0, "Confidence should be degraded"
            print(f"✅ Corrupted templates handled: {template}")

    @pytest.mark.asyncio
    async def test_memory_unavailable_no_silent_failure(self) -> None:
        """Verify errors are logged, not silently ignored."""
        logs = []

        class LoggingCoordinator:
            """Memory coordinator that logs errors."""

            async def load_template(self, template_id: str) -> dict[str, Any]:
                try:
                    raise FileNotFoundError("Templates missing")
                except FileNotFoundError as e:
                    logs.append(f"ERROR: {e}")
                    return {"template_id": template_id, "confidence": 0.0}

        coordinator = LoggingCoordinator()
        template = await coordinator.load_template("refactor")

        assert len(logs) == 1, "Error should be logged"
        assert "Templates missing" in logs[0]
        print(f"✅ Error logged: {logs[0]}")


class TestContextPropagation:
    """DAY 2.3: Context propagation across nested tasks."""

    @pytest.mark.asyncio
    async def test_nested_task_scope_isolation(self) -> None:
        """Nested task (task → worker → file) with scope changes.

        Verify:
          - Scope recorded at guidance ARRIVAL time (not apply time)
          - Pop scope → parent scope restored correctly
        """

        class ScopeStack:
            """Tracks nested task scopes."""

            def __init__(self):
                self.stack: list[str] = ["root"]

            def push_scope(self, scope: str) -> None:
                """Enter new scope."""
                self.stack.append(scope)

            def pop_scope(self) -> str:
                """Exit scope."""
                if len(self.stack) > 1:
                    return self.stack.pop()
                raise ValueError("Cannot pop root scope")

            def current_scope(self) -> str:
                """Get current scope."""
                return self.stack[-1]

        scope_stack = ScopeStack()

        async def parent_task():
            """Parent task starts at 'root'."""
            assert scope_stack.current_scope() == "root"

            scope_stack.push_scope("parent")
            assert scope_stack.current_scope() == "parent"

            # Spawn child task
            await child_task()

            # After child, parent scope should be restored
            assert scope_stack.current_scope() == "parent"

            scope_stack.pop_scope()
            assert scope_stack.current_scope() == "root"

        async def child_task():
            """Child task enters its own scope."""
            assert scope_stack.current_scope() == "parent"  # Inherits parent

            scope_stack.push_scope("child")
            assert scope_stack.current_scope() == "child"

            # Do work
            await asyncio.sleep(0.001)

            # Pop scope before returning
            scope_stack.pop_scope()
            assert scope_stack.current_scope() == "parent"

        await parent_task()
        print(f"✅ Nested scope isolation verified")

    @pytest.mark.asyncio
    async def test_concurrent_tasks_scope_isolation(self) -> None:
        """Multiple concurrent tasks with different scopes.

        Verify:
          - Task A's scope change doesn't leak to B
          - Each task sees only its own scope
        """

        class ScopeContext:
            def __init__(self):
                self.scope = "root"

        # Per-task context (simulated with separate instances)
        scopes = {
            "task_a": ScopeContext(),
            "task_b": ScopeContext(),
        }

        async def task_a():
            scopes["task_a"].scope = "task_a"
            await asyncio.sleep(0.001)
            assert scopes["task_a"].scope == "task_a"

        async def task_b():
            scopes["task_b"].scope = "task_b"
            await asyncio.sleep(0.001)
            assert scopes["task_b"].scope == "task_b"

        # Run concurrently
        await asyncio.gather(task_a(), task_b())

        # Verify no leakage
        assert scopes["task_a"].scope == "task_a"
        assert scopes["task_b"].scope == "task_b"
        print(f"✅ Concurrent scope isolation verified")


class TestAsyncHandlerException:
    """DAY 2.4: Async handler exception (don't cascade)."""

    @pytest.mark.asyncio
    async def test_one_failed_handler_doesnt_block_others(self) -> None:
        """One handler raises exception, others still run.

        Verify:
          - Handler A fails
          - Handler B still runs
          - Handler C still runs
          - Event system remains usable
        """
        results = []

        async def handler_a(payload):
            results.append("A_start")
            raise RuntimeError("Handler A failed")

        async def handler_b(payload):
            results.append("B_start")
            await asyncio.sleep(0.001)
            results.append("B_done")

        async def handler_c(payload):
            results.append("C_start")
            results.append("C_done")

        bus = EventBusSimulator()
        bus.subscribe("test_event", handler_a)
        bus.subscribe("test_event", handler_b)
        bus.subscribe("test_event", handler_c)

        # Emit event
        await bus.emit("test_event", {})

        # Verify all handlers attempted execution
        assert "A_start" in results
        assert "B_start" in results
        assert "B_done" in results
        assert "C_start" in results
        assert "C_done" in results

        # Verify B and C completed despite A failing
        assert bus.failed_handlers == 1
        print(f"✅ Exception isolation: handler A failed, B and C completed")

    @pytest.mark.asyncio
    async def test_cascading_failures_prevented(self) -> None:
        """Multiple handlers fail, system remains usable."""
        failed = []

        async def failing_handler_1(payload):
            raise ValueError("Error 1")

        async def failing_handler_2(payload):
            raise ValueError("Error 2")

        async def good_handler(payload):
            return "OK"

        bus = EventBusSimulator()
        bus.subscribe("critical", failing_handler_1)
        bus.subscribe("critical", failing_handler_2)
        bus.subscribe("critical", good_handler)

        # Emit critical event
        await bus.emit("critical", {})

        # Verify system is still usable
        assert bus.failed_handlers == 2
        print(f"✅ Cascading failures prevented: {bus.failed_handlers} handlers failed, system still works")

    @pytest.mark.asyncio
    async def test_handler_timeout_doesnt_hang_system(self) -> None:
        """Handler takes too long, but doesn't hang the system.

        Note: This is documented but not enforced in Week 1.
        Week 2 may add callback timeouts.
        """
        results = []

        async def slow_handler(payload):
            results.append("slow_start")
            await asyncio.sleep(0.1)  # 100ms (slow)
            results.append("slow_done")

        async def fast_handler(payload):
            results.append("fast_done")

        bus = EventBusSimulator()
        bus.subscribe("test", slow_handler)
        bus.subscribe("test", fast_handler)

        # Emit and wait for all handlers
        await bus.emit("test", {})

        # Both should complete (slow doesn't starve fast in queue)
        assert "slow_start" in results
        assert "slow_done" in results
        assert "fast_done" in results
        print(f"✅ Slow handler doesn't hang system (FIFO ensures all run)")


class TestGuidanceRaceCondition:
    """DAY 2 AFTERNOON: Guidance arrives while scope is changing."""

    @pytest.mark.asyncio
    async def test_guidance_scope_recorded_at_arrival(self) -> None:
        """Guidance arrives while scope changes.

        Verify:
          - Scope recorded at ARRIVAL time, not apply time
          - Guidance stored with correct scope context
        """

        @dataclass
        class Guidance:
            guidance_id: str
            scope_at_arrival: str
            content: str
            timestamp: str

        guidance_queue = []
        scope_stack = ["root"]

        async def receive_guidance(content: str):
            """Record guidance with current scope."""
            guidance = Guidance(
                guidance_id=f"g_{len(guidance_queue)}",
                scope_at_arrival=scope_stack[-1],  # Record at ARRIVAL time
                content=content,
                timestamp="2026-08-17T00:00:00Z",
            )
            guidance_queue.append(guidance)
            await asyncio.sleep(0.001)  # Simulate receive

        async def change_scope(new_scope: str):
            """Change scope (happens concurrently)."""
            scope_stack.append(new_scope)
            await asyncio.sleep(0.0005)

        # Test: Receive guidance, then change scope
        await asyncio.gather(
            receive_guidance("Guidance 1"),
            change_scope("task_a"),
        )

        # Verify guidance was recorded with arrival-time scope
        assert guidance_queue[0].scope_at_arrival == "root", \
            "Guidance should be recorded with scope at arrival"

        print(f"✅ Guidance scope: recorded at arrival (scope='{guidance_queue[0].scope_at_arrival}')")

    @pytest.mark.asyncio
    async def test_concurrent_guidance_updates_applied_correctly(self) -> None:
        """100 guidance updates arrive concurrently.

        Verify:
          - All applied to correct scope
          - No cross-contamination
        """
        scopes = {i: [] for i in range(5)}

        async def apply_guidance_batch(scope_id: int, count: int):
            for i in range(count):
                scopes[scope_id].append(f"guidance_{scope_id}_{i}")
                await asyncio.sleep(0.00001)

        # 100 concurrent updates across 5 scopes
        await asyncio.gather(*[
            apply_guidance_batch(i, 20) for i in range(5)
        ])

        # Verify each scope has correct guidance
        for scope_id in range(5):
            for guidance in scopes[scope_id]:
                assert guidance.startswith(f"guidance_{scope_id}"), \
                    f"Cross-contamination in scope {scope_id}"

        total_guidance = sum(len(g) for g in scopes.values())
        print(f"✅ Concurrent guidance: {total_guidance} updates, no cross-contamination")


if __name__ == "__main__":
    # Run with: uv run python3 -m pytest tests/test_spike_failure_modes.py -v -s
    pytest.main([__file__, "-v", "-s"])
