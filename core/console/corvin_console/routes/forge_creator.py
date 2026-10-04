"""``/v1/console/forge-creator`` — Tool Forge and Plugin Forge (ADR-2217).

Generate a tool or a plugin from a description, exactly the way Skill Forge
generates a skill: POST starts a run on the shared run store (``forge_runs``),
the panel polls the tenant-bound status. Every route needs a session; every
mutation needs CSRF and ``forge.create`` (ADR-0701, member-only — tools,
panels and the plugin builder never left the member gate, ADR-2095).

Endpoints:
  POST   /forge-creator/{kind}/generate    kind = tool | plugin
  GET    /forge-creator/status/{run_id}
  GET    /forge-creator/plugins            staged (forged) plugins
  GET    /forge-creator/plugins/{dirname}  files, docs, provenance, panel HTML
  DELETE /forge-creator/plugins/{dirname}

Forged plugins are staged, never installed (ADR-0244, ADR-2186): nothing here
writes a plugin registry or loads code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import audit as console_audit
from .. import auth as session_auth
from .. import forge_runs
from ..deps import require_csrf, require_session
from .license_gates import require_forge_capability

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[4]
for _extra in (_REPO / "corvin_operator", _REPO / "core" / "plugins"):
    # APPENDED, never inserted first — see skill_creator_api.py (ADR-0405).
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.append(str(_extra))

router = APIRouter(prefix="/forge-creator", tags=["forge-creator"])

Kind = Literal["tool", "plugin"]
_DIRNAME_RE = re.compile(r"^[a-z0-9_]{1,120}$")
#: Text files a forged plugin may hand back to the console; anything else is listed only.
_TEXT_SUFFIXES = {".py", ".md", ".html", ".yaml", ".json", ".txt"}
_MAX_FILE_BYTES = 256 * 1024


def _phases(kind: str) -> tuple[str, ...]:
    if kind == "tool":
        from skill_creator.tool_creator import TOOL_PHASES  # noqa: PLC0415
        return TOOL_PHASES
    from skill_creator.plugin_creator import PLUGIN_PHASES  # noqa: PLC0415
    return PLUGIN_PHASES


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_request: str = Field(..., min_length=10, max_length=4000)
    #: Plugin only — what the plugin's console panel should show. Empty = no panel.
    panel_request: str = Field(default="", max_length=2000)


def _operator_hint(exc: Exception) -> str:
    text = str(exc)
    if "claude binary not found" in text:
        return ("Claude Code CLI not found. Install it or set CORVIN_CLAUDE_BIN — "
                "generation runs on your Claude subscription.")
    if "Could not resolve authentication" in text:
        return "No engine authentication. Log in with `claude` or set ANTHROPIC_API_KEY."
    if "timed out" in text:
        return f"Engine timed out: {text}. Raise CORVIN_SKILL_CREATOR_TIMEOUT_S if the model needs longer."
    return text


@router.post("/{kind}/generate", status_code=202)
async def generate(
    kind: Kind,
    req: GenerateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _lic: Annotated[session_auth.SessionRecord, Depends(require_forge_capability)],
) -> Dict[str, Any]:
    request = req.user_request.strip()
    if len(request) < 10:
        raise HTTPException(status_code=400, detail="Request must be at least 10 characters")
    if kind == "tool" and req.panel_request.strip():
        raise HTTPException(status_code=400, detail="panel_request applies to plugins only")
    gate_text = request + ("\n\n" + req.panel_request.strip() if req.panel_request.strip() else "")
    try:
        forge_runs.check_spawn_gates(gate_text, tenant_id=rec.tenant_id,
                                     sid_fingerprint=rec.sid_fingerprint, kind=kind)
    except forge_runs.GenerationRefused as refused:
        raise HTTPException(status_code=403, detail=str(refused))

    tenant_id = rec.tenant_id
    run_id = forge_runs.new_run(tenant_id=tenant_id, kind=kind, phases=_phases(kind),
                                sid_fingerprint=rec.sid_fingerprint)

    if kind == "tool":
        def work(progress: forge_runs.ProgressCb) -> Dict[str, Any]:
            from skill_creator.tool_creator import ToolCreatorOrchestrator  # noqa: PLC0415
            orch = ToolCreatorOrchestrator(tenant_id=tenant_id, progress_cb=progress)
            forge_runs.update_run(run_id, engine=orch.engine_id)
            tool = asyncio.run(orch.create_tool(request))
            return {"target_id": tool["name"], "phase": "promotion", "tool": tool,
                    "message": f"Tool '{tool['name']}' passed its sandbox tests and is registered."}
        success, failure = "tool.generated_created", "tool.generated_creation_failed"
    else:
        panel_request = req.panel_request.strip()

        def work(progress: forge_runs.ProgressCb) -> Dict[str, Any]:
            from skill_creator.plugin_creator import PluginCreatorOrchestrator  # noqa: PLC0415
            orch = PluginCreatorOrchestrator(tenant_id=tenant_id, progress_cb=progress)
            forge_runs.update_run(run_id, engine=orch.engine_id)
            plugin = asyncio.run(orch.create_plugin(request, panel_request=panel_request))
            return {"target_id": plugin["plugin_id"], "phase": "staging", "plugin": plugin,
                    "message": (f"Plugin '{plugin['plugin_id']}' is staged under Marketplace → "
                                "Forged. It is not installed.")}
        success, failure = "plugin.forged_staged", "plugin.forged_staging_failed"

    forge_runs.spawn(run_id=run_id, kind=kind, tenant_id=tenant_id,
                     sid_fingerprint=rec.sid_fingerprint, work=work,
                     success_action=success, failure_action=failure, hint=_operator_hint)
    return {"status": "accepted", "run_id": run_id, "kind": kind,
            "message": f"{kind.capitalize()} generation started. Poll /forge-creator/status/{run_id}."}


@router.get("/status/{run_id}")
async def status(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    run = forge_runs.get_run(run_id, rec.tenant_id)
    if run is None or run.get("kind") not in ("tool", "plugin"):
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return {
        "run_id": run_id,
        "kind": run["kind"],
        "status": run["status"],
        "phase": run.get("phase", ""),
        "phases": run.get("phases", []),
        "progress": run.get("progress", 0),
        "message": run.get("message", ""),
        "engine": run.get("engine", "unknown"),
        "error": run.get("error"),
        "tool": run.get("tool"),
        "plugin": run.get("plugin"),
    }


# ── Forged plugins (staged) ────────────────────────────────────────────────

def _forged_root(tenant_id: str) -> Path:
    from plugin_builder.turn import output_dir  # noqa: PLC0415
    return Path(output_dir(tenant_id))


def _provenance(dest: Path) -> Optional[Dict[str, Any]]:
    from skill_creator.plugin_creator import PROVENANCE_FILE  # noqa: PLC0415
    try:
        data = json.loads((dest / PROVENANCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("generator") == "plugin_forge" else None


def _forged_dir(tenant_id: str, dirname: str) -> Path:
    """A forged plugin's directory — only names this tenant's Plugin Forge wrote."""
    if not _DIRNAME_RE.match(dirname or ""):
        raise HTTPException(status_code=400, detail="invalid plugin directory name")
    root = _forged_root(tenant_id).resolve()
    dest = (root / dirname)
    if dest.is_symlink() or not dest.is_dir() or dest.resolve().parent != root:
        raise HTTPException(status_code=404, detail="forged plugin not found")
    if _provenance(dest) is None:
        # Scaffolds written by the chat /plugin-builder carry no forge provenance;
        # they are listed under Settings → Plugins, not managed here.
        raise HTTPException(status_code=404, detail="forged plugin not found")
    return dest


def _summary(dest: Path, prov: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "dirname": dest.name,
        "plugin_id": prov.get("plugin_id"),
        "display_name": prov.get("display_name"),
        "kind": prov.get("kind"),
        "tier": prov.get("tier"),
        "quality": prov.get("quality"),
        "review_skipped": bool(prov.get("review_skipped")),
        "risk_flags": prov.get("risk_flags") or [],
        "created_at": prov.get("created_at"),
        "has_panel": bool(prov.get("panel")),
        "installed": False,
        "origin_on_install": "community",
        "signed": False,
    }


@router.get("/plugins")
async def list_forged(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    root = _forged_root(rec.tenant_id)
    items: List[Dict[str, Any]] = []
    if root.is_dir():
        for dest in sorted(root.iterdir()):
            if dest.is_symlink() or not dest.is_dir():
                continue
            prov = _provenance(dest)
            if prov is not None:
                items.append(_summary(dest, prov))
    items.sort(key=lambda i: i.get("created_at") or 0, reverse=True)
    return {"plugins": items, "count": len(items)}


@router.get("/plugins/{dirname}")
async def get_forged(
    dirname: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    dest = _forged_dir(rec.tenant_id, dirname)
    prov = _provenance(dest) or {}
    files: List[Dict[str, Any]] = []
    for path in sorted(dest.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = str(path.relative_to(dest))
        entry: Dict[str, Any] = {"path": rel, "size": path.stat().st_size}
        if path.suffix in _TEXT_SUFFIXES and entry["size"] <= _MAX_FILE_BYTES:
            entry["content"] = path.read_text(encoding="utf-8", errors="replace")
        files.append(entry)
    panel_html = None
    panel = prov.get("panel") or None
    if panel:
        index = dest / "panel" / "index.html"
        if index.is_file() and not index.is_symlink():
            panel_html = index.read_text(encoding="utf-8", errors="replace")
    return {**_summary(dest, prov), "request": prov.get("request"),
            "findings": prov.get("findings") or [], "warnings": prov.get("warnings") or [],
            "egress_hosts": prov.get("egress_hosts") or [], "engine": prov.get("engine"),
            "panel": panel, "panel_html": panel_html, "files": files}


@router.delete("/plugins/{dirname}")
async def delete_forged(
    dirname: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _lic: Annotated[session_auth.SessionRecord, Depends(require_forge_capability)],
) -> Dict[str, Any]:
    dest = _forged_dir(rec.tenant_id, dirname)
    plugin_id = (_provenance(dest) or {}).get("plugin_id") or dirname
    shutil.rmtree(dest)
    try:
        from plugin_builder import index_store  # noqa: PLC0415
        index_store.remove(rec.tenant_id, str(dest))
    except Exception as exc:  # noqa: BLE001 — the listing is best-effort; the files are gone
        logger.warning("forged plugin %s: index cleanup failed: %s", dirname, exc)
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="plugin.forged_deleted", target_kind="generated_plugin", target_id=plugin_id,
    )
    return {"ok": True, "dirname": dirname}
