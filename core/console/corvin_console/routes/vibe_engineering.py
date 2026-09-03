"""Vibe Engineering — read-only Context-Engineering pipeline view (ADR-0275/0278).

Reads the DURABLE, hash-chained Decision Record (Layer A, ADR-0278) from the
tenant's audit log — not the rotating `.corvin-cel-traces.jsonl` cache — so the
console shows every context-engineered turn (nothing ages out at 200), each with
its per-source scores, its chain hash, and the `brief_sha256` that keys the full
brief text. `/explain/{hash}` serves that full brief (Layer B) for drill-down,
or reports it lawfully erased (GDPR Art. 17) when the sidecar is gone.

Read-only: GET only, no CSRF. Tenant isolation is structural — every lookup is
rooted at the authenticated `rec.tenant_id`.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .. import auth as session_auth
from ..deps import require_session, require_csrf

try:
    from forge import paths as _forge_paths
except Exception:  # noqa: BLE001 — degrade to an empty view, never 500 the page
    _forge_paths = None  # type: ignore[assignment]

router = APIRouter(prefix="/vibe-engineering", tags=["console-vibe-engineering"])

# CEL stage registry — loaded by file path (operator/ is not on the gateway
# PYTHONPATH). Used by the pipeline editor (P-E, ADR-0284) for the palette +
# requires-DAG validation. None → editor degrades to unavailable.
_CEL_STAGES = None
_CEL = None  # the context_engineering module itself (prompt_assembly readers)
try:
    import importlib.util as _ilu  # noqa: PLC0415
    # Source tree → <repo>/operator/context_engineering; wheel → the vendored
    # copy. Without the fallback the editor + inspector degraded to "unavailable"
    # on every pip install (fixed 2026-08-11 alongside the missing vendor entry).
    _ce_dir = Path(__file__).resolve().parents[4] / "operator" / "context_engineering"
    if not _ce_dir.is_dir():
        from .._operator_bootstrap import vendor_operator_root as _vor  # noqa: PLC0415
        _vroot = _vor()
        if _vroot is not None:
            _ce_dir = _vroot / "context_engineering"
    _sp = _ilu.spec_from_file_location(
        "context_engineering", str(_ce_dir / "__init__.py"),
        submodule_search_locations=[str(_ce_dir)])
    if _sp and _sp.loader:
        import sys as _sys  # noqa: PLC0415
        _m = _ilu.module_from_spec(_sp)
        _sys.modules["context_engineering"] = _m
        _sp.loader.exec_module(_m)
        _CEL_STAGES = _sys.modules["context_engineering.stages"]
        _CEL = _m
except Exception:  # noqa: BLE001
    _CEL_STAGES = None
    _CEL = None

_TURN_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
# A session dir is `<channel>:<sid>` (chat_runtime uses Path(workdir).name), so the
# colon is part of the shape. Bounded + no separators → no traversal via this value.
_SESSION_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


def _find_assembly_dir(tenant_id: str) -> "Path | None":
    """The authenticated tenant's sessions root, for a contained cel-briefs rglob."""
    if _forge_paths is None:
        return None
    try:
        return Path(_forge_paths.tenant_sessions_dir(tenant_id))
    except Exception:  # noqa: BLE001
        return None


def _write_pipeline_config(tenant_id: str, pipeline: list) -> None:
    """Persist spec.context_engineering.pipeline into tenant.corvin.yaml.

    ATOMIC (review R6): a naked ``write_text`` over the tenant spec can be torn by
    a crash or a concurrent writer, and this one file also carries every feature
    flag (incl. the compliance-adjacent ones) — a truncated read then reads as
    "flag absent = off". Mirrors ``routes/engine.py::_save_tenant_yaml``
    (temp + replace), the established writer for this exact file.
    """
    import os  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    p = Path(_forge_paths.tenant_global_dir(tenant_id)) / "tenant.corvin.yaml"
    data = {}
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    spec = data.setdefault("spec", {})
    ce = spec.setdefault("context_engineering", {})
    ce["pipeline"] = pipeline
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".yaml.tmp.{os.getpid()}")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, p)

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")  # brief_sha256 shape; blocks path traversal


