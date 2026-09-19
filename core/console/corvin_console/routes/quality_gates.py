"""Quality Gates API endpoints for Console (Phase 2.1, ADR-0688).

Exposes 7 API endpoints for quality gate status, execution, history, and graph queries.
All endpoints require a valid console session and respect tenant isolation.

Endpoints (mounted under /v1/console by app.py):
  - GET  /api/quality/gates/status
  - GET  /api/quality/gates/status/<gate_name>
  - GET  /api/quality/gates/history/<artifact_id>
  - POST /api/quality/gates/run/all
  - GET  /api/quality/gates/results/<run_id>
  - GET  /api/quality/gates/graph/nodes
  - GET  /api/quality/gates/graph/edges

Auth + tenant (ADR-0007): every route requires a live console session (``require_session``)
and derives the tenant from ``rec.tenant_id`` — never from query parameters or env vars.
Mutations (POST) additionally require CSRF token (``require_csrf``).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.artifacts import KINDS, load_artifacts, resolve_adr_root
from core.quality_gates.config import get_validator_class, list_gates, load_gate_config
from core.quality_gates.models import KGNode, VerdictType, KGNodeType

from .. import _bootstrap
from .. import auth as session_auth
from ..deps import require_csrf, require_session

_forge_paths = _bootstrap.forge_paths
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quality", tags=["quality-gates"])

# In-memory run cache (bounded; a run is an operator action, not a record —
# the record is the hash-chained gate_events table it writes).
_run_cache: Dict[str, Dict[str, Any]] = {}
_runs_lock = threading.Lock()
_MAX_RUNS = 64


def _get_graph(tenant_id: str) -> KnowledgeGraph:
    """Get or initialize Knowledge Graph for tenant."""
    db_path = _forge_paths.corvin_home() / "tenants" / tenant_id / "global" / "quality_gates.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return KnowledgeGraph(str(db_path), tenant_id)


def _get_audit_logger(tenant_id: str) -> QualityGateAuditLogger:
    """Get audit logger for tenant."""
    graph = _get_graph(tenant_id)
    return QualityGateAuditLogger(graph.conn)


def _iso(dt: datetime) -> str:
    """UTC, ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` — the same shape the audit logger
    writes, so the string comparisons in the SQL windows are exact. Until
    2026-09-20 this appended ``Z`` to an offset-aware isoformat (``+00:00Z``),
    which ``_parse_timestamp`` rejected — every event a run wrote was then
    skipped by the status window and the page counted zero."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now() -> str:
    """Return current timestamp in ISO 8601 format."""
    return _iso(datetime.now(timezone.utc))


def _parse_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 timestamp string."""
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1]
            if not ts_str.endswith("+00:00"):
                ts_str += "+00:00"
        parsed = datetime.fromisoformat(ts_str)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


# ============================================================================
# Endpoint 1: GET /api/quality/gates/status
# ============================================================================

@router.get("/gates/status", summary="Get overall gate status summary")
async def get_gates_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get summary of all gate statuses for the tenant.

    Returns 24h and 7d breakdowns, verdicts by gate, and trend indicators.
    """
    tenant_id = rec.tenant_id
    try:
        graph = _get_graph(tenant_id)
        # Aggregate in SQL — one run over the checkout writes ~1 100 events, and
        # the previous "last 1000 rows" window silently cut the gates that ran
        # first (2026-09-20: IdeaGate and ConceptGate read "not run" on the
        # page right after a completed run). Timestamps are the audit logger's
        # ``...Z`` shape, so the string comparison is exact.
        now = datetime.now(timezone.utc)
        day_ago = _iso(now - timedelta(hours=24))
        week_ago = _iso(now - timedelta(days=7))
        agg_rows = graph.conn.execute(
            "SELECT gate_name, lower(verdict), "
            "SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) "
            "FROM gate_events WHERE tenant_id = ? GROUP BY gate_name, lower(verdict)",
            [day_ago, week_ago, tenant_id],
        ).fetchall()
        last_rows = graph.conn.execute(
            "SELECT gate_name, lower(verdict), timestamp FROM gate_events e WHERE tenant_id = ? "
            "AND timestamp = (SELECT max(timestamp) FROM gate_events WHERE tenant_id = e.tenant_id AND gate_name = e.gate_name)",
            [tenant_id],
        ).fetchall()
        events_total = int(graph.conn.execute(
            "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?", [tenant_id]
        ).fetchall()[0][0])

        gate_summaries = {}
        for gate_name in list_gates():
            gate_summaries[gate_name] = {
                "last_24h": {"pass": 0, "warn": 0, "fail": 0},
                "last_7d": {"pass": 0, "warn": 0, "fail": 0},
                "last_verdict": None,
                "last_timestamp": None,
            }
        for gate_name, verdict, n_24h, n_7d in agg_rows:
            if gate_name not in gate_summaries or verdict not in ("pass", "warn", "fail"):
                continue
            gate_summaries[gate_name]["last_24h"][verdict] += int(n_24h or 0)
            gate_summaries[gate_name]["last_7d"][verdict] += int(n_7d or 0)
        for gate_name, verdict, ts in last_rows:
            if gate_name in gate_summaries:
                gate_summaries[gate_name]["last_verdict"] = verdict
                gate_summaries[gate_name]["last_timestamp"] = ts

        # ADR-0688 amendment (2026-09-20): the page renders what is here — a
        # gate's 24h pass share over the artifacts it actually judged, and the
        # totals. A gate with no event in the window has pass_percentage null,
        # never 0.
        events_24h = 0
        for gs in gate_summaries.values():
            for window in ("last_24h", "last_7d"):
                w = gs[window]
                judged = w["pass"] + w["warn"] + w["fail"]
                w["total"] = judged
                w["pass_percentage"] = round(w["pass"] / judged * 100, 1) if judged else None
            events_24h += gs["last_24h"]["total"]
        last_run = None
        with _runs_lock:
            for run in _run_cache.values():
                if run["tenant_id"] == tenant_id and (last_run is None or run["started_at"] > last_run["started_at"]):
                    last_run = {k: run.get(k) for k in ("run_id", "status", "started_at", "completed_at", "artifacts_total", "artifacts_done", "error")}
        return {
            "tenant_id": tenant_id,
            "timestamp": _now(),
            "summary": gate_summaries,
            "gates_total": len(list_gates()),
            "events_24h": events_24h,
            "events_total": events_total,
            "source_root": str(resolve_adr_root() or ""),
            "last_run": last_run,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to get gates status: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get status: {type(exc).__name__}")


# ============================================================================
# Trend + failures — what the page draws (2026-09-20)
# ============================================================================

@router.get("/gates/trend", summary="Per-day pass share over the last N days")
async def get_gates_trend(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> Dict[str, Any]:
    """One point per day that HAS gate events (never a zero for a day nobody
    ran anything — that is a gap, not a 0 % pass rate)."""
    tenant_id = rec.tenant_id
    try:
        graph = _get_graph(tenant_id)
        since = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        rows = graph.conn.execute(
            "SELECT timestamp, verdict FROM gate_events WHERE tenant_id = ? AND timestamp >= ? ORDER BY timestamp",
            [tenant_id, since],
        ).fetchall()
        by_day: Dict[str, Dict[str, int]] = {}
        for ts, verdict in rows:
            parsed = _parse_timestamp(ts)
            if not parsed:
                continue
            day = parsed.date().isoformat()
            d = by_day.setdefault(day, {"pass": 0, "warn": 0, "fail": 0})
            d[str(verdict).lower()] = d.get(str(verdict).lower(), 0) + 1
        points = []
        for day in sorted(by_day):
            d = by_day[day]
            total = d["pass"] + d["warn"] + d["fail"]
            points.append({"date": day, **d, "total": total,
                           "pass_percentage": round(d["pass"] / total * 100, 1) if total else None})
        return {"tenant_id": tenant_id, "days": days, "points": points, "timestamp": _now()}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to get gates trend: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get trend: {type(exc).__name__}")


@router.get("/gates/failures", summary="Recent fail/warn verdicts with their reason")
async def get_gates_failures(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Dict[str, Any]:
    tenant_id = rec.tenant_id
    try:
        graph = _get_graph(tenant_id)
        since = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
        rows = graph.conn.execute(
            "SELECT gate_name, artifact_id, verdict, confidence, reason, timestamp, findings_count "
            "FROM gate_events WHERE tenant_id = ? AND timestamp >= ? AND verdict IN ('fail', 'warn') "
            "ORDER BY timestamp DESC LIMIT ?",
            [tenant_id, since, limit],
        ).fetchall()
        total = graph.conn.execute(
            "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ? AND timestamp >= ? AND verdict IN ('fail', 'warn')",
            [tenant_id, since],
        ).fetchall()[0][0]
        failures = [
            {"gate_name": g, "artifact_id": a, "verdict": v, "confidence": float(c or 0.0),
             "reason": r or "", "timestamp": ts, "findings_count": int(fc or 0),
             "artifact_type": KINDS.get(g, {}).get("node_type", "")}
            for g, a, v, c, r, ts, fc in rows
        ]
        return {"tenant_id": tenant_id, "hours": hours, "failures": failures, "total": int(total), "timestamp": _now()}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to get gates failures: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get failures: {type(exc).__name__}")


# ============================================================================
# Endpoint 2: GET /api/quality/gates/status/<gate_name>
# ============================================================================

@router.get("/gates/status/{gate_name}", summary="Get status for a specific gate")
async def get_gate_status(
    gate_name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get detailed status breakdown for a specific gate (24h, 7d, 30d)."""
    tenant_id = rec.tenant_id
    try:
        # Validate gate name
        if gate_name not in list_gates():
            raise HTTPException(status_code=404, detail=f"Gate {gate_name} not found")

        graph = _get_graph(tenant_id)

        # Query gate events for this specific gate
        query = (
            "SELECT verdict, timestamp FROM gate_events "
            "WHERE tenant_id = ? AND gate_name = ? ORDER BY timestamp DESC LIMIT 5000"
        )
        rows = graph.conn.execute(query, [tenant_id, gate_name]).fetchall()

        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        summaries = {
            "last_24h": {"pass": 0, "warn": 0, "fail": 0},
            "last_7d": {"pass": 0, "warn": 0, "fail": 0},
            "last_30d": {"pass": 0, "warn": 0, "fail": 0},
        }

        for verdict_str, timestamp_str in rows:
            parsed_ts = _parse_timestamp(timestamp_str)
            if not parsed_ts:
                continue

            verdict = verdict_str.lower()

            if parsed_ts >= day_ago:
                summaries["last_24h"][verdict] = summaries["last_24h"].get(verdict, 0) + 1
            if parsed_ts >= week_ago:
                summaries["last_7d"][verdict] = summaries["last_7d"].get(verdict, 0) + 1
            if parsed_ts >= month_ago:
                summaries["last_30d"][verdict] = summaries["last_30d"].get(verdict, 0) + 1

        config = load_gate_config(gate_name)

        return {
            "tenant_id": tenant_id,
            "gate_name": gate_name,
            "description": config.get("description", ""),
            "summary": summaries,
            "timestamp": _now(),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to get gate status for {gate_name}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get status: {type(exc).__name__}")


# ============================================================================
# Endpoint 3: GET /api/quality/gates/history/<artifact_id>
# ============================================================================

@router.get("/gates/history/{artifact_id}", summary="Get decision history for an artifact")
async def get_artifact_history(
    artifact_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
) -> Dict[str, Any]:
    """Get gate decision history for a specific artifact (paginated)."""
    tenant_id = rec.tenant_id
    try:
        graph = _get_graph(tenant_id)

        # Query gate events for this artifact
        query = (
            "SELECT gate_name, verdict, confidence, reason, timestamp FROM gate_events "
            "WHERE tenant_id = ? AND artifact_id = ? "
            "ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        )
        rows = graph.conn.execute(query, [tenant_id, artifact_id, limit, offset]).fetchall()

        # Count total
        count_query = (
            "SELECT COUNT(*) FROM gate_events "
            "WHERE tenant_id = ? AND artifact_id = ?"
        )
        total_result = graph.conn.execute(count_query, [tenant_id, artifact_id]).fetchall()
        total = total_result[0][0] if total_result else 0

        events = []
        for gate_name, verdict_str, confidence, reason, timestamp_str in rows:
            events.append({
                "gate_name": gate_name,
                "verdict": verdict_str,
                "confidence": float(confidence) if confidence else 0.0,
                "reason": reason,
                "timestamp": timestamp_str,
            })

        return {
            "tenant_id": tenant_id,
            "artifact_id": artifact_id,
            "events": events,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
            },
            "timestamp": _now(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to get history for artifact {artifact_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get history: {type(exc).__name__}")


# ============================================================================
# Endpoint 4: POST /api/quality/gates/run/all
# ============================================================================

@router.post("/gates/run/all", summary="Run all gate validators")
async def run_all_gates(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Run every gate validator over the REAL artifacts of the Corvin-ADR
    checkout (``core.quality_gates.artifacts``) and record each verdict as a
    hash-chained ``gate_events`` row plus a knowledge-graph node.

    Until 2026-09-20 this wrote one hard-coded ``pass`` per gate ("validated
    successfully", confidence 0.95) and touched no artifact — a fabricated
    result on a page titled "Quality Gates". The run is a JOB on a worker
    thread; ``GET /api/quality/gates/results/{run_id}`` reports its progress
    (artifacts judged so far) and, once completed, the per-gate counts.
    """
    tenant_id = rec.tenant_id
    run_id = str(uuid.uuid4())[:8]
    root = resolve_adr_root()
    run: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "status": "running",
        "started_at": _now(),
        "completed_at": None,
        "gates_total": len(list_gates()),
        "gates_completed": 0,
        "artifacts_total": 0,
        "artifacts_done": 0,
        "source_root": str(root) if root else None,
        "results": {},
        "error": None,
    }
    with _runs_lock:
        _run_cache[run_id] = run
        while len(_run_cache) > _MAX_RUNS:
            _run_cache.pop(next(iter(_run_cache)))
    if root is None:
        run["status"] = "failed"
        run["error"] = "no Corvin-ADR checkout found (CORVIN_ADR_ROOT, ../Corvin-ADR or corvin_decisions/)"
        run["completed_at"] = _now()
        return {"tenant_id": tenant_id, "run_id": run_id, "status": "failed", "error": run["error"],
                "results_url": f"/v1/console/api/quality/gates/results/{run_id}", "timestamp": _now()}

    def _worker() -> None:
        graph = None
        try:
            graph = _get_graph(tenant_id)
            audit_logger = QualityGateAuditLogger(graph.conn)
            per_gate = {g: load_artifacts(root, g) for g in list_gates()}
            run["artifacts_total"] = sum(len(v) for v in per_gate.values())
            for gate_name in list_gates():
                counts = {"pass": 0, "warn": 0, "fail": 0}
                validator = get_validator_class(gate_name)(graph, tenant_id)
                node_type = KGNodeType(KINDS[gate_name]["node_type"])
                for artifact in per_gate[gate_name]:
                    result = validator.validate(artifact)
                    if not result.timestamp:
                        result = dataclasses.replace(result, timestamp=_now())
                    audit_logger.write_gate_event(result)
                    try:
                        graph.write_node(KGNode(
                            id=str(artifact.get("id") or result.artifact_id), node_type=node_type, tenant_id=tenant_id,
                            data={"path": artifact.get("path"), "last_verdict": result.verdict.value,
                                  "last_gate_run": run_id},
                        ))
                    except Exception as exc:  # noqa: BLE001 — the event is the record; the node is a projection
                        logger.warning("kg node write failed for %s: %s", result.artifact_id, type(exc).__name__)
                    counts[result.verdict.value] = counts.get(result.verdict.value, 0) + 1
                    run["artifacts_done"] += 1
                run["results"][gate_name] = {"gate_name": gate_name, "artifacts": len(per_gate[gate_name]), **counts}
                run["gates_completed"] += 1
            run["status"] = "completed"
        except Exception as exc:  # noqa: BLE001 — the run must never stay "running"
            logger.error("gate run %s failed: %s", run_id, exc)
            run["status"] = "failed"
            run["error"] = f"{type(exc).__name__}"
        finally:
            run["completed_at"] = _now()
            if graph is not None:
                try:
                    graph.close()
                except Exception:  # noqa: BLE001
                    pass

    threading.Thread(target=_worker, name=f"quality-gates-run-{run_id}", daemon=True).start()
    return {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "status": "running",
        "artifacts_total": run["artifacts_total"],
        "results_url": f"/v1/console/api/quality/gates/results/{run_id}",
        "timestamp": _now(),
    }


# ============================================================================
# Endpoint 5: GET /api/quality/gates/results/<run_id>
# ============================================================================

@router.get("/gates/results/{run_id}", summary="Get results of a gate run")
async def get_run_results(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Get detailed results of a specific gate run."""
    tenant_id = rec.tenant_id

    with _runs_lock:
        run = _run_cache.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Verify tenant isolation
    if run["tenant_id"] != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")

    total = run.get("artifacts_total") or 0
    done = run.get("artifacts_done") or 0
    return {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "status": run["status"],
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "gates_total": run["gates_total"],
        "gates_completed": run["gates_completed"],
        "artifacts_total": total,
        "artifacts_done": done,
        "progress": 100 if run["status"] in ("completed", "failed") else (round(done / total * 100) if total else 0),
        "source_root": run.get("source_root"),
        "error": run.get("error"),
        "gates_results": list(run.get("results", {}).values()),
        "timestamp": _now(),
    }


# ============================================================================
# Endpoint 6: GET /api/quality/gates/graph/nodes
# ============================================================================

@router.get("/gates/graph/nodes", summary="Get knowledge graph nodes")
async def get_graph_nodes(
    node_type: Annotated[Optional[str], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
) -> Dict[str, Any]:
    """Get paginated list of knowledge graph nodes.

    Supports filtering by node_type (ADR, Concept, Idea, ImplementationPlan, Commit, Task).
    """
    tenant_id = rec.tenant_id
    try:
        graph = _get_graph(tenant_id)

        # Parse node_type if provided
        node_type_enum = None
        if node_type:
            try:
                node_type_enum = KGNodeType(node_type)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid node_type: {node_type}. "
                           f"Must be one of {[t.value for t in KGNodeType]}"
                )

        # Query nodes with type filter
        if node_type_enum:
            query = (
                "SELECT id, node_type, data, created_at FROM kg_nodes "
                "WHERE tenant_id = ? AND node_type = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?"
            )
            rows = graph.conn.execute(
                query, [tenant_id, node_type_enum.value, limit, offset]
            ).fetchall()
            count_query = (
                "SELECT COUNT(*) FROM kg_nodes "
                "WHERE tenant_id = ? AND node_type = ?"
            )
            total_result = graph.conn.execute(count_query, [tenant_id, node_type_enum.value]).fetchall()
        else:
            query = (
                "SELECT id, node_type, data, created_at FROM kg_nodes "
                "WHERE tenant_id = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?"
            )
            rows = graph.conn.execute(query, [tenant_id, limit, offset]).fetchall()
            count_query = "SELECT COUNT(*) FROM kg_nodes WHERE tenant_id = ?"
            total_result = graph.conn.execute(count_query, [tenant_id]).fetchall()

        total = total_result[0][0] if total_result else 0

        nodes = []
        for node_id, node_type_str, data_json, created_at in rows:
            data = json.loads(data_json) if data_json else {}
            nodes.append({
                "id": node_id,
                "type": node_type_str,
                "data": data,
                "created_at": created_at,
            })

        return {
            "tenant_id": tenant_id,
            "nodes": nodes,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
            },
            "timestamp": _now(),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to get graph nodes: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get nodes: {type(exc).__name__}")


# ============================================================================
# Endpoint 7: GET /api/quality/gates/graph/edges
# ============================================================================

@router.get("/gates/graph/edges", summary="Get knowledge graph edges")
async def get_graph_edges(
    relationship_type: Annotated[Optional[str], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
) -> Dict[str, Any]:
    """Get paginated list of knowledge graph edges.

    Supports filtering by relationship_type (e.g., 'depends_on', 'relates_to').
    """
    tenant_id = rec.tenant_id
    try:
        graph = _get_graph(tenant_id)

        # Query edges with optional relationship_type filter
        if relationship_type:
            query = (
                "SELECT source_id, target_id, relationship_type, data, created_at "
                "FROM kg_edges "
                "WHERE tenant_id = ? AND relationship_type = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?"
            )
            rows = graph.conn.execute(
                query, [tenant_id, relationship_type, limit, offset]
            ).fetchall()
            count_query = (
                "SELECT COUNT(*) FROM kg_edges "
                "WHERE tenant_id = ? AND relationship_type = ?"
            )
            total_result = graph.conn.execute(count_query, [tenant_id, relationship_type]).fetchall()
        else:
            query = (
                "SELECT source_id, target_id, relationship_type, data, created_at "
                "FROM kg_edges WHERE tenant_id = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?"
            )
            rows = graph.conn.execute(query, [tenant_id, limit, offset]).fetchall()
            count_query = "SELECT COUNT(*) FROM kg_edges WHERE tenant_id = ?"
            total_result = graph.conn.execute(count_query, [tenant_id]).fetchall()

        total = total_result[0][0] if total_result else 0

        edges = []
        for source_id, target_id, rel_type, data_json, created_at in rows:
            data = json.loads(data_json) if data_json else {}
            edges.append({
                "source_id": source_id,
                "target_id": target_id,
                "relationship_type": rel_type,
                "data": data,
                "created_at": created_at,
            })

        return {
            "tenant_id": tenant_id,
            "edges": edges,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
            },
            "timestamp": _now(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to get graph edges: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get edges: {type(exc).__name__}")
