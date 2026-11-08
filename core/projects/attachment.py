"""Which project a console chat is attached to, and what that adds to the chat's CLI spawn (ADR-2317).

The attachment is console state per chat, kept in ``<tenant_home>/projects/chat_attachments.json`` - never in
the project, never in the chat's working directory (so the worker cannot rewrite it). It only ever NAMES a
project; the path is resolved through the tenant's registry on every spawn. The project directory is never
the CLI's cwd. Verified with real CLI runs (NOTE-0018): rules reach the context through ``--add-dir`` plus
``CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1``; the project's MCP block rides on an additional
``--mcp-config``; write access is withheld with ``//<abs>/**`` deny rules, which hold even under
``--dangerously-skip-permissions`` (a Bash write is outside what a tool rule can see).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from . import registry
from .fsutil import atomic_write, file_lock

SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_ENTRIES = 500
_MAX_FILE = 64 * 1024
RULES_ENV = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"


def _path(tenant_id: str) -> Path:
    return registry.registry_path(tenant_id).with_name("chat_attachments.json")


def _load(tenant_id: str) -> dict[str, str]:
    try:
        data = json.loads(_path(tenant_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    chats = data.get("chats") if isinstance(data, dict) else None
    return {k: v for k, v in (chats or {}).items() if isinstance(k, str) and isinstance(v, str)}


def _save(tenant_id: str, chats: dict[str, str]) -> None:
    p = _path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, (json.dumps({"schema": 1, "chats": chats}, indent=1, sort_keys=True) + "\n").encode("utf-8"), mode=0o600)


def attached(tenant_id: str, sid: str) -> Optional[str]:
    return _load(tenant_id).get(sid) if SID_RE.match(sid or "") else None


def attach(tenant_id: str, sid: str, project_id: str) -> None:
    if not SID_RE.match(sid or ""):
        raise ValueError("invalid chat id")
    with file_lock(_path(tenant_id).with_name("chat_attachments.lock")):
        chats = _load(tenant_id)
        if sid not in chats and len(chats) >= MAX_ENTRIES:
            raise ValueError("too many attached chats")
        chats[sid] = project_id
        _save(tenant_id, chats)


def detach(tenant_id: str, sid: str) -> bool:
    if not SID_RE.match(sid or ""):
        return False
    with file_lock(_path(tenant_id).with_name("chat_attachments.lock")):
        chats = _load(tenant_id)
        if sid not in chats:
            return False
        del chats[sid]
        _save(tenant_id, chats)
    return True


def _regular(p: Path) -> bool:
    return p.is_file() and not p.is_symlink() and p.stat().st_size <= _MAX_FILE


def spawn_parts(tenant_id: str, sid: str, *, l34_allows=None) -> Optional[dict[str, Any]]:
    """What the chat's next CLI spawn adds, or None (not attached, project gone or invalid, or a gate said no).

    ``l34_allows(text) -> bool`` judges the project's rules text against the engine that will read it
    (ADR-2317 Decision 6); an error or ``False`` withholds the whole attachment (fail closed)."""
    pid = attached(tenant_id, sid)
    if not pid:
        return None
    entry = registry.get(tenant_id, pid)
    if not entry:
        return None
    root = Path(str(entry.get("path")))
    try:
        if not root.is_dir() or root.is_symlink():
            return None
        from . import manifest  # noqa: PLC0415
        man = manifest.load(root)
        if man.get("tenant") != tenant_id or man.get("id") != pid:
            return None
        rules = root / "CLAUDE.md"
        if _regular(rules) and l34_allows is not None:
            try:
                if not l34_allows(rules.read_text(encoding="utf-8", errors="replace")):
                    return None
            except Exception:  # noqa: BLE001 - fail closed
                return None
    except (OSError, manifest.ManifestError):
        return None
    abs_root = os.path.abspath(root)
    deny = [f"{tool}(/{Path(abs_root).as_posix()}/**)" for tool in ("Write", "Edit", "NotebookEdit")]
    mcp = root / ".mcp.json"
    return {
        "project_id": pid,
        "add_dir": abs_root,
        "env": {RULES_ENV: "1"},
        "mcp_config": str(mcp) if _regular(mcp) else None,
        "deny": deny,
    }
