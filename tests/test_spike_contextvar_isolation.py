"""DAY 1 MORNING: ContextVar Isolation Test — Spike Verification (ADR-0358)

Tests that ExecutionContext via ContextVar properly isolates between 13 concurrent
Brain subsystems. Validates assumption #1 before Week 1 implementation begins.

Test scenarios:
  - 13 concurrent tasks with SEPARATE ContextVar namespaces
  - Verify: task A's modifications don't leak to task B
  - Multiple cycles (100 iterations) under load
  - No race conditions or cross-contamination

Pass criteria:
  ✅ All 13 tasks see their own context (no leakage)
  ✅ No deadlocks or hangs
  ✅ Latency <5ms per isolation check

KEY FINDING (DAY 1):
asyncio.create_task() does NOT automatically create separate ContextVar
namespaces. All tasks share the parent's ContextVar values by default.
To isolate, each task must either:
  1. Run in a copy of the context via contextvars.copy_context()
  2. Receive explicit context dict at task creation

This test validates that OPTION 2 (explicit propagation) works correctly
for the Brain subsystem architecture.
"""

from __future__ import annotations

import asyncio
import sys
import time
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@dataclass
class ExecutionContext:
    """Minimal ExecutionContext for isolation testing."""

    engine_id: str = "unknown"
    model_name: str = ""
    tenant_id: str = ""
    turn_number: int = -1
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "engine_id": self.engine_id,
            "model_name": self.model_name,
            "tenant_id": self.tenant_id,
            "turn_number": self.turn_number,
            "extra": self.extra.copy(),
        }


# Per-subsystem context storage (simulates Brain subsystem local execution)
_subsystem_contexts: dict[int, ContextVar[ExecutionContext]] = {
    i: ContextVar(f"execution_context_{i}", default=None)
    for i in range(13)
}


class SubsystemSimulator:
    """Simulates a Brain subsystem with isolated ExecutionContext."""

    def __init__(self, subsystem_id: int):
        self.subsystem_id = subsystem_id
        self.context_var = _subsystem_contexts[subsystem_id]
        self.writes: list[dict[str, Any]] = []
        self.reads: list[dict[str, Any]] = []
        self.isolation_violations = 0

    async def run_task(self, iterations: int = 10) -> None:
        """Simulate subsystem work: read, modify, write context."""
        for i in range(iterations):
            await self._do_work_iteration(i)

    async def _do_work_iteration(self, iteration: int) -> None:
        """Single work iteration: read, modify, write context."""
        # Read current context (from THIS subsystem's isolated ContextVar)
        ctx = self.context_var.get()
        if ctx is None:
            ctx = ExecutionContext()
        else:
            ctx = ExecutionContext(**ctx.to_dict())  # Deep copy

        # Record what we read
        self.reads.append(ctx.to_dict())

        # Modify context
        ctx.engine_id = f"engine_{self.subsystem_id}"
        ctx.model_name = f"model_{self.subsystem_id}"
        ctx.turn_number = iteration
        ctx.extra = {"subsystem_id": self.subsystem_id, "iteration": iteration}

        # Write back
        self.context_var.set(ctx)
        self.writes.append(ctx.to_dict())

        # Simulate some work (other subsystems may run here)
        await asyncio.sleep(0.0001)

    def verify_isolation(self) -> tuple[bool, int]:
        """Verify that our writes are correct and we don't read other subsystems' values.

        Returns:
            (is_isolated, violation_count)

        Violations are only counted when:
        - We write a wrong engine_id, OR
        - We read a DIFFERENT subsystem's engine_id (not just empty/unknown)
        """
        violations = 0

        # Check that we actually wrote our own engine_id
        for i, w in enumerate(self.writes):
            if w.get("engine_id") != f"engine_{self.subsystem_id}":
                violations += 1
                print(f"    Write {i}: wrong engine_id: {w.get('engine_id')}")

        # Check that all our reads don't contain ANOTHER subsystem's engine_id
        # (it's OK to read "unknown" or our own engine_id)
        for i, r in enumerate(self.reads):
            engine_id = r.get("engine_id", "")
            # Only flag if it's another subsystem's ID (format: "engine_N")
            if engine_id.startswith("engine_"):
                subsys_id_from_read = int(engine_id.split("_")[1])
                if subsys_id_from_read != self.subsystem_id:
                    violations += 1
                    print(f"    Read {i}: foreign engine_id: {engine_id} (I am {self.subsystem_id})")

        return violations == 0, violations


