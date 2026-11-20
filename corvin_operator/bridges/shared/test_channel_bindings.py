"""Registry of app-bound channels (ADR-2333, T-0423): races, tampering, refusals, audit, sender token.

Runs against the real module, the real hash-chained audit writer and real subprocesses, in a sandboxed CORVIN_HOME.

    python3 -m pytest corvin_operator/bridges/shared/test_channel_bindings.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
FORGE = REPO / "corvin_operator" / "forge"
if str(FORGE) not in sys.path:
    sys.path.insert(0, str(FORGE))

import core.channel_bindings as cb  # noqa: E402

TENANT = "_default"
CHAT = "4242424242424242"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    (home / "tenants" / TENANT / "global" / "forge").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str((tmp_path / "anchor.key").resolve()))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    bridges = tmp_path / "bridges"
    (bridges / "discord").mkdir(parents=True)
    (bridges / "discord" / "settings.json").write_text(json.dumps({"whitelist": ["owner1"], "chat_profiles": {}}))
    monkeypatch.setenv("ADAPTER_BRIDGES_DIR", str(bridges))
    return tmp_path


def plugin_dir(tmp_path: Path, name: str = "plug") -> Path:
    d = tmp_path / name
    (d / "src").mkdir(parents=True, exist_ok=True)
    (d / "describe.py").write_text('import json; print(json.dumps({"chat_id": "%s"}))' % CHAT)
    (d / "handler.py").write_text("import sys, json; t = sys.stdin.read(); print(json.dumps({'text': 'got %d' % len(t)}))")
    return d


def decl_for(root: Path, **over) -> dict:
    decl = {"channel": "discord", "describe": {"argv": ["python", "describe.py"]},
            "handler": {"argv": ["python", "handler.py"], "env_allow": ["HOME"], "pythonpath": ["src"]}}
    decl.update(over)
    return cb.validate_declaration(decl, root)


def chain_events() -> list[str]:
    from core.paths.tenant import tenant_audit_chain
    p = tenant_audit_chain(TENANT)
    if not p.exists():
        return []
    return [json.loads(line).get("event_type") or json.loads(line).get("event") for line in p.read_text().splitlines() if line.strip()]


def test_fresh_chat_is_active_and_audited(sandbox):
    d = decl_for(plugin_dir(sandbox))
    info = cb.describe(d, TENANT)
    assert info["chat_id"] == CHAT
    b = cb.claim(TENANT, plugin_id="plug", decl=d, chat_id=CHAT)
    assert b["status"] == "active" and cb.resolve(TENANT, "discord", CHAT)["binding_id"] == b["binding_id"]
    assert "channel_binding.claimed" in chain_events()
    assert oct(cb.registry_path(TENANT).stat().st_mode & 0o777) == "0o600"


def test_a_chat_with_history_needs_a_confirmation(sandbox):
    led = Path(os.environ["CORVIN_HOME"]) / "tenants" / TENANT / "session_ledger" / "discord" / CHAT / "ledger.jsonl"
    led.parent.mkdir(parents=True)
    led.write_text('{"kind": "turn"}\n')
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    assert b["status"] == "pending"
    assert cb.confirm(TENANT, b["binding_id"])["status"] == "active"
    assert "channel_binding.activated" in chain_events()


def test_two_processes_claiming_the_same_chat_leave_exactly_one_owner(sandbox):
    a, b = plugin_dir(sandbox, "pa"), plugin_dir(sandbox, "pb")
    script = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(REPO)!r}); sys.path.insert(0, {str(FORGE)!r})
        import core.channel_bindings as cb
        from pathlib import Path
        root = Path(sys.argv[2])
        decl = cb.validate_declaration({{"channel": "discord", "describe": {{"argv": ["python", "describe.py"]}},
                                         "handler": {{"argv": ["python", "handler.py"]}}}}, root)
        try:
            cb.claim("{TENANT}", plugin_id=sys.argv[1], decl=decl, chat_id="{CHAT}")
            print("won")
        except cb.BindingError:
            print("refused")
    """)
    procs = [subprocess.Popen([sys.executable, "-c", script, pid, str(root)], stdout=subprocess.PIPE, text=True,
                              env=os.environ.copy()) for pid, root in (("plug-a", a), ("plug-b", b))]
    outs = sorted(p.communicate(timeout=60)[0].strip() for p in procs)
    assert outs == ["refused", "won"], outs
    owners = [x for x in cb.list_bindings(TENANT) if x["chat_id"] == CHAT]
    assert len(owners) == 1
    assert "channel_binding.refused" in chain_events()


