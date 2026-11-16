"""Bridge facade, pairing and erasure (AC-O7, O8, O11, O12, O14, O15, O18, AC-C12, AC-S5 /me part)."""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from core.personal_kb import bridge, erasure, model, owner, paths, session
from core.personal_kb.store import PersonalStore, Revoked

from .conftest import PW, chain_records, verify_chain

T = "_default"


def me(text, uid="111222333", chat_type="private", channel="discord", **kw):
    return bridge.handle_command(T, text, channel=channel, uid=uid, display="Anna", chat_type=chat_type, **kw)


def test_me_on_requires_consent_and_private_chat(home):
    assert "private chat" in me("/me on yes", chat_type="group").reply
    assert "/personal on yes" in me("/me on").reply
    assert "ready" in me("/me on yes").reply
    assert "already exists" in me("/me on yes").reply
    assert me("/me status").reply.startswith("Entries: 0")
    r = me("/me idea A shared shopping list for the flat")
    assert "IDEA-0001" in r.reply
    assert "Remembered" in me("/me remember I work late on Fridays").reply
    assert "Entries: 2" in me("/me status").reply
    assert "Forgot" in me("/me forget IDEA-0001").reply


def test_unknown_channels_and_ids_cannot_bind(home):
    for ch, uid in (("email", "a@b.example"), ("whatsapp", "4917@s.whatsapp.net"), ("discord", "unknown"),
                    ("discord", "")):
        r = bridge.handle_command(T, "/me on yes", channel=ch, uid=uid, display="x", chat_type="private")
        assert "cannot be linked" in r.reply or "private" in r.reply


def test_context_block_only_private_and_bound(home, monkeypatch):
    from core.personal_kb import engine
    monkeypatch.setattr(engine, "l34_allows", lambda t, x, **k: True)
    me("/me on yes")
    me("/me remember I prefer short answers")
    assert bridge.context_block(T, channel="discord", uid="111222333", chat_type="private", task_text="x")
    for ct in ("group", "unknown", ""):
        assert bridge.context_block(T, channel="discord", uid="111222333", chat_type=ct, task_text="x") == ""
    assert bridge.context_block(T, channel="discord", uid="999", chat_type="private", task_text="x") == ""


def test_strict_owner_is_console_only(home):
    c = owner.create_owner(T, "carla", PW, consent=True, unlock_mode="strict")
    with pytest.raises(owner.OwnerError, match="console only"):
        owner.issue_pair_code(T, c.owner_id)
    # even a binding planted by hand opens nothing: strict has no device wrap
    from core.personal_kb import locks as _l
    with _l.exclusive(paths.tenant_lock_file(T)):
        owner._bind_index(T, "discord", "555", c.owner_id)
    assert bridge.context_block(T, channel="discord", uid="555", chat_type="private", task_text="x") == ""
    assert "did not work" in me("/me status", uid="555").reply or "not available" in me("/me status", uid="555").reply


def test_pairing_flow_and_hijack_attempts(home):
    c = owner.create_owner(T, "anna", PW, consent=True)
    s = PersonalStore.open(T, c.owner_id, c.keys)
    code = owner.issue_pair_code(T, c.owner_id)
    # a code spoken in a group is burned
    r = me(f"/me link {code}", uid="999", chat_type="group")
    assert "no longer valid" in r.reply
    assert "not valid" in me(f"/me link {code}", uid="999").reply
    code = owner.issue_pair_code(T, c.owner_id)
    assert "confirm" in me(f"/me link {code}", uid="111222333", chat_key="dm-1").reply
    pend = owner.pending_pairs(T, s)
    assert pend and pend[0]["masked"] == "…2333" and "111222333" not in json.dumps(pend)
    # nothing is bound before Confirm
    assert owner.resolve_owner(T, channel="discord", uid="111222333") is None
    owner.confirm_pair(T, s, pend[0]["pair_id"])
    assert owner.resolve_owner(T, channel="discord", uid="111222333") == c.owner_id
    # an already bound identity cannot be bound again
    code2 = owner.issue_pair_code(T, c.owner_id)
    assert "already linked" in me(f"/me link {code2}", uid="111222333").reply
    # pairing never touched the passphrase
    oid, _ = owner.unlock_with_passphrase(T, "anna", PW, session_key="z")
    assert oid == c.owner_id
    # per-sender limit
    for _ in range(6):
        r = me("/me link WRONGCODE", uid="77777")
    assert "too many" in r.reply
    # raw uids never on disk outside the encrypted meta
    raw = b"".join(p.read_bytes() for p in paths.root(T).rglob("*") if p.is_file())
    assert b"111222333" not in raw  # _pairs.json included: the pending identity is sealed to the owner


