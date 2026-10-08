"""
Fixtures for Layer Forge E2E tests (ADR-2222).

Provides:
- tmp_corvin_home: Sandboxed CORVIN_HOME + tenant structure
- verify_chain: Audit chain integrity checker (verifies hash links)
- layer_forge_manifest: Factory for valid/invalid manifests
- cli_runner: Subprocess runner for layer_forge_cli.py
- console_client: FastAPI TestClient w/ CSRF + session auth
- registry_state: Inspect registry files on disk
"""
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TENANT_ID = "_default"


@pytest.fixture
def tmp_corvin_home(tmp_path: Path) -> Generator[Path, None, None]:
    """Sandboxed CORVIN_HOME with tenant structure.

    Creates:
    - CORVIN_HOME/tenants/_default/global/layer_forge/{registry,locks}
    - CORVIN_HOME/tenants/_default/global/forge/ (for audit chain)
    - CORVIN_HOME/tenants/_default/global/console/sessions/ (for console fixtures)

    Restores env vars at cleanup.
    """
    home = tmp_path / "corvin_home"
    th = home / "tenants" / TENANT_ID

    # Create directory structure
    for subdir in [
        "global/layer_forge/registry",
        "global/layer_forge/locks",
        "global/forge",
        "global/console/sessions",
        "global/auth",
    ]:
        (th / subdir).mkdir(parents=True, exist_ok=True)

    # Store original env
    orig_env = {
        "CORVIN_HOME": os.environ.get("CORVIN_HOME"),
        "CORVIN_TENANT_ID": os.environ.get("CORVIN_TENANT_ID"),
        "VOICE_AUDIT_PATH": os.environ.get("VOICE_AUDIT_PATH"),
    }

    # Set new env
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT_ID
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")

    try:
        yield home
    finally:
        # Restore env
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def verify_chain(tmp_corvin_home: Path) -> Callable[[], list[dict]]:
    """Verify audit chain integrity.

    Returns a callable that reads the tenant's audit.jsonl, verifies hash links,
    and returns all events. Called automatically at test teardown.

    Raises AssertionError if chain is broken.
    """
    def _verify() -> list[dict]:
        chain = tmp_corvin_home / "tenants" / TENANT_ID / "global" / "forge" / "audit.jsonl"
        if not chain.exists():
            return []

        lines = chain.read_text().strip().splitlines()
        if not lines:
            return []

        events = []
        for line in lines:
            if line.strip():
                events.append(json.loads(line))

        # Verify prev_hash chain links
        if len(events) > 0:
            assert events[0].get("prev_hash") is None, "First event must have no prev_hash"

        for i in range(1, len(events)):
            expected_prev = events[i - 1].get("hash")
            actual_prev = events[i].get("prev_hash")
            assert actual_prev == expected_prev, (
                f"Chain break at event {i}: expected prev_hash={expected_prev}, "
                f"got {actual_prev}"
            )

        return events

    yield _verify

    # Verify chain at test teardown
    _verify()


@pytest.fixture
def layer_forge_manifest() -> Callable[..., dict]:
    """Factory for Layer Forge manifests.

    Usage:
        manifest() -> valid default manifest
        manifest(id="custom.id", version="1.2.3", gates=[...]) -> override fields
    """
    def _make(
        id: str = "test.entry",
        version: str = "0.1.0",
        gates: list | None = None,
        enforce: list | None = None,
        targets: list | None = None,
        deps: list | None = None,
        host_awareness: dict | None = None,
        **kw,
    ) -> dict:
        m = {
            "id": id,
            "version": version,
            "targets": targets or [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
            "quality_gates": gates or [
                {"gate_id": "schema", "test_path": "tests/layer_forge/test_schema.py"}
            ],
            "enforcement_rules": enforce or [],
        }
        if deps:
            m["dependencies"] = deps
        if host_awareness:
            m["host_awareness"] = host_awareness
        m.update(kw)
        return m

    return _make


@pytest.fixture
def cli_runner(tmp_corvin_home: Path) -> Callable[[list[str], str], subprocess.CompletedProcess]:
    """Run layer_forge_cli.py as subprocess.

    Returns subprocess.CompletedProcess with stdout/stderr/returncode.

    Usage:
        result = cli_runner(["create", "manifest.json"])
        assert result.returncode == 0
        body = json.loads(result.stdout)
    """
    def _run(args: list[str], tenant: str = TENANT_ID) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["CORVIN_HOME"] = str(tmp_corvin_home)
        env["CORVIN_TENANT_ID"] = tenant

        cli_path = REPO_ROOT / "scripts" / "layer_forge_cli.py"
        cmd = [sys.executable, str(cli_path)] + args

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )

    return _run


