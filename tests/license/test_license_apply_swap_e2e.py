"""Apply Key is validate-then-swap: a pasted key that is rejected AFTER the write never costs the
operator the previously valid key (license.py, ``_swap_in_license_key``).

Red on origin/main (925406772): the route ``os.replace``d the new token over ``license.key`` and,
when the post-write reload left nothing loaded, ``key_path.unlink()``ed the file -- which removed the
PREVIOUS valid key too. Green: the previous bytes are restored (and reloaded); a key bound to a
different installation is refused before anything is written; a reload that raises restores the
previous key; a successful apply keeps the new key at mode 0600.

The route is driven over HTTP (FastAPI TestClient on the real console router, real session cookie,
CSRF and re-auth token). Production-signed licence tokens cannot be minted here, so the crypto
primitives of ``license.validator`` (``_verify_ed25519``, ``_validate_claims``, revocation and
binding checks) and ``reload_from_disk`` are replaced; the route, its ordering, the file swap and the
restore are the real code under test.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import console  # noqa: E402

BASE = "/v1/console/license"
PREVIOUS = "PREVIOUS-VALID-KEY-aaaaaaaaaaaaaaaa\n"
NEW_TOKEN = "NEW-PASTED-KEY-bbbbbbbbbbbbbbbbbbbbbb"
NEW_CLAIMS = {"jti": "newjti0123456789", "exp": 4102444800, "issued_to": None}


@pytest.fixture
def con(tmp_path):
    with console(tmp_path) as c:
        yield c


def _key_path(con) -> Path:
    return con.home / "global" / "license.key"


def _plant_previous(con) -> bytes:
    kp = _key_path(con)
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_text(PREVIOUS, encoding="utf-8")
    kp.chmod(0o600)
    return kp.read_bytes()


def _fp(con) -> str:
    return con.client.get("/v1/console/auth/whoami").json()["fingerprint"]


def _post(con, token=NEW_TOKEN):
    return con.client.post(f"{BASE}/key", json={"key": token, "re_auth_token": _fp(con)}, headers=con.h)


class _Validator:
    """Context manager: crypto primitives accept NEW_TOKEN; reload behaviour is chosen per test."""

    def __init__(self, *, reload, bound=True):
        self.reload, self.bound = reload, bound

    def __enter__(self):
        import license.validator as lv
        import corvin_console.routes.license as route
        self.lv, self.route = lv, route
        self.stack = [
            mock.patch.object(lv, "_verify_ed25519", lambda t: dict(NEW_CLAIMS)),
            mock.patch.object(lv, "_validate_claims", lambda c: dict(c)),
            mock.patch.object(lv, "_is_token_fp_revoked", lambda t: False),
            mock.patch.object(lv, "_check_device_fp", lambda c: True),
            mock.patch.object(lv, "_check_instance_id_bound", lambda c: self.bound),
            mock.patch.object(lv, "reload_from_disk", self.reload),
        ]
        for m in self.stack:
            m.start()
        return self

    def __exit__(self, *exc):
        for m in reversed(self.stack):
            m.stop()


def test_G_license_rejected_after_write_restores_the_previous_key(con):
    previous = _plant_previous(con)
    # reload finds nothing loadable (the exact LIC-5 shape): active licence stays unset
    with _Validator(reload=lambda: None):
        with mock.patch("corvin_console.routes.license._lic_is_loaded", lambda: False):
            r = _post(con)
    assert r.status_code == 400, r.text
    assert _key_path(con).read_bytes() == previous, "the previous valid key was lost"
    assert stat.S_IMODE(_key_path(con).stat().st_mode) == 0o600


def test_G_license_reload_that_raises_restores_the_previous_key(con):
    previous = _plant_previous(con)

    def boom():
        raise RuntimeError("reload failed")

    with _Validator(reload=boom):
        with mock.patch("corvin_console.routes.license._lic_is_loaded", lambda: True):
            r = _post(con)
    assert r.status_code == 400, r.text
    assert _key_path(con).read_bytes() == previous


def test_G_license_a_loaded_old_licence_is_not_mistaken_for_the_new_one(con):
    """Reload fails but the OLD licence is still 'loaded': the active jti differs from the pasted one."""
    import license.validator as lv

    previous = _plant_previous(con)
    with _Validator(reload=lambda: None):
        with mock.patch("corvin_console.routes.license._lic_is_loaded", lambda: True), \
             mock.patch.object(lv, "_ACTIVE_LICENSE", {"jti": "oldjti0000000000"}):
            r = _post(con)
    assert r.status_code == 400, r.text
    assert _key_path(con).read_bytes() == previous


def test_G_license_installation_binding_is_checked_before_anything_is_written(con):
    previous = _plant_previous(con)
    before = os.stat(_key_path(con))
    with _Validator(reload=lambda: None, bound=False):
        r = _post(con)
    assert r.status_code == 400 and "not valid for this installation" in r.text, r.text
    after = os.stat(_key_path(con))
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns), "the file was rewritten"
    assert _key_path(con).read_bytes() == previous


def test_G_license_failed_apply_without_a_previous_key_leaves_no_file(con):
    with _Validator(reload=lambda: None):
        with mock.patch("corvin_console.routes.license._lic_is_loaded", lambda: False):
            r = _post(con)
    assert r.status_code == 400
    assert not _key_path(con).exists()


def test_G_license_successful_apply_keeps_the_new_key_at_0600(con):
    import license.validator as lv

    _plant_previous(con)
    with _Validator(reload=lambda: None):
        with mock.patch("corvin_console.routes.license._lic_is_loaded", lambda: True), \
             mock.patch("corvin_console.routes.license._lic_active_tier", lambda: "member"), \
             mock.patch.object(lv, "_ACTIVE_LICENSE", {"jti": NEW_CLAIMS["jti"]}):
            r = _post(con)
    assert r.status_code == 200, r.text
    assert r.json()["loaded"] is True and r.json()["tier"] == "member"
    assert _key_path(con).read_text().strip() == NEW_TOKEN
    assert stat.S_IMODE(_key_path(con).stat().st_mode) == 0o600
    applied = [e for e in con.audit_events() if e.get("event_type") == "console.action_performed"
               and e.get("details", {}).get("action") == "license.key_apply"]
    assert applied
