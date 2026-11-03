"""Per-tenant memory for the console memory page (ADR A-10; replaces the S-1a stub).

Everything this module reads or writes lives under the caller's tenant home,
``<CORVIN_HOME>/tenants/<tenant>/``. It never touches ``~/.claude``, the XDG voice
memory or any other host-level store — the host developer's memory is not tenant
data (S-1a) — and it never fabricates sample entries (ADR-0763).

Sources, each one a real tenant store:

* ``conversation`` — ``global/memory/recall.db`` (Layer 28.1, ``conversation_recall``):
  turn-pairs, PII-redacted at index time. The messenger bridges index every turn;
  console web chats index through :func:`note_chat_turn` (called from
  ``chat_runtime._append_turn``) so the page fills by itself through normal use.
* ``note`` — ``global/memory/notes/<id>.json``: entries the owner adds on the page.
  JSON with an identity field (``created_by``) so the Art. 17 store sweep over
  ``global/memory`` (``erasure_handlers.COVERED_DIRS``) can attribute them.
* ``topic`` — ``global/memory/*.md``: tenant topic files.
* ``harvest`` — ``global/memory/worker_harvest/*.md``: ``memory_bridge`` worker harvest.
* ``session`` — ``sessions/*/.claude/memory/*.md``: session-scoped harvest (read-only).
* ``forge`` — ``forge/memory.md``: forge notes, listed once it holds a note (read-only).

Entry names are opaque and resolved by enumerating the tenant's own stores — a
name is never joined onto a path — so ``GET /memory/{name}`` cannot traverse.
Files reached through a symlink, or resolving outside the tenant home, are skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
_FORGE_TOP = _REPO / "corvin_operator" / "forge"
_BRIDGES_SHARED = _REPO / "corvin_operator" / "bridges" / "shared"
if str(_FORGE_TOP) not in sys.path:
    sys.path.insert(0, str(_FORGE_TOP))

from forge import paths as _forge_paths  # noqa: E402

NOTE_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")
MAX_TITLE_CHARS = 200
MAX_CONTENT_CHARS = 20_000
MAX_NOTES = 5_000
RECENT_TURNS = 50
_MAX_FILES_PER_SOURCE = 1_000
_MAX_READ_BYTES = 256 * 1024

_NOTE_ID_RE = re.compile(r"^note-[0-9a-f]{16}$")
_SAFE_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,160}$")
_TURN_RE = re.compile(r"^turn-([1-9][0-9]{0,18})$")


class MemoryNotFound(LookupError):
    pass


class MemoryReadOnly(PermissionError):
    pass


class MemoryInvalid(ValueError):
    pass


class MemoryFull(RuntimeError):
    pass


# ── tenant paths ────────────────────────────────────────────────────────────

def tenant_home(tenant_id: str) -> Path:
    return _forge_paths.tenant_home(tenant_id)


def memory_dir(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "memory"


def notes_dir(tenant_id: str) -> Path:
    return memory_dir(tenant_id) / "notes"


def ensure_layout(tenant_id: str) -> Path:
    """Create ``<tenant>/global/memory/notes`` (0700) — idempotent, called lazily on the
    first note write (recall.db is created lazily by ``conversation_recall`` on the first
    indexed turn), so a fresh install needs no manual step; reads never create anything."""
    d = notes_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    for p in (memory_dir(tenant_id), d):
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
    return d


# ── recall store (conversation_recall, imported once) ───────────────────────

_recall_mod: Any = None
_recall_lock = threading.Lock()


def recall_module() -> Any:
    """The bridge-shared ``conversation_recall`` module — the SAME writer the
    messenger bridges use, so redaction and schema are identical. ``None`` when it
    cannot be imported (the page then reports the conversation source unavailable)."""
    global _recall_mod
    if _recall_mod is not None:
        return _recall_mod
    with _recall_lock:
        if _recall_mod is not None:
            return _recall_mod
        try:
            if str(_BRIDGES_SHARED) not in sys.path:
                sys.path.insert(0, str(_BRIDGES_SHARED))
            import conversation_recall as mod  # type: ignore  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return None
        # Without a tenant resolver the module falls back to <CORVIN_HOME>/global/memory —
        # one store shared by every tenant. Pin the canonical resolver so that can't happen.
        if getattr(mod, "_tenant_global_dir", None) is None:
            mod._tenant_global_dir = _forge_paths.tenant_global_dir
        _recall_mod = mod
        return mod


# ── console chat → recall indexing ──────────────────────────────────────────

_pending_user: dict[tuple[str, str], str] = {}
_pending_lock = threading.Lock()


def _parts_text(parts: Any) -> str:
    out: list[str] = []
    for p in parts or []:
        if isinstance(p, dict) and p.get("kind") == "text" and p.get("text"):
            out.append(str(p["text"]))
    return "".join(out)


def note_chat_turn(*, tenant_id: str, sid: str, chat_key: str, role: str, parts: Any,
                   answered: bool, refused: bool = False, aside: bool = False) -> dict[str, Any] | None:
    """Feed one persisted console chat turn into the tenant recall store.

    A user turn is held until its answer arrives; an answer (``answered`` — the OS
    session, a TDE or an ACS delegation produced it) indexes the pair through
    ``conversation_recall.index_turn``, which redacts PII before anything is stored.
    Gate refusals, notices, ``/btw`` notes and console slash commands are not
    conversation and are not indexed. Never raises."""
    try:
        key = (tenant_id, sid)
        if role == "user":
            if aside:
                return None
            text = _parts_text(parts).strip()
            with _pending_lock:
                if text and not text.startswith("/"):
                    _pending_user[key] = text
                else:
                    _pending_user.pop(key, None)
            return None
        if role != "assistant" or aside:
            return None
        with _pending_lock:
            user_text = _pending_user.pop(key, "")
        answer = _parts_text(parts).strip()
        if refused or not answered or not user_text or not answer:
            return None
        mod = recall_module()
        if mod is None:
            return None
        return mod.index_turn(
            "web", chat_key, user_text=user_text, assistant_text=answer,
            persona="assistant", tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001 — memory must never break a chat turn
        return None


def forget_chat(tenant_id: str, chat_key: str) -> int:
    """Erase a deleted chat's turns from the recall store (deletion is erasure)."""
    with _pending_lock:
        _pending_user.pop((tenant_id, chat_key.split(":", 1)[-1]), None)
    mod = recall_module()
    if mod is None or not chat_key:
        return 0
    try:
        if not mod._db_path(tenant_id).is_file():
            return 0
        return int(mod.forget(chat_key=chat_key, tenant_id=tenant_id))
    except Exception:  # noqa: BLE001
        return 0


