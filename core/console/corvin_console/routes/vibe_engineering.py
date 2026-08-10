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
try:
    import importlib.util as _ilu  # noqa: PLC0415
    _ce_dir = Path(__file__).resolve().parents[4] / "operator" / "context_engineering"
    _sp = _ilu.spec_from_file_location(
        "context_engineering", str(_ce_dir / "__init__.py"),
        submodule_search_locations=[str(_ce_dir)])
    if _sp and _sp.loader:
        import sys as _sys  # noqa: PLC0415
        _m = _ilu.module_from_spec(_sp)
        _sys.modules["context_engineering"] = _m
        _sp.loader.exec_module(_m)
        _CEL_STAGES = _sys.modules["context_engineering.stages"]
except Exception:  # noqa: BLE001
    _CEL_STAGES = None


def _write_pipeline_config(tenant_id: str, pipeline: list) -> None:
    """Persist spec.context_engineering.pipeline into tenant.corvin.yaml."""
    import yaml  # noqa: PLC0415
    p = Path(_forge_paths.tenant_global_dir(tenant_id)) / "tenant.corvin.yaml"
    data = {}
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    spec = data.setdefault("spec", {})
    ce = spec.setdefault("context_engineering", {})
    ce["pipeline"] = pipeline
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

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


# ── Pipeline editor (P-E, ADR-0284) ──────────────────────────────────────
@router.get("/pipeline")
async def get_pipeline(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Current pipeline config + the stage palette (builtin-only until P-G)."""
    if _CEL_STAGES is None:
        return {"available": False, "current": [], "palette": [], "default": []}
    specs, _dropped = _CEL_STAGES.resolve_pipeline(rec.tenant_id)
    return {
        "available": True,
        "current": [{"stage": s.id, "config": s.config} for s in specs],
        "palette": _CEL_STAGES.all_specs(),   # [{id, requires, effect, trust}]
        "default": list(_CEL_STAGES.DEFAULT_PIPELINE),
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
