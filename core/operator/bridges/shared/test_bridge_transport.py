"""Tests for ADR-0159 M4 — bridge_transport.py and sitecustomize.py update."""
from __future__ import annotations

import ast
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
_SHARED = str(_REPO / "operator" / "bridges" / "shared")
_HELPERS = str(_REPO / "operator" / "forge" / "forge" / "sandbox_helpers")
for _p in (_SHARED,):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── bridge_transport ─────────────────────────────────────────────────────────

class TestBridgeTransportDetection(unittest.TestCase):
    def setUp(self):
        os.environ.pop("CORVIN_BRIDGE_TRANSPORT", None)

    def tearDown(self):
        os.environ.pop("CORVIN_BRIDGE_TRANSPORT", None)

    def test_unix_socket_on_linux(self):
        import bridge_transport as bt
        with patch.object(bt, "_have_unix_socket", return_value=True):
            self.assertEqual(bt.get_transport(), "unix_socket")

    def test_tcp_loopback_on_windows(self):
        import bridge_transport as bt
        with patch.object(bt, "_have_unix_socket", return_value=False):
            self.assertEqual(bt.get_transport(), "tcp_loopback")

    def test_env_override_tcp(self):
        import bridge_transport as bt
        os.environ["CORVIN_BRIDGE_TRANSPORT"] = "tcp_loopback"
        self.assertEqual(bt.get_transport(), "tcp_loopback")

    def test_env_override_unix(self):
        import bridge_transport as bt
        os.environ["CORVIN_BRIDGE_TRANSPORT"] = "unix_socket"
        self.assertEqual(bt.get_transport(), "unix_socket")

    def test_invalid_env_ignored(self):
        import bridge_transport as bt
        os.environ["CORVIN_BRIDGE_TRANSPORT"] = "invalid_transport"
        # Falls back to platform detection
        result = bt.get_transport()
        self.assertIn(result, ("unix_socket", "tcp_loopback"))


class TestBridgeTransportProbeUnix(unittest.TestCase):
    """probe_socket via Unix domain socket (Linux/macOS only)."""

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "AF_UNIX not available")
    def test_probe_unix_success(self):
        import bridge_transport as bt
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sock_path = Path(td) / "test.sock"
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(sock_path))
            srv.listen(1)
            # Start accept loop in daemon thread
            def _accept():
                try:
                    conn, _ = srv.accept()
                    conn.close()
                except OSError:
                    pass
            t = threading.Thread(target=_accept, daemon=True)
            t.start()
            reachable = bt.probe_socket(sock_path, timeout=1.0)
            srv.close()
            t.join(timeout=1.0)
        self.assertTrue(reachable)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "AF_UNIX not available")
    def test_probe_unix_missing_file(self):
        import bridge_transport as bt
        sock_path = Path("/tmp/nonexistent_corvin_test_8675309.sock")
        self.assertFalse(bt.probe_socket(sock_path, timeout=0.1))


class TestBridgeTransportProbeTCP(unittest.TestCase):
    """probe_socket via TCP loopback (cross-platform)."""

    def test_probe_tcp_success(self):
        import bridge_transport as bt
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sock_path = Path(td) / "test.sock"
            # Start a TCP echo server on a random port
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            # Write port file
            port_file = bt._port_file_for(sock_path)
            port_file.write_text(str(port), encoding="ascii")
            def _accept():
                try:
                    conn, _ = srv.accept()
                    conn.close()
                except OSError:
                    pass
            t = threading.Thread(target=_accept, daemon=True)
            t.start()
            with patch.object(bt, "get_transport", return_value="tcp_loopback"):
                reachable = bt.probe_socket(sock_path, timeout=1.0)
            srv.close()
            t.join(timeout=1.0)
        self.assertTrue(reachable)

    def test_probe_tcp_no_port_file(self):
        import bridge_transport as bt
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sock_path = Path(td) / "noport.sock"
            with patch.object(bt, "get_transport", return_value="tcp_loopback"):
                reachable = bt.probe_socket(sock_path, timeout=0.1)
        self.assertFalse(reachable)

    def test_port_file_invalid_content(self):
        import bridge_transport as bt
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sock_path = Path(td) / "bad.sock"
            bt._port_file_for(sock_path).write_text("not_a_port", encoding="ascii")
            self.assertIsNone(bt.read_port_file(sock_path))

    def test_port_file_out_of_range(self):
        import bridge_transport as bt
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sock_path = Path(td) / "oob.sock"
            bt._port_file_for(sock_path).write_text("99999", encoding="ascii")
            self.assertIsNone(bt.read_port_file(sock_path))


