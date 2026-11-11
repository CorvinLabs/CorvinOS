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
from .fsutil import atomic_write, file_lock, is_link, passes_link, read_text_any, dirent_is_junction

SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_ENTRIES = 500
_MAX_FILE = 256 * 1024
RULES_ENV = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"


def _path(tenant_id: str) -> Path:
    return registry.registry_path(tenant_id).with_name("chat_attachments.json")


def _load(tenant_id: str) -> dict[str, Any]:
    """sid -> {"project_id", "path"} (the folder the attachment was made for). A legacy entry is a bare project id."""
    try:
        data = json.loads(read_text_any(_path(tenant_id=tenant_id)))
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


#: the rules files Claude Code loads from an additional directory (CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1)
RULES_FILES = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
RULES_DIR = ".claude/rules"
#: also loaded from an additional directory: skills (their SKILL.md and files), subagents and commands - their text reaches the engine
#: as well, so it goes through the same data-flow check and the same link refusal (adversarial review round 4)
CONTEXT_DIRS = (".claude/rules", ".claude/skills", ".claude/agents", ".claude/commands")


def deny_base(abs_root: str) -> Optional[str]:
    """The folder as the CLI's permission rules spell an ABSOLUTE path: ``//<path>`` with ``<path>`` POSIX-style. On Windows that is
    the drive letter in lower case without its colon (``Edit(//c/Users/me/proj/**)``): ``/C:/Users/...`` is read as a path relative to
    the working folder and matches nothing - every deny rule was silently void (adversarial review round 4, checked against the CLI).
    A network (UNC) folder has no such form: None, and the caller withholds the attachment."""
    pp = Path(abs_root)
    if os.name == "nt":
        if len(pp.drive) != 2 or pp.drive[1] != ":":
            return None
        rest = "/".join(pp.parts[1:])
        base = f"/{pp.drive[0].lower()}/{rest}".rstrip("/")
    else:
        base = pp.as_posix()
    # the rule is a gitignore-style pattern: [ ] * ? in a folder name (legal: "Report [final]") would turn the path into a pattern that
    # no longer matches the folder, and the read-only rule would be void - so such a folder is never attached read-only
    if any(ch in base for ch in "[]*?"):
        return None
    return base


def project_rules_text(root: Path) -> Optional[str]:
    """All rules text the assistant's CLI will read from the project, for the data-flow (L34) check. None = a rules file exists that
    the check cannot read (too large, a link, unreadable): the caller withholds the attachment - an unchecked file never goes to the
    engine (fail closed, adversarial review round 3). "" = no rules file."""
    parts: list[str] = []
    used: list[Path] = []
    cands = [root / r for r in RULES_FILES]
    seen = 0
    for sub in CONTEXT_DIRS:
        rd = root / sub
        if is_link(rd) or rd.exists():
            if passes_link(root, rd) or not rd.is_dir():
                return None
            # a bounded walk that NEVER descends into a link: pathlib's rglob follows directory junctions, and two junctions pointing back
            # up make that tree infinite (adversarial review round 8)
            stack = [str(rd)]
            texts = 0
            while stack:
                d = stack.pop()
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            seen += 1
                            if seen > 20000:
                                return None           # an enormous tree: withheld
                            if e.is_symlink() or dirent_is_junction(e):
                                return None           # a link inside: withheld (the CLI might follow it)
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.name.lower().endswith((".md", ".markdown")):
                                # the CLI auto-loads the markdown of skills, agents, commands and rules; scripts, lock files and assets
                                # next to them are not context and must not make a project unattachable
                                texts += 1
                                if texts > 300:
                                    return None
                                cands.append(Path(e.path))
                except OSError:
                    return None
    cands.sort()
    for p in cands:
        if not (is_link(p) or p.exists()):
            continue
        if not _regular(p, root):
            return None
        try:
            body = _gate_text(p)
            if body is None:
                return None
            parts.append(body)
            used.append(p)
        except OSError:
            return None
    imported = _imports_text(root, [(c, t) for c, t in zip(used, parts)])
    if imported is None:
        return None
    return "\n\n".join(parts + imported)


_IMPORT = re.compile(r"(?:^|(?<=\s))@(\S+)")


