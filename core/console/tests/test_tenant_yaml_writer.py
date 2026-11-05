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


# ── the gateway CLI writes through the same module (review RC-3) ────────────

_GATEWAY = Path(__file__).resolve().parents[2] / "gateway" / "corvin_gateway"
_GATEWAY_DUMP_ALLOWED = {
    "cli.py": "prints a config to stdout (`corvin tenant show`), writes nothing",
    "packaging.py": "dumps a plugin package manifest, not the tenant spec",
}


def test_no_gateway_module_dumps_the_tenant_yaml_outside_the_shared_writer():
    files = [p for p in _GATEWAY.rglob("*.py") if "tests" not in p.parts]
    assert len(files) >= 15, "positive control: the scan must reach the gateway package"
    offenders = []
    reached = False
    for p in files:
        src = p.read_text(encoding="utf-8")
        if "tenant.corvin" not in src and "CONFIG_FILENAME" not in src:
            continue
        rel = p.relative_to(_GATEWAY).as_posix()
        if rel == "tenant_config.py":
            reached = True
            assert "_tenant_yaml_writer()" in src and "write_mapping" in src, \
                "tenant_config.save must write through corvin_console.tenant_yaml"
        if _yaml_dump_calls(ast.parse(src)) and rel not in _GATEWAY_DUMP_ALLOWED:
            offenders.append(rel)
    assert reached, "positive control: tenant_config.py must be among the scanned modules"
    assert offenders == [], f"{offenders} dump YAML next to a tenant.corvin.yaml reference"


def test_gateway_save_is_0600_locked_and_keeps_console_sections(home, monkeypatch):
    from corvin_console import tenant_yaml
    from corvin_gateway import tenant_config

    p = _seed(home)
    taken: list[Path] = []
    real_locked = tenant_yaml.locked

    def _spy(path):
        taken.append(path)
        return real_locked(path)

    monkeypatch.setattr(tenant_yaml, "locked", _spy)
    cfg = tenant_config.load("_default")
    tenant_config.save(cfg)
    assert taken == [p], "save() must take the shared tenant-YAML lock"
    assert _mode(p) == 0o600
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["spec"]["web_chat"] == {"worker_engine": "acs"}


# ── no unlocked read-modify-write (review RC-3) ──────────────────────────────

_READS = {"load_for_write", "_load_tenant_yaml_for_write", "_load_policy", "_read_tenant_yaml",
          "_read_tenant_config", "_load_tenant_yaml", "safe_load"}
_WRITES = {"write_mapping", "_save_tenant_yaml", "_write_tenant_yaml", "_write_tenant_config"}