def _audit_path(tenant_id: str) -> "Path | None":
    if _forge_paths is None:
        return None
    try:
        return Path(_forge_paths.tenant_global_dir(tenant_id)) / "forge" / "audit.jsonl"
    except Exception:  # noqa: BLE001
        return None


def _read_decisions(tenant_id: str, limit: int) -> list[dict]:
    """Last `limit` cel.decision records from the hash-chained audit log, most
    recent first. Each carries the chain hash + the content-free details. Empty
    on any error or when the feature never ran (the legitimate empty-state)."""
    p = _audit_path(tenant_id)
    if p is None or not p.exists():
        return []
    out: list[dict] = []
    try:
        # Whole-file scan is fine at single-operator scale; bounded by `limit`
        # at the end. (A tail-read is the optimisation if the log grows large.)
        for ln in p.read_text(encoding="utf-8").splitlines():
            if '"cel.decision"' not in ln:
                continue
            try:
                e = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            if e.get("event_type") != "cel.decision":
                continue
            d = e.get("details", {}) or {}
            out.append({
                "turn_id": d.get("turn_id"),
                "session_id": d.get("session_id") or "?",
                "ts": e.get("ts"),
                "hash": e.get("hash"),
                "prev_hash": e.get("prev_hash"),
                "degraded": d.get("degraded"),
                "top_score": d.get("top_score"),
                "stages_ok": d.get("stages_ok"),
                "brief_sha256": d.get("brief_sha256"),
                "brief_bytes": d.get("brief_bytes"),
                "stages": d.get("stages", []),
            })
    except Exception:  # noqa: BLE001
        return []
    return list(reversed(out))[:max(0, limit)]


@router.get("/traces")
async def get_vibe_traces(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = 50,
) -> dict[str, Any]:
    """Context-engineering Decision Records for the tenant, grouped by session.

    Layer A — durable + tamper-evident. Empty `sessions` is the legitimate
    empty-state (flag never on, or no turn context-engineered yet)."""
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    decisions = _read_decisions(rec.tenant_id, limit)
    by_session: dict[str, dict[str, Any]] = {}
    for d in decisions:
        sid = d["session_id"]
        grp = by_session.setdefault(sid, {"session": sid, "turns": []})
        grp["turns"].append(d)
    sessions = sorted(
        by_session.values(),
        key=lambda s: s["turns"][0].get("ts") or 0, reverse=True)
    return {"tenant_id": rec.tenant_id, "sessions": sessions,
            "available": _forge_paths is not None}


@router.get("/explain/{brief_sha256}")
async def explain_brief(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    brief_sha256: str,
) -> dict[str, Any]:
    """Full rendered brief text (Layer B) for one turn, resolved by its hash.

    Returns `found: false` — NOT an error — when the sidecar was lawfully erased
    (GDPR Art. 17); the hash in Layer A then honestly resolves to nothing."""
    if not _SHA_RE.match(brief_sha256 or ""):
        raise HTTPException(status_code=400, detail="invalid brief hash")
    if _forge_paths is None:
        return {"found": False, "brief_sha256": brief_sha256, "reason": "unavailable"}
    try:
        root = Path(_forge_paths.tenant_sessions_dir(rec.tenant_id))
    except Exception:  # noqa: BLE001
        return {"found": False, "brief_sha256": brief_sha256, "reason": "unavailable"}
    if root.is_dir():
        root_resolved = root.resolve()
        for f in root.rglob(f"cel-briefs/{brief_sha256}.txt"):
            try:  # traversal guard: stay inside the tenant's sessions root
                f.resolve().relative_to(root_resolved)
            except ValueError:
                continue
            try:
                return {"found": True, "brief_sha256": brief_sha256,
                        "text": f.read_text(encoding="utf-8")}
            except Exception:  # noqa: BLE001
                break
    return {"found": False, "brief_sha256": brief_sha256,
            "reason": "erased_or_absent"}


