"""Regression tests for the adversarial review of the decoder v2 (CorvinOS 1ba47cc05, two reviewers, 2026-11-17).

Each test names the finding it pins. They run the real decoder, consistency sweep and policy on a real encrypted store.
"""
from __future__ import annotations

import json
import time

import pytest

from core.personal_kb import consistency, decoder, evolve, model, owner, paths, policy
from core.personal_kb.store import PersonalStore, VersionConflict

from .conftest import PW
from .test_learning import GOOD, T, _idea, gen_from


@pytest.fixture()
def store(home):
    c = owner.create_owner(T, "anna", PW, consent=True, unlock_mode="convenient")
    return PersonalStore.open(T, c.owner_id, c.keys)


def _plan(store, title="Herb garden on the balcony", table=GOOD, t0=1000):
    idea = _idea(store, title)
    for k, stage in enumerate(("shape", "choices", "plan")):
        r = decoder.unfold(store, idea["uid"], stage=stage, generate=gen_from(table), now=t0 + k * 86_401)
        assert r.gate_ok, r.failures
    return store.get(idea["uid"])


def test_core1_a_forgotten_layer_note_never_comes_back_into_a_prompt(store):
    idea = _idea(store)
    decoder.unfold(store, idea["uid"], stage="shape", generate=gen_from(GOOD), now=1000)
    note = store.get(idea["uid"])["decoder"]["children"]["shape"][0]
    store.forget(note)
    consistency.run_sweep(store)                       # prunes the pointer and lowers the cursor
    st = store.get(idea["uid"])["decoder"]
    assert decoder._context(store, st) == {}
    assert "shape" not in (st.get("context") or {}), "the cached copy of the forgotten text stays on the idea"


def test_core2_a_draft_edited_after_the_sweep_read_it_is_not_deleted(store):
    idea = _plan(store)
    stray = store.put(model.new_entity("task", "left behind", origin="derived", labels=["decoder"],
                                       links=[{"rel": "derived_from", "to": idea["uid"]}], now=0.0))
    v = next(x for x in consistency.sweep(consistency._graph(store), time.time()) if x.code == "STRAY_DRAFT")
    other = PersonalStore.open(T, store.owner_id, store.keys)            # another process edits it meanwhile
    cur = other.get(stray["uid"])
    other.put(dict(cur, title="mine now"), expected_version=cur["version"])
    with pytest.raises(VersionConflict):
        store.forget(stray["uid"], expected_version=stray["version"])
    store.reload_if_changed()
    assert consistency._apply_live(store, v, time.time()) is False
    assert store.get(stray["uid"])["title"] == "mine now"