def _called_names(fn: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


def test_no_function_reads_then_writes_the_tenant_yaml_without_update():
    """A function that both loads the tenant YAML and writes it whole is an
    unlocked read-modify-write: a concurrent ``tenant_yaml.update`` landing in
    between is silently undone. It must be ONE ``tenant_yaml.update(path, mutate)``."""
    files = [p for p in _CONSOLE.rglob("*.py") if "web-next" not in p.parts and "tests" not in p.parts]
    files += [p for p in _GATEWAY.rglob("*.py") if "tests" not in p.parts]
    offenders = []
    scanned_engine = False
    for p in files:
        src = p.read_text(encoding="utf-8")
        if "tenant" not in src or p.name in ("tenant_yaml.py", "plugin_cmd.py"):
            continue
        if p.name == "engine.py":
            scanned_engine = True
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = _called_names(node)
                if names & _READS and names & _WRITES:
                    offenders.append(f"{p.name}:{node.name}")
    assert scanned_engine, "positive control: routes/engine.py must be scanned"
    assert offenders == [], f"unlocked read-modify-write of tenant.corvin.yaml: {offenders}"


def test_engine_put_holds_the_lock_across_its_read_modify_write(home, rec, monkeypatch):
    """Two threads: PUT /settings/engine (real route) and a features-style
    ``tenant_yaml.update``. The update is started while the engine route is inside
    its read-modify-write; it must wait for the lock and keep both changes. Before
    RC-3 the engine route read, the update committed, and the engine wrote its stale
    copy - ``features_whitelist`` was gone without an error."""
    import threading

    from corvin_console import tenant_yaml
    from corvin_console.routes import engine

    p = _seed(home)
    inside = threading.Event()
    other_started = threading.Event()
    real_load = tenant_yaml.load_for_write

    def _slow_load(path):
        data = real_load(path)
        if threading.current_thread().name == "engine-put":
            inside.set()
            other_started.wait(5)
            import time as _t
            _t.sleep(0.3)  # the other writer is now blocked on the lock
        return data

    monkeypatch.setattr(tenant_yaml, "load_for_write", _slow_load)
    client = _client(engine.router, rec)
    result: dict = {}

    def put():
        result["r"] = client.put("/v1/console/settings/engine", json={"default_engine": "claude_code"})

    def toggle():
        inside.wait(5)
        other_started.set()

        def _m(doc):
            doc["spec"]["features_whitelist"] = ["vibe_engineering"]
        tenant_yaml.update(p, _m)

    a = threading.Thread(target=put, name="engine-put")
    b = threading.Thread(target=toggle, name="features-toggle")
    a.start()
    b.start()
    a.join(10)
    b.join(10)
    assert result["r"].status_code == 200, result["r"].text
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["spec"]["default_engine"] == "claude_code"
    assert doc["spec"]["features_whitelist"] == ["vibe_engineering"], doc
    assert doc["spec"]["data_residency"] == {"zone": "eu"}


def test_engine_put_refuses_an_unreadable_file(home, rec):
    from corvin_console.routes import engine

    p = _seed(home, MALFORMED)
    r = _client(engine.router, rec).put("/v1/console/settings/engine",
                                        json={"default_engine": "claude_code"})
    assert r.status_code == 422, r.text
    assert p.read_text(encoding="utf-8") == MALFORMED


# ── mode, symlink, comments (review RC-9 / RC-13) ────────────────────────────


def test_chmod_before_replace_is_what_makes_the_file_0600(home, monkeypatch):
    """The temp file is widened to 0o664 right after mkstemp (what an ``open()``-
    based writer would create) and the target pre-exists at 0o664; only the
    explicit chmod can bring the result to 0o600. Patching that chmod away fails
    this test (mutation-checked below)."""
    import tempfile

    from corvin_console import tenant_yaml

    p = _seed(home)
    os.chmod(p, 0o664)
    real_mkstemp = tempfile.mkstemp
    real_chmod = os.chmod

    def _wide_mkstemp(*a, **kw):
        fd, name = real_mkstemp(*a, **kw)
        real_chmod(name, 0o664)
        return fd, name

    monkeypatch.setattr(tenant_yaml.tempfile, "mkstemp", _wide_mkstemp)
    tenant_yaml.write_mapping(p, {"spec": {"a": 1}})
    assert _mode(p) == 0o600

    # mutation: without the chmod the same write leaves 0o664 behind
    os.chmod(p, 0o664)
    monkeypatch.setattr(tenant_yaml.os, "chmod",
                        lambda path, mode, *a, **k: None if str(path).endswith(".tmp")
                        else real_chmod(path, mode, *a, **k))
    tenant_yaml.write_mapping(p, {"spec": {"a": 2}})
    assert _mode(p) == 0o664, "positive control: the chmod is the only thing making it 0o600"


def test_a_symlinked_tenant_yaml_is_written_through(home, tmp_path):
    from corvin_console import tenant_yaml

    p = _seed(home)
    real = tmp_path / "shared-tenant.yaml"
    real.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(real, 0o600)
    p.unlink()
    p.symlink_to(real)
    tenant_yaml.update(p, lambda d: d["spec"].__setitem__("x", 1))
    assert p.is_symlink(), "the link must stay a link"
    assert yaml.safe_load(real.read_text(encoding="utf-8"))["spec"]["x"] == 1
    assert _mode(real) == 0o600
    assert not [x for x in real.parent.iterdir() if x.name.endswith(".tmp")]


def test_rewrites_carry_the_managed_by_console_header(home):
    from corvin_console import tenant_yaml

    p = _seed(home, "# operator note\n" + yaml.safe_dump(SEED))
    tenant_yaml.update(p, lambda d: None)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# managed by console")
    assert "operator note" not in text  # documented: comments are not preserved
    assert yaml.safe_load(text)["spec"]["data_residency"] == {"zone": "eu"}
