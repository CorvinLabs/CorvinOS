"""Pins the measured claims of the personal decoder v2 design (tests/personal_kb/decoder_v2/).

Every assertion is a number the design note quotes. The shipped-decoder test is deliberately a tripwire: it passes while
the shipped decoder still has the gaps the design is built on, and fails the day one of them is fixed, so the design note
has to be updated in the same commit (the same pattern as the boot-layer guard tests).
"""
from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

import pytest

from tests.personal_kb.decoder_v2 import consistency as C
from tests.personal_kb.decoder_v2 import estimators, learning, run_eval
from tests.personal_kb.decoder_v2.corpus import FAULTS, NOW, clean_graph

HERE = Path(run_eval.__file__).parent


def test_consistency_layer_scores_on_the_seeded_faults_and_the_legitimate_actions(monkeypatch):
    monkeypatch.setattr(run_eval, "INSTANCES", 6)
    m = run_eval.measure_consistency()
    assert m["clean_violations"] == 0
    assert m["legit_violations"] == {}, "a legitimate owner or decoder action raised a violation"
    assert m["unexpected_on_faults"] == 0
    assert m["unsafe_heal_share"] == 0.0
    assert m["recall_mean"] >= 0.98, {k: v["recall"] for k, v in m["per_class"].items() if v["recall"] < 1}
    assert m["hostile_identical_pair_found_at_2300_ideas"] and m["newest_pair_found_among_2298_long_titles"]
    assert m["filtered_path_recall_on_1560_natural_titles"] >= 0.95
    assert m["sweep_cpu_ms_worst_of_6_corpora_informational"] < 1500, "the sweep must stay cheap on hostile corpora (about 200 ms idle, bounded by MAX_DUPLICATE_WORK)"
    assert m["loss"] == 0.0


def test_the_duplicate_index_equals_brute_force_when_no_word_is_dropped(monkeypatch):
    monkeypatch.setattr(C, "EXACT_PAIRS_UP_TO", 0)
    monkeypatch.setattr(C, "COMMON_TOKEN_FLOOR", 10 ** 9)
    rng = random.Random(1)
    words = [f"w{i}x" for i in range(14)]
    pairs_seen = 0
    for _ in range(400):
        ideas = [{"uid": f"I{i:03d}", "title": " ".join(rng.sample(words, rng.randint(2, 7))), "body": ""}
                 for i in range(rng.randint(2, 30))]
        fast, capped = C._duplicates(ideas)
        sigs = {i["uid"]: C.title_sig(i) for i in ideas}
        brute = {(a, b) for a, b in itertools.combinations(sorted(sigs), 2) if C._alike(sigs[a], sigs[b])}
        assert not capped
        # identical groups are reported as a star (first member against the others), the index finds every other pair
        assert {p for p in fast if p in brute} == {p for p in brute if p in set(fast) or sigs[p[0]] != sigs[p[1]]}
        assert all(sigs[a] == sigs[b] or (a, b) in brute for a, b in fast)
        pairs_seen += len(brute)
    assert pairs_seen > 500, "the corpora must actually contain duplicate pairs"


def test_heal_only_lowers_a_cursor_never_touches_status_and_is_idempotent_on_fault_mixes():
    rng = random.Random(21)
    names = list(FAULTS)
    for trial in range(40):
        G = clean_graph(seed=300 + trial)
        for name in rng.sample(names, 3):
            try:
                FAULTS[name](G, rng)
            except (StopIteration, IndexError):
                continue            # the clean graph had no site for this fault
        assert run_eval._heal_problems(G) == [], f"trial {trial}"
        H, _ = C.heal(G, NOW)
        order = C.STAGES
        for uid, e in G["entities"].items():
            h = H["entities"].get(uid)
            if e["type"] == "idea" and h is not None and e.get("decoder") and h.get("decoder"):
                assert order.index(h["decoder"]["stage"]) <= order.index(e["decoder"]["stage"]), "a heal raised a cursor"


def test_a_stray_draft_is_only_swept_after_the_grace_period():
    G = clean_graph(seed=9)
    idea = next(e for e in G["entities"].values() if e["type"] == "idea" and e["status"] == "exploring" and e["decoder"]["stage"] == "choices")
    fresh = {"schema": 1, "uid": "U70000", "type": "task", "title": "being written", "body": "", "status": "open", "origin": "derived",
             "review": "none", "key": None, "links": [{"rel": "derived_from", "to": idea["uid"]}], "labels": ["decoder"], "version": 1,
             "created": NOW - 5.0, "task": {"dod": "x", "blocked_reason": None, "cancel_reason": None}, "decoder": None, "promoted_to": None}
    G["entities"][fresh["uid"]] = fresh
    assert not [v for v in C.sweep(G, NOW) if v.code == "STRAY_DRAFT"]
    assert [v for v in C.sweep(G, NOW + C.GRACE_S) if v.code == "STRAY_DRAFT"]