@pytest.fixture
def console_client(tmp_corvin_home: Path):
    """FastAPI TestClient for console routes.

    Yields (client, csrf_token) tuple. Client has session cookie set.

    Usage:
        client, csrf = console_client
        r = client.get("/v1/console/layer-forge/definitions")
        r = client.post("/v1/console/layer-forge/definitions",
                        json=manifest,
                        headers={"X-CSRF-Token": csrf})
    """
    # Reset imports to use the sandboxed CORVIN_HOME
    _reset_module_cache()

    try:
        from corvin_console import auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Create session
        rec = auth.create_session(tenant_id=TENANT_ID, token_fingerprint="test-fp")
        csrf = auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        # Create app
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")

        # Create client
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)

        yield client, csrf
    finally:
        _reset_module_cache()


@pytest.fixture
def registry_state(tmp_corvin_home: Path) -> Callable[[], dict]:
    """Inspect registry files on disk.

    Returns a callable that reads all registry entries and returns their details.

    Usage:
        state = registry_state()
        assert state["test.entry@0.1.0"]["status"] == "proposed"
    """
    def _state() -> dict:
        registry = tmp_corvin_home / "tenants" / TENANT_ID / "global" / "layer_forge" / "registry"
        if not registry.exists():
            return {}

        result = {}
        for entry_file in registry.glob("*.json"):
            data = json.loads(entry_file.read_text())
            key = entry_file.stem  # "<id>@<version>"
            result[key] = data

        return result

    return _state


def _reset_module_cache() -> None:
    """Clear module cache for imports that depend on CORVIN_HOME.

    Called before/after console_client fixture to ensure fresh imports
    that respect the sandboxed environment.
    """
    modules_to_clear = [
        key for key in list(sys.modules.keys())
        if any(key.startswith(p) for p in [
            "corvin_console", "corvin_gateway", "forge",
            "core.paths", "core.orchestration",
        ])
    ]
    for mod in modules_to_clear:
        del sys.modules[mod]


@pytest.fixture(autouse=True)
def _review_phase_is_stubbed(request, monkeypatch):
    """The REVIEW phase calls the Anthropic API — the one external boundary of Layer Forge.

    Without this, every test that creates a layer ran the real review: green on a machine
    with an API key and red on one without ("Could not resolve authentication method"), and
    the key-less result looked like 19 product failures (2026-10-08). The stub honours the
    real contract (a ``ReviewVerdict``, never an exception). A test that exercises the
    review itself opts out with ``@pytest.mark.real_review``; its model client is mocked there.
    """
    if request.node.get_closest_marker("real_review") or request.module.__name__.endswith("test_review_phase"):
        return
    from core.orchestration.layer_forge import orchestrator
    from core.orchestration.layer_forge.review import ReviewVerdict

    monkeypatch.setattr(orchestrator, "review_layer_definition",
                        lambda manifest, enforcement, **_k: ReviewVerdict("PASS", flags=[]))


# Marks for organizing tests by phase

def pytest_configure(config):
    """Register custom pytest marks for Layer Forge test phases."""
    config.addinivalue_line("markers", "real_review: runs the real review phase (model client mocked by the test)")
    config.addinivalue_line("markers", "validate: schema validation tests")
    config.addinivalue_line("markers", "gate: quality gate execution tests")
    config.addinivalue_line("markers", "enforce: enforcement rule tests")
    config.addinivalue_line("markers", "create: definition creation tests")
    config.addinivalue_line("markers", "promote: status transition tests")
    config.addinivalue_line("markers", "cli: CLI subprocess tests")
    config.addinivalue_line("markers", "console: HTTP console route tests")
    config.addinivalue_line("markers", "audit: audit chain integrity tests")
    config.addinivalue_line("markers", "tenant: tenant isolation tests")
    config.addinivalue_line("markers", "error: error handling and edge cases")
    config.addinivalue_line("markers", "concurrency: locking and serialization tests")
    config.addinivalue_line("markers", "compliance: GDPR/security tests")
