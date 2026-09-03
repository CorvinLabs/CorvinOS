"""Drift-E2E — feature-flag overlay resolution (context-drift core path).

These tests pin the resolution rules that, when they drift, silently change
which features a live turn runs — the exact class of "the operator flipped a
switch and nothing happened" / "a quoted YAML value turned a feature ON"
context-drift bug.

Every assertion here goes through a REAL boundary:

  * ``is_enabled`` / ``set_enabled`` read+write the REAL ``features.json``
    overlay and the REAL ``tenant.corvin.yaml`` on disk (a throwaway
    ``CORVIN_HOME``, never the live one). The filesystem IS the boundary — no
    resolver internals are monkeypatched.
  * the HTTP block drives the REAL ``routes/features.py`` router through a
    FastAPI ``TestClient`` (real ASGI request → auth dependency → disk write),
    then re-resolves through the public ``is_enabled`` API.

Complements ``test_feature_flags.py`` (registry invariants) and
``test_features_route.py`` — it does NOT duplicate them; it adds the drift
paths those two do not cover: ``_coerce_flag`` string handling through the
real YAML overlay, self-locking recovery, and the whitelist⇄overlay
precedence driven end-to-end from the HTTP surface.

Run: python3 -m pytest core/console/tests/test_drift_feature_flags_e2e.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from corvin_console import feature_flags as ff  # noqa: E402


# ── Public-API drift paths (real overlay + real YAML on disk) ──────────────

@pytest.fixture()
def tenant_home(tmp_path, monkeypatch):
    """A throwaway CORVIN_HOME so is_enabled/set_enabled hit real files, never
    the live ~/.corvin. Mirrors test_feature_flags.py's fixture."""
    home = tmp_path / "corvin"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    ff._spec_cache.clear()
    yield home
    ff._spec_cache.clear()


def _write_yaml(home: Path, body: str) -> None:
    p = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    p.write_text(body, encoding="utf-8")
    ff._spec_cache.clear()


def _pick_self_locking() -> str:
    for f in ff.REGISTRY:
        if f.self_locking:
            return f.id
    raise AssertionError("registry has no self-locking flag to exercise")


def test_quoted_false_string_in_yaml_stays_off(tenant_home):
    """The D2 drift: a hand-edited / quoted-YAML ``flag: "false"`` must resolve
    OFF. ``bool("false")`` is True, so a naive coercion turned the feature ON —
    violating "absent/invalid = off, never on because unset". Driven through the
    real YAML overlay that ``is_enabled`` reads from disk."""
    fid = _pick_self_locking()
    _write_yaml(tenant_home, f'spec:\n  features:\n    {fid}: "false"\n')
    assert ff.is_enabled(fid) is False, (
        'a quoted "false" must never enable a feature (bool("false") is True)')


def test_quoted_true_string_in_yaml_enables(tenant_home):
    """Symmetric to the above: an explicit truthy string DOES enable, so the
    coercion is not simply 'reject every string'."""
    fid = ff.REGISTRY[0].id
    _write_yaml(tenant_home, f'spec:\n  features:\n    {fid}: "true"\n')
    assert ff.is_enabled(fid) is True


def test_junk_string_in_yaml_stays_off(tenant_home):
    """Any non-truthy string (typo, garbage) is off — fail-dark."""
    fid = ff.REGISTRY[0].id
    _write_yaml(tenant_home, f'spec:\n  features:\n    {fid}: maybe\n')
    assert ff.is_enabled(fid) is False


def test_coerce_flag_matrix():
    """Unit-level pin of the coercion table the drift depends on. Kept next to
    the disk-level tests so the contract is visible in one place."""
    assert ff._coerce_flag(True) is True
    assert ff._coerce_flag(False) is False
    for on in ("true", "TRUE", " True ", "1", "yes", "on", "ON"):
        assert ff._coerce_flag(on) is True, on
    for off in ("false", "0", "no", "off", "maybe", "", "2", None, 0, 2, 1.5):
        assert ff._coerce_flag(off) is False, off
    assert ff._coerce_flag(1) is True
    assert ff._coerce_flag(1.0) is True


