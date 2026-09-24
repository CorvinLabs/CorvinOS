"""Unit tests for the dedicated A2A ingress listener's gates (a2a_ingress.py).

The listener itself is exercised end-to-end by test_a2a_zero_config_e2e.py;
these pin the pure decisions: which peer addresses are served, how the
config resolves, and the rate limiter.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_ingress as ing  # noqa: E402


class TestPeerGate(unittest.TestCase):
    def test_private_and_loopback_peers_are_served(self):
        for addr in ("127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.2.131",
                     "::1", "fd12:3456::1", "::ffff:192.168.2.131", "100.101.102.103"):
            self.assertTrue(ing.peer_allowed(addr, allow_public=False), addr)

    def test_public_peers_are_refused_unless_opted_in(self):
        for addr in ("8.8.8.8", "2001:4860:4860::8888", "::ffff:8.8.8.8", "not-an-ip", ""):
            self.assertFalse(ing.peer_allowed(addr, allow_public=False), addr)
        self.assertTrue(ing.peer_allowed("8.8.8.8", allow_public=True))

    def test_unspecified_address_is_refused(self):
        self.assertFalse(ing.peer_allowed("0.0.0.0", allow_public=False))


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"CORVIN_HOME": self._tmp.name}, clear=False)
        self._env.start()
        for k in ("CORVIN_A2A_INGRESS", "CORVIN_A2A_INGRESS_PORT"):
            os.environ.pop(k, None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_default_is_enabled_on_8775_private_only(self):
        cfg = ing.load_config()
        self.assertEqual((cfg.enabled, cfg.port, cfg.allow_public), (True, 8775, False))

    def test_file_and_env_overrides(self):
        p = ing.config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"enabled": True, "port": 9100, "allow_public": True}))
        cfg = ing.load_config()
        self.assertEqual((cfg.port, cfg.allow_public), (9100, True))
        os.environ["CORVIN_A2A_INGRESS"] = "off"
        os.environ["CORVIN_A2A_INGRESS_PORT"] = "9200"
        cfg = ing.load_config()
        self.assertEqual((cfg.enabled, cfg.port), (False, 9200))

    def test_garbage_config_falls_back_to_defaults(self):
        p = ing.config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        self.assertEqual(ing.load_config().port, 8775)
        p.write_text(json.dumps({"port": 70000}))
        self.assertEqual(ing.load_config().port, 8775)


class TestRateLimiter(unittest.TestCase):
    def test_burst_then_refusal_per_address(self):
        rl = ing._RateLimiter(burst=3, per_s=0.0)
        self.assertEqual([rl.allow("a") for _ in range(4)], [True, True, True, False])
        self.assertTrue(rl.allow("b"))  # independent bucket


class TestRunnerDisabled(unittest.TestCase):
    def test_disabled_config_binds_nothing(self):
        runner = ing.IngressRunner(receiver=object(), endpoints_dir=Path("/nonexistent"),
                                   pending_dir=Path("/nonexistent"))
        self.assertEqual(runner.ensure(ing.IngressConfig(enabled=False)), "unchanged")
        self.assertFalse(runner.running)


if __name__ == "__main__":
    unittest.main()
