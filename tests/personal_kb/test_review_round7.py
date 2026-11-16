"""Regression tests for the round-7 review findings on the personal knowledge base."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.personal_kb import engine, erasure, model, owner, paths
from core.personal_kb.store import PersonalStore

from .conftest import PW, chain_records

T = "_default"
REPO = Path(__file__).resolve().parents[2]


def _store(handle="anna"):
    c = owner.create_owner(T, handle, PW, consent=True, unlock_mode="convenient")
    return c, PersonalStore.open(T, c.owner_id, c.keys)


def test_r7_file_hub_never_serves_or_writes_the_personal_space(home):
    sys.path.insert(0, str(REPO / "core/console"))
    from corvin_console.routes.files import _access

    for rel in ("personal_kb/.pepper", "personal_kb/_index.json", "personal_kb/abc/owner.json",
                "sessions/discord/c1/.corvin-sysprompt-1234.txt", "personal_kb"):
        assert _access(rel) == "none", rel
    assert _access("files/notes.txt") == "full"        # the user's own zone is untouched


def test_r7_one_shot_refuses_when_a_provider_is_assigned(home, monkeypatch):
    monkeypatch.setattr(engine, "_native_env", lambda tid, p: (_ for _ in ()).throw(engine.Withheld("provider")))
    with pytest.raises(engine.Withheld):
        engine.one_shot("hello", tenant_id=T)


def test_r7_native_env_passes_without_a_provider(home):
    env = engine._native_env(T, "hello")
    assert not env.get("ANTHROPIC_BASE_URL") and not env.get("CORVIN_CC_PROVIDER")


def test_r7_erasure_drops_the_owners_link_attempt_counters(home):
    from core.personal_kb import bridge
    c, s = _store()
    code = owner.issue_pair_code(T, c.owner_id)
    owner.redeem_pair_code(T, code, channel="discord", uid="6161", display="x", chat_type="private")
    owner.confirm_pair(T, s, owner.pending_pairs(T, s)[0]["pair_id"])
    bridge.handle_command(T, "/me link WRONG", channel="discord", uid="7171", display="y", chat_type="private")
    pairs = (paths.root(T) / "_pairs.json")
    import json
    data = json.loads(pairs.read_text())
    data.setdefault("senders", {})[owner.identity_key(T, "discord", "6161")] = [1.0]
    pairs.write_text(json.dumps(data))
    erasure.erase_owner(T, c.owner_id)
    after = json.loads(pairs.read_text())
    assert owner.identity_key(T, "discord", "6161") not in after.get("senders", {})


def test_r7_a_withheld_block_is_recorded_in_the_chain(home):
    from core.personal_kb import bridge
    c, s = _store()
    s.put(model.new_entity("preference", "brief", key="verbosity", origin="user"))
    owner.create_bridge_owner  # noqa: B018 - the owner exists; bind a bridge chat to it below
    code = owner.issue_pair_code(T, c.owner_id)
    owner.redeem_pair_code(T, code, channel="telegram", uid="8080", display="z", chat_type="private")
    owner.confirm_pair(T, s, owner.pending_pairs(T, s)[0]["pair_id"])
    assert bridge.block_owner_hash(T, channel="telegram", uid="8080") == s.owner_hash
    bridge.note_withheld(T, s.owner_hash, "telegram")
    recs = [r for r in chain_records(home) if r.get("event_type") == "personal_kb.context_withheld"]
    assert recs, [r.get("event_type") for r in chain_records(home)][-5:]
    assert s.owner_hash in str(recs[-1])


def test_r8_erasure_drops_counters_of_unconfirmed_link_requests(home):
    import json
    c, s = _store()
    code = owner.issue_pair_code(T, c.owner_id)
    owner.redeem_pair_code(T, code, channel="telegram", uid="9191", display="q", chat_type="private")
    pairs = paths.root(T) / "_pairs.json"
    data = json.loads(pairs.read_text())
    skey = owner.identity_key(T, "telegram", "9191")
    data.setdefault("senders", {})[skey] = [1.0]
    pairs.write_text(json.dumps(data))
    erasure.erase_owner(T, c.owner_id)
    after = json.loads(pairs.read_text()) if pairs.exists() else {}
    assert skey not in after.get("senders", {})
    assert not any(v.get("owner_id") == c.owner_id for v in after.get("codes", {}).values())


def test_r10_a_lone_surrogate_is_refused_before_it_is_stored(home):
    from core.personal_kb import model as m
    c, s = _store()
    with pytest.raises(m.ValidationError):
        s.put(m.new_entity("preference", "I prefer \ud83d short answers", key="verbosity", origin="user"))
    assert s.list() == []


def test_r15_odd_separator_still_runs_the_command(home):
    from core.personal_kb import bridge
    kw = dict(channel="telegram", uid="5151", display="x", chat_type="private")
    bridge.handle_command(T, "/me on yes", **kw)
    r = bridge.handle_command(T, "/me\ufeffremember I am allergic to nuts", **kw)
    assert r.handled and "Remembered" in r.reply


def test_r16_idea_colon_triggers_capture(home):
    from core.personal_kb import capture
    assert capture.triggered("idea: a dog calendar")
    assert capture.triggered("Idee: ein Kalender für Hunde")
    assert not capture.triggered("nothing to see here")


def test_r16_one_shot_sends_only_the_guarded_prompt(home, monkeypatch):
    import subprocess
    from core.personal_kb import engine
    sent = {}

    class _Done:
        returncode = 0
        stdout = '{"ok": true}'
    def fake_run(args, input=None, **kw):
        sent["input"] = input
        return _Done()
    monkeypatch.setattr(engine, "_claude_bin", lambda: "/bin/true")
    monkeypatch.setattr(engine, "_native_env", lambda tid, p: {})
    monkeypatch.setattr(subprocess, "run", fake_run)
    engine.one_shot("I always use @/etc/hostname here", tenant_id=T)
    assert sent["input"] and " @/etc/hostname" not in sent["input"]   # the guard neutralises the bare @path


def test_r17_teams_space_is_erased_by_the_upn_too(home):
    from core.personal_kb import bridge
    kw = dict(channel="teams", uid="aad-6f1c", display="Alice", chat_type="private")
    bridge.handle_command(T, "/me on yes", **kw)
    bridge.note_alias(T, channel="teams", uid="aad-6f1c", alias="alice@contoso.com")
    oid = owner.resolve_owner(T, channel="teams", uid="aad-6f1c")
    assert oid and owner.owners_for_bare_uid(T, "alice@contoso.com") == [oid]
    erasure.erase_owner(T, oid)
    assert owner.resolve_owner(T, channel="teams", uid="alice@contoso.com") is None
    assert not paths.owner_dir(T, oid).exists()


def test_r18_unlinking_a_teams_account_removes_its_upn_alias(home):
    from core.personal_kb import bridge
    c, s = _store()
    code = owner.issue_pair_code(T, c.owner_id)
    owner.redeem_pair_code(T, code, channel="teams", uid="aad-9", display="Bob", chat_type="private")
    owner.confirm_pair(T, s, owner.pending_pairs(T, s)[0]["pair_id"])
    bridge.note_alias(T, channel="teams", uid="aad-9", alias="Bob@Corp.com")
    assert erasure.owners_for_subject(T, "teams:BOB@corp.com") == [c.owner_id]
    assert owner.unbind(T, s, "teams", "aad-9")
    assert erasure.owners_for_subject(T, "teams:bob@corp.com") == []
    assert erasure.owners_for_subject(T, "bob@corp.com") == []


def test_r19_a_stale_upn_alias_never_erases_the_new_holders_space_or_the_old_one(home):
    from core.personal_kb import bridge
    a_c, a_s = _store("ana")
    code = owner.issue_pair_code(T, a_c.owner_id)
    owner.redeem_pair_code(T, code, channel="teams", uid="aad-A", display="A", chat_type="private")
    owner.confirm_pair(T, a_s, owner.pending_pairs(T, a_s)[0]["pair_id"])
    bridge.note_alias(T, channel="teams", uid="aad-A", alias="shared@corp.com")
    assert erasure.owners_for_subject(T, "teams:shared@corp.com") == [a_c.owner_id]
    owner.unbind(T, a_s, "teams", "aad-A")                  # A's account is unlinked: the alias is stale
    assert erasure.owners_for_subject(T, "teams:shared@corp.com") == []
    b_c, b_s = _store("bert")
    code = owner.issue_pair_code(T, b_c.owner_id)
    owner.redeem_pair_code(T, code, channel="teams", uid="aad-B", display="B", chat_type="private")
    owner.confirm_pair(T, b_s, owner.pending_pairs(T, b_s)[0]["pair_id"])
    bridge.note_alias(T, channel="teams", uid="aad-B", alias="shared@corp.com")   # the UPN is reassigned to B
    assert erasure.owners_for_subject(T, "teams:shared@corp.com") == [b_c.owner_id]
    assert erasure.owners_for_subject(T, "teams:shared@corp.com") != [a_c.owner_id]


def test_r19_an_alias_for_an_unlinked_account_is_refused(home):
    from core.personal_kb import bridge
    c, s = _store("cora")
    code = owner.issue_pair_code(T, c.owner_id)
    owner.redeem_pair_code(T, code, channel="teams", uid="aad-C", display="C", chat_type="private")
    owner.confirm_pair(T, s, owner.pending_pairs(T, s)[0]["pair_id"])
    owner.unbind(T, s, "teams", "aad-C")
    bridge.note_alias(T, channel="teams", uid="aad-C", alias="late@corp.com")   # the race: a late message
    assert erasure.owners_for_subject(T, "teams:late@corp.com") == []
