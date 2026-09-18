"""
Corvin-Knowledge console routes (marketplace plugin panel backend).

Mounted under the console router, so the live paths are
``/v1/console/plugins/corvin-knowledge/{config,graph,sync,init,cleanup}``.
GETs require a console session; every mutating POST additionally requires
the CSRF token, like every other console route.

The graph itself is read from the plugin's local checkout of Corvin-Knowledge
(``graph/entities.jsonl`` + ``graph/relations.jsonl``); ``sync`` drives git in
that checkout. Nothing here is a Flask blueprint: the console is FastAPI and
``flask`` is not installed in its venv, so a blueprint import would remove the
whole console from the gateway process on the next restart.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import require_csrf, require_session

router = APIRouter(
    prefix="/plugins/corvin-knowledge",
    tags=["console-corvin-knowledge"],
)

# ────────────────────────────────────────────────────────
# CONFIG MANAGEMENT
# ────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".claude" / "plugins" / "corvin-knowledge.json"
DEFAULT_CONFIG: Dict[str, Any] = {
    "repo_path": "~/.corvin-knowledge/",
    "remote_url": "https://github.com/CorvinLabs/Corvin-Knowledge.git",
    "auto_sync_on_query": True,
    "consistency_level": "warn",
}
_GIT_TIMEOUT_S = 30


def load_config() -> Dict[str, Any]:
    """Load plugin configuration from disk; defaults when absent or unreadable."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (OSError, ValueError):
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: Dict[str, Any]) -> None:
    """Persist plugin configuration."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


# ────────────────────────────────────────────────────────
# KNOWLEDGE GRAPH DATA
# ────────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return rows
    return rows


def load_graph_data() -> Dict[str, Any]:
    """Load entities + relations from the plugin's repository checkout."""
    config = load_config()
    graph_dir = Path(config["repo_path"]).expanduser() / "graph"
    entities = [
        {
            "id": row.get("id", ""),
            "type": row.get("type", "decision"),
            "title": row.get("title", ""),
            "status": row.get("status", "proposed"),
            "tags": row.get("tags", []),
        }
        for row in _read_jsonl(graph_dir / "entities.jsonl")
    ]
    relations = [
        {
            "from_id": row.get("from_id", ""),
            "to_id": row.get("to_id", ""),
            "relation": row.get("relation", "relates_to"),
        }
        for row in _read_jsonl(graph_dir / "relations.jsonl")
    ]
    return {"entities": entities, "relations": relations}


# ────────────────────────────────────────────────────────
# SCHEMAS
# ────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    repo_path: Optional[str] = None
    remote_url: Optional[str] = None
    auto_sync_on_query: Optional[bool] = None
    consistency_level: Optional[str] = None


class SyncRequest(BaseModel):
    sync_type: str = "pull"


# ────────────────────────────────────────────────────────
# ROUTES
# ────────────────────────────────────────────────────────

@router.get("/config")
async def get_config(session: Any = Depends(require_session)) -> Dict[str, Any]:
    return load_config()


@router.post("/config")
async def update_config(
    body: ConfigUpdate, session: Any = Depends(require_csrf)
) -> Dict[str, Any]:
    config = load_config()
    for key, value in body.model_dump(exclude_none=True).items():
        config[key] = value
    try:
        save_config(config)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "config": config}


@router.get("/graph")
async def get_graph(session: Any = Depends(require_session)) -> Dict[str, Any]:
    return load_graph_data()


def _git(args: List[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
    )


@router.post("/sync")
async def sync_repository(
    body: SyncRequest, session: Any = Depends(require_csrf)
) -> Dict[str, Any]:
    """Pull, push or both against the plugin's Corvin-Knowledge checkout."""
    sync_type = body.sync_type
    if sync_type not in ("pull", "push", "both"):
        raise HTTPException(status_code=400, detail=f"unknown sync_type {sync_type!r}")
    repo_path = Path(load_config()["repo_path"]).expanduser()
    if not repo_path.exists():
        raise HTTPException(status_code=400, detail=f"Repository not found at {repo_path}")

    result: Dict[str, Any] = {
        "status": "success",
        "message": f"Sync operation '{sync_type}' completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conflicts": [],
        "errors": [],
    }

    if sync_type in ("pull", "both"):
        try:
            _git(["fetch", "origin"], repo_path)
            merge = _git(["merge", "origin/main"], repo_path)
            if merge.returncode != 0:
                result["status"] = "conflict"
                result["conflicts"].append(f"Merge conflict: {merge.stderr}")
        except subprocess.TimeoutExpired:
            result["errors"].append("Git operation timed out")
        except OSError as exc:
            result["errors"].append(f"Pull failed: {exc}")

    if sync_type in ("push", "both") and result["status"] == "success":
        try:
            push = _git(["push", "origin", "main"], repo_path)
            if push.returncode != 0:
                result["errors"].append(f"Push failed: {push.stderr}")
        except subprocess.TimeoutExpired:
            result["errors"].append("Git push operation timed out")
        except OSError as exc:
            result["errors"].append(f"Push failed: {exc}")

    if result["errors"]:
        result["status"] = "error"
    return result


@router.post("/init")
async def init_plugin(session: Any = Depends(require_csrf)) -> Dict[str, Any]:
    """Called on plugin installation: default config + graph directory."""
    try:
        save_config(DEFAULT_CONFIG.copy())
        (Path(DEFAULT_CONFIG["repo_path"]).expanduser() / "graph").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "initialized",
        "message": "Plugin initialized successfully",
        "config": load_config(),
    }


@router.post("/cleanup")
async def cleanup_plugin(session: Any = Depends(require_csrf)) -> Dict[str, Any]:
    """Called on plugin uninstallation: remove the config file."""
    try:
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "cleaned", "message": "Plugin cleaned up successfully"}
