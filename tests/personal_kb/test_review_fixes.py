"""Regression tests for the code-review findings on the personal knowledge base (round 1)."""
from __future__ import annotations

import json

import pytest

from core.personal_kb import bridge, capture, cli, crypto, decoder, erasure, model, owner, paths
from core.personal_kb.store import PersonalStore

from .conftest import PW, chain_records

T = "_default"


def _store(handle="anna", mode="convenient"):
    c = owner.create_owner(T, handle, PW, consent=True, unlock_mode=mode)
    return c, PersonalStore.open(T, c.owner_id, c.keys)


def test_c01_capture_never_overwrites_an_owner_entry(home):
    _, s = _store()
    mine = s.put(model.new_entity("context", "My own careful role description", key="role", origin="user"))
    item = {"type": "context", "key": "role", "value": "developer", "confidence": 0.9, "sensitive": False,
            "about_other_person": False, "uncertain": False}
    r = capture.capture_turn(s, "i am a developer", extractor=lambda t: {"items": [item]}, now=1000)
    assert r.stored == []
    e = s.get(mine["uid"])
    assert (e["title"], e["origin"], e["expires_at"]) == ("My own careful role description", "user", None)


def test_c02_unlinking_the_only_way_in_is_refused(home):
    me = lambda t, uid="4242": bridge.handle_command(T, t, channel="telegram", uid=uid, display="x", chat_type="private")  # noqa: E731
    me("/me on yes")
    assert "lock you out" in me("/me unlink").reply
    assert owner.resolve_owner(T, channel="telegram", uid="4242")


def test_c05_pending_pair_identity_is_sealed_and_purgeable(home):
    c, s = _store()
    code = owner.issue_pair_code(T, c.owner_id)
    owner.redeem_pair_code(T, code, channel="discord", uid="987654321", display="Anna", chat_type="private")
    raw = (paths.root(T) / "_pairs.json").read_bytes()
    assert b"987654321" not in raw and b"Anna" not in raw
    assert owner.pending_pairs(T, s)[0]["display"] == "Anna"
    assert owner.purge_pairs_for(T, channel="discord", uid="987654321") >= 1
    assert owner.pending_pairs(T, s) == []


def test_c06_object_ahead_of_manifest_is_repaired(home, monkeypatch):
    from core.personal_kb import store as st

    c, s = _store()
    e = s.put(model.new_entity("note", "one"))
    with monkeypatch.context() as mp:
        mp.setattr(st.PersonalStore, "_write_manifest", lambda self: (_ for _ in ()).throw(OSError("crash")))
        with pytest.raises(OSError):
            s.put(dict(e, title="two"))
    s2 = PersonalStore.open(T, c.owner_id, c.keys)
    assert s2.problems == {} and s2.get(e["uid"])["title"] == "two" and s2.get(e["uid"])["version"] == 2
    assert s2.versions(e["uid"]) == [1]
    assert s2.restore(e["uid"], 1)["title"] == "one"
    assert PersonalStore.open(T, c.owner_id, c.keys).problems == {}


def test_c10_dismissing_an_old_objection_does_not_touch_the_current_stage(home):
    _, s = _store()
    idea = s.put(model.new_entity("idea", "Balcony herbs", origin="user"))
    good = {"shape": {"goal": "g", "boundaries": ["b"], "why_now": "w", "success": "s"},
            "choices": {"meaning": "m", "reach": "r", "doing": "d",
                        "decisions": [{"question": "q", "alternatives": ["a", "b"], "chosen": "a"}]}}
    objs = {"objections": [{"category": "risk", "text": "x"}]}

    def g(stage, prompt):
        return json.loads(json.dumps(objs if stage == "adversarial" else good[stage]))
    decoder.unfold(s, idea["uid"], generate=g, now=1000)
    r2 = decoder.unfold(s, idea["uid"], generate=g, now=1000 + 86401)
    before = decoder._state(s.get(idea["uid"]))["loss"]
    decoder.resolve_objection(s, idea["uid"], 0, "dismiss", reason="ok")  # the SHAPE objection
    after = decoder._state(s.get(idea["uid"]))["loss"]
    assert r2.stage == "choices" and before == after


def test_c11_rekey_keeps_bridge_unlock_working_across_the_switch(home):
    c, s = _store()
    new, _ = owner.rekey(T, c.owner_id, c.keys, PW)
    files = paths.device_key_files(T, c.owner_id)
    assert [f.name for f in files] == [f"{c.owner_id}.e2.key"]
    assert owner.unlock_with_device(T, c.owner_id, surface="t").dek == new.dek


