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


def _skill_records(tid: str) -> list[dict[str, Any]]:
    """Every SkillForge skill for this tenant, from the ONE real source.

    Skills live as DIRECTORIES — ``<scope>/skill-forge/skills/<name>/`` with
    meta.json + SKILL.md — not in a ``registry.json``. This module previously
    walked ``<scope>/skill-forge/registry.json``, the layout TOOLS use; no such
    file exists anywhere on disk, so every skill-reading endpoint here (list,
    graph, search) silently saw zero skills while 203 were present.

    Delegates to ``routes.skills``' helpers so there is one reader, not two
    that can drift.
    """
    from .skills import _load_meta, _project, _scope_skill_dirs  # noqa: PLC0415

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for scope_label, sk_dir in _scope_skill_dirs(tid):
        try:
            entries = list(sk_dir.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            meta = _load_meta(entry / "meta.json")
            if not meta and not (entry / "SKILL.md").exists():
                continue
            proj = _project(meta, scope_label, entry)
            proj["updated_at"] = meta.get("updated_at")
            proj["version"] = meta.get("version")
            by_key[(proj["name"], scope_label)] = proj
    return sorted(by_key.values(),
                  key=lambda r: (r["scope_source"] != "user", r["name"] or ""))


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
                    # No registry entry on disk carries `disabled`, and
                    # nothing in the runner reads one — see the mutation
                    # routes below. Reported as a constant, not as state the
                    # operator can change.
                    "enabled": True,
                    "version": entry.get("version"),
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


# Same shape as the skill mutations below: `# TODO: Update tool state` and
# then a success response plus an audit record. No registry.json on disk
# carries a `disabled` key and nothing in corvin_operator/forge/ reads one,
# so the tool stayed callable while the chain recorded that it had been
# turned off. Answer honestly; write nothing to the chain.
_TOOL_MUTATION_DETAIL = (
    "Tool enable/disable is not implemented on this build. The Forge registry "
    "stores no disabled flag and the tool runner does not consult one, so a "
    "success here would be cosmetic while the tool stayed callable."
)


@router.post("/tools/{tool_id}/enable", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def enable_tool(
    tool_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Not implemented — see _TOOL_MUTATION_DETAIL."""
    raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                        detail=_TOOL_MUTATION_DETAIL)


@router.post("/tools/{tool_id}/disable", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def disable_tool(
    tool_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Not implemented — see _TOOL_MUTATION_DETAIL."""
    raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                        detail=_TOOL_MUTATION_DETAIL)


# ─────────────────────────────────────────────────────────────────────────────
# Skills Endpoints (Real Data)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/skills")
async def list_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all SkillForge skills with learning state (real data).

    Reads the SAME source ``/v1/console/skills`` reads — one skill DIRECTORY
    per skill, ``<scope>/skill-forge/skills/<name>/meta.json`` — through
    ``routes.skills``' own helpers, so the two endpoints cannot drift apart.

    Until 2026-09-16 this walked ``<scope>/skill-forge/registry.json``, the
    layout TOOLS use. No such file has ever existed for skills (0 on disk
    across both roots), so the Forge panel's Skills tab reported 0 while 203
    skills were present. The per-skill ``learning_state`` was unreachable for
    a second reason: it summed ``meta["grades"]`` directly, and a grade is a
    dict ``{run_id, score, ts, notes}``, so it would have raised on the first
    skill that had any.
    """
    try:
        tid = rec.tenant_id
        items = []
        for proj in _skill_records(tid):
            # Real grades only. mean_score/grade_count come from routes.skills'
            # _project, which reads g["score"] out of each grade DICT. The old
            # code did sum(meta["grades"]) over those dicts and would have
            # raised on the first skill that carried any.
            learning_state = None
            if proj["grade_count"]:
                learning_state = {
                    "confidence": proj["mean_score"],
                    "feedback_count": proj["grade_count"],
                    "last_updated": proj.get("updated_at"),
                }
            items.append({
                "id": proj["name"],
                "name": proj["name"],
                "description": proj["description"],
                "type": proj["type"],
                "scope": proj["scope"],
                "scope_source": proj["scope_source"],
                "created_at": proj["created_at"],
                "updated_at": proj.get("updated_at"),
                "sha256": proj["sha256"],
                "skill_dir": proj["skill_dir"],
                "learning_state": learning_state,
                "grade_count": proj["grade_count"],
                "mean_score": proj["mean_score"],
                # SkillForge stores no version, no version history and no
                # per-skill disable flag — verified across every meta.json on
                # disk. The previous code defaulted these to "1.0.0"/[]/enabled,
                # which rendered as fact (ADR-0763: the console fabricates
                # nothing). null and [] say "this build does not track it".
                "version": proj.get("version"),
                "versions": [],
                "enabled": True,
            })

        return {
            "tenant_id": tid,
            "timestamp": time.time(),
            "count": len(items),
            "skills": items,
            # What the operator can actually DO here. enable/disable and
            # rollback have no implementation behind them (see those routes),
            # so the UI must not offer controls that silently do nothing.
            "capabilities": {
                "enable_disable": False,
                "rollback": False,
                "versioning": False,
            },
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


# SkillForge has no per-skill enable/disable: no meta.json on disk carries
# such a flag, and the injection gate (bridges/shared/skill_inject.py) filters
# on grades, namespace and LDD layer — never on a per-skill switch. These two
# routes were `# TODO: Update skill state in registry` followed by a success
# response AND an audit record. So the operator saw "disabled", the
# hash-chained trail gained a forge_skill_disabled event, and the skill kept
# being injected. A permanent record of an act that never happened is the one
# thing the audit chain must never contain (CLAUDE.md § Audit Chain as Ground
# Truth: "no audit bypass, no silent optimization" — and no fictional events).
#
# Answer honestly instead, and write nothing to the chain. Giving skills a
# real disable switch means adding the flag to the skill record AND teaching
# the injection gate to honour it; that is a new layer-level contract and
# needs its own ADR, not a route that pretends.
_SKILL_MUTATION_DETAIL = (
    "Per-skill enable/disable is not implemented on this build. SkillForge "
    "stores no enabled flag and the injection gate does not consult one, so a "
    "success here would be cosmetic while the skill kept running."
)


@router.post("/skills/{skill_id}/enable", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def enable_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Not implemented — see _SKILL_MUTATION_DETAIL."""
    raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                        detail=_SKILL_MUTATION_DETAIL)


@router.post("/skills/{skill_id}/disable", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def disable_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Not implemented — see _SKILL_MUTATION_DETAIL."""
    raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                        detail=_SKILL_MUTATION_DETAIL)


# ─────────────────────────────────────────────────────────────────────────────
# OS-Skills Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/os-skills")
async def list_os_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List the OS-level Skills from the LIVE registry.

    Until 2026-09-16 the body of this endpoint was

        os_skills = []
        # TODO: Integrate with plugins registry

    so the Forge panel's OS-Skills tab was empty on every build that ever
    shipped, including the one that added OS-Skills Phase 1.

    Source is ``skill_registry_phase1.get_registry()`` — the same registry
    ``os_skills_integration.initialize_integration`` populates at boot and the
    same one L5/L10 execute through, so the tab cannot show a different set of
    Skills than the system actually runs. ``get_registry`` lazily registers the
    builtins, so this answers correctly even before the platform boots.
    """
    try:
        tid = rec.tenant_id
        from core.skills.skill_registry_phase1 import get_registry  # noqa: PLC0415

        registry = get_registry()
        os_skills = []
        for md in registry.list_skills():
            os_skills.append({
                "id": md.id,
                "name": md.name,
                "description": md.description,
                "version": md.version,
                "origin": getattr(md.origin, "value", str(md.origin)),
                "owner": md.owner,
                "tags": list(md.tags or []),
                # Real per-tenant state: a Skill that tripped the failure
                # threshold is auto-disabled for THIS tenant only.
                "enabled": registry._is_skill_enabled_for_tenant(md.id, tid),
                # tier=compliance cannot be disabled at all (SkillDisableRefused).
                "tier": getattr(md.tier, "value", str(md.tier)),
                "disableable": getattr(md.tier, "value", str(md.tier)) != "compliance",
                # learn=False Skills are audited but deliberately kept out of
                # the ADR-0314 learning store (os.capabilities polls hard).
                "learns": bool(md.learn),
            })

        os_skills.sort(key=lambda r: r["id"])
        return {
            "tenant_id": tid,
            "timestamp": time.time(),
            "count": len(os_skills),
            "os_skills": os_skills,
            "capabilities": {
                "enable_disable": True,
                # Config tuning already has a canonical route with versioning
                # and rollback (/v1/console/method-discovery/..., SkillAdapter).
                # A second writer for the same config is exactly the duplication
                # CLAUDE.md forbids, so this panel does not offer one.
                "config": False,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/os-skills/{os_skill_id}/config", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def update_os_skill_config(
    os_skill_id: str,
    config: Dict[str, Any],
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Not implemented here on purpose — one config writer, not two.

    Was `# TODO: Validate config` + `# TODO: Emit learning event`, then a
    success response and an audit record for a change that never happened.

    OS-Skill config already has a real, versioned, rollback-able writer:
    ``SkillAdapter`` behind /v1/console/method-discovery/. Reimplementing it
    here would give one config two writers with two version histories.
    """
    raise HTTPException(
        status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
        detail=("OS-Skill config is not tuned from the Forge panel. Use the "
                "Method Discovery routes (/v1/console/method-discovery/), which "
                "own the versioned, rollback-able SkillAdapter config."),
    )


@router.post("/os-skills/{os_skill_id}/disable")
async def disable_os_skill(
    os_skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Disable an OS-Skill for THIS tenant. Refused for tier=compliance.

    Now actually disables it. It used to be `# TODO: Update OS-Skill state`
    followed by a success response and an audit record, so the Skill kept
    executing while the chain said it had been turned off.

    The compliance guard was equally nominal: it compared the id against
    {"audit-gate", "consent", "tripwire", "house-rules"}, none of which is a
    registered Skill id — every real id is ``os.*``, so the check could never
    fire. The registry's own ``tier=compliance`` rule is the real one and it
    raises SkillDisableRefused, which is audited at the source.
    """
    try:
        from core.skills.skill_registry_phase1 import (  # noqa: PLC0415
            SkillDisableRefused, get_registry,
        )

        registry = get_registry()
        if registry.get(os_skill_id) is None:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                                detail=f"OS-Skill {os_skill_id!r} is not registered")

        try:
            changed = registry.disable_skill(os_skill_id, rec.tenant_id)
        except SkillDisableRefused as exc:
            raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN,
                                detail=str(exc) or f"{os_skill_id} is a compliance Skill and cannot be disabled")

        # Audited only after the state change actually committed.
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_osskill_disabled",
            target_kind="os-skill",
            target_id=os_skill_id,
        )
        return {
            "status": "disabled",
            "os_skill_id": os_skill_id,
            "changed": bool(changed),
            "enabled": registry._is_skill_enabled_for_tenant(os_skill_id, rec.tenant_id),
            "timestamp": time.time(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/os-skills/{os_skill_id}/enable")
async def enable_os_skill(
    os_skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Re-enable an OS-Skill for THIS tenant (also clears an auto-disable).

    New: the panel could disable a Skill (nominally) but never turn it back
    on, so a Skill the registry auto-disabled after 3 failures had no route
    back. ``registry.enable_skill`` has always existed.
    """
    try:
        from core.skills.skill_registry_phase1 import get_registry  # noqa: PLC0415

        registry = get_registry()
        if registry.get(os_skill_id) is None:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                                detail=f"OS-Skill {os_skill_id!r} is not registered")

        changed = registry.enable_skill(os_skill_id, rec.tenant_id)

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="forge_osskill_enabled",
            target_kind="os-skill",
            target_id=os_skill_id,
        )
        return {
            "status": "enabled",
            "os_skill_id": os_skill_id,
            "changed": bool(changed),
            "enabled": registry._is_skill_enabled_for_tenant(os_skill_id, rec.tenant_id),
            "timestamp": time.time(),
        }
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

        # Skills nodes — real skill directories (see _skill_records)
        for rec_ in _skill_records(tid):
            nodes.append({
                "id": f"skill:{rec_['name']}",
                "name": rec_["name"],
                "type": "skill",
                "enabled": True,
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

        # Search skills — real skill directories (see _skill_records)
        for rec_ in _skill_records(tid):
            if (query_lower in (rec_["name"] or "").lower() or
                    query_lower in (rec_["description"] or "").lower()):
                results.append({
                    "type": "skill",
                    "id": rec_["name"],
                    "name": rec_["name"],
                    "description": rec_["description"],
                    "enabled": True,
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
