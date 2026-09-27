"""Regression: core.plugins.staging imported ``forge.tenant`` (no such module;
it is ``forge.tenants``), so StagingManager() raised ModuleNotFoundError and
every /v1/console/plugin-uploads route answered 500 (adversarial review
2026-09-27). Drives the real route through HTTP with only the session
dependencies overridden.
"""
from __future__ import annotations

import io
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO, _REPO / "corvin_operator" / "forge", _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


@pytest.fixture()
def corvin_home(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    monkeypatch.setenv("CORVIN_HOME", str(home))
    return home


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"name": "p", "version": "1.0.0", "author": "t"}))
        zf.writestr("src/plugin.py", "x = 1\n")
    return buf.getvalue()


def test_staging_manager_constructs(corvin_home):
    from core.plugins.staging import StagingManager

    m = StagingManager("_default")
    assert str(m.staging_root).startswith(str(corvin_home))


def test_plugin_upload_route_stages_and_approves(corvin_home):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    try:
        from corvin_console import deps
        from corvin_console.routes import plugin_upload
    except Exception as exc:  # pragma: no cover - console not importable here
        pytest.skip(f"console not importable: {type(exc).__name__}")

    rec = types.SimpleNamespace(tenant_id="_default", tier="owner", sid_fingerprint="fp00")
    app = fastapi.FastAPI()
    app.include_router(plugin_upload.router, prefix="/v1/console")
    for dep in (deps.require_session, deps.require_csrf, deps.require_session_csrf_on_mutation):
        app.dependency_overrides[dep] = lambda: rec
    c = TestClient(app)

    r = c.post("/v1/console/plugin-uploads",
               files={"file": ("p.zip", _zip_bytes(), "application/zip")})
    assert r.status_code == 200, r.text
    uid = r.json()["upload_id"]
    assert c.get("/v1/console/plugin-uploads").json()["count"] == 1
    r = c.post(f"/v1/console/plugin-uploads/{uid}/approve")
    assert r.status_code == 200, r.text
    assert (corvin_home / "tenants" / "_default" / "global" / "plugins_installed" / f"{uid}.zip").exists()
