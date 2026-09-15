"""
Model Cost Optimizer API — ADR-0377 Phase 2b, renamed ADR-0696

REST API endpoints for learned threshold management and operator controls.

Endpoints:
  GET  /v1/console/learning/model-cost-optimizer/status    → Dashboard status (convergence, cost, quality)
  POST /v1/console/learning/model-cost-optimizer/override   → Manual threshold override (not exposed in the UI — see ADR-0696, the value is recomputed from real data on every refresh anyway)
  POST /v1/console/learning/model-cost-optimizer/reset      → Reset all learning
  GET  /v1/console/learning/model-cost-optimizer/export     → Export learned thresholds as JSON
  POST /v1/console/learning/model-cost-optimizer/import     → Import thresholds from JSON

Auth: requires session (tenant isolation enforced via rec.tenant_id)
Audit: all actions logged to audit trail

Constraints (ADR-0377 Phase 2b):
- Audit-first: every threshold change logged before persisting
- Per-tenant isolation: all queries filter by tenant_id
- Fail-safe: graceful degradation if store unavailable
"""

from __future__ import annotations

import logging
import json
from typing import Annotated, Any, Dict, List, Optional
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status, UploadFile, File
from pydantic import BaseModel

try:
    from core.learning.learned_threshold_store import get_store, StoredThreshold
    from core.learning.cost_variance_optimizer import get_optimizer
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.learning.learned_threshold_store import get_store, StoredThreshold
    from core.learning.cost_variance_optimizer import get_optimizer

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/learning/model-cost-optimizer", tags=["model-cost-optimizer"])


# ── Pydantic models for requests/responses ──────────────────────────────

class ThresholdStatus(BaseModel):
    """Single threshold status."""
    task_type: str
    subsystem: str
    learned_threshold: float
    base_threshold: float
    sample_count: int
    converged: bool
    # Real share of this bucket's turns that exited 0 without a timeout —
    # completion reliability, NOT a content-quality assessment.
    success_rate: float = 0.0
    timestamp: str
    # What this tier actually costs. The threshold above is an internal
    # parameter with no live consumer; volume, reliability and spend are what an
    # operator acts on.
    dominant_model: str = ""
    actual_usd: float = 0.0
    baseline_usd: float = 0.0
    priced_turns: int = 0


class DashboardStatusResponse(BaseModel):
    """Dashboard status response."""
    converged_count: int
    total_count: int
    thresholds: List[ThresholdStatus]
    cost_savings_percent: float
    cost_baseline_usd: float
    cost_current_usd: float
    accuracy_percent: float
    last_updated: str
    history: Optional[List[Dict[str, Any]]] = None
    cost_data_available: bool = False
    cost_history: List[Dict[str, Any]] = []
    # ACS-delegated worker spend — the substantive-work path (full tool
    # access), tracked entirely separately from the OS-turn numbers above.
    # Zero when no acs.engine_completed event has usable token data.
    acs_cost_actual_usd: float = 0.0
    acs_cost_baseline_usd: float = 0.0
    acs_model_mix: Dict[str, int] = {}
    # Whether ANY acs.engine_completed event with usable token data was found.
    # Load-bearing for honesty: without it the UI cannot tell "workers cost
    # $0.00" from "no worker turn was ever recorded", and the per-day
    # acs_*_usd zeros below plot as a flat line that reads as the former.
    acs_data_available: bool = False
    # Real per-model turn counts for every counted os_turn.completed event
    # (e.g. {"claude-haiku-4-5-20251001": 244}). A single-key mix means
    # cost_savings_percent is just that model's fixed price ratio against the
    # baseline model — a pricing fact, not a routing achievement — so the UI
    # can say so instead of presenting it as an optimization result.
    cost_model_mix: Dict[str, int] = {}
    # How many os_turn.completed events the cost totals above actually rest on,
    # against how many were seen at all. These diverge hard in practice (live
    # 2026-09-15: 154 of 391 — turns before the ADR-0696 token emitter carry no
    # counts), and a $ total quoted without that ratio reads as the full bill
    # when it is 39% of it.
    cost_counted_turns: int = 0
    cost_total_turns: int = 0
    # The tenant's explicit spec.engine_models.claude_code.os_model pin, when
    # set (None = adaptive default). NOT a license/tier thing — no license
    # check exists in the model-selection path; a single-model cost_model_mix
    # is explained by THIS, when set, not by license tier.
    cost_os_model_pin: Optional[str] = None
    # ── Delegated WORKER turns, same shape as the OS fields above (ADR-0760).
    # They existed only as two dollar totals, so the panel could draw a worker
    # line but could not say which models produced it, how many turns it rests
    # on, or what it saved — while the OS block beside it said all three. Same
    # question, same answer shape, for the half an operator is usually paying
    # for.
    acs_counted_turns: int = 0
    acs_total_turns: int = 0
    acs_savings_percent: float = 0.0
    acs_worker_model_pin: Optional[str] = None
    # ADR-0761 — per-model dollars for both sources:
    # {model_id: {actual_usd, baseline_usd, turns}}. The mix fields above say
    # which models ran; these say what each one cost against the same-token Opus
    # reference, which is what lets the panel show WHERE the saving came from
    # instead of only that there was one.
    cost_model_cost: Dict[str, Dict[str, float]] = {}
    acs_model_cost: Dict[str, Dict[str, float]] = {}
    # ── OS + worker together. Neither number alone is "what this install
    # saved": the OS series is the cheap orchestration layer and the worker
    # series is the substantive work. Derived from the two REAL totals, never
    # from averaging two percentages (which would weight a 3-turn worker series
    # like a 500-turn OS one).
    combined_actual_usd: float = 0.0
    combined_baseline_usd: float = 0.0
    combined_savings_percent: float = 0.0
    combined_data_available: bool = False
    # ── ADR-0760 counting window. Present in the SAME payload as every number
    # it narrows, so a caller cannot render a total without the period it
    # covers. {"active": false} = all-time.
    window: Dict[str, Any] = {}


