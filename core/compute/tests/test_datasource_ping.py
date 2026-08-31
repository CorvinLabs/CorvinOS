"""Tests for DataSourceAdapter.ping() honesty (finding #5).

Regression guard: the console "Test connection" button must NOT report a
misleading green "connection OK" for an adapter that has not actually probed
reachability.

Covers:
- BaseDataSourceAdapter.ping() default is an HONEST stub (ok=False), not a
  success no-op.
- LocalFileAdapter.ping() is a real filesystem probe (exists/readable).
- Network adapters do a credential-free TCP reachability probe via
  tcp_reachability_ping (no secrets in detail).
- DataSourceRegistry.test_connection passes the non-secret config into ping()
  and never surfaces raw exception text.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.protocol import (
    BaseDataSourceAdapter,
    PingResult,
    SourceConfig,
    tcp_reachability_ping,
)
from corvin_compute.fabric.datasources.builtin.local_file import LocalFileAdapter
from corvin_compute.fabric.datasources.builtin.postgresql import PostgreSQLAdapter
from corvin_compute.fabric.datasources.builtin.http_rest import HTTPRestAdapter
from corvin_compute.fabric.datasources.builtin.kafka_batch import KafkaBatchAdapter
from corvin_compute.fabric.datasources.registry import DataSourceRegistry


def _free_listener() -> tuple[socket.socket, int]:
    """Return a bound, listening loopback socket and its port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def _closed_port() -> int:
    """Return a port number that is (almost certainly) not accepting."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # close so nothing is listening
    return port


class _StubAdapter(BaseDataSourceAdapter):
    adapter_name = "stub"
    display_name = "Stub"
    description = "Stub adapter for default-ping test."
    supported_formats = frozenset({"csv"})
    locality = "local"
    network_egress = "none"
    config_schema = {"type": "object", "properties": {}}


class TestBaseDefaultPingIsHonest(unittest.TestCase):
    def test_default_ping_is_not_a_success_stub(self):
        result = _StubAdapter().ping()
        self.assertIsInstance(result, PingResult)
        # The whole point of the finding: default MUST NOT be ok=True.
        self.assertFalse(result.ok)
        self.assertIn("not implemented", result.detail)


class TestLocalFilePing(unittest.TestCase):
    def test_existing_readable_file_ok(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "data.csv"
            p.write_text("a,b\n1,2\n", encoding="utf-8")
            result = LocalFileAdapter().ping(
                config=SourceConfig(adapter="local_file", region="local", raw={"path": str(p)})
            )
            self.assertTrue(result.ok)
            self.assertIn("readable", result.detail)

    def test_missing_file_fails(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "nope.csv"
            result = LocalFileAdapter().ping(
                config=SourceConfig(adapter="local_file", region="local", raw={"path": str(p)})
            )
            self.assertFalse(result.ok)
            self.assertIn("not found", result.detail)

    def test_directory_is_not_a_file(self):
        with TemporaryDirectory() as td:
            result = LocalFileAdapter().ping(
                config=SourceConfig(adapter="local_file", region="local", raw={"path": td})
            )
            self.assertFalse(result.ok)
            self.assertIn("not a regular file", result.detail)

    def test_no_path_configured(self):
        result = LocalFileAdapter().ping(
            config=SourceConfig(adapter="local_file", region="local", raw={})
        )
        self.assertFalse(result.ok)
        self.assertIn("no path", result.detail)


class TestTcpReachabilityProbe(unittest.TestCase):
    def test_reachable_host_ok(self):
        listener, port = _free_listener()
        try:
            result = tcp_reachability_ping("127.0.0.1", port, timeout_s=2.0)
            self.assertTrue(result.ok)
            self.assertIn("reachable", result.detail)
            self.assertIn("auth not verified", result.detail)
        finally:
            listener.close()

    def test_unreachable_port_fails(self):
        port = _closed_port()
        result = tcp_reachability_ping("127.0.0.1", port, timeout_s=2.0)
        self.assertFalse(result.ok)
        self.assertIn("unreachable", result.detail)

    def test_no_host_fails(self):
        result = tcp_reachability_ping("", 5432, timeout_s=1.0)
        self.assertFalse(result.ok)

    def test_invalid_port_fails(self):
        result = tcp_reachability_ping("127.0.0.1", "not-a-port", timeout_s=1.0)
        self.assertFalse(result.ok)

    def test_detail_never_contains_password(self):
        # host/port are non-secret; ensure nothing credential-ish leaks.
        port = _closed_port()
        result = tcp_reachability_ping("127.0.0.1", port, timeout_s=1.0)
        self.assertNotIn("password", result.detail.lower())


class TestNetworkAdapterPings(unittest.TestCase):
    def test_postgres_probes_configured_host_port(self):
        listener, port = _free_listener()
        try:
            result = PostgreSQLAdapter().ping(
                config=SourceConfig(
                    adapter="postgresql", region="eu",
                    raw={"host": "127.0.0.1", "port": port},
                )
            )
            self.assertTrue(result.ok)
        finally:
            listener.close()

    def test_postgres_unreachable_fails(self):
        result = PostgreSQLAdapter().ping(
            config=SourceConfig(
                adapter="postgresql", region="eu",
                raw={"host": "127.0.0.1", "port": _closed_port()},
            )
        )
        self.assertFalse(result.ok)

    def test_http_rest_parses_base_url(self):
        listener, port = _free_listener()
        try:
            result = HTTPRestAdapter().ping(
                config=SourceConfig(
                    adapter="http_rest", region="eu",
                    raw={"base_url": f"http://127.0.0.1:{port}/api"},
                )
            )
            self.assertTrue(result.ok)
        finally:
            listener.close()

    def test_http_rest_no_base_url_fails(self):
        result = HTTPRestAdapter().ping(
            config=SourceConfig(adapter="http_rest", region="eu", raw={})
        )
        self.assertFalse(result.ok)

    def test_kafka_probes_first_broker(self):
        listener, port = _free_listener()
        try:
            result = KafkaBatchAdapter().ping(
                config=SourceConfig(
                    adapter="kafka_batch", region="eu",
                    raw={"bootstrap_servers": f"127.0.0.1:{port},other:9092"},
                )
            )
            self.assertTrue(result.ok)
        finally:
            listener.close()


class TestRegistryTestConnection(unittest.TestCase):
    def _write_manifest(self, home: Path, name: str, config: dict) -> None:
        conn_dir = home / "tenants" / "_default" / "datasource_connections"
        conn_dir.mkdir(parents=True, exist_ok=True)
        (conn_dir / f"{name}.json").write_text(
            json.dumps({
                "dsi_version": "1",
                "name": name,
                "adapter": "local_file",
                "config": config,
                "data_classification": "INTERNAL",
                "secrets": [],
                "read_only": True,
            }),
            encoding="utf-8",
        )

    def test_passes_config_into_ping_real_file(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            data = home / "data.csv"
            data.write_text("a\n1\n", encoding="utf-8")
            self._write_manifest(home, "ds_ok", {"path": str(data), "region": "local"})
            reg = DataSourceRegistry(corvin_home=home)
            result = reg.test_connection("ds_ok", "_default", timeout_s=2.0)
            self.assertTrue(result.ok)

    def test_missing_file_reports_failure_not_green(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            self._write_manifest(
                home, "ds_missing",
                {"path": str(home / "absent.csv"), "region": "local"},
            )
            reg = DataSourceRegistry(corvin_home=home)
            result = reg.test_connection("ds_missing", "_default", timeout_s=2.0)
            self.assertFalse(result.ok)

    def test_exception_detail_is_coarse_not_raw(self):
        # Force the adapter load to raise by writing an unknown adapter.
        with TemporaryDirectory() as td:
            home = Path(td)
            conn_dir = home / "tenants" / "_default" / "datasource_connections"
            conn_dir.mkdir(parents=True, exist_ok=True)
            (conn_dir / "ds_bad.json").write_text(
                json.dumps({
                    "dsi_version": "1",
                    "name": "ds_bad",
                    "adapter": "nonexistent_adapter",
                    "config": {},
                    "data_classification": "INTERNAL",
                    "secrets": [],
                    "read_only": True,
                }),
                encoding="utf-8",
            )
            reg = DataSourceRegistry(corvin_home=home)
            result = reg.test_connection("ds_bad", "_default", timeout_s=2.0)
            self.assertFalse(result.ok)
            self.assertEqual(result.detail, "connectivity test failed")


if __name__ == "__main__":
    unittest.main()
