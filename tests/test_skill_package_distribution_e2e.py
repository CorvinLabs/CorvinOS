"""
E2E tests for Skill Packaging & Distribution (ADR-0674).

Package → list → download → install → list-installed, driven through the real
console routes (``routes/skill_forge_distribution_routes.py``) with a real
console session and CSRF token, against a scratch ``CORVIN_HOME``.

Rewritten 2026-09-27 (adversarial review): the previous file tested an
installer API that no longer exists (``await installer.install_skill(path)``,
``DependencyResolutionError``, ``SkillDomain.TESTING``) and HTTP tests that
awaited a synchronous client — it could not even be collected, while the route
it claimed to cover answered 500 on every install.

License: Apache-2.0
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from core.skills.manifest_v2 import SkillManifestV2
from core.skills.skill_installer import SkillInstaller
from core.skills.skill_packager import SkillPackager

BASE = "/v1/console/v1/skill-forge"

_MANIFEST = {
    "skill_id": "test_skill",
    "version": "1.0.0",
    "name": "Test Skill",
    "description": "A test skill",
    "domain": "routing",
    "entry_point": "src.skill:TestSkill.execute",
    "boot_layer": "installed",
    "input_schema": {},
    "output_schema": {},
}


def _make_skill(root: Path, manifest: dict = _MANIFEST) -> Path:
    skill_dir = root / manifest["skill_id"]
    for d in ["src", "hooks", "tests", "scripts", "docs", "references"]:
        (skill_dir / d).mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.json").write_text(json.dumps(manifest))
    (skill_dir / "README.md").write_text("# Test Skill")
    (skill_dir / "src" / "skill.py").write_text("class TestSkill: pass\n")
    return skill_dir


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "corvin_home"
    for sub in ("auth", "forge", "console/sessions"):
        (h / "tenants" / "_default" / "global" / sub).mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    return h


@pytest.fixture
def client(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console import auth as _auth
    from corvin_console.routes import skill_forge_distribution_routes as dist

    rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
    app = FastAPI()
    app.include_router(dist.router, prefix="/v1/console")
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("corvin_console_sid", rec.sid)
    c.headers.update({"X-CSRF-Token": _auth.derive_csrf_token(rec.csrf_secret, rec.sid)})
    return c


# ── packager (unit) ─────────────────────────────────────────────────────────


class TestSkillPackager:
    def test_package_valid_skill(self, tmp_path):
        skill = _make_skill(tmp_path / "gen")
        zip_path, zip_hash, metadata = SkillPackager(tmp_path / "pkgs").package(
            skill, SkillManifestV2.from_dict(_MANIFEST))
        assert zip_path.exists()
        assert zip_hash == "sha256:" + hashlib.sha256(zip_path.read_bytes()).hexdigest()
        assert (metadata["skill_id"], metadata["version"]) == ("test_skill", "1.0.0")
        with zipfile.ZipFile(zip_path) as zf:
            assert "test_skill/skill.json" in zf.namelist()

    def test_package_already_exists(self, tmp_path):
        skill = _make_skill(tmp_path / "gen")
        packager = SkillPackager(tmp_path / "pkgs")
        packager.package(skill, SkillManifestV2.from_dict(_MANIFEST))
        with pytest.raises(FileExistsError):
            packager.package(skill, SkillManifestV2.from_dict(_MANIFEST))

    def test_package_missing_structure(self, tmp_path):
        skill = tmp_path / "gen" / "test_skill"
        skill.mkdir(parents=True)
        (skill / "skill.json").write_text(json.dumps(_MANIFEST))
        with pytest.raises(ValueError, match="Missing required directory"):
            SkillPackager(tmp_path / "pkgs").package(skill, SkillManifestV2.from_dict(_MANIFEST))


# ── installer (unit, current API) ───────────────────────────────────────────


class TestSkillInstaller:
    def _pkg(self, tmp_path):
        skill = _make_skill(tmp_path / "gen")
        zip_path, zip_hash, _ = SkillPackager(tmp_path / "pkgs").package(
            skill, SkillManifestV2.from_dict(_MANIFEST))
        return zip_path, zip_hash

    def test_install_and_registry(self, tmp_path):
        zip_path, zip_hash = self._pkg(tmp_path)
        inst = SkillInstaller(tmp_path / "installed")
        ok, msg = inst.install_skill(zip_path, zip_hash, {"skill_id": "test_skill", "version": "1.0.0"})
        assert ok, msg
        assert (tmp_path / "installed" / "test_skill" / "1.0.0" / "test_skill" / "skill.json").exists()
        assert inst._load_registry()["test_skill"][0]["version"] == "1.0.0"
        ok2, msg2 = inst.install_skill(zip_path, zip_hash, {"skill_id": "test_skill", "version": "1.0.0"})
        assert not ok2 and "already installed" in msg2

    def test_checksum_mismatch_refused(self, tmp_path):
        zip_path, _ = self._pkg(tmp_path)
        inst = SkillInstaller(tmp_path / "installed")
        ok, msg = inst.install_skill(zip_path, "sha256:" + "0" * 64, {"skill_id": "test_skill", "version": "1.0.0"})
        assert not ok and "Checksum" in msg
        assert inst._load_registry() == {}

    def test_path_traversal_entry_refused(self, tmp_path):
        bad = tmp_path / "evil.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("../escape.txt", "x")
        digest = hashlib.sha256(bad.read_bytes()).hexdigest()
        ok, _ = SkillInstaller(tmp_path / "installed").install_skill(
            bad, digest, {"skill_id": "evil", "version": "1.0.0"})
        assert not ok
        assert not (tmp_path / "escape.txt").exists()

    def test_unsafe_ids_refused(self, tmp_path):
        zip_path, zip_hash = self._pkg(tmp_path)
        ok, msg = SkillInstaller(tmp_path / "installed").install_skill(
            zip_path, zip_hash, {"skill_id": "../x", "version": "1.0.0"})
        assert not ok and "Invalid" in msg


# ── HTTP: the real routes, real session, scratch CORVIN_HOME ────────────────


class TestHTTPEndpoints:
    def test_requires_session(self, client):
        client.cookies.clear()
        assert client.get(f"{BASE}/installed").status_code == 401
        assert client.post(f"{BASE}/install?zip_path=x.zip&zip_hash=x").status_code == 401

    def test_package_download_install_list_roundtrip(self, client, home):
        _make_skill(home / "skills_gen")

        r = client.post(f"{BASE}/package?skill_id=test_skill")
        assert r.status_code == 200, r.text
        body = r.json()
        zip_hash = body["zip_hash"]
        assert "zip_path" not in body["metadata"]  # no absolute host path to the client
        assert (home / "skills_packages" / "test_skill_1.0.0.zip").is_file()

        r = client.get(f"{BASE}/packages")
        assert [p["filename"] for p in r.json()["packages"]] == ["test_skill_1.0.0.zip"]

        r = client.get(f"{BASE}/download/test_skill_1.0.0.zip")
        assert r.status_code == 200
        assert r.headers["X-Skill-Hash"] == zip_hash

        r = client.post(f"{BASE}/install", params={"zip_path": "test_skill_1.0.0.zip", "zip_hash": zip_hash})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "installed"
        # Installed into the SAME store /skills-manager manages, under CORVIN_HOME.
        assert (home / "skills_installed" / "test_skill" / "1.0.0").is_dir()

        r = client.get(f"{BASE}/installed")
        assert r.status_code == 200, r.text
        assert r.json()["skills"] == [{
            "skill_id": "test_skill", "version": "1.0.0", "boot_layer": "installed",
            "verified": True, "status": "healthy",
        }]

        chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        actions = [json.loads(l).get("details", {}).get("action") for l in chain.read_text().splitlines()]
        assert {"skill_forge.package", "skill_forge.download", "skill_forge.install"} <= set(actions)

    def test_install_refuses_wrong_hash(self, client, home):
        _make_skill(home / "skills_gen")
        assert client.post(f"{BASE}/package?skill_id=test_skill").status_code == 200
        r = client.post(f"{BASE}/install", params={"zip_path": "test_skill_1.0.0.zip", "zip_hash": "0" * 64})
        assert r.status_code == 400
        assert not (home / "skills_installed" / "test_skill").exists()

    def test_install_requires_source_and_hash(self, client):
        assert client.post(f"{BASE}/install").status_code == 400
        assert client.post(f"{BASE}/install?zip_path=a.zip").status_code == 400

    def test_install_from_url_refused(self, client):
        r = client.post(f"{BASE}/install", params={"url": "http://169.254.169.254/x.zip", "zip_hash": "x"})
        assert r.status_code == 400

    def test_install_outside_packages_root_refused(self, client, tmp_path):
        outside = tmp_path / "elsewhere.zip"
        outside.write_bytes(b"PK")
        r = client.post(f"{BASE}/install", params={"zip_path": str(outside), "zip_hash": "x"})
        assert r.status_code == 400

    def test_package_outside_skills_gen_refused(self, client, tmp_path):
        other = _make_skill(tmp_path / "anywhere")
        r = client.post(f"{BASE}/package", params={"skill_id": "test_skill", "skill_path": str(other)})
        assert r.status_code == 400
        assert not (other / ".forge").exists()  # nothing written into the foreign folder

    def test_download_404_and_traversal(self, client):
        assert client.get(f"{BASE}/download/nonexistent.zip").status_code == 404
        assert client.get(f"{BASE}/download/..%5C..%5Cetc%5Cpasswd").status_code in (400, 404)

    def test_list_installed_empty(self, client):
        r = client.get(f"{BASE}/installed")
        assert r.status_code == 200
        assert r.json() == {"skills": []}
