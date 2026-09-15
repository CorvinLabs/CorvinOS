"""Per-subtask E2E — ADR-0020 Phase 30.2c + 30.2f.

Covers the drift-detection layer end-to-end:

  * `detect_drift` pure-Python (cold-start / no-data / drift / no-drift)
  * `emit_drift_event` audit-emission contract (drifted only)
  * `load_drift_policy_for_tenant` defaults + opt-in
  * `evaluate_drift_for_spawn` (engine_trust.py) — passes/blocks
  * Adapter `_check_engine_trust_or_fail` — drift-refusal returned,
    audit lands, fail-open paths preserved.

Layer 30.2 is the trend-monitor; the spawn-time gate is opt-in. The
default behaviour for a tenant without `auto_block_on_drift: true`
must be byte-identical to Phase 30.1b: drift events land in the
chain (forensic), but the spawn proceeds.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "voice" / "scripts"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _fresh_modules():
    for m in ("engine_canary", "engine_trust", "adapter"):
        sys.modules.pop(m, None)
    ec = importlib.import_module("engine_canary")
    et = importlib.import_module("engine_trust")
    ad = importlib.import_module("adapter")
    return ec, et, ad


def _record(date_offset_days: int, score: float):
    """Build a ScoreRecord in the past by ``date_offset_days``."""
    from engine_canary import ScoreRecord  # late import after sys.path setup
    d = (date_cls.today() + timedelta(days=date_offset_days)).strftime("%Y-%m-%d")
    return ScoreRecord(date=d, score=score, passed=10, failed=0,
                       unparseable=0, wall_clock_s=1.0)


def _write_tenant_config(tmp: Path, *, et_block: dict[str, Any] | None) -> None:
    """Stand up <corvin_home>/tenants/_default/global/tenant.corvin.yaml."""
    p = tmp / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "apiVersion": "corvin/v1",
        "kind": "Tenant",
        "metadata": {"id": "_default"},
        "spec": {},
    }
    if et_block is not None:
        body["spec"]["engine_trust"] = et_block
    import yaml as _y
    p.write_text(_y.safe_dump(body))


def _write_scores(tmp: Path, engine_id: str, klass: str,
                  records: list) -> None:
    """Write a scores.json with the provided per-class history."""
    p = tmp / "global" / "engine_canary" / "scores.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {engine_id: {klass: [r.__dict__ for r in records]}}
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, 0o600)


class _StubEngine:
    def __init__(self, name: str) -> None:
        self.name = name


# ---------------------------------------------------------------------------
# Section 1 — detect_drift pure logic
# ---------------------------------------------------------------------------


def section_detect_drift() -> None:
    print("\n[1/5] detect_drift — pure logic")
    ec, _, _ = _fresh_modules()

    # 1a — empty records → no-data
    v = ec.detect_drift([], engine_id="x", klass="harmful_content",
                         min_window_days=7)
    t("empty records → no-data",
      (not v.drifted) and v.reason == "no-data",
      detail=v.reason)

    # 1b — Too few records → cold-start (no false-positive)
    recs = [_record(-i, 0.95) for i in range(3)]
    v = ec.detect_drift(recs, engine_id="x", klass="harmful_content",
                         min_window_days=7)
    t("3 records < 8 needed → cold-start",
      (not v.drifted) and v.reason == "cold-start",
      detail=v.reason)

    # 1c — Sufficient records, no drift → ok
    recs = [_record(-i, 0.95) for i in range(8)]
    v = ec.detect_drift(recs, engine_id="x", klass="harmful_content",
                         min_window_days=7)
    t("stable 0.95 score → ok (no drift)",
      (not v.drifted) and v.reason == "ok",
      detail=f"{v.reason}, delta={v.delta_pct_points}")

    # 1d — Drift: latest dropped from baseline 0.95 → 0.70 (delta 0.25)
    recs = [_record(0, 0.70)] + [_record(-i, 0.95) for i in range(1, 8)]
    v = ec.detect_drift(recs, engine_id="x", klass="harmful_content",
                         min_window_days=7, alert_delta=0.10)
    t("score 0.95 → 0.70 (delta 0.25 ≥ 0.10) → drift-detected",
      v.drifted and v.reason == "drift-detected",
      detail=f"current={v.current_score} baseline={v.baseline_score} delta={v.delta_pct_points}")
    t("verdict carries metadata",
      v.current_score == 0.70 and v.baseline_score == 0.95
      and v.delta_pct_points is not None
      and abs(v.delta_pct_points - 0.25) < 0.001)

    # 1e — Borderline drift (delta < alert_delta) → ok
    recs = [_record(0, 0.93)] + [_record(-i, 0.95) for i in range(1, 8)]
    v = ec.detect_drift(recs, engine_id="x", klass="harmful_content",
                         min_window_days=7, alert_delta=0.10)
    t("delta 0.02 below alert → ok",
      (not v.drifted) and v.reason == "ok")

    # 1f — Improvement (current ≥ baseline) → never drift
    recs = [_record(0, 0.99)] + [_record(-i, 0.85) for i in range(1, 8)]
    v = ec.detect_drift(recs, engine_id="x", klass="harmful_content",
                         min_window_days=7, alert_delta=0.10)
    t("improvement (0.85 → 0.99) → ok (never drift on improvement)",
      (not v.drifted) and v.reason == "ok",
      detail=f"delta={v.delta_pct_points}")

    # 1g — Validation: invalid alert_delta raises
    try:
        ec.detect_drift(recs, engine_id="x", klass="x",
                         alert_delta=2.0)
        t("invalid alert_delta raises", False)
    except ValueError:
        t("invalid alert_delta raises", True)

    try:
        ec.detect_drift(recs, engine_id="x", klass="x",
                         min_window_days=0)
        t("invalid min_window_days raises", False)
    except ValueError:
        t("invalid min_window_days raises", True)


# ---------------------------------------------------------------------------
# Section 2 — emit_drift_event
# ---------------------------------------------------------------------------


def section_emit_drift_event() -> None:
    print("\n[2/5] emit_drift_event — audit emission")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            ec, _, _ = _fresh_modules()

            # 2a — non-drifted → no event
            recs = [_record(-i, 0.95) for i in range(8)]
            v = ec.detect_drift(recs, engine_id="claude_code",
                                 klass="harmful_content")
            ev = ec.emit_drift_event(v)
            t("non-drifted verdict → no event", ev is None)

            audit_p = (Path(tmp) / "tenants" / "_default" / "global" /
                       "forge" / "audit.jsonl")
            t("audit chain not created on non-drift", not audit_p.exists())

            # 2b — drifted → canary_drift_detected emitted
            recs = [_record(0, 0.70)] + [_record(-i, 0.95) for i in range(1, 8)]
            v = ec.detect_drift(recs, engine_id="claude_code",
                                 klass="harmful_content",
                                 alert_delta=0.10)
            ev = ec.emit_drift_event(v)
            t("drifted verdict → canary_drift_detected",
              ev == "engine.canary_drift_detected",
              detail=str(ev))
            t("audit chain file created", audit_p.exists())
            if audit_p.exists():
                lines = [json.loads(line) for line in
                         audit_p.read_text().splitlines() if line]
                t("chain has one entry", len(lines) == 1)
                ev_rec = lines[0]
                d = ev_rec["details"]
                t("event has engine_id", "engine_id" in d)
                t("event has current_score", "current_score" in d)
                t("event has baseline_score", "baseline_score" in d)
                t("event has delta_pct_points", "delta_pct_points" in d)
                t("event has window_days", "window_days" in d)
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 3 — load_drift_policy_for_tenant
# ---------------------------------------------------------------------------


def section_load_drift_policy() -> None:
    print("\n[3/5] load_drift_policy_for_tenant — defaults + opt-in")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            _, et, _ = _fresh_modules()

            # 3a — no tenant config → permissive defaults
            p = et.load_drift_policy_for_tenant("_default")
            t("no config → auto_block_on_drift=False",
              p["auto_block_on_drift"] is False)
            t("no config → alert_delta=0.10",
              p["canary_alert_delta"] == 0.10)
            t("no config → min_window_days=7",
              p["canary_min_window_days"] == 7)

            # 3b — config with engine_trust block but defaults
            _write_tenant_config(Path(tmp), et_block={"min_tier": "low"})
            p = et.load_drift_policy_for_tenant("_default")
            t("engine_trust without drift fields → defaults",
              p["auto_block_on_drift"] is False
              and p["canary_alert_delta"] == 0.10
              and p["canary_min_window_days"] == 7)

            # 3c — explicit opt-in
            _write_tenant_config(Path(tmp), et_block={
                "auto_block_on_drift": True,
                "canary_alert_delta": 0.20,
                "canary_min_window_days": 14,
            })
            p = et.load_drift_policy_for_tenant("_default")
            t("opt-in auto_block_on_drift",
              p["auto_block_on_drift"] is True)
            t("custom alert_delta",
              p["canary_alert_delta"] == 0.20)
            t("custom window_days",
              p["canary_min_window_days"] == 14)

            # 3d — invalid alert_delta in config → falls back to default
            _write_tenant_config(Path(tmp), et_block={
                "canary_alert_delta": "bogus",
            })
            p = et.load_drift_policy_for_tenant("_default")
            t("invalid alert_delta → fallback 0.10",
              p["canary_alert_delta"] == 0.10)
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 4 — evaluate_drift_for_spawn
# ---------------------------------------------------------------------------


def section_evaluate_drift_for_spawn() -> None:
    print("\n[4/5] evaluate_drift_for_spawn — gate semantics")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            ec, et, _ = _fresh_modules()

            # 4a — no scores at all → fail-open passes
            v = et.evaluate_drift_for_spawn("claude_code")
            t("no scores → passes (fail-open)", v.passed)
            t("no scores → enforced=False (no opt-in)",
              v.enforced is False)

            # 4b — drift exists, no opt-in → passes (audit-only mode)
            recs = [_record(0, 0.70)] + [_record(-i, 0.95) for i in range(1, 8)]
            _write_scores(Path(tmp), "claude_code", "harmful_content", recs)
            v = et.evaluate_drift_for_spawn("claude_code")
            t("drift exists, no opt-in → passes",
              v.passed,
              detail=f"enforced={v.enforced}, drifted={v.drifted_classes}")
            t("drifted classes still surfaced",
              "harmful_content" in v.drifted_classes)

            # 4c — drift exists, opt-in → blocks
            _write_tenant_config(Path(tmp), et_block={
                "auto_block_on_drift": True,
                "canary_alert_delta": 0.10,
                "canary_min_window_days": 7,
            })
            v = et.evaluate_drift_for_spawn("claude_code")
            t("drift + opt-in → blocks",
              (not v.passed) and v.enforced
              and "harmful_content" in v.drifted_classes,
              detail=f"passed={v.passed}, enforced={v.enforced}")

            # 4d — opt-in but no drift (stable) → passes
            stable = [_record(-i, 0.95) for i in range(8)]
            _write_scores(Path(tmp), "claude_code", "harmful_content", stable)
            v = et.evaluate_drift_for_spawn("claude_code")
            t("opt-in but stable scores → passes", v.passed)

            # 4e — audit emitted on drift (regardless of enforcement)
            audit_p = (Path(tmp) / "tenants" / "_default" / "global" /
                       "forge" / "audit.jsonl")
            # reset for clean count
            if audit_p.exists():
                prior = len(audit_p.read_text().splitlines())
            else:
                prior = 0
            _write_scores(Path(tmp), "claude_code", "harmful_content", recs)
            et.evaluate_drift_for_spawn("claude_code")
            after = (len(audit_p.read_text().splitlines())
                     if audit_p.exists() else 0)
            t("drift audit emission per spawn",
              after > prior,
              detail=f"prior={prior} after={after}")
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 5 — adapter wiring (full path through _check_engine_trust_or_fail)
# ---------------------------------------------------------------------------


def section_adapter_wiring() -> None:
    print("\n[5/5] adapter wiring — _check_engine_trust_or_fail")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            ec, et, ad = _fresh_modules()

            # 5a — Phase 30.1b path still intact: tier passes + no drift → None
            engine = _StubEngine("claude_code")
            r = ad._check_engine_trust_or_fail(
                engine, channel="t", chat_key="c1")
            t("baseline (no scores, no policy) → None",
              r is None,
              detail=str(r)[:80] if r else "None")

            # 5b — auto_block_on_drift=true + drift → German refusal
            recs = [_record(0, 0.60)] + [_record(-i, 0.95) for i in range(1, 8)]
            _write_scores(Path(tmp), "claude_code", "harmful_content", recs)
            _write_tenant_config(Path(tmp), et_block={
                "auto_block_on_drift": True,
            })
            ec, et, ad = _fresh_modules()
            r = ad._check_engine_trust_or_fail(
                _StubEngine("claude_code"),
                channel="t", chat_key="c1")
            t("drift + opt-in → user-facing refusal",
              isinstance(r, str) and "[engine-trust]" in r and "drift" in r.lower(),
              detail=str(r)[:80] if r else "None")
            t("refusal mentions auto_block context",
              "auto_block" in (r or "") or "drift" in (r or "").lower(),
              detail=str(r)[:80] if r else "")

            # 5c — auto_block_on_drift=false + drift → spawn proceeds
            _write_tenant_config(Path(tmp), et_block={
                "auto_block_on_drift": False,
            })
            ec, et, ad = _fresh_modules()
            r = ad._check_engine_trust_or_fail(
                _StubEngine("claude_code"),
                channel="t", chat_key="c1")
            t("drift + opt-out (default) → spawn proceeds",
              r is None,
              detail=str(r)[:80] if r else "None")

            # 5d — tier-violation still beats drift (ordering correctness)
            _write_tenant_config(Path(tmp), et_block={
                "min_tier": "high",
                "auto_block_on_drift": True,
            })
            ec, et, ad = _fresh_modules()
            # opencode is tier=low → fails tier first
            r = ad._check_engine_trust_or_fail(
                _StubEngine("opencode"),
                channel="t", chat_key="c1")
            t("tier-violation beats drift in ordering",
              isinstance(r, str)
              and ("Mindestvertrauensstufe" in r or "minimum trust tier" in r or "does not meet" in r),
              detail=str(r)[:80] if r else "None")
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("test_engine_trust_drift.py — ADR-0020 Phase 30.2c + 30.2f")
    print("=" * 60)

    section_detect_drift()
    section_emit_drift_event()
    section_load_drift_policy()
    section_evaluate_drift_for_spawn()
    section_adapter_wiring()

    print()
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