# ── file-backed entries ─────────────────────────────────────────────────────

def _inside(path: Path, root: Path) -> bool:
    try:
        rp = path.resolve()
        return rp.is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _safe_files(root: Path, pattern: str, home: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        return []
    out = []
    for p in sorted(root.glob(pattern)):
        if p.is_symlink() or not p.is_file() or not _inside(p, home):
            continue
        out.append(p)
        if len(out) >= _MAX_FILES_PER_SOURCE:
            break
    return out


def _read_text(p: Path) -> str:
    with p.open("rb") as fh:
        data = fh.read(_MAX_READ_BYTES)
    return data.decode("utf-8", errors="replace")


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal ``---`` front matter (``name``, ``description``, ``type`` incl. the
    nested ``metadata: type:`` form memory_bridge writes). Never raises."""
    norm = text.replace("\r\n", "\n")
    if not norm.startswith("---\n"):
        return {}, norm
    end = norm.find("\n---", 4)
    if end < 0:
        return {}, norm
    meta: dict[str, str] = {}
    for line in norm[4:end].splitlines():
        m = re.match(r"^\s*([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m and m.group(2).strip():
            meta.setdefault(m.group(1).lower(), m.group(2).strip().strip("\"'"))
    body = norm[end + 4:].lstrip("-").lstrip("\n")
    return meta, body


def _first_line(text: str, n: int = 160) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:n]
    return ""


def _md_type(meta: dict[str, str], default: str) -> str:
    t = (meta.get("type") or "").lower()
    return t if t in NOTE_TYPES else default


def _stat(p: Path) -> tuple[int | None, float | None]:
    try:
        st = p.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return None, None


def _md_entry(source: str, name: str, p: Path, default_type: str, *,
              deletable: bool, origin: str | None = None) -> dict[str, Any]:
    try:
        text = _read_text(p)
    except OSError:
        text = ""
    meta, body = _frontmatter(text)
    size, mtime = _stat(p)
    return {
        "name": name, "source": source, "type": _md_type(meta, default_type),
        "title": (meta.get("name") or p.stem)[:MAX_TITLE_CHARS],
        "description": (meta.get("description") or _first_line(body)) or None,
        "size_bytes": size, "modified": mtime, "deletable": deletable,
        "origin": origin, "_path": p, "_content": body,
    }


def _note_entry(p: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(_read_text(p))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    content = str(data.get("content") or "")
    size, mtime = _stat(p)
    t = str(data.get("type") or "project")
    return {
        "name": p.stem, "source": "note", "type": t if t in NOTE_TYPES else "project",
        "title": str(data.get("title") or p.stem)[:MAX_TITLE_CHARS],
        "description": _first_line(content) or None,
        "size_bytes": size, "modified": float(data.get("updated") or mtime or 0) or mtime,
        "created": data.get("created"), "deletable": True, "origin": None,
        "_path": p, "_content": content,
    }


def _file_entries(tenant_id: str) -> list[dict[str, Any]]:
    home = tenant_home(tenant_id)
    mem = memory_dir(tenant_id)
    out: list[dict[str, Any]] = []
    for p in _safe_files(mem / "notes", "note-*.json", home):
        if _NOTE_ID_RE.match(p.stem):
            e = _note_entry(p)
            if e:
                out.append(e)
    for p in _safe_files(mem, "*.md", home):
        if _SAFE_STEM_RE.match(p.stem):
            out.append(_md_entry("topic", f"topic-{p.stem}", p, "project", deletable=True))
    for p in _safe_files(mem / "worker_harvest", "*.md", home):
        if _SAFE_STEM_RE.match(p.stem):
            out.append(_md_entry("harvest", f"harvest-{p.stem}", p, "project", deletable=True))
    sessions = home / "sessions"
    if sessions.is_dir() and not sessions.is_symlink():
        n = 0
        for sdir in sorted(sessions.iterdir()):
            if n >= _MAX_FILES_PER_SOURCE or sdir.is_symlink() or not sdir.is_dir():
                continue
            for p in _safe_files(sdir / ".claude" / "memory", "*.md", home):
                rel = p.relative_to(home).as_posix()
                name = "session-" + hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]
                out.append(_md_entry("session", name, p, "project", deletable=False,
                                     origin=sdir.name))
                n += 1
    forge_mem = home / "forge" / "memory.md"
    if forge_mem.is_file() and not forge_mem.is_symlink() and _inside(forge_mem, home):
        try:
            text = _read_text(forge_mem)
        except OSError:
            text = ""
        if "\n## " in text.replace("\r\n", "\n"):
            e = _md_entry("forge", "forge-notes", forge_mem, "reference", deletable=False)
            e["title"] = "Forge notes"
            e["description"] = "Lessons learned recorded by forge runs"
            out.append(e)
    return out


def _public(e: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in e.items() if not k.startswith("_")}


def _turn_entry(t: dict[str, Any]) -> dict[str, Any]:
    user = " ".join((t.get("user_text") or "").split())
    asst = " ".join((t.get("assistant_text") or "").split())
    return {
        "name": f"turn-{t['id']}", "source": "conversation", "type": "conversation",
        "title": (user[:120] or "(turn)"), "description": asst[:200] or None,
        "size_bytes": len(t.get("user_text") or "") + len(t.get("assistant_text") or ""),
        "modified": t.get("ts"), "deletable": True,
        "origin": t.get("chat_key") or None, "channel": t.get("channel") or None,
    }


# ── public API (routes/memory.py) ───────────────────────────────────────────

def index(tenant_id: str) -> dict[str, Any]:
    files = [_public(e) for e in _file_entries(tenant_id)]
    counts: dict[str, int] = {s: 0 for s in ("conversation", "note", "topic", "harvest",
                                             "session", "forge")}
    for e in files:
        counts[e["source"]] = counts.get(e["source"], 0) + 1
    mod = recall_module()
    recall_info: dict[str, Any] = {"available": mod is not None, "turns": 0, "chats": 0,
                                   "last_ts": None}
    turns: list[dict[str, Any]] = []
    if mod is not None:
        recall_info.update(mod.turn_stats(tenant_id=tenant_id))
        turns = [_turn_entry(t) for t in mod.list_turns(limit=RECENT_TURNS, tenant_id=tenant_id)]
    counts["conversation"] = int(recall_info["turns"])
    entries = files + turns
    entries.sort(key=lambda e: e.get("modified") or 0, reverse=True)
    total = sum(counts.values())
    return {
        "tenant_id": tenant_id,
        "present": total > 0,
        "available": True,
        "reason": None if mod is not None else "conversation recall store unavailable",
        "count": total,
        "sources": counts,
        "recall": recall_info,
        "truncated": counts["conversation"] > len(turns),
        "files": entries,
    }


def get_entry(tenant_id: str, name: str) -> dict[str, Any]:
    m = _TURN_RE.match(name or "")
    if m:
        mod = recall_module()
        t = mod.get_turn(int(m.group(1)), tenant_id=tenant_id) if mod is not None else None
        if t is None:
            raise MemoryNotFound(name)
        e = _turn_entry(t)
        e.update({
            "user_text": t["user_text"], "assistant_text": t["assistant_text"],
            "redacted_classes": t["redacted_classes"], "persona": t["persona"] or None,
            "content": f"User:\n{t['user_text']}\n\nAssistant:\n{t['assistant_text']}",
        })
        return e
    for e in _file_entries(tenant_id):
        if e["name"] == name:
            out = _public(e)
            out["content"] = e["_content"]
            return out
    raise MemoryNotFound(name)


def add_note(tenant_id: str, *, title: str, content: str, type: str = "project",
             created_by: str = "", session_fingerprint: str = "") -> dict[str, Any]:
    title = " ".join((title or "").split())
    content = (content or "").replace("\r\n", "\n").strip()
    if not title or len(title) > MAX_TITLE_CHARS:
        raise MemoryInvalid(f"title must be 1..{MAX_TITLE_CHARS} characters")
    if not content or len(content) > MAX_CONTENT_CHARS:
        raise MemoryInvalid(f"content must be 1..{MAX_CONTENT_CHARS} characters")
    if type not in NOTE_TYPES:
        raise MemoryInvalid(f"type must be one of {', '.join(NOTE_TYPES)}")
    d = ensure_layout(tenant_id)
    if sum(1 for _ in d.glob("note-*.json")) >= MAX_NOTES:
        raise MemoryFull(f"memory holds the maximum of {MAX_NOTES} notes")
    note_id = "note-" + secrets.token_hex(8)
    now = time.time()
    payload = {
        "v": 1, "id": note_id, "title": title, "type": type, "content": content,
        "created": now, "updated": now, "source": "console",
        # Identity key for the Art. 17 store sweep (erasure_handlers._SUBJECT_KEYS).
        "created_by": created_by,
        "session_fingerprint": session_fingerprint,
    }
    target = d / f"{note_id}.json"
    tmp = d / f".{note_id}.json.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return get_entry(tenant_id, note_id)


def delete_entry(tenant_id: str, name: str) -> str:
    """Delete one entry; returns its source. Session and forge entries are read-only."""
    m = _TURN_RE.match(name or "")
    if m:
        mod = recall_module()
        if mod is None or not mod.delete_turn(int(m.group(1)), tenant_id=tenant_id):
            raise MemoryNotFound(name)
        return "conversation"
    for e in _file_entries(tenant_id):
        if e["name"] != name:
            continue
        if not e["deletable"]:
            raise MemoryReadOnly(name)
        try:
            e["_path"].unlink()
        except FileNotFoundError as exc:
            raise MemoryNotFound(name) from exc
        return e["source"]
    raise MemoryNotFound(name)


def _snippet(text: str, needle: str, width: int = 90) -> str:
    flat = " ".join(text.split())
    i = flat.lower().find(needle.lower()) if needle else -1
    if i < 0:
        return flat[: 2 * width]
    a = max(0, i - width)
    return ("…" if a else "") + flat[a: i + len(needle) + width] + ("…" if i + len(needle) + width < len(flat) else "")


def search(tenant_id: str, query: str, *, limit: int = 20) -> dict[str, Any]:
    q = " ".join((query or "").split())[:200]
    limit = max(1, min(int(limit), 100))
    results: list[dict[str, Any]] = []
    if q:
        tokens = [t.lower() for t in q.split()]
        for e in _file_entries(tenant_id):
            hay = f"{e['title']}\n{e.get('description') or ''}\n{e['_content']}".lower()
            if all(t in hay for t in tokens):
                r = _public(e)
                r["snippet"] = _snippet(e["_content"] or e["title"], tokens[0])
                results.append(r)
        results.sort(key=lambda e: e.get("modified") or 0, reverse=True)
        results = results[:limit]
        mod = recall_module()
        if mod is not None and len(results) < limit:
            for t in mod.search_turns(q, limit=limit - len(results), tenant_id=tenant_id):
                r = _turn_entry(t)
                r["snippet"] = _snippet(f"{t['user_text']} — {t['assistant_text']}", tokens[0])
                r["score"] = t.get("score")
                results.append(r)
    return {"tenant_id": tenant_id, "query": q, "count": len(results), "results": results}
