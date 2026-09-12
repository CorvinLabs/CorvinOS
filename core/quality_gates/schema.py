"""DuckDB schema and migrations for Quality Gates System (ADR-0688).

Defines tables for:
- gate_events: gate verdicts with hash-chain
- kg_nodes: knowledge graph nodes
- kg_edges: knowledge graph edges
- gate_event_audit: audit verification records
"""

import duckdb
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class GateSchema:
    """Manages DuckDB schema for Quality Gates."""

    # SQL DDL for gate_events table
    GATE_EVENTS_DDL = """
    CREATE TABLE IF NOT EXISTS gate_events (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        gate_name TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'warn', 'fail')),
        confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
        reason TEXT,
        event_hash TEXT NOT NULL UNIQUE,
        prior_hash TEXT,
        findings_count INTEGER DEFAULT 0
    )
    """

    # SQL DDL for kg_nodes table
    KG_NODES_DDL = """
    CREATE TABLE IF NOT EXISTS kg_nodes (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        node_type TEXT NOT NULL,
        data JSON NOT NULL,
        created_at TEXT NOT NULL
    )
    """

    # SQL DDL for kg_edges table
    KG_EDGES_DDL = """
    CREATE TABLE IF NOT EXISTS kg_edges (
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        data JSON,
        created_at TEXT NOT NULL,
        PRIMARY KEY (source_id, target_id, relationship_type)
    )
    """

    # SQL DDL for gate_event_audit table
    GATE_EVENT_AUDIT_DDL = """
    CREATE TABLE IF NOT EXISTS gate_event_audit (
        id TEXT PRIMARY KEY,
        gate_event_id TEXT NOT NULL UNIQUE,
        verification_status TEXT,
        verification_timestamp TEXT,
        FOREIGN KEY (gate_event_id) REFERENCES gate_events(id)
    )
    """

    @staticmethod
    def initialize(db_path: str) -> duckdb.DuckDBPyConnection:
        """Initialize DuckDB connection and create schema if needed.

        Args:
            db_path: Path to DuckDB database file

        Returns:
            DuckDB connection

        Raises:
            ValueError: If tenant support check fails
        """
        conn = duckdb.connect(db_path)

        # Ensure database is properly initialized
        conn.execute("PRAGMA journal_mode = WAL")

        # Create tables (idempotent)
        conn.execute(GateSchema.GATE_EVENTS_DDL)
        conn.execute(GateSchema.KG_NODES_DDL)
        conn.execute(GateSchema.KG_EDGES_DDL)
        conn.execute(GateSchema.GATE_EVENT_AUDIT_DDL)

        # Create indexes for performance
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gate_events_tenant "
            "ON gate_events(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gate_events_gate_name "
            "ON gate_events(gate_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gate_events_verdict "
            "ON gate_events(verdict)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_nodes_tenant "
            "ON kg_nodes(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_nodes_type "
            "ON kg_nodes(node_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_edges_tenant "
            "ON kg_edges(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_edges_relationship "
            "ON kg_edges(relationship_type)"
        )

        logger.info(f"Quality Gates schema initialized at {db_path}")
        return conn

    @staticmethod
    def migrate_v1(conn: duckdb.DuckDBPyConnection) -> None:
        """Run version 1 migration (creates base tables).

        Args:
            conn: DuckDB connection
        """
        conn.execute(GateSchema.GATE_EVENTS_DDL)
        conn.execute(GateSchema.KG_NODES_DDL)
        conn.execute(GateSchema.KG_EDGES_DDL)
        conn.execute(GateSchema.GATE_EVENT_AUDIT_DDL)
        logger.info("Migration v1 complete")

    @staticmethod
    def migrate_v2(conn: duckdb.DuckDBPyConnection) -> None:
        """Run version 2 migration (adds indexes).

        Args:
            conn: DuckDB connection
        """
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gate_events_tenant "
            "ON gate_events(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gate_events_gate_name "
            "ON gate_events(gate_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gate_events_verdict "
            "ON gate_events(verdict)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_nodes_tenant "
            "ON kg_nodes(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_nodes_type "
            "ON kg_nodes(node_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_edges_tenant "
            "ON kg_edges(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kg_edges_relationship "
            "ON kg_edges(relationship_type)"
        )
        logger.info("Migration v2 complete")
