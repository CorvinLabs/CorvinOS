"""App-bound channels (ADR-2333, T-0425): the plugin lifecycle keeps the binding registry in step.

Drives the REAL ``PluginLifecycle`` (``corvin_plugins.state``), the REAL binding registry
(``core.channel_bindings``, its own audit-first writer into the sandboxed tenant chain) and the
REAL boot path (``bootstrap.bootstrap_tenant``). The plugin is a small directory in ``tmp_path``
whose ``describe`` command is a Python script that prints the chat it serves.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
_FORGE = _REPO / "corvin_operator" / "forge"
_SHARED = _REPO / "corvin_operator" / "bridges" / "shared"
_CONSOLE = _REPO / "core" / "console"
for _p in (str(_PKG), str(_FORGE), str(_SHARED), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import core.channel_bindings as cb  # noqa: E402
from corvin_plugins import bootstrap  # noqa: E402
from corvin_plugins.manifest import PIIRisk, PluginOrigin, PluginRecord  # noqa: E402
from corvin_plugins.state import PluginLifecycle, TenantRegistry, registry_path  # noqa: E402

TID = "_default"
CHANNEL = "discord"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str((tmp_path / "anchor.key").resolve()))
    monkeypatch.setenv("ADAPTER_BRIDGES_DIR", str(tmp_path / "bridges"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    return home


def _plugin_dir(tmp_path: Path, name: str, chat_id: str) -> Path:
    d = tmp_path / "plugins" / name
    d.mkdir(parents=True)
    (d / "chat.txt").write_text(chat_id, encoding="utf-8")
    (d / "describe.py").write_text(textwrap.dedent("""
        import json, pathlib
        here = pathlib.Path(__file__).resolve().parent
        print(json.dumps({"chat_id": (here / "chat.txt").read_text().strip()}))
    """), encoding="utf-8")
    (d / "handler.py").write_text('import json; print(json.dumps({"text": "ok"}))\n', encoding="utf-8")
    return d


def _decl(plugin_dir: Path) -> dict:
    return cb.validate_declaration({
        "channel": CHANNEL,
        "describe": {"argv": ["python", "describe.py"]},
        "handler": {"argv": ["python", "handler.py"]},
    }, plugin_dir)


def _record(pid: str, decl: dict | None) -> PluginRecord:
    return PluginRecord(
        plugin_id=pid, version="1.0.0", display_name=pid, plugin_type="notification_backend",
        origin=PluginOrigin.VETTED, pii_risk=PIIRisk.LOW, bridge_channel=decl,
    )


def _lc(home: Path) -> PluginLifecycle:
    return PluginLifecycle(tenant_id=TID, corvin_home_path=home, lifecycle_enabled=True)


def _bindings(pid: str | None = None) -> list[dict]:
    return [b for b in cb.list_bindings(TID) if pid is None or b["plugin_id"] == pid]


def _chain_events(home: Path) -> list[str]:
    chain = home / "tenants" / TID / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    out = []
    for line in chain.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        out.append(str(rec.get("event") or rec.get("event_type") or ""))
    return out


def _install_enable(home: Path, record: PluginRecord) -> PluginLifecycle:
    lc = _lc(home)
    lc.install(record, installed_by="operator")
    lc.enable(record.plugin_id)
    return lc


# ── AC1: fresh chat active on enable, suspended on disable, back on re-enable, gone on uninstall ──


def test_fresh_chat_lifecycle(env, tmp_path):
    d = _plugin_dir(tmp_path, "app-one", "1111")
    lc = _install_enable(env, _record("app-one", _decl(d)))

    [b] = _bindings("app-one")
    assert (b["channel"], b["chat_id"], b["status"], b["claimed_by"]) == (CHANNEL, "1111", "active", "plugin_manifest")
    assert "channel_binding.claimed" in _chain_events(env)

    lc.disable("app-one")
    [b] = _bindings("app-one")
    assert b["status"] == "suspended" and b["reason"] == "plugin_disabled"

    lc.enable("app-one")
    [b2] = _bindings("app-one")
    assert b2["status"] == "active" and b2["binding_id"] == b["binding_id"]

    lc.disable("app-one")
    lc.uninstall("app-one")
    assert _bindings() == []
    assert "channel_binding.released" in _chain_events(env)


# ── AC2: a chat with history is pending; re-enable restores pending, never confirms ──


def test_chat_with_history_is_pending(env, tmp_path):
    ledger = env / "tenants" / TID / "session_ledger" / CHANNEL / "2222" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"user_text": "earlier"}\n', encoding="utf-8")
    d = _plugin_dir(tmp_path, "app-one", "2222")
    lc = _install_enable(env, _record("app-one", _decl(d)))

    [b] = _bindings("app-one")
    assert b["status"] == "pending"

    lc.disable("app-one")
    assert _bindings("app-one")[0]["status"] == "suspended"
    lc.enable("app-one")
    assert _bindings("app-one")[0]["status"] == "pending"


# ── AC3: a second plugin claiming the same chat is refused and audited; its enable still succeeds ──


def test_second_plugin_same_chat_refused(env, tmp_path):
    d1 = _plugin_dir(tmp_path, "app-one", "3333")
    d2 = _plugin_dir(tmp_path, "app-two", "3333")
    _install_enable(env, _record("app-one", _decl(d1)))
    lc = _install_enable(env, _record("app-two", _decl(d2)))

    assert TenantRegistry.load(tenant_id=TID, corvin_home_path=env).get("app-two").enabled
    assert _bindings("app-two") == []
    [b] = _bindings()
    assert b["plugin_id"] == "app-one" and b["status"] == "active"
    assert "channel_binding.refused" in _chain_events(env)
    lc.disable("app-two")
    assert _bindings("app-one")[0]["status"] == "active"


# ── AC4: boot reconcile restores without duplicates, suspends orphans ──


def test_reconcile_restores_without_duplicates(env, tmp_path):
    d = _plugin_dir(tmp_path, "app-one", "4444")
    _install_enable(env, _record("app-one", _decl(d)))
    [before] = _bindings()

    bootstrap.bootstrap_tenant(tenant_id=TID, corvin_home=env, lifecycle_enabled=True)
    bootstrap.bootstrap_tenant(tenant_id=TID, corvin_home=env, lifecycle_enabled=True)
    [after] = _bindings()
    assert after["binding_id"] == before["binding_id"] and after["status"] == "active"

    # The binding list is lost: the next boot rebuilds it from the records.
    cb.registry_path(TID).unlink()
    bootstrap.bootstrap_tenant(tenant_id=TID, corvin_home=env, lifecycle_enabled=True)
    [rebuilt] = _bindings()
    assert (rebuilt["plugin_id"], rebuilt["chat_id"], rebuilt["status"]) == ("app-one", "4444", "active")


def test_reconcile_suspends_orphaned_binding(env, tmp_path):
    d = _plugin_dir(tmp_path, "app-gone", "5555")
    cb.claim(TID, plugin_id="app-gone", decl=_decl(d), chat_id="5555")
    assert _bindings("app-gone")[0]["status"] == "active"

    bootstrap.bootstrap_tenant(tenant_id=TID, corvin_home=env, lifecycle_enabled=True)
    [b] = _bindings("app-gone")
    assert b["status"] == "suspended" and b["reason"] == "orphaned"


# ── AC5: a record without the field writes no key; one with it round-trips ──


def test_record_without_field_writes_no_key(env, tmp_path):
    rec = _record("app-plain", None)
    assert "bridge_channel" not in rec.to_dict()
    _lc(env).install(rec, installed_by="operator")
    text = registry_path(tenant_id=TID, corvin_home_path=env).read_text(encoding="utf-8")
    assert "bridge_channel" not in text
    assert TenantRegistry.load(tenant_id=TID, corvin_home_path=env).get("app-plain").bridge_channel is None


def test_record_with_field_round_trips(env, tmp_path):
    decl = _decl(_plugin_dir(tmp_path, "app-one", "6666"))
    _lc(env).install(_record("app-one", decl), installed_by="operator")
    stored = TenantRegistry.load(tenant_id=TID, corvin_home_path=env).get("app-one")
    assert stored.bridge_channel == decl
    assert PluginRecord.from_dict(stored.to_dict()).bridge_channel == decl


# ── Projection at install time: valid kept, invalid dropped (never the install) ──


def _resolve_module():
    if str(_CONSOLE) not in sys.path:
        sys.path.append(str(_CONSOLE))
    from corvin_console.routes import marketplace_resolve  # noqa: PLC0415

    return marketplace_resolve


def _manifest(pid: str, bridge_channel: dict) -> dict:
    return {"plugin_id": pid, "version": "1.0.0", "plugin_type": "notification_backend",
            "pii_risk": "low", "bridge_channel": bridge_channel}


def test_projection_keeps_a_valid_declaration(env, tmp_path):
    mr = _resolve_module()
    d = _plugin_dir(tmp_path, "app-one", "7777")
    rec = mr.record_from_manifest(_manifest("app-one", {
        "channel": CHANNEL, "describe": {"argv": ["python", "describe.py"]},
        "handler": {"argv": ["python", "handler.py"]}}), plugin_dir=d)
    assert rec.bridge_channel == _decl(d)


def test_projection_drops_argv_leaving_the_plugin_dir(env, tmp_path):
    mr = _resolve_module()
    d = _plugin_dir(tmp_path, "app-one", "8888")
    rec = mr.record_from_manifest(_manifest("app-one", {
        "channel": CHANNEL, "describe": {"argv": ["python", "../../escape.py"]},
        "handler": {"argv": ["python", "handler.py"]}}), plugin_dir=d)
    assert rec.bridge_channel is None
    assert "bridge_channel" not in rec.to_dict()
    _lc(env).install(rec, installed_by="operator")          # the install itself is unaffected
    assert TenantRegistry.load(tenant_id=TID, corvin_home_path=env).has("app-one")


def test_enable_without_a_declaration_releases_a_left_over_binding(env, tmp_path):
    """Updated while disabled to a version that declares no channel: enabling it must not leave the old binding
    suspended forever (the chat would drop every message)."""
    from corvin_plugins.state import sync_bridge_channel
    d = _plugin_dir(tmp_path, "app-two", "2222")
    lc = _install_enable(env, _record("app-two", _decl(d)))
    lc.disable("app-two")
    assert _bindings("app-two")[0]["status"] == "suspended"
    sync_bridge_channel(_record("app-two", None), "enable", tenant_id=TID)
    assert _bindings("app-two") == []


def test_enable_from_inside_an_event_loop_never_runs_the_plugin_subprocess_on_the_loop(env, tmp_path):
    import asyncio
    import threading
    from corvin_plugins import state as st
    d = _plugin_dir(tmp_path, "app-loop", "3333")
    lc = _lc(env)
    lc.install(_record("app-loop", _decl(d)), installed_by="operator")
    seen = []
    real = st.sync_bridge_channel

    def spy(record, action, *, tenant_id):
        seen.append(threading.current_thread().name)
        return real(record, action, tenant_id=tenant_id)
    st.sync_bridge_channel = spy
    try:
        async def main():
            lc.enable("app-loop")                                          # the console calls it like this
        asyncio.run(main())
        st._bridge_sync_pool().submit(lambda: None).result(timeout=30)     # wait for the queued sync
    finally:
        st.sync_bridge_channel = real
    assert seen and seen[0].startswith("bridge-channel-sync")
    assert [b["chat_id"] for b in _bindings("app-loop")] == ["3333"]
