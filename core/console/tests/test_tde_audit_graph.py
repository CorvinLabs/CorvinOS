"""ADR-0214: TDE audit-graph endpoint (hash-chain-verified delegation tree).

Covers the builder (`_build_tde_audit_graph`) and the route
(`compute_tde_audit_graph`) that reconstruct one TDE-delegated turn's real
tde.* audit trail into a vis.js-compatible graph, with chain integrity
surfaced in ``meta.chain_verified``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge",
           _REPO / "operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_console.routes import compute as compute_mod  # noqa: E402
from forge import security_events as se  # noqa: E402


def _fake_session_record():
    from corvin_console import auth as _auth
    return _auth.SessionRecord(
        sid="s", sid_fingerprint="fp", tier="owner", tenant_id="_default",
        token_fingerprint="tf", csrf_secret="cs",
        created_at=0.0, last_seen_at=0.0, expires_at=2_000_000_000.0,
    )


def _write_normal_turn(path: Path, run_id: str, *, base_ts: float = 1_000.0,
                       tenant_id: str = "_default") -> None:
    """Write a real hash-chained tde.* trail for one TDE turn: one delegated
    step (with a shadow loss measurement) + one local step.

    ``tenant_id`` mirrors production since 2026-07-24: chat_runtime threads the
    authenticated tenant through SendIntegration/AdaptiveDelegationExecutor
    into every tde.* record (audit_event's reserved arg → details block), and
    the graph endpoint refuses records not stamped with the caller's tenant.
    """
    t = base_ts
    se.write_event(path, "tde.engine_selected", ts=t, details={
        "engine": "tiered_delegation", "confidence": 0.92, "override": False,
        "trivial": False, "task_type": "code_generation", "complexity": "moderate",
        "tde_run_id": run_id, "tenant_id": tenant_id,
    })
    t += 1
    se.write_event(path, "tde.delegation_decision", ts=t, details={
        "step_action": "generate_code", "delegate": True, "reason_code": "gates_passed",
        "tde_run_id": run_id, "tenant_id": tenant_id, "step_num": 1,
    })
    t += 1
    se.write_event(path, "tde.step_delegated", ts=t, details={
        "step_action": "generate_code", "success": True, "duration_ms": 1200,
        "ipc": "SubprocessWorkerIPC", "tde_run_id": run_id, "tenant_id": tenant_id, "step_num": 1,
    })
    t += 1
    se.write_event(path, "tde.loss_recorded", ts=t, details={
        "task_type": "generate_code", "engine": "tiered_delegation",
        "loss_pct": 0.02, "measured": True, "tde_run_id": run_id, "tenant_id": tenant_id, "step_num": 1,
    })
    t += 1
    se.write_event(path, "tde.delegation_decision", ts=t, details={
        "step_action": "reason_about", "delegate": False, "reason_code": "budget_exhausted",
        "tde_run_id": run_id, "tenant_id": tenant_id, "step_num": 2,
    })
    t += 1
    se.write_event(path, "tde.step_executed_local", ts=t, details={
        "step_action": "reason_about", "success": True, "duration_ms": 800,
        "tde_run_id": run_id, "tenant_id": tenant_id, "step_num": 2,
    })
    t += 1
    se.write_event(path, "tde.plan_executed", ts=t, details={
        "step_count": 2, "batch_count": 1, "delegated_count": 1, "local_count": 1,
        "tde_run_id": run_id, "tenant_id": tenant_id,
    })


class TestBuilderShape:
    """Direct unit tests of _build_tde_audit_graph — no file I/O."""

    def _details(self, **kw):
        return kw

    def _ev(self, event_type, ts, **details):
        return {"ts": ts, "event_type": event_type, "details": details}

    def test_normal_turn_produces_sane_graph(self):
        run_id = "tde-111-aaaa"
        events = [
            self._ev("tde.engine_selected", 1.0, engine="tiered_delegation",
                     confidence=0.9, tde_run_id=run_id, task_type="code_generation",
                     complexity="moderate"),
            self._ev("tde.delegation_decision", 2.0, step_action="generate_code",
                     delegate=True, reason_code="gates_passed", tde_run_id=run_id, step_num=1),
            self._ev("tde.step_delegated", 3.0, step_action="generate_code",
                     success=True, duration_ms=500, tde_run_id=run_id, step_num=1),
            self._ev("tde.delegation_decision", 4.0, step_action="reason_about",
                     delegate=False, reason_code="budget_exhausted", tde_run_id=run_id, step_num=2),
            self._ev("tde.step_executed_local", 5.0, step_action="reason_about",
                     success=True, duration_ms=300, tde_run_id=run_id, step_num=2),
            self._ev("tde.plan_executed", 6.0, step_count=2, batch_count=1,
                     delegated_count=1, local_count=1, tde_run_id=run_id),
        ]
        payload = compute_mod._build_tde_audit_graph(
            run_id, events, chain_verified=True, chain_problems=[],
        )
        assert payload["mode"] == "tde"
        assert payload["run_id"] == run_id
        node_ids = {n["id"] for n in payload["nodes"]}
        assert "task_root" in node_ids
        assert "mgr_1" in node_ids
        assert "completion" in node_ids
        # 1 root + 1 manager + 2 decisions + 2 workers + 1 completion
        assert len(payload["nodes"]) == 7
        groups = [n["group"] for n in payload["nodes"]]
        assert groups.count("decision") == 2
        assert groups.count("worker") == 2
        meta = payload["meta"]
        assert meta["chain_verified"] is True
        assert meta["n_steps"] == 2
        assert meta["n_delegated"] == 1
        assert meta["n_local"] == 1
        assert meta["engine"] == "tiered_delegation"
        assert meta["loss_min"] is None and meta["loss_max"] is None  # no loss_recorded here
        completion = next(n for n in payload["nodes"] if n["id"] == "completion")
        assert completion["color"] == "#00E676"  # success: no failed worker

    def test_loss_recorded_feeds_loss_curve_and_worker_color(self):
        run_id = "tde-222-bbbb"
        events = [
            self._ev("tde.engine_selected", 1.0, engine="tiered_delegation",
                     confidence=0.9, tde_run_id=run_id),
            self._ev("tde.delegation_decision", 2.0, step_action="generate_code",
                     delegate=True, reason_code="gates_passed", tde_run_id=run_id, step_num=1),
            self._ev("tde.step_delegated", 3.0, step_action="generate_code",
                     success=True, duration_ms=500, tde_run_id=run_id, step_num=1),
            self._ev("tde.loss_recorded", 3.5, task_type="generate_code",
                     engine="tiered_delegation", loss_pct=0.04, measured=True,
                     tde_run_id=run_id, step_num=1),
            self._ev("tde.plan_executed", 4.0, step_count=1, batch_count=1,
                     delegated_count=1, local_count=0, tde_run_id=run_id),
        ]
        payload = compute_mod._build_tde_audit_graph(
            run_id, events, chain_verified=True, chain_problems=[],
        )
        assert payload["meta"]["loss_min"] == 0.04
        assert payload["meta"]["loss_max"] == 0.04
        assert payload["meta"]["loss_curve"] == [{"step": 1, "loss": 0.04}]
        worker = next(n for n in payload["nodes"] if n["group"] == "worker")
        assert worker["loss_pct"] == 0.04

    def test_l34_prescan_block_and_step_block_surface_as_red_nodes(self):
        run_id = "tde-333-cccc"
        events = [
            self._ev("tde.l34_blocked", 0.5, scope="prescan",
                     reason_code="prescan_block", tde_run_id=run_id),
            self._ev("tde.engine_selected", 1.0, engine="claude_code",
                     confidence=1.0, tde_run_id=run_id),
            self._ev("tde.delegation_decision", 2.0, step_action="write_file",
                     delegate=False, reason_code="l34_blocked", tde_run_id=run_id, step_num=1),
            self._ev("tde.l34_blocked", 2.1, scope="step",
                     reason_code="classification_exceeded", tde_run_id=run_id, step_num=1),
        ]
        payload = compute_mod._build_tde_audit_graph(
            run_id, events, chain_verified=True, chain_problems=[],
        )
        prescan = next(n for n in payload["nodes"] if n["id"] == "l34_prescan_block")
        assert prescan["color"] == "#FF1744"
        decision = next(n for n in payload["nodes"] if n["group"] == "decision")
        assert decision["l34_blocked"] is True
        assert decision["color"] == "#FF1744"

    def test_broken_chain_meta_reflects_caller(self):
        run_id = "tde-444-dddd"
        events = [
            self._ev("tde.engine_selected", 1.0, engine="tiered_delegation",
                     confidence=0.9, tde_run_id=run_id),
        ]
        payload = compute_mod._build_tde_audit_graph(
            run_id, events, chain_verified=False,
            chain_problems=[{"line": 3, "issue": "tampered"}],
        )
        assert payload["meta"]["chain_verified"] is False
        assert payload["meta"]["chain_problems"] == [{"line": 3, "issue": "tampered"}]


class TestRouteEndToEnd:
    """Real hash-chained audit.jsonl on disk -> full route handler."""

    def _call(self, run_id: str, monkeypatch, path: Path):
        monkeypatch.setattr(compute_mod, "_tde_audit_path", lambda: path)
        monkeypatch.setattr(compute_mod, "_TDE_AUDIT_OK", True)
        monkeypatch.setattr(compute_mod.console_audit, "action_performed", lambda **k: None)
        rec = _fake_session_record()
        return compute_mod.compute_tde_audit_graph(run_id, rec)

    def test_normal_turn_end_to_end(self, tmp_path, monkeypatch):
        path = tmp_path / "audit.jsonl"
        run_id = "tde-555-eeee"
        _write_normal_turn(path, run_id)
        payload = self._call(run_id, monkeypatch, path)
        assert payload["meta"]["chain_verified"] is True
        assert payload["meta"]["n_delegated"] == 1
        assert payload["meta"]["n_local"] == 1
        assert not payload["meta"]["chain_problems"]

    def test_unknown_run_id_404(self, tmp_path, monkeypatch):
        path = tmp_path / "audit.jsonl"
        _write_normal_turn(path, "tde-555-eeee")
        with pytest.raises(HTTPException) as ei:
            self._call("tde-does-not-exist", monkeypatch, path)
        assert ei.value.status_code == 404

    def test_invalid_run_id_400(self, tmp_path, monkeypatch):
        path = tmp_path / "audit.jsonl"
        with pytest.raises(HTTPException) as ei:
            self._call("../etc/passwd", monkeypatch, path)
        assert ei.value.status_code == 400

    def test_tampered_record_in_run_breaks_chain_verified(self, tmp_path, monkeypatch):
        """The BROKEN-CHAIN case: tamper one of this run's own records after
        writing (content changed, hash/prev_hash left stale) — verify_chain
        must report a problem at that line, and it must fall inside this
        run's own [lo, hi] line range so meta.chain_verified flips False."""
        path = tmp_path / "audit.jsonl"
        run_id = "tde-666-ffff"
        _write_normal_turn(path, run_id)

        lines = path.read_text().splitlines()
        # Tamper the delegation_decision record for step 1 (line index 1,
        # 0-based) — NOT the last line of the run, so the resulting
        # broken_chain problem (surfacing on the next record) stays inside
        # this run's own matched line range.
        target_idx = 1
        rec = json.loads(lines[target_idx])
        assert rec["event_type"] == "tde.delegation_decision"
        rec["details"]["delegate"] = False  # flip content post-hoc, hash left stale
        lines[target_idx] = json.dumps(rec)
        path.write_text("\n".join(lines) + "\n")

        payload = self._call(run_id, monkeypatch, path)
        assert payload["meta"]["chain_verified"] is False
        assert payload["meta"]["chain_problems"]

    def test_tamper_outside_run_does_not_affect_this_run(self, tmp_path, monkeypatch):
        """A tamper in a DIFFERENT run's segment of the same shared chain must
        not falsely mark THIS run's graph as broken (local-segment scoping)."""
        path = tmp_path / "audit.jsonl"
        run_a = "tde-777-1111"
        run_b = "tde-777-2222"
        _write_normal_turn(path, run_a, base_ts=1_000.0)
        _write_normal_turn(path, run_b, base_ts=2_000.0)

        lines = path.read_text().splitlines()
        # Tamper a record belonging to run_b only.
        target_idx = 8  # second record of run_b's block (delegation_decision)
        rec = json.loads(lines[target_idx])
        assert rec["details"].get("tde_run_id") == run_b
        rec["details"]["delegate"] = not rec["details"]["delegate"]
        lines[target_idx] = json.dumps(rec)
        path.write_text("\n".join(lines) + "\n")

        payload_a = self._call(run_a, monkeypatch, path)
        assert payload_a["meta"]["chain_verified"] is True

        payload_b = self._call(run_b, monkeypatch, path)
        assert payload_b["meta"]["chain_verified"] is False

    def test_tamper_outside_run_surfaces_in_global_verdict(self, tmp_path, monkeypatch):
        """Local-segment scoping must not HIDE a broken chain: the whole-chain
        verdict rides along in meta.chain_verified_global (review 2026-07-24 —
        prev-hash linkage is transitive, a break before this segment
        un-anchors it)."""
        path = tmp_path / "audit.jsonl"
        run_a = "tde-888-1111"
        run_b = "tde-888-2222"
        _write_normal_turn(path, run_a, base_ts=1_000.0)
        _write_normal_turn(path, run_b, base_ts=2_000.0)

        lines = path.read_text().splitlines()
        rec = json.loads(lines[2])  # run_a's segment
        assert rec["details"].get("tde_run_id") == run_a
        rec["details"]["success"] = not rec["details"]["success"]
        lines[2] = json.dumps(rec)
        path.write_text("\n".join(lines) + "\n")

        payload_b = self._call(run_b, monkeypatch, path)
        assert payload_b["meta"]["chain_verified"] is True          # own segment intact
        assert payload_b["meta"]["chain_verified_global"] is False  # chain as a whole is not
        assert payload_b["meta"]["chain_problems_total"] >= 1

    def test_cross_tenant_run_is_404(self, tmp_path, monkeypatch):
        """ADR-0007: another tenant's run must be indistinguishable from a
        nonexistent one (fail-closed 404, no existence oracle)."""
        path = tmp_path / "audit.jsonl"
        run_id = "tde-999-aaaa"
        _write_normal_turn(path, run_id, tenant_id="other-tenant")
        with pytest.raises(HTTPException) as exc_info:
            self._call(run_id, monkeypatch, path)  # caller tenant: _default
        assert exc_info.value.status_code == 404

    def test_unstamped_legacy_run_is_404(self, tmp_path, monkeypatch):
        """Records without a tenant stamp (pre-2026-07-24 runs, standalone
        bench) are never served to ANY tenant — deny-by-default."""
        path = tmp_path / "audit.jsonl"
        run_id = "tde-999-bbbb"
        _write_normal_turn(path, run_id, tenant_id="")
        # Strip the stamp entirely to mimic legacy records.
        lines = path.read_text().splitlines()
        out = []
        for line in lines:
            rec = json.loads(line)
            rec["details"].pop("tenant_id", None)
            out.append(json.dumps(rec))
        # NOTE: rewriting breaks the hash chain, but the tenant filter runs
        # BEFORE chain verification and must 404 first.
        path.write_text("\n".join(out) + "\n")
        with pytest.raises(HTTPException) as exc_info:
            self._call(run_id, monkeypatch, path)
        assert exc_info.value.status_code == 404
