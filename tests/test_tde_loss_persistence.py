"""ADR-0219 R4: cross-session persistence of the loss profile tracker.

The tracker was in-session only, so the amplifier that is supposed to learn
which task_types delegate well forgot everything each session and could never
actually learn. R4 persists the MEASURED (shadow-run) entries to a TENANT-scoped
file so a tenant's evidence survives its own sessions — without breaking the
ADR-0215 F4 cross-tenant isolation.

Run: python3 -m pytest tests/test_tde_loss_persistence.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))
sys.path.insert(0, str(_REPO / "operator"))

from tde.loss_profile_tracker import (  # noqa: E402
    LossProfileTracker, _persist_path_for,
)


def test_measured_entries_survive_a_new_tracker(tmp_path):
    p = tmp_path / "loss_profile.jsonl"
    t1 = LossProfileTracker(model_id="haiku", persist_path=p)
    t1.record_delegation_result("analyze", "tiered_delegation", 12.0, measured=True)
    t1.record_delegation_result("analyze", "tiered_delegation", 8.0, measured=True)

    # A brand-new tracker (a new session) loads the tenant's prior evidence.
    t2 = LossProfileTracker(model_id="haiku", persist_path=p)
    assert len(t2.history) == 2
    assert all(e.measured for e in t2.history)
    assert {round(e.loss_pct) for e in t2.history} == {12, 8}


def test_proxy_entries_are_not_persisted(tmp_path):
    p = tmp_path / "loss_profile.jsonl"
    t1 = LossProfileTracker(model_id="haiku", persist_path=p)
    t1.record_delegation_result("analyze", "tiered_delegation", 5.0, measured=False)  # proxy
    t1.record_delegation_result("analyze", "tiered_delegation", 9.0, measured=True)   # measured
    t2 = LossProfileTracker(model_id="haiku", persist_path=p)
    assert len(t2.history) == 1  # only the measured one crossed the session boundary
    assert t2.history[0].measured is True


def test_decayed_entries_are_dropped_on_load(tmp_path):
    p = tmp_path / "loss_profile.jsonl"
    t1 = LossProfileTracker(model_id="haiku", persist_path=p)
    t1.record_delegation_result("analyze", "tiered_delegation", 10.0, measured=True)
    # Hand-write an ancient entry (older than PRUNE_AFTER_HALF_LIVES half-lives).
    ancient_ts = time.time() - (LossProfileTracker.DECAY_HALF_LIFE_DAYS * 86400 *
                                (LossProfileTracker.PRUNE_AFTER_HALF_LIVES + 2))
    import json
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": ancient_ts, "task_type": "old",
                             "model_id": "haiku", "loss_pct": 1.0,
                             "engine": "tiered_delegation", "complexity": "moderate",
                             "measured": True, "alternative_scores": {}}) + "\n")
    t2 = LossProfileTracker(model_id="haiku", persist_path=p)
    assert len(t2.history) == 1  # ancient one pruned
    assert t2.history[0].task_type == "analyze"


def test_persistence_is_fail_soft_on_a_bad_path(tmp_path):
    # A directory where a file is expected → append/load must not raise.
    bad = tmp_path / "adir"
    bad.mkdir()
    t = LossProfileTracker(model_id="haiku", persist_path=bad)  # load: it's a dir
    # record must not raise even though writing fails
    t.record_delegation_result("analyze", "x", 5.0, measured=True)
    assert True  # got here without an exception


def test_torn_line_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "loss_profile.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"timestamp": ' + str(time.time()) + ', "task_type": "ok", "model_id": "h",'
                 '"loss_pct": 3, "engine": "e", "measured": true}\n')
        fh.write("{ this is not valid json\n")  # torn/partial line
    t = LossProfileTracker(model_id="h", persist_path=p)
    assert len(t.history) == 1 and t.history[0].task_type == "ok"


def test_no_persistence_without_path(tmp_path):
    t = LossProfileTracker(model_id="haiku")  # no persist_path
    t.record_delegation_result("analyze", "x", 5.0, measured=True)
    assert t._persist_path is None
    # nothing written anywhere
    assert not list(tmp_path.iterdir())


# ── _persist_path_for (tenant derivation + isolation) ────────────────────────

def test_default_session_key_has_no_persistence():
    assert _persist_path_for("default") is None


def test_env_opt_out_disables_persistence():
    os.environ["CORVIN_TDE_LOSS_PERSIST"] = "0"
    try:
        assert _persist_path_for("acme:sid123") is None
    finally:
        os.environ.pop("CORVIN_TDE_LOSS_PERSIST", None)


def test_tenants_get_distinct_paths(monkeypatch, tmp_path):
    # Mock forge.paths.tenant_global_dir so the test does not depend on a real
    # corvin_home; assert the sid is dropped and tenants never share a file.
    monkeypatch.setenv("CORVIN_TDE_LOSS_PERSIST", "1")  # conftest disables it by default
    import types
    fake = types.SimpleNamespace(
        tenant_global_dir=lambda t: tmp_path / t
    )
    monkeypatch.setitem(sys.modules, "forge", types.SimpleNamespace(paths=fake))
    monkeypatch.setitem(sys.modules, "forge.paths", fake)
    a1 = _persist_path_for("acme:sessionA")
    a2 = _persist_path_for("acme:sessionB")   # same tenant, different session
    b1 = _persist_path_for("globex:sessionA")  # different tenant
    assert a1 == a2, "same tenant → same file across sessions (cross-session learning)"
    assert a1 != b1, "different tenants → different files (F4 isolation)"
    assert a1 is not None and str(a1).endswith("loss_profile.jsonl")


def test_record_unmeasured_does_not_assume_good():
    # ADR-0219 R5b: a side-effecting action must NOT be recorded at the 1%
    # 'good' proxy loss — record_unmeasured uses the conservative default and
    # flags it unmeasured, so the estimate never drifts optimistic.
    t = LossProfileTracker(model_id="haiku")
    t.record_unmeasured("write_file", "tiered_delegation")
    assert len(t.history) == 1
    e = t.history[0]
    assert e.measured is False
    assert e.loss_pct == LossProfileTracker.DEFAULT_LOSS_PCT  # neutral, not 1%


def test_measured_count_for_counts_only_measured_same_model():
    t = LossProfileTracker(model_id="haiku")
    t.record_delegation_result("analyze", "e", 5.0, measured=True)
    t.record_delegation_result("analyze", "e", 6.0, measured=True)
    t.record_delegation_result("analyze", "e", 7.0, measured=False)  # proxy
    t.record_delegation_result("other", "e", 5.0, measured=True)     # other action
    assert t.measured_count_for("analyze") == 2
    assert t.measured_count_for("nonexistent") == 0


def test_f2_records_the_real_executed_model_not_the_session_constant():
    # ADR-0222 F2: the log must be multi-arm. An entry recorded with an explicit
    # model_id carries THAT model, not the tracker's session constant.
    t = LossProfileTracker(model_id="claude-haiku-4-5")
    t.record_delegation_result("analyze", "tiered_delegation", 5.0, measured=True,
                               model_id="claude-sonnet-5")
    assert t.history[0].model_id == "claude-sonnet-5"
    # No override → falls back to the session constant (proxy/legacy path).
    t.record_delegation_result("analyze", "tiered_delegation", 5.0, measured=True)
    assert t.history[1].model_id == "claude-haiku-4-5"


def test_f2_per_arm_estimate_does_not_cross_contaminate():
    # Sonnet's loss must not count as evidence about Haiku and vice-versa —
    # the exact single-arm corruption F2 fixes.
    t = LossProfileTracker(model_id="claude-haiku-4-5")
    for _ in range(t.MIN_SAMPLES + 2):
        t.record_delegation_result("analyze", "tiered_delegation", 2.0, measured=True,
                                   model_id="claude-haiku-4-5")   # haiku: low loss
        t.record_delegation_result("analyze", "tiered_delegation", 40.0, measured=True,
                                   model_id="claude-sonnet-5")    # sonnet arm: high (contrived)
    haiku = t.estimate_loss_for_task_type("analyze", model_id="claude-haiku-4-5")
    sonnet = t.estimate_loss_for_task_type("analyze", model_id="claude-sonnet-5")
    assert haiku < 0.10, haiku      # ~2% — only haiku entries
    assert sonnet > 0.30, sonnet    # ~40% — only sonnet entries
    # Default (no model_id) estimates the current worker arm (haiku).
    assert t.estimate_loss_for_task_type("analyze") == haiku


def test_malicious_tenant_is_rejected_fail_soft(monkeypatch):
    # A path-traversal session_key must not produce a path — validate_tenant_id
    # rejects it and _persist_path_for fails soft to None (in-session only),
    # never a crash and never an out-of-tree write.
    monkeypatch.setenv("CORVIN_TDE_LOSS_PERSIST", "1")  # conftest disables it by default
    import types

    def _raises(_t):
        raise ValueError("tenant_id fails charset rule")

    monkeypatch.setitem(sys.modules, "forge",
                        types.SimpleNamespace(paths=types.SimpleNamespace(tenant_global_dir=_raises)))
    monkeypatch.setitem(sys.modules, "forge.paths",
                        types.SimpleNamespace(tenant_global_dir=_raises))
    assert _persist_path_for("../../etc/passwd:sid") is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
