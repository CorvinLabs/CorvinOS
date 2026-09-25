"""E2E: the skill catalogue is the Marketplace's "Skills" tab (ADR-0682 in ADR-0892).

Replaces a string-grep suite that asserted `web-next/src/pages/marketplace.tsx`
EXISTS — the very file that shadowed `pages/marketplace/index.tsx` and hid
the plugin marketplace (Browse / Installed / Packages / MCP tools) behind a
skill-only page. It also pinned a `POST /{skill_id}/install` that answered
"queued" and installed nothing, and a double prefix that put every route at
/v1/console/marketplace/marketplace/* while the page fetched
/v1/console/marketplace/* (404 on every call).

These tests go through the real console router at its real mount point, with
a registry written by the real SkillInstaller.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console import deps as console_deps
from core.console.corvin_console.app import router as console_router
from core.console.corvin_console.routes import marketplace_routes
from core.skills.skill_installer import SkillInstaller

WEB = Path(__file__).resolve().parents[2] / "core/console/corvin_console/web-next/src/pages"


def _app(authed: bool) -> TestClient:
    app = FastAPI()
    app.include_router(console_router, prefix="/v1/console")
    if authed:
        app.dependency_overrides[console_deps.require_session] = lambda: SimpleNamespace(tenant_id="_default")
    return TestClient(app)


def _install(root: Path, skill_id: str, version: str, deps: list[dict] | None = None) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SKILL.md", f"# {skill_id}\n")
    zp = root.parent / f"{skill_id}-{version}.zip"
    zp.write_bytes(buf.getvalue())
    ok, msg = SkillInstaller(install_root=root).install_skill(
        zp, hashlib.sha256(zp.read_bytes()).hexdigest(),
        {"skill_id": skill_id, "version": version, "dependencies": deps or []},
    )
    assert ok, msg


@pytest.fixture
def registry(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "skills_installed"
    monkeypatch.setattr(marketplace_routes, "_REGISTRY_PATH", root / "skills_registry.json")
    return root


def test_requires_a_session():
    assert _app(authed=False).get("/v1/console/marketplace/skills/index").status_code == 401


def test_empty_registry_is_an_empty_catalogue(registry):
    r = _app(authed=True).get("/v1/console/marketplace/skills/search")
    assert r.status_code == 200
    assert r.json()["total"] == 0 and r.json()["skills"] == []


def test_installer_written_skills_are_listed_live(registry):
    client = _app(authed=True)
    _install(registry, "os.alpha", "1.0.0")
    assert [s["skill_id"] for s in client.get("/v1/console/marketplace/skills/search").json()["skills"]] == ["os.alpha"]

    # Installed after the first read — a process-lifetime index missed this.
    _install(registry, "os.beta", "2.0.0", deps=[{"skill_id": "os.alpha", "version": "1.0.0"}])
    body = client.get("/v1/console/marketplace/skills/search?sort_by=alphabetical").json()
    assert body["total"] == 2
    assert {s["skill_id"] for s in body["skills"]} == {"os.alpha", "os.beta"}

    d = client.get("/v1/console/marketplace/skills/os.beta")
    assert d.status_code == 200
    assert d.json()["version"] == "2.0.0"
    assert d.json()["dependencies"] == ["os.alpha"]


def test_unknown_skill_and_bad_filter(registry):
    client = _app(authed=True)
    assert client.get("/v1/console/marketplace/skills/nope").status_code == 404
    assert client.get("/v1/console/marketplace/skills/search?domain=bogus").status_code == 400


def test_no_fake_install_route(registry):
    _install(registry, "os.alpha", "1.0.0")
    r = _app(authed=True).post("/v1/console/marketplace/skills/os.alpha/install")
    assert r.status_code in (404, 405)


def test_old_double_prefix_is_gone(registry):
    assert _app(authed=True).get("/v1/console/marketplace/marketplace/index").status_code == 404


def test_frontend_skills_tab_lives_inside_the_one_marketplace():
    # Positive control first: the directory page is where the tabs live.
    assert (WEB / "marketplace" / "index.tsx").is_file()
    assert "SkillsTab" in (WEB / "marketplace" / "index.tsx").read_text()
    assert '"skills"' in (WEB / "marketplace" / "tabs.ts").read_text()
    assert "/marketplace/skills/search" in (WEB / "marketplace" / "api.ts").read_text()
    # No sibling file may shadow the directory again.
    assert not (WEB / "marketplace.tsx").exists()