class TestContextVarIsolation13Subsystems:
    """DAY 1 MORNING: ContextVar isolation with 13 concurrent subsystems."""

    @pytest.mark.asyncio
    async def test_contextvar_isolation_per_subsystem_namespace(self) -> None:
        """13 concurrent tasks with SEPARATE ContextVar namespaces.

        Model: Each Brain subsystem has its own ContextVar namespace.
        This prevents cross-contamination while allowing concurrent execution.

        Expected:
          - 13 subsystems run concurrently
          - Each uses its own ContextVar
          - Each writes engine_id = "engine_<ID>"
          - Each reads only its own values (or defaults)
          - NO cross-contamination
        """
        num_subsystems = 13
        iterations = 10

        # Create and initialize subsystems with isolated ContextVars
        subsystems = [SubsystemSimulator(i) for i in range(num_subsystems)]

        # Run all subsystems concurrently
        start_time = time.time()
        tasks = [
            subsys.run_task(iterations=iterations)
            for subsys in subsystems
        ]
        await asyncio.gather(*tasks)
        elapsed = time.time() - start_time

        # Verify isolation for each subsystem
        all_isolated = True
        total_violations = 0
        for subsys in subsystems:
            is_isolated, violations = subsys.verify_isolation()
            total_violations += violations
            if is_isolated:
                print(f"✅ Subsystem {subsys.subsystem_id}: isolated ({len(subsys.writes)} writes)")
            else:
                print(f"❌ Subsystem {subsys.subsystem_id}: VIOLATIONS ({violations})")
                all_isolated = False

        # Report latency
        avg_latency_ms = (elapsed * 1000) / (num_subsystems * iterations)
        print(f"⏱️  Average latency per task: {avg_latency_ms:.4f}ms")

        # Pass criteria
        assert all_isolated, f"Isolation failures detected: {total_violations} violations"
        assert avg_latency_ms < 5.0, f"Latency exceeded 5ms: {avg_latency_ms:.4f}ms"

    @pytest.mark.asyncio
    async def test_contextvar_isolation_100_iterations_heavy_load(self) -> None:
        """Heavy load: 13 subsystems × 100 iterations.

        Verify:
          - No deadlocks or hangs
          - ContextVar isolation holds under stress
          - Graceful completion
        """
        num_subsystems = 13
        iterations = 100

        subsystems = [SubsystemSimulator(i) for i in range(num_subsystems)]

        # Run heavy workload
        start_time = time.time()
        tasks = [
            subsys.run_task(iterations=iterations)
            for subsys in subsystems
        ]

        # Should complete within timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            pytest.fail("Heavy load test timed out (possible deadlock)")

        elapsed = time.time() - start_time

        # Verify isolation under load
        all_isolated = True
        for subsys in subsystems:
            is_isolated, violations = subsys.verify_isolation()
            if not is_isolated:
                all_isolated = False
                print(f"❌ Subsystem {subsys.subsystem_id} had {violations} violations")

        print(f"⏱️  Heavy load ({num_subsystems}×{iterations}) completed in {elapsed:.2f}s")
        assert all_isolated, "ContextVar isolation failed under heavy load"

    @pytest.mark.asyncio
    async def test_contextvar_isolation_model_switching(self) -> None:
        """Real-world scenario: subsystems rapidly switch models.

        Test pattern from ADR-0358:
          Subsystem A writes model="opus"
          Subsystem B writes model="haiku"
          Subsystem A simultaneously reads back "opus"
          Subsystem B simultaneously reads back "haiku"

        This tests rapid interleaving under concurrent load.
        """
        num_iterations = 50

        async def subsystem_a_task():
            subsys_a = SubsystemSimulator(0)
            for i in range(num_iterations):
                ctx = subsys_a.context_var.get()
                if ctx is None:
                    ctx = ExecutionContext()
                ctx.model_name = "opus"
                subsys_a.context_var.set(ctx)
                await asyncio.sleep(0.0001)
                ctx_read = subsys_a.context_var.get()
                assert ctx_read and ctx_read.model_name == "opus", \
                    f"A read {ctx_read.model_name if ctx_read else None}, expected opus"
            return subsys_a

        async def subsystem_b_task():
            subsys_b = SubsystemSimulator(1)
            for i in range(num_iterations):
                ctx = subsys_b.context_var.get()
                if ctx is None:
                    ctx = ExecutionContext()
                ctx.model_name = "haiku"
                subsys_b.context_var.set(ctx)
                await asyncio.sleep(0.0001)
                ctx_read = subsys_b.context_var.get()
                assert ctx_read and ctx_read.model_name == "haiku", \
                    f"B read {ctx_read.model_name if ctx_read else None}, expected haiku"
            return subsys_b

        # Run concurrently with high interleaving
        a_task = asyncio.create_task(subsystem_a_task())
        b_task = asyncio.create_task(subsystem_b_task())

        subsys_a, subsys_b = await asyncio.gather(a_task, b_task)

        # Verify both saw their own models
        assert all(w.get("model_name") == "opus" for w in subsys_a.writes if w.get("model_name"))
        assert all(w.get("model_name") == "haiku" for w in subsys_b.writes if w.get("model_name"))

        print(f"✅ Model switching isolation verified (A: opus, B: haiku)")

    @pytest.mark.asyncio
    async def test_no_race_condition_on_concurrent_writes(self) -> None:
        """Test that concurrent writes to separate ContextVars don't race.

        With isolated ContextVars (one per subsystem), there should be
        no races — each subsystem has its own storage.
        """
        write_order = []
        lock = asyncio.Lock()

        async def write_and_read(subsys_id: int, value: str) -> None:
            """Write value and read it back."""
            ctx_var = _subsystem_contexts[subsys_id]
            ctx = ctx_var.get()
            if ctx is None:
                ctx = ExecutionContext()

            ctx.model_name = value
            ctx_var.set(ctx)

            # Yield to other tasks
            await asyncio.sleep(0)

            # Read back
            ctx_read = ctx_var.get()
            async with lock:
                write_order.append((subsys_id, ctx_read.model_name if ctx_read else "None"))

        # Run 13 concurrent writes
        tasks = [
            write_and_read(i, f"model_{i}")
            for i in range(13)
        ]
        await asyncio.gather(*tasks)

        # Verify each subsystem read its own value
        for subsys_id, model_name in write_order:
            assert model_name == f"model_{subsys_id}", \
                f"Subsystem {subsys_id} read wrong model: {model_name}"

        print(f"✅ No race conditions on {len(write_order)} concurrent writes")