def test_c24_resume_finishes_when_the_directory_is_already_gone(home, monkeypatch):
    c, _ = _store()
    me = lambda t: bridge.handle_command(T, t, channel="discord", uid="777", display="", chat_type="private")  # noqa: E731
    code = owner.issue_pair_code(T, c.owner_id)
    me(f"/me link {code}")
    s = PersonalStore.open(T, c.owner_id, c.keys)
    owner.confirm_pair(T, s, owner.pending_pairs(T, s)[0]["pair_id"])
    real_index = owner._index

    calls = {"n": 0}

    def boom(tid):
        calls["n"] += 1
        raise SystemExit("crash after the directory went")
    with monkeypatch.context() as mp:
        mp.setattr(owner, "_index", boom)
        with pytest.raises(SystemExit):
            erasure.erase_owner(T, c.owner_id)
    assert not paths.owner_dir(T, c.owner_id).exists()
    assert real_index(T)["bindings"]  # leftover binding
    assert erasure.resume_pending(T, min_age_s=0) == 1
    assert not real_index(T)["bindings"]
    assert any(r.get("event_type") == "erasure.personal_kb_deleted" for r in chain_records(home))


@pytest.mark.parametrize("pw", ["passwordpassword", "abcabcabcabcabc", "Summer2026Summer2026"])
def test_c26_repeated_passphrases_refused(pw):
    assert crypto.passphrase_problem(pw)


def test_c22_operator_cli_erases_by_handle(home, capsys):
    c, _ = _store()
    assert cli.main(["count"]) == 0 and capsys.readouterr().out.strip() == "1"
    assert cli.main(["erase", "--handle", "anna"]) == 0
    assert not paths.owner_dir(T, c.owner_id).exists()
    assert cli.main(["erase", "--handle", "anna"]) == 2


def test_c20_withheld_block_is_audited(home):
    _, s = _store()
    from core.personal_kb import render
    s.put(model.new_entity("preference", "brief", key="verbosity", origin="auto"))
    assert render.block(s, "x", surface="discord", gate=lambda t, x: False) == ""
    assert any(r.get("event_type") == "personal_kb.context_withheld" for r in chain_records(home))


def test_d14_sweep_skips_an_unreadable_object_and_finishes(home):
    c, s = _store()
    good = s.put(model.new_entity("note", "readable"))
    bad = s.put(model.new_entity("note", "will be damaged"))
    p = paths.object_file(T, c.owner_id, s.manifest["objects"][bad["uid"]]["oid"])
    blob = bytearray(p.read_bytes())
    blob[-1] ^= 0x01
    p.write_bytes(bytes(blob))
    nk, _rc = owner.rekey(T, c.owner_id, c.keys, PW)
    s2 = PersonalStore.open(T, c.owner_id, nk)
    assert s2.get(good["uid"])["title"] == "readable"
    assert s2.problems.get(bad["uid"]) == "corrupt"


def test_d14_capture_drops_its_write_when_the_owner_changed_the_entry_meanwhile(home, monkeypatch):
    _, s = _store()
    item = {"type": "preference", "key": "verbosity", "value": "brief", "confidence": 0.9, "sensitive": False,
            "about_other_person": False, "uncertain": False}
    first = capture.capture_turn(s, "i prefer brief answers", extractor=lambda t: {"items": [item]}, now=1000)
    assert first.stored
    uid = first.stored[0]
    real_put = s.put

    def racing_put(ent, **kw):
        # the owner corrects the entry between capture's read and its write
        if kw.get("expected_version") is not None and ent.get("origin") == "auto":
            cur = s.get(uid)
            real_put(dict(cur, title="owner wrote this", origin="user", review="accepted"))
        return real_put(ent, **kw)
    monkeypatch.setattr(s, "put", racing_put)
    item2 = dict(item, value="detailed")
    capture.capture_turn(s, "i prefer detailed answers", extractor=lambda t: {"items": [item2]}, now=1000 + 7200)
    assert s.get(uid)["title"] == "owner wrote this"


# ---- review round 3 ------------------------------------------------------------------------------

def test_r3_redo_keeps_tasks_the_owner_already_moved(home):
    from .test_learning import GOOD, gen_from

    _, s = _store()
    idea = s.put(model.new_entity("idea", "Herb garden on the balcony", origin="user"))
    g = gen_from(GOOD)
    for _st in ("shape", "choices", "plan"):
        decoder.unfold(s, idea["uid"], generate=g, now=1000)
    t1, t2 = s.list(type="task")
    s.put(model.transition(t1, "in_progress"))
    decoder.unfold(s, idea["uid"], stage="plan", generate=g, now=2000)
    assert s.get(t1["uid"]) is not None and s.get(t1["uid"])["status"] == "in_progress"
    assert s.get(t2["uid"]) is None                    # untouched draft replaced
    assert len(s.list(type="task")) == 3                # kept one + two new drafts


