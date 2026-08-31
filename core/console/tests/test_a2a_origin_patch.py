"""Tests for PATCH /remote-trigger/origins/{id} — per-connection rights + rename."""
from __future__ import annotations

import contextlib
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
_PLUGIN_PARENT = _REPO / "plugins" / "core" / "console"
if _PLUGIN_PARENT.is_dir() and str(_PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PARENT))

from fastapi import HTTPException  # type: ignore[import-not-found]

from corvin_console.routes import a2a_pair as ap  # type: ignore[import-not-found]
from corvin_console.routes import remote_trigger_log as rtl  # type: ignore[import-not-found]


class _FakeRec:
    tenant_id = "_default"
    sid_fingerprint = "fp-test"


def _write_origin(dirpath: Path, origin_id: str, **extra) -> Path:
    cfg = {
        "origin_id": origin_id,
        "hmac_key": "a" * 64,
        "recv_key": "b" * 64,
        "enabled": True,
        "spawn_worker": False,
        "allowed_personas": ["assistant"],
    }
    cfg.update(extra)
    path = dirpath / f"{origin_id}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    path.chmod(0o600)
    return path


class TestPatchOrigin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-origins-")
        self.dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            "os.environ", {"REMOTE_ORIGINS_DIR": self._tmp.name}
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

    def _patch(self, origin_id: str, **body):
        return ap.patch_origin(origin_id, ap.OriginPatchRequest(**body), _FakeRec())

    def test_tool_flags_are_persisted_and_returned(self):
        _write_origin(self.dir, "o1")
        res = self._patch(
            "o1", spawn_worker=True, allow_bash=True, allow_network=True,
        )
        self.assertTrue(res["allow_bash"])
        self.assertTrue(res["allow_network"])
        self.assertFalse(res["allow_write_files"])
        cfg = json.loads((self.dir / "o1.json").read_text("utf-8"))
        self.assertTrue(cfg["allow_bash"])
        self.assertTrue(cfg["allow_network"])
        self.assertNotIn("allow_write_files", cfg)  # untouched stays absent

    def test_tool_flag_can_be_revoked_retroactively(self):
        _write_origin(self.dir, "o1", allow_bash=True, spawn_worker=True)
        res = self._patch("o1", allow_bash=False)
        self.assertFalse(res["allow_bash"])
        cfg = json.loads((self.dir / "o1.json").read_text("utf-8"))
        self.assertFalse(cfg["allow_bash"])

    def test_label_set_and_cleared(self):
        _write_origin(self.dir, "o1")
        res = self._patch("o1", label="  Papa Laptop  ")
        self.assertEqual(res["label"], "Papa Laptop")
        res = self._patch("o1", label="")
        self.assertIsNone(res["label"])
        cfg = json.loads((self.dir / "o1.json").read_text("utf-8"))
        self.assertNotIn("label", cfg)

    def test_label_control_chars_stripped(self):
        _write_origin(self.dir, "o1")
        res = self._patch("o1", label="Pa\x00pa\n\x1b[31m")
        self.assertNotIn("\x00", res["label"])
        self.assertNotIn("\n", res["label"])
        self.assertNotIn("\x1b", res["label"])

    def test_keys_survive_patch(self):
        _write_origin(self.dir, "o1")
        self._patch("o1", enabled=False, label="x")
        cfg = json.loads((self.dir / "o1.json").read_text("utf-8"))
        self.assertEqual(cfg["hmac_key"], "a" * 64)
        self.assertEqual(cfg["recv_key"], "b" * 64)
        self.assertFalse(cfg["enabled"])

    def test_path_traversal_rejected(self):
        for bad in ("../x", "a/b", "a\\b", ".hidden", ""):
            with self.assertRaises(HTTPException) as ctx:
                self._patch(bad, enabled=True)
            self.assertIn(ctx.exception.status_code, (400, 404))

    def test_unknown_origin_404(self):
        with self.assertRaises(HTTPException) as ctx:
            self._patch("missing", enabled=True)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_audit_records_changed_rights(self):
        _write_origin(self.dir, "o1")
        self._patch("o1", spawn_worker=True, allow_bash=True)
        call = ap.console_audit.action_performed.call_args
        trigger = call.kwargs["trigger"]
        self.assertIn("allow_bash=True", trigger)
        self.assertIn("spawn_worker=True", trigger)

    def test_oversized_persona_list_rejected(self):
        _write_origin(self.dir, "o1")
        with self.assertRaises(HTTPException) as ctx:
            self._patch("o1", allowed_personas=[f"p{i}" for i in range(40)])
        self.assertEqual(ctx.exception.status_code, 400)

    def test_control_char_persona_rejected(self):
        _write_origin(self.dir, "o1")
        with self.assertRaises(HTTPException) as ctx:
            self._patch("o1", allowed_personas=["ok", "bad\x00persona"])
        self.assertEqual(ctx.exception.status_code, 400)

    def test_patch_response_sanitizes_untouched_stored_label(self):
        # A4-RESIDUAL (2026-07-20): when the PATCH body does NOT carry a label,
        # the response echoes the raw on-disk value — a pre-existing bidi/ANSI
        # label must be sanitized on the way out too.
        rlo = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
        _write_origin(self.dir, "o1", label="Ok\x1b[31mRed" + rlo + "x")
        res = self._patch("o1", enabled=False)  # label untouched
        self.assertNotIn("\x1b", res["label"])
        self.assertNotIn(rlo, res["label"])
        self.assertIn("Ok", res["label"])


