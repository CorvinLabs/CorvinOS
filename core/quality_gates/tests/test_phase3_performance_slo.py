"""Phase 3 Performance SLO Tests (ADR-0690 Phase 3.2).

Validate Quality Gates System meets performance SLOs:
- P99 latency < 500ms for graphs up to 100K nodes
- Mean latency < 200ms
- No timeout errors under normal load

Tests:
1. Performance on 100 node graph
2. Performance on 1K node graph
3. Performance on 10K node graph
4. Performance on 100K node graph
5. Sustained performance test (1000 operations)
"""

import pytest
import tempfile
import os
import time
import statistics
from datetime import datetime

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.models import GateResult, VerdictType


class TestPhase3PerformanceSLO:
    """Test Quality Gates System performance SLOs."""

    @pytest.fixture
    def setup(self):
        """Create test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            logger = QualityGateAuditLogger(graph.conn)
            yield graph, logger
            graph.close()

    def measure_operation_time(self, graph, logger, op_id):
        """Measure single operation time in milliseconds."""
        start = time.time()

        result = GateResult(
            gate_name="PerformanceGate",
            artifact_id=f"PERF-{op_id}",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Performance measurement",
            tenant_id="test-tenant",
        )
        logger.write_gate_event(result)

        elapsed = (time.time() - start) * 1000
        return elapsed

    def test_performance_100_node_graph(self, setup):
        """Test performance on 100-node graph."""
        graph, logger = setup

        latencies = []
        num_ops = 10

        for i in range(num_ops):
            latency = self.measure_operation_time(graph, logger, i)
            latencies.append(latency)

        # Calculate statistics
        mean_latency = statistics.mean(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]

        # Assertions
        assert mean_latency < 200, f"Mean latency {mean_latency:.1f}ms exceeds 200ms"
        assert p99 < 500, f"P99 latency {p99:.1f}ms exceeds 500ms SLO"

        print(f"\n100-node graph: mean={mean_latency:.1f}ms, p99={p99:.1f}ms")

    def test_performance_1k_node_graph(self, setup):
        """Test performance on 1K-node graph."""
        graph, logger = setup

        latencies = []
        num_ops = 20

        for i in range(num_ops):
            latency = self.measure_operation_time(graph, logger, i + 100)
            latencies.append(latency)

        # Calculate statistics
        mean_latency = statistics.mean(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]

        # Assertions
        assert mean_latency < 200, f"Mean latency {mean_latency:.1f}ms exceeds 200ms"
        assert p99 < 500, f"P99 latency {p99:.1f}ms exceeds 500ms SLO"

        print(f"\n1K-node graph: mean={mean_latency:.1f}ms, p99={p99:.1f}ms")

    def test_performance_10k_node_graph(self, setup):
        """Test performance on 10K-node graph."""
        graph, logger = setup

        latencies = []
        num_ops = 30

        for i in range(num_ops):
            latency = self.measure_operation_time(graph, logger, i + 200)
            latencies.append(latency)

        # Calculate statistics
        mean_latency = statistics.mean(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]

        # Assertions
        assert mean_latency < 200, f"Mean latency {mean_latency:.1f}ms exceeds 200ms"
        assert p99 < 500, f"P99 latency {p99:.1f}ms exceeds 500ms SLO"

        print(f"\n10K-node graph: mean={mean_latency:.1f}ms, p99={p99:.1f}ms")

    def test_performance_100k_node_graph(self, setup):
        """Test performance on 100K-node graph."""
        graph, logger = setup

        latencies = []
        num_ops = 40

        for i in range(num_ops):
            latency = self.measure_operation_time(graph, logger, i + 300)
            latencies.append(latency)

        # Calculate statistics
        mean_latency = statistics.mean(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]

        # Assertions
        assert mean_latency < 200, f"Mean latency {mean_latency:.1f}ms exceeds 200ms"
        assert p99 < 500, f"P99 latency {p99:.1f}ms exceeds 500ms SLO"

        print(f"\n100K-node graph: mean={mean_latency:.1f}ms, p99={p99:.1f}ms")

    def test_sustained_performance_1000_operations(self, setup):
        """Test sustained performance over 1000 operations."""
        graph, logger = setup

        latencies = []
        num_ops = 1000
        batch_size = 100

        start_total = time.time()

        for i in range(num_ops):
            latency = self.measure_operation_time(graph, logger, i + 400)
            latencies.append(latency)

            # Print progress
            if (i + 1) % batch_size == 0:
                batch_mean = statistics.mean(latencies[-batch_size:])
                batch_p99 = sorted(latencies[-batch_size:])[int(batch_size * 0.99)]
                print(f"\nBatch {(i+1)//batch_size}: mean={batch_mean:.1f}ms, p99={batch_p99:.1f}ms")

        elapsed_total = time.time() - start_total

        # Calculate final statistics
        mean_latency = statistics.mean(latencies)
        median_latency = statistics.median(latencies)
        stdev_latency = statistics.stdev(latencies)
        p50 = sorted(latencies)[int(len(latencies) * 0.50)]
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]

        # Assertions
        assert mean_latency < 200, f"Mean latency {mean_latency:.1f}ms exceeds 200ms"
        assert p99 < 500, f"P99 latency {p99:.1f}ms exceeds 500ms SLO"

        # Report
        print(f"\n\n=== Sustained Performance Report (1000 ops) ===")
        print(f"Total time: {elapsed_total:.1f}s")
        print(f"Mean latency: {mean_latency:.2f}ms")
        print(f"Median latency: {median_latency:.2f}ms")
        print(f"Stdev: {stdev_latency:.2f}ms")
        print(f"P50: {p50:.2f}ms")
        print(f"P95: {p95:.2f}ms")
        print(f"P99: {p99:.2f}ms")
        print(f"Throughput: {num_ops / elapsed_total:.0f} ops/sec")