def test_me_erase_only_for_bridge_only_owner(home):
    c = owner.create_owner(T, "anna", PW, consent=True)
    s = PersonalStore.open(T, c.owner_id, c.keys)
    code = owner.issue_pair_code(T, c.owner_id)
    me(f"/me link {code}")
    owner.confirm_pair(T, s, owner.pending_pairs(T, s)[0]["pair_id"])
    assert "console" in me("/me erase " + bridge.ERASE_PHRASE).reply
    assert paths.owner_dir(T, c.owner_id).exists()
    me("/me on yes", uid="424242")
    assert "confirm" in me("/me erase", uid="424242").reply
    assert "erased" in me("/me erase " + bridge.ERASE_PHRASE, uid="424242").reply
    assert owner.resolve_owner(T, channel="discord", uid="424242") is None


def _orchestrate(subject):
    from erasure_handlers import real_handler_chain  # type: ignore[import-not-found]

    out = []
    for h in real_handler_chain(tenant_id=T):
        if h.layer_id == "L-personal-kb":
            out.append(h.purge(subject, "req-1"))
    return out


def test_generic_erasure_forms(home):
    me("/me on yes", uid="4815162342001")
    me("/me on yes", uid="4815162342002")
    o1 = owner.resolve_owner(T, channel="discord", uid="4815162342001")
    o2 = owner.resolve_owner(T, channel="discord", uid="4815162342002")
    c3 = owner.create_owner(T, "anna", PW, consent=True)
    # forms the orchestrator path must refuse
    for subject in (c3.owner_id, "anna", "pkb:" + __import__("core.personal_kb.audit", fromlist=["x"]).owner_hash(c3.owner_id)):
        res = _orchestrate(subject)
        assert res[0].status.value == "skipped"
    assert paths.owner_dir(T, c3.owner_id).exists()
    # channel:uid and bare uid both work (ids long enough never to appear by chance in a timestamp or hash)
    assert _orchestrate("discord:4815162342001")[0].status.value == "applied"
    assert _orchestrate("4815162342002")[0].status.value == "applied"
    for o in (o1, o2):
        assert not paths.owner_dir(T, o).exists() and not paths.device_key_files(T, o)
    assert verify_chain(home)
    for r in chain_records(home):
        d = json.dumps(r)
        if r.get("event_type", "").startswith(("personal_kb.", "erasure.personal")):
            assert o1 not in d and "4815162342001" not in d and "anna" not in d


