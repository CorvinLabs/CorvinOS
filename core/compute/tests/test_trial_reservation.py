"""The compute trial is a budget of ITERATIONS, not of submissions.

Adversarial review 2026-11-06: each accepted compute_run charged +1 while being
granted up to everything left (three runs got 500 + 499 + 498 iterations), and
compute_submit (pipeline / HAC) bypassed the trial: stage budgets overrode the
clamped top budget and nothing was charged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from corvin_compute import license_gate as LG  # noqa: E402


def test_a_run_is_charged_the_iterations_it_was_granted(tmp_path):
    granted = LG.enforce_trial_strategy({"strategy": "grid", "budget": {"max_iterations": 10**6}}, tmp_path)
    n = granted["budget"]["max_iterations"]
    assert n == LG.TRIAL_ITERATION_CAP
    LG.record_trial_iteration(tmp_path, strategy="grid", n=n)
    with pytest.raises(ValueError):
        LG.enforce_trial_engine({"engine": "pipeline", "extra": {"stages": [{"stage_id": "a"}]}}, tmp_path)
    assert LG._check_trial(tmp_path).allowed is False


def test_pipeline_stages_share_the_remaining_trial(tmp_path):
    args, reserved = LG.enforce_trial_engine({
        "engine": "pipeline", "budget": {"max_iterations": 10**6},
        "extra": {"stages": [{"stage_id": "a", "budget": {"max_iterations": 100000}},
                             {"stage_id": "b"}]}}, tmp_path)
    stages = args["extra"]["stages"]
    assert [s["budget"]["max_iterations"] for s in stages] == [250, 250]
    assert args["budget"]["max_iterations"] == 250 and reserved == 500


def test_hac_gets_one_round_and_floors_within_its_share(tmp_path):
    args, reserved = LG.enforce_trial_engine({
        "engine": "hac", "budget": {"max_iterations": 100},
        "extra": {"backprop_gate": True, "max_backprop_rounds": 9,
                  "sub_managers": [{"manager_id": "m1", "min_iterations": 10**6,
                                    "stages": [{"budget": {"max_iterations": 10**6}}]},
                                   {"manager_id": "m2"}]}}, tmp_path)
    ex = args["extra"]
    assert ex["max_backprop_rounds"] == 1 and ex["backprop_gate"] is False
    assert [m["min_iterations"] for m in ex["sub_managers"]] == [50, 10]
    assert ex["sub_managers"][0]["stages"][0]["budget"]["max_iterations"] == 50
    assert reserved == 100


def test_the_reservation_never_exceeds_what_is_left(tmp_path):
    LG.record_trial_iteration(tmp_path, strategy="grid", n=LG.TRIAL_ITERATION_CAP - 3)
    args, reserved = LG.enforce_trial_engine({"engine": "pipeline", "extra": {"stages": [{}, {}]}}, tmp_path)
    assert reserved <= 3
