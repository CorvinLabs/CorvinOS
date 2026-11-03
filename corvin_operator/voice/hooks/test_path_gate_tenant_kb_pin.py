"""The tenant's own knowledge base is a pinned kb.py location (ADR-2265, 2026-11-03).

A fresh install has no sibling Corvin-Knowledge checkout: its KB is <CORVIN_HOME>/tenants/<tenant>/knowledge,
created by the Knowledge Graph plugin. Before this change the path gate pinned only the sibling path, so every
agent session of a fresh install was refused ``kb.py`` (it imports subprocess -> AST gate). Drives the real
``path_gate.check`` with the REAL engine bytes the plugin installs (LF and CRLF), and proves the pin follows the
session's tenant only.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parent
if str(_HOOK) not in sys.path:
    sys.path.insert(0, str(_HOOK))

import path_gate as pg  # type: ignore  # noqa: E402

_ENGINE_DIR = (Path(__file__).resolve().parents[3].parent / "Corvin-Marketplace" / "plugins" / "buildin"
               / "knowledge_management" / "corvin_knowledge" / "src" / "kb_engine")


def _check(cmd: str) -> tuple[bool, str]:
    return pg.check({"tool_name": "Bash", "tool_input": {"command": cmd}})


def _engine_bytes() -> dict[str, bytes]:
    if not (_ENGINE_DIR / "ENGINE.json").is_file():
        pytest.skip("no Corvin-Marketplace checkout beside this repo")
    return {n: (_ENGINE_DIR / n).read_bytes().replace(b"\r\n", b"\n") for n in pg._KB_ENGINE_PIN}


def _install(kb: Path, files: dict[str, bytes], crlf: bool = False) -> Path:
    d = kb / "scripts"
    d.mkdir(parents=True)
    for n, data in files.items():
        (d / n).write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    return d / "kb.py"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "corvin_home"
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.delenv("CORVIN_KB_REPO", raising=False)
    monkeypatch.delenv("CORVIN_ACS_TENANT_ID", raising=False)
    monkeypatch.setattr(pg, "_PINNED_PYTHON_TOOLS", {})          # no sibling checkout: a fresh install
    monkeypatch.setattr(pg, "_emit_code_exec_audit", lambda *a, **k: None)
    return h


def test_the_pin_equals_the_engine_the_plugin_ships():
    m = json.loads((_ENGINE_DIR / "ENGINE.json").read_text(encoding="utf-8")) \
        if (_ENGINE_DIR / "ENGINE.json").is_file() else pytest.skip("no Corvin-Marketplace checkout")
    assert m["files"] == pg._KB_ENGINE_PIN
    for name, data in _engine_bytes().items():
        assert hashlib.sha256(data).hexdigest() == pg._KB_ENGINE_PIN[name], name


@pytest.mark.parametrize("crlf", [False, True])
def test_the_sessions_tenant_kb_runs(home, monkeypatch, crlf):
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    script = _install(home / "tenants" / "acme" / "knowledge", _engine_bytes(), crlf=crlf)
    ok, reason = _check(f"python3 {script.as_posix()} check")
    assert ok, reason


def test_control_another_tenants_kb_is_not_pinned_for_this_session(home, monkeypatch):
    script = _install(home / "tenants" / "acme" / "knowledge", _engine_bytes())
    monkeypatch.setenv("CORVIN_TENANT_ID", "globex")
    ok, reason = _check(f"python3 {script.as_posix()} check")
    assert not ok and "AST gate" in reason, reason


def test_a_modified_tenant_engine_is_denied(home, monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    files = _engine_bytes()
    files["kb_lib.py"] += b"\n# tampered\n"
    script = _install(home / "tenants" / "acme" / "knowledge", files)
    ok, reason = _check(f"python3 {script.as_posix()} check")
    assert not ok and "sha256 mismatch" in reason, reason


@pytest.mark.parametrize("tenant", ["../acme", "a/b", "", "..", "x" * 80])
def test_a_malformed_tenant_id_pins_no_tenant_path(home, monkeypatch, tenant):
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant)
    pins = pg._session_kb_pins()
    assert all("tenants" not in Path(p).parts or Path(p).parts[-4] == "_default" for p in pins), pins
    if tenant:
        assert not any(tenant in p for p in pins), pins


def test_the_operators_explicit_kb_is_pinned(tmp_path, home, monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    script = _install(tmp_path / "my-kb", _engine_bytes())
    monkeypatch.setenv("CORVIN_KB_REPO", str(tmp_path / "my-kb"))
    ok, reason = _check(f"python3 {script.as_posix()} new task --title x --epic E-001")
    assert ok, reason


def test_the_relative_form_names_the_tenant_kb_in_the_hint(home, monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    script = _install(home / "tenants" / "acme" / "knowledge", _engine_bytes())
    monkeypatch.chdir(script.parent.parent)
    ok, reason = _check("python3 scripts/kb.py check")
    assert not ok and str(script) in reason, reason