class TestOriginsListing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-origins-")
        self.dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            "os.environ", {"REMOTE_ORIGINS_DIR": self._tmp.name}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_listing_exposes_tool_flags_never_keys(self):
        _write_origin(self.dir, "o1", allow_bash=True, label="Papa")
        out = rtl.remote_trigger_origins(_FakeRec())
        row = out["origins"][0]
        self.assertTrue(row["allow_bash"])
        self.assertFalse(row["allow_network"])
        self.assertEqual(row["label"], "Papa")
        self.assertNotIn("hmac_key", row)
        self.assertNotIn("recv_key", row)

    def test_listing_sanitizes_legacy_stored_label(self):
        # A4 (2026-07-20, defense-in-depth): labels stored BEFORE the
        # ingestion sanitizer existed must be sanitized read-side too.
        rlo = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
        _write_origin(self.dir, "o1", label="Ok\x1b[31mRed" + rlo + "x")
        out = rtl.remote_trigger_origins(_FakeRec())
        label = out["origins"][0]["label"]
        self.assertNotIn("\x1b", label)
        self.assertNotIn(rlo, label)
        self.assertIn("Ok", label)


def _write_endpoint(dirpath: Path, endpoint_id: str, **extra) -> Path:
    cfg = {
        "endpoint_id": endpoint_id,
        "url": "https://peer.example.invalid/v1/a2a/receive",
        "hmac_key": "a" * 64,
        "recv_key": "b" * 64,
        "enabled": True,
    }
    cfg.update(extra)
    path = dirpath / f"{endpoint_id}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    path.chmod(0o600)
    return path


class TestPatchEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-endpoints-")
        self.dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            "os.environ", {"REMOTE_ENDPOINTS_DIR": self._tmp.name}
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

    def _patch(self, endpoint_id: str, **body):
        return ap.patch_endpoint(
            endpoint_id, ap.EndpointPatchRequest(**body), _FakeRec()
        )

    # A6 — URL schema validation (deliberately NO L35 egress gate).
    def test_url_bad_scheme_rejected(self):
        _write_endpoint(self.dir, "e1")
        for bad in ("javascript:alert(1)", "ftp://host/x", "file:///etc/passwd",
                    "not-a-url", "//host/path"):
            with self.assertRaises(HTTPException) as ctx:
                self._patch("e1", url=bad)
            self.assertEqual(ctx.exception.status_code, 400, bad)

    def test_url_without_host_rejected(self):
        _write_endpoint(self.dir, "e1")
        for bad in ("http://", "https:///path-only", "http://:8080/x"):
            with self.assertRaises(HTTPException) as ctx:
                self._patch("e1", url=bad)
            self.assertEqual(ctx.exception.status_code, 400, bad)

    def test_url_embedded_credentials_rejected(self):
        _write_endpoint(self.dir, "e1")
        for bad in ("https://user:pw@host.example/x", "http://user@host.example/x"):
            with self.assertRaises(HTTPException) as ctx:
                self._patch("e1", url=bad)
            self.assertEqual(ctx.exception.status_code, 400, bad)

    def test_url_valid_https_accepted(self):
        _write_endpoint(self.dir, "e1")
        res = self._patch("e1", url="https://peer.example.com:7433/v1/a2a/receive")
        self.assertEqual(res["url"], "https://peer.example.com:7433/v1/a2a/receive")

    # A8 — ':' must be blocked ('C:foo' is drive-relative on Windows).
    def test_colon_in_endpoint_id_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            self._patch("C:evil", enabled=False)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_endpoint_listing_sanitizes_legacy_stored_label(self):
        # A4 companion — endpoint listing is a label delivery point too.
        rlo = chr(0x202E)
        _write_endpoint(self.dir, "e1", label="Ok\x1b[31mRed" + rlo + "x")
        out = rtl.remote_trigger_endpoints(_FakeRec())
        label = out["endpoints"][0]["label"]
        self.assertNotIn("\x1b", label)
        self.assertNotIn(rlo, label)
        self.assertIn("Ok", label)

    def test_patch_response_sanitizes_untouched_stored_label(self):
        # A4-RESIDUAL — the endpoint PATCH response echoes the raw on-disk
        # label when the body omits it; it must be sanitized too.
        rlo = chr(0x202E)
        _write_endpoint(self.dir, "e1", label="Ok\x1b[31mRed" + rlo + "x")
        res = self._patch("e1", enabled=False)  # label untouched
        self.assertNotIn("\x1b", res["label"])
        self.assertNotIn(rlo, res["label"])
        self.assertIn("Ok", res["label"])

    # A2 — console PATCH must take the cross-process file lock.
    def test_patch_endpoint_takes_cross_process_file_lock(self):
        _write_endpoint(self.dir, "e1")
        calls: list[tuple] = []

        @contextlib.contextmanager
        def fake_lock(*dirs):
            calls.append(tuple(str(d) for d in dirs))
            yield

        with mock.patch.object(ap._ft, "config_file_lock", fake_lock):
            self._patch("e1", enabled=False)
        self.assertTrue(
            calls, "patch_endpoint did not take a2a_friendship.config_file_lock"
        )