def _imports_text(root: Path, seeds: "list[tuple[Path, str]]") -> Optional[list[str]]:
    """The CLI expands ``@path`` imports in the rules files it loads. Their text goes to the engine as well, so it is part of what
    the data-flow check reads; an import it must not or cannot follow withholds the attachment (None): an absolute, home, drive,
    network or ``..`` path (judged lexically - nothing is stat-ed first), a link, a file too large, or too many / too deep imports."""
    out: list[str] = []
    queue = [(p, t, 1) for p, t in seeds]
    seen = 0
    while queue:
        path, text, depth = queue.pop()
        for m in _IMPORT.finditer(text):
            tok = m.group(1).rstrip(".,;:)]}>\"'")
            if not tok or tok.startswith(("@", "#")):
                continue
            if tok.startswith(("~", "/", "\\")) or re.match(r"^[A-Za-z]:", tok) or ".." in tok.replace("\\", "/").split("/"):
                return None
            if ":" in tok:
                # "@12:30", "@https://x": not a file - unless the part before the colon IS one (an alternate data stream of it)
                if (path.parent / tok.split(":", 1)[0]).exists():
                    return None
                continue
            cand = path.parent / tok
            if is_link(cand):
                return None                                # judged before anything stats it: a link to //host/share would be opened
            if not cand.exists():
                continue                                   # the CLI ignores an import that does not exist (an @mention)
            if cand.is_dir():
                continue                                   # "@docs": a folder is not an import
            if depth >= 5 or seen >= 50 or not _regular(cand, root):
                return None
            seen += 1
            try:
                body = _gate_text(cand)
            except OSError:
                return None
            if body is None:
                return None
            out.append(body)
            queue.append((cand, body, depth + 1))
    return out


def _gate_text(p: Path) -> Optional[str]:
    """The text of a rules/import file as the data-flow classifier must see it. UTF-16 with a byte order mark is decoded (read as UTF-8
    it is text with NULs between the characters, which no detector recognises, while the engine still reads it); NULs without a
    byte order mark (UTF-16 without BOM, binary) cannot be judged: None, and the caller withholds the attachment."""
    data = p.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data:
        return None
    return data.decode("utf-8-sig", errors="replace")


def _regular(p: Path, root: Path) -> bool:
    """A regular file below ``root`` that is not reached through a link (symlink or junction) on the way."""
    return not passes_link(root, p) and p.is_file() and p.stat().st_size <= _MAX_FILE


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
    if not entry or not entry.get("path") or entry.get("pending") or registry.is_trashed(entry):
        return None                                   # pending: an import that is not applied yet (ADR-2323 Decision 13); in the trash
    root = Path(str(entry.get("path")))
    if not att.get("path") or os.path.abspath(att["path"]) != os.path.abspath(root):
        return None                                   # a legacy entry, or another folder that once had this id: attach again (R2)
    try:
        if not root.is_dir() or is_link(root):
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
        rules_text = project_rules_text(root)
        if rules_text is None:
            return None                                   # a rules file the gate cannot read (too large, a link): withheld
        if rules_text and l34_allows is not None:
            try:
                if not l34_allows(rules_text):
                    return None
            except Exception:  # noqa: BLE001 - fail closed
                return None
        # the title goes into the system prompt too: one line, and judged by the same gate as the rules text; a title the gate
        # refuses (or cannot judge) is replaced by the project id - the attachment itself stays (REVIEW-0066 m1)
        title = manifest.one_line(str(man.get("title") or pid)) or pid
        if title != pid and l34_allows is not None:
            try:
                if not l34_allows(title):
                    title = pid
            except Exception:  # noqa: BLE001 - fail closed
                title = pid
    except (OSError, manifest.ManifestError):
        return None
    abs_root = os.path.abspath(root)
    base = deny_base(abs_root)
    if base is None:
        return None                                   # the read-only rules cannot be written for this folder: never attach without them
    # always: the project's own control files (its grants live there too) - even with write access (release review R5)
    control = [f"{tool}(/{base}/{rel})" for tool in ("Write", "Edit", "NotebookEdit")
               for rel in ("project.yaml", ".mcp.json", ".corvin/**", ".git/**", ".gitattributes")]
    deny = control + ([] if access == "write" else [f"{tool}(/{base}/**)" for tool in ("Write", "Edit", "NotebookEdit")])
    mcp = root / ".corvin" / "local" / "mcp.json"      # the ENGINE's copy only - an adopted repo's own .mcp.json never runs (review R6)
    want = registry.mcp_sha(tenant_id=tenant_id, project_id=pid)   # set only while an MCP feature is on and the engine wrote the file
    mcp_ok = False
    # read-only means no tool that writes (ADR-2317): the engine's MCP servers (forge_exec, acs_delegate, skill_create ...) are
    # handed to the chat only with write access (REVIEW-0068 #4)
    if want and access == "write" and _regular(mcp, root):
        import hashlib  # noqa: PLC0415
        mcp_ok = hashlib.sha256(mcp.read_bytes()).hexdigest() == want
    return {
        "project_id": pid,
        "title": title,
        "add_dir": abs_root,
        "env": {RULES_ENV: "1"},
        "mcp_config": str(mcp) if mcp_ok else None,
        "deny": deny,
        "classification": cls,
        "access": access,
    }
