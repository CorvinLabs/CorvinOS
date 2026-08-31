"""ADR-0154 M3 (SDLP) — console session license-proof E2E.

Properties asserted:
  * create_session stamps a non-empty lic_proof.
  * a session loads while the license state is unchanged.
  * changing the license invalidates outstanding sessions (the OTA deterrent).
  * a pre-M3 session (no lic_proof) still loads (backward-compat / fail-open).
  * free-tier (no license) stays stable across loads — login never bricks.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (
    str(_REPO / "core" / "console"),
    str(_REPO / "operator"),
    str(_REPO / "operator" / "forge"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture()
def console_home(tmp_path, monkeypatch):
    tenant = "_default"
    (tmp_path / "global" / "console" / "sessions").mkdir(parents=True)
    (tmp_path / "tenants" / tenant / "global" / "console" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant)
    # Fresh import so corvin_home() picks up the env.
    for m in [k for k in list(sys.modules) if k.startswith("corvin_console.auth")]:
        del sys.modules[m]
    from corvin_console import auth  # type: ignore
    # Resolve the SAME module object auth uses (sys.modules['license.feature_lattice']).
    # `from license import feature_lattice` would read the package *attribute*, which
    # can diverge from sys.modules after another test surgically reimports license.*
    # (see operator/license/tests/conftest.py) — importlib always returns the
    # sys.modules entry, matching auth's `from license.feature_lattice import …`.
    import importlib

    fl = importlib.import_module("license.feature_lattice")  # type: ignore

    fl.set_feature_root_key(None)  # free baseline
    yield auth, fl, tmp_path
    fl.set_feature_root_key(None)


def test_create_session_stamps_proof(console_home):
    auth, fl, _ = console_home
    rec = auth.create_session(tenant_id="_default")
    assert rec.lic_proof  # non-empty
    assert rec.lic_proof == fl.session_lic_proof(rec.sid)


def test_session_loads_when_license_unchanged(console_home):
    auth, _, _ = console_home
    rec = auth.create_session(tenant_id="_default")
    loaded = auth.load_session(rec.sid)
    assert loaded is not None
    assert loaded.sid == rec.sid


def test_license_change_invalidates_session(console_home):
    auth, fl, _ = console_home
    rec = auth.create_session(tenant_id="_default")  # free root
    assert auth.load_session(rec.sid) is not None
    # Operator applies a paid license → root key changes → proof mismatch.
    fl.set_feature_root_key("CORVIN-paid.token.signature")
    assert auth.load_session(rec.sid) is None


def test_free_tier_stable_across_loads(console_home):
    auth, fl, _ = console_home
    fl.set_feature_root_key(None)
    rec = auth.create_session(tenant_id="_default")
    # Multiple loads with no license change must keep working.
    assert auth.load_session(rec.sid) is not None
    assert auth.load_session(rec.sid) is not None


def test_pre_m3_session_without_proof_still_loads(console_home):
    auth, _, home = console_home
    rec = auth.create_session(tenant_id="_default")
    # Rewrite the session file dropping lic_proof (simulate a pre-M3 record).
    path = auth._session_path(rec.sid)
    data = json.loads(path.read_text())
    data.pop("lic_proof", None)
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)
    loaded = auth.load_session(rec.sid)
    assert loaded is not None  # empty stored proof → skip check (backward-compat)


def test_fail_open_when_lattice_unavailable(console_home, monkeypatch):
    auth, _, _ = console_home
    # Force _compute_lic_proof to return "" (lattice unavailable) at verify time.
    rec = auth.create_session(tenant_id="_default")  # has a real proof
    monkeypatch.setattr(auth, "_compute_lic_proof", lambda sid: "")
    # Recompute returns "" → check skipped → session still loads (no brick).
    assert auth.load_session(rec.sid) is not None
