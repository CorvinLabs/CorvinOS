"""Tests for POST /remote-trigger/pair/friendship/create's URL auto-detect
fallback (2026-08-02).

Reported live: a2a pairing between a Windows and a Linux instance on the
same LAN got stuck at "Imported (URL pending)" / UNREACHABLE. Root cause:
issuing a token with a blank "own URL" field produced url=None in the
token, permanently -- the importer had no address to ever reach, and no
recovery short of the issuer discovering their own LAN IP by hand and
re-pairing. friendship_create() now falls back to the already-configured
"My URL", then to the same auto-detection GET /my-url already offers
(mesh-VPN address, else local outbound-interface IP), so a same-LAN
pairing works without the operator ever needing to know their own address.
"""
from __future__ import annotations

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
_PLUGIN_PARENT = _REPO / "plugins" / "core" / "console"
if _PLUGIN_PARENT.is_dir() and str(_PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PARENT))

from corvin_console.routes import a2a_pair as ap  # type: ignore[import-not-found]


class _FakeRec:
    tenant_id = "_default"
    sid_fingerprint = "fp-test"


class TestFriendshipCreateUrlAutodetect(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-a2a-create-")
        self.dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            "os.environ",
            {
                "CORVIN_HOME": str(self.dir / "corvin_home"),
                "REMOTE_PENDING_FRIENDSHIPS_DIR": str(self.dir / "pending"),
            },
        )
        self._env.start()
        self._audit = mock.patch.object(
            ap.console_audit, "action_performed", mock.MagicMock()
        )
        self._audit.start()

    def tearDown(self):
        self._audit.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _create(self, **body):
        return ap.friendship_create(ap.FriendshipCreateRequest(**body), _FakeRec())

    def test_blank_url_falls_back_to_stored_my_url(self):
        ap._ft.set_my_url("http://192.168.1.42:8765")
        res = self._create(url="")
        # Token payload isn't directly inspectable from the response (only
        # the opaque token string + kid are returned), but the important
        # observable is that the fallback did NOT need to auto-detect
        # (suggest_my_url must not even be called) and the stored value is
        # unchanged, not overwritten with something else.
        self.assertEqual(ap._ft.get_my_url(), "http://192.168.1.42:8765")
        self.assertTrue(res.token)

    def test_blank_url_and_nothing_stored_auto_detects_and_persists(self):
        self.assertIsNone(ap._ft.get_my_url())
        with mock.patch.object(ap._ft, "suggest_my_url", return_value="http://10.0.0.7:8765"):
            self._create(url="")
        # The auto-detected address must be persisted so it's visible under
        # Settings -> A2A and future tokens don't need to re-detect it.
        self.assertEqual(ap._ft.get_my_url(), "http://10.0.0.7:8765")

    def test_blank_url_and_autodetect_fails_degrades_to_none_without_crashing(self):
        # No network / no mesh VPN / detect_local_ip() also empty -- must
        # not raise; the historical "URL pending" outcome is still a valid
        # degrade path when genuinely nothing can be inferred.
        self.assertIsNone(ap._ft.get_my_url())
        with mock.patch.object(ap._ft, "suggest_my_url", return_value=None):
            res = self._create(url="")
        self.assertTrue(res.token)
        self.assertIsNone(ap._ft.get_my_url())

    def test_explicit_url_is_never_overridden_by_autodetect(self):
        with mock.patch.object(ap._ft, "suggest_my_url") as mock_suggest:
            self._create(url="https://explicit.example.com:9000")
            mock_suggest.assert_not_called()
        # remember_url defaults to False, so an explicit one-off URL must
        # NOT silently become the persisted "My URL".
        self.assertIsNone(ap._ft.get_my_url())

    def test_stored_my_url_takes_priority_over_autodetect(self):
        ap._ft.set_my_url("http://192.168.1.42:8765")
        with mock.patch.object(ap._ft, "suggest_my_url") as mock_suggest:
            self._create(url="")
            mock_suggest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