def test_core3_a_corrupt_idea_does_not_take_its_children_with_it(store):
    idea = _plan(store)
    kids = [u for us in idea["decoder"]["children"].values() for u in us]
    rec = store.manifest["objects"][idea["uid"]]
    f = paths.object_file(T, store.owner_id, rec["oid"])
    b = bytearray(f.read_bytes())
    b[len(b) // 2] ^= 0xFF
    f.write_bytes(bytes(b))
    s2 = PersonalStore.open(T, store.owner_id, store.keys)
    assert idea["uid"] in s2.problems
    consistency.run_sweep(s2, now=time.time() + 10_000)
    assert all(s2.get(k) is not None for k in kids), "children of a recoverable idea were deleted"


def test_core4_the_heal_never_makes_an_untouched_draft_look_edited(store):
    idea = _plan(store)
    tasks = idea["decoder"]["children"]["plan"]
    store.forget(idea["decoder"]["children"]["choices"][0])            # the tasks now have a dangling related link
    consistency.run_sweep(store)
    assert all(store.get(t)["version"] == 1 for t in tasks)
    r = decoder.unfold(store, idea["uid"], stage="plan", generate=gen_from(GOOD), now=10 * 86_401)
    assert r.gate_ok
    assert all(store.get(t) is None for t in tasks), "untouched old plan tasks pile up after a redo"


def test_core5_a_plan_is_written_dependents_first_so_its_links_never_dangle(store):
    seen = []
    orig = store.put

    def spy(ent, **kw):
        out = orig(ent, **kw)
        if ent["type"] == "task":
            seen.append({ln["to"] for ln in ent["links"] if ln["rel"] == "blocks"} - set(store.index))
        return out
    store.put = spy
    _plan(store)
    assert seen and all(not missing for missing in seen), "a blocks link pointed at a task not yet written"


def test_core6_drafts_the_idea_recorded_are_kept_when_a_later_write_fails(store, monkeypatch):
    idea = _idea(store)
    real_put = store.put

    def put(ent, **kw):
        out = real_put(ent, **kw)
        if ent["uid"] == idea["uid"] and (ent.get("decoder") or {}).get("children", {}).get("shape"):
            raise OSError("disk full after the idea landed")
        return out
    monkeypatch.setattr(store, "put", put)
    with pytest.raises(OSError):
        decoder.unfold(store, idea["uid"], stage="shape", generate=gen_from(GOOD), now=1000)
    monkeypatch.setattr(store, "put", real_put)
    landed = store.get(idea["uid"])["decoder"]["children"]["shape"]
    assert landed and all(store.get(u) is not None for u in landed)


def test_core7_a_direct_change_moves_a_waiting_suggestion_instead_of_voiding_it(store):
    policy.propose(store, {"decoder_template_variant": "t0_strict"}, cause="decoder_errors", require_confirmation=True)
    for _ in range(4):
        evolve.record_plan_outcome(store, planned=7, kept=2)              # lowers the cap directly
    pend = store.meta["policy_pending"]
    assert pend["target"]["decoder_template_variant"] == "t0_strict"
    policy.confirm(store, pend["change_id"])
    cur = policy.current(store)
    assert cur["decoder_template_variant"] == "t0_strict" and cur["decoder_task_cap"] < 7


def test_core8_forgetting_an_idea_with_many_drafts_stays_cheap(store):
    big = dict(GOOD, plan={"tasks": [{"title": f"step {i}", "dod": "done", "depends_on": []} for i in range(7)]})
    for i in range(40):
        store.put(model.new_entity("idea", f"Filler idea number {i} about something else", origin="user"))
    idea = _plan(store, table=big)
    store.forget(idea["uid"])
    t0 = time.perf_counter()
    r = consistency.run_sweep(store, now=time.time() + 10_000)
    assert r["healed"] >= 9 and time.perf_counter() - t0 < 5.0


def test_core9_one_fix_that_cannot_be_written_does_not_stop_the_others(store, monkeypatch):
    from core.personal_kb.store import QuotaExceeded
    a = _plan(store, "Idea one about the shed")
    b = _plan(store, "Idea two about the garage", t0=900_000)
    kids_b = [u for us in b["decoder"]["children"].values() for u in us]
    store.forget(a["decoder"]["children"]["plan"][0])                  # needs a put (pointer fix)
    store.forget(b["uid"])                                             # needs deletions (orphans)
    real_put = store.put
    monkeypatch.setattr(store, "put", lambda *a_, **k: (_ for _ in ()).throw(QuotaExceeded("full")))
    consistency.run_sweep(store, now=time.time() + 10_000)
    monkeypatch.setattr(store, "put", real_put)
    assert all(store.get(u) is None for u in kids_b)


def test_core10_the_estimator_counts_forgotten_plan_tasks_as_planned(store, monkeypatch):
    seen = {}
    monkeypatch.setattr(evolve, "record_plan_outcome", lambda s, planned, kept: seen.update(planned=planned, kept=kept))
    big = dict(GOOD, plan={"tasks": [{"title": f"step {i}", "dod": "done", "depends_on": []} for i in range(3)]})
    idea = _plan(store, table=big)
    tasks = idea["decoder"]["children"]["plan"]
    for t in tasks[:2]:
        store.forget(t)
    consistency.run_sweep(store)
    t = store.get(tasks[2])
    for to in ("in_progress", "done"):
        t = store.get(tasks[2])
        store.put(model.transition(t, to, dod="ok" if to == "done" else None), expected_version=t["version"])
    assert decoder.refresh_done(store, idea["uid"]) is True
    assert seen == {"planned": 3, "kept": 1}


def test_core11_ranks_run_across_critic_rounds_and_stop_at_three(store):
    policy.owner_edit(store, {"decoder_adversarial_rounds": 3})
    adv = {"objections": [{"category": "risk", "text": f"remark {i}"} for i in range(3)]}
    idea = _idea(store)
    r = decoder.unfold(store, idea["uid"], stage="shape", generate=gen_from(dict(GOOD, adversarial=adv)), now=1000)
    assert [o["rank"] for o in r.objections] == [1, 2, 3]


def test_api3_a_new_plan_clears_keep_it_open(store):
    idea = _plan(store)
    st = dict(idea["decoder"], dismissed=["STUCK_REALIZATION"])
    store.put(dict(idea, decoder=st), expected_version=idea["version"])
    r = decoder.unfold(store, idea["uid"], stage="plan", generate=gen_from(GOOD), now=10 * 86_401)
    assert r.gate_ok and store.get(idea["uid"])["decoder"]["dismissed"] == []


def test_api4_close_rechecks_the_current_plan(store):
    idea = _plan(store)
    with pytest.raises(decoder.DecoderError, match="not finished"):
        decoder.close_idea(store, idea["uid"])
    for u in idea["decoder"]["children"]["plan"]:
        for to in ("in_progress", "done"):
            t = store.get(u)
            store.put(model.transition(t, to, dod="ok" if to == "done" else None), expected_version=t["version"])
    cur = store.get(idea["uid"])
    if cur["status"] != "realized":
        assert decoder.close_idea(store, idea["uid"]) is True
    assert store.get(idea["uid"])["status"] == "realized"


def test_api9_a_stale_panel_cannot_resolve_a_different_remark(store):
    adv = {"objections": [{"category": "risk", "text": "frost in April"}]}
    idea = _idea(store)
    decoder.unfold(store, idea["uid"], stage="shape", generate=gen_from(dict(GOOD, adversarial=adv)), now=1000)
    with pytest.raises(decoder.DecoderError, match="changed meanwhile"):
        decoder.resolve_objection(store, idea["uid"], 0, "dismiss", reason="x", expect="something else")
    decoder.resolve_objection(store, idea["uid"], 0, "dismiss", reason="x", expect="frost in April")
    assert store.get(idea["uid"])["decoder"]["objections"][0]["resolved"] is True


@pytest.mark.parametrize("notices", [True, False])
def test_canary_counts_whether_the_critic_notices_a_planted_duplicate(store, monkeypatch, notices):
    monkeypatch.setattr(decoder, "CANARY_EVERY", 1)

    def adversarial(prompt):
        planted = prompt.count('"title": "buy planters"') >= 2
        return {"objections": [{"category": "duplicate", "text": "task listed twice"}]} if planted and notices else {"objections": []}
    idea = _plan(store, table=dict(GOOD, adversarial=adversarial))
    c = store.meta["critic_canary"]
    assert (c["runs"], c["missed"], c["last"]) == (1, 0 if notices else 1, "found" if notices else "missed")
    assert all(o["category"] != "duplicate" for o in store.get(idea["uid"])["decoder"]["objections"])   # never in the idea