def test_r3_unreadable_entry_can_be_removed_and_rekey_finishes(home):
    c, s = _store()
    keep = s.put(model.new_entity("note", "fine"))
    gone = s.put(model.new_entity("note", "file lost"))
    paths.object_file(T, c.owner_id, s.manifest["objects"][gone["uid"]]["oid"]).unlink()
    s2 = PersonalStore.open(T, c.owner_id, c.keys)
    assert s2.problems == {gone["uid"]: "missing"}
    assert s2.drop_unreadable() == 1
    s3 = PersonalStore.open(T, c.owner_id, c.keys)
    assert s3.problems == {} and s3.get(keep["uid"])["title"] == "fine"
    nk, _ = owner.rekey(T, c.owner_id, c.keys, PW)
    assert nk.epoch == c.keys.epoch + 1


def test_r3_parallel_unlocks_for_one_handle_run_one_at_a_time(home):
    from core.personal_kb import limits

    c, _ = _store()
    hk = owner.handle_key(T, "anna")
    with limits.handle_attempt(T, hk):
        with pytest.raises(limits.TooSoon):
            owner.unlock_with_passphrase(T, "anna", PW, session_key="s-par")
    sid, keys = owner.unlock_with_passphrase(T, "anna", PW, session_key="s-par")
    assert keys.epoch == c.keys.epoch


def test_r3_wrong_passphrase_in_change_and_rekey_is_slowed_and_audited(home):
    from core.personal_kb import limits

    c, _ = _store()
    with pytest.raises(owner.UnlockFailed):
        owner.change_passphrase(T, c.owner_id, "Wrong-Passphrase-1234", "Another-Long-Pass-99x", session_key="s-x")
    with pytest.raises(limits.TooSoon):            # the handle delay now applies
        owner.rekey(T, c.owner_id, c.keys, "Wrong-Passphrase-1234", session_key="s-y")
    names = [r.get("event_type") for r in chain_records(home)]
    assert any(n == "personal_kb.unlock_failed" for n in names)


def test_r3_policy_refusal_never_audits_a_request_key(home):
    from core.personal_kb import policy

    _, s = _store()
    with pytest.raises(policy.PolicyRefused):
        policy.owner_edit(s, {"my secret note about Bob": 1})
    recs = [r for r in chain_records(home) if "policy_refused" in json.dumps(r)]
    assert recs and "Bob" not in json.dumps(recs) and "unknown" in json.dumps(recs[-1])


def test_r3_extractor_variant_reaches_the_extraction_prompt(home, monkeypatch):
    from core.personal_kb import engine, policy

    seen = []
    monkeypatch.setattr(engine, "one_shot", lambda prompt, **kw: seen.append(prompt) or '{"items": []}')
    _, s = _store()
    s.update_meta(lambda m: m.__setitem__("auto_capture", True))
    policy.owner_edit(s, {"extractor_variant": "v0_strict"})
    capture.capture_turn(s, "I prefer short answers", now=5000)
    assert seen and capture.VARIANT_RULES["v0_strict"].strip() in seen[-1]


def test_r3_decoder_drafts_do_not_feed_the_brake(home):
    _, s = _store()
    d = s.put(model.new_entity("note", "Shape: x", origin="derived",
                               links=[{"rel": "derived_from", "to": "legacy:x"}]))
    assert d["review"] == "none"
    capture.review(s, d["uid"], "forget")
    assert not [e for e in s.meta.get("review_log", []) if e.get("auto")]


def test_r3_legacy_import_drops_other_people_and_hedged_items(home):
    from core.personal_kb import migrate_user_model as mig

    um = mig._user_model_module()
    m = um.UserModel.empty("discord", "dm-9")
    m.preferences = ["likes short answers", "my wife prefers mornings", "maybe likes jazz"]
    um.save(m, tenant_id=T)
    _, s = _store()
    assert mig.migrate(s, channel="discord", chat_key="dm-9") == 1
    assert [e["title"] for e in s.list(type="note")] == ["Preferences: likes short answers"]


# ---- review round 4 ------------------------------------------------------------------------------

def test_r4_rekey_switches_sessions_in_the_lock_and_can_defer_the_sweep(home):
    from core.personal_kb import session

    c, s = _store()
    for i in range(5):
        s.put(model.new_entity("note", f"n{i}"))
    seen = []
    nk, code = owner.rekey(T, c.owner_id, c.keys, PW, sweep=False, on_switched=lambda k: seen.append(k.epoch))
    assert code and seen == [nk.epoch]
    assert owner.read_owner(T, c.owner_id).get("prev")          # sweep still pending
    assert owner.resume_rekey_if_pending(T, c.owner_id, nk)
    assert not owner.read_owner(T, c.owner_id).get("prev")
    assert len(PersonalStore.open(T, c.owner_id, nk).index) == 5
    assert session is not None


