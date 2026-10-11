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
        rec = inst._load_registry()["test_skill"][0]
        assert rec["version"] == "1.0.0"
        # No signature exists on any package and the checksum comes from the caller, so
        # the record must not claim authenticity; it records what WAS checked.
        assert rec["verified"] is False
        assert rec["verification"] == {"checksum": "caller_supplied",
                                       "manifest": "consistent", "signature": "absent"}
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

    @staticmethod
    def _zip_with(tmp_path, files):
        z = tmp_path / "p.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for name, body in files.items():
                zf.writestr(name, body)
        return z, hashlib.sha256(z.read_bytes()).hexdigest()

    def test_manifest_naming_another_skill_is_refused(self, tmp_path):
        z, h = self._zip_with(tmp_path, {"other/skill.json": json.dumps(
            {"skill_id": "other", "version": "9.9.9"})})
        inst = SkillInstaller(tmp_path / "installed")
        ok, msg = inst.install_skill(z, h, {"skill_id": "test_skill", "version": "1.0.0"})
        assert not ok and "Manifest check failed" in msg and "'other'" in msg
        assert inst._load_registry() == {}
        assert not (tmp_path / "installed" / "test_skill").exists()

    @pytest.mark.parametrize("body", ["not json", "[1, 2]"])
    def test_malformed_manifest_is_refused(self, tmp_path, body):
        z, h = self._zip_with(tmp_path, {"skill.json": body})
        ok, msg = SkillInstaller(tmp_path / "installed").install_skill(
            z, h, {"skill_id": "test_skill", "version": "1.0.0"})
        assert not ok and "Manifest check failed" in msg

    def test_manifest_that_names_nothing_is_recorded_as_incomplete_not_consistent(self, tmp_path):
        z, h = self._zip_with(tmp_path, {"skill.json": "{}"})
        inst = SkillInstaller(tmp_path / "installed")
        ok, msg = inst.install_skill(z, h, {"skill_id": "test_skill", "version": "1.0.0"})
        assert ok, msg
        assert inst._load_registry()["test_skill"][0]["verification"]["manifest"] == "incomplete"

    def test_package_without_a_manifest_installs_but_is_recorded_as_such(self, tmp_path):
        z, h = self._zip_with(tmp_path, {"README.md": "x"})
        inst = SkillInstaller(tmp_path / "installed")
        ok, msg = inst.install_skill(z, h, {"skill_id": "test_skill", "version": "1.0.0"})
        assert ok, msg
        rec = inst._load_registry()["test_skill"][0]
        assert rec["verified"] is False and rec["verification"]["manifest"] == "absent"

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
            "verified": False, "status": "healthy",
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


# ── signed packages (ADR-2252): real keys, real packager, real installer ────


def _generate_operator_key(home: Path) -> None:
    from core.skills.signature.key_manager import OperatorKeyManager
    OperatorKeyManager(home).generate_keypair()


