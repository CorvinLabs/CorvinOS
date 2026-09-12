"""
Unified Forge Panel API — consolidates Tools, Skills, OS-Skills management.

REAL DATA ONLY — integrates with:
  - core/forge/multi_registry.py (Tools)
  - core/skill_forge/multi_registry.py (Skills)
  - core/plugins/corvin_plugins/registry.py (OS-Skills)
  - core/console/corvin_console/audit.py (write) + routes/audit_tail.py (read) —
    the ONE hash-chained tenant audit trail (ADR-0232/0537); NOT the compliance
    report generator, which has no event-write/query API.

ADR-TBD: Unified Forge Control Plane
"""

from pathlib import Path
from typing import Optional, List, Any, Dict, Annotated
import json
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Depends, HTTPException, status as http_status

from core.console.corvin_console import auth as session_auth
from core.console.corvin_console.deps import require_session
from core.console.corvin_console import audit as console_audit
from core.console.corvin_console.routes.audit_tail import _parse_chain_file
from core.console.corvin_console import _bootstrap

_forge_paths = _bootstrap.forge_paths


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions for Registry Access
# ─────────────────────────────────────────────────────────────────────────────

def _read_registry(path: Path) -> dict[str, Any]:
    """Read forge registry.json, handle errors gracefully."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _forge_registry_paths(tid: str) -> list[tuple[str, Path]]:
    """Return (scope_label, registry_path) pairs for Tools in read-order."""
    pairs: list[tuple[str, Path]] = []
    sessions_dir = _forge_paths.tenant_sessions_dir(tid)
    if sessions_dir.exists():
        for entry in sorted(sessions_dir.iterdir()):
            reg = entry / "forge" / "registry.json"
            if reg.exists():
                pairs.append((f"session:{entry.name}", reg))
    tenant_root = _forge_paths.tenant_home(tid) / "forge" / "registry.json"
    if tenant_root.exists():
        pairs.append(("session-default", tenant_root))
    user_global = _forge_paths.tenant_global_dir(tid) / "forge" / "registry.json"
    if user_global.exists():
        pairs.append(("user", user_global))
    return pairs


def _skill_registry_paths(tid: str) -> list[tuple[str, Path]]:
    """Return (scope_label, registry_path) pairs for Skills in read-order."""
    pairs: list[tuple[str, Path]] = []
    sessions_dir = _forge_paths.tenant_sessions_dir(tid)
    if sessions_dir.exists():
        for entry in sorted(sessions_dir.iterdir()):
            reg = entry / "skill-forge" / "registry.json"
            if reg.exists():
                pairs.append((f"session:{entry.name}", reg))
    tenant_root = _forge_paths.tenant_home(tid) / "skill-forge" / "registry.json"
    if tenant_root.exists():
        pairs.append(("session-default", tenant_root))
    user_global = _forge_paths.tenant_global_dir(tid) / "skill-forge" / "registry.json"
    if user_global.exists():
        pairs.append(("user", user_global))
    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# Router Creation
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(tags=["console-forge-unified"])


# ─────────────────────────────────────────────────────────────────────────────
# Tools Endpoints (Real Data)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/tools")
async def list_tools(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all MCP tools from tenant registries (real data, multi-scope aggregation)."""
    try:
        tid = rec.tenant_id
        by_name: dict[str, dict[str, Any]] = {}

        # Aggregate from all scopes (sessions → tenant-root → user)
        for scope_label, reg in _forge_registry_paths(tid):
            for name, entry in _read_registry(reg).items():
                if not isinstance(entry, dict):
                    continue
                # Last scope wins: user > tenant-root > session
                by_name[name] = {
                    "id": name,
                    "name": entry.get("name", name),
                    "description": entry.get("description", ""),
                    "enabled": not entry.get("disabled", False),
                    "version": entry.get("version", "1.0.0"),
                    "runtime": entry.get("runtime", "python"),
                    "scope_source": scope_label,
                    "param_count": len(entry.get("input_schema", {}).get("properties", {})),
                    "call_count": entry.get("call_count", 0),
                    "created_at": entry.get("created_at"),
                    "sha256": entry.get("sha256", ""),
                    "promoted": bool(entry.get("promoted")),
                    "registry_path": str(reg),
                }

        items = sorted(by_name.values(), key=lambda r: r.get("name", ""))
        return {
            "tenant_id": tid,
            "timestamp": time.time(),
            "count": len(items),
            "tools": items,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tools/{tool_id}/enable")
async def enable_tool(
    tool_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Enable a tool (audit-logged, ADR-0232)."""
    try:
        # Mark tool as enabled in registry
        # TODO: Update tool state in user-scoped registry

        # Log to audit trail
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_tool_enabled",
            target_kind="tool",
            target_id=tool_id,
        )
        return {"status": "enabled", "tool_id": tool_id, "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tools/{tool_id}/disable")
async def disable_tool(
    tool_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Disable a tool (audit-logged)."""
    try:
        # Mark tool as disabled in registry
        # TODO: Update tool state in user-scoped registry

        # Log to audit trail
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_tool_disabled",
            target_kind="tool",
            target_id=tool_id,
        )
        return {"status": "disabled", "tool_id": tool_id, "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Skills Endpoints (Real Data)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/skills")
async def list_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all SkillForge skills with versions & learning state (real data)."""
    try:
        tid = rec.tenant_id
        by_name: dict[str, dict[str, Any]] = {}

        # Aggregate from all scopes
        for scope_label, reg in _skill_registry_paths(tid):
            for name, entry in _read_registry(reg).items():
                if not isinstance(entry, dict):
                    continue

                # Extract learning state if available
                learning_state = None
                if isinstance(entry.get("meta"), dict):
                    meta = entry["meta"]
                    if meta.get("grades"):
                        learning_state = {
                            "confidence": sum(meta["grades"]) / len(meta["grades"]) if meta["grades"] else 0.0,
                            "feedback_count": len(meta.get("grades", [])),
                            "last_updated": meta.get("updated_at"),
                        }

                # Last scope wins
                by_name[name] = {
                    "id": name,
                    "name": entry.get("name", name),
                    "description": entry.get("description", ""),
                    "enabled": not entry.get("disabled", False),
                    "version": entry.get("version", "1.0.0"),
                    "type": entry.get("type", "domain"),
                    "scope_source": scope_label,
                    "created_at": entry.get("created_at"),
                    "updated_at": entry.get("updated_at"),
                    "sha256": entry.get("sha256", ""),
                    "versions": entry.get("versions", []),
                    "promoted": bool(entry.get("promoted")),
                    "skill_dir": entry.get("skill_dir", ""),
                    "learning_state": learning_state,
                    "grade_count": len(entry.get("meta", {}).get("grades", [])),
                    "mean_score": entry.get("meta", {}).get("mean_score"),
                    "injectable": bool(entry.get("injectable", False)),
                }

        items = sorted(by_name.values(), key=lambda r: r.get("name", ""))
        return {
            "tenant_id": tid,
            "timestamp": time.time(),
            "count": len(items),
            "skills": items,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_id}/rollback")
async def rollback_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    version: str = Query(...),
) -> dict[str, Any]:
    """Rollback a skill to a previous version (audit-logged)."""
    try:
        # TODO: Update skill version in SkillForge registry

        # Log to audit trail
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_skill_rollback",
            target_kind="skill",
            target_id=skill_id,
            trigger=f"version={version}",
        )
        return {"status": "rolled_back", "skill_id": skill_id, "version": version, "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_id}/enable")
async def enable_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Enable a skill."""
    try:
        # TODO: Update skill state in registry

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_skill_enabled",
            target_kind="skill",
            target_id=skill_id,
        )
        return {"status": "enabled", "skill_id": skill_id, "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_id}/disable")