def test_r4_sweep_reads_every_file_once(home, monkeypatch):
    from pathlib import Path

    c, s = _store()
    for i in range(30):
        s.put(model.new_entity("note", f"n{i}"))
    nk, _ = owner.rekey(T, c.owner_id, c.keys, PW, sweep=False)
    s2 = PersonalStore.open(T, c.owner_id, nk, expire=False)
    reads = []
    real = Path.read_bytes

    def counting(self):
        if "/objects/" in str(self):
            reads.append(str(self))
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", counting)
    s2.sweep_epoch(batch=7)
    assert len(reads) == len(set(reads)) == 30


def test_r4_concurrent_decoder_step_on_one_idea_is_refused_and_edit_is_kept(home):
    from .test_learning import GOOD, gen_from

    _, s = _store()
    idea = s.put(model.new_entity("idea", "Herb garden on the balcony", origin="user"))
    g = gen_from(GOOD)

    def editing_gen(stage, prompt):
        if stage == "shape":
            with pytest.raises(decoder.DecoderError):      # a second step on the same idea is refused
                decoder.unfold(s, idea["uid"], generate=g, now=1000)
            cur = s.get(idea["uid"])
            s.put(dict(cur, title="Owner renamed it"))      # the owner edits the idea meanwhile
        return g(stage, prompt)
    with pytest.raises(decoder.DecoderError):
        decoder.unfold(s, idea["uid"], generate=editing_gen, now=1000)
    after = s.get(idea["uid"])
    assert after["title"] == "Owner renamed it" and not after.get("decoder")
    assert s.list(type="note") == []                         # this step's drafts were dropped


def test_r4_forget_outside_recently_learned_feeds_the_brake(home):
    _, s = _store()
    e = s.put(model.new_entity("preference", "brief", key="verbosity", origin="auto"))
    s.forget(e["uid"])
    capture.note_owner_action(s, e, "forget")
    assert [x for x in s.meta.get("review_log", []) if x.get("auto")]


def test_r4_pair_record_holds_no_channel_or_masked_id(home):
    c, s = _store()
    code = owner.issue_pair_code(T, c.owner_id)
    owner.redeem_pair_code(T, code, channel="telegram", uid="5550001234", display="Anna", chat_type="private")
    raw = (paths.root(T) / "_pairs.json").read_text()
    assert "telegram" not in raw and "1234" not in raw and "masked" not in raw
    pend = owner.pending_pairs(T, s)
    assert pend[0]["channel"] == "telegram" and pend[0]["masked"].endswith("1234")


def test_r4_observer_link_code_is_burned(home):
    c, s = _store()
    code = owner.issue_pair_code(T, c.owner_id)
    r = bridge.handle_command(T, f"/me link {code}", channel="discord", uid="777", display="x", chat_type="group")
    assert "no longer valid" in r.reply
    with pytest.raises(owner.OwnerError):
        owner.redeem_pair_code(T, code, channel="discord", uid="777", display="x", chat_type="private")


# ---- review round 5 ------------------------------------------------------------------------------

def test_r5_edit_during_a_pending_sweep_keeps_every_version_readable(home):
    c, s = _store()
    e = s.put(model.new_entity("note", "note B"))
    nk, _ = owner.rekey(T, c.owner_id, c.keys, PW, sweep=False)     # sweep not run yet
    s2 = PersonalStore.open(T, c.owner_id, nk, expire=False)
    s2.put(dict(s2.get(e["uid"]), title="note B edited"))            # version 1 copied from an old-key file
    assert owner.resume_rekey_if_pending(T, c.owner_id, nk)          # prev destroyed
    s3 = PersonalStore.open(T, c.owner_id, nk)
    assert s3.problems == {}
    assert s3.restore(e["uid"], 1)["title"] == "note B"


# ---- review round 6 ------------------------------------------------------------------------------

def test_r6_pending_link_survives_a_key_renewal(home):
    c, s = _store()
    code = owner.issue_pair_code(T, c.owner_id)
    owner.redeem_pair_code(T, code, channel="telegram", uid="5550009999", display="Bert", chat_type="private")
    nk, _ = owner.rekey(T, c.owner_id, c.keys, PW)
    s2 = PersonalStore.open(T, c.owner_id, nk)
    pend = owner.pending_pairs(T, s2)
    assert pend and pend[0]["display"] == "Bert"
    out = owner.confirm_pair(T, s2, pend[0]["pair_id"])
    assert out["channel"] == "telegram"
