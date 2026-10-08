"""
Corvin-Knowledge console routes (marketplace plugin panel backend).

Mounted under the console router, so the live paths are
``/v1/console/plugins/corvin-knowledge/{config,graph,sync,init,cleanup}``.
GETs require a console session; every mutating POST additionally requires
the CSRF token, like every other console route.

The graph itself is read from the plugin's local checkout of Corvin-Knowledge —
``kb/graph/entities.jsonl`` + ``kb/graph/relations.jsonl`` (ADR-2206), the output of
``kb.py index``, translated to this panel's wire shape in ``load_graph_data()``. The
pre-cutover ``graph/`` directory (ADR-MESH-002) is dead: 707 untracked rows, no
relations, never updated since 2026-09-18 (ADR-2205 froze that design). ``sync`` drives
git in the checkout. Nothing here is a Flask blueprint: the console is FastAPI and
``flask`` is not installed in its venv, so a blueprint import would remove the whole
console from the gateway process on the next restart.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import require_csrf, require_session
from .. import kb_projection

router = APIRouter(
    prefix="/plugins/corvin-knowledge",
    tags=["console-corvin-knowledge"],
)

# ────────────────────────────────────────────────────────
# CONFIG MANAGEMENT
# ────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".claude" / "plugins" / "corvin-knowledge.json"
DEFAULT_CONFIG: Dict[str, Any] = {
    # ADR-2206: the knowledge home's real checkout (the one the board projector reads,
    # CORVIN_KB_REPO or the sibling of this repo), not the unused mesh-era convention.
    "repo_path": str(Path(os.environ.get("CORVIN_KB_REPO") or
                          Path(__file__).resolve().parents[5] / "Corvin-Knowledge")),
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


def load_graph_data(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load entities + relations from the KB's generated graph (``kb.py index``, ADR-2206)
    and translate it to this panel's wire shape. A node's id is ``uid or id`` — the SAME
    fallback ``kb.py index`` uses for an edge's ``src``/``dst``, so every edge always
    resolves to a node that exists."""
    config = config or load_config()
    graph_dir = Path(config["repo_path"]).expanduser() / "kb" / "graph"
    _entities_raw = _read_jsonl(graph_dir / "entities.jsonl")
    entities = [
        {
            "id": row.get("uid") or row.get("id", ""),
            "type": row.get("type", "decision"),
            "title": row.get("title", ""),
            "status": row.get("status", "proposed"),
            "tags": row.get("labels", []),
        }
        for row in _entities_raw
    ]
    relations = [
        {
            "from_id": row.get("src", ""),
            "to_id": row.get("dst", ""),
            "relation": row.get("rel", "relates_to"),
        }
        for row in _read_jsonl(graph_dir / "relations.jsonl")
        if not row.get("external") and row.get("resolved")
    ]
    # entities.jsonl is append-only in practice (the sync writes new rows for
    # re-proposed entities); the graph library refuses a duplicate node id
    # ("Cannot add item: item with id ADR-0010 already exists" crashed the
    # panel on the maintainer checkout, 707 rows). Last row wins per id.
    by_id: Dict[str, Dict[str, Any]] = {}
    for e in entities:
        if e["id"]:
            by_id[e["id"]] = e
    entities = list(by_id.values())
    seen_rel = set()
    unique_relations = []
    for r in relations:
        key = (r["from_id"], r["to_id"], r["relation"])
        if key in seen_rel:
            continue
        seen_rel.add(key)
        unique_relations.append(r)
    return {"entities": entities, "relations": unique_relations, "duplicate_rows": len(_entities_raw) - len(entities)}


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

# ADR-0892 (2026-09-20): the plugin is a marketplace contributor plugin whose
# record in the tenant registry carries the SAME four keys as ``settings``
# (plugin.yaml settings_schema). One store, two doors: the panel reads the
# registry's values over the file defaults when the plugin is installed, and a
# save from the panel writes the registry through the lifecycle (audited
# ``plugin.config_changed``) as well as the file the CLI reads.
_PLUGIN_ID = "corvin_knowledge"
_KEYS = ("repo_path", "remote_url", "auto_sync_on_query", "consistency_level")


def _registry_settings(tenant_id: str) -> Optional[Dict[str, Any]]:
    try:
        from .plugins import _PLUGINS_AVAILABLE, _load  # noqa: PLC0415

        if not _PLUGINS_AVAILABLE:
            return None
        record = _load(tenant_id).records.get(_PLUGIN_ID)
        return dict(record.settings) if record is not None else None
    except Exception:  # noqa: BLE001 — the file config stays the fallback
        return None


