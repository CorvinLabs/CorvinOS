"""Meta loop + 9D optimizer hardening (adversarial review F-L1 / F-L9 / F-L12).

Replaces ``tests/test_meta_robustness.py`` and ``tests/test_adversarial_meta*.py``,
which tested a ``MetaOptimizer`` API that never existed in this module
(``alpha_core_min``, ``get_tuned_hyperparameters``, ``MetaOptimizerState``,
``WatchdogIntegration``, ``detect_divergence``) or their own local mock classes.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from core.learning.live_collector_integration import LiveCollectorIntegration
from core.learning.meta_optimizer import HYPERPARAMETERS, MetaOptimizer
from core.learning.nine_d_loss import NineD_LossOptimizer
from core.learning.watchdog import DivergenceWatchdog


class RecordingAudit:
    """Audit sink that records every hyperparameter-change record (tests only)."""

    def __init__(self, fail: bool = False):
        self.records: list[dict] = []
        self.fail = fail

    def __call__(self, event_type, *, tenant_id, details):
        if self.fail:
            raise RuntimeError("chain unavailable")
        self.records.append({"event_type": event_type, "tenant_id": tenant_id, "details": details})
        return f"ref-{len(self.records)}"


def _warm(meta: MetaOptimizer, n: int = 12) -> None:
    """Push enough loss/gradient history past the stability gate."""
    for i in range(n):
        meta.compute_loss({"loss_delta_core": 0.02 * (i % 3), "loss_delta_infra": 0.01})
        meta.compute_gradients(0.3, 0.2)


# ── F-L9: key contract ───────────────────────────────────────────────────────


def test_compute_loss_reads_the_9d_key_convention():
    meta = MetaOptimizer(audit=RecordingAudit())
    loss = meta.compute_loss({"core_loss": 0.35, "prev_core_loss": 0.30, "infra_loss": 0.22, "prev_infra_loss": 0.20})
    assert loss == pytest.approx(0.7)  # (0.05 + 0.02) / 0.1 — was ALWAYS 0.0 before
    assert meta.compute_loss({"loss_delta_core": 0.05, "loss_delta_infra": 0.02}) == pytest.approx(0.7)


def test_non_finite_delta_is_worst_case_not_zero():
    meta = MetaOptimizer(audit=RecordingAudit())
    assert meta.compute_loss({"core_loss": float("nan"), "prev_core_loss": 0.3}) == 1.0
    assert meta.compute_loss({"loss_delta_core": float("inf"), "loss_delta_infra": 0.0}) == 1.0
    assert meta.non_finite_inputs == 2


# ── F-L9: set_state validation via the watchdog bounds ──────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        {"α_core": float("nan"), "α_infra": 0.01, "damping_core": 0.9, "damping_infra": 0.95},
        {"α_core": 5.0, "α_infra": 0.01, "damping_core": 0.9, "damping_infra": 0.95},
        {"α_core": 0.1, "α_infra": 0.01, "damping_core": 1.5, "damping_infra": 0.95},
        {"α_core": 0.1, "α_infra": 0.01, "damping_core": 0.9},  # missing key
        {"α_core": "0.1", "α_infra": 0.01, "damping_core": 0.9, "damping_infra": 0.95},
    ],
)
def test_set_state_rejects_invalid_state(bad):
    meta = MetaOptimizer(audit=RecordingAudit())
    before = meta.get_state_params()
    with pytest.raises(ValueError):
        meta.set_state(bad)
    assert meta.get_state_params() == before


def test_set_state_bounds_match_the_watchdog():
    meta = MetaOptimizer(audit=RecordingAudit())
    assert meta._bounds == DivergenceWatchdog().bounds
    good = {"α_core": 0.2, "α_infra": 0.02, "damping_core": 0.85, "damping_infra": 0.9, "update_count": 7}
    meta.set_state(good)
    assert meta.get_state() == good


# ── F-L9: audit on every hyperparameter change, fail-closed ─────────────────


def test_every_hyperparameter_change_is_audited_first():
    audit = RecordingAudit()
    meta = MetaOptimizer(tenant_id="_default", audit=audit)
    _warm(meta)
    before = meta.get_state_params()
    meta.apply_gradients({"α_core": 5.0, "α_infra": 5.0, "damping_core": 0.0, "damping_infra": 0.0}, learning_rate=0.01)
    assert meta.get_state_params() != before
    assert len(audit.records) == 1
    rec = audit.records[0]
    assert rec["event_type"] == "learning.hyperparameter_changed"
    assert rec["tenant_id"] == "_default"
    assert rec["details"]["reason"] == "gradient_step"
    changes = rec["details"]["changes"]
    assert set(changes) <= set(HYPERPARAMETERS)
    for name, change in changes.items():
        assert change["old"] == before[name]
        assert change["new"] == meta.get_state_params()[name]

    meta.set_state(before)
    assert audit.records[-1]["details"]["reason"] == "set_state"
    assert meta.get_state_params() == before


def test_unchanged_step_writes_no_audit_record():
    audit = RecordingAudit()
    meta = MetaOptimizer(audit=audit)
    _warm(meta)
    meta.apply_gradients({"α_core": 0.0, "α_infra": 0.0, "damping_core": 0.0, "damping_infra": 0.0})
    assert audit.records == []


def test_change_is_not_applied_when_the_chain_write_fails():
    meta = MetaOptimizer(audit=RecordingAudit(fail=True))
    _warm(meta)
    before = meta.get_state_params()
    with pytest.raises(RuntimeError):
        meta.apply_gradients({"α_core": 5.0, "α_infra": 5.0, "damping_core": 0.0, "damping_infra": 0.0}, learning_rate=0.01)
    assert meta.get_state_params() == before


def test_default_audit_sink_is_the_core_chain(tmp_path: Path, monkeypatch):
    """Without an injected sink the record lands on the isolated core chain."""
    chain = tmp_path / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    meta = MetaOptimizer(tenant_id="_default")
    _warm(meta)
    meta.apply_gradients({"α_core": 5.0, "α_infra": 5.0, "damping_core": 0.0, "damping_infra": 0.0}, learning_rate=0.01)
    records = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    hp = [r for r in records if r["event_type"] == "learning.hyperparameter_changed"]
    assert len(hp) == 1
    assert hp[0]["details"]["tenant_id"] == "_default"
    assert "changes" in hp[0]["details"]


# ── F-L9: emit_event matches the collector's signature ──────────────────────


def test_emit_event_writes_a_meta_tuning_record(tmp_path: Path):
    collector = LiveCollectorIntegration(tenant_id="_default")
    assert str(collector.event_log_dir).startswith(os.environ["CORVIN_HOME"])  # F-L12
    meta = MetaOptimizer(audit=RecordingAudit())
    meta.emit_event(collector, step_count=100)  # raised TypeError before
    events = [json.loads(l) for l in collector.event_log_file.read_text().splitlines()]
    assert events[-1]["event_type"] == "learning_meta_tuning"
    assert events[-1]["alpha_core"] == meta.α_core


# ── F-L1: the 9D optimizer imports, uses the real watchdog, checkpoints under CORVIN_HOME ──


def _feedback(step: int) -> dict:
    return {
        "memory": {"missing_context_ratio": 0.2, "irrelevance_score": 0.1, "retrieval_latency_ms": 20.0, "token_waste_ratio": 0.1},
        "skills": {"composition_error_rate": 0.1, "dag_execution_time_ms": 50.0},
        "plugins": {"quality_gain": 0.8, "execution_time_ms": 50.0, "error_rate": 0.05, "conflict_score": 0.0},
    }


def test_nine_d_uses_meta_hyperparameters_and_the_divergence_watchdog(tmp_path: Path):
    audit = RecordingAudit()
    meta = MetaOptimizer(tenant_id="_default", audit=audit)
    opt = NineD_LossOptimizer(tenant_id="_default", meta_optimizer=meta)
    assert isinstance(opt.watchdog, DivergenceWatchdog)
    assert opt.infra_learning_rate == meta.α_infra and opt.infra_damping == meta.damping_infra
    assert str(opt.checkpoint_dir).startswith(os.environ["CORVIN_HOME"])
    assert ".corvin" not in str(opt.checkpoint_dir).replace(os.environ["CORVIN_HOME"], "")

    for step in range(1, 201):
        loss = opt.step(_feedback(step))
        assert 0.0 <= loss <= 1.0 and math.isfinite(loss)

    # two phase-locked meta updates → two checkpoints persisted on disk under the tenant home
    assert opt.watchdog.checkpoint_count == 2
    files = sorted(opt.checkpoint_dir.glob("ckpt_*.json"))
    assert len(files) == 2
    ckpt = json.loads(files[0].read_text())
    assert ckpt["tenant_id"] == "_default"
    assert set(HYPERPARAMETERS) <= set(ckpt["state"])
    # the meta loop's own loss now feeds L_meta (was a hard-coded 0.0)
    assert opt.meta_optimizer.loss_history
    snapshot = opt.get_state_snapshot()
    assert snapshot["meta_loop"]["checkpoints"] == 2


def test_nine_d_restores_the_checkpoint_when_the_meta_step_diverges(tmp_path: Path):
    class Diverging(MetaOptimizer):
        def apply_gradients(self, gradients, learning_rate=None, damping=None):
            # bypass the audited path on purpose to corrupt the live value
            self.α_core = float("nan")

    meta = Diverging(tenant_id="_default", audit=RecordingAudit())
    opt = NineD_LossOptimizer(tenant_id="_default", meta_optimizer=meta)
    for step in range(1, 101):
        opt.step(_feedback(step))
    assert opt.meta_rollbacks == 1
    assert meta.α_core == 0.1  # restored from the checkpoint taken before the step
    assert opt.watchdog.rollback_count == 1