@pytest.mark.parametrize("bad", ["*", "", "a/b", "../x", "4242 4242", "x" * 200])
def test_invalid_chat_ids_are_refused(sandbox, bad):
    with pytest.raises(cb.BindingError):
        cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=bad)


def test_a_tampered_registry_binds_nothing_and_refuses_writes(sandbox):
    cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    cb.registry_path(TENANT).write_text('{"version": 1, "bindings": [{"chat_id": "*"}]}')
    assert cb.resolve(TENANT, "discord", CHAT) is None and cb.list_bindings(TENANT) == []
    with pytest.raises(cb.BindingError):                                    # routing readers must learn it is damaged
        cb.resolve_strict(TENANT, "discord", CHAT)
    with pytest.raises(cb.BindingError):
        cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox, "p2")), chat_id="99")


def test_declarations_that_leave_the_plugin_dir_or_leak_secrets_are_refused(sandbox):
    root = plugin_dir(sandbox)
    for decl in ({"channel": "discord", "describe": {"argv": ["python", "../evil.py"]}, "handler": {"argv": ["python", "handler.py"]}},
                 {"channel": "discord", "describe": {"argv": ["/bin/sh", "-c", "x"]}, "handler": {"argv": ["python", "handler.py"]}},
                 {"channel": "discord", "describe": {"argv": ["python", "describe.py"]},
                  "handler": {"argv": ["python", "handler.py"], "env_allow": ["DISCORD_TOKEN"]}},
                 {"channel": "discord", "describe": {"argv": ["python", "describe.py"]},
                  "handler": {"argv": ["python", "handler.py"], "pythonpath": ["../.."]}}):
        with pytest.raises(cb.BindingError):
            cb.validate_declaration(decl, root)


def test_an_open_chat_cannot_be_claimed(sandbox):
    s = Path(os.environ["ADAPTER_BRIDGES_DIR"]) / "discord" / "settings.json"
    s.write_text(json.dumps({"whitelist": ["owner1"], "chat_profiles": {CHAT: {"audience": "all"}}}))
    with pytest.raises(cb.BindingError):
        cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    s.write_text(json.dumps({"whitelist": ["owner1"], "chat_profiles": {CHAT: {"observer_visibility": "transcript"}}}))
    with pytest.raises(cb.BindingError):
        cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)


def test_failures_suspend_and_notices_are_rate_limited(sandbox):
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    for _ in range(cb.MAX_FAILURES - 1):
        assert cb.record_result(TENANT, b["binding_id"], False)["status"] == "active"
    assert cb.record_result(TENANT, b["binding_id"], False)["status"] == "suspended"
    assert "channel_binding.suspended" in chain_events()
    assert cb.take_notice_slot(TENANT, b["binding_id"]) is True
    assert cb.take_notice_slot(TENANT, b["binding_id"]) is False


def test_disable_then_enable_restores_the_previous_status(sandbox):
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    cb.mark_was(TENANT, "plug")
    cb.suspend(TENANT, plugin_id="plug", reason="plugin_disabled")
    assert cb.resolve(TENANT, "discord", CHAT)["status"] == "suspended"
    cb.resume(TENANT, plugin_id="plug")
    assert cb.resolve(TENANT, "discord", CHAT)["status"] == b["status"] == "active"
    cb.release(TENANT, plugin_id="plug")
    assert cb.resolve(TENANT, "discord", CHAT) is None and "channel_binding.released" in chain_events()


def test_a_change_is_not_made_when_its_audit_record_cannot_be_written(sandbox):
    def broken(event, details):
        raise OSError("chain not writable")
    with pytest.raises(cb.BindingError):
        cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT, audit=broken)
    assert cb.resolve(TENANT, "discord", CHAT) is None