def _rewrite_zip(src: Path, dst: Path, edit) -> tuple:
    """Copy ``src`` to ``dst`` entry by entry; ``edit(name, data) -> data|None`` may change or drop."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = edit(info.filename, zin.read(info))
            if data is not None:
                zout.writestr(info.filename, data)
        extra = edit(None, None)
        for name, data in (extra or {}).items():
            zout.writestr(name, data)
    return dst, hashlib.sha256(dst.read_bytes()).hexdigest()


class TestSignedPackages:
    def _signed(self, tmp_path, home):
        _generate_operator_key(home)
        skill = _make_skill(tmp_path / "gen")
        zip_path, zip_hash, meta = SkillPackager(tmp_path / "pkgs").package(
            skill, SkillManifestV2.from_dict(_MANIFEST))
        assert meta["signed"] is True
        return zip_path, zip_hash

    def _install(self, tmp_path, zip_path, digest):
        inst = SkillInstaller(tmp_path / "installed")
        ok, msg = inst.install_skill(zip_path, digest, {"skill_id": "test_skill", "version": "1.0.0"})
        return inst, ok, msg

    def test_a_package_signed_by_this_installs_key_is_verified(self, tmp_path, home):
        zip_path, zip_hash = self._signed(tmp_path, home)
        inst, ok, msg = self._install(tmp_path, zip_path, zip_hash)
        assert ok, msg
        rec = inst._load_registry()["test_skill"][0]
        assert rec["verified"] is True
        assert rec["verification"] == {"checksum": "caller_supplied", "manifest": "consistent",
                                       "signature": "valid"}

    def test_a_package_built_without_a_key_is_unsigned_and_unverified(self, tmp_path, home):
        skill = _make_skill(tmp_path / "gen")
        zip_path, zip_hash, meta = SkillPackager(tmp_path / "pkgs").package(
            skill, SkillManifestV2.from_dict(_MANIFEST))
        assert meta["signed"] is False
        assert not (home / "tenants" / "_default" / "global" / "operator_keys").exists(), (
            "asking whether packages can be signed must not create a key store")
        inst, ok, msg = self._install(tmp_path, zip_path, zip_hash)
        assert ok, msg
        rec = inst._load_registry()["test_skill"][0]
        assert rec["verified"] is False and rec["verification"]["signature"] == "absent"

    def test_a_file_changed_after_signing_is_refused(self, tmp_path, home):
        zip_path, _ = self._signed(tmp_path, home)
        bad, digest = _rewrite_zip(zip_path, tmp_path / "bad.zip", lambda n, d: (
            b"import os\nos.system('x')\n" if n == "test_skill/src/skill.py" else d))
        inst, ok, msg = self._install(tmp_path, bad, digest)
        assert not ok and "file changed after signing: src/skill.py" in msg, msg
        assert inst._load_registry() == {}

    def test_a_file_added_after_signing_is_refused(self, tmp_path, home):
        zip_path, _ = self._signed(tmp_path, home)
        bad, digest = _rewrite_zip(zip_path, tmp_path / "bad.zip", lambda n, d: (
            {"test_skill/src/extra.py": b"print('payload')"} if n is None else d))
        _, ok, msg = self._install(tmp_path, bad, digest)
        assert not ok and "not covered by the signature: src/extra.py" in msg, msg

    def test_a_listed_file_removed_after_signing_is_refused(self, tmp_path, home):
        zip_path, _ = self._signed(tmp_path, home)
        bad, digest = _rewrite_zip(zip_path, tmp_path / "bad.zip", lambda n, d: (
            None if n == "test_skill/README.md" else d))
        _, ok, msg = self._install(tmp_path, bad, digest)
        assert not ok and "listed file missing: README.md" in msg, msg

    def test_a_tampered_manifest_is_refused(self, tmp_path, home):
        zip_path, _ = self._signed(tmp_path, home)
        def edit(n, d):
            if n == "test_skill/skill.json":
                return json.dumps({**_MANIFEST, "description": "changed"}).encode()
            return d
        bad, digest = _rewrite_zip(zip_path, tmp_path / "bad.zip", edit)
        _, ok, msg = self._install(tmp_path, bad, digest)
        assert not ok and "does not verify" in msg, msg

    def test_a_signature_by_an_untrusted_key_is_refused(self, tmp_path, home):
        zip_path, _ = self._signed(tmp_path, home)
        # Same package, signed by a key this install never trusted.
        from cryptography.hazmat.primitives.asymmetric import rsa
        from core.skills import package_signing as ps
        stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with zipfile.ZipFile(zip_path) as zf:
            manifest = zf.read("test_skill/skill.json")
            checksums = zf.read("test_skill/.forge/checksum.sha256")
        forged = ps.sign(manifest, checksums, stranger)
        bad, digest = _rewrite_zip(zip_path, tmp_path / "bad.zip", lambda n, d: (
            forged.encode() if n == "test_skill/skill.sig" else d))
        _, ok, msg = self._install(tmp_path, bad, digest)
        assert not ok and "does not verify against any trusted operator key" in msg, msg

    def test_a_signed_package_on_an_install_without_a_key_is_unverifiable_not_valid(self, tmp_path, home):
        zip_path, zip_hash = self._signed(tmp_path, home)
        import shutil
        shutil.rmtree(home / "tenants" / "_default" / "global" / "operator_keys")
        inst, ok, msg = self._install(tmp_path, zip_path, zip_hash)
        assert ok, msg
        rec = inst._load_registry()["test_skill"][0]
        assert rec["verified"] is False and rec["verification"]["signature"] == "unverifiable", (
            "a key shipped with the package would prove nothing; none is ever read from the ZIP")

    def test_http_roundtrip_a_signed_package_lists_as_verified(self, client, home):
        _generate_operator_key(home)
        _make_skill(home / "skills_gen")
        r = client.post(f"{BASE}/package?skill_id=test_skill")
        assert r.status_code == 200, r.text
        zip_hash = r.json()["zip_hash"].split(":")[-1]
        r = client.post(f"{BASE}/install", params={"zip_path": "test_skill_1.0.0.zip", "zip_hash": zip_hash})
        assert r.status_code == 200, r.text
        listed = client.get(f"{BASE}/installed").json()["skills"]
        assert [(s["skill_id"], s["verified"]) for s in listed] == [("test_skill", True)]


class TestSignedPackagesReview2026_10_12:
    """Adversarial review of ADR-2252 (2026-10-12): each test failed against the first version."""

    _signed = TestSignedPackages._signed
    _install = TestSignedPackages._install

    @pytest.mark.parametrize("smuggled", [
        "TEST_SKILL/src/skill.py",            # case-folds onto the signed file (Windows/macOS)
        "test_skill\\src/skill.py",           # a backslash is a separator on Windows
        "XXXXXXXXXXREADME.md",                # outside the signed folder, suffix matches a listed path
    ])
    def test_an_entry_that_only_resembles_a_signed_file_is_refused(self, tmp_path, home, smuggled):
        zip_path, _ = self._signed(tmp_path, home)
        bad, digest = _rewrite_zip(zip_path, tmp_path / "bad.zip", lambda n, d: (
            {smuggled: b"import os; os.system('id')"} if n is None else d))
        inst, ok, msg = self._install(tmp_path, bad, digest)
        assert not ok, msg
        assert inst._load_registry() == {}

    def test_an_unreadable_key_store_refuses_instead_of_installing_unverifiable(self, tmp_path, home):
        zip_path, _ = self._signed(tmp_path, home)
        meta = home / "tenants" / "_default" / "global" / "operator_keys" / "metadata.json"
        meta.write_text("{not json")
        bad, digest = _rewrite_zip(zip_path, tmp_path / "bad.zip", lambda n, d: (
            b"tampered" if n == "test_skill/src/skill.py" else d))
        inst, ok, msg = self._install(tmp_path, bad, digest)
        assert not ok and "unreadable" in msg, msg

    def test_one_malformed_rotated_key_does_not_discard_the_current_key(self, tmp_path, home):
        zip_path, zip_hash = self._signed(tmp_path, home)
        meta = home / "tenants" / "_default" / "global" / "operator_keys" / "metadata.json"
        data = json.loads(meta.read_text())
        data["old_keys"] = [{"rotated_at": "2026-10-01T00:00:00", "public_key_pem": "garbage"},
                            {"rotated_at": "not a date"}]
        meta.write_text(json.dumps(data))
        inst, ok, msg = self._install(tmp_path, zip_path, zip_hash)
        assert ok, msg
        assert inst._load_registry()["test_skill"][0]["verification"]["signature"] == "valid"

    def test_symlinks_and_nested_forge_dirs_are_never_shipped(self, tmp_path, home):
        _generate_operator_key(home)
        skill = _make_skill(tmp_path / "gen")
        secret = tmp_path / "secret.txt"
        secret.write_text("OPERATOR-SECRET")
        (skill / "docs" / "link.txt").symlink_to(secret)
        (skill / "src" / ".forge").mkdir()
        (skill / "src" / ".forge" / "helper.py").write_text("x = 1\n")
        zip_path, zip_hash, _ = SkillPackager(tmp_path / "pkgs").package(
            skill, SkillManifestV2.from_dict(_MANIFEST))
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert "test_skill/docs/link.txt" not in names
            assert all(b"OPERATOR-SECRET" not in zf.read(n) for n in names)
            listed = zf.read("test_skill/.forge/checksum.sha256").decode()
        assert "src/.forge/helper.py" in listed            # covered, so it may ship
        inst, ok, msg = self._install(tmp_path, zip_path, zip_hash)
        assert ok, msg                                     # what the packager signs installs

    def test_forge_metadata_is_covered_by_the_signature(self, tmp_path, home):
        zip_path, _ = self._signed(tmp_path, home)
        bad, digest = _rewrite_zip(zip_path, tmp_path / "bad.zip", lambda n, d: (
            b'{"boot_layer": "compliance"}' if n == "test_skill/.forge/generation_context.json" else d))
        _, ok, msg = self._install(tmp_path, bad, digest)
        assert not ok and "generation_context.json" in msg, msg

    def test_checksum_keys_are_posix_paths(self, tmp_path, home):
        skill = _make_skill(tmp_path / "gen")
        sums = SkillPackager(tmp_path / "pkgs")._compute_checksums(skill)
        assert sums and all("\\" not in k for k in sums)
        assert "src/skill.py" in sums
