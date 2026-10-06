"""Fixtures for Forge Bundle export E2E tests (ADR-2229 Phase 2).

Mirrors ``tests/layer_forge/conftest.py``'s ``tmp_corvin_home`` pattern, plus
the three other forges' own storage roots: ``skills_gen``/``skills_packages``
(host-global, matching ``skill_forge_distribution_routes.py``), the tool
registry (tenant+scope-rooted via ``MultiRegistry``), and the layer registry
(tenant-rooted via ``layer_forge_home``).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable, Generator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TENANT_ID = "_default"


# ADR-0701 G1: ``Registry.create`` runs the real licence gate, which denies
# the free tier. Mirrors ``corvin_operator/forge/tests/conftest.py`` exactly —
# that fixture does not apply here (different directory, no shared conftest
# ancestor), and ``make_tool`` calls ``MultiRegistry.create`` for real.
@pytest.fixture(autouse=True)
def _forge_licence_tier(monkeypatch):
    try:
        from corvin_operator.license import capability_api
    except ImportError:
        yield
        return
    monkeypatch.setattr(capability_api, "active_tier", lambda **_k: "member")
    yield


@pytest.fixture
def tmp_corvin_home(tmp_path: Path) -> Generator[Path, None, None]:
    home = tmp_path / "corvin_home"
    th = home / "tenants" / TENANT_ID
    for subdir in [
        "global/layer_forge/registry",
        "global/layer_forge/locks",
        "global/forge",
    ]:
        (th / subdir).mkdir(parents=True, exist_ok=True)
    (home / "skills_gen").mkdir(parents=True, exist_ok=True)
    (home / "skills_packages").mkdir(parents=True, exist_ok=True)

    orig_env = {k: os.environ.get(k) for k in (
        "CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "CORVIN_FORCE_SCOPE",
    )}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT_ID
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    os.environ["CORVIN_FORCE_SCOPE"] = "user"
    try:
        yield home
    finally:
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def make_skill(tmp_corvin_home: Path) -> Callable[..., Path]:
    """Write a Phase-1/2-shaped Skill folder under ``skills_gen/``.

    Matches ``SkillPackager._validate_skill_folder``'s required dirs/files, and
    pre-writes ``.forge/generation_context.json`` so ``package()`` takes its
    "use existing" branch — the fallback branch reads ``manifest.domain``,
    a field ``SkillManifestV2`` does not have.
    """
    def _make(skill_id: str = "summarize", version: str = "1.0.0") -> Path:
        folder = tmp_corvin_home / "skills_gen" / skill_id
        for sub in ("src", "hooks", "tests", "scripts", "docs", "references"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        (folder / "src" / "skill.py").write_text("def run(text):\n    return text[:100]\n")
        (folder / "README.md").write_text(f"# {skill_id}\n")
        manifest = {
            "skill_id": skill_id, "version": version, "boot_layer": "installed",
            "entry_point": "src.skill:run",
        }
        (folder / "skill.json").write_text(json.dumps(manifest))
        (folder / ".forge").mkdir(exist_ok=True)
        (folder / ".forge" / "generation_context.json").write_text(json.dumps({
            "generated_at": "2026-01-01T00:00:00Z", "generated_by": "test-fixture",
            "skill_id": skill_id, "version": version,
        }))
        return folder
    return _make


@pytest.fixture
def make_tool(tmp_corvin_home: Path) -> Callable[..., None]:
    """Create a tool via the real ``MultiRegistry.create`` (same path export reads).

    ``impl`` is the tool's SOURCE CODE, not a path — ``Registry.create`` writes
    it verbatim to ``<registry_root>/tools/<name>.py`` and that written path
    becomes ``ToolSpec.impl_path``.
    """
    def _make(name: str = "csv.count", impl_body: str = "def run(req):\n    return 0\n") -> None:
        from forge.multi_registry import MultiRegistry

        MultiRegistry(tenant_id=TENANT_ID).create(
            scope="user", name=name, description="test tool",
            input_schema={"type": "object"}, impl=impl_body,
        )
    return _make


@pytest.fixture
def make_layer(tmp_corvin_home: Path) -> Callable[..., dict]:
    """Create a layer definition directly via ``LayerRegistry.create`` (no gates)."""
    def _make(entry_id: str = "acme.audit-l34", version: str = "1.0.0") -> dict:
        from core.orchestration.layer_forge.orchestrator import layer_forge_home
        from core.orchestration.layer_forge.registry import LayerRegistry

        registry = LayerRegistry(layer_forge_home(TENANT_ID) / "registry")
        manifest = {
            "id": entry_id, "version": version,
            "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
            "quality_gates": [], "enforcement_rules": [],
        }
        registry.create(manifest)
        return manifest
    return _make


@pytest.fixture
def make_plugin_wheel(tmp_path: Path) -> Callable[..., Path]:
    """A minimal file standing in for a Plugin Builder wheel — export only reads it."""
    def _make(plugin_id: str = "acme-audit-sink", version: str = "0.5.0") -> Path:
        wheel = tmp_path / f"{plugin_id}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr(f"{plugin_id}/plugin.json", json.dumps({"id": plugin_id}))
        return wheel
    return _make


@pytest.fixture
def cli_runner(tmp_corvin_home: Path) -> Callable[[list[str]], subprocess.CompletedProcess]:
    def _run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "forge_bundle_cli.py"), *args],
            capture_output=True, text=True, timeout=30,
            env={**os.environ},
        )
    return _run


@pytest.fixture
def make_plugin_package(tmp_path: Path) -> Callable[..., Path]:
    """An ADR-0511 plugin ZIP (manifest.json with name/version/author) — what StagingManager accepts."""
    def _make(plugin_id: str = "acme-audit-sink", version: str = "0.5.0") -> Path:
        pkg = tmp_path / f"{plugin_id}-{version}.zip"
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"name": plugin_id, "version": version, "author": "acme"}))
            zf.writestr("src/plugin.py", "def setup():\n    return None\n")
        return pkg
    return _make


@pytest.fixture(autouse=True)
def _layer_review_passes(monkeypatch):
    """Layer Forge's REVIEW phase calls the Anthropic API — the one external
    boundary these tests replace. Every other gate runs for real."""
    from core.orchestration.layer_forge import orchestrator
    from core.orchestration.layer_forge.review import ReviewVerdict

    monkeypatch.setattr(orchestrator, "review_layer_definition",
                        lambda manifest, enforcement, **_k: ReviewVerdict("PASS", flags=[]))


@pytest.fixture
def chain_events(tmp_corvin_home: Path) -> Callable[[], list[dict]]:
    def _read() -> list[dict]:
        chain = tmp_corvin_home / "tenants" / TENANT_ID / "global" / "forge" / "audit.jsonl"
        if not chain.exists():
            return []
        return [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return _read


@pytest.fixture
def console_client(tmp_corvin_home: Path):
    """The console router mounted EXACTLY as corvin_gateway/app.py mounts it
    (``prefix="/v1/console"``), with a real session cookie + CSRF token."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console import auth
    from corvin_console.app import router

    rec = auth.create_session(tenant_id=TENANT_ID, token_fingerprint="test-fp")
    csrf = auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    return client, csrf