class UsageEpochRequest(BaseModel):
    """Reset (or clear) the counting window."""
    reason: str = "Operator counter reset"
    #: true = drop the epoch and return to the full all-time view. History was
    #: never deleted, so this genuinely restores it.
    clear: bool = False


class OverrideRequest(BaseModel):
    """Manual threshold override request."""
    task_type: str
    new_threshold: float
    reason: str = "Manual override"


class ResetRequest(BaseModel):
    """Reset learning request."""
    reason: str = "Operator manual reset"


class ImportRequest(BaseModel):
    """Import thresholds request."""
    data: Dict[str, Any]
    reason: str = "Operator import"


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/status", response_model=DashboardStatusResponse)
async def get_learning_status(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get Model Cost Optimizer dashboard status.

    Returns:
        converged_count: number of converged task types
        total_count: total task types tracked
        thresholds: list of learned thresholds
        cost_savings_percent: real savings %, actual model mix vs. an
            always-Opus baseline, computed from real os_turn.completed
            token usage (ADR-0696); 0.0 when cost_data_available is False
        cost_baseline_usd: real total $ if every turn had used the
            reference top-tier model, from real token counts
        cost_current_usd: real total $ actually spent, from real token
            counts x the real price of the model actually used per turn
        cost_data_available: False when no os_turn.completed event in the
            scanned window has both a recognized model and real token
            counts (e.g. events from before ADR-0696 shipped) — the
            "insufficient data" state, distinct from real zero-cost data
        cost_history: real daily [{date, actual_usd, baseline_usd, counted_turns,
            total_turns}] series, empty when cost_data_available is False.
            counted_turns/total_turns expose coverage per day (how many of
            that day's completed turns actually had usable token data) so a
            thin bar reads as "sparse data", never as a real cost crash.
        cost_model_mix: real per-model turn counts across every counted turn
            (e.g. {"claude-haiku-4-5-20251001": 244}). A single-key mix means
            cost_savings_percent is just that model's fixed price ratio
            against the baseline model — a pricing fact, not evidence of a
            routing decision (live finding 2026-09-13: this tenant's traffic
            has been 100% one model since recording began, so savings_percent
            has been mathematically constant, never a measurement).
        cost_os_model_pin: the tenant's explicit
            spec.engine_models.claude_code.os_model value from
            tenant.corvin.yaml, or None when unset (adaptive default). When
            set, it — NOT a license/tier restriction — is why cost_model_mix
            above is a single key: no license/tier check exists anywhere in
            the model-selection code path (model_selector.py,
            engine_models.py), confirmed by direct code search 2026-09-13.
        acs_cost_actual_usd / acs_cost_baseline_usd: real $ for ACS-delegated
            worker turns (full tool-access agentic runs) — the substantive
            work the OS-turn numbers above never cover. A SEPARATE cost
            source, tracked from ``acs.engine_completed`` events, never
            blended into cost_current_usd/cost_baseline_usd.
        acs_model_mix: real per-model turn counts for ACS workers.
        accuracy_percent: real turn-success rate — sample-count-weighted
            average of each threshold bucket's share of turns that exited 0
            without a timeout (see StoredThreshold.success_rate). This is
            completion reliability, NOT a content-quality assessment; no
            content-quality signal exists in this system today.
        last_updated: timestamp
    """
    try:
        # Recompute learned thresholds from real os_turn.* audit events
        # before reading the store (cooldown-gated — see
        # model_selection_learner.py). A refresh failure must not break the
        # dashboard: the store still returns whatever it last had.
        try:
            from core.learning.model_selection_learner import refresh_store
            refresh_store(rec.tenant_id)
        except Exception as refresh_err:  # noqa: BLE001
            logger.warning(f"Learned-threshold refresh failed (non-fatal): {refresh_err}")

        store = get_store(rec.tenant_id)
        optimizer = get_optimizer()

        thresholds = store.get_all()
        converged = sum(1 for t in thresholds if t.converged)
        total = len(thresholds) or 1  # Avoid division by zero

        # Real cost-efficiency: actual token usage x published per-model
        # pricing, from real os_turn.completed events (ADR-0696). A turn
        # with no recognized model or no token counts is excluded rather
        # than estimated — see model_selection_learner.compute_cost_efficiency.
        try:
            from core.learning.model_selection_learner import compute_cost_efficiency
            cost_result = compute_cost_efficiency(rec.tenant_id)
        except Exception as cost_err:  # noqa: BLE001
            logger.warning(f"Cost efficiency computation failed (non-fatal): {cost_err}")
            cost_result = None

        cost_data_available = bool(cost_result and cost_result.has_data)
        cost_baseline = cost_result.total_baseline_usd if cost_data_available else 0.0
        cost_current = cost_result.total_actual_usd if cost_data_available else 0.0
        cost_savings_pct = cost_result.savings_percent if cost_data_available else 0.0

        # 2026-09-13 finding: a single-model mix is NOT a license/tier
        # restriction (no license check exists anywhere in the model-selection
        # code path) — it's this tenant's explicit
        # spec.engine_models.claude_code.os_model pin in tenant.corvin.yaml,
        # which wins over the adaptive Haiku/Sonnet selector. Surface the
        # real cause instead of a generic "no routing" guess.
        os_model_pin: Optional[str] = None
        try:
            import sys
            _bridge_shared = Path(__file__).resolve().parents[4] / "operator" / "bridges" / "shared"
            if str(_bridge_shared) not in sys.path:
                sys.path.insert(0, str(_bridge_shared))
            from engine_models import get_tenant_engine_model  # type: ignore  # noqa: PLC0415
            os_model_pin = get_tenant_engine_model(rec.tenant_id, "claude_code", "os_model")
        except Exception:  # noqa: BLE001 — advisory only, never break the dashboard
            pass

        # ACS-delegated worker spend is a SEPARATE series, never blended into
        # the OS-turn numbers above (see CostEfficiencyResult.acs_daily
        # docstring — two independently-measured cost sources). Its own
        # availability check: ACS can have data on days the OS-turn chain
        # doesn't, and vice versa.
        acs_data_available = bool(cost_result and cost_result.acs_daily)
        acs_by_date = {p.date: p for p in cost_result.acs_daily} if acs_data_available else {}
        os_by_date = {p.date: p for p in cost_result.daily} if cost_data_available else {}
        # A date only earns a point if SOMETHING on it was actually priced.
        # Until 2026-09-15 every date that produced an event joined the series
        # even when not one of its turns carried token data, so the chart drew
        # a flat $0.00 line for days that were merely unmeasured — live
        # finding: 11 of 16 points came from 267 acs.engine_completed events
        # (2026-07-09 .. 2026-09-06) of which ZERO had token counts, reading
        # as "these days were free" when they were "these days were not
        # recorded". An unmeasured day is absent, never a zero.
        all_dates = sorted(
            d
            for d in set(os_by_date) | set(acs_by_date)
            if (d in os_by_date and os_by_date[d].counted_turns > 0)
            or (d in acs_by_date and acs_by_date[d].counted_turns > 0)
        )
        cost_history = (
            [
                {
                    "date": d,
                    "actual_usd": os_by_date[d].actual_usd if d in os_by_date else 0.0,
                    "baseline_usd": os_by_date[d].baseline_usd if d in os_by_date else 0.0,
                    "counted_turns": os_by_date[d].counted_turns if d in os_by_date else 0,
                    "total_turns": os_by_date[d].total_turns if d in os_by_date else 0,
                    "acs_actual_usd": acs_by_date[d].actual_usd if d in acs_by_date else 0.0,
                    "acs_baseline_usd": acs_by_date[d].baseline_usd if d in acs_by_date else 0.0,
                    "acs_counted_turns": acs_by_date[d].counted_turns if d in acs_by_date else 0,
                    "acs_total_turns": acs_by_date[d].total_turns if d in acs_by_date else 0,
                }
                for d in all_dates
            ]
            if (cost_data_available or acs_data_available)
            else []
        )
        cost_counted_turns = sum(p.counted_turns for p in cost_result.daily) if cost_data_available else 0
        cost_total_turns = sum(p.total_turns for p in cost_result.daily) if cost_data_available else 0
        acs_total_actual = cost_result.acs_total_actual_usd if acs_data_available else 0.0
        acs_total_baseline = cost_result.acs_total_baseline_usd if acs_data_available else 0.0
        acs_model_mix = cost_result.acs_model_mix if acs_data_available else {}

        # Real turn-success rate — sample-count-weighted average of each
        # bucket's success_rate (already computed in compute_learned_thresholds
        # from real exit_code/timed_out data). Replaces a fabricated
        # 85+converged*10 placeholder formula that had no connection to any
        # real signal (live finding 2026-09-13).
        weighted_success = sum(t.success_rate * t.sample_count for t in thresholds)
        total_samples = sum(t.sample_count for t in thresholds)
        accuracy = (weighted_success / total_samples * 100) if total_samples > 0 else 0.0

        cost_model_mix = cost_result.model_mix if cost_data_available else {}

        acs_counted_turns = (
            sum(p.counted_turns for p in cost_result.acs_daily) if acs_data_available else 0
        )
        acs_total_turns = (
            sum(p.total_turns for p in cost_result.acs_daily) if acs_data_available else 0
        )
        # Same definition as the OS figure: 1 - actual/baseline against the
        # Opus reference, on the SAME real token counts. Clamped like the OS one
        # because a model priced ABOVE the reference yields a negative saving,
        # which is the honest reading but not what this field means.
        acs_savings_pct = (
            round(max(0.0, min(100.0, (1 - acs_total_actual / acs_total_baseline) * 100)), 2)
            if acs_total_baseline > 0 else 0.0
        )

        combined_actual = round(cost_current + acs_total_actual, 4)
        combined_baseline = round(cost_baseline + acs_total_baseline, 4)
        combined_savings_pct = (
            round(max(0.0, min(100.0, (1 - combined_actual / combined_baseline) * 100)), 2)
            if combined_baseline > 0 else 0.0
        )

        # The worker-model pin, read from the same key the console writes and
        # the gateway spawns with (ADR-0759). A single-key acs_model_mix is
        # explained by THIS when set, exactly as cost_os_model_pin explains a
        # single-key cost_model_mix — without it the UI would have to guess
        # whether one model means "pinned" or "nothing else was ever tried".
        worker_model_pin = None
        try:
            import sys as _sys  # noqa: PLC0415
            from pathlib import Path as _Path  # noqa: PLC0415

            _shared = str(_Path(__file__).resolve().parents[4]
                          / "corvin_operator" / "bridges" / "shared")
            if _shared not in _sys.path:
                _sys.path.insert(0, _shared)
            from engine_models import get_tenant_engine_model  # type: ignore  # noqa: PLC0415

            worker_model_pin = get_tenant_engine_model(
                rec.tenant_id, "claude_code", "worker_model")
        except Exception:  # noqa: BLE001 — a pin we cannot read is reported as absent
            worker_model_pin = None

        try:
            from .. import usage_epoch  # noqa: PLC0415

            window = usage_epoch.window_note(rec.tenant_id)
        except Exception:  # noqa: BLE001
            window = {"active": False, "epoch_ts": None, "since_iso": None, "reason": ""}

        return {
            "converged_count": converged,
            "total_count": total,
            "thresholds": [
                {
                    "task_type": t.task_type,
                    "subsystem": t.subsystem,
                    "learned_threshold": t.learned_threshold,
                    "base_threshold": t.base_threshold,
                    "sample_count": t.sample_count,
                    "converged": t.converged,
                    "success_rate": t.success_rate,
                    "timestamp": t.timestamp,
                    "dominant_model": getattr(t, "dominant_model", "") or "",
                    "actual_usd": getattr(t, "actual_usd", 0.0) or 0.0,
                    "baseline_usd": getattr(t, "baseline_usd", 0.0) or 0.0,
                    "priced_turns": getattr(t, "priced_turns", 0) or 0,
                }
                for t in thresholds
            ],
            "cost_savings_percent": cost_savings_pct,
            "cost_baseline_usd": cost_baseline,
            "cost_current_usd": cost_current,
            "cost_data_available": cost_data_available,
            "cost_history": cost_history,
            "cost_model_mix": cost_model_mix,
            "cost_counted_turns": cost_counted_turns,
            "cost_total_turns": cost_total_turns,
            "cost_os_model_pin": os_model_pin,
            "acs_cost_actual_usd": acs_total_actual,
            "acs_cost_baseline_usd": acs_total_baseline,
            "acs_model_mix": acs_model_mix,
            "acs_data_available": acs_data_available,
            "acs_counted_turns": acs_counted_turns,
            "acs_total_turns": acs_total_turns,
            "acs_savings_percent": acs_savings_pct,
            "acs_worker_model_pin": worker_model_pin,
            "cost_model_cost": cost_result.model_cost if cost_data_available else {},
            "acs_model_cost": cost_result.acs_model_cost if acs_data_available else {},
            "combined_actual_usd": combined_actual,
            "combined_baseline_usd": combined_baseline,
            "combined_savings_percent": combined_savings_pct,
            "combined_data_available": bool(cost_data_available and acs_data_available),
            "window": window,
            "accuracy_percent": accuracy,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get learning status: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get status: {e}",
        )


@router.post("/override")
async def override_threshold(
    req: OverrideRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Manually override a learned threshold.

    Args:
        task_type: task type to override
        new_threshold: new threshold value [0.1, 0.9]
        reason: reason for override (for audit trail)

    Returns:
        status: "ok"
        message: confirmation message
        updated_at: timestamp
    """
    try:
        # Validate input
        if not 0.1 <= req.new_threshold <= 0.9:
            raise ValueError(f"Threshold out of range [0.1, 0.9]: {req.new_threshold}")

        store = get_store(rec.tenant_id)

        # Get old threshold for comparison
        old_threshold = store.get_threshold(req.task_type, "default")

        # Create new stored threshold
        stored = StoredThreshold(
            task_type=req.task_type,
            subsystem="default",
            tenant_id=rec.tenant_id,
            learned_threshold=req.new_threshold,
            base_threshold=0.5,
            timestamp=datetime.now(timezone.utc).isoformat(),
            sample_count=0,  # Reset sample count on manual override
            converged=False,  # Not converged until learning resumes
            notes=f"Manual override: {req.reason}",
        )

        # Store (with audit logging)
        audit_backend = _get_audit_backend(rec.tenant_id)
        store.set_threshold(stored, audit_backend=audit_backend)

        logger.info(
            f"Threshold override: {req.task_type} "
            f"{old_threshold:.3f} → {req.new_threshold:.3f} "
            f"(reason: {req.reason})"
        )

        return {
            "status": "ok",
            "message": f"Threshold for {req.task_type} updated to {req.new_threshold:.3f}",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to override threshold: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to apply override: {e}",
        )


@router.post("/reset")
async def reset_learning(
    req: ResetRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Reset all learned thresholds (operator action).

    Args:
        reason: reason for reset (for audit trail)

    Returns:
        status: "ok"
        message: confirmation message
        reset_at: timestamp
    """
    try:
        store = get_store(rec.tenant_id)
        audit_backend = _get_audit_backend(rec.tenant_id)

        # Reset (with audit logging)
        store.reset_all(
            audit_backend=audit_backend,
            operator_note=req.reason,
        )

        logger.warning(
            f"Learning reset for tenant {rec.tenant_id} (reason: {req.reason})"
        )

        return {
            "status": "ok",
            "message": "All learned thresholds have been reset",
            "reset_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to reset learning: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset learning: {e}",
        )


@router.post("/usage-epoch")
async def reset_usage_window(
    req: UsageEpochRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Start counting turns and dollars from NOW — or clear the window (ADR-0760).

    This is the ONLY supported "reset the counters" action, and it deliberately
    touches no data. The audit chain is append-only, hash-linked, verified by
    the ADR-0232 boot tripwire and is the GDPR Art. 30/32 record; trimming it to
    make two series start level would not reset a counter, it would break the
    chain and fail the next boot. So the chain is untouched and one timestamp
    moves instead. ``clear: true`` removes it and every historical turn is back
    — which is exactly the property that makes this safe to expose.

    Applies to BOTH the turn-share view (``/v1/engine/model-usage``) and the
    dollar view (this panel), because both read the same epoch.
    """
    try:
        from .. import usage_epoch  # noqa: PLC0415

        if req.clear:
            usage_epoch.clear_epoch(rec.tenant_id)
            record: Dict[str, Any] = {}
            action = "learning.usage_window_cleared"
        else:
            record = usage_epoch.set_epoch(
                rec.tenant_id,
                reason=req.reason,
                # A session FINGERPRINT, never a uid or an address (GDPR Art. 5).
                actor=rec.sid_fingerprint,
            )
            action = "learning.usage_window_reset"

        try:
            console_audit.action_performed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action=action,
                target_kind="usage_epoch",
                target_id=str(record.get("epoch_ts") or "cleared"),
            )
        except Exception:  # noqa: BLE001 — the reset itself already committed
            logger.warning("usage-epoch reset could not be audited", exc_info=True)

        return {
            "status": "ok",
            "window": usage_epoch.window_note(rec.tenant_id),
            # Said plainly in the response, because the one thing an operator
            # must not believe about this button is that it deleted anything.
            "note": (
                "Counting window only — no audit record was modified or removed. "
                "Clear the window to see the full history again."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to set usage epoch: {exc}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set usage window: {exc}",
        )


@router.get("/export")
async def export_thresholds(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Export learned thresholds as JSON.

    Returns:
        {
          "version": "1",
          "tenant_id": "_default",
          "export_at": "2026-09-11T12:00:00Z",
          "thresholds": [...]
        }
    """
    try:
        store = get_store(rec.tenant_id)
        export_data = store.export_json()

        logger.info(f"Thresholds exported for tenant {rec.tenant_id}")

        return export_data

    except Exception as e:
        logger.error(f"Failed to export thresholds: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export: {e}",
        )


@router.post("/import")
async def import_thresholds(
    data: Dict[str, Any],
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Import learned thresholds from JSON backup.

    Args:
        data: export dict (from /export endpoint)

    Returns:
        status: "ok"
        imported_count: number of imported thresholds
        imported_at: timestamp
    """
    try:
        store = get_store(rec.tenant_id)
        audit_backend = _get_audit_backend(rec.tenant_id)

        # Import (with audit logging)
        count = store.import_json(
            data,
            audit_backend=audit_backend,
            operator_note="Operator import via console",
        )

        logger.info(
            f"Imported {count} thresholds for tenant {rec.tenant_id}"
        )

        return {
            "status": "ok",
            "imported_count": count,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }

    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to import thresholds: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import: {e}",
        )


# ── Private helpers ────────────────────────────────────────────────────

def _get_audit_backend(tenant_id: str) -> Optional[Any]:
    """Get audit backend for the tenant.

    Returns:
        Audit backend instance, or None if unavailable.
    """
    try:
        # NOT core.skills.skill_audit (no such class there — was silently
        # falling through to None, so override/reset ran unaudited; fixed
        # 2026-09-12). The real adapter lives next to CostVarianceOptimizer,
        # which shares this audit event family (learned_threshold_updated).
        from core.learning.cost_variance_optimizer import _SkillAuditBackend  # noqa: PLC0415
        return _SkillAuditBackend()
    except Exception:  # noqa: BLE001
        logger.warning("Audit backend unavailable (non-fatal)")
        return None