def _write_registry_settings(tenant_id: str, config: Dict[str, Any]) -> None:
    try:
        from .plugins import _PLUGINS_AVAILABLE, _lifecycle  # noqa: PLC0415

        if not _PLUGINS_AVAILABLE or _registry_settings(tenant_id) is None:
            return
        _lifecycle(tenant_id).set_settings(_PLUGIN_ID, {k: config[k] for k in _KEYS if k in config})
    except Exception:  # noqa: BLE001 — the file was written; the registry copy is best-effort
        pass


def effective_config(tenant_id: str) -> Dict[str, Any]:
    config = load_config()
    stored = _registry_settings(tenant_id)
    if stored:
        config.update({k: v for k, v in stored.items() if k in _KEYS and v is not None})
    return config


@router.get("/config")
async def get_config(session: Any = Depends(require_session)) -> Dict[str, Any]:
    return effective_config(getattr(session, "tenant_id", "_default"))


@router.post("/config")
async def update_config(
    body: ConfigUpdate, session: Any = Depends(require_csrf)
) -> Dict[str, Any]:
    tenant_id = getattr(session, "tenant_id", "_default")
    config = effective_config(tenant_id)
    for key, value in body.model_dump(exclude_none=True).items():
        config[key] = value
    try:
        save_config(config)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _write_registry_settings(tenant_id, config)
    return {"status": "saved", "config": config}


@router.get("/graph")
async def get_graph(session: Any = Depends(require_session)) -> Dict[str, Any]:
    return load_graph_data(effective_config(getattr(session, "tenant_id", "_default")))


_GIT_ENV_KEEP = ("PATH", "HOME", "LANG", "SSH_AUTH_SOCK", "GIT_SSH_COMMAND")


def _git(args: List[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # A pulled repository may carry config the console would otherwise execute: hooks
    # and an fsmonitor command are switched off, and the console's own environment
    # (API keys, tokens) is not handed to git.
    env = {k: os.environ[k] for k in _GIT_ENV_KEEP if k in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        env=env,
    )


@contextlib.contextmanager
def _kb_lock(repo: Path):
    """The same lock `kb.py` takes for every write — a pull must never merge underneath a
    projector heal or a board transition that is committing in the same checkout."""
    lock = repo / "kb" / ".lock"
    if not (repo / "kb").is_dir() or (repo / "kb").is_symlink() or lock.is_symlink():
        raise HTTPException(status_code=400, detail="not a knowledge-base checkout (kb/ missing or a symlink)")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@router.post("/sync")
def sync_repository(
    body: SyncRequest, session: Any = Depends(require_csrf)
) -> Dict[str, Any]:
    """Pull, push or both against the plugin's Corvin-Knowledge checkout."""
    sync_type = body.sync_type
    if sync_type not in ("pull", "push", "both"):
        raise HTTPException(status_code=400, detail=f"unknown sync_type {sync_type!r}")
    repo_path = Path(effective_config(getattr(session, "tenant_id", "_default"))["repo_path"]).expanduser()
    if not repo_path.exists():
        raise HTTPException(status_code=400, detail=f"Repository not found at {repo_path}")
    # `git fetch` / `merge` / `push` against the operator's REAL sibling checkout is
    # exactly what kb_projection.kb_repo() refuses to a sandboxed CORVIN_HOME
    # (ADV-10); this route reached it by its own path (R4-I-2). An explicitly
    # configured other repository is the operator's choice and stays allowed.
    sibling = (kb_projection._REPO_ROOT.parent / "Corvin-Knowledge")
    if kb_projection.kb_repo() is None and repo_path.resolve() == sibling.resolve():
        raise HTTPException(
            status_code=409,
            detail="this install runs on a sandboxed CORVIN_HOME and may not sync the operator's "
                   "Corvin-Knowledge checkout; set CORVIN_KB_REPO to a repository of its own",
        )

    result: Dict[str, Any] = {
        "status": "success",
        "message": f"Sync operation '{sync_type}' completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conflicts": [],
        "errors": [],
    }

    # A plain `def` route: git can take up to _GIT_TIMEOUT_S and must not stall the event loop.
    if sync_type in ("pull", "both"):
        try:
            with _kb_lock(repo_path):
                _git(["fetch", "origin"], repo_path)
                # fast-forward only: a merge commit (or a conflicted tree) in the KB checkout
                # would be written by no KB writer and is not this button's decision
                merge = _git(["merge", "--ff-only", "origin/main"], repo_path)
            if merge.returncode != 0:
                result["status"] = "conflict"
                result["conflicts"].append(f"Not a fast-forward — reconcile in the checkout: {merge.stderr.strip()[:300]}")
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
