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

from .. import auth as session_auth
from ..deps import require_session
from .. import audit as console_audit
from .audit_tail import _parse_chain_file
from .. import _bootstrap
from ..deps import require_session_csrf_on_mutation

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


_MOD_MODULE: Any = False  # False = not tried yet, None = not available on this build


def _mod_generator() -> Any:
    """The mod generator module, loaded from its FILE so the quiz-card grammar has ONE definition.

    ``plugin_builder/generators/mod.py`` is stdlib-only, so loading it by path does not run the
    plugin-builder package ``__init__`` (which pulls in the scaffold writer) inside the console process.
    ``None`` when the file is not there — a wheel build without the plugin-builder.
    """
    global _MOD_MODULE
    if _MOD_MODULE is not False:
        return _MOD_MODULE
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415

    path = Path(__file__).resolve().parents[3] / "plugins" / "plugin_builder" / "generators" / "mod.py"
    _MOD_MODULE = None
    if path.is_file():
        spec = importlib.util.spec_from_file_location("_corvin_mod_generator", path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules["_corvin_mod_generator"] = module  # dataclasses resolve their module through sys.modules
            try:
                spec.loader.exec_module(module)
                _MOD_MODULE = module
            except Exception:  # noqa: BLE001 - an unreadable generator means "not available", never a 500
                sys.modules.pop("_corvin_mod_generator", None)
    return _MOD_MODULE


def _quiz_card_count(name: str, skill_dir: Path) -> int | None:
    """Quiz cards in a skill's ``SKILL.md``, counted with the mod generator's own parser.

    ``None`` means "unknown" (no generator on this build, an unreadable file or a malformed quiz line) and
    must render as unknown, never as 0: a malformed quiz is not the same thing as a skill without one.
    """
    gen = _mod_generator()
    if gen is None:
        return None
    try:
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return len(gen.parse_quiz_cards(name, body))
    except gen.ModSpecError:
        return None


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
            proj["quiz_cards"] = _quiz_card_count(proj["name"], entry)
            by_key[(proj["name"], scope_label)] = proj
    return sorted(by_key.values(),
                  key=lambda r: (r["scope_source"] != "user", r["name"] or ""))


# ─────────────────────────────────────────────────────────────────────────────
# Router Creation
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)], tags=["console-forge-unified"])


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
                # The same quiz-card count a generated mod serves; null = unknown, not zero.
                "quiz_cards": proj.get("quiz_cards"),
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


#: How far back from the END of the tenant chain /mod-activity may read. The live chain is hundreds of MB and
#: grows by ~0.65 MiB an hour (measured 2026-10-26: a 4 MiB window reached back only ~6 hours and showed
#: nothing), so this is a window, not the history — and the response says so. The scan reads backwards in
#: chunks and stops at ``_MOD_ACTIVITY_LIMIT`` events, so a busy tenant costs one chunk, not the window.
_MOD_ACTIVITY_WINDOW_BYTES = 64 * 1024 * 1024
_MOD_ACTIVITY_CHUNK_BYTES = 1024 * 1024
_MOD_ACTIVITY_LIMIT = 50
_MOD_EVENT = "mod.command_executed"


def _scan_mod_events(chain: Path, tid: str) -> tuple[list[dict[str, Any]], int, int, bool]:
    """Newest-first ``mod.command_executed`` records of tenant *tid*, scanning *chain* backwards.

    Returns ``(events, bytes_scanned, chain_bytes, limit_reached)``. Lines are re-joined across chunk
    boundaries, so a record split by a boundary is neither lost nor parsed in halves.
    """
    try:
        size = chain.stat().st_size
    except OSError:
        return [], 0, 0, False
    needle = _MOD_EVENT.encode()
    events: list[dict[str, Any]] = []
    pos, scanned, carry = size, 0, b""
    limit_reached = False
    try:
        with chain.open("rb") as fh:
            while pos > 0 and scanned < _MOD_ACTIVITY_WINDOW_BYTES and not limit_reached:
                step = min(_MOD_ACTIVITY_CHUNK_BYTES, pos, _MOD_ACTIVITY_WINDOW_BYTES - scanned)
                pos -= step
                fh.seek(pos)
                data = fh.read(step) + carry
                scanned += step
                if pos > 0:
                    nl = data.find(b"\n")
                    if nl == -1:  # one line longer than the chunk: keep growing it
                        carry = data
                        continue
                    carry, data = data[:nl], data[nl + 1:]  # the first line may be cut: finish it next round
                else:
                    carry = b""
                for raw in reversed(data.split(b"\n")):
                    if needle not in raw:  # cheap pre-check before parsing JSON
                        continue
                    try:
                        obj = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if obj.get("event_type") != _MOD_EVENT:
                        continue
                    d = obj.get("details") or {}
                    if d.get("tenant_id") not in (None, tid):  # a foreign tenant inside this chain: skip
                        continue
                    events.append({
                        "ts": obj.get("ts"),
                        "mod": d.get("mod"),
                        "command": d.get("command"),
                        "skill": d.get("skill"),
                        "score": d.get("score"),
                    })
                    if len(events) >= _MOD_ACTIVITY_LIMIT:
                        limit_reached = True
                        break
    except OSError:
        return events, scanned, size, limit_reached
    return events, scanned, size, limit_reached