def test_a_task_that_names_a_kb_reference_or_a_promotion_survives_heal():
    G = clean_graph(seed=4)
    H, _ = C.heal(G, NOW)
    for uid, e in G["entities"].items():
        for ln in e["links"]:
            if ln["rel"] in C.KEEP_LINKS or not C.is_local(ln["to"]):
                assert ln in H["entities"][uid]["links"]


def test_learning_has_no_controller_that_reliably_beats_doing_nothing():
    r = learning.measure(seeds=16)
    eq = {k: r[k]["loss"]["equal(regret,cost,harm)"] for k in ("do_nothing", "ratchet_v1", "guarded_ratchet", "measured_v2")}
    assert abs(eq["measured_v2"] - eq["do_nothing"]) < 0.05, eq                 # no reliable gain over doing nothing
    assert eq["ratchet_v1"] > eq["do_nothing"] + 0.05, eq                       # the shipped ratchet is worse on balance
    ps = r["ratchet_v1"]["per_scenario"]
    assert ps["start_is_optimal"]["harm"] > 0.5                                 # it wrecks a start that was already right
    assert ps["big_tighten_needed"]["regret"] < 0.05                            # and it wins where tightening IS needed
    assert r["measured_v2"]["per_scenario"]["start_is_optimal"]["harm"] < 0.2


def test_the_plan_size_estimator_never_forbids_what_the_owner_finishes():
    r = estimators.measure(seeds=20)["cap"]
    for label in r:
        assert r[label]["estimator_v2"]["shortfall"] < 0.05, label
    assert r["large_plans_10to14"]["ratchet_v1"]["shortfall"] > 1.0          # the ratchet can never raise the cap
    assert r["large_plans_10to14"]["static_7"]["shortfall"] > 1.0


def test_the_critic_probe_results_are_what_the_design_quotes():
    s = run_eval.critic_probe_summary()
    assert s["objections_per_run"] == [3]                                        # the count never varies, so it cannot be a signal
    assert (s["seeded_defect_addressed"], s["seeded_defect_ranked_first"], s["flawed_runs"]) == (16, 16, 16)
    assert s["absent_defect_claimed_on_sound"] == 0 and s["defect_present_in_sound_by_judge"] == 0
    real = [s["ratings_by_rank"][k]["real_defect"] for k in (1, 2, 3)]
    assert real == sorted(real, reverse=True) and real[0] > 3 * real[2]          # usefulness falls with the rank
    d = estimators.critic_display()
    assert d["show_three_loud_v1"]["loud_noise"] > 5 * d["first_loud_rest_quiet"]["loud_noise"]


def test_the_stored_real_kb_crosscheck_matches_the_design_note():
    r = json.loads((HERE / "real_kb_crosscheck_results.json").read_text())
    assert r["dangling_links"]["both"] == r["dangling_links"]["kb_check"]
    assert r["duplicate_ids"]["both"] == r["duplicate_ids"]["kb_check"]
    nd = r["near_duplicates_ideas_and_concepts"]
    assert nd["both"] == nd["mine"] and nd["both"] >= 4                          # no false alarm against kb check's own rule


def test_the_shipped_decoder_still_has_the_gaps_the_design_is_built_on():
    """Tripwire. If one of these fails, the shipped decoder was fixed: update the design note (section 1) and this test."""
    from tests.personal_kb.decoder_v2.baseline_v1 import measure_v1_baseline

    o = measure_v1_baseline()
    assert o["tasks_with_a_blocks_link"] == 0 and o["plan_tasks"] >= 2             # depends_on is not stored
    assert o["closed_after_one_cancelled_rest_done"] is False                      # one cancelled task keeps the idea open forever
    assert o["forgotten_task_still_listed_as_child"] is True
    assert o["orphans_after_forgetting_the_idea"] == o["children_before_forgetting_the_idea"] >= 2
    assert o["public_consistency_functions"] == []
    assert o["context_copy_of_stage_data_kept_on_the_idea"] == ["choices", "plan", "shape"]
