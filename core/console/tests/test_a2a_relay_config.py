"""Tests for the ADR-0258 Stage 3 relay config surface added 2026-08-03:

- GET/POST /remote-trigger/pair/relay-url (previously no Console route
  existed at all — CORVIN_A2A_RELAY_URL / hand-edited config file were the
  only way to set a relay URL, despite the a2a_relay_fallback flag's own
  description promising "Settings -> A2A -> Relay URL").
- POST /remote-trigger/pair/friendship/{kid}/enable-relay — one-click,
  peer-scoped-in-the-UI opt-in that sets relay_url (if given) and flips the
  a2a_relay_fallback flag via the SAME tenant overlay the Settings toggle
  uses, then re-verifies reachability.
- `via` ("direct"/"relay") now flows from RemoteTriggerSender.ping() through
  _recheck_connection() into the persisted connection record and the
  /friendship/connections listing.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CONSOLE_PARENT = _HERE.parent  # core/console
if str(_CONSOLE_PARENT) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_PARENT))
_REPO = _HERE.parents[2]
_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

from fastapi import HTTPException  # noqa: E402

from corvin_console.routes import a2a_pair as ap  # type: ignore[import-not-found]  # noqa: E402
import a2a_friendship as ft  # type: ignore[import-not-found]  # noqa: E402


class _FakeRec:
    tenant_id = "_default"
    sid_fingerprint = "fp-test"


class _FakePingResult:
    def __init__(self, reachable: bool, via: str = "direct"):
        self.reachable = reachable
        self.via = via


class _RelayConfigTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-a2a-relay-")
        self.dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            "os.environ",
            {
                "CORVIN_HOME": str(self.dir / "corvin_home"),
                "REMOTE_ORIGINS_DIR": str(self.dir / "origins"),
                "REMOTE_ENDPOINTS_DIR": str(self.dir / "endpoints"),
                "REMOTE_PENDING_FRIENDSHIPS_DIR": str(self.dir / "pending"),
            },
            clear=False,
        )
        self._env.start()
        # CORVIN_A2A_RELAY_URL must not leak in from the real environment —
        # get_my_relay_url() prioritizes the env var over the config file.
        import os
        if "CORVIN_A2A_RELAY_URL" in os.environ:
            self._relay_env = mock.patch.dict("os.environ", {}, clear=False)
            del os.environ["CORVIN_A2A_RELAY_URL"]
        self._audit = mock.patch.object(
            ap.console_audit, "action_performed", mock.MagicMock()
        )
        self._audit.start()

    def tearDown(self):
        self._audit.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _write_friendship(self, kid: str, *, url: str = "http://peer.example:8765/v1/a2a/receive"):
        (self.dir / "origins").mkdir(parents=True, exist_ok=True)
        (self.dir / "endpoints").mkdir(parents=True, exist_ok=True)
        origin = {
            "origin_id": kid, "hmac_key": "a" * 64, "recv_key": "b" * 64,
            "enabled": True, "state": "UNREACHABLE", "_friendship": True,
        }
        endpoint = {
            "endpoint_id": kid, "url": url, "hmac_key": "a" * 64, "recv_key": "b" * 64,
            "origin_id_for_send": kid, "enabled": True, "state": "UNREACHABLE",
            "_friendship": True,
        }
        (self.dir / "origins" / f"{kid}.json").write_text(json.dumps(origin))
        (self.dir / "endpoints" / f"{kid}.json").write_text(json.dumps(endpoint))


class TestRelayUrlRoutes(_RelayConfigTestBase):
    def test_get_relay_url_defaults_to_none_and_flag_off(self):
        res = ap.get_my_a2a_relay_url(_FakeRec())
        self.assertIsNone(res["url"])
        self.assertFalse(res["flag_enabled"])

    def test_set_relay_url_persists_and_is_returned_by_get(self):
        ap.set_my_a2a_relay_url(ap.RelayUrlRequest(url="wss://relay.example.com:9443"), _FakeRec())
        res = ap.get_my_a2a_relay_url(_FakeRec())
        self.assertEqual(res["url"], "wss://relay.example.com:9443")

    def test_setting_relay_url_does_not_flip_the_flag(self):
        ap.set_my_a2a_relay_url(ap.RelayUrlRequest(url="wss://relay.example.com:9443"), _FakeRec())
        res = ap.get_my_a2a_relay_url(_FakeRec())
        self.assertFalse(res["flag_enabled"])

    def test_rejects_http_scheme(self):
        with self.assertRaises(HTTPException) as cm:
            ap.set_my_a2a_relay_url(ap.RelayUrlRequest(url="http://relay.example.com"), _FakeRec())
        self.assertEqual(cm.exception.status_code, 400)

    def test_rejects_missing_host(self):
        with self.assertRaises(HTTPException):
            ap.set_my_a2a_relay_url(ap.RelayUrlRequest(url="wss://"), _FakeRec())

    def test_rejects_embedded_credentials(self):
        with self.assertRaises(HTTPException):
            ap.set_my_a2a_relay_url(
                ap.RelayUrlRequest(url="wss://user:pass@relay.example.com"), _FakeRec()
            )

    def test_accepts_plain_ws_scheme(self):
        ap.set_my_a2a_relay_url(ap.RelayUrlRequest(url="ws://relay.internal:9000"), _FakeRec())
        self.assertEqual(ft.get_my_relay_url(), "ws://relay.internal:9000")


class TestEnableRelayForPeer(_RelayConfigTestBase):
    def test_invalid_kid_rejected(self):
        with self.assertRaises(HTTPException) as cm:
            ap.friendship_enable_relay(
                "../etc", ap.EnableRelayRequest(relay_url=""), _FakeRec()
            )
        self.assertEqual(cm.exception.status_code, 400)

    def test_unknown_kid_rejected(self):
        with self.assertRaises(HTTPException) as cm:
            ap.friendship_enable_relay(
                "nope", ap.EnableRelayRequest(relay_url="wss://relay.example.com"), _FakeRec()
            )
        self.assertEqual(cm.exception.status_code, 404)

    def test_no_relay_url_anywhere_rejected(self):
        self._write_friendship("peerA")
        with self.assertRaises(HTTPException) as cm:
            ap.friendship_enable_relay("peerA", ap.EnableRelayRequest(relay_url=""), _FakeRec())
        self.assertEqual(cm.exception.status_code, 400)
        # Must not have flipped the flag on a rejected request.
        self.assertFalse(ap._ff.is_enabled("a2a_relay_fallback"))

    def test_happy_path_sets_url_flips_flag_and_rechecks(self):
        self._write_friendship("peerA")
        with mock.patch("remote_trigger_sender.RemoteTriggerSender.ping",
                         return_value=_FakePingResult(True, via="relay")):
            result = ap.friendship_enable_relay(
                "peerA", ap.EnableRelayRequest(relay_url="wss://relay.example.com:9443"), _FakeRec()
            )
        self.assertTrue(ap._ff.is_enabled("a2a_relay_fallback"))
        self.assertEqual(ft.get_my_relay_url(), "wss://relay.example.com:9443")
        self.assertTrue(result["relay_enabled"])
        self.assertEqual(result["state"], "ACTIVE")
        self.assertEqual(result["via"], "relay")

    def test_reuses_already_configured_relay_url_when_none_given(self):
        self._write_friendship("peerA")
        ft.set_my_relay_url("wss://already-set.example.com")
        with mock.patch("remote_trigger_sender.RemoteTriggerSender.ping",
                         return_value=_FakePingResult(True, via="relay")):
            result = ap.friendship_enable_relay(
                "peerA", ap.EnableRelayRequest(relay_url=""), _FakeRec()
            )
        self.assertTrue(result["relay_enabled"])
        self.assertEqual(ft.get_my_relay_url(), "wss://already-set.example.com")

    def tearDown(self):
        # The flag write lands in the tmp CORVIN_HOME overlay (isolated per
        # test), but be defensive in case a future refactor changes that.
        try:
            ap._ff.set_enabled("a2a_relay_fallback", False)
        except Exception:
            pass
        super().tearDown()


class TestViaPropagation(_RelayConfigTestBase):
    def test_recheck_persists_via_and_connections_reports_it(self):
        self._write_friendship("peerA")
        with mock.patch("remote_trigger_sender.RemoteTriggerSender.ping",
                         return_value=_FakePingResult(True, via="relay")):
            result = ap.friendship_recheck("peerA", _FakeRec())
        self.assertEqual(result["state"], "ACTIVE")
        self.assertEqual(result["via"], "relay")

        listing = ap.friendship_connections(_FakeRec())
        conn = next(c for c in listing["connections"] if c["kid"] == "peerA")
        self.assertEqual(conn["via"], "relay")

    def test_via_is_sticky_across_a_failed_recheck(self):
        self._write_friendship("peerA")
        with mock.patch("remote_trigger_sender.RemoteTriggerSender.ping",
                         return_value=_FakePingResult(True, via="relay")):
            ap.friendship_recheck("peerA", _FakeRec())
        with mock.patch("remote_trigger_sender.RemoteTriggerSender.ping",
                         return_value=_FakePingResult(False)):
            result = ap.friendship_recheck("peerA", _FakeRec())
        self.assertEqual(result["state"], "UNREACHABLE")
        self.assertIsNone(result["via"])  # this check's own via
        listing = ap.friendship_connections(_FakeRec())
        conn = next(c for c in listing["connections"] if c["kid"] == "peerA")
        # Persisted _last_via is sticky — still shows the last time it WAS
        # reachable, so the UI can say "last seen via relay".
        self.assertEqual(conn["via"], "relay")

    def test_direct_reachability_reports_via_direct(self):
        self._write_friendship("peerA")
        with mock.patch("remote_trigger_sender.RemoteTriggerSender.ping",
                         return_value=_FakePingResult(True, via="direct")):
            result = ap.friendship_recheck("peerA", _FakeRec())
        self.assertEqual(result["via"], "direct")


if __name__ == "__main__":
    unittest.main()
