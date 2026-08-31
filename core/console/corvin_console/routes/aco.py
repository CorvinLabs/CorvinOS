"""ACO REST endpoints — ADR-0174.

GET  /chat/sessions/{sid}/aco/anomalies   — Layer 3 anomaly scan
GET  /chat/sessions/{sid}/aco/diagnosis   — Layer 3+4 full diagnosis
POST /chat/sessions/{sid}/aco/replay      — Layer 2 manifest validation against log
POST /chat/sessions/{sid}/aco/repair      — Layer 5 self-repair (opt-in)

The replay endpoint does NOT send new messages; it validates the existing
chat_debug.jsonl against a manifest's expected event sequences. The caller
(e.g. Playwright) is responsible for sending the actual user messages
before calling this endpoint.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from starlette import status as http_status

from .. import audit as console_audit
from .. import chat_runtime
from .. import auth as session_auth
from ..deps import require_csrf, require_session  # noqa: F401 (used via Depends)

router = APIRouter()


# ── Layer 3: Anomaly scan ─────────────────────────────────────────────────────

@router.get("/chat/sessions/{sid}/aco/anomalies")
def get_session_anomalies(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
) -> dict[str, Any]:
    """Return anomaly scan results for a session (Layer 3)."""
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="aco.anomaly_scan",
        target_kind="chat_session",
        target_id=sid,
    )
    from ..aco.anomaly_detector import scan_session_to_dict
    return {"ok": True, "sid": sid, **scan_session_to_dict(sess.workdir)}


# ── Layer 4: Full diagnosis ───────────────────────────────────────────────────

@router.get("/chat/sessions/{sid}/aco/diagnosis")
def get_session_diagnosis(
    sid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
) -> dict[str, Any]:
    """Return anomaly scan + root-cause diagnosis for CRITICAL/HIGH (Layer 3+4)."""
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="aco.diagnosis",
        target_kind="chat_session",
        target_id=sid,
    )
    from ..aco.diagnosis import diagnose_session
    return {"ok": True, "sid": sid, **diagnose_session(sess.workdir)}


# ── Layer 2: Replay manifest validation ──────────────────────────────────────

@router.post("/chat/sessions/{sid}/aco/replay")
def validate_replay(
    sid: str,
    body: dict,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
) -> dict[str, Any]:
    """Validate a replay manifest against the session's existing chat_debug.jsonl.

    This endpoint does NOT send messages — it analyses the turn event sequences
    already recorded in the debug log. The caller (Playwright / test harness)
    must send the actual user messages first and then call this endpoint.

    Request body: ReplayManifest dict
    {
      "version": 1,
      "scenario": "basic-turn",
      "description": "...",
      "turns": [
        {
          "input": "Hello",                           // matched by prompt_preview
          "expect_events": ["turn.start", "turn.done"],
          "expect_fields": {"event": "turn.done", "rc": 0},
          "max_elapsed_ms": 60000
        }
      ]
    }
    """
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="aco.replay_validate",
        target_kind="chat_session",
        target_id=sid,
    )

    from ..aco.replay import ReplayManifest
    from ..aco.events import read_session_log

    try:
        manifest = ReplayManifest.from_dict(body)
    except (KeyError, TypeError) as exc:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    _MAX_REPLAY_TURNS = 100
    if not manifest.turns:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            "manifest must have at least one turn",
        )
    if len(manifest.turns) > _MAX_REPLAY_TURNS:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"manifest exceeds {_MAX_REPLAY_TURNS}-turn limit",
        )

    events = read_session_log(sess.workdir)

    # Find turn windows in the log: each turn.start→turn.done pair
    turn_windows: list[list[dict]] = []
    current: list[dict] | None = None
    for ev in events:
        if ev.get("event") == "turn.start":
            current = [ev]
        elif current is not None:
            current.append(ev)
            if ev.get("event") == "turn.done":
                turn_windows.append(current)
                current = None
    # Incomplete final turn (no turn.done yet)
    if current:
        turn_windows.append(current)

    turn_results: list[dict] = []
    for idx, expectation in enumerate(manifest.turns):
        # Match this expectation against the turn window at the same index
        # (sequential order assumption: replay order = log order)
        if idx >= len(turn_windows):
            turn_results.append({
                "turn_index": idx,
                "input_preview": expectation.input[:80],
                "passed": False,
                "error": f"Turn {idx} not found in log (only {len(turn_windows)} turns recorded)",
                "missing_events": expectation.expect_events,
                "missing_fields": [],
                "elapsed_ms": None,
            })
            continue

        window = turn_windows[idx]

        # Check expected events
        found_events = {e.get("event", "") for e in window}
        missing_ev = [ev for ev in expectation.expect_events if ev not in found_events]

        # Check expected fields
        missing_fld: list[str] = []
        if expectation.expect_fields:
            target_event = expectation.expect_fields.get("event", "")
            other_fields = {k: v for k, v in expectation.expect_fields.items()
                           if k != "event"}
            candidates = [e for e in window
                         if not target_event or e.get("event") == target_event]
            matched = any(
                all(c.get(k) == v for k, v in other_fields.items())
                for c in candidates
            )
            if not matched:
                missing_fld = [f"{k}={v!r}" for k, v in expectation.expect_fields.items()]

        # Check elapsed
        start = window[0] if window else {}
        done = next((e for e in window if e.get("event") == "turn.done"), None)
        elapsed_ms = done.get("elapsed_ms") if done else None
        elapsed_error = (
            f"elapsed_ms={elapsed_ms} exceeds max {expectation.max_elapsed_ms}"
            if isinstance(elapsed_ms, int) and elapsed_ms > expectation.max_elapsed_ms
            else ""
        )

        passed = not missing_ev and not missing_fld and not elapsed_error
        turn_results.append({
            "turn_index": idx,
            "input_preview": expectation.input[:80],
            "passed": passed,
            "error": elapsed_error,
            "missing_events": missing_ev,
            "missing_fields": missing_fld,
            "elapsed_ms": elapsed_ms,
        })

    all_passed = all(t["passed"] for t in turn_results)
    return {
        "ok": True,
        "sid": sid,
        "scenario": manifest.scenario,
        "passed": all_passed,
        "summary": f"{manifest.scenario}: {sum(t['passed'] for t in turn_results)}/{len(turn_results)} turns passed",
        "turns_in_log": len(turn_windows),
        "turns": turn_results,
    }


# ── Layer 5: Self-Repair ──────────────────────────────────────────────────────

@router.post("/chat/sessions/{sid}/aco/repair")
def apply_repair(
    sid: str,
    body: dict,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
) -> dict[str, Any]:
    """Apply Layer 5 self-repair actions to a session (opt-in, ADR-0174).

    Runs the repair engine against all HIGH/CRITICAL anomalies found in the
    session's chat_debug.jsonl and annotates repaired anomalies with repair.*
    events so that subsequent Layer 3 scans reflect the corrected state.

    Returns before/after anomaly counts, delta_loss, and convergence_reached
    so the caller can measure whether repair achieved convergence.

    Request body (all optional):
        {"dry_run": true}   — compute what would be done, write nothing
    """
    sess = chat_runtime.get_session(rec.tenant_id, sid)
    if sess is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "session not found")

    dry_run = bool(body.get("dry_run", False))

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="aco.repair_dry_run" if dry_run else "aco.repair_applied",
        target_kind="chat_session",
        target_id=sid,
    )

    from ..aco.repair import repair_session
    result = repair_session(sess.workdir, dry_run=dry_run)
    return {"ok": True, "sid": sid, **result.to_dict()}


# ── Nervous System (ADR-0177) ─────────────────────────────────────────────────

@router.get("/aco/nerve/scan")
def get_nerve_scan(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
) -> dict[str, Any]:
    """Trigger a full nervous-system scan and return the results (ADR-0177).

    Runs NerveRegistry.scan_all() — Tier-0 built-in fibers plus any
    registered entry-point (Tier-1) or local ~/.corvin/nerve_fibers/ (Tier-2)
    plugins — and returns:

      fibers     — list of registered fiber descriptors
      summary    — counts per severity + list of fibers that need repair
      signals    — flat list of NerveSignal dicts (CRITICAL+HIGH only for audit)
      ok         — True when no CRITICAL signals are present

    This endpoint is operator-only (requires an authenticated session) and
    writes an aco.nerve_scan audit event for every call.
    """
    from ..aco.nerve import NerveRegistry, summarize_signals

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="aco.nerve_scan",
        target_kind="system",
        target_id="nervous_system",
    )

    try:
        signals = NerveRegistry.scan_all()
    except Exception as exc:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Nervous-system scan failed: {exc}",
        )

    summary = summarize_signals(signals)
    fibers = NerveRegistry.list_fibers()

    # Surface all signals in the API; the frontend/caller decides what to show
    signal_dicts = [s.to_dict() for s in signals]

    return {
        "ok": summary["critical"] == 0,
        "fibers": fibers,
        "summary": summary,
        "signals": signal_dicts,
    }


@router.post("/aco/nerve/repair")
def trigger_nerve_repair(
    body: dict,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
) -> dict[str, Any]:
    """Trigger repair for HIGH/CRITICAL nerve signals (ADR-0177).

    Runs NerveRegistry.scan_all() followed by NerveRegistry.repair_all()
    for each signal that needs repair. Returns the repaired signal list.

    Request body (all optional):
        {"dry_run": true}  — scan only, do not call repair() on fibers
    """
    from ..aco.nerve import NerveRegistry, summarize_signals

    dry_run = bool(body.get("dry_run", False))

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="aco.nerve_repair_dry_run" if dry_run else "aco.nerve_repair",
        target_kind="system",
        target_id="nervous_system",
    )

    signals = NerveRegistry.scan_all()
    summary_before = summarize_signals(signals)

    repair_results: list[dict] = []
    if not dry_run:
        repaired = NerveRegistry.repair_all(signals)
        repair_results = [s.to_dict() for s in repaired]

    return {
        "ok": True,
        "dry_run": dry_run,
        "summary_before": summary_before,
        "repaired": repair_results,
    }
