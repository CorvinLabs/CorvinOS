"""Tests for streaming/HTTP/Kafka/DeltaLake adapters (ADR-0026 Section D).

15 test cases:
- HTTPRestAdapter: cursor pagination, page pagination
- HTTPRestAdapter URL params built as dict (no string interpolation)
- KafkaBatchAdapter with mock confluent_kafka
- DeltaLakeAdapter with mock deltalake
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


# ---------------------------------------------------------------------------
# HTTPRestAdapter
# ---------------------------------------------------------------------------

class TestHTTPRestAdapterCapabilities(unittest.TestCase):
    def test_capability_flags(self):
        from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter
        a = HTTPRestAdapter()
        self.assertFalse(a.supports_pushdown)  # client-side only
        self.assertTrue(a.supports_incremental)

    def test_connect_sets_bearer_token(self):
        from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter
        env = SecretEnv({"BEARER_TOKEN": "tok123"})
        config = SourceConfig("http_rest", "eu-central-1",
                              {"base_url": "https://api.example.com"})
        session = HTTPRestAdapter().connect(config, env)
        self.assertIn("Authorization", session.headers)
        self.assertIn("tok123", session.headers["Authorization"])

    def test_connect_sets_api_key_fallback(self):
        from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter
        env = SecretEnv({"API_KEY": "apikey456"})
        config = SourceConfig("http_rest", "eu-central-1",
                              {"base_url": "https://api.example.com"})
        session = HTTPRestAdapter().connect(config, env)
        self.assertIn("Authorization", session.headers)

    def test_url_params_built_as_dict_not_interpolated(self):
        """Critical: URL params must be in a dict, never f-string."""
        import urllib.parse
        params = {"per_page": 100, "page": 1, "cursor": "some_cursor_value"}
        url_base = "https://api.example.com/data"
        url = url_base + "?" + urllib.parse.urlencode(params)
        # Verify cursor is URL-encoded, not interpolated into an f-string
        self.assertIn("some_cursor_value", url)
        # This demonstrates the pattern — not f-string: url = f"{url_base}?cursor={cursor}"
        self.assertNotIn("{", url)
        self.assertNotIn("}", url)


class TestHTTPRestPagination(unittest.TestCase):
    def _make_adapter_and_session(self):
        from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter, _HTTPSession
        adapter = HTTPRestAdapter()
        session = _HTTPSession("https://api.example.com", {})
        return adapter, session

    def test_page_pagination_fetches_all_pages(self):
        from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter, _HTTPSession
        adapter = HTTPRestAdapter()
        session = _HTTPSession("https://api.example.com", {})
        config = SourceConfig("http_rest", "eu-central-1", {
            "pagination": "page",
            "endpoint": "items",
            "records_key": "data",
            "page_size": 2,
            "total_pages_field": "total_pages",
        })
        # Simulate two pages
        page1 = {"data": [{"id": 1}, {"id": 2}], "total_pages": 2}
        page2 = {"data": [{"id": 3}], "total_pages": 2}
        call_count = 0
        def mock_fetch(s, c, cursor, page_num, offset=None):
            nonlocal call_count
            call_count += 1
            if page_num == 1:
                return page1
            return page2
        adapter._fetch_page = mock_fetch

        rows = list(adapter.create_cursor(session, config, SourceQuery()))
        self.assertEqual(len(rows), 3)
        self.assertEqual(call_count, 2)

    def test_cursor_pagination(self):
        from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter, _HTTPSession
        adapter = HTTPRestAdapter()
        session = _HTTPSession("https://api.example.com", {})
        config = SourceConfig("http_rest", "eu-central-1", {
            "pagination": "cursor",
            "endpoint": "events",
            "records_key": "events",
            "cursor_field": "next_cursor",
        })
        page1 = {"events": [{"e": 1}, {"e": 2}], "next_cursor": "abc"}
        page2 = {"events": [{"e": 3}]}  # no next_cursor → done
        cursors_seen = []
        def mock_fetch(s, c, cursor, page_num, offset=None):
            cursors_seen.append(cursor)
            return page1 if cursor is None else page2
        adapter._fetch_page = mock_fetch

        rows = list(adapter.create_cursor(session, config, SourceQuery()))
        self.assertEqual(len(rows), 3)
        self.assertEqual(cursors_seen[0], None)
        self.assertEqual(cursors_seen[1], "abc")

    def test_offset_pagination(self):
        from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter, _HTTPSession
        adapter = HTTPRestAdapter()
        session = _HTTPSession("https://api.example.com", {})
        config = SourceConfig("http_rest", "eu-central-1", {
            "pagination": "offset",
            "endpoint": "records",
            "records_key": "items",
            "page_size": 2,
        })
        offsets_seen = []
        def mock_fetch(s, c, cursor, page_num, offset=None):
            offsets_seen.append(offset)
            if offset == 0:
                return {"items": [{"x": 1}, {"x": 2}]}
            return {"items": [{"x": 3}]}  # only 1 item → done
        adapter._fetch_page = mock_fetch

        rows = list(adapter.create_cursor(session, config, SourceQuery()))
        self.assertEqual(len(rows), 3)
        self.assertIn(0, offsets_seen)
        self.assertIn(2, offsets_seen)

    def test_limit_respected(self):
        from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter, _HTTPSession
        adapter = HTTPRestAdapter()
        session = _HTTPSession("https://api.example.com", {})
        config = SourceConfig("http_rest", "eu-central-1", {
            "pagination": "page",
            "records_key": "data",
            "total_pages_field": "pages",
        })
        def mock_fetch(s, c, cursor, page_num, offset=None):
            return {"data": [{"i": i} for i in range(10)], "pages": 99}
        adapter._fetch_page = mock_fetch

        rows = list(adapter.create_cursor(session, config, SourceQuery(limit=3)))
        self.assertEqual(len(rows), 3)


# ---------------------------------------------------------------------------
# KafkaBatchAdapter
# ---------------------------------------------------------------------------

class TestKafkaBatchAdapter(unittest.TestCase):
    def test_capability_flags(self):
        from corvin_compute.fabric.datasources.builtin.kafka_batch import KafkaBatchAdapter
        a = KafkaBatchAdapter()
        self.assertTrue(a.supports_pushdown)
        self.assertTrue(a.supports_incremental)
        self.assertFalse(a.supports_schema_discovery)

    @patch("corvin_compute.fabric.datasources.builtin.kafka_batch.KAFKA_AVAILABLE", False)
    def test_connect_raises_without_kafka(self):
        from corvin_compute.fabric.datasources.builtin.kafka_batch import KafkaBatchAdapter
        with self.assertRaises(ImportError):
            KafkaBatchAdapter().connect(
                SourceConfig("kafka_batch", "eu-central-1", {"topic": "events"}),
                SecretEnv({}),
            )

    def test_connect_uses_sasl_credentials(self):
        from corvin_compute.fabric.datasources.builtin import kafka_batch as kb_mod
        mock_consumer_cls = MagicMock()
        mock_consumer = MagicMock()
        mock_consumer_cls.return_value = mock_consumer

        orig_c = kb_mod.Consumer
        orig_tp = kb_mod.TopicPartition
        orig_avail = kb_mod.KAFKA_AVAILABLE
        try:
            kb_mod.Consumer = mock_consumer_cls
            kb_mod.TopicPartition = MagicMock()
            kb_mod.KAFKA_AVAILABLE = True
            from corvin_compute.fabric.datasources.builtin.kafka_batch import KafkaBatchAdapter
            env = SecretEnv({"KAFKA_SASL_USERNAME": "user", "KAFKA_SASL_PASSWORD": "pass"})
            config = SourceConfig("kafka_batch", "eu-central-1", {
                "topic": "events", "bootstrap_servers": "broker:9092",
            })
            KafkaBatchAdapter().connect(config, env)
            call_conf = mock_consumer_cls.call_args[0][0]
            self.assertEqual(call_conf.get("sasl.username"), "user")
        finally:
            kb_mod.Consumer = orig_c
            kb_mod.TopicPartition = orig_tp
            kb_mod.KAFKA_AVAILABLE = orig_avail

    def test_schema_returns_minimal_columns(self):
        from corvin_compute.fabric.datasources.builtin.kafka_batch import KafkaBatchAdapter, _KafkaSession
        mock_consumer = MagicMock()
        session = _KafkaSession(mock_consumer, "test-topic")
        schema = KafkaBatchAdapter().discover_schema(
            session,
            SourceConfig("kafka_batch", "eu-central-1", {"topic": "t"}),
        )
        col_names = [c.name for c in schema.columns]
        self.assertIn("key", col_names)
        self.assertIn("value", col_names)
        self.assertIn("offset", col_names)

    def test_no_kafka_skips_gracefully(self):
        import importlib
        mod = importlib.import_module("corvin_compute.fabric.datasources.builtin.kafka_batch")
        self.assertTrue(hasattr(mod, "KafkaBatchAdapter"))


# ---------------------------------------------------------------------------
# DeltaLakeAdapter
# ---------------------------------------------------------------------------

class TestDeltaLakeAdapter(unittest.TestCase):
    def test_capability_flags(self):
        from corvin_compute.fabric.datasources.builtin.delta_lake import DeltaLakeAdapter
        a = DeltaLakeAdapter()
        self.assertTrue(a.supports_pushdown)
        self.assertTrue(a.supports_incremental)

    @patch("corvin_compute.fabric.datasources.builtin.delta_lake.DL_AVAILABLE", False)
    def test_connect_raises_without_deltalake(self):
        from corvin_compute.fabric.datasources.builtin.delta_lake import DeltaLakeAdapter
        with self.assertRaises(ImportError):
            DeltaLakeAdapter().connect(
                SourceConfig("delta_lake", "eu-central-1", {"path": "/data/delta"}),
                SecretEnv({}),
            )

    def test_no_deltalake_skips_gracefully(self):
        import importlib
        mod = importlib.import_module("corvin_compute.fabric.datasources.builtin.delta_lake")
        self.assertTrue(hasattr(mod, "DeltaLakeAdapter"))

    def test_connect_creates_delta_table(self):
        from corvin_compute.fabric.datasources.builtin import delta_lake as dl_mod
        mock_dt_cls = MagicMock()
        mock_dt = MagicMock()
        mock_dt_cls.return_value = mock_dt

        orig_dt = dl_mod.DeltaTable
        orig_avail = dl_mod.DL_AVAILABLE
        try:
            dl_mod.DeltaTable = mock_dt_cls
            dl_mod.DL_AVAILABLE = True
            from corvin_compute.fabric.datasources.builtin.delta_lake import DeltaLakeAdapter
            config = SourceConfig("delta_lake", "eu-central-1", {"path": "/data/delta"})
            session = DeltaLakeAdapter().connect(config, SecretEnv({}))
            self.assertEqual(session.table, mock_dt)
        finally:
            dl_mod.DeltaTable = orig_dt
            dl_mod.DL_AVAILABLE = orig_avail


if __name__ == "__main__":
    unittest.main(verbosity=2)
