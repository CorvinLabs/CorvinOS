"""Tests for boot-time public key integrity check (ADR-0098)."""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Insert operator/ so that `license` is importable as a package,
# which satisfies validator.py's relative imports (from .limits import ...).
_OPERATOR_DIR = Path(__file__).resolve().parents[2]  # operator/
if str(_OPERATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR_DIR))

from license import session_refresh as _sr  # noqa: E402
from license.validator import SESSION_SERVER_PUBLIC_KEY_B64 as _EMBEDDED_KEY  # noqa: E402


def _mock_urlopen(key_b64: str, status: int = 200):
    """Return a context-manager mock that yields a fake HTTP response.

    Uses the key-ring format (ADR-0098 P3) expected by /v1/keys/session-key-ring.
    """
    body = json.dumps({
        "keys": {"sess-v1": key_b64},
        "current_kid": "sess-v1",
        # Legacy fields so the fallback path also works:
        "public_key_b64": key_b64,
        "kid": "sess-v1",
    }).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class _NoTestMode:
    """Mixin: ensure CORVIN_TEST_MODE is NOT set so check_public_key_integrity runs."""

    def setUp(self) -> None:
        self._env_patcher = patch.dict(os.environ, {}, clear=False)
        self._env_patcher.start()
        os.environ.pop("CORVIN_TEST_MODE", None)

    def tearDown(self) -> None:
        self._env_patcher.stop()


class TestKeyIntegrityMatchingKey(_NoTestMode, unittest.TestCase):
    """Server returns the same key as embedded — no mismatch."""

    def test_returns_true_on_matching_key(self):
        resp = _mock_urlopen(_EMBEDDED_KEY)
        with patch("urllib.request.urlopen", return_value=resp):
            result = _sr.check_public_key_integrity()
        self.assertTrue(result)

    def test_no_audit_event_on_matching_key(self):
        emitted: list[str] = []
        resp = _mock_urlopen(_EMBEDDED_KEY)
        with patch("urllib.request.urlopen", return_value=resp):
            with patch.object(_sr, "_audit_license_event", side_effect=lambda *a, **k: emitted.append(a[0])):
                _sr.check_public_key_integrity()
        self.assertNotIn("license.key_integrity_mismatch", emitted)


class TestKeyIntegrityMismatch(_NoTestMode, unittest.TestCase):
    """Server returns a DIFFERENT key — tampered local copy detected."""

    _FAKE_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    def test_returns_false_on_mismatch(self):
        resp = _mock_urlopen(self._FAKE_KEY)
        with patch("urllib.request.urlopen", return_value=resp):
            result = _sr.check_public_key_integrity()
        self.assertFalse(result)

    def test_emits_audit_event_on_mismatch(self):
        emitted: list[str] = []
        resp = _mock_urlopen(self._FAKE_KEY)
        with patch("urllib.request.urlopen", return_value=resp):
            with patch.object(_sr, "_audit_license_event", side_effect=lambda *a, **k: emitted.append(a[0])):
                _sr.check_public_key_integrity()
        self.assertIn("license.key_integrity_mismatch", emitted)

    def test_logs_critical_on_mismatch(self):
        resp = _mock_urlopen(self._FAKE_KEY)
        with patch("urllib.request.urlopen", return_value=resp):
            with self.assertLogs("corvin.license.session_refresh", level="CRITICAL") as cm:
                _sr.check_public_key_integrity()
        self.assertTrue(any("KEY INTEGRITY MISMATCH" in line for line in cm.output))


class TestKeyIntegrityNetworkError(_NoTestMode, unittest.TestCase):
    """Server unreachable — fail-open so offline installs still work."""

    def test_returns_true_on_network_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = _sr.check_public_key_integrity()
        self.assertTrue(result)

    def test_returns_true_on_timeout(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            result = _sr.check_public_key_integrity()
        self.assertTrue(result)

    def test_no_audit_event_on_network_error(self):
        import urllib.error
        emitted: list[str] = []
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
            with patch.object(_sr, "_audit_license_event", side_effect=lambda *a, **k: emitted.append(a[0])):
                _sr.check_public_key_integrity()
        self.assertEqual(emitted, [])


class TestKeyIntegrityTestMode(unittest.TestCase):
    """CORVIN_TEST_MODE=1 skips the check entirely.

    NOTE: session_refresh.py snapshots CORVIN_TEST_MODE at module import time
    (ADR-0144 Fix B1) to prevent post-boot env-var injection attacks.  That
    means patch.dict(os.environ) has no effect once the module is loaded.
    We therefore patch the snapshot variable directly.
    """

    def test_skips_check_in_test_mode(self):
        called: list[bool] = []
        with patch.object(_sr, "_TEST_MODE_SNAPSHOT", "1"):
            with patch("urllib.request.urlopen", side_effect=lambda *a, **k: called.append(True)):
                result = _sr.check_public_key_integrity()
        self.assertTrue(result)
        self.assertEqual(called, [], "urlopen must not be called in test mode")

    def test_returns_true_in_test_mode(self):
        with patch.object(_sr, "_TEST_MODE_SNAPSHOT", "1"):
            self.assertTrue(_sr.check_public_key_integrity())


class TestKeyIntegrityEmptyServerResponse(_NoTestMode, unittest.TestCase):
    """Server returns empty key — skip gracefully (fail-open)."""

    def test_returns_true_on_empty_key(self):
        resp = _mock_urlopen("")
        with patch("urllib.request.urlopen", return_value=resp):
            result = _sr.check_public_key_integrity()
        self.assertTrue(result)


class TestMappingProxyGcAttack(unittest.TestCase):
    """P2-A (security review 2026-06-18): document that gc.get_referents() can
    bypass MappingProxyType — ADR-0139 accepted risk.

    This test exists to CONFIRM the vulnerability is present (it is accepted),
    not to assert it is fixed. If this test starts FAILING it means the standard
    library or Python runtime has closed the gc bypass, which would be a
    positive development — update ADR-0139 accordingly.
    """

    def test_gc_attack_bypasses_mapping_proxy(self):
        """gc.get_referents() reaches the underlying dict behind a MappingProxy."""
        import gc
        from types import MappingProxyType

        original = {"tier": "free", "engines_allowed": 1}
        proxy = MappingProxyType(original)

        # Direct assignment is blocked (good)
        with self.assertRaises(TypeError):
            proxy["tier"] = "enterprise"

        # gc.get_referents() reaches the underlying dict and mutation succeeds
        underlying = next(
            r for r in gc.get_referents(proxy)
            if isinstance(r, dict) and "tier" in r
        )
        underlying["tier"] = "enterprise"

        # The proxy reflects the mutation — this IS the vulnerability
        self.assertEqual(proxy["tier"], "enterprise",
                         "gc attack bypasses MappingProxyType (ADR-0139 accepted risk — "
                         "test must PASS to confirm the vulnerability is still present)")

        # Reset to avoid cross-test pollution
        underlying["tier"] = "free"
        underlying["engines_allowed"] = 1

    def test_adr0139_compensating_control_note(self):
        """Non-code record: the compensating controls for the gc attack are:
        1. bwrap: Forge tools never run in the adapter process (no gc access)
        2. L10 path-gate: no in-process Python writes to license modules
        3. Operator vetting: in-process MCP servers must be reviewed before load
        See ADR-0139 for the full accepted-risk analysis and the out-of-process
        enforcer (Option A) as the long-term fix.
        """
        # This test documents policy, not code. It always passes.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