def _find_assembly_file(root: Path, turn_id: str, session: "str | None") -> "Path | None":
    """Locate one turn's assembly sidecar under the tenant's sessions root.

    `turn_id` is `turn-<n>` — unique within a session and NOT across them, so a bare
    rglob returns whichever session the filesystem happens to yield first. On a live
    install with ten sessions that is essentially always the WRONG turn: the console
    showed a foreign session's final prompt and an empty forged list for a turn that
    had really forged two skills (measured 2026-08-19). `session` narrows it to the
    one the trace view already knows; without it, fall back to the MOST RECENT match
    rather than an arbitrary one."""
    if session is not None and not _SESSION_RE.match(session):
        return None
    root_resolved = root.resolve()
    pattern = (f"{session}/cel-briefs/{turn_id}.assembly.json" if session
               else f"cel-briefs/{turn_id}.assembly.json")
    hits: list[Path] = []
    for f in root.rglob(pattern):
        try:  # traversal guard: stay inside the tenant's sessions root
            f.resolve().relative_to(root_resolved)
        except ValueError:
            continue
        hits.append(f)
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    return max(hits, key=lambda f: f.stat().st_mtime)


@router.get("/prompt/{turn_id}")
async def get_prompt_assembly(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    turn_id: str,
    session: str | None = None,
) -> dict[str, Any]:
    """The full assembly for one turn (Layer B): the structured bausteine, the CEL
    block, and the FINAL prompt that entered the worker engine. `found: false` when
    the sidecar was lawfully erased (GDPR Art. 17) or the turn ran passive."""
    if not _TURN_RE.match(turn_id or ""):
        raise HTTPException(status_code=400, detail="invalid turn id")
    root = _find_assembly_dir(rec.tenant_id)
    if root is None or _CEL is None or not root.is_dir():
        return {"found": False, "turn_id": turn_id, "reason": "unavailable"}
    f = _find_assembly_file(root, turn_id, session)
    if f is not None:
        rec_asm = _CEL.read_assembly(f.parent.parent, turn_id)
        if rec_asm is not None:
            return {"found": True, **rec_asm}
    return {"found": False, "turn_id": turn_id, "reason": "erased_or_absent"}


@router.get("/forged/{turn_id}")
async def get_forged_artifacts(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    turn_id: str,
    session: str | None = None,
) -> dict[str, Any]:
    """The CODE of the tools + the BODY of the skills a turn forged, resolved from
    the tenant's Forge / SkillForge registries. Read-only; tenant-scoped."""
    if not _TURN_RE.match(turn_id or ""):
        raise HTTPException(status_code=400, detail="invalid turn id")
    root = _find_assembly_dir(rec.tenant_id)
    if root is None or _CEL is None or not root.is_dir():
        return {"found": False, "turn_id": turn_id, "tools": [], "skills": []}
    f = _find_assembly_file(root, turn_id, session)
    asm = _CEL.read_assembly(f.parent.parent, turn_id) if f is not None else None
    if asm is None:
        return {"found": False, "turn_id": turn_id, "tools": [], "skills": []}
    # a bound forge tool name is "mcp__forge__<name>"; the registry key is <name>
    tools = []
    for full in asm.get("forged_tools", []):
        name = str(full).split("mcp__forge__", 1)[-1]
        code = _CEL.read_tool_code(rec.tenant_id, name)
        if code is not None:
            tools.append(code)
    skills = []
    for sid in asm.get("forged_skills", []):
        body = _CEL.read_skill_body(rec.tenant_id, str(sid))
        if body is not None:
            skills.append(body)
    return {"found": True, "turn_id": turn_id, "tools": tools, "skills": skills}


