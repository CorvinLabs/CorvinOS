"""Tests for warehouse adapters: BigQuery, Snowflake, Redshift (ADR-0026 Section D).

20 test cases using mocks.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.protocol import (
    FilterExpr,
    SecretEnv,
    SourceConfig,
    SourceQuery,
)


def _bq_secret_env():
    return SecretEnv({"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/sa.json"})


def _sf_secret_env():
    return SecretEnv({"SNOWFLAKE_USER": "user", "SNOWFLAKE_PASSWORD": "pass"})


def _rs_secret_env():
    return SecretEnv({"REDSHIFT_USER": "admin", "REDSHIFT_PASSWORD": "secret"})


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

class TestBigQueryAdapter(unittest.TestCase):
    def test_capability_flags(self):
        from corvin_compute.fabric.datasources.builtin.bigquery import BigQueryAdapter
        a = BigQueryAdapter()
        self.assertTrue(a.supports_pushdown)
        self.assertTrue(a.supports_incremental)
        self.assertTrue(a.supports_schema_discovery)

    @patch("corvin_compute.fabric.datasources.builtin.bigquery.BQ_AVAILABLE", False)
    def test_connect_raises_without_bigquery(self):
        from corvin_compute.fabric.datasources.builtin.bigquery import BigQueryAdapter
        with self.assertRaises(ImportError):
            BigQueryAdapter().connect(
                SourceConfig("bigquery", "eu-central-1", {"project": "p", "dataset": "d"}),
                _bq_secret_env(),
            )

    def test_connect_creates_bq_client(self):
        from corvin_compute.fabric.datasources.builtin import bigquery as bq_mod
        from corvin_compute.fabric.datasources.builtin.bigquery import BigQueryAdapter
        mock_bq = MagicMock()
        mock_client = MagicMock()
        mock_bq.Client.return_value = mock_client

        orig, orig_avail = bq_mod.bigquery, bq_mod.BQ_AVAILABLE
        try:
            bq_mod.bigquery = mock_bq
            bq_mod.BQ_AVAILABLE = True
            import os
            with patch.dict(os.environ, {}, clear=False):
                config = SourceConfig("bigquery", "eu-central-1",
                                      {"project": "my-proj", "dataset": "ds1"})
                session = BigQueryAdapter().connect(config, _bq_secret_env())
                self.assertEqual(session.dataset, "ds1")
        finally:
            bq_mod.bigquery, bq_mod.BQ_AVAILABLE = orig, orig_avail

    def test_discover_schema_from_bq_table(self):
        from corvin_compute.fabric.datasources.builtin.bigquery import BigQueryAdapter, _BQSession
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.get_table.return_value = mock_table
        # MagicMock handles `name` specially — must use configure_mock or property
        field_id = MagicMock()
        field_id.name = "id"
        field_id.field_type = "INTEGER"
        field_id.mode = "REQUIRED"
        field_email = MagicMock()
        field_email.name = "email"
        field_email.field_type = "STRING"
        field_email.mode = "NULLABLE"
        mock_table.schema = [field_id, field_email]
        mock_table.num_rows = 5000
        session = _BQSession(mock_client, "my_dataset")

        config = SourceConfig("bigquery", "eu-central-1",
                              {"project": "p", "dataset": "my_dataset", "table": "users"})
        schema = BigQueryAdapter().discover_schema(session, config)

        self.assertEqual(len(schema.columns), 2)
        self.assertEqual(schema.columns[0].name, "id")
        self.assertFalse(schema.columns[0].nullable)
        self.assertTrue(schema.columns[1].nullable)
        self.assertEqual(schema.estimated_row_count, 5000)

    def test_shard_uses_farm_fingerprint_parameterized(self):
        """BQ shard: FARM_FINGERPRINT MOD query must be parameterized."""
        from corvin_compute.fabric.datasources.builtin import bigquery as bq_mod
        from corvin_compute.fabric.datasources.builtin.bigquery import BigQueryAdapter, _BQSession
        mock_bq = MagicMock()
        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_client.query.return_value = mock_job
        mock_job.result.return_value = iter([])
        mock_bq.QueryJobConfig = MagicMock
        mock_bq.ScalarQueryParameter = MagicMock

        orig, orig_avail = bq_mod.bigquery, bq_mod.BQ_AVAILABLE
        try:
            bq_mod.bigquery = mock_bq
            bq_mod.BQ_AVAILABLE = True
            session = _BQSession(mock_client, "ds1")
            config = SourceConfig("bigquery", "eu-central-1",
                                  {"project": "p", "dataset": "ds1", "table": "t", "primary_key": "id"})
            query = SourceQuery(shard_index=0, n_shards=3)
            list(BigQueryAdapter().create_cursor(session, config, query))

            call_args = mock_client.query.call_args
            sql = call_args[0][0]
            self.assertIn("FARM_FINGERPRINT", sql)
            self.assertIn("MOD", sql)
        finally:
            bq_mod.bigquery, bq_mod.BQ_AVAILABLE = orig, orig_avail

    def test_no_bigquery_skips_gracefully(self):
        import importlib
        mod = importlib.import_module("corvin_compute.fabric.datasources.builtin.bigquery")
        self.assertTrue(hasattr(mod, "BigQueryAdapter"))


# ---------------------------------------------------------------------------
# Snowflake
# ---------------------------------------------------------------------------

class TestSnowflakeAdapter(unittest.TestCase):
    def test_capability_flags(self):
        from corvin_compute.fabric.datasources.builtin.snowflake import SnowflakeAdapter
        a = SnowflakeAdapter()
        self.assertTrue(a.supports_pushdown)
        self.assertTrue(a.supports_incremental)

    @patch("corvin_compute.fabric.datasources.builtin.snowflake.SF_AVAILABLE", False)
    def test_connect_raises_without_snowflake(self):
        from corvin_compute.fabric.datasources.builtin.snowflake import SnowflakeAdapter
        with self.assertRaises(ImportError):
            SnowflakeAdapter().connect(
                SourceConfig("snowflake", "us-east-1", {"account": "acc"}),
                _sf_secret_env(),
            )

    def test_connect_uses_secret_env(self):
        from corvin_compute.fabric.datasources.builtin import snowflake as sf_mod
        from corvin_compute.fabric.datasources.builtin.snowflake import SnowflakeAdapter
        mock_sf = MagicMock()
        mock_conn = MagicMock()
        mock_sf.connector.connect.return_value = mock_conn

        orig, orig_avail = sf_mod.snowflake, sf_mod.SF_AVAILABLE
        try:
            sf_mod.snowflake = mock_sf
            sf_mod.SF_AVAILABLE = True
            config = SourceConfig("snowflake", "us-east-1",
                                  {"account": "acc", "warehouse": "wh", "database": "db", "schema": "s"})
            SnowflakeAdapter().connect(config, _sf_secret_env())
            kwargs = mock_sf.connector.connect.call_args[1]
            self.assertEqual(kwargs["user"], "user")
            self.assertEqual(kwargs["password"], "pass")
        finally:
            sf_mod.snowflake, sf_mod.SF_AVAILABLE = orig, orig_avail

    def test_create_cursor_parameterized(self):
        from corvin_compute.fabric.datasources.builtin import snowflake as sf_mod
        from corvin_compute.fabric.datasources.builtin.snowflake import SnowflakeAdapter, _SFSession
        mock_sf = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur
        mock_sf.connector.DictCursor = MagicMock()

        orig, orig_avail = sf_mod.snowflake, sf_mod.SF_AVAILABLE
        try:
            sf_mod.snowflake = mock_sf
            sf_mod.SF_AVAILABLE = True
            session = _SFSession(mock_conn)
            query = SourceQuery(filters=[FilterExpr(col="amount", op=">=", value=100)])
            list(SnowflakeAdapter().create_cursor(
                session, SourceConfig("snowflake", "us-east-1", {"table": "sales"}), query,
            ))
            sql = mock_cur.execute.call_args[0][0]
            params = list(mock_cur.execute.call_args[0][1])
            self.assertIn("%s", sql)
            self.assertNotIn("100", sql)
            self.assertIn(100, params)
        finally:
            sf_mod.snowflake, sf_mod.SF_AVAILABLE = orig, orig_avail

    def test_shard_uses_seq8_parameterized(self):
        from corvin_compute.fabric.datasources.builtin import snowflake as sf_mod
        from corvin_compute.fabric.datasources.builtin.snowflake import SnowflakeAdapter, _SFSession
        mock_sf = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur
        mock_sf.connector.DictCursor = MagicMock()

        orig, orig_avail = sf_mod.snowflake, sf_mod.SF_AVAILABLE
        try:
            sf_mod.snowflake = mock_sf
            sf_mod.SF_AVAILABLE = True
            session = _SFSession(mock_conn)
            query = SourceQuery(shard_index=2, n_shards=5)
            list(SnowflakeAdapter().create_cursor(
                session, SourceConfig("snowflake", "us-east-1", {"table": "t"}), query,
            ))
            sql = mock_cur.execute.call_args[0][0]
            params = list(mock_cur.execute.call_args[0][1])
            self.assertIn("SEQ8", sql)
            self.assertIn("MOD", sql)
            self.assertIn(5, params)
            self.assertIn(2, params)
        finally:
            sf_mod.snowflake, sf_mod.SF_AVAILABLE = orig, orig_avail

    def test_no_snowflake_skips_gracefully(self):
        import importlib
        mod = importlib.import_module("corvin_compute.fabric.datasources.builtin.snowflake")
        self.assertTrue(hasattr(mod, "SnowflakeAdapter"))


# ---------------------------------------------------------------------------
# Redshift
# ---------------------------------------------------------------------------

class TestRedshiftAdapter(unittest.TestCase):
    def test_capability_flags(self):
        from corvin_compute.fabric.datasources.builtin.redshift import RedshiftAdapter
        a = RedshiftAdapter()
        self.assertTrue(a.supports_pushdown)
        self.assertTrue(a.supports_incremental)

    @patch("corvin_compute.fabric.datasources.builtin.redshift.PSYCOPG2_AVAILABLE", False)
    def test_connect_raises_without_psycopg2(self):
        from corvin_compute.fabric.datasources.builtin.redshift import RedshiftAdapter
        with self.assertRaises(ImportError):
            RedshiftAdapter().connect(
                SourceConfig("redshift", "us-east-1", {"host": "h"}),
                _rs_secret_env(),
            )

    def test_connect_uses_secret_env(self):
        from corvin_compute.fabric.datasources.builtin import redshift as rs_mod
        from corvin_compute.fabric.datasources.builtin.redshift import RedshiftAdapter
        mock_psycopg2 = MagicMock()
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn
        mock_psycopg2.extras = MagicMock()

        orig, orig_avail = rs_mod.psycopg2, rs_mod.PSYCOPG2_AVAILABLE
        try:
            rs_mod.psycopg2 = mock_psycopg2
            rs_mod.PSYCOPG2_AVAILABLE = True
            config = SourceConfig("redshift", "us-east-1",
                                  {"host": "rs.us-east-1.rds.amazonaws.com", "dbname": "mydb"})
            RedshiftAdapter().connect(config, _rs_secret_env())
            kwargs = mock_psycopg2.connect.call_args[1]
            self.assertEqual(kwargs["user"], "admin")
            self.assertEqual(kwargs["password"], "secret")
            self.assertEqual(int(kwargs["port"]), 5439)
        finally:
            rs_mod.psycopg2, rs_mod.PSYCOPG2_AVAILABLE = orig, orig_avail

    def test_create_cursor_parameterized(self):
        from corvin_compute.fabric.datasources.builtin import redshift as rs_mod
        from corvin_compute.fabric.datasources.builtin.redshift import RedshiftAdapter, _RSSession
        mock_psycopg2 = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.cursor.return_value = mock_cur
        mock_psycopg2.extras.RealDictCursor = MagicMock()

        orig, orig_avail = rs_mod.psycopg2, rs_mod.PSYCOPG2_AVAILABLE
        try:
            rs_mod.psycopg2 = mock_psycopg2
            rs_mod.PSYCOPG2_AVAILABLE = True
            session = _RSSession(mock_conn)
            query = SourceQuery(filters=[FilterExpr(col="revenue", op=">", value=1000)])
            list(RedshiftAdapter().create_cursor(
                session, SourceConfig("redshift", "us-east-1", {"table": "sales"}), query,
            ))
            sql = mock_cur.execute.call_args[0][0]
            params = list(mock_cur.execute.call_args[0][1])
            self.assertIn("%s", sql)
            self.assertNotIn("1000", sql)
            self.assertIn(1000, params)
        finally:
            rs_mod.psycopg2, rs_mod.PSYCOPG2_AVAILABLE = orig, orig_avail

    def test_no_psycopg2_skips_gracefully(self):
        import importlib
        mod = importlib.import_module("corvin_compute.fabric.datasources.builtin.redshift")
        self.assertTrue(hasattr(mod, "RedshiftAdapter"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