@router.get("/mod-activity")
def mod_activity(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Recent actions reported by Claude Code mods, from this tenant's audit chain (read-only).

    The events are MOD-REPORTED (ADR-2285): attribution, not proof of what the mod did. The tenant comes
    from the authenticated session and picks the chain file; a record that names another tenant is skipped.

    Only the last ``_MOD_ACTIVITY_WINDOW_BYTES`` of the chain are read, so ``window.complete`` is false for a
    long chain and the UI must show the window next to the list — a short list is "none in the window", never
    "none ever".
    """
    tid = rec.tenant_id
    chain = _forge_paths.tenant_audit_chain(tid)
    events, scanned, size, limit_reached = _scan_mod_events(chain, tid)
    return {
        "tenant_id": tid,
        "timestamp": time.time(),
        "reported_by": "mod",
        "window": {
            "bytes_scanned": scanned,
            "chain_bytes": size,
            # complete = the whole chain was read AND the list was not cut at the limit: only then does an
            # empty or short list mean "none recorded".
            "complete": scanned >= size and not limit_reached,
            "limit_reached": limit_reached,
        },
        "limit": _MOD_ACTIVITY_LIMIT,
        "count": len(events),
        "events": events,
    }


# SkillForge keeps no version history (``versions`` is always [] above), so there
# is nothing to roll back to. This route used to be `# TODO: Update skill
# version` followed by an audit record ``forge_skill_rollback`` and a
# ``rolled_back`` answer: a permanent chain record of an act that never
# happened. Same fix as enable/disable: 501, nothing written to the chain.
# (Canary rollback of an autonomous-forge candidate is a different, real route:
# POST /autonomous-forge/rollback.)
_SKILL_ROLLBACK_DETAIL = (
    "Skill rollback is not implemented on this build. SkillForge stores no "
    "version history, so there is no earlier version to restore. For an "
    "autonomous-forge canary use the canary Rollback control."
)


@router.post("/skills/{skill_id}/rollback", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
async def rollback_skill(
    skill_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    version: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Not implemented — see _SKILL_ROLLBACK_DETAIL. Writes nothing to the chain."""
    raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                        detail=_SKILL_ROLLBACK_DETAIL)


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
# Plugins Endpoint (staged, forged plugins)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/plugins")
async def list_forged_plugins(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List this tenant's staged forged plugins for the Forge panel's Plugins tab.

    Until 2026-10-24 the tab called ``GET /forge/plugins``, which had no route
    (404, swallowed by the page loader), so it always read "No plugins yet".
    Reads the same staging directory Plugin Forge writes and Marketplace ->
    Forged lists (``forge_creator.forged_summaries``), so there is one reader.
    Forged plugins are staged, never installed: ``enabled`` is false and
    ``installed`` is false until the operator installs one from Marketplace.
    """
    try:
        from .forge_creator import forged_summaries  # noqa: PLC0415

        items = [
            {
                "id": s["dirname"],
                "dirname": s["dirname"],
                "plugin_id": s.get("plugin_id"),
                "name": s.get("display_name") or s.get("plugin_id") or s["dirname"],
                "description": s.get("kind") or "",
                "kind": s.get("kind"),
                "tier": s.get("tier"),
                "quality": s.get("quality"),
                "enabled": False,
                "installed": False,
                "created_at": s.get("created_at"),
            }
            for s in forged_summaries(rec.tenant_id)
        ]
        return {"tenant_id": rec.tenant_id, "timestamp": time.time(),
                "count": len(items), "plugins": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

# Which chain records are "Forge events".
#
# Until 2026-10-24 this endpoint kept only ``console.action_performed`` records
# whose ``details.action`` started with ``forge_`` and read only the last 256 KiB
# of the chain. The only writers of such an action were the OS-Skill toggles
# and a stub, so the tab showed 0 events on an install whose chain held >1,500
# ``forge.tool_executed``, ~70 ``forge_run.*`` and 26 ``forge_bundle.*`` records.
# The real Forge events have four shapes:
#   * their own event_type: ``forge.*``, ``forge_run.*``, ``forge_bundle.*``,
#     ``forge_intake.*``, ``layer_forge.*``;
#   * ``console.action_performed`` / ``console.action_failed`` whose
#     ``details.action`` names a Forge act: ``forge_osskill_*``, ``skill.*``
#     (generated / manual / canary / promote), ``tool.*``, ``plugin.forged_*``.
_FORGE_EVENT_PREFIXES: tuple[str, ...] = (
    "forge.", "forge_", "layer_forge.", "tool.",
)
# SkillForge's own records (``skill_forge.registry`` / MCP: the skill name is the record's
# ``tool`` field) and the Tool-Forge runner's gate records. Exact names, NOT a ``skill.`` prefix:
# ``skill.executed`` / ``skill.model_selector.*`` are OS-skill telemetry written on every turn.
_FORGE_EXACT_EVENTS: frozenset[str] = frozenset({
    "skill.create", "skill.create_forced_scope", "skill.delete", "skill.grade",
    "skill.promote", "skill.namespace_denied", "skill.scope_denied",
    "skill.bootstrap_grade_failed",
    "code.exec_attempt", "policy.import_denied", "policy.namespace_denied",
})
_FORGE_ACTION_PREFIXES: tuple[str, ...] = (
    "forge", "layer_forge", "skill.", "tool.", "plugin.forged",
)
_CONSOLE_ACTION_EVENTS = frozenset({"console.action_performed", "console.action_failed"})
# Cheap byte pre-filter so the reverse scan json-parses only candidate lines.
_FORGE_LINE_NEEDLES: tuple[bytes, ...] = (
    b"forge", b'"skill.', b'"tool.', b"plugin.forged", b'"code.exec_attempt"', b'"policy.',
)
# How far back the endpoint reads. The chain is append-only and can be hundreds
# of MB; the newest records matter, and the response says when the window did
# not reach back to ``since`` (``complete: false``).
_AUDIT_SCAN_BYTES = 64 * 1024 * 1024
_AUDIT_MAX_EVENTS = 100
# Detail keys that are metadata-only and safe to re-serve (audit_tail's rule:
# no free text, no user identifiers, no tool input/output).
_AUDIT_EXTRA_DETAIL_KEYS: frozenset[str] = frozenset({
    "run_kind", "artifact_kind", "artifact_id", "event_count", "truncated",
    "item_id", "format", "data_class", "reason_code", "worker_id", "decision",
    "cache_hit", "sandbox", "bundle_id", "version", "entry_id", "to_status",
    "from_status", "gate_id", "kind", "quality", "plugin_id", "skill_id",
    "tool_name", "exit_code", "duration_ms", "status", "run_id",
    "sha", "scope", "type", "persona", "language", "outcome",
})


def _forge_event_effective_type(rec_obj: dict[str, Any]) -> str:
    """The name an operator recognises: the action for console records, else the event_type."""
    et = str(rec_obj.get("event_type") or "")
    if et in _CONSOLE_ACTION_EVENTS:
        return str((rec_obj.get("details") or {}).get("action") or et)
    return et


def _is_forge_event(rec_obj: dict[str, Any]) -> bool:
    et = str(rec_obj.get("event_type") or "")
    if et.startswith(_FORGE_EVENT_PREFIXES) or et in _FORGE_EXACT_EVENTS:
        return True
    if et in _CONSOLE_ACTION_EVENTS:
        action = str((rec_obj.get("details") or {}).get("action") or "")
        return action.startswith(_FORGE_ACTION_PREFIXES)
    return False


def _forge_resource(rec_obj: dict[str, Any]) -> tuple[str, str]:
    """(resource_type, resource_id) of a Forge chain record."""
    et = str(rec_obj.get("event_type") or "")
    d = rec_obj.get("details") or {}
    if et in _CONSOLE_ACTION_EVENTS:
        kind = str(d.get("target_kind") or "")
        kind = {"generated_plugin": "plugin", "generated_skill": "skill",
                "generated_tool": "tool", "os_skill": "os-skill"}.get(kind, kind)
        return kind, str(d.get("target_id") or "")
    if et.startswith("skill."):
        return "skill", str(rec_obj.get("tool") or d.get("skill_id") or "")
    if et == "forge.tool_executed" or et.startswith(("tool.", "code.", "policy.")):
        return "tool", str(d.get("tool_name") or rec_obj.get("tool") or "")
    if et.startswith("forge_run."):
        return "run", str(d.get("run_id") or rec_obj.get("run_id") or "")
    if et.startswith("forge_bundle."):
        return "bundle", str(d.get("bundle_id") or d.get("artifact_id") or d.get("entry_id") or "")
    if et.startswith("forge_intake."):
        return "intake", str(d.get("item_id") or "")
    if et.startswith("layer_forge."):
        return "layer", str(d.get("layer_id") or d.get("entry_id") or d.get("id") or "")
    return "", ""


def _reverse_lines(path: Path, max_bytes: int) -> tuple[Any, list[bool]]:
    """Yield the chain's lines newest-first, reading at most ``max_bytes`` from the end.

    Returns (generator, flag) where flag[0] becomes True when the generator
    reached the start of the file (the window covered the whole chain).
    """
    reached_start = [False]

    def gen():
        chunk = 1 << 20
        try:
            fh = path.open("rb")
        except OSError:
            reached_start[0] = True
            return
        with fh:
            fh.seek(0, 2)
            pos = fh.tell()
            stop = max(0, pos - max_bytes)
            carry = b""
            while pos > stop:
                n = min(chunk, pos - stop)
                pos -= n
                fh.seek(pos)
                parts = (fh.read(n) + carry).split(b"\n")
                carry = parts[0]
                for line in reversed(parts[1:]):
                    if line.strip():
                        yield line
            if stop == 0:
                reached_start[0] = True
                if carry.strip():
                    yield carry

    return gen(), reached_start


def _scan_forge_events(chain: Path, since_ts: float | None, limit: int,
                       event_filter: str) -> tuple[list[dict[str, Any]], bool]:
    """Newest-first Forge records of ``chain``. Second value: the scan covered the whole requested window."""
    from .audit_tail import _sanitize_details  # noqa: PLC0415

    lines, reached_start = _reverse_lines(chain, _AUDIT_SCAN_BYTES)
    out: list[dict[str, Any]] = []
    covered_since = False
    for raw in lines:
        if not any(n in raw for n in _FORGE_LINE_NEEDLES):
            # Still need the timestamp bound: cheap check only on candidates, so
            # a long run of non-Forge lines cannot prove "older than since";
            # the byte window bounds the work instead.
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        ts = obj.get("ts")
        if since_ts is not None and isinstance(ts, (int, float)) and ts < since_ts:
            covered_since = True
            break
        if not _is_forge_event(obj):
            continue
        effective = _forge_event_effective_type(obj)
        if event_filter and not effective.startswith(event_filter):
            continue
        d = obj.get("details") or {}
        safe = _sanitize_details(d)
        safe.update({k: v for k, v in d.items()
                     if k in _AUDIT_EXTRA_DETAIL_KEYS and isinstance(v, (str, int, float, bool))})
        rtype, rid = _forge_resource(obj)
        h = str(obj.get("hash") or "")
        ph = str(obj.get("prev_hash") or "")
        out.append({
            "id": h[:16] or f"{ts}",
            "timestamp": (datetime.utcfromtimestamp(ts).isoformat() + "Z"
                          if isinstance(ts, (int, float)) else ""),
            "type": effective,
            "event_type": str(obj.get("event_type") or ""),
            "severity": obj.get("severity", "INFO"),
            "resource_type": rtype,
            "resource_id": rid,
            "resource_name": rid or effective,
            "action": effective,
            "user": str(d.get("sid_fingerprint") or ""),
            "details": safe,
            "hash": h[:8],
            "prev_hash": ph[:8],
            "_ts": ts if isinstance(ts, (int, float)) else 0.0,
        })
        if len(out) >= limit:
            covered_since = True  # the page is full: nothing older is needed
            break
    complete = covered_since or reached_start[0]
    for e in out:
        e.pop("_ts", None)
    return out, complete


@router.get("/audit")
async def get_audit_trail(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    since: Optional[str] = Query(None),
    event_type: str = Query("forge_*"),
) -> dict[str, Any]:
    """Hash-chained audit events for Forge operations (real data, ADR-0232/0537).

    Reads the tenant chain (``<tenant>/global/forge/audit.jsonl``, ADR-0007
    one-chain-per-tenant) newest-first, up to 64 MiB from its end, and keeps
    every record that is a Forge event: the Forge families
    (``forge.*``, ``forge_run.*``, ``forge_bundle.*``, ``forge_intake.*``,
    ``layer_forge.*``) and the console actions that name a Forge act
    (``forge_osskill_*``, ``skill.*``, ``tool.*``, ``plugin.forged_*``).

    ``event_type`` is an optional prefix on the event name an operator sees
    (the action for console records); the legacy default ``forge_*`` means
    "all Forge events". ``complete`` is false when the scan window ended
    before reaching ``since``, so the UI never claims a full history it did
    not read.
    """
    try:
        tid = rec.tenant_id

        since_ts: float | None = None
        if since:
            try:
                since_ts = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
            except ValueError:
                since_ts = None

        flt = (event_type or "").strip()
        flt = "" if flt in ("", "*", "forge_*", "all") else flt.rstrip("*")

        chain = _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl"
        events, complete = _scan_forge_events(chain, since_ts, _AUDIT_MAX_EVENTS, flt) if chain.exists() else ([], True)

        return {
            "tenant_id": tid,
            "timestamp": time.time(),
            "count": len(events),
            "complete": complete,
            "window_bytes": _AUDIT_SCAN_BYTES,
            "events": events,
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