class TestPatchOriginColonAndLock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-origins-")
        self.dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            "os.environ", {"REMOTE_ORIGINS_DIR": self._tmp.name}
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

    # A8 — ':' must be blocked ('C:foo' is drive-relative on Windows).
    def test_colon_in_origin_id_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            ap.patch_origin(
                "C:evil", ap.OriginPatchRequest(enabled=False), _FakeRec()
            )
        self.assertEqual(ctx.exception.status_code, 400)

    # A2 — console PATCH must take the cross-process file lock.
    def test_patch_origin_takes_cross_process_file_lock(self):
        _write_origin(self.dir, "o1")
        calls: list[tuple] = []

        @contextlib.contextmanager
        def fake_lock(*dirs):
            calls.append(tuple(str(d) for d in dirs))
            yield

        with mock.patch.object(ap._ft, "config_file_lock", fake_lock):
            ap.patch_origin(
                "o1", ap.OriginPatchRequest(enabled=False), _FakeRec()
            )
        self.assertTrue(
            calls, "patch_origin did not take a2a_friendship.config_file_lock"
        )


class TestFriendshipSetUrlLocking(unittest.TestCase):
    """A1 (2026-07-20): friendship_set_url does a read-modify-write on the
    origin+endpoint files (via activate_connection) and must hold _pair_lock
    so a concurrent PATCH (e.g. enabled=false) is not silently overwritten."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-seturl-")
        self.dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            "os.environ",
            {
                "REMOTE_ORIGINS_DIR": str(self.dir / "origins"),
                "REMOTE_ENDPOINTS_DIR": str(self.dir / "endpoints"),
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

    def test_set_url_holds_pair_lock_across_rmw(self):
        held: list[bool] = []

        def fake_activate(kid, url, *, origins_dir, endpoints_dir):
            held.append(ap._pair_lock.locked())

        with mock.patch.object(ap._ft, "activate_connection", fake_activate):
            ap.friendship_set_url(
                ap.FriendshipSetUrlRequest(
                    kid="k1", peer_url="https://peer.example.com"
                ),
                _FakeRec(),
            )
        self.assertEqual(held, [True])


class TestFriendshipConnectionsSanitize(unittest.TestCase):
    """A4-RESIDUAL (2026-07-20): GET /pair/friendship/connections delivered the
    raw on-disk label to the React UI. A friendship record written with a bidi
    override (U+202E) / ANSI escape must be sanitized read-side there too."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-conn-")
        self.dir = Path(self._tmp.name)
        self.origins = self.dir / "origins"
        self.endpoints = self.dir / "endpoints"
        self.origins.mkdir()
        self.endpoints.mkdir()
        self._env = mock.patch.dict(
            "os.environ",
            {
                "REMOTE_ORIGINS_DIR": str(self.origins),
                "REMOTE_ENDPOINTS_DIR": str(self.endpoints),
            },
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _write_friend(self, dirpath: Path, kid: str, label: str) -> None:
        cfg = {
            "origin_id": kid, "endpoint_id": kid,
            "hmac_key": "a" * 64, "recv_key": "b" * 64,
            "enabled": True, "state": "ACTIVE",
            "_friendship": True, "allowed_personas": ["assistant"],
            "label": label,
        }
        p = dirpath / f"{kid}.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        p.chmod(0o600)

    def test_connections_sanitizes_origin_and_endpoint_labels(self):
        rlo = chr(0x202E)
        # kid1 present only as an origin; kid2 present only as an endpoint —
        # both label-delivery branches of the route are exercised.
        self._write_friend(self.origins, "kid1", "Ok\x1b[31mRed" + rlo + "x")
        self._write_friend(self.endpoints, "kid2", "Nom" + rlo + "evil")
        out = ap.friendship_connections(_FakeRec())
        by_kid = {c["kid"]: c for c in out["connections"]}
        for kid in ("kid1", "kid2"):
            label = by_kid[kid]["label"]
            self.assertIsNotNone(label)
            self.assertNotIn("\x1b", label)
            self.assertNotIn(rlo, label)


class TestDeleteRouteIdHardening(unittest.TestCase):
    """A8 follow-up: the DELETE routes and friendship_revoke had the same
    Windows drive-relative ':' escape the PATCH routes were hardened against."""

    def test_delete_origin_rejects_colon(self):
        with self.assertRaises(HTTPException) as ctx:
            ap.delete_origin("C:evil", _FakeRec())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_delete_endpoint_rejects_colon(self):
        with self.assertRaises(HTTPException) as ctx:
            ap.delete_endpoint("C:evil", _FakeRec())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_friendship_revoke_rejects_colon(self):
        with self.assertRaises(HTTPException) as ctx:
            ap.friendship_revoke("C:evil", _FakeRec())
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