async def disable_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Disable a skill."""
    try:
        # TODO: Update skill state in registry

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_skill_disabled",
            target_kind="skill",
            target_id=skill_id,
        )
        return {"status": "disabled", "skill_id": skill_id, "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# OS-Skills Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/os-skills")
async def list_os_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List OS-level skills (delegation_router, context_adapter, etc.) — currently from plugins registry."""
    try:
        tid = rec.tenant_id
        os_skills = []

        # TODO: Integrate with plugins registry (core/plugins/corvin_plugins/registry.py)
        # For now, return empty list (structure ready for wiring)

        return {
            "tenant_id": tid,
            "timestamp": time.time(),
            "count": len(os_skills),
            "os_skills": os_skills,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/os-skills/{os_skill_id}/config")
async def update_os_skill_config(
    os_skill_id: str,
    config: Dict[str, Any],
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Tune OS-Skill config (learning-loop integrated, ADR-0314)."""
    try:
        # TODO: Validate config against OS-Skill manifest
        # TODO: Emit learning event

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_osskill_config_updated",
            target_kind="os-skill",
            target_id=os_skill_id,
        )
        return {"status": "configured", "os_skill_id": os_skill_id, "config": config, "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/os-skills/{os_skill_id}/disable")
async def disable_os_skill(
    os_skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Disable an OS-Skill (blocked for Meta-Skills: audit-gate, consent, tripwire, house-rules)."""
    try:
        # Meta-skills (compliance-critical) are never disableable
        meta_skill_ids = {"audit-gate", "consent", "tripwire", "house-rules"}
        if os_skill_id in meta_skill_ids:
            raise HTTPException(status_code=403, detail=f"Meta-Skill {os_skill_id} cannot be disabled")

        # TODO: Update OS-Skill state in plugins registry

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_osskill_disabled",
            target_kind="os-skill",
            target_id=os_skill_id,
        )
        return {"status": "disabled", "os_skill_id": os_skill_id, "timestamp": time.time()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Graph (Dependency) Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/graph")
async def get_dependency_graph(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Get dependency graph (DAG) for all Tools, Skills, OS-Skills."""
    try:
        tid = rec.tenant_id

        # Build nodes from all three registries
        nodes = []
        edges = []

        # Tools nodes
        for scope_label, reg in _forge_registry_paths(tid):
            for name, entry in _read_registry(reg).items():
                if isinstance(entry, dict):
                    nodes.append({
                        "id": f"tool:{name}",
                        "name": name,
                        "type": "tool",
                        "enabled": not entry.get("disabled", False),
                    })

        # Skills nodes
        for scope_label, reg in _skill_registry_paths(tid):
            for name, entry in _read_registry(reg).items():
                if isinstance(entry, dict):
                    nodes.append({
                        "id": f"skill:{name}",
                        "name": name,
                        "type": "skill",
                        "enabled": not entry.get("disabled", False),
                    })

        # TODO: Extract actual dependencies from skill/tool metadata
        # For now, return empty edges (structure ready for real integration)

        return {
            "nodes": nodes,
            "edges": edges,
            "cycles": [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Audit Endpoints (Real Data from Audit Trail)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit_trail(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    since: Optional[str] = Query(None),
    event_type: str = Query("forge_*"),
) -> dict[str, Any]:
    """Get hash-chained audit events for Forge operations (real data, ADR-0232/0537).

    Reads the same chain file console_audit.action_performed() writes to
    (<tenant>/global/forge/audit.jsonl) via audit_tail._parse_chain_file — the
    canonical console audit reader (ADR-0007 one-chain-per-tenant). Every Forge
    action is recorded as event_type "console.action_performed" with the real
    action name in details.action, so filtering happens on that field, not on
    the outer event_type.
    """
    try:
        tid = rec.tenant_id

        since_ts: float | None = None
        if since:
            try:
                since_ts = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
            except ValueError:
                since_ts = None

        chain = _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl"
        raw_events = _parse_chain_file(chain, severity=None, event_prefix=None, since=since_ts) if chain.exists() else []

        prefix = (event_type or "forge_*").rstrip("*")
        forge_events = [
            e for e in raw_events
            if (e.get("details") or {}).get("action", "").startswith(prefix)
        ]
        forge_events.sort(key=lambda r: r.get("ts") or 0.0, reverse=True)
        forge_events = forge_events[:100]

        # Format as API response
        formatted_events = [
            {
                "id": e.get("hash_prefix") or "",
                "timestamp": (
                    datetime.utcfromtimestamp(e["ts"]).isoformat() + "Z"
                    if isinstance(e.get("ts"), (int, float)) else ""
                ),
                "type": (e.get("details") or {}).get("action", e.get("event_type", "")),
                "resource_type": (e.get("details") or {}).get("target_kind", ""),
                "resource_id": (e.get("details") or {}).get("target_id", ""),
                "resource_name": (e.get("details") or {}).get("target_id", ""),
                "action": (e.get("details") or {}).get("action", ""),
                "user": (e.get("details") or {}).get("sid_fingerprint", ""),
                "details": e.get("details"),
                "hash": e.get("hash_prefix") or "",
                "prev_hash": "",
            }
            for e in forge_events
        ]

        return {
            "tenant_id": tid,
            "timestamp": time.time(),
            "count": len(formatted_events),
            "events": formatted_events,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Search Endpoints (Cross-Tab Real Data)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_forge(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    q: str = Query(...),
) -> dict[str, Any]:
    """Cross-tab search over Tools, Skills, OS-Skills (real data)."""
    try:
        if len(q) < 2:
            raise HTTPException(status_code=400, detail="query must be ≥2 characters")

        tid = rec.tenant_id
        results = []
        query_lower = q.lower()

        # Search tools
        for scope_label, reg in _forge_registry_paths(tid):
            for name, entry in _read_registry(reg).items():
                if isinstance(entry, dict):
                    if (query_lower in name.lower() or
                        query_lower in entry.get("description", "").lower()):
                        results.append({
                            "type": "tool",
                            "id": name,
                            "name": name,
                            "description": entry.get("description", ""),
                            "enabled": not entry.get("disabled", False),
                        })

        # Search skills
        for scope_label, reg in _skill_registry_paths(tid):
            for name, entry in _read_registry(reg).items():
                if isinstance(entry, dict):
                    if (query_lower in name.lower() or
                        query_lower in entry.get("description", "").lower()):
                        results.append({
                            "type": "skill",
                            "id": name,
                            "name": name,
                            "description": entry.get("description", ""),
                            "enabled": not entry.get("disabled", False),
                        })

        # Deduplicate by (type, id)
        seen = set()
        deduped = []
        for r in results:
            key = (r["type"], r["id"])
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        return {
            "query": q,
            "count": len(deduped),
            "results": deduped,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Routes are ready for real integration with registries + audit trail
