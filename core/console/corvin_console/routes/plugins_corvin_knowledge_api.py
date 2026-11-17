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

import asyncio
import contextlib
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from ..deps import require_csrf, require_session
from .. import kb_projection
from corvin_core.process_signals import run_tree_safe as _run_tree_safe  # Windows: no hang on timeout

router = APIRouter(
    prefix="/plugins/corvin-knowledge",
    tags=["console-corvin-knowledge"],
)

# ────────────────────────────────────────────────────────
# CONFIG MANAGEMENT
# ────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".claude" / "plugins" / "corvin-knowledge.json"
DEFAULT_CONFIG: Dict[str, Any] = {
    # Empty = the knowledge base bound to the task board (kb_projection.kb_repo(): CORVIN_KB_REPO,
    # the operator's sibling Corvin-Knowledge, or the tenant's own <tenant_home>/knowledge that
    # the plugin creates on enable) — resolved per request in effective_config().
    "repo_path": "",
    # a tenant knowledge base has no remote until the operator configures one
    "remote_url": "",
    "auto_sync_on_query": True,
    "consistency_level": "warn",
}
_GIT_TIMEOUT_S = 30


def load_config() -> Dict[str, Any]:
    """Load plugin configuration from disk; defaults when absent or unreadable."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: Dict[str, Any]) -> None:
    """Persist plugin configuration."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


# ────────────────────────────────────────────────────────
# KNOWLEDGE GRAPH DATA
# ────────────────────────────────────────────────────────

#: a graph file above this size is refused, never read (REVIEW-0066 M2): the bound Corvin-Knowledge graph is ~1.6 MB for ~2000
#: entities, so 32 MiB leaves room for a large KB and still keeps one request from parsing a 95 MB file a project folder carries
_GRAPH_MAX_BYTES = 32 * 1024 * 1024
#: parsed graph files by (path, mtime_ns, size): a document read does not re-parse entities.jsonl on every click
_JSONL_CACHE: "dict[str, tuple[tuple[int, int], List[Dict[str, Any]]]]" = {}
_JSONL_CACHE_MAX = 8
_JSONL_LOCK = __import__("threading").Lock()


class GraphTooLarge(ValueError):
    """A ``kb/graph`` file is larger than :data:`_GRAPH_MAX_BYTES`."""


def _parse_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """The rows of one graph file; [] when it is absent or unreadable. Raises :class:`GraphTooLarge` above the size limit.
    Cached by mtime and size - callers must not mutate the rows they get."""
    try:
        st = path.stat()
    except OSError:
        return []
    if st.st_size > _GRAPH_MAX_BYTES:
        raise GraphTooLarge(f"{path.name} is too large to read ({st.st_size} bytes, the limit is {_GRAPH_MAX_BYTES})")
    key, stamp = str(path), (st.st_mtime_ns, st.st_size)
    with _JSONL_LOCK:
        hit = _JSONL_CACHE.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    try:
        rows = _parse_jsonl(path)
    except OSError:
        return []
    with _JSONL_LOCK:
        _JSONL_CACHE.pop(key, None)
        _JSONL_CACHE[key] = (stamp, rows)
        while len(_JSONL_CACHE) > _JSONL_CACHE_MAX:
            _JSONL_CACHE.pop(next(iter(_JSONL_CACHE)))
    return rows