def test_entry_point_erasure_complete_and_audit_clean(home):
    c = owner.create_owner(T, "anna", PW, consent=True)
    s = PersonalStore.open(T, c.owner_id, c.keys)
    s.put(model.new_entity("note", "to be erased"))
    code = owner.issue_pair_code(T, c.owner_id)
    me(f"/me link {code}", uid="31337")
    owner.confirm_pair(T, s, owner.pending_pairs(T, s)[0]["pair_id"])
    # fail a few unlocks so a handle counter exists
    with pytest.raises(owner.UnlockFailed):
        owner.unlock_with_passphrase(T, "anna", "Wrong-Passphrase-123x", session_key="w")
    assert any(r.get("owner_id") == c.owner_id for r in [owner.read_owner(T, c.owner_id)])  # positive control
    n = erasure.erase_owner(T, c.owner_id)
    assert n == 1
    assert not paths.owner_dir(T, c.owner_id).exists()
    assert not paths.device_key_files(T, c.owner_id)
    assert owner.resolve_owner(T, channel="discord", uid="31337") is None
    lock = json.loads(paths.lockouts_file(T).read_text()) if paths.lockouts_file(T).exists() else {}
    assert owner.handle_key(T, "anna") not in lock
    for r in chain_records(home):
        d = json.dumps(r)
        assert c.owner_id not in d and "31337" not in d and '"anna"' not in d
    with pytest.raises(Revoked):
        s.put(model.new_entity("note", "after erasure"))
    assert not paths.owner_dir(T, c.owner_id).exists()  # the writer did not recreate it


def test_crashed_erasure_is_finished_by_resume(home, monkeypatch):
    c = owner.create_owner(T, "anna", PW, consent=True)
    with monkeypatch.context() as mp:
        mp.setattr(erasure, "_finish", lambda *a, **k: (_ for _ in ()).throw(SystemExit("crash")))
        with pytest.raises(SystemExit):
            erasure.erase_owner(T, c.owner_id)
    assert paths.owner_dir(T, c.owner_id).exists()
    assert erasure.resume_pending(T, min_age_s=0) == 1  # the scan finishes it (also run by every store.open)
    assert not paths.owner_dir(T, c.owner_id).exists()
    assert not list(paths.device_key_dir(T).glob(f"{c.owner_id}*"))
    with pytest.raises(Revoked):
        PersonalStore.open(T, c.owner_id, c.keys)


def _holder(home_dir, owner_id, dek_hex, q):
    os.environ["CORVIN_HOME"] = home_dir
    import time
    from core.personal_kb import model as m
    from core.personal_kb.store import Keyring, PersonalStore as PS, Revoked as Rv
    s = PS.open("_default", owner_id, Keyring(epoch=1, dek=bytes.fromhex(dek_hex)))
    q.put("open")
    for _ in range(200):
        time.sleep(0.05)
        try:
            s.put(m.new_entity("note", "late write"))
        except Rv:
            q.put("revoked")
            return
        except Exception as exc:  # noqa: BLE001
            q.put("other:" + type(exc).__name__)
            return
    q.put("never-revoked")


def test_other_process_with_dek_is_revoked(home, monkeypatch):
    monkeypatch.delenv("CORVIN_AUDIT_ANCHOR_KEY", raising=False)
    c = owner.create_owner(T, "anna", PW, consent=True)
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_holder, args=(str(home), c.owner_id, c.keys.dek.hex(), q))
    p.start()
    assert q.get(timeout=60) == "open"
    erasure.erase_owner(T, c.owner_id)
    res = q.get(timeout=60)
    p.join(30)
    assert res in ("revoked", "other:FileNotFoundError")
    assert not paths.owner_dir(T, c.owner_id).exists()


def test_migration_of_legacy_user_model(home):
    import sys
    from core.personal_kb import migrate_user_model as mig

    um = mig._user_model_module()
    m = um.UserModel.empty("discord", "dm-1")
    m.preferences = ["likes short answers", "has a doctor appointment for depression"]
    m.communication_style = "direct"
    um.save(m, tenant_id=T)
    path = mig.legacy_path("discord", "dm-1", T)
    assert path.exists()
    c = owner.create_owner(T, "anna", PW, consent=True)
    s = PersonalStore.open(T, c.owner_id, c.keys)
    n = mig.migrate(s, channel="discord", chat_key="dm-1")
    assert n == 2 and not path.exists()
    notes = s.list(type="note")
    assert all(e["origin"] == "derived" and e["review"] == "pending" and e["expires_at"] for e in notes)
    assert not any("depression" in e["title"] for e in notes)