class TestBridgeTransportNoAnthropicImport(unittest.TestCase):
    def test_no_anthropic_import(self):
        src = Path(_SHARED) / "bridge_transport.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    self.assertFalse(
                        (name or "").startswith("anthropic"),
                        f"bridge_transport.py must not import anthropic (found: {name!r})",
                    )


# ── sitecustomize.py bridge-port exception ───────────────────────────────────

class TestSitecustomizeBridgePortException(unittest.TestCase):
    """Verify the CORVIN_BRIDGE_PORT exception in sitecustomize.py."""

    def _load_fresh(self, bridge_port: str = "") -> object:
        """Reload sitecustomize with a fresh module to pick up env var."""
        import importlib
        import importlib.util
        shim_path = Path(_HELPERS) / "sitecustomize.py"
        env_backup = os.environ.get("CORVIN_BRIDGE_PORT")
        if bridge_port:
            os.environ["CORVIN_BRIDGE_PORT"] = bridge_port
        else:
            os.environ.pop("CORVIN_BRIDGE_PORT", None)
        spec = importlib.util.spec_from_file_location("_sc_test", shim_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Restore env
        if env_backup is not None:
            os.environ["CORVIN_BRIDGE_PORT"] = env_backup
        else:
            os.environ.pop("CORVIN_BRIDGE_PORT", None)
        return mod

    def test_loopback_blocked_without_bridge_port(self):
        mod = self._load_fresh("")
        self.assertFalse(mod._is_bridge_port_exception(("127.0.0.1", 12345)))

    def test_bridge_port_allows_exact_match(self):
        mod = self._load_fresh("12345")
        self.assertTrue(mod._is_bridge_port_exception(("127.0.0.1", 12345)))

    def test_bridge_port_blocks_different_port(self):
        mod = self._load_fresh("12345")
        # Same host, different port → NOT an exception
        self.assertFalse(mod._is_bridge_port_exception(("127.0.0.1", 12346)))

    def test_bridge_port_blocks_non_loopback(self):
        """Even with CORVIN_BRIDGE_PORT set, non-loopback addresses are not bridged."""
        mod = self._load_fresh("12345")
        # External host, correct port — should NOT be excepted
        self.assertFalse(mod._is_bridge_port_exception(("8.8.8.8", 12345)))

    def test_invalid_bridge_port_env_ignored(self):
        mod = self._load_fresh("not_a_number")
        self.assertEqual(mod._BRIDGE_PORT, 0)
        self.assertFalse(mod._is_bridge_port_exception(("127.0.0.1", 0)))

    def test_bridge_port_zero_excluded(self):
        mod = self._load_fresh("0")
        self.assertEqual(mod._BRIDGE_PORT, 0)

    def test_loopback_still_blocked_for_other_ports(self):
        """With bridge port set, OTHER loopback connections are still blocked."""
        mod = self._load_fresh("12345")
        # 127.0.0.1:9999 is not the bridge port → still blocked by _is_blocked
        self.assertTrue(mod._is_blocked("127.0.0.1"))
        # But the exception is only for the exact port
        self.assertFalse(mod._is_bridge_port_exception(("127.0.0.1", 9999)))


if __name__ == "__main__":
    unittest.main()