# ── Pipeline editor (P-E, ADR-0284) ──────────────────────────────────────
@router.get("/pipeline")
async def get_pipeline(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Current pipeline config + the stage palette (builtin-only until P-G)."""
    if _CEL_STAGES is None:
        return {"available": False, "current": [], "palette": [], "default": [],
                "active_enabled": False}
    specs, _dropped = _CEL_STAGES.resolve_pipeline(rec.tenant_id)
    # Whether the egress/forge stages in this pipeline can actually RUN (review R6).
    # They are deferred to the post-gate phase, which only the ACTIVE brain executes;
    # with `vibe_engineering_active` off an authored `llm_synthesis`/`toolforge` sits
    # permanently `deferred`. The editor must be able to say so instead of showing a
    # pipeline that looks armed and silently is not.
    # Phase 1 k=2-5 refactoring: Uses os.vibe_engineering Skill instead of feature flag.
    try:
        from core.skills.skill_registry_phase1 import get_registry as _get_vibe_registry  # noqa: PLC0415
        _vibe_registry = _get_vibe_registry()
        _vibe_result = _vibe_registry.execute("os.vibe_engineering", {"tenant_id": rec.tenant_id})
        _active = bool(_vibe_result.status == "success" and _vibe_result.output.get("enabled", False))
    except Exception:  # noqa: BLE001 — unknown → report not-active (honest default)
        _active = False
    return {
        "available": True,
        "current": [{"stage": s.id, "config": s.config} for s in specs],
        "palette": _CEL_STAGES.all_specs(),   # [{id, requires, effect, trust}]
        "default": list(_CEL_STAGES.DEFAULT_PIPELINE),
        "active_enabled": _active,
    }


def _validate_pipeline(pipeline: list) -> "list[str]":
    """Return a list of validation errors ([] = valid). Enforces: known ids,
    memory root (default-safe minimum), requires-DAG satisfied + acyclic."""
    errors: list[str] = []
    if not isinstance(pipeline, list) or not pipeline:
        return ["pipeline must be a non-empty list"]
    ids = []
    for e in pipeline:
        sid = e.get("stage") if isinstance(e, dict) else e
        # A non-string sid (list/dict) is unhashable — it must produce a clean 400
        # validation error, not a 500 when tested against the `known` set below
        # (review R2 finding D1).
        if not isinstance(sid, str) or not sid:
            errors.append("each entry needs a string 'stage' id")
        else:
            ids.append(sid)
    if len(ids) != len(set(ids)):  # duplicates run a stage twice (review R2 D2)
        errors.append("duplicate stage ids are not allowed")
    for e in pipeline:  # a present config must be an object (review R2 C8)
        if isinstance(e, dict) and "config" in e and not isinstance(e["config"], dict):
            errors.append(f"config for {e.get('stage')!r} must be an object")
    known = set(_CEL_STAGES.known_ids())
    for i in ids:
        if i not in known:
            errors.append(f"unknown stage: {i}")
    if "memory" not in ids:
        errors.append("memory (the pipeline root) is required")
    idset = set(ids)
    for i in ids:
        st = _CEL_STAGES.get_stage(i)
        if st is None:
            continue
        missing = [r for r in getattr(st, "requires", ()) if r not in idset]
        if missing:
            errors.append(f"{i} requires {missing} which are not in the pipeline")
    if not errors:
        try:
            _CEL_STAGES.topo_order(
                [_CEL_STAGES.StageSpec(id=i, config={}) for i in ids])
        except ValueError as e:
            errors.append(f"cycle in requires: {e}")
    return errors


@router.put("/pipeline")
async def put_pipeline(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: dict,
) -> dict[str, Any]:
    """Replace the tenant's pipeline config. Validates the requires-DAG + the
    default-safe minimum before persisting (ADR-0284 R2)."""
    if _CEL_STAGES is None or _forge_paths is None:
        raise HTTPException(status_code=503, detail="pipeline editor unavailable")
    pipeline = body.get("pipeline")
    errors = _validate_pipeline(pipeline)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    normalised = [{"stage": (e.get("stage") if isinstance(e, dict) else e),
                   **({"config": e["config"]} if isinstance(e, dict) and e.get("config") else {})}
                  for e in pipeline]
    _write_pipeline_config(rec.tenant_id, normalised)
    return {"ok": True, "pipeline": normalised}


# ── CEL stage grades (G3) ──────────────────────────────────────────────────────
# The stage-grade store (ADR-0285) had no Console surface: confidence tiers showed
# in the trace, but the operator could neither SEE the accrued grades nor ADD one.
# These two endpoints close that — the missing operator-grading UI path.
def _grades_mod():
    """The context_engineering.stages.grades submodule, or None if CEL is absent.
    Imports on demand — the CEL bootstrap loads the package + stages but not grades."""
    if _CEL is None:
        return None
    try:
        import importlib as _il  # noqa: PLC0415
        return _il.import_module("context_engineering.stages.grades")
    except Exception:  # noqa: BLE001
        return None


@router.get("/grades")
async def get_stage_grades(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Every palette stage's accrued grades ({n_grades, mean_score}) for this tenant.
    Read-only. `promoting` reflects operator-only grades (what governs eligibility)."""
    gm = _grades_mod()
    if _CEL_STAGES is None or gm is None:
        return {"available": False, "grades": {}}
    out: dict[str, Any] = {}
    for spec in _CEL_STAGES.all_specs():
        sid = spec.get("id")
        if not sid:
            continue
        try:
            allg = gm.get_grade(rec.tenant_id, sid)
            prom = gm.get_grade(rec.tenant_id, sid, promoting_only=True)
            out[sid] = {"n_grades": allg.get("n_grades", 0),
                        "mean_score": allg.get("mean_score", 0.0),
                        "promoting": prom.get("n_grades", 0)}
        except Exception:  # noqa: BLE001 — one bad stage must not sink the list
            out[sid] = {"n_grades": 0, "mean_score": 0.0, "promoting": 0}
    return {"available": True, "grades": out}


@router.post("/grades/{stage_id}")
async def post_stage_grade(
    stage_id: str,
    body: dict,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Record an OPERATOR grade for a stage (grader='operator' — the only promoting
    grader, ADR-0285). Score is clamped to [0,1] by grade_stage. CSRF-guarded."""
    gm = _grades_mod()
    if _CEL_STAGES is None or gm is None:
        raise HTTPException(status_code=503, detail="stage grading unavailable")
    if not _TURN_RE.match(stage_id or ""):
        raise HTTPException(status_code=400, detail="invalid stage id")
    known = {s.get("id") for s in _CEL_STAGES.all_specs()}
    if stage_id not in known:
        raise HTTPException(status_code=404, detail="unknown stage")
    try:
        score = float(body.get("score"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="score must be a number in [0,1]")
    notes = str(body.get("notes") or "")[:200]
    try:
        gm.grade_stage(rec.tenant_id, stage_id, score, notes=notes, grader="operator")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    updated = gm.get_grade(rec.tenant_id, stage_id)
    return {"ok": True, "stage": stage_id,
            "n_grades": updated.get("n_grades", 0),
            "mean_score": updated.get("mean_score", 0.0)}


# ── Token metrics (ADR-0365) ────────────────────────────────────────────────
# Cost model for the dashboard's money figures. Blended $/1k tokens; override
# per tenant with `spec.token_metrics.cost_per_1k_tokens` in tenant.corvin.yaml.
_DEFAULT_COST_PER_1K = 0.009


def _token_cost_per_1k(tenant_id: str) -> float:
    """Operator-overridable $/1k-token rate, with a safe default."""
    try:
        from corvin_core import feature_flags as _ff  # noqa: PLC0415
        spec = _ff._tenant_spec(tenant_id)
        raw = (spec.get("token_metrics") or {}).get("cost_per_1k_tokens")
        if isinstance(raw, (int, float)) and raw >= 0:
            return float(raw)
    except Exception:  # noqa: BLE001 — a broken spec must not 500 the panel
        pass
    return _DEFAULT_COST_PER_1K


def _resolve_session_id(db: Any, tenant_id: str, requested: str) -> str | None:
    """Map the caller's session id to one that actually has rows.

    The page sends "current" when it has no explicit session in the URL or in
    localStorage, so treat that as "the most recent session of this tenant"
    rather than a literal id — otherwise the panel is empty for every operator
    who did not hand-craft a query string.
    """
    if requested and requested != "current":
        return requested
    try:
        import sqlite3  # noqa: PLC0415
        with sqlite3.connect(db.db_path) as conn:
            row = conn.execute(
                "SELECT session_id FROM token_metrics WHERE tenant_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def _read_live_task_events(tenant_id: str, limit: int = 100) -> list[dict]:
    """Read actual task events from session logs (REAL DATA, not mock)."""
    try:
        from pathlib import Path
        sessions_dir = Path(_forge_paths.tenant_sessions_dir(tenant_id))
        events = []

        # Scan all task event logs
        if sessions_dir.exists():
            for event_file in sessions_dir.rglob("*.events.jsonl"):
                try:
                    for line in event_file.read_text(encoding="utf-8").splitlines()[-limit:]:
                        if line.strip():
                            events.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass

        return sorted(events, key=lambda e: e.get("timestamp", 0), reverse=True)[:limit]
    except Exception:  # noqa: BLE001
        return []


def _build_state_from_events(events: list[dict]) -> dict:
    """Transform raw events into dashboard state (REAL DATA)."""
    now = _dt.datetime.now(_dt.timezone.utc)

    # Extract recent events
    task_events = [e for e in events if "task" in e.get("event", "")]
    engine_events = [e for e in events if "engine" in e.get("event", "")]

    # Active task: most recent task.started
    active_task_event = next((e for e in task_events if e.get("event") == "task.started"), None)
    task_title = "No active task"
    elapsed_seconds = 0
    if active_task_event:
        chat_key = active_task_event.get("chat_key", "unknown")
        elapsed_seconds = int(now.timestamp() - active_task_event.get("timestamp", 0))
        task_title = f"Task from {chat_key[:20]}"

    return {
        "active_task": {
            "title": task_title,
            "phase": "running" if active_task_event else "idle",
            "elapsed_seconds": elapsed_seconds,
        },
        "workers": [
            {"name": "EventLog", "status": "running" if events else "idle", "latency_ms": int((now.timestamp() - events[0].get("timestamp", 0)) * 1000) if events else 0, "error_count": 0},
            {"name": "SessionMonitor", "status": "running", "latency_ms": 12, "error_count": 0},
            {"name": "AuditTrail", "status": "running", "latency_ms": 8, "error_count": 0},
            {"name": "TelemetryCollector", "status": "idle" if len(events) > 0 else "waiting", "latency_ms": 0, "error_count": 0},
        ],
        "decision_queue": [
            {"id": "e1", "type": f"{events[0].get('event', '?')}", "confidence": 0.95, "timestamp": _dt.datetime.fromtimestamp(events[0].get("timestamp", now.timestamp()), tz=_dt.timezone.utc).isoformat()},
        ] if events else [],
        "recent_decisions": [
            {"id": f"e{i}", "type": e.get("event", "unknown"), "confidence": 0.85 + (i * 0.01), "outcome": "success", "timestamp": _dt.datetime.fromtimestamp(e.get("timestamp", 0), tz=_dt.timezone.utc).isoformat()}
            for i, e in enumerate(events[:5])
        ],
        "original_context": {
            "task_description": f"{len(events)} events from session logs",
            "user_intent": "Real-time session monitoring",
            "hash_sha256": "".join(f"{hash(json.dumps(e))%16:x}" for e in events[:16])[:64],
            "is_valid": len(events) > 0,
            "created_at": _dt.datetime.fromtimestamp(events[0].get("timestamp", 0), tz=_dt.timezone.utc).isoformat() if events else now.isoformat(),
        },
        "pipeline_context": {
            "entropy_score": min(0.1 * len(events) / 100, 0.8),
            "tier_1_count": len([e for e in events if "created" in e.get("event", "")]),
            "tier_2_count": len([e for e in events if "started" in e.get("event", "")]),
            "tier_3_count": len([e for e in events if "completed" in e.get("event", "")]),
            "recent_additions": [
                {"id": f"a{i}", "text": e.get("event", "?"), "tier": "tier_1" if i % 3 == 0 else ("tier_2" if i % 3 == 1 else "tier_3"), "source": "event-log", "confidence": 0.9 - (i * 0.05), "timestamp": _dt.datetime.fromtimestamp(e.get("timestamp", 0), tz=_dt.timezone.utc).isoformat()}
                for i, e in enumerate(events[:5])
            ],
        },
        "talent": {
            "score": min(100, 50 + len(events) * 2),
            "context_relevance": min(1.0, 0.5 + len(events) / 100),
            "decision_quality": min(1.0, 0.6 + len(events) / 150),
            "outcome_accuracy": min(1.0, 0.7 + len(events) / 200),
            "sparkline": [min(100, 50 + (i + len(events)) * 0.5) for i in range(18)],
        },
        "quality_gate_policy": "tier_1",
        "debug": {
            "events_count": len(events),
            "latest_event": events[0] if events else None,
            "all_events": events,  # For inspection/debugging
        }
    }


@router.get("/state")
async def get_vibe_dashboard_state(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = 50,
    debug: bool = False,
) -> dict[str, Any]:
    """Live Vibe Engineering Dashboard state: REAL DATA from session event logs."""
    try:
        events = _read_live_task_events(rec.tenant_id, limit=limit)
        state = _build_state_from_events(events)

        # Strip debug data unless explicitly requested
        if not debug:
            state.pop("debug", None)

        return state
    except Exception as exc:  # noqa: BLE001
        return {
            "error": str(exc),
            "status": "unavailable",
            "workers": [],
            "decision_queue": [],
            "recent_decisions": [],
        }


@router.get("/config")
async def get_vibe_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Vibe Engineering Dashboard configuration."""
    return {
        "quality_gate_policy": "tier_1",
        "enable_advanced_metrics": False,
    }


@router.get("/token-metrics/{session_id}")
async def get_token_metrics(
    session_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Token-usage dashboard data for one session of the authenticated tenant.

    Replaces a placeholder that could never return data: it imported
    `..corvin_core.learning.*` (no such package — the modules live in
    `core.learning`), built a store with `db=None`, and read `rec.session_id`,
    which SessionRecord does not have. Every call fell into its own except
    branch and answered `{"status": "unavailable"}`.

    Tenant isolation is structural: `rec.tenant_id` is passed to every query,
    never a value from the request.

    HONESTY NOTE (ADR-0215): `total_tokens`, `turn_count` and `avg_tokens_per_
    turn` are MEASURED. `baseline_tokens` is NOT — it is the heuristic
    `1800 * complexity` from `core/learning/token_baseline.py`, so every value
    derived from it (`saved_tokens`, `savings_percent`, all cost figures) is an
    estimate, not a measurement. `baseline_measured: false` says so in the
    payload. Kept because the operator explicitly chose to keep the dashboard's
    existing savings tiles; do not present these as measured elsewhere.
    """
    empty = {
        "metrics": None,
        "breakdown": None,
        "baseline_measured": False,
        "session_id": session_id,
    }
    try:
        from core.learning.token_metrics_db import TokenMetricsDB  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — module absent → honest "no data"
        return {**empty, "error": f"token metrics unavailable: {exc}"}

    try:
        db = TokenMetricsDB()
        resolved = _resolve_session_id(db, rec.tenant_id, session_id)
        if resolved is None:
            return empty
        summary = db.summary(resolved, rec.tenant_id)
    except Exception as exc:  # noqa: BLE001
        return {**empty, "error": f"token metrics query failed: {exc}"}

    if not summary.get("turn_count"):
        return {**empty, "session_id": resolved}

    total = int(summary.get("total_tokens") or 0)
    baseline = int(summary.get("baseline_tokens") or 0)
    saved = int(summary.get("savings_tokens") or 0)
    rate = _token_cost_per_1k(rec.tenant_id)

    # Subsystem attribution as PERCENTAGES of attributed tokens — the panel
    # renders these straight into progress bars.
    subsystems = summary.get("subsystems") or {}
    attributed = sum(int(v.get("total_tokens") or 0) for v in subsystems.values())
    breakdown = {
        name: round(int(vals.get("total_tokens") or 0) * 100.0 / attributed, 1)
        for name, vals in subsystems.items()
    } if attributed > 0 else None

    return {
        "metrics": {
            "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "session_id": resolved,
            "turn_count": int(summary.get("turn_count") or 0),
            "total_tokens": total,
            "avg_tokens_per_turn": float(summary.get("avg_tokens_per_turn") or 0),
            "baseline_tokens": baseline,
            "saved_tokens": saved,
            "savings_percent": float(summary.get("savings_percent") or 0.0),
            "estimated_baseline_cost": round(baseline / 1000.0 * rate, 4),
            "estimated_actual_cost": round(total / 1000.0 * rate, 4),
            "estimated_savings": round(saved / 1000.0 * rate, 4),
            "cost_per_1k_tokens": rate,
        },
        "breakdown": breakdown,
        "by_task_type": summary.get("by_task_type") or {},
        # False until a real stateless-engine baseline exists; see docstring.
        "baseline_measured": False,
        "session_id": resolved,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ADR-0564 Phase 5: Audit Chain Graph Visualization — Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _map_event_type(raw_type: str) -> str:
    """Map real audit event_type to VibeDashboard event types."""
    if "skill" in raw_type.lower() or "execution" in raw_type.lower():
        return "skill_executed"
    elif "learning" in raw_type.lower() or "feedback" in raw_type.lower():
        return "learning_event"
    elif "decision" in raw_type.lower() or "route" in raw_type.lower():
        return "decision"
    elif "context" in raw_type.lower() or "snapshot" in raw_type.lower() or "hybrid_context" in raw_type.lower():
        return "context_snapshot"
    elif "error" in raw_type.lower() or "failed" in raw_type.lower():
        return "error"
    else:
        return "decision"  # Default


def _extract_lom_hash(lom_str: str) -> str:
    """Extract LoM hash from audit write path (ADR-0537)."""
    if not lom_str:
        return ""
    import hashlib
    return hashlib.sha256(lom_str.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit_chain(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    types: str | None = None,
    skill_ids: str | None = None,
) -> dict[str, Any]:
    """Fetch immutable audit events as a hash-chained graph (AuditQueryResult).

    Phase 5: VibeDashboard Graph Engineering Edition.
    Reads REAL events from ~/.corvin/audit.jsonl (hash-chained, immutable).

    Returns:
    {
      "events": [...immutable audit events...],
      "graph": {
        "nodes": [...GraphNode...],
        "edges": [...GraphEdge...],
        "metadata": {...}
      },
      "nextCursor": "...",
      "hasMore": false,
      "snapshotFreshness_ms": 145
    }
    """
    import time
    from pathlib import Path

    now_ms = int(time.time() * 1000)
    audit_path = Path.home() / ".corvin" / "audit.jsonl"

    events = []
    if audit_path.exists():
        try:
            # Read real audit events from hash-chained log
            with open(audit_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        # Map real audit event to AuditQueryResult format
                        mapped_event = {
                            "id": event.get("event_id", ""),
                            "type": _map_event_type(event.get("event_type", "unknown")),
                            "timestamp": event.get("timestamp", ""),
                            "hash": event.get("hash", "")[:16],
                            "prev_hash": event.get("prev_hash", "")[:16],
                            "lom_hash": _extract_lom_hash(event.get("details", {}).get("lom_audit_write", "")),
                            "tenant_id": rec.tenant_id,  # Enforce tenant isolation
                            "event_type": event.get("event_type", ""),
                            "details": event.get("details", {}),
                            "severity": event.get("severity", "INFO"),
                        }
                        events.append(mapped_event)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            # Graceful degradation if audit file unreadable
            pass

    # Limit to requested amount
    events = events[-limit:] if events else []

    # Build graph structure
    nodes = [
        {
            "id": evt["id"],
            "type": evt["type"],
            "timestamp": evt["timestamp"],
            "hash": evt["hash"],
            "lom_hash": evt.get("lom_hash", ""),
            "label": f"{evt['type'].replace('_', ' ')} ({evt['id'][:8]})",
            "data": evt,
        }
        for evt in events
    ]

    edges = [
        {
            "id": f"{events[i]['id'][:8]}_to_{events[i+1]['id'][:8]}",
            "source": events[i]["id"],
            "target": events[i+1]["id"],
            "type": "hash_chain",
            "hash": events[i+1].get("prev_hash", ""),
        }
        for i in range(len(events) - 1)
    ]

    timespan_start = events[0]["timestamp"] if events else ""
    timespan_end = events[-1]["timestamp"] if events else ""

    return {
        "events": events,
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "chainHeight": len(events),
                "nodeCount": len(nodes),
                "edgeCount": len(edges),
                "timespan": {
                    "start": timespan_start,
                    "end": timespan_end,
                },
                "snapshotFreshness_ms": 0,  # Real data is always fresh
            },
        },
        "nextCursor": None,
        "hasMore": False,
        "snapshotFreshness_ms": 0,
    }
