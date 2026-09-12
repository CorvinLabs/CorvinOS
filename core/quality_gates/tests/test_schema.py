"""Tests for DuckDB schema (ADR-0688)."""

import pytest
import tempfile
import os
from pathlib import Path
from datetime import datetime

from core.quality_gates.schema import GateSchema


class TestSchemaInitialization:
    """Test schema initialization."""

    def test_schema_initialize_creates_tables(self):
        """Test that schema initialization creates all tables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = GateSchema.initialize(db_path)

            # Verify tables exist
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
            table_names = [t[0] for t in tables]

            assert "gate_events" in table_names
            assert "kg_nodes" in table_names
            assert "kg_edges" in table_names
            assert "gate_event_audit" in table_names

            conn.close()

    def test_schema_initialize_idempotent(self):
        """Test that schema initialization is idempotent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn1 = GateSchema.initialize(db_path)
            conn1.close()

            # Run again on same database
            conn2 = GateSchema.initialize(db_path)

            # Verify tables still exist
            tables = conn2.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
            table_names = [t[0] for t in tables]

            assert "gate_events" in table_names
            assert "kg_nodes" in table_names
            assert "kg_edges" in table_names
            assert "gate_event_audit" in table_names

            conn2.close()

    def test_schema_gate_events_constraints(self):
        """Test gate_events table constraints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = GateSchema.initialize(db_path)

            # Test NOT NULL constraints
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO gate_events (id, verdict, confidence, event_hash) "
                    "VALUES ('1', 'pass', 0.5, 'hash1')"
                )

            # Test CHECK constraint on verdict
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO gate_events "
                    "(id, timestamp, tenant_id, gate_name, artifact_id, verdict, confidence, event_hash) "
                    "VALUES ('1', '2026-09-12T00:00:00Z', 'test', 'gate', 'art', 'invalid', 0.5, 'hash1')"
                )

            # Test CHECK constraint on confidence
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO gate_events "
                    "(id, timestamp, tenant_id, gate_name, artifact_id, verdict, confidence, event_hash) "
                    "VALUES ('1', '2026-09-12T00:00:00Z', 'test', 'gate', 'art', 'pass', 1.5, 'hash1')"
                )

            conn.close()

    def test_schema_kg_nodes_constraints(self):
        """Test kg_nodes table constraints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = GateSchema.initialize(db_path)

            # Test NOT NULL constraints
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO kg_nodes (id, node_type, data) "
                    "VALUES ('1', 'ADR', '{}')"
                )

            conn.close()

    def test_schema_indexes_created(self):
        """Test that indexes are created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = GateSchema.initialize(db_path)

            # Verify indexes exist
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
            index_names = [i[0] for i in indexes]

            assert "idx_gate_events_tenant" in index_names
            assert "idx_gate_events_gate_name" in index_names
            assert "idx_gate_events_verdict" in index_names
            assert "idx_kg_nodes_tenant" in index_names
            assert "idx_kg_nodes_type" in index_names
            assert "idx_kg_edges_tenant" in index_names
            assert "idx_kg_edges_relationship" in index_names

            conn.close()

    def test_migrate_v1(self):
        """Test v1 migration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = GateSchema.initialize(db_path)

            # Run v1 migration (should be idempotent)
            GateSchema.migrate_v1(conn)

            # Verify tables exist
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
            table_names = [t[0] for t in tables]

            assert "gate_events" in table_names

            conn.close()

    def test_migrate_v2(self):
        """Test v2 migration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = GateSchema.initialize(db_path)

            # Run v2 migration (adds indexes)
            GateSchema.migrate_v2(conn)

            # Verify indexes exist
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
            index_names = [i[0] for i in indexes]

            assert "idx_gate_events_tenant" in index_names

            conn.close()
