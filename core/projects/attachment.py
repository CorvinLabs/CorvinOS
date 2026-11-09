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

from . import manifest, registry
from .fsutil import atomic_write, file_lock

SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_ENTRIES = 500
_MAX_FILE = 64 * 1024
RULES_ENV = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"


def _path(tenant_id: str) -> Path:
    return registry.registry_path(tenant_id).with_name("chat_attachments.json")


def _load(tenant_id: str) -> dict[str, Any]:
    """sid -> {"project_id", "path"} (the folder the attachment was made for). A legacy entry is a bare project id."""
    try:
        data = json.loads(_path(tenant_id=tenant_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    chats = data.get("chats") if isinstance(data, dict) else None
    out: dict[str, Any] = {}
    for k, v in (chats or {}).items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = {"project_id": v, "path": None}
        elif isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("project_id"), str):
            out[k] = {"project_id": v["project_id"], "path": v.get("path") if isinstance(v.get("path"), str) else None}
    return out


def _save(tenant_id: str, chats: dict[str, Any]) -> None:
    p = _path(tenant_id=tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, (json.dumps({"schema": 1, "chats": chats}, indent=1, sort_keys=True) + "\n").encode("utf-8"), mode=0o600)


def attached(tenant_id: str, sid: str) -> Optional[str]:
    e = _load(tenant_id=tenant_id).get(sid) if SID_RE.match(sid or "") else None
    return e["project_id"] if e else None


def _entry(tenant_id: str, sid: str) -> Optional[dict[str, Any]]:
    return _load(tenant_id=tenant_id).get(sid) if SID_RE.match(sid or "") else None


class AttachmentFull(ValueError):
    """The tenant has MAX_ENTRIES attached chats."""


def attach(tenant_id: str, sid: str, project_id: str, path: Optional[str] = None) -> None:
    if not SID_RE.match(sid or ""):
        raise ValueError("invalid chat id")
    with file_lock(_path(tenant_id=tenant_id).with_name("chat_attachments.lock")):
        chats = _load(tenant_id=tenant_id)
        if sid not in chats and len(chats) >= MAX_ENTRIES:
            raise AttachmentFull("too many attached chats")
        chats[sid] = {"project_id": project_id, "path": path}
        _save(tenant_id=tenant_id, chats=chats)


def detach_project(tenant_id: str, project_id: str) -> list[str]:
    """Detach every chat of this project (removed from the list). Returns the chat ids (release review R2)."""
    with file_lock(_path(tenant_id=tenant_id).with_name("chat_attachments.lock")):
        chats = _load(tenant_id=tenant_id)
        gone = [k for k, v in chats.items() if v["project_id"] == project_id]
        for k in gone:
            del chats[k]
        if gone:
            _save(tenant_id=tenant_id, chats=chats)
    return gone


def prune(tenant_id: str, keep) -> int:
    """Drop entries whose chat no longer exists (``keep(sid) -> bool``)."""
    with file_lock(_path(tenant_id=tenant_id).with_name("chat_attachments.lock")):
        chats = _load(tenant_id=tenant_id)
        dead = [k for k in chats if not keep(k)]
        for k in dead:
            del chats[k]
        if dead:
            _save(tenant_id=tenant_id, chats=chats)
    return len(dead)


def detach(tenant_id: str, sid: str) -> bool:
    if not SID_RE.match(sid or ""):
        return False
    with file_lock(_path(tenant_id=tenant_id).with_name("chat_attachments.lock")):
        chats = _load(tenant_id=tenant_id)
        if sid not in chats:
            return False
        del chats[sid]
        _save(tenant_id=tenant_id, chats=chats)
    return True


def _regular(p: Path) -> bool:
    return p.is_file() and not p.is_symlink() and p.stat().st_size <= _MAX_FILE


def spawn_parts(tenant_id: str, sid: str, *, l34_allows=None, class_allows=None) -> Optional[dict[str, Any]]:
    """What the chat's next CLI spawn adds, or None (not attached, project gone or invalid, or a gate said no).

    ``l34_allows(text) -> bool`` judges the project's rules text against the engine that will read it
    (ADR-2317 Decision 6); an error or ``False`` withholds the whole attachment (fail closed).
    ``class_allows(classification) -> bool`` judges the PROJECT's data class against the same engine (ADR-2319);
    the same fail-closed rule holds. ``assistant_access: write`` is the only thing that lifts the write-deny rules."""
    att = _entry(tenant_id=tenant_id, sid=sid)
    if not att:
        return None
    pid = att["project_id"]
    from . import builtin  # noqa: PLC0415
    entry = builtin.entry(tenant_id) if builtin.is_builtin(pid) else registry.get(tenant_id, pid)
    if not entry or not entry.get("path"):
        return None
    root = Path(str(entry.get("path")))
    if not att.get("path") or os.path.abspath(att["path"]) != os.path.abspath(root):
        return None                                   # a legacy entry, or another folder that once had this id: attach again (R2)
    try:
        if not root.is_dir() or root.is_symlink():
            return None
        man = builtin.manifest(tenant_id) if builtin.is_builtin(pid) else manifest.load(root)
        if man.get("tenant") != tenant_id or man.get("id") != pid:
            return None
        # the protection is TENANT state (the registry), not only project.yaml: the assistant can edit files in the project (review R5)
        prot = (None if builtin.is_builtin(pid) else registry.protection(tenant_id, pid)) or {}
        cls = prot.get("classification") or (manifest.DEFAULT_CLASS if builtin.is_builtin(pid) else manifest.legacy_class(man))
        access = prot.get("assistant_access") or manifest.DEFAULT_ACCESS
        if class_allows is not None:
            try:
                if not class_allows(cls):
                    return None
            except Exception:  # noqa: BLE001 - fail closed
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
    base = Path(abs_root).as_posix()
    # always: the project's own control files (its grants live there too) - even with write access (release review R5)
    control = [f"{tool}(/{base}/{rel})" for tool in ("Write", "Edit", "NotebookEdit")
               for rel in ("project.yaml", ".mcp.json", ".corvin/**", ".git/**", ".gitattributes")]
    deny = control + ([] if access == "write" else [f"{tool}(/{base}/**)" for tool in ("Write", "Edit", "NotebookEdit")])
    mcp = root / ".corvin" / "local" / "mcp.json"      # the ENGINE's copy only - an adopted repo's own .mcp.json never runs (review R6)
    want = registry.mcp_sha(tenant_id=tenant_id, project_id=pid)   # set only while an MCP feature is on and the engine wrote the file
    mcp_ok = False
    if want and _regular(mcp):
        import hashlib  # noqa: PLC0415
        mcp_ok = hashlib.sha256(mcp.read_bytes()).hexdigest() == want
    return {
        "project_id": pid,
        "title": str(man.get("title") or pid)[:120],
        "add_dir": abs_root,
        "env": {RULES_ENV: "1"},
        "mcp_config": str(mcp) if mcp_ok else None,
        "deny": deny,
        "classification": cls,
        "access": access,
    }
