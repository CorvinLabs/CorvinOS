"""SRF-3 / SRF-4: every console writer of ``tenant.corvin.yaml`` goes through
``corvin_console.tenant_yaml``: mode 0o600 (set before ``os.replace``), atomic, and an
unreadable file is REFUSED instead of being rewritten from ``{}``.

The gateway reads this file fail-closed on its mode (``corvin_gateway.tenant_config``),
so a writer at umask (0o664) disables every gateway run.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

_CONSOLE = Path(__file__).resolve().parents[1] / "corvin_console"

SEED = {
    "apiVersion": "corvin/v1",
    "kind": "Tenant",
    "spec": {
        "web_chat": {"worker_engine": "acs"},
        "telemetry": {"ping_enabled": False},
        "data_residency": {"zone": "eu"},
    },
}
MALFORMED = "spec: [unclosed\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    old = os.umask(0o002)
    try:
        yield tmp_path
    finally:
        os.umask(old)


def _yaml_path(home: Path, tid: str = "_default") -> Path:
    return home / "tenants" / tid / "global" / "tenant.corvin.yaml"


def _seed(home: Path, text: str | None = None) -> Path:
    p = _yaml_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text is not None else yaml.safe_dump(SEED), encoding="utf-8")
    os.chmod(p, 0o600)
    return p


def _mode(p: Path) -> int:
    return p.stat().st_mode & 0o777


def _client(router, rec) -> TestClient:
    from corvin_console import deps

    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    app.dependency_overrides[deps.require_session] = lambda: rec
    app.dependency_overrides[deps.require_csrf] = lambda: rec
    return TestClient(app)


@pytest.fixture
def rec():
    from types import SimpleNamespace

    return SimpleNamespace(tenant_id="_default", sid_fingerprint="fp-test", tier="owner")


# ── the shared writer ───────────────────────────────────────────────────────


def test_write_mapping_is_0600_under_a_wide_umask(home):
    from corvin_console import tenant_yaml

    p = _yaml_path(home)
    tenant_yaml.write_mapping(p, {"spec": {"a": 1}})
    assert _mode(p) == 0o600
    assert not [x for x in p.parent.iterdir() if x.name.endswith(".tmp")]


def test_update_refuses_an_unparseable_file_and_leaves_it_byte_identical(home):
    from corvin_console import tenant_yaml

    p = _seed(home, MALFORMED)
    with pytest.raises(tenant_yaml.TenantYamlUnreadable):
        tenant_yaml.update(p, lambda d: d.setdefault("spec", {}).update(x=1))
    assert p.read_text(encoding="utf-8") == MALFORMED


# ── every console writer, through its real route ────────────────────────────


def test_features_toggle_keeps_0600_and_every_other_section(home, rec):
    from corvin_console import feature_flags
    from corvin_console.routes import features

    p = _seed(home)
    os.chmod(p, 0o600)
    fid = feature_flags.REGISTRY[0].id
    r = _client(features.router, rec).post(
        "/v1/console/features/toggle", json={"feature_id": fid, "enabled": True})
    assert r.status_code == 200, r.text
    assert _mode(p) == 0o600
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["spec"]["features_whitelist"] == [fid]
    assert doc["spec"]["telemetry"] == {"ping_enabled": False}
    assert doc["spec"]["data_residency"] == {"zone": "eu"}
    assert doc["apiVersion"] == "corvin/v1"


def test_features_toggle_on_a_fresh_tenant_creates_the_file_at_0600(home, rec):
    from corvin_console import feature_flags
    from corvin_console.routes import features

    fid = feature_flags.REGISTRY[0].id
    r = _client(features.router, rec).post(
        "/v1/console/features/toggle", json={"feature_id": fid, "enabled": True})
    assert r.status_code == 200, r.text
    assert _mode(_yaml_path(home)) == 0o600


def test_features_toggle_refuses_an_unreadable_file_instead_of_wiping_it(home, rec, monkeypatch):
    from corvin_console import audit as console_audit
    from corvin_console import feature_flags
    from corvin_console.routes import features

    failed: list[dict] = []
    monkeypatch.setattr(console_audit, "action_failed", lambda **kw: failed.append(kw))
    p = _seed(home, MALFORMED)
    fid = feature_flags.REGISTRY[0].id
    r = _client(features.router, rec).post(
        "/v1/console/features/toggle", json={"feature_id": fid, "enabled": True})
    assert r.status_code == 409, r.text
    assert p.read_text(encoding="utf-8") == MALFORMED
    assert failed and failed[0]["reason"] == "tenant-yaml-unreadable"


def test_vibe_pipeline_writer_keeps_0600(home):
    from corvin_console.routes import vibe_engineering as ve

    if ve._forge_paths is None:
        pytest.skip("forge paths unavailable")
    p = _seed(home)
    ve._write_pipeline_config("_default", [{"stage": "memory"}])
    assert _mode(p) == 0o600
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["spec"]["context_engineering"]["pipeline"] == [{"stage": "memory"}]
    assert doc["spec"]["web_chat"] == {"worker_engine": "acs"}


def test_vibe_pipeline_writer_refuses_an_unreadable_file(home):
    from corvin_console import tenant_yaml
    from corvin_console.routes import vibe_engineering as ve

    if ve._forge_paths is None:
        pytest.skip("forge paths unavailable")
    p = _seed(home, MALFORMED)
    with pytest.raises(tenant_yaml.TenantYamlUnreadable):
        ve._write_pipeline_config("_default", [{"stage": "memory"}])
    assert p.read_text(encoding="utf-8") == MALFORMED


def test_healing_config_writer_keeps_0600_and_refuses_unreadable(home):
    from corvin_console import tenant_yaml
    from corvin_console.routes import healing_config as hc

    p = _seed(home)
    hc._write_flags("_default", {"risky_enabled": True})
    assert _mode(p) == 0o600
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["spec"]["data_residency"] == {"zone": "eu"}
    p.write_text(MALFORMED, encoding="utf-8")
    with pytest.raises(tenant_yaml.TenantYamlUnreadable):
        hc._write_flags("_default", {"risky_enabled": False})
    assert p.read_text(encoding="utf-8") == MALFORMED


def test_engine_and_compute_writers_keep_0600(home):
    from corvin_console.routes import compute, engine

    p = _seed(home)
    os.chmod(p, 0o664)
    engine._save_tenant_yaml("_default", dict(SEED))
    assert _mode(engine._tenant_yaml_path("_default")) == 0o600
    os.chmod(p, 0o664)
    compute._write_tenant_yaml("_default", dict(SEED))
    assert _mode(p) == 0o600


def test_preset_writer_uses_the_session_tenant_file(home, rec):
    from corvin_console.api import feature_status_endpoints as fse

    p = _seed(home)
    fse._save_preset("_default", "advanced")
    assert _mode(p) == 0o600
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["spec"]["preset"] == "advanced"
    assert doc["spec"]["web_chat"] == {"worker_engine": "acs"}


# ── source guard: no new writer bypasses tenant_yaml ───────────────────────

# A module that names the tenant YAML AND contains a YAML dump is a candidate
# writer of that file. Only these may do so, each for a stated reason.
_DUMP_ALLOWED = {
    "tenant_yaml.py": "the one writer",
    "routes/workflows.py": "dumps workflow manifests, not the tenant spec",
}


def _yaml_dump_calls(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in {"dump", "safe_dump"} \
                and isinstance(node.func.value, ast.Name) and "yaml" in node.func.value.id:
            n += 1
    return n


def test_no_console_module_dumps_the_tenant_yaml_outside_the_shared_writer():
    files = [p for p in _CONSOLE.rglob("*.py")
             if "web-next" not in p.parts and "tests" not in p.parts]
    assert len(files) > 100, "positive control: the scan must reach the console package"
    offenders = []
    reached_writer = False
    for p in files:
        src = p.read_text(encoding="utf-8")
        if "tenant.corvin" not in src and "_tenant_yaml_path" not in src:
            continue
        rel = p.relative_to(_CONSOLE).as_posix()
        if rel == "tenant_yaml.py":
            reached_writer = True
        if _yaml_dump_calls(ast.parse(src)) and rel not in _DUMP_ALLOWED:
            offenders.append(rel)
    assert reached_writer, "positive control: tenant_yaml.py must be among the scanned writers"
    assert offenders == [], (
        f"{offenders} dump YAML next to a tenant.corvin.yaml reference - write the tenant "
        "spec through corvin_console.tenant_yaml (0o600, atomic, refuse-unreadable)")
