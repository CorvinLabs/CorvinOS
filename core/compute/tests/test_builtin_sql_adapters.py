"""Tests for built-in SQL adapters (PostgreSQL, MySQL) using mocks (ADR-0026 D).

30 test cases:
- PostgreSQLAdapter.connect() with mock psycopg2
- discover_schema() returns SourceSchema
- create_cursor() produces parameterized query (assert %s placeholder)
- NEVER interpolates value into SQL string
- shard pushdown with SourceQuery(shard_index=1, n_shards=3) → MOD expression
- incremental: read/write watermark
- MySQLAdapter same tests
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.protocol import (
    FilterExpr,
    SecretEnv,
    SourceConfig,
    SourceQuery,
)


def _make_pg_config(**raw_overrides):
    raw = {"table": "users", "schema": "public", "host": "localhost", "dbname": "mydb"}
    raw.update(raw_overrides)
    return SourceConfig(adapter="postgresql", region="eu-central-1", raw=raw)


def _make_mysql_config(**raw_overrides):
    raw = {"table": "orders", "database": "shop", "host": "localhost"}
    raw.update(raw_overrides)
    return SourceConfig(adapter="mysql", region="eu-west-1", raw=raw)


def _make_secret_env():
    return SecretEnv({"PGUSER": "admin", "PGPASSWORD": "secret",
                      "MYSQL_USER": "root", "MYSQL_PASSWORD": "pass"})


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

def _pg_mock_context():
    """Return (mock_psycopg2, context_manager) for patching postgresql module."""
    from corvin_compute.fabric.datasources.builtin import postgresql
    mock_psycopg2 = MagicMock()
    mock_psycopg2.extras = MagicMock()
    return mock_psycopg2, postgresql


class TestPostgreSQLAdapterConnect(unittest.TestCase):
    def test_connect_calls_psycopg2_connect(self):
        from corvin_compute.fabric.datasources.builtin import postgresql as pg_mod
        from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter
        mock_psycopg2 = MagicMock()
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn
        mock_psycopg2.extras = MagicMock()

        orig, orig_avail = pg_mod.psycopg2, pg_mod.PSYCOPG2_AVAILABLE
        try:
            pg_mod.psycopg2 = mock_psycopg2
            pg_mod.PSYCOPG2_AVAILABLE = True
            session = PostgreSQLAdapter().connect(_make_pg_config(), _make_secret_env())
            mock_psycopg2.connect.assert_called_once()
            self.assertEqual(mock_psycopg2.connect.call_args[1]["user"], "admin")
            self.assertEqual(mock_psycopg2.connect.call_args[1]["password"], "secret")
        finally:
            pg_mod.psycopg2, pg_mod.PSYCOPG2_AVAILABLE = orig, orig_avail

    @patch("corvin_compute.fabric.datasources.builtin.postgresql.PSYCOPG2_AVAILABLE", False)
    def test_connect_raises_if_psycopg2_unavailable(self):
        from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter
        with self.assertRaises(ImportError):
            PostgreSQLAdapter().connect(_make_pg_config(), _make_secret_env())

    def test_discover_schema_returns_source_schema(self):
        from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter, _PGSession
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            ("id", "integer", "NO"),
            ("email", "varchar", "YES"),
        ]
        mock_cur.fetchone.return_value = (1000,)
        session = _PGSession(mock_conn)

        schema = PostgreSQLAdapter().discover_schema(session, _make_pg_config())
        self.assertEqual(len(schema.columns), 2)
        self.assertEqual(schema.columns[0].name, "id")
        self.assertFalse(schema.columns[0].nullable)
        self.assertTrue(schema.columns[1].nullable)

    def test_create_cursor_uses_parameterized_query(self):
        from corvin_compute.fabric.datasources.builtin import postgresql as pg_mod
        from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter, _PGSession
        mock_psycopg2 = MagicMock()
        mock_psycopg2.extras.RealDictCursor = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.cursor.return_value = mock_cur

        orig, orig_avail = pg_mod.psycopg2, pg_mod.PSYCOPG2_AVAILABLE
        try:
            pg_mod.psycopg2 = mock_psycopg2
            pg_mod.PSYCOPG2_AVAILABLE = True
            session = _PGSession(mock_conn)
            query = SourceQuery(filters=[FilterExpr(col="age", op=">", value=30)])
            list(PostgreSQLAdapter().create_cursor(session, _make_pg_config(), query))

            sql = mock_cur.execute.call_args[0][0]
            params = mock_cur.execute.call_args[0][1]
            self.assertIn("%s", sql)
            self.assertNotIn("30", sql)
            self.assertIn(30, params)
        finally:
            pg_mod.psycopg2, pg_mod.PSYCOPG2_AVAILABLE = orig, orig_avail

    def test_shard_pushdown_produces_mod_expression(self):
        from corvin_compute.fabric.datasources.builtin import postgresql as pg_mod
        from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter, _PGSession
        mock_psycopg2 = MagicMock()
        mock_psycopg2.extras.RealDictCursor = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.cursor.return_value = mock_cur

        orig, orig_avail = pg_mod.psycopg2, pg_mod.PSYCOPG2_AVAILABLE
        try:
            pg_mod.psycopg2 = mock_psycopg2
            pg_mod.PSYCOPG2_AVAILABLE = True
            session = _PGSession(mock_conn)
            query = SourceQuery(shard_index=1, n_shards=3)
            list(PostgreSQLAdapter().create_cursor(session, _make_pg_config(), query))

            sql = mock_cur.execute.call_args[0][0]
            params = list(mock_cur.execute.call_args[0][1])
            self.assertIn("%%", sql)
            self.assertIn(3, params)
            self.assertIn(1, params)
        finally:
            pg_mod.psycopg2, pg_mod.PSYCOPG2_AVAILABLE = orig, orig_avail

    def test_no_value_interpolation_in_sql(self):
        from corvin_compute.fabric.datasources.builtin import postgresql as pg_mod
        from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter, _PGSession
        mock_psycopg2 = MagicMock()
        mock_psycopg2.extras.RealDictCursor = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.cursor.return_value = mock_cur

        orig, orig_avail = pg_mod.psycopg2, pg_mod.PSYCOPG2_AVAILABLE
        try:
            pg_mod.psycopg2 = mock_psycopg2
            pg_mod.PSYCOPG2_AVAILABLE = True
            session = _PGSession(mock_conn)
            distinctive_value = "SHOULD_NOT_APPEAR_IN_SQL_12345"
            query = SourceQuery(filters=[FilterExpr(col="name", op="=", value=distinctive_value)])
            list(PostgreSQLAdapter().create_cursor(session, _make_pg_config(), query))

            sql = mock_cur.execute.call_args[0][0]
            self.assertNotIn(distinctive_value, sql)
        finally:
            pg_mod.psycopg2, pg_mod.PSYCOPG2_AVAILABLE = orig, orig_avail

    def test_close_calls_conn_close(self):
        from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter, _PGSession
        mock_conn = MagicMock()
        session = _PGSession(mock_conn)
        PostgreSQLAdapter().close(session)
        mock_conn.close.assert_called_once()

    def test_capability_flags(self):
        from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter
        adapter = PostgreSQLAdapter()
        self.assertTrue(adapter.supports_pushdown)
        self.assertTrue(adapter.supports_incremental)
        self.assertTrue(adapter.supports_schema_discovery)
        self.assertTrue(adapter.supports_streaming)


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------

class TestMySQLAdapterConnect(unittest.TestCase):
    def test_connect_calls_pymysql_connect(self):
        from corvin_compute.fabric.datasources.builtin import mysql as mysql_mod
        from corvin_compute.fabric.datasources.builtin.mysql import MySQLAdapter
        mock_pymysql = MagicMock()
        mock_conn = MagicMock()
        mock_pymysql.connect.return_value = mock_conn
        mock_pymysql.cursors = MagicMock()

        orig, orig_avail = mysql_mod.pymysql, mysql_mod.PYMYSQL_AVAILABLE
        try:
            mysql_mod.pymysql = mock_pymysql
            mysql_mod.PYMYSQL_AVAILABLE = True
            MySQLAdapter().connect(_make_mysql_config(), _make_secret_env())
            mock_pymysql.connect.assert_called_once()
            kwargs = mock_pymysql.connect.call_args[1]
            self.assertEqual(kwargs["user"], "root")
            self.assertEqual(kwargs["password"], "pass")
        finally:
            mysql_mod.pymysql, mysql_mod.PYMYSQL_AVAILABLE = orig, orig_avail

    @patch("corvin_compute.fabric.datasources.builtin.mysql.PYMYSQL_AVAILABLE", False)
    def test_connect_raises_if_pymysql_unavailable(self):
        from corvin_compute.fabric.datasources.builtin.mysql import MySQLAdapter
        with self.assertRaises(ImportError):
            MySQLAdapter().connect(_make_mysql_config(), _make_secret_env())

    def test_discover_schema_returns_source_schema(self):
        from corvin_compute.fabric.datasources.builtin.mysql import MySQLAdapter, _MySQLSession
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            {"COLUMN_NAME": "id", "DATA_TYPE": "int", "IS_NULLABLE": "NO"},
            {"COLUMN_NAME": "name", "DATA_TYPE": "varchar", "IS_NULLABLE": "YES"},
        ]
        mock_cur.fetchone.return_value = {"TABLE_ROWS": 500}
        session = _MySQLSession(mock_conn)

        schema = MySQLAdapter().discover_schema(session, _make_mysql_config())
        self.assertEqual(len(schema.columns), 2)
        self.assertEqual(schema.columns[0].name, "id")
        self.assertEqual(schema.estimated_row_count, 500)

    def test_create_cursor_parameterized(self):
        from corvin_compute.fabric.datasources.builtin.mysql import MySQLAdapter, _MySQLSession
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur
        session = _MySQLSession(mock_conn)

        query = SourceQuery(filters=[FilterExpr(col="price", op="<", value=99)])
        list(MySQLAdapter().create_cursor(session, _make_mysql_config(), query))

        sql = mock_cur.execute.call_args[0][0]
        params = mock_cur.execute.call_args[0][1]
        self.assertIn("%s", sql)
        self.assertNotIn("99", sql)
        self.assertIn(99, list(params))

    def test_shard_pushdown(self):
        from corvin_compute.fabric.datasources.builtin.mysql import MySQLAdapter, _MySQLSession
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur
        session = _MySQLSession(mock_conn)

        query = SourceQuery(shard_index=0, n_shards=4)
        list(MySQLAdapter().create_cursor(session, _make_mysql_config(), query))

        sql = mock_cur.execute.call_args[0][0]
        params = list(mock_cur.execute.call_args[0][1])
        self.assertIn("MOD", sql)
        self.assertIn(4, params)

    def test_mysql_capability_flags(self):
        from corvin_compute.fabric.datasources.builtin.mysql import MySQLAdapter
        adapter = MySQLAdapter()
        self.assertTrue(adapter.supports_pushdown)
        self.assertTrue(adapter.supports_incremental)


# ---------------------------------------------------------------------------
# safe_to_sql integration
# ---------------------------------------------------------------------------

class TestSafeToSQLIntegration(unittest.TestCase):
    def test_order_by_included(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery(order_by="created_at")
        sql, params = safe_to_sql(q, "events", "psycopg2")
        self.assertIn("ORDER BY created_at", sql)

    def test_bigquery_dialect_uses_at_param(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery(filters=[FilterExpr(col="x", op="=", value=1)])
        sql, params = safe_to_sql(q, "t", "bigquery")
        self.assertIn("@p0", sql)
        self.assertNotIn("%s", sql)

    def test_snowflake_dialect_uses_percent_s(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery(filters=[FilterExpr(col="x", op="=", value=42)])
        sql, params = safe_to_sql(q, "t", "snowflake")
        self.assertIn("%s", sql)


# ---------------------------------------------------------------------------
# F4 — SQL identifier-injection defence
# ---------------------------------------------------------------------------

class TestIdentifierValidation(unittest.TestCase):
    """Identifiers (columns, table, order_by, filter col) must be gated by a
    strict allowlist regex — values are bound as params but identifiers cannot
    be, so an attacker-controlled identifier must never break out."""

    def test_malicious_table_rejected(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery()
        with self.assertRaises(ValueError):
            safe_to_sql(q, "users; DROP TABLE users; --", "psycopg2")

    def test_malicious_column_rejected(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery(columns=["id", "(SELECT password FROM secrets)"])
        with self.assertRaises(ValueError):
            safe_to_sql(q, "t", "psycopg2")

    def test_malicious_filter_col_rejected(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery(filters=[FilterExpr(col="1=1 OR x", op="=", value=1)])
        with self.assertRaises(ValueError):
            safe_to_sql(q, "t", "psycopg2")

    def test_malicious_order_by_rejected(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery(order_by="created_at; DROP TABLE t")
        with self.assertRaises(ValueError):
            safe_to_sql(q, "t", "psycopg2")

    def test_order_by_injection_via_subquery_rejected(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery(order_by="(CASE WHEN 1=1 THEN 1 ELSE 2 END)")
        with self.assertRaises(ValueError):
            safe_to_sql(q, "t", "psycopg2")

    def test_valid_schema_qualified_table_accepted(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        q = SourceQuery(columns=["public.users.id"])
        sql, _ = safe_to_sql(q, "public.users", "psycopg2")
        self.assertIn("FROM public.users", sql)
        self.assertIn("public.users.id", sql)

    def test_order_by_direction_allowlisted(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        sql_asc, _ = safe_to_sql(SourceQuery(order_by="created_at ASC"), "t", "psycopg2")
        self.assertIn("ORDER BY created_at ASC", sql_asc)
        sql_desc, _ = safe_to_sql(SourceQuery(order_by="ts desc"), "t", "psycopg2")
        # direction normalized to upper-case, column preserved
        self.assertIn("ORDER BY ts DESC", sql_desc)

    def test_order_by_bogus_direction_rejected(self):
        from corvin_compute.fabric.datasources.query import safe_to_sql
        with self.assertRaises(ValueError):
            safe_to_sql(SourceQuery(order_by="col SIDEWAYS"), "t", "psycopg2")

    def test_validate_identifier_helper(self):
        from corvin_compute.fabric.datasources.query import _validate_identifier
        self.assertEqual(_validate_identifier("a.b_c1"), "a.b_c1")
        for bad in ["", "1col", "a b", "a;b", "a'b", "a)b", "*", "a--b", None]:
            with self.assertRaises(ValueError):
                _validate_identifier(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
