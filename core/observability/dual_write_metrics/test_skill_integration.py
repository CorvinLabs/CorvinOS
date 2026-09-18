#!/usr/bin/env python3
"""Tests for skill execution integration (decorators + context managers).

Verifies metrics are automatically emitted after skill execution.
"""

import sys
import tempfile
import time
from pathlib import Path

from collector import DualWriteMetricsCollector, SkillExecutionStatus
from skill_execution_integration import (
    SkillMetricsContext,
    track_skill_execution,
    SkillExecutionMetricsCollector,
)

print("=" * 60)
print("TEST 1: SkillMetricsContext Manager")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "test.db"
    collector = DualWriteMetricsCollector(db_path=db_path, otel_enabled=False)

    ctx = SkillMetricsContext(
        skill_id="os.router",
        skill_version="1.0.0",
        tenant_id="test_tenant",
        collector=collector,
    )

    # Simulate skill execution
    with ctx.track():
        time.sleep(0.01)  # Simulate 10ms execution
        ctx.tokens_used = 150
        ctx.convergence_score = 0.85

    # Verify metrics recorded
    metrics = collector.get_unexported_metrics()
    assert len(metrics) == 1
    assert metrics[0]["skill_id"] == "os.router"
    assert metrics[0]["tokens_used"] == 150
    assert metrics[0]["convergence_score"] == 0.85
    assert metrics[0]["status"] == "success"

    duration_ms = metrics[0]["execution_duration_ms"]
    assert 8.0 < duration_ms < 50.0, f"Duration {duration_ms}ms out of range"
    print(f"✅ Context manager works: {duration_ms:.1f}ms execution recorded")

print("\n" + "=" * 60)
print("TEST 2: Error Handling in Context")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "test.db"
    collector = DualWriteMetricsCollector(db_path=db_path, otel_enabled=False)

    ctx = SkillMetricsContext(
        skill_id="os.failed_skill",
        skill_version="1.0.0",
        tenant_id="test_tenant",
        collector=collector,
    )

    try:
        with ctx.track():
            time.sleep(0.01)
            raise ValueError("Something went wrong")
    except ValueError:
        pass  # Expected

    # Verify error metrics recorded
    metrics = collector.get_unexported_metrics()
    assert len(metrics) == 1
    assert metrics[0]["status"] == "error"
    assert metrics[0]["error_type"] == "ValueError"
    assert "Something went wrong" in metrics[0]["error_message"]
    print(f"✅ Error handling works: status={metrics[0]['status']}, error_type={metrics[0]['error_type']}")

print("\n" + "=" * 60)
print("TEST 3: Decorator for Skill Functions")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "test.db"
    collector = DualWriteMetricsCollector(db_path=db_path, otel_enabled=False)

    # Get the global collector and replace it
    import core.observability.dual_write_metrics.collector as collector_module
    original_collector = collector_module._collector
    collector_module._collector = collector

    @track_skill_execution("os.router", "1.0.0")
    def router_skill(request, tenant_id="_default"):
        time.sleep(0.01)
        return {"result": "routed", "tokens_used": 200}

    # Call the decorated function
    result = router_skill({"query": "test"}, tenant_id="test_tenant")
    assert result["result"] == "routed"
    assert result["tokens_used"] == 200

    # Verify metrics
    metrics = collector.get_unexported_metrics()
    assert len(metrics) == 1
    assert metrics[0]["skill_id"] == "os.router"
    assert metrics[0]["status"] == "success"
    print(f"✅ Decorator works: {len(metrics)} metric recorded")

    # Restore
    collector_module._collector = original_collector

print("\n" + "=" * 60)
print("TEST 4: SkillExecutionMetricsCollector High-Level API")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "test.db"
    base_collector = DualWriteMetricsCollector(db_path=db_path, otel_enabled=False)
    metrics_collector = SkillExecutionMetricsCollector(collector=base_collector)

    # Execute skill with context manager
    with metrics_collector.execute("os.context_adapter", "test_tenant") as ctx:
        time.sleep(0.01)
        ctx.tokens_used = 250
        ctx.input_size_bytes = 2048
        ctx.output_size_bytes = 1024

    # Verify metrics
    metrics = base_collector.get_unexported_metrics()
    assert len(metrics) == 1
    assert metrics[0]["skill_id"] == "os.context_adapter"
    assert metrics[0]["tokens_used"] == 250
    assert metrics[0]["input_size_bytes"] == 2048
    assert metrics[0]["output_size_bytes"] == 1024
    print(f"✅ High-level API works: {len(metrics)} metric recorded with all fields")

print("\n" + "=" * 60)
print("TEST 5: PII Scrubbing in Error Messages")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "test.db"
    collector = DualWriteMetricsCollector(db_path=db_path, otel_enabled=False)

    ctx = SkillMetricsContext(
        skill_id="os.pii_test",
        skill_version="1.0.0",
        tenant_id="test_tenant",
        collector=collector,
    )

    # Simulate error with PII
    try:
        with ctx.track():
            raise ValueError("Failed to process user@example.com with token secret_xyz123")
    except ValueError:
        pass

    # Verify PII scrubbed
    metrics = collector.get_unexported_metrics()
    error_msg = metrics[0]["error_message"]
    assert "[EMAIL]" in error_msg, f"Email not scrubbed: {error_msg}"
    assert "secret_xyz123" not in error_msg, f"Secret not scrubbed: {error_msg}"
    print(f"✅ PII scrubbing works: {error_msg}")

print("\n" + "=" * 60)
print("TEST 6: Multiple Concurrent Skills")
print("=" * 60)

import threading

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "test.db"
    collector = DualWriteMetricsCollector(db_path=db_path, otel_enabled=False)

    def run_skill(skill_id: str, count: int):
        metrics_collector = SkillExecutionMetricsCollector(collector=collector)
        for i in range(count):
            with metrics_collector.execute(skill_id, "test_tenant") as ctx:
                time.sleep(0.001)
                ctx.tokens_used = 100 + i

    threads = []
    for skill_id in ["skill_1", "skill_2", "skill_3"]:
        t = threading.Thread(target=run_skill, args=(skill_id, 5))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify all metrics recorded
    metrics = collector.get_unexported_metrics(batch_size=100)
    assert len(metrics) == 15, f"Expected 15 metrics (3 skills × 5 each), got {len(metrics)}"
    print(f"✅ Concurrent execution works: {len(metrics)} metrics from 3 parallel skills")

print("\n" + "=" * 60)
print("ALL INTEGRATION TESTS PASSED ✅")
print("=" * 60)
print("""
Verified:
  1. ✅ SkillMetricsContext manager (timing + metrics)
  2. ✅ Error handling (status=error, error_type, error_message)
  3. ✅ Decorator for automatic metrics
  4. ✅ High-level API (context manager)
  5. ✅ PII scrubbing in error messages (GDPR Art. 5)
  6. ✅ Concurrent skill execution (thread-safe)

LDD k=3 (Red→Green Iteration): Ready for integration ✅
Skill execution pipeline can emit metrics automatically.
""")
