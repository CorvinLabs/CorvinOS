"""ulo.py — User-Defined Learning Objectives (ADR-0163, M1).

Registry, prompt injection, and CLI for per-chat user-authored behavioural
constraints.  Compliance checking (M2) and reinforcement (M3) will extend
this module; M1 covers storage, injection, and slash-command plumbing.

Storage
-------
  <tenant_home>/<tid>/global/ulo/<safe_chan>__<safe_chat>.json  (scope=chat)
  <tenant_home>/<tid>/global/ulo/_all__<uid8>.json              (scope=all, M4)

File mode 0600.  All scopes are persisted to disk; in-memory session-scope
clearing (on /new / /clear / /reset) is planned for a future milestone.

CLI
---
  python3 ulo.py add    <channel> <chat_key> <priority> <text>
  python3 ulo.py list   <channel> <chat_key>
  python3 ulo.py pause  <channel> <chat_key> <id>
  python3 ulo.py resume <channel> <chat_key> <id>
  python3 ulo.py delete <channel> <chat_key> <id>

All output is JSON:  {"ok": true, ...}  or  {"ok": false, "error": "..."}.
Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from ulo_schema import (  # type: ignore[import-not-found]
        UserObjective, MAX_OBJECTIVES,
        make_id, validate_text, validate_priority, validate_scope,
        validate_check_trigger, sanitize_text,
    )
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from ulo_schema import (  # type: ignore[import-not-found]
        UserObjective, MAX_OBJECTIVES,
        make_id, validate_text, validate_priority, validate_scope,
        validate_check_trigger, sanitize_text,
    )


# ── Debug-log helpers (best-effort, never raise) ─────────────────────────

def _dbg_io(op: str, channel: str, chat_key: str, **kw: Any) -> None:
    try:
        import ulo_debug_log as _d  # type: ignore
        _d.log_io(op, channel, chat_key, **kw)
    except Exception:  # noqa: BLE001
        pass


def _dbg_exc(site: str, exc: BaseException, channel: str = "", chat_key: str = "") -> None:
    try:
        import ulo_debug_log as _d  # type: ignore
        _d.log_exception(site, exc, channel=channel, chat_key=chat_key)
    except Exception:  # noqa: BLE001
        pass


def _dbg_crud(op: str, channel: str, chat_key: str, **kw: Any) -> None:
    try:
        import ulo_debug_log as _d  # type: ignore
        _d.log_crud(op, channel, chat_key, **kw)
    except Exception:  # noqa: BLE001
        pass


# ── Path helpers ──────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / ".corvin_repo").exists() or (p / "plugins").is_dir():
            return p / ".corvin"
    return Path.home() / ".corvin"


def _safe(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:64] or "anon"


def _ulo_dir(tenant_id: str | None = None) -> Path:
    """Return the per-tenant ULO store directory.

    When tenant_id is given the path is scoped to that tenant
    (<corvin_home>/tenants/<tid>/global/ulo/) so objectives from
    different tenants never mix.  Falls back to the legacy global path
    for callers that don't supply a tenant_id (backward-compat).
    """
    if tenant_id:
        try:
            from paths import tenant_home as _th  # type: ignore[import-not-found]
            return _th(tenant_id) / "global" / "ulo"
        except Exception:
            pass
    return _corvin_home() / "global" / "ulo"


def _store_path(channel: str, chat_key: str, tenant_id: str | None = None) -> Path:
    return (
        _ulo_dir(tenant_id)
        / f"{_safe(channel or 'unknown')}__{_safe(str(chat_key or 'anon'))}.json"
    )


# ── Persistence ───────────────────────────────────────────────────────────

def _load_raw(channel: str, chat_key: str, tenant_id: str | None = None) -> dict[str, Any]:
    p = _store_path(channel, chat_key, tenant_id)
    _t0 = time.perf_counter()
    if not p.exists():
        _dbg_io("load", channel, chat_key,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                file_existed=False, obj_count=0)
        return {"ulo_schema_version": "1.0", "objectives": []}
    try:
        data = json.loads(p.read_text("utf-8"))
        _dbg_io("load", channel, chat_key,
                duration_ms=int((time.perf_counter() - _t0) * 1000),
                file_existed=True,
                obj_count=len(data.get("objectives", [])))
        return data
    except Exception as _exc:
        _dbg_exc("_load_raw.json_parse", _exc, channel=channel, chat_key=chat_key)
        # Rename the corrupt file to prevent silent overwrite on next save.
        try:
            p.rename(p.with_suffix(".corrupt"))
        except OSError:
            pass
        # Best-effort audit event — do not let audit failure suppress the corruption signal.
        try:
            import hashlib as _hl  # noqa: PLC0415
            try:
                from forge.security_events import write_event as _sec_write  # type: ignore  # noqa: PLC0415
            except ImportError:
                from security_events import write_event as _sec_write  # type: ignore  # noqa: PLC0415
            _sec_write("ulo.store_corrupted", {
                "channel": channel,
                "chat_key_hash": _hl.sha256(chat_key.encode()).hexdigest()[:16],
            })
        except Exception:  # noqa: BLE001
            pass
        return {"ulo_schema_version": "1.0", "objectives": []}


def _save_raw(channel: str, chat_key: str, data: dict[str, Any], tenant_id: str | None = None) -> None:
    p = _store_path(channel, chat_key, tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    _t0 = time.perf_counter()
    # Use mkstemp for a unique tmp name — avoids concurrent-writer collision
    # when multiple threads write to the same chat's file simultaneously.
    fd, tmp_path = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        os.write(fd, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    Path(tmp_path).replace(p)
    _dbg_io("save", channel, chat_key,
            duration_ms=int((time.perf_counter() - _t0) * 1000),
            obj_count=len(data.get("objectives", [])))


def load(channel: str, chat_key: str, tenant_id: str | None = None) -> list[UserObjective]:
    """Return all objectives for this chat (active + paused)."""
    raw = _load_raw(channel, chat_key, tenant_id)
    objs: list[UserObjective] = []
    for d in raw.get("objectives", []):
        try:
            objs.append(UserObjective.from_dict(d))
        except Exception:
            pass
    return objs


def _save_all(channel: str, chat_key: str, objectives: list[UserObjective], tenant_id: str | None = None) -> None:
    data = {
        "ulo_schema_version": "1.0",
        "updated_at": time.time(),
        "objectives": [o.to_dict() for o in objectives],
    }
    _save_raw(channel, chat_key, data, tenant_id)


# ── CRUD operations ───────────────────────────────────────────────────────

def add(
    channel: str,
    chat_key: str,
    text: str,
    priority: str = "medium",
    scope: str = "chat",
    check_trigger: str = "always",
    max_objectives: int | None = None,
    tenant_id: str | None = None,
) -> UserObjective:
    """Create a new objective.  Raises ValueError on validation failure
    or when the per-chat limit is reached."""
    text = validate_text(text)
    priority = validate_priority(priority)
    scope = validate_scope(scope)
    check_trigger = validate_check_trigger(check_trigger)

    cap = max_objectives if max_objectives is not None else int(
        os.environ.get("ULO_MAX_OBJECTIVES", MAX_OBJECTIVES)
    )
    existing = load(channel, chat_key, tenant_id)
    active_count = sum(1 for o in existing if o.active)
    if active_count >= cap:
        raise ValueError(
            f"maximum of {cap} active objectives reached for this chat"
        )

    obj = UserObjective(
        id=make_id(),
        text=text,
        priority=priority,         # type: ignore[arg-type]
        scope=scope,               # type: ignore[arg-type]
        check_trigger=check_trigger,  # type: ignore[arg-type]
    )
    existing.append(obj)
    _audit("ulo.objective_created", {
        "ulo_id": obj.id, "priority": obj.priority,
        "scope": obj.scope, "channel": channel,
    })
    _save_all(channel, chat_key, existing, tenant_id)
    _dbg_crud("add", channel, chat_key, obj_id=obj.id,
              scope=scope, priority=priority, check_trigger=check_trigger,
              active_count_after=sum(1 for o in existing if o.active))
    return obj


def _find(objectives: list[UserObjective], ulo_id: str) -> UserObjective | None:
    for o in objectives:
        if o.id == ulo_id:
            return o
    return None


def set_active(channel: str, chat_key: str, ulo_id: str, active: bool, tenant_id: str | None = None) -> bool:
    """Pause (active=False) or resume (active=True) an objective.
    Returns True if found and changed, False if not found."""
    objs = load(channel, chat_key, tenant_id)
    obj = _find(objs, ulo_id)
    if obj is None:
        _dbg_crud("pause" if not active else "resume", channel, chat_key,
                  obj_id=ulo_id, found=False)
        return False
    obj.active = active
    obj.updated_at = time.time()
    _save_all(channel, chat_key, objs, tenant_id)
    _dbg_crud("pause" if not active else "resume", channel, chat_key,
              obj_id=ulo_id, found=True,
              active_count_after=sum(1 for o in objs if o.active))
    return True


def delete(channel: str, chat_key: str, ulo_id: str, tenant_id: str | None = None) -> bool:
    """Delete an objective by id.  Returns True if deleted."""
    objs = load(channel, chat_key, tenant_id)
    new = [o for o in objs if o.id != ulo_id]
    if len(new) == len(objs):
        _dbg_crud("delete", channel, chat_key, obj_id=ulo_id, found=False)
        return False
    _save_all(channel, chat_key, new, tenant_id)
    _dbg_crud("delete", channel, chat_key, obj_id=ulo_id, found=True,
              active_count_after=sum(1 for o in new if o.active))
    return True


def update_text(
    channel: str, chat_key: str, ulo_id: str, new_text: str, tenant_id: str | None = None,
) -> bool:
    """Update the text of an existing objective.  Returns True if found."""
    new_text = validate_text(new_text)
    objs = load(channel, chat_key, tenant_id)
    obj = _find(objs, ulo_id)
    if obj is None:
        _dbg_crud("update", channel, chat_key, obj_id=ulo_id, found=False)
        return False
    obj.text = new_text
    obj.updated_at = time.time()
    _save_all(channel, chat_key, objs, tenant_id)
    _dbg_crud("update", channel, chat_key, obj_id=ulo_id, found=True)
    return True


# ── Prompt injection ──────────────────────────────────────────────────────

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def render_block(channel: str, chat_key: str, tenant_id: str | None = None) -> str:
    """Return the <learning_objectives> system-prompt block, or '' if none
    are active.  Objectives are sorted high → medium → low priority."""
    objs = [o for o in load(channel, chat_key, tenant_id) if o.active]
    if not objs:
        return ""

    objs.sort(key=lambda o: _PRIORITY_ORDER.get(o.priority, 1))

    lines = ["<learning_objectives>"]
    for obj in objs:
        prio_tag = {"high": "high", "medium": "med", "low": "low"}.get(
            obj.priority, obj.priority
        )
        # Defense-in-depth: re-sanitise at render time so objectives written to
        # the store BEFORE the validate_text hardening cannot break out of the
        # block (security review 2026-06-27).
        lines.append(f"  [{prio_tag}] {sanitize_text(obj.text)}")
    lines.append("</learning_objectives>")
    return "\n".join(lines)


# ── Erasure handler (L36) ─────────────────────────────────────────────────

class ULOErasureHandler:
    """GDPR Art. 17 erasure handler for ULO objective files (L36 / ADR-0163).

    Deletes every ``ulo/<chan>__<chat>.json`` under the tenant home when the
    file's chat_key is attributed to ``subject_id``.  Since filenames encode
    channel/chat but not uid, we conservatively delete all ULO files under
    this tenant home that match subject_id as a prefix of the chat component
    (same convention as A2AErasureHandler — ``subject_id`` or
    ``subject_id:`` prefix).
    """

    layer_id: str = "L163-ulo"

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    def purge(self, subject_id: str, request_id: str) -> object:  # returns ErasureLayerResult
        try:
            from erasure_orchestrator import (  # type: ignore[import-not-found]
                ErasureLayerResult, LayerStatus,
            )
        except ImportError:
            return None  # not installed — silently skip

        start = time.time()
        ulo_dir = _ulo_dir(self._tenant_id)
        if not ulo_dir.is_dir():
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason="ulo_store_absent",
                duration_ms=0,
            )

        count = 0
        prefix_bare   = subject_id
        prefix_colon  = subject_id + ":"
        # Filenames are _safe()-normalised (colons → underscores), so also
        # compare the safe form of subject_id directly against the chat part.
        safe_subject  = _safe(subject_id)
        for f in list(ulo_dir.iterdir()):
            if not f.is_file() or f.suffix not in (".json", ""):
                continue
            # Filename: <safe_chan>__<safe_chat>.json
            stem = f.stem
            chat_part = stem.split("__", 1)[-1] if "__" in stem else stem
            if (
                chat_part == prefix_bare
                or chat_part.startswith(prefix_colon)
                or chat_part == safe_subject
            ):
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass

        duration_ms = int((time.time() - start) * 1000)
        if count:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.APPLIED,
                count=count,
                reason="",
                duration_ms=duration_ms,
            )
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.SKIPPED,
            count=0,
            reason="no_ulo_files_matched",
            duration_ms=duration_ms,
        )


# ── CLI entry-point ───────────────────────────────────────────────────────

def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _audit(event_type: str, details: dict) -> None:
    """Emit a metadata-only L16 audit event.  Best-effort — never raises."""
    try:
        try:
            from forge.security_events import write_event as _we  # type: ignore  # noqa: PLC0415
        except ImportError:
            from security_events import write_event as _we  # type: ignore  # noqa: PLC0415
        _we(event_type, details)
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str]) -> None:  # noqa: C901
    # Optional --tenant-id <tid> before the positional args (for bridge callers
    # that know the tenant).  Strip it first so positional indexing is stable.
    tenant_id: str | None = None
    rest = list(argv[1:])
    for i, arg in enumerate(rest):
        if arg == "--tenant-id" and i + 1 < len(rest):
            tenant_id = rest[i + 1]
            del rest[i:i + 2]
            break

    if len(rest) < 3:
        _emit({"ok": False, "error":
               "Usage: ulo.py [--tenant-id <tid>] "
               "<add|list|pause|resume|delete|update> "
               "<channel> <chat_key> [args...]"})
        return

    cmd, channel, chat_key = rest[0], rest[1], rest[2]

    if cmd == "list":
        objs = load(channel, chat_key, tenant_id)
        _emit({
            "ok": True,
            "objectives": [o.to_dict() for o in objs],
            "count": len(objs),
            "active_count": sum(1 for o in objs if o.active),
        })

    elif cmd == "add":
        # rest: add <chan> <chat> <priority> <text...>
        if len(rest) < 5:
            _emit({"ok": False,
                   "error": "add requires: <priority> <text>"})
            return
        priority_arg = rest[3]
        text_arg     = " ".join(rest[4:]).strip()
        scope_arg    = "chat"
        trigger_arg  = "always"
        try:
            obj = add(channel, chat_key, text_arg,
                      priority=priority_arg, scope=scope_arg,
                      check_trigger=trigger_arg, tenant_id=tenant_id)
            _emit({"ok": True, "objective": obj.to_dict()})
        except ValueError as e:
            _emit({"ok": False, "error": str(e)})

    elif cmd in ("pause", "resume"):
        if len(rest) < 4:
            _emit({"ok": False, "error": f"{cmd} requires: <id>"})
            return
        ulo_id = rest[3]
        active = (cmd == "resume")
        # Peek to check existence before emitting — audit only real mutations.
        _peek = load(channel, chat_key, tenant_id)
        if any(o.id == ulo_id for o in _peek):
            _audit("ulo.objective_updated", {
                "ulo_id": ulo_id, "action": cmd, "channel": channel,
            })
        found  = set_active(channel, chat_key, ulo_id, active, tenant_id)
        if found:
            _emit({"ok": True, "id": ulo_id, "active": active})
        else:
            _emit({"ok": False, "error": f"objective {ulo_id!r} not found"})

    elif cmd == "delete":
        if len(rest) < 4:
            _emit({"ok": False, "error": "delete requires: <id>"})
            return
        ulo_id = rest[3]
        _peek = load(channel, chat_key, tenant_id)
        if any(o.id == ulo_id for o in _peek):
            _audit("ulo.objective_deleted", {
                "ulo_id": ulo_id, "channel": channel,
            })
        found  = delete(channel, chat_key, ulo_id, tenant_id)
        if found:
            _emit({"ok": True, "id": ulo_id, "deleted": True})
        else:
            _emit({"ok": False, "error": f"objective {ulo_id!r} not found"})

    elif cmd == "update":
        # rest: update <chan> <chat> <id> <new_text...>
        if len(rest) < 5:
            _emit({"ok": False, "error": "update requires: <id> <new_text>"})
            return
        ulo_id   = rest[3]
        new_text = " ".join(rest[4:]).strip()
        try:
            _peek = load(channel, chat_key, tenant_id)
            if any(o.id == ulo_id for o in _peek):
                _audit("ulo.objective_updated", {
                    "ulo_id": ulo_id, "action": "update", "channel": channel,
                })
            found = update_text(channel, chat_key, ulo_id, new_text, tenant_id)
            if found:
                _emit({"ok": True, "id": ulo_id, "text": new_text})
            else:
                _emit({"ok": False,
                       "error": f"objective {ulo_id!r} not found"})
        except ValueError as e:
            _emit({"ok": False, "error": str(e)})

    elif cmd == "render":
        block = render_block(channel, chat_key, tenant_id)
        _emit({"ok": True, "block": block})

    else:
        _emit({"ok": False, "error": f"unknown command: {cmd!r}"})


if __name__ == "__main__":
    main(sys.argv)