def test_sender_token_is_salted_per_tenant(sandbox):
    t1, t2 = cb.sender_token(TENANT, "owner1"), cb.sender_token(TENANT, "owner1")
    assert t1 == t2 and len(t1) == 16 and t1 != hashlib.sha256(b"owner1").hexdigest()[:16]
    (Path(os.environ["CORVIN_HOME"]) / "tenants" / "other" / "global").mkdir(parents=True)
    assert cb.sender_token("other", "owner1") != t1


def test_the_audit_chain_still_verifies(sandbox):
    cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    from forge.security_events import verify_chain
    from core.paths.tenant import tenant_audit_chain
    res = verify_chain(tenant_audit_chain(TENANT))
    ok = res[0] if isinstance(res, tuple) else getattr(res, "ok", res)
    assert ok, res


def test_the_handler_gets_a_cleared_environment_and_cannot_choose_the_target(sandbox, monkeypatch):
    root = plugin_dir(sandbox)
    (root / "handler.py").write_text(
        "import os, sys, json; sys.stdin.read(); "
        "print(json.dumps({'text': ','.join(sorted(os.environ)), 'chat_id': '1'}))")
    monkeypatch.setenv("DISCORD_TOKEN", "do-not-leak")
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(root), chat_id=CHAT)
    ok, reply = cb.call_handler(b, TENANT, text="hello", sender="owner1", msg_id="m1")
    names = set(reply.split(","))
    assert ok and "DISCORD_TOKEN" not in names
    assert names <= {"HOME", "PATH", "PYTHONPATH", "PYTHONIOENCODING", "CORVIN_TENANT_ID", *cb.PROVIDED_ENV, "LC_CTYPE"}


def test_a_suspended_binding_can_be_resumed_on_purpose(sandbox):
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    for _ in range(cb.MAX_FAILURES):
        cb.record_result(TENANT, b["binding_id"], False)
    assert cb.resolve(TENANT, "discord", CHAT)["status"] == "suspended"
    assert cb.reactivate(TENANT, b["binding_id"])["status"] == "active"
    assert cb.reactivate(TENANT, b["binding_id"]) is None                  # nothing suspended any more


@pytest.mark.parametrize("where", ["sessions/discord/{chat}", "sessions/voice/discord/{chat}", "sessions/discord/{chat}-1a2b"])
def test_any_session_directory_counts_as_history(sandbox, where):
    d = Path(os.environ["CORVIN_HOME"]) / "tenants" / TENANT / where.format(chat=CHAT)
    d.mkdir(parents=True)
    assert cb.chat_has_history(TENANT, "discord", CHAT)
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    assert b["status"] == "pending"


def test_credential_like_names_cannot_be_allowed(sandbox):
    for name in ("CORVIN_AUDIT_ANCHOR_KEY", "AWS_ACCESS_KEY_ID", "DATABASE_URL", "SENTRY_DSN", "GPG_PRIVATE"):
        with pytest.raises(cb.BindingError):
            cb.validate_declaration({"channel": "discord", "describe": {"argv": ["python", "describe.py"]},
                                     "handler": {"argv": ["python", "handler.py"], "env_allow": [name]}},
                                    plugin_dir(sandbox))


def test_resume_from_the_chat_only_after_handler_failures(sandbox):
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    cb.mark_was(TENANT, "plug")
    cb.suspend(TENANT, plugin_id="plug", reason="plugin_disabled")
    assert cb.reactivate(TENANT, b["binding_id"]) is None                  # a disabled app does not come back from chat
    assert cb.resolve(TENANT, "discord", CHAT)["status"] == "suspended"


def test_a_chat_opened_after_the_claim_cannot_be_confirmed(sandbox):
    led = Path(os.environ["CORVIN_HOME"]) / "tenants" / TENANT / "session_ledger" / "discord" / CHAT / "ledger.jsonl"
    led.parent.mkdir(parents=True)
    led.write_text('{"kind": "turn"}\n')
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    s = Path(os.environ["ADAPTER_BRIDGES_DIR"]) / "discord" / "settings.json"
    s.write_text(json.dumps({"whitelist": ["owner1"], "chat_profiles": {CHAT: {"audience": "all"}}}))
    with pytest.raises(cb.BindingError):
        cb.confirm(TENANT, b["binding_id"])
    assert cb.resolve(TENANT, "discord", CHAT)["status"] == "pending"


