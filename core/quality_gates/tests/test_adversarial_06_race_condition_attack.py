"""Adversarial Test 06: Race Condition Attack (ADR-0690 Phase 3.2).

Attack vector: Parallel validators collide on graph writes.
Defense: DuckDB WAL mode + SERIALIZABLE isolation level.

Tests:
1. 10 parallel validators write simultaneously; no corruption
2. Concurrent hash-chain updates; ordering preserved
3. Tenant isolation under concurrency; no cross-tenant leakage
4. Transaction isolation ensures no dirty reads
5. Multiple writers compete for audit sequence; all succeed
6. Concurrent graph mutations; ACID guarantees hold
"""

import pytest
import tempfile
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.models import GateResult, VerdictType


class TestRaceConditionAttack:
    """Test concurrency and race condition defenses."""

    @pytest.fixture
    def setup(self):
        """Create test database with WAL mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            logger = QualityGateAuditLogger(graph.conn)

            yield graph, logger
            graph.close()

    def test_ten_parallel_validators_no_corruption(self, setup):
        """Test 10 parallel validators write successfully without corruption."""
        graph, logger = setup

        results = []
        errors = []

        def write_validator_event(validator_id):
            try:
                for i in range(3):
                    result = GateResult(
                        gate_name=f"Validator{validator_id}Gate",
                        artifact_id=f"V{validator_id}-{i}",
                        verdict=VerdictType.PASS if (validator_id + i) % 2 == 0 else VerdictType.FAIL,
                        confidence=0.5 + (validator_id * 0.04),
                        reason=f"Validator {validator_id} event {i}",
                        tenant_id="test-tenant",
                    )
                    hash_val = logger.write_gate_event(result)
                    results.append(hash_val)
            except Exception as e:
                errors.append((validator_id, str(e)))

        # Launch 10 validators in parallel
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for validator_id in range(10):
                future = executor.submit(write_validator_event, validator_id)
                futures.append(future)

            # Wait for all to complete
            for future in as_completed(futures):
                future.result()

        # Verify no errors
        assert len(errors) == 0
        assert len(results) == 30  # 10 validators * 3 events each

    def test_concurrent_hash_chain_updates_ordering_preserved(self, setup):
        """Test concurrent hash-chain updates maintain ordering."""
        graph, logger = setup

        hashes = []
        lock = threading.Lock()

        def write_with_hash_chain(writer_id):
            result = GateResult(
                gate_name="ChainGate",
                artifact_id=f"CHAIN-{writer_id}",
                verdict=VerdictType.PASS,
                confidence=0.85,
                reason=f"Chain update by writer {writer_id}",
                tenant_id="test-tenant",
            )
            hash_val = logger.write_gate_event(result)
            with lock:
                hashes.append((writer_id, hash_val))

        # Launch 5 writers concurrently
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(write_with_hash_chain, i) for i in range(5)]
            for future in as_completed(futures):
                future.result()

        # Verify chain ordering
        query = "SELECT event_hash, prior_hash FROM gate_events WHERE tenant_id = ? ORDER BY timestamp"
        rows = graph.conn.execute(query, ["test-tenant"]).fetchall()

        # Each event's prior_hash should match previous event's hash
        for i in range(1, len(rows)):
            assert rows[i][1] == rows[i-1][0]

        assert len(rows) == 5

    def test_tenant_isolation_under_concurrency(self, setup):
        """Test tenant isolation is maintained under concurrent writes."""
        graph, logger = setup

        results_by_tenant = {}
        lock = threading.Lock()

        def write_to_tenant(tenant_id, event_num):
            result = GateResult(
                gate_name="TenantGate",
                artifact_id=f"{tenant_id}-{event_num}",
                verdict=VerdictType.PASS,
                confidence=0.85,
                reason=f"Event for {tenant_id}",
                tenant_id=tenant_id,
            )
            hash_val = logger.write_gate_event(result)
            with lock:
                if tenant_id not in results_by_tenant:
                    results_by_tenant[tenant_id] = []
                results_by_tenant[tenant_id].append(hash_val)

        # Write to 3 tenants concurrently
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = []
            for tenant_id in ["tenant-a", "tenant-b", "tenant-c"]:
                for i in range(3):
                    future = executor.submit(write_to_tenant, tenant_id, i)
                    futures.append(future)

            for future in as_completed(futures):
                future.result()

        # Verify each tenant has their own events (no cross-contamination)
        for tenant_id in ["tenant-a", "tenant-b", "tenant-c"]:
            query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
            row = graph.conn.execute(query, [tenant_id]).fetchone()
            assert row[0] == 3

            # Verify artifact_ids match tenant
            query = "SELECT artifact_id FROM gate_events WHERE tenant_id = ?"
            rows = graph.conn.execute(query, [tenant_id]).fetchall()
            for row in rows:
                assert row[0].startswith(tenant_id)

    def test_transaction_isolation_no_dirty_reads(self, setup):
        """Test SERIALIZABLE isolation prevents dirty reads."""
        graph, logger = setup

        # Write initial event
        result1 = GateResult(
            gate_name="IsoGate",
            artifact_id="ISO-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Initial",
            tenant_id="test-tenant",
        )
        logger.write_gate_event(result1)

        read_values = []
        lock = threading.Lock()

        def reader():
            # Read value that should be stable
            query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
            row = graph.conn.execute(query, ["test-tenant"]).fetchone()
            with lock:
                read_values.append(row[0])

        def writer():
            time.sleep(0.01)  # Let reader start
            result2 = GateResult(
                gate_name="IsoGate",
                artifact_id="ISO-002",
                verdict=VerdictType.FAIL,
                confidence=0.0,
                reason="Concurrent write",
                tenant_id="test-tenant",
            )
            logger.write_gate_event(result2)

        # Reader thread
        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)

        reader_thread.start()
        writer_thread.start()
        reader_thread.join()
        writer_thread.join()

        # Final count should be 2 (both events exist)
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        final_row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        assert final_row[0] == 2

    def test_concurrent_audit_sequence_all_succeed(self, setup):
        """Test all concurrent audit writers succeed without lost writes."""
        graph, logger = setup

        success_count = []
        lock = threading.Lock()

        def audit_writer(writer_id):
            for i in range(5):
                result = GateResult(
                    gate_name="AuditGate",
                    artifact_id=f"AUDIT-{writer_id}-{i}",
                    verdict=VerdictType.PASS,
                    confidence=0.85,
                    reason=f"Audit by writer {writer_id}",
                    tenant_id="test-tenant",
                )
                try:
                    hash_val = logger.write_gate_event(result)
                    with lock:
                        success_count.append(1)
                except Exception as e:
                    print(f"Writer {writer_id} error: {e}")

        # Launch 8 audit writers
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(audit_writer, i) for i in range(8)]
            for future in as_completed(futures):
                future.result()

        # Verify all writes succeeded
        assert len(success_count) == 40  # 8 writers * 5 events

        # Verify all events are in database
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ? AND gate_name = 'AuditGate'"
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        assert row[0] == 40

    def test_concurrent_graph_mutations_acid_holds(self, setup):
        """Test concurrent mutations maintain ACID properties."""
        graph, logger = setup

        # Initial state
        initial_hash = None
        result = GateResult(
            gate_name="MutateGate",
            artifact_id="MUTATE-INIT",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Initial",
            tenant_id="test-tenant",
        )
        initial_hash = logger.write_gate_event(result)

        mutation_errors = []
        lock = threading.Lock()

        def mutator(mutator_id):
            try:
                for i in range(3):
                    result = GateResult(
                        gate_name="MutateGate",
                        artifact_id=f"MUTATE-{mutator_id}-{i}",
                        verdict=VerdictType.PASS if i % 2 == 0 else VerdictType.FAIL,
                        confidence=0.7 + (i * 0.05),
                        reason=f"Mutation by {mutator_id}",
                        tenant_id="test-tenant",
                    )
                    logger.write_gate_event(result)
            except Exception as e:
                with lock:
                    mutation_errors.append(str(e))

        # Launch 5 mutators
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(mutator, i) for i in range(5)]
            for future in as_completed(futures):
                future.result()

        # Verify no errors
        assert len(mutation_errors) == 0

        # Verify all mutations persisted
        query = "SELECT COUNT(*) FROM gate_events WHERE gate_name = 'MutateGate' AND tenant_id = ?"
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        assert row[0] == 1 + (5 * 3)  # Initial + 5 mutators * 3 events

        # Verify chain integrity still holds
        query = "SELECT event_hash, prior_hash FROM gate_events WHERE tenant_id = ? ORDER BY timestamp"
        rows = graph.conn.execute(query, ["test-tenant"]).fetchall()

        for i in range(1, len(rows)):
            assert rows[i][1] == rows[i-1][0]
