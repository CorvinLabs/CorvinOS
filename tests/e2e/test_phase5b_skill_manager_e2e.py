"""Phase 5b: Skill Manager E2E Tests (ADR-0681).

Drives the REAL console router (``/v1/console/skills-manager/*``) over HTTP with
a real console session + CSRF token against a scratch CORVIN_HOME: list, health,
install (real ZIP), duplicate refusal, uninstall, and the auth doors.

Rewritten 2026-09-27 (adversarial review): the previous file requested an
``async_client`` fixture that exists nowhere (11 setup errors — nothing ran),
and half of its tests grepped the SPA shell HTML for component text the
server never renders. The panel's registration is covered by
``test_phase5_skill_manager_wiring.py``. The Phase 5 stub file
(``test_phase5_skill_manager_e2e.py``, 25 bodies of ``pass`` against the
never-mounted ``/v1/skills/*`` mock router) was deleted: its subject does not
exist.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

P = "/v1/console/skills-manager/skills"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / "_default" / "global" / sub).mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

    from core.console.corvin_console import auth as _auth
    from core.console.corvin_console.app import router

    rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("corvin_console_sid", rec.sid)
    csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    return c, csrf, home


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "# demo\n")
    return buf.getvalue()


def test_list_and_health_on_empty_install(ctx):
    c, _, _ = ctx
    r = c.get(f"{P}/installed")
    assert r.status_code == 200
    assert r.json() == {"skills": [], "total": 0}
    r = c.get(f"{P}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["installed_count"] == 0
    assert "registry_path" not in body  # no host path to the client


def test_requires_session_and_csrf(ctx):
    c, csrf, _ = ctx
    files = {"file": ("demo.zip", _zip_bytes(), "application/zip")}
    data = {"skill_id": "demo_skill", "version": "1.0.0"}
    assert c.post(f"{P}/install", data=data, files=files).status_code == 403  # no CSRF
    c.cookies.clear()
    assert c.get(f"{P}/installed").status_code == 401


def test_install_list_duplicate_uninstall_roundtrip(ctx):
    c, csrf, home = ctx
    h = {"X-CSRF-Token": csrf}
    data = {"skill_id": "demo_skill", "version": "1.0.0"}

    r = c.post(f"{P}/install", data=data, headers=h,
               files={"file": ("demo.zip", _zip_bytes(), "application/zip")})
    assert r.status_code == 200, r.text
    assert (home / "skills_installed" / "demo_skill" / "1.0.0" / "SKILL.md").is_file()

    listed = c.get(f"{P}/installed").json()
    assert listed["total"] == 1 and listed["skills"][0]["skill_id"] == "demo_skill"

    r = c.post(f"{P}/install", data=data, headers=h,
               files={"file": ("demo.zip", _zip_bytes(), "application/zip")})
    assert r.status_code == 400 and "already installed" in r.json()["detail"]

    r = c.delete(f"{P}/uninstall/demo_skill/1.0.0", headers=h)
    assert r.status_code == 200, r.text
    assert c.get(f"{P}/installed").json()["total"] == 0


def test_install_rejects_non_zip_and_traversal_ids(ctx):
    c, csrf, home = ctx
    h = {"X-CSRF-Token": csrf}
    r = c.post(f"{P}/install", data={"skill_id": "x", "version": "1.0.0"}, headers=h,
               files={"file": ("x.txt", b"nope", "text/plain")})
    assert r.status_code == 400
    r = c.post(f"{P}/install", data={"skill_id": "../../evil", "version": "1.0.0"}, headers=h,
               files={"file": ("demo.zip", _zip_bytes(), "application/zip")})
    assert r.status_code == 400
    assert not (home / "evil").exists()


def test_uninstall_unknown_is_400(ctx):
    c, csrf, _ = ctx
    r = c.delete(f"{P}/uninstall/nope/1.0.0", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
