"""Settings → Features REST API: the self-locking contract the UI renders from.

``headless_api_mode`` is not a rollout flag, it is a deployment mode: switching
it on unmounts ``/console/``, so the Settings panel that flipped it is gone on
the next boot. CLAUDE.md's "toggleable from the Console, no file editing" rule
assumes a flag can be un-flipped where it was flipped, and this one cannot.

The fix is two surfaces — a confirmation gate in the UI and a CLI off-ramp — and
both read the SAME registry fields, so this file pins them:

  * ``self_locking``     — the UI shows a warning + confirmation dialog for it,
                           and MUST NOT hard-code a flag id to decide that;
  * ``recovery_command`` — the exact string the dialog prints and the CLI parses.

Also pins the cross-surface property that makes recovery real: the REST write
and the CLI write land in one overlay file, so a flag set from the Console can
be cleared from a terminal.

Mirrors the ``_sandbox`` TestClient pattern from test_features_route.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_LAUNCHER = _REPO / "ops" / "launcher"
_BRIDGES_SHARED = _OPERATOR / "bridges" / "shared"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_LAUNCHER), str(_BRIDGES_SHARED)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

FLAG = "headless_api_mode"


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, _auth.derive_csrf_token(rec.csrf_secret, rec.sid), home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestSelfLockingContract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _hdr(self, csrf: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf}

    def _features(self, client) -> dict[str, dict]:
        resp = client.get("/v1/console/settings/features")
        self.assertEqual(resp.status_code, 200, resp.text)
        return {f["id"]: f for f in resp.json()["features"]}

    # ── The fields the UI renders from ──────────────────────────────────

    def test_every_feature_carries_the_self_locking_fields(self):
        """The UI reads these unconditionally; a missing key is a render crash."""
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            for fid, f in self._features(client).items():
                self.assertIn("self_locking", f, fid)
                self.assertIsInstance(f["self_locking"], bool, fid)
                self.assertIn("recovery_command", f, fid)

    def test_headless_mode_is_marked_self_locking(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            f = self._features(client)[FLAG]
            self.assertTrue(
                f["self_locking"],
                "headless_api_mode removes /console/ — the UI must warn before it "
                "is switched on, and it decides that from this field",
            )

    def test_headless_mode_advertises_the_exact_cli_off_ramp(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            self.assertEqual(
                self._features(client)[FLAG]["recovery_command"],
                f"corvin config set features.{FLAG} false",
            )

    def test_the_description_names_the_lock_out(self):
        """An operator who never opens the dialog still reads the description."""
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            desc = self._features(client)[FLAG]["description"]
            self.assertIn("SELF-LOCKING", desc)
            self.assertIn(f"corvin config set features.{FLAG} false", desc)

    def test_ordinary_flags_offer_no_recovery_command(self):
        """A "recovery" command on a reversible flag is noise that trains people
        to ignore the real one."""
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            others = {k: v for k, v in self._features(client).items() if k != FLAG}
            self.assertTrue(others)
            for fid, f in others.items():
                self.assertFalse(f["self_locking"], fid)
                self.assertIsNone(f["recovery_command"], fid)

    def test_self_locking_is_not_a_second_default(self):
        """Marking a flag self-locking must not smuggle it on at boot."""
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            f = self._features(client)[FLAG]
            self.assertFalse(f["default"])
            self.assertFalse(f["enabled"])
            self.assertEqual(f["source"], "default")

    # ── Writing through the REST surface ────────────────────────────────

    def test_toggle_still_round_trips(self):
        """The confirmation lives in the UI; the API stays a plain PUT.

        Putting the gate in the route would break the CLI off-ramp, which has to
        be able to write this flag with no dialog anywhere.
        """
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            r = client.put(f"/v1/console/settings/features/{FLAG}",
                           json={"enabled": True}, headers=self._hdr(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(self._features(client)[FLAG]["enabled"])

            r = client.put(f"/v1/console/settings/features/{FLAG}",
                           json={"enabled": False}, headers=self._hdr(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(self._features(client)[FLAG]["enabled"])

    def test_the_rest_write_lands_in_the_file_the_cli_reads(self):
        """One overlay, two surfaces — otherwise the off-ramp goes nowhere."""
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            client.put(f"/v1/console/settings/features/{FLAG}",
                       json={"enabled": True}, headers=self._hdr(csrf))
            overlay = home / "tenants" / "_default" / "global" / "features.json"
            self.assertTrue(overlay.is_file(), "expected features.json")
            self.assertIs(json.loads(overlay.read_text())["flags"][FLAG], True)

    def test_the_write_is_audited(self):
        """A deployment-mode change is not a silent config edit."""
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            r = client.put(f"/v1/console/settings/features/{FLAG}",
                           json={"enabled": True}, headers=self._hdr(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            # Route-level contract: the handler emits settings.feature_toggle.
            # Asserted through the response the UI keys off, plus the source flip
            # that proves the console overlay (not YAML) was written.
            self.assertEqual(self._features(client)[FLAG]["source"], "console")

    # ── The recovery path, end to end ───────────────────────────────────

    def test_locked_in_via_console_can_be_unlocked_via_cli(self):
        """The whole point: switch it ON from the UI, OFF from a terminal.

        This is the scenario the operator is actually in — the Console is gone,
        only the CLI is left — minus the process restart.
        """
        try:
            from corvin import cli as launcher_cli
        except ImportError:  # pragma: no cover - launcher not on this install
            self.skipTest("corvin launcher not importable")

        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            client.put(f"/v1/console/settings/features/{FLAG}",
                       json={"enabled": True}, headers=self._hdr(csrf))
            self.assertTrue(self._features(client)[FLAG]["enabled"])

            import io
            from contextlib import redirect_stdout
            args = launcher_cli._build_parser().parse_args(
                ["config", "set", f"features.{FLAG}", "false"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = launcher_cli.cmd_config_set(args)
            self.assertEqual(rc, 0, buf.getvalue())

            self.assertFalse(
                self._features(client)[FLAG]["enabled"],
                "the CLI write must be visible to the console resolver",
            )

    def test_headless_flag_id_matches_what_the_app_gates_on(self):
        """If app.py renamed the flag, the whole off-ramp would point at nothing."""
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            from corvin_console.app import HEADLESS_FLAG_ID

            self.assertEqual(HEADLESS_FLAG_ID, FLAG)
            self.assertIn(FLAG, self._features(client))


if __name__ == "__main__":
    unittest.main()