def load_graph_data(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load entities + relations from the KB's generated graph (``kb.py index``, ADR-2206)
    and translate it to this panel's wire shape. A node's id is ``uid or id`` — the SAME
    fallback ``kb.py index`` uses for an edge's ``src``/``dst``, so every edge always
    resolves to a node that exists."""
    config = config or load_config()
    graph_dir = Path(config["repo_path"]).expanduser() / "kb" / "graph"
    try:
        _entities_raw = _read_jsonl(graph_dir / "entities.jsonl")
        _relations_raw = _read_jsonl(graph_dir / "relations.jsonl")
    except GraphTooLarge as exc:
        return {"entities": [], "relations": [], "duplicate_rows": 0, "graph_error": str(exc)}
    entities = [
        {
            "id": row.get("uid") or row.get("id", ""),
            "label": row.get("id") or row.get("uid", ""),
            "file": Path(str(row.get("path") or "")).name,
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
        for row in _relations_raw
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


# The pre-1.1 manifest default: a mesh-era convention no knowledge base ever lived in. It is
# stored in every registry record created from that manifest, so it means "unset" too.
_UNSET_REPO_PATHS = {"", "~/.corvin-knowledge/", "~/.corvin-knowledge"}


def bound_repo_path(tenant_id: str) -> str:
    """The knowledge base the board projects for this tenant (or, before the first tick, the
    place the tenant's own KB is created)."""
    repo = kb_projection.kb_repo(tenant_id)   # never another tenant's KB (tenant isolation)
    return str(repo or kb_projection.tenant_kb_path(tenant_id))


def _is_operator_tenant(tenant_id: str) -> bool:
    """The tenant the operator's own KB binds to (``CORVIN_KB_TENANT``). Only this tenant reads
    or writes ``CONFIG_FILE``: the file lives in the OS user's home and is shared by every
    tenant of the install, so for any other tenant it is someone else's configuration (S-1b)."""
    return tenant_id == kb_projection.kb_tenant()


def _repo_allowed(tenant_id: str, value: Any) -> bool:
    """ADR-2316 Decision 7 (T-0345). A tenant's ``repo_path`` is its bound KB; only the
    operator's tenant may name another one (the operator's choice, like ``CORVIN_KB_REPO``),
    and then only an existing knowledge-base checkout (``kb/_meta/sources.yaml``) that does
    not resolve into another tenant's home. Resolved first, so ``..`` and symlinks are judged
    by where they land. Until the project registry exists (ADR-2315) nothing else is allowed."""
    raw = str(value or "").strip()
    if raw in _UNSET_REPO_PATHS:
        return True
    try:
        target = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    try:
        bound = Path(bound_repo_path(tenant_id)).expanduser().resolve()
    except (OSError, RuntimeError):
        bound = None
    if bound is not None and target == bound:
        return True
    if not _is_operator_tenant(tenant_id):
        return False
    if not target.is_dir() or not (target / "kb" / "_meta" / "sources.yaml").is_file():
        return False
    try:
        own = kb_projection.tenant_kb_path(tenant_id).parent.resolve()   # <home>/tenants/<tid>
        tenants_root = own.parent
    except (OSError, RuntimeError):
        return False
    if target.is_relative_to(tenants_root) and not target.is_relative_to(own):
        return False
    return True


def effective_config(tenant_id: str) -> Dict[str, Any]:
    config = load_config() if _is_operator_tenant(tenant_id) else DEFAULT_CONFIG.copy()
    stored = _registry_settings(tenant_id)
    if stored:
        config.update({k: v for k, v in stored.items() if k in _KEYS and v is not None})
    rp = str(config.get("repo_path") or "").strip()
    if rp in _UNSET_REPO_PATHS or not _repo_allowed(tenant_id, rp):
        config["repo_path"] = bound_repo_path(tenant_id)
    return config


@router.get("/config")
async def get_config(session: Any = Depends(require_session)) -> Dict[str, Any]:
    return effective_config(getattr(session, "tenant_id", "_default"))


@router.post("/config")
async def update_config(
    body: ConfigUpdate, session: Any = Depends(require_csrf)
) -> Dict[str, Any]:
    tenant_id = getattr(session, "tenant_id", "_default")
    changes = body.model_dump(exclude_none=True)
    if "repo_path" in changes and not _repo_allowed(tenant_id, changes["repo_path"]):
        # nothing is written: an arbitrary client path is never stored (ADR-2316, T-0345)
        raise HTTPException(status_code=422, detail=(
            "repo_path refused: a tenant reads its own knowledge base; only the operator's tenant may "
            "name another existing knowledge-base checkout outside every other tenant's home"))
    config = effective_config(tenant_id)
    config.update(changes)
    if _is_operator_tenant(tenant_id):
        try:
            save_config(config)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif _registry_settings(tenant_id) is None:
        raise HTTPException(status_code=409, detail=(
            "the Knowledge Graph plugin has no settings record for this tenant; install it first"))
    _write_registry_settings(tenant_id, config)
    return {"status": "saved", "config": config}


def _scoped_config(tenant_id: str, project: Optional[str]) -> Dict[str, Any]:
    """ADR-2316 Decision 7: ``?project=<id>`` reads that project's KB. The path comes from THIS
    tenant's project registry (``kb_projection.project_kbs``), never from the request; an id
    that is not registered for this tenant is 404."""
    config = effective_config(tenant_id)
    if project is None:
        return config
    if project == "corvin":
        # The built-in Corvin project is the tenant's OWN knowledge base - the one the Tasks board projects and the project's Docs
        # tab counts (``builtin.repo`` = ``kb_projection.kb_repo``, CONCEPT-0129). The graph's Repository setting may name another
        # knowledge base for the default view; it never relabels Corvin's (REVIEW-0067 M4). Before the KB exists: the default view.
        from core.projects import builtin  # noqa: PLC0415

        repo = builtin.repo(tenant_id)
        return {**config, "repo_path": str(repo)} if repo is not None else config
    for pid, docs in kb_projection.project_kbs(tenant_id, only=project):   # resolves (and walks the .git of) this project only
        if pid == project:
            return {**config, "repo_path": str(docs)}
    raise HTTPException(status_code=404, detail="no such project")


def graph_payload(tenant_id: str, project: Optional[str]) -> Dict[str, Any]:
    """The graph of one knowledge base, with the path it was read from and its state:
    ``ok`` · ``built`` (a project graph that was missing or stale, indexed just now) · ``empty``
    (the knowledge base holds no entity) · ``missing`` (the bound KB was never indexed) ·
    ``error`` (+ ``graph_error``; a project graph that could not be built). Blocking: file reads
    and, for a project, possibly a ``kb index`` subprocess — call it off the event loop."""
    config = _scoped_config(tenant_id, project)
    repo = Path(config["repo_path"]).expanduser()
    state, error = "ok", None
    if project is not None and project != "corvin":
        with kb_projection.graph_lock(repo):
            res = kb_projection.ensure_project_graph(tenant_id, repo)
            state, error = res["state"], res.get("error")
            data = load_graph_data(config)
    else:
        data = load_graph_data(config)
        if not (repo / "kb" / "graph" / "entities.jsonl").is_file():
            state = "missing"
    if data.get("graph_error"):
        state, error = "error", data.pop("graph_error")
    if state in ("ok", "built") and not data["entities"]:
        state = "empty"
    return {**data, "project": project, "repo_path": str(repo), "graph_state": state, "graph_error": error}


@router.get("/graph")
async def get_graph(response: Response, session: Any = Depends(require_session),
                    project: Optional[str] = Query(default=None, max_length=28),
                    scope: Optional[str] = Query(default=None, max_length=16),
                    corvin_personal: Optional[str] = Cookie(default=None)) -> Dict[str, Any]:
    tenant_id = getattr(session, "tenant_id", "_default")
    if scope is not None:
        # ADR-2325 item 6: the personal graph is an explicit scope, read with the unlock token only.
        if scope != "personal" or project is not None:
            raise HTTPException(status_code=422, detail="scope must be 'personal' and cannot be combined with project")
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(_personal_graph, tenant_id, getattr(session, "sid", ""), corvin_personal)
    # off the event loop: a project graph may need a `kb index` run first (console loop health)
    return await asyncio.to_thread(graph_payload, tenant_id, project)


def _personal_graph(tenant_id: str, sid: str, token: Optional[str]) -> Dict[str, Any]:
    from core.personal_kb import session as pkb_session, views as pkb_views

    from . import personal_kb as pkb_routes

    entry = pkb_session.get_console(token, tenant_id=tenant_id, sid=sid)
    if entry is None:
        return {"entities": [], "relations": [], "scope": "personal", "unlocked": False, "graph_state": "locked",
                "graph_error": None, "project": None, "repo_path": ""}
    try:
        store = pkb_routes._store_for(entry, token or "")
    except Exception:  # noqa: BLE001 - erased or unreadable: shown as locked, never a 500
        return {"entities": [], "relations": [], "scope": "personal", "unlocked": False, "graph_state": "locked",
                "graph_error": None, "project": None, "repo_path": ""}
    return {**pkb_views.graph_payload(store), "unlocked": True}


_DOC_MAX_BYTES = 512 * 1024


def _split_frontmatter(text: str) -> tuple[Dict[str, Any], str, Optional[str]]:
    """``(meta, body, error)``. The frontmatter is text a project folder controls: it is parsed WITHOUT aliases
    (``core.projects.safe_yaml``, REVIEW-0066 M1) - an alias bomb of a few hundred bytes otherwise expands to hundreds of MB in
    the JSON response. Unreadable or refused frontmatter is shown as text (the whole document), with the reason."""
    if not text.startswith("---"):
        return {}, text, None
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return {}, text, None
    head = parts[0][3:]
    body = parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""
    try:
        from core.projects import safe_yaml  # noqa: PLC0415

        meta = safe_yaml.load(head) or {}
    except safe_yaml.AliasRefused:
        return {}, text, "the frontmatter uses YAML aliases, which are not shown; it is displayed as text"
    except Exception:  # noqa: BLE001 — unreadable frontmatter is shown as text, not an error
        return {}, text, "the frontmatter is not valid YAML; it is displayed as text"
    return (meta if isinstance(meta, dict) else {}), body, None


def load_document(config: Dict[str, Any], key: str) -> Dict[str, Any]:
    """One node's Markdown plus its resolved neighbours. ``path`` in entities.jsonl is data,
    not a trusted path: it is read only if it resolves INSIDE the configured repo (a symlink
    out, ``..`` or an absolute path elsewhere all fail that test)."""
    repo = Path(config["repo_path"]).expanduser()
    graph_dir = repo / "kb" / "graph"
    try:
        rows = [r for r in _read_jsonl(graph_dir / "entities.jsonl") if (r.get("uid") or r.get("id"))]
        rel_rows = _read_jsonl(graph_dir / "relations.jsonl")
    except GraphTooLarge as exc:
        raise HTTPException(status_code=413, detail=f"the knowledge graph is too large to read: {exc}") from None
    by_node: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        by_node[r.get("uid") or r.get("id")] = r
    row = by_node.get(key)
    if row is None:
        same_id = [r for r in by_node.values() if r.get("id") == key]
        if len(same_id) > 1:
            raise HTTPException(status_code=409, detail="id is carried by several documents; use the uid")
        row = same_id[0] if same_id else None
    if row is None or not row.get("path"):
        raise HTTPException(status_code=404, detail="document not found")
    raw = Path(str(row["path"]))
    target = (raw if raw.is_absolute() else repo / raw)
    try:
        resolved, root = target.resolve(strict=True), repo.resolve(strict=True)
    except OSError:
        raise HTTPException(status_code=404, detail="document not found") from None
    if not resolved.is_relative_to(root) or resolved.suffix != ".md" or not resolved.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    if resolved.stat().st_size > _DOC_MAX_BYTES:
        raise HTTPException(status_code=413, detail="document too large to display")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise HTTPException(status_code=404, detail="document not found") from None
    meta, body, fm_error = _split_frontmatter(text)

    node = row.get("uid") or row.get("id")

    def _ref(other: str, rel: str) -> Optional[Dict[str, Any]]:
        o = by_node.get(other)
        if o is None:
            return None
        return {"id": other, "label": o.get("id") or other, "title": o.get("title", ""),
                "type": o.get("type", ""), "relation": rel}

    outgoing: List[Dict[str, Any]] = []
    incoming: List[Dict[str, Any]] = []
    for rel in rel_rows:
        if rel.get("external") or not rel.get("resolved"):
            continue
        if rel.get("src") == node and (x := _ref(rel.get("dst", ""), rel.get("rel", "relates_to"))):
            outgoing.append(x)
        elif rel.get("dst") == node and (x := _ref(rel.get("src", ""), rel.get("rel", "relates_to"))):
            incoming.append(x)
    return {
        "entity": {"id": node, "label": row.get("id") or node, "type": row.get("type", ""),
                   "title": row.get("title", ""), "status": row.get("status", ""),
                   "realization": row.get("realization", "")},
        "frontmatter": json.loads(json.dumps(meta, default=str)),     # bounded: no aliases (above), at most _DOC_MAX_BYTES of text
        **({"frontmatter_error": fm_error} if fm_error else {}),
        "markdown": body,
        "outgoing": outgoing,
        "incoming": incoming,
    }


@router.get("/doc/{key}")
async def get_document(key: str, session: Any = Depends(require_session),
                       project: Optional[str] = Query(default=None, max_length=28)) -> Dict[str, Any]:
    tenant_id = getattr(session, "tenant_id", "_default")
    # off the event loop (REVIEW-0066 M2): the project lookup walks that project's .git, the read parses entities.jsonl
    return await asyncio.to_thread(lambda: load_document(_scoped_config(tenant_id, project), key))


_GIT_ENV_KEEP = ("PATH", "HOME", "LANG", "SSH_AUTH_SOCK", "GIT_SSH_COMMAND")


def _git(args: List[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # A pulled repository may carry config the console would otherwise execute: hooks
    # and an fsmonitor command are switched off, and the console's own environment
    # (API keys, tokens) is not handed to git.
    env = {k: os.environ[k] for k in _GIT_ENV_KEEP if k in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return _run_tree_safe(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        env=env, encoding="utf-8", errors="replace",
    )


# ────────────────────────────────────────────────────────
# WHAT'S NEW — read-only feed of recently added / changed entries
# ────────────────────────────────────────────────────────
# The graph rows carry no timestamps, but the knowledge base is a git repository: the first commit
# that adds a file is when the entry appeared, every later commit is a change. This feed reads that
# history (``git log --name-status`` over the Markdown files) and joins each touched file to its graph
# entity. It runs git exactly as the project change view does (``core.projects.changes``): the
# repository's own configuration cannot start a program (no gpg for a signed commit, no filter or
# hook), replace refs and grafts are ignored, and the repository is the one that OWNS the folder (a
# plain-mode project's ``docs/`` is part of the project root's repository). It writes nothing, takes no
# path from the request, and is tenant-scoped like ``/graph``. The date of an entry is the COMMIT time
# (the same clock ``--since`` filters on), normalised to UTC, so the order does not depend on the
# author's time zone.

_RECENT_SUBJECT_MAX = 140
_RECENT_CTRL = {c: None for c in range(0, 32)}
_RECENT_OUTPUT_CAP = 4_000_000          # bytes of ``git log`` read; older changes beyond it are left out (``truncated``)


def _clean_subject(text: str) -> str:
    return str(text or "").translate(_RECENT_CTRL).strip()[:_RECENT_SUBJECT_MAX]


def _utc(iso: str) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(iso.strip())
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


_GIT_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _unquote_git_path(s: str) -> str:
    """git prints a name with a quote, a backslash, a tab, a newline or a control character C-quoted ("a\"b.md"), whatever
    ``core.quotepath`` says; undo that so the name matches the graph's path."""
    if len(s) < 2 or not (s.startswith('"') and s.endswith('"')):
        return s
    body, out, i = s[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 >= len(body):
            out += ch.encode("utf-8")
            i += 1
        elif body[i + 1] in _GIT_ESCAPES:
            out.append(_GIT_ESCAPES[body[i + 1]])
            i += 2
        elif body[i + 1] in "01234567" and i + 3 < len(body) + 0 and body[i + 1:i + 4].isdigit():
            out.append(int(body[i + 1:i + 4], 8) & 0xFF)
            i += 4
        else:
            out += body[i:i + 2].encode("utf-8")
            i += 2
    return out.decode("utf-8", errors="replace")


def _parse_git_recent(out: str) -> List[Dict[str, Any]]:
    """Commits as git printed them (newest first), each with its ``(status, old_path, path)`` rows. A rename carries the
    old path too, so an entry added and then renamed inside the window keeps its history."""
    commits: List[Dict[str, Any]] = []
    for block in out.split("\x00"):              # NUL: git refuses it in a commit message, so no subject can fake a boundary
        block = block.strip("\n")
        if not block:
            continue
        head, _, rest = block.partition("\n")
        parts = head.split("\x1f", 2)            # hash, commit time, subject (which may itself contain \x1f)
        if len(parts) < 3:
            continue
        when = _utc(parts[1])
        if when is None:
            continue
        files: List[tuple[str, str, str]] = []
        for line in rest.split("\n"):             # not splitlines(): U+0085/U+2028 are raw in a name (quotepath=false) and are not breaks
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0]:
                status = cols[0][0]
                files.append((status, _unquote_git_path(cols[1]) if status in "RC" and len(cols) >= 3 else "", _unquote_git_path(cols[-1])))
        commits.append({"when": when, "subject": _clean_subject(parts[2]), "files": files})
    return commits


def _entities_by_relpath(repo: Path, all_paths: Optional[set[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Graph rows keyed by their path relative to the knowledge base (last row wins, like ``load_graph_data``). *all_paths*, when
    given, also receives the path of every row, including one without an id (the graph knows that file, so it is not "unindexed")."""
    root = repo.resolve()
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_jsonl(repo / "kb" / "graph" / "entities.jsonl"):
        raw = str(row.get("path") or "")
        if not raw:
            continue
        pp = Path(raw)
        try:
            rel = pp.relative_to(root) if pp.is_absolute() else pp
        except ValueError:
            try:
                rel = pp.resolve().relative_to(root)
            except (ValueError, OSError):
                continue
        if all_paths is not None:
            all_paths.add(os.path.normpath(rel.as_posix()))     # "sub/../x.md" is "x.md" to git and to the feed
        if row.get("uid") or row.get("id"):
            out[os.path.normpath(rel.as_posix())] = row
    return out


# The engine's id prefixes (kb_model.TYPES): a file gets an id from its frontmatter, or from its name "<PREFIX>-<n>-..." (a decision's
# older "0466-..." / "adr_0801_..." name too).
_ID_PREFIX = {"decision": "ADR", "concept": "CONCEPT", "idea": "IDEA", "plan": "PLAN", "review": "REVIEW", "note": "NOTE",
              "initiative": "I", "epic": "E", "task": "T"}
_NOT_ENTRIES = ("readme.md", "index.md", "_template.md")


def _source_dirs(repo: Path) -> Dict[str, str]:
    """The folders (relative to the knowledge base, with a trailing slash) its own ``sources.yaml`` keeps entities in, with their type."""
    try:
        from core.projects import safe_yaml  # noqa: PLC0415 - no aliases: a hostile file cannot balloon in memory
        meta = safe_yaml.load((repo / "kb" / "_meta" / "sources.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable or unusual file: no folder is known, nothing is counted as missing
        return {}
    out: Dict[str, str] = {}
    for s in (meta.get("sources") or []) if isinstance(meta, dict) else []:
        if not (isinstance(s, dict) and isinstance(s.get("dirs"), dict)):
            continue
        if s.get("ref"):
            continue                              # a pinned worktree (.kb-cache/...): its paths are not the working tree's; no count
        for d, ty in s["dirs"].items():
            # the engine reads ``root / dir``: "./", "sub" and "./decisions" are all spellings of a folder inside the repository
            rel = os.path.normpath(os.path.join(str(s.get("root", ".")), str(d)))
            if os.path.isabs(rel) or rel == ".." or rel.startswith(".." + os.sep) or rel == ".":
                continue                          # outside the repository (or the repository itself): not read here
            # the engine keeps only what lies inside the source's own root: a dirs key that climbs out of the root is not listed
            root_rel = os.path.normpath(str(s.get("root", ".")))
            if root_rel.startswith("..") or os.path.isabs(root_rel) or not (
                    root_rel == "." or rel == root_rel or rel.startswith(root_rel.rstrip(os.sep) + os.sep)):
                continue
            out[Path(rel).as_posix().strip("/") + "/"] = str(ty)
    return out


def _name_gives_id(name: str, type_: str) -> bool:
    """The engine's ``id_from_name``: "<PREFIX>-<n>..." — and for a decision the older "0466-..." / "adr_0801_..." names."""
    pre = _ID_PREFIX.get(type_)
    if pre and re.match(rf"^{re.escape(pre)}-\d+", name):
        return True
    return type_ == "decision" and bool(re.match(r"^(?:adr_)?\d{3,4}[-_]", name))


def _frontmatter_gives_id(path: Path) -> bool:
    """Does the file carry a frontmatter ``id``/``uid``, read the way the engine reads it (``kb_lib.parse``): BOM removed, strict UTF-8,
    no mixed CRLF/LF, the block runs from a first ``---`` line to the next line that is ``---``. A file the engine cannot read
    gets no frontmatter there. Frontmatter with YAML aliases (refused here, accepted by the engine) counts as having one: the safe side
    for a notice that clears itself once the file is a graph row."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_FRONTMATTER_MAX_BYTES + 1)
    except OSError:
        return False
    cut = len(raw) > _FRONTMATTER_MAX_BYTES     # the file goes on past what is read here
    raw = raw[:_FRONTMATTER_MAX_BYTES]
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        if not cut or exc.start < len(raw) - 3:
            return False                        # genuinely not UTF-8
        text = raw[:exc.start].decode("utf-8", errors="replace")     # only the cut landed inside a character
    if "\r\n" in text and "\n" in text.replace("\r\n", ""):
        return False                            # mixed line endings: the engine reads no frontmatter
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    end = next((n for n in range(1, len(lines)) if lines[n].strip() == "---"), None)
    if end is None:
        return cut                              # a frontmatter longer than what is read here: unknown, counted (the safe side)
    from core.projects import safe_yaml  # noqa: PLC0415 - no aliases: a hostile file cannot balloon in memory
    try:
        meta = safe_yaml.load("\n".join(lines[1:end])) or {}
    except safe_yaml.AliasRefused:
        return True
    except Exception:  # noqa: BLE001 - invalid YAML: no frontmatter, as in the engine
        return False
    return isinstance(meta, dict) and bool(meta.get("id") or meta.get("uid"))


_FRONTMATTER_READ_BUDGET = 400          # files whose name gives no id and that are read per request ...
_FRONTMATTER_READ_SECONDS = 2.0         # ... and the time they may take altogether
_FRONTMATTER_MAX_BYTES = 32 * 1024      # a frontmatter block is a few hundred bytes; pure-Python YAML on much more is slow


def _unindexed_entries(repo: Path, commits: List[Dict[str, Any]], known: set[str]) -> tuple[set[str], bool]:
    """Entry files the history touched that ``kb index`` WOULD list but the graph does not. The rules are the engine's
    (``kb_model.discover``): a ``.md`` DIRECTLY inside a source folder, not readme/index/_template, not a symlink and not resolving
    outside the repository, that gets an id. Anything else (a nested folder, an archive, a link, a file with no id) is never listed, so it
    must never count as missing."""
    dirs = _source_dirs(repo)
    if not dirs:
        return set(), False
    try:
        root = repo.resolve()
    except OSError:
        return set(), False
    out: set[str] = set()
    capped = False                              # the read budget or its time ran out: the count is a lower bound
    deadline = time.monotonic() + _FRONTMATTER_READ_SECONDS
    checked: set[str] = set()                   # a file touched by many commits is looked at once
    budget = _FRONTMATTER_READ_BUDGET
    for c in commits:
        for status, _old, p in c["files"]:
            if status == "D" or p in known or p in checked:
                continue
            folder = (p.rsplit("/", 1)[0] + "/") if "/" in p else None   # the file's own folder, as the engine lists it
            if folder is None or folder not in dirs:
                continue
            name = p[len(folder):]
            if "/" in name or not name.endswith(".md") or name.lower() in _NOT_ENTRIES:
                continue
            f = repo / p
            try:
                if not f.is_file() or f.is_symlink() or not f.resolve().is_relative_to(root):
                    continue
            except OSError:
                continue
            checked.add(p)
            if _name_gives_id(name, dirs[folder]):
                out.add(p)                      # the usual case: the name carries the id, no file is read
            elif budget > 0 and time.monotonic() < deadline:
                budget -= 1
                if _frontmatter_gives_id(f):
                    out.add(p)
            else:
                capped = True
    return out, capped


def _aggregate_recent(commits: List[Dict[str, Any]], by_path: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One item per entry. Walks newest to oldest; a rename row makes the OLD path an alias of the new one, so the older
    commits that touched it count for the same entry."""
    alias: Dict[str, str] = {}
    dead: set[str] = set()                    # literal paths whose OLDER rows belong to a different file lifetime
    seen: Dict[str, Dict[str, Any]] = {}
    for commit in commits:
        for status, old, rel in commit["files"]:
            if rel in dead:
                if status in "RC" and old:
                    dead.add(old)                   # the file that moved here is a dead lifetime: so is its history at the old path
                continue
            final = alias.get(rel, rel)
            if status in "RC" and old:
                alias[old] = final
                dead.discard(old)                   # older rows at the old path are this entry's history again
            row = by_path.get(final)
            if row is None:
                continue
            item = seen.get(final)
            if item is not None and not item.get("deleted") and status == "D":
                dead.add(rel)                       # a file was deleted at this path before the live entry took it over
                continue
            if item is None:
                if status == "D":
                    seen[final] = {"deleted": True}        # the newest touch removed it: not shown
                    continue
                seen[final] = item = {
                    "id": row.get("uid") or row.get("id"), "label": row.get("id") or row.get("uid", ""),
                    "type": row.get("type", "decision"), "title": row.get("title", ""),
                    "status": row.get("status", "proposed"), "changed_at": commit["when"],
                    "summary": commit["subject"], "commits": 0, "kind": "updated",
                }
            if item.get("deleted"):
                continue
            item["commits"] += 1
            if status in "AC":
                item["kind"] = "new"                        # added (or copied into existence) inside the window
                item["first_seen_at"] = commit["when"]
    items = [i for i in seen.values() if not i.get("deleted")]
    items.sort(key=lambda i: i["changed_at"], reverse=True)   # real instants: offsets never reorder
    for i in items:
        i["changed_at"] = i["changed_at"].isoformat()
        if "first_seen_at" in i:
            i["first_seen_at"] = i["first_seen_at"].isoformat()
    return items


_RECENT_LOCK_WAIT_S = 15.0
_ENSURE_TTL_S = 30.0
_ensure_memo: Dict[str, tuple[float, Dict[str, Any]]] = {}
_ensure_memo_lock = threading.Lock()


def _ensure_project_graph_once(tenant_id: str, repo: Path) -> Dict[str, Any]:
    """``ensure_project_graph`` with ONE thing remembered: a FAILED index, for 30 seconds. Every tile, chip or period click is a new
    request, and a graph whose index fails or times out must not run its 60-second index again for each one. A success that leaves
    the graph current is never remembered (``ensure_project_graph`` starts with a bounded staleness check, so the list reads a
    current graph, like /graph); a failure, or an index that left the graph stale anyway, is, and is dropped as soon as the graph is
    current again (fixed, or built by the Graph tab).
    The caller holds ``graph_lock(repo)``."""
    key, now = f"{tenant_id}\0{repo}", time.monotonic()
    with _ensure_memo_lock:
        hit = _ensure_memo.get(key)
        if hit is not None and now - hit[0] < _ENSURE_TTL_S and kb_projection.graph_stale(repo):
            return hit[1]
        _ensure_memo.pop(key, None)
    res = kb_projection.ensure_project_graph(tenant_id, repo)
    if res.get("state") == "error" or kb_projection.graph_stale(repo):
        # a failure, or an index that left the graph "stale" anyway (a source file dated in the future, a missing sources.yaml):
        # either way the next 30 seconds do not run it again
        with _ensure_memo_lock:
            _ensure_memo[key] = (time.monotonic(), res)
    return res


_RECENT_PREFIX_OK = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")


def _true_case(owner: Path, prefix: str) -> str:
    """*prefix* spelled as the folders are on disk (a macOS ``Docs`` that the project calls ``docs``). An exact match wins, so a
    case-sensitive file system with both ``docs`` and ``Docs`` keeps them apart; otherwise a single case-insensitive match."""
    cur, out = owner, []
    for part in prefix.split("/"):
        try:
            names = [n for n in os.listdir(cur)]
        except OSError:
            return prefix
        pick = part if part in names else next(iter([n for n in names if n.lower() == part.lower()]), part)
        out.append(pick)
        cur = cur / pick
    return "/".join(out)


def recent_payload(tenant_id: str, project: Optional[str], days: int, limit: int, kind: Optional[str] = None,
                   etype: Optional[str] = None) -> Dict[str, Any]:
    """Entries added or changed in the last ``days`` days, newest first. ``kind``/``etype`` narrow ``items`` and ``matching``.
    The tiles count within the active type (``new_count``/``updated_count``/``total``) and the type chips within the active kind
    (``type_counts``, every type of the window present, zero included), so what a click lists is what its number says.
    State: ``ok`` · ``empty`` (nothing changed in the window) · ``missing`` (the bound knowledge base was never indexed) ·
    ``no_commits`` (the repository has no commit yet) · ``no_history`` (no repository this feed reads owns the folder) ·
    ``busy`` (the graph is being rebuilt right now: look again shortly) · ``error`` (+ ``error``). Blocking (file reads + one git subprocess): call it off the event loop."""
    from core.projects import changes  # noqa: PLC0415 - the project change view's hardened git setup

    config = _scoped_config(tenant_id, project)
    repo = Path(config["repo_path"]).expanduser()
    base = {"project": project, "window_days": days, "items": [], "total": 0, "new_count": 0, "updated_count": 0,
            "matching": 0, "type_counts": {}, "truncated": False, "graph_stale": False, "unindexed": 0, "unindexed_capped": False, "index_error": None,
            "generated_at": datetime.now(timezone.utc).isoformat()}
    index_error: Optional[str] = None
    known_paths: set[str] = set()
    try:
        if project is not None and project != "corvin":
            # a project's graph is built on demand and rewritten in place by ``kb index``: the same lock and the same
            # staleness check as /graph, so the list never reads a half-written or outdated graph
            lock = kb_projection.graph_lock(repo)
            if not lock.acquire(timeout=_RECENT_LOCK_WAIT_S):
                return {**base, "state": "busy"}
            try:
                res = _ensure_project_graph_once(tenant_id, repo)
                has_graph = (repo / "kb" / "graph" / "entities.jsonl").is_file()
                if res["state"] == "error":
                    if not has_graph:
                        return {**base, "state": "error", "error": str(res.get("error") or "the graph could not be built")}
                    index_error = str(res.get("error") or "the graph could not be re-indexed")   # an older graph still serves the list
                by_path = _entities_by_relpath(repo, known_paths) if has_graph else None
            finally:
                lock.release()
        else:
            by_path = _entities_by_relpath(repo, known_paths) if (repo / "kb" / "graph" / "entities.jsonl").is_file() else None
    except GraphTooLarge as exc:
        return {**base, "state": "error", "error": str(exc)}
    if by_path is None:
        return {**base, "state": "missing"}
    base["index_error"] = index_error
    owner = changes._owning_repo(repo)            # noqa: SLF001 - the repository that owns this folder, never an enclosing one
    if owner is None:
        return {**base, "state": "no_history"}
    try:
        prefix = repo.resolve().relative_to(owner.resolve()).as_posix()
    except ValueError:
        return {**base, "state": "no_history"}
    prefix = "" if prefix == "." else _true_case(owner, prefix)
    if prefix and not _RECENT_PREFIX_OK.match(prefix):
        return {**base, "state": "no_history"}     # a folder name git would read as a pattern: not read here
    try:
        if not changes._has_head(owner):          # noqa: SLF001 - a repository that was just initialised
            return {**base, "state": "no_commits"}
        # only Markdown reaches the output (and its 4 MB cap): images and generated files under docs/ never do
        spec = f":(glob){prefix}/**/*.md" if prefix else "*.md"
        out = changes._git_capped(                # noqa: SLF001
            owner, "log", f"--since={int(days)}.days.ago", "--no-merges", "-M", "--name-status",
            "--format=%x00%H%x1f%cI%x1f%s", "--", spec, cap=_RECENT_OUTPUT_CAP, timeout=_GIT_TIMEOUT_S)
    except (changes.ChangesError, subprocess.SubprocessError, OSError) as exc:
        return {**base, "state": "error", "error": f"git could not be read: {type(exc).__name__}"}
    truncated = len(out.encode("utf-8", "ignore")) >= _RECENT_OUTPUT_CAP
    commits = _parse_git_recent(out)
    if prefix:                                    # the knowledge base is a folder inside the owning repository: paths relative to it
        keep = len(prefix) + 1
        head = prefix + "/"
        for c in commits:
            c["files"] = [(s, o[keep:] if o.startswith(head) else "", p[keep:])
                          for s, o, p in c["files"] if p.startswith(head) and p.endswith(".md")]
    # The graph lags the history when the history names entry files the graph does not know (a pull, a commit nobody indexed yet): those
    # entries are absent below, so say so. Counted from the data, not from clocks (a pulled commit keeps the other host's commit
    # time; a commit made after an index that already saw the edit changes nothing). Only files in the knowledge base's own source
    # folders that look like entries and exist now count: a README or an edit of an entry the graph knows does not.
    missing, capped = _unindexed_entries(repo, commits, known_paths)
    base["graph_stale"], base["unindexed"], base["unindexed_capped"] = bool(missing) or capped, len(missing), capped
    items = _aggregate_recent(commits, by_path)
    by_type = [i for i in items if not etype or i["type"] == etype]       # what the kind tiles count within
    by_kind = [i for i in items if not kind or i["kind"] == kind]         # what the type chips count within
    type_counts: Dict[str, int] = {i["type"]: 0 for i in items}
    for i in by_kind:
        type_counts[i["type"]] += 1
    new_count = sum(1 for i in by_type if i["kind"] == "new")
    narrowed = [i for i in by_type if not kind or i["kind"] == kind]
    return {**base, "state": "ok" if items else "empty", "items": narrowed[:limit], "total": len(by_type),
            "new_count": new_count, "updated_count": len(by_type) - new_count, "matching": len(narrowed),
            "type_counts": type_counts, "truncated": truncated}


@router.get("/recent")
async def get_recent(session: Any = Depends(require_session),
                     project: Optional[str] = Query(default=None, max_length=28),
                     days: int = Query(default=14, ge=1, le=90),
                     limit: int = Query(default=80, ge=1, le=500),
                     kind: Optional[str] = Query(default=None, pattern="^(new|updated)$"),
                     etype: Optional[str] = Query(default=None, alias="type", max_length=24, pattern="^[a-z_]+$")) -> Dict[str, Any]:
    """Read-only. Same scoping as ``/graph``: the knowledge base is the tenant's bound one or a registered project's;
    the path never comes from the request."""
    tenant_id = getattr(session, "tenant_id", "_default")
    return await asyncio.to_thread(recent_payload, tenant_id, project, days, limit, kind, etype)


@contextlib.contextmanager
def _kb_lock(repo: Path):
    """The same lock `kb.py` takes for every write — a pull must never merge underneath a
    projector heal or a board transition that is committing in the same checkout."""
    lock = repo / "kb" / ".lock"
    if not (repo / "kb").is_dir() or (repo / "kb").is_symlink() or lock.is_symlink():
        raise HTTPException(status_code=400, detail="not a knowledge-base checkout (kb/ missing or a symlink)")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        _lock_fd(fd, True)
        yield
    finally:
        _lock_fd(fd, False)
        os.close(fd)


def _lock_fd(fd: int, lock: bool) -> None:
    """Blocking exclusive lock of kb/.lock, compatible with kb.py on every platform: flock on
    POSIX, the same one-byte msvcrt region lock kb.py takes on Windows (a no-op fcntl stub
    there would exclude nothing)."""
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK if lock else msvcrt.LK_UNLCK, 1)
        return
    import fcntl  # noqa: PLC0415

    fcntl.flock(fd, fcntl.LOCK_EX if lock else fcntl.LOCK_UN)


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
    # configured other repository is the operator's choice and stays allowed — for the
    # operator's tenant only, validated by _repo_allowed() (ADR-2316, T-0345).
    sibling = (kb_projection._REPO_ROOT.parent / "Corvin-Knowledge")
    if kb_projection.kb_repo(getattr(session, "tenant_id", "_default")) is None and \
            repo_path.resolve() == sibling.resolve():
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
    """Called on plugin installation: default config. Only the operator's tenant owns the
    shared file; for any other tenant this is a no-op that reports its effective config."""
    tenant_id = getattr(session, "tenant_id", "_default")
    if _is_operator_tenant(tenant_id):
        try:
            save_config(DEFAULT_CONFIG.copy())
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "initialized",
        "message": "Plugin initialized successfully",
        "config": effective_config(tenant_id),
    }


@router.post("/cleanup")
async def cleanup_plugin(session: Any = Depends(require_csrf)) -> Dict[str, Any]:
    """Called on plugin uninstallation: remove the config file (the operator's tenant only —
    the file is shared by every tenant of the install)."""
    try:
        if _is_operator_tenant(getattr(session, "tenant_id", "_default")) and CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "cleaned", "message": "Plugin cleaned up successfully"}