def test_overlay_overrides_whitelist_both_directions(tenant_home):
    """The load-bearing precedence: an explicit console overlay beats the
    whitelist in BOTH directions — ON for an unlisted flag, OFF for a listed
    one. If this drifts, the Settings panel becomes a no-op. Uses a self-locking
    flag as the unlisted one to also exercise that code path."""
    listed = "browser_automation"
    unlisted = _pick_self_locking()
    _write_yaml(tenant_home, f"spec:\n  features_whitelist:\n    - {listed}\n")

    # Baseline: whitelist decides, overlay empty.
    assert ff.is_enabled(listed) is True
    assert ff.is_enabled(unlisted) is False

    # Overlay turns the LISTED flag OFF and the UNLISTED flag ON.
    ff.set_enabled(listed, False)
    ff.set_enabled(unlisted, True)
    assert ff.is_enabled(listed) is False, "overlay OFF must beat whitelist ON"
    assert ff.is_enabled(unlisted) is True, "overlay ON must beat whitelist absent"
    assert ff._source_of(listed, "_default") == "console"
    assert ff._source_of(unlisted, "_default") == "console"


def test_unregistered_flag_is_fail_dark_not_exception(tenant_home):
    """A read of a flag id nobody registered must degrade to False, never raise
    in the middle of a turn."""
    assert ff.is_enabled("totally_made_up_flag_xyz") is False
    # even with a whitelist that (nonsensically) lists it — phantom ids are inert
    _write_yaml(tenant_home,
                "spec:\n  features_whitelist:\n    - totally_made_up_flag_xyz\n")
    assert ff.is_enabled("totally_made_up_flag_xyz") is False


def test_self_locking_recovery_command_is_stable(tenant_home):
    """A self-locking flag must expose a Console-independent off-ramp string,
    and describe_all must surface it only for self-locking flags."""
    fid = _pick_self_locking()
    assert ff.recovery_command(fid) == f"corvin config set features.{fid} false"
    with pytest.raises(ff.UnknownFlagError):
        ff.recovery_command("not_a_flag")

    states = {f["id"]: f for f in ff.describe_all()}
    assert states[fid]["self_locking"] is True
    assert states[fid]["recovery_command"] == (
        f"corvin config set features.{fid} false")
    # A normal flag must NOT advertise a recovery command.
    normal = next(f.id for f in ff.REGISTRY if not f.self_locking)
    assert states[normal]["self_locking"] is False
    assert states[normal]["recovery_command"] is None


# ── HTTP boundary — real routes/features.py router via TestClient ──────────

def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """Mount the real console app router against a throwaway CORVIN_HOME with a
    live auth session. Mirrors test_features_route.py's _sandbox."""
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
        yield client, _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestWhitelistRouteE2E(unittest.TestCase):
    """The /features/whitelist + /features/toggle HTTP surface (routes/features.py)
    is what the Console UI calls to declare which features are enabled. Drive it
    through the real ASGI boundary and prove the write reaches is_enabled()."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_toggle_adds_to_whitelist_and_enables(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf):
            # Fresh install: empty whitelist, feature off.
            r = client.get("/v1/console/features/whitelist")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["whitelist"], [])

            from corvin_console import feature_flags as _ff
            self.assertFalse(_ff.is_enabled("browser_automation", "_default"))

            # Operator whitelists the feature over HTTP.
            r = client.post("/v1/console/features/toggle", headers={"X-CSRF-Token": _csrf},
                            json={"feature_id": "browser_automation", "enabled": True})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("browser_automation", r.json()["whitelist"])

            # The real resolver now reports it ON (write reached disk → is_enabled).
            _ff._spec_cache.clear()
            self.assertTrue(_ff.is_enabled("browser_automation", "_default"))

            # Reading the whitelist back over HTTP confirms persistence.
            r = client.get("/v1/console/features/whitelist")
            self.assertIn("browser_automation", r.json()["whitelist"])

    def test_toggle_off_removes_from_whitelist(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf):
            client.post("/v1/console/features/toggle", headers={"X-CSRF-Token": _csrf},
                        json={"feature_id": "browser_automation", "enabled": True})
            r = client.post("/v1/console/features/toggle", headers={"X-CSRF-Token": _csrf},
                            json={"feature_id": "browser_automation", "enabled": False})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("browser_automation", r.json()["whitelist"])
            from corvin_console import feature_flags as _ff
            _ff._spec_cache.clear()
            self.assertFalse(_ff.is_enabled("browser_automation", "_default"))

    def test_toggle_rejects_unregistered_flag(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf):
            r = client.post("/v1/console/features/toggle", headers={"X-CSRF-Token": _csrf},
                            json={"feature_id": "house_rules_off", "enabled": True})
            # feature_flags.flag() raises UnknownFlagError (a KeyError subclass);
            # the route does not catch it, so FastAPI returns a 500 — the point
            # is that an unregistered/compliance-shaped id is NEVER whitelisted.
            self.assertGreaterEqual(r.status_code, 400, r.text)
            r2 = client.get("/v1/console/features/whitelist")
            self.assertNotIn("house_rules_off", r2.json()["whitelist"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