def test_only_discord_can_be_bound_in_this_version(sandbox):
    with pytest.raises(cb.BindingError):
        cb.validate_declaration({"channel": "telegram", "describe": {"argv": ["python", "describe.py"]},
                                 "handler": {"argv": ["python", "handler.py"]}}, plugin_dir(sandbox))


def test_the_voice_sessions_root_counts_as_history(sandbox):
    d = Path(os.environ["CORVIN_HOME"]) / "tenants" / TENANT / "voice" / "sessions" / "discord" / CHAT
    d.mkdir(parents=True)
    assert cb.chat_has_history(TENANT, "discord", CHAT)


def test_a_tenant_other_than_the_process_tenant_can_be_bound(sandbox):
    import inspect
    from forge.security_events import write_event
    if "tenant_id" not in inspect.signature(write_event).parameters:
        pytest.skip("this audit writer cannot tag a record for another tenant yet (only the process tenant)")
    other = "acme"
    (Path(os.environ["CORVIN_HOME"]) / "tenants" / other / "global" / "forge").mkdir(parents=True)
    b = cb.claim(other, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    assert b["status"] == "active" and cb.resolve(other, "discord", CHAT)


def test_the_cli_says_when_the_registry_is_unreadable(sandbox):
    cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    cb.registry_path(TENANT).write_text("not json")
    r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "bindings_cli.py"), "list"],
                       capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert r.returncode == 3 and "unreadable" in r.stderr


def test_transcripts_off_is_closed_and_an_open_default_profile_is_open(sandbox):
    s = Path(os.environ["ADAPTER_BRIDGES_DIR"]) / "discord" / "settings.json"
    s.write_text(json.dumps({"whitelist": ["owner1"], "chat_profiles": {CHAT: {"observer_visibility": "off"}}}))
    b = cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    assert b["status"] == "pending"                                       # configured, so not fresh - but claimable
    cb.release(TENANT, plugin_id="plug")
    s.write_text(json.dumps({"whitelist": ["owner1"], "chat_profiles": {"default": {"audience": "all"}}}))
    with pytest.raises(cb.BindingError):
        cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)


def test_resume_to_pending_is_audited(sandbox):
    led = Path(os.environ["CORVIN_HOME"]) / "tenants" / TENANT / "session_ledger" / "discord" / CHAT / "ledger.jsonl"
    led.parent.mkdir(parents=True)
    led.write_text('{"kind": "turn"}\n')
    cb.claim(TENANT, plugin_id="plug", decl=decl_for(plugin_dir(sandbox)), chat_id=CHAT)
    cb.mark_was(TENANT, "plug")
    cb.suspend(TENANT, plugin_id="plug", reason="plugin_disabled")
    n = chain_events().count("channel_binding.claimed")
    cb.resume(TENANT, plugin_id="plug")
    assert cb.resolve(TENANT, "discord", CHAT)["status"] == "pending"
    assert chain_events().count("channel_binding.claimed") == n + 1


def test_plugin_enabled_reads_both_registry_layouts(sandbox):
    reg = Path(os.environ["CORVIN_HOME"]) / "tenants" / TENANT / "plugins" / "registry.yaml"
    assert cb.plugin_enabled(TENANT, "plug") is True                       # no plugin system: not a reason to drop
    reg.parent.mkdir(parents=True)
    reg.write_text("plugins:\n  plug: {enabled: true}\n")
    assert cb.plugin_enabled(TENANT, "plug") is True
    reg.write_text("spec:\n  plugins:\n    plug: {enabled: true}\n")
    assert cb.plugin_enabled(TENANT, "plug") is True
    reg.write_text("plugins:\n  plug: {enabled: false}\n")
    assert cb.plugin_enabled(TENANT, "plug") is False
    reg.write_text(": not yaml :")
    assert cb.plugin_enabled(TENANT, "plug") is False