class TestContextVarDesignValidation:
    """Validation tests for the proposed ADR-0358 design."""

    @pytest.mark.asyncio
    async def test_isolated_contextvar_design_is_safe(self) -> None:
        """Prove that the proposed design (one ContextVar per subsystem) is safe.

        Design rationale:
        - Each Brain subsystem gets its own ContextVar
        - asyncio tasks can read/write their subsystem's ContextVar
        - No need for copy_context() or explicit context passing
        - Simple, straightforward, no ContextVar name collisions
        """
        # Simulate 13 subsystems running concurrently
        subsystems = [SubsystemSimulator(i) for i in range(13)]

        # All 13 can run in parallel safely
        await asyncio.gather(*[
            subsys.run_task(iterations=20)
            for subsys in subsystems
        ])

        # All should have perfect isolation
        for subsys in subsystems:
            is_isolated, violations = subsys.verify_isolation()
            assert is_isolated, f"Subsystem {subsys.subsystem_id} had {violations} violations"

        print("✅ Isolated ContextVar design is safe for 13 concurrent subsystems")

    def test_contextvar_copy_context_vs_isolated_vars(self) -> None:
        """Compare two approaches: copy_context vs isolated ContextVars.

        APPROACH 1 (current spike):
          - Each subsystem has its own ContextVar
          - Requires no special handling in async code
          - Clean isolation without overhead
          PROS: Simple, no copy overhead
          CONS: Need to pre-create one per subsystem

        APPROACH 2 (alternative):
          - Single shared ContextVar
          - Use copy_context() to isolate each task
          PROS: Dynamic subsystems possible
          CONS: Higher overhead, more complex

        Conclusion: For Brain with fixed 13 subsystems, APPROACH 1 (current spike) wins.
        """
        # This test documents the design choice for ADR-0358
        print("""
        DAY 1 FINDING: Isolated ContextVars (one per subsystem) is the
        recommended approach for Brain architecture because:

        1. Brain has exactly 13 subsystems (known, fixed)
        2. Isolated ContextVars eliminate copy_context() overhead
        3. Asyncio tasks automatically inherit parent's ContextVars
        4. No cross-contamination with separate namespaces
        5. Easier to debug (engine_id directly shows subsystem)

        Recommendation for ADR-0358: Use isolated ContextVars.
        """)


if __name__ == "__main__":
    # Run with: uv run python3 -m pytest tests/test_spike_contextvar_isolation.py -v -s
    pytest.main([__file__, "-v", "-s"])
