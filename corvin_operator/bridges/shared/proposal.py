"""proposal.py — Layer 21: curated proposal stack (multi-user input, owner trigger).

Layer 16 Phase 2 (`observer_visibility = "transcript"`) lets a chat collect
read-only-sender messages into a buffer that automatically prepends to the
*next* OWNER turn. Useful, but **passive**: the owner sees what landed only
after the LLM has already replied.

This module is the **active**, **curated** variant. Multiple users
(members, observers, even the owner) can submit content via `/propose`;
the stack accumulates without auto-triggering. The owner gets a
``/proposals`` overview, can drop individual entries via
``/proposal rm <id>``, and explicitly fires the LLM with ``/go [text]``,
at which point the stack is consumed and folded into the prompt
together with the owner's optional steering message.

Storage
-------
Single JSON file per (channel, chat) at::

    <corvin_home>/global/proposals/<safe_channel>__<safe_chat>.json

::

    {
      "proposals": [
        {
          "id":       "ab12cd",            # 6-char short hash for /proposal rm
          "from_uid": "alice",
          "from_role": "member",            # snapshot at submit-time
          "text":     "...",                # ≤ MAX_TEXT_CHARS
          "ts":       1778204770.0
        },
        ...
      ]
    }

Limits:

  * ``MAX_STACK_SIZE`` = 50 proposals per chat (oldest dropped)
  * ``MAX_TEXT_CHARS`` = 2000 per entry

Audit
-----
``proposal.added`` (INFO), ``proposal.removed`` (INFO),
``proposal.cleared`` (INFO), ``proposal.executed`` (INFO with count).
All land in the unified hash chain at
``<corvin_home>/global/forge/audit.jsonl``.

Design notes
------------
* The stack is **chat-scoped**, not user-scoped. A team in one chat
  builds one stack together; a different chat has its own.
* Proposals do not expire by themselves. The operator clears via
  ``/proposal clear`` or implicitly via ``/go`` (atomic consume).
  This avoids surprise loss of accumulated content overnight.
* The ``/go`` flow is implemented in the daemon, not here — this
  module exposes ``consume_for_go()`` (atomic read + clear) and
  ``format_for_prompt()`` (build the prompt augmentation block).
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

# Hard caps. Cannot be lowered without losing data; the daemon should
# refuse new submissions when the stack is full and ask the owner to
# /go or /proposal clear first.
MAX_STACK_SIZE = 50
MAX_TEXT_CHARS = 2000


# ── Path resolution (mirror of consent.py / roles.py) ─────────────────

def _corvin_home() -> Path:
    """Phase 1 strangler-fig: CORVIN_HOME canonical, CORVIN_HOME alias.
    On disk .corvin preferred, .corvinOS legacy fallback. Silent on
    legacy reads — paths.py emits the canonical deprecation log."""
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            new = parent / ".corvin"
            legacy = parent / ".corvinOS"
            if new.is_dir():
                return new
            if legacy.is_dir():
                return legacy
            return new
    new_default = Path.home() / ".corvin"
    legacy_default = Path.home() / ".corvinOS"
    if not new_default.is_dir() and legacy_default.is_dir():
        return legacy_default
    return new_default


def _safe_component(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:64] or "anon"


def _store_path(channel: str, chat_key: str, *, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        base = home / "global" / "proposals"
    else:
        base = home / "tenants" / tenant_id / "global" / "proposals"
    return (base / f"{_safe_component(channel or 'unknown')}__"
            f"{_safe_component(str(chat_key) if chat_key is not None else 'anon')}.json")


def _audit_path(*, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        return home / "global" / "forge" / "audit.jsonl"
    return home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


# ── Audit helper ───────────────────────────────────────────────────────

def _audit(event_type: str, *, channel: str, chat_key: str,
           details: dict[str, Any] | None = None,
           severity: str | None = None) -> None:
    try:
        import sys
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                forge_pkg = parent / "operator" / "forge"
                if str(forge_pkg) not in sys.path:
                    sys.path.insert(0, str(forge_pkg))
                break
        from forge.security_events import write_event  # type: ignore
    except Exception:
        return
    body: dict[str, Any] = {"channel": channel, "chat_key": chat_key}
    if details:
        body.update(details)
    try:
        if severity:
            write_event(_audit_path(), event_type, details=body, severity=severity)
        else:
            write_event(_audit_path(), event_type, details=body)
    except Exception:
        pass


# ── Store I/O ──────────────────────────────────────────────────────────

def _load_store(path: Path) -> dict:
    if not path.exists():
        return {"proposals": []}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {"proposals": []}
        if not isinstance(data.get("proposals"), list):
            data["proposals"] = []
        return data
    except (OSError, json.JSONDecodeError):
        return {"proposals": []}


def _save_store(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _short_id(seed: str) -> str:
    """Deterministic-ish short ID from seed + ts. Collisions inside a
    single chat are unlikely (50-cap stack); we re-hash with the
    counter on the off-chance of dupes."""
    h = hashlib.sha1(seed.encode("utf-8", errors="replace")).hexdigest()
    return h[:6]


# ── Public API ─────────────────────────────────────────────────────────

def add(channel: str, chat_key: str, *,
        from_uid: str, text: str,
        from_role: str = "unknown",
        via: str = "slash") -> dict:
    """Append ``text`` to the proposal stack.

    Returns ``{"ok": bool, "reason": str, "entry"?: dict}``. Failure
    reasons:

      * ``"empty-text"`` — the user typed ``/propose`` alone
      * ``"text-too-long"`` — over MAX_TEXT_CHARS (clamped at submit)
      * ``"stack-full"`` — over MAX_STACK_SIZE (oldest is dropped to
        make room; this is *not* a refusal but a notice)
    """
    if not from_uid:
        return {"ok": False, "reason": "missing-from-uid"}
    body = (text or "").strip()
    if not body:
        return {"ok": False, "reason": "empty-text"}
    truncated = False
    if len(body) > MAX_TEXT_CHARS:
        body = body[:MAX_TEXT_CHARS]
        truncated = True

    now = time.time()
    path = _store_path(channel, chat_key)
    data = _load_store(path)
    stack = data["proposals"]

    # Drop oldest if at cap
    dropped = None
    if len(stack) >= MAX_STACK_SIZE:
        dropped = stack.pop(0)

    seed = f"{from_uid}|{now}|{body[:40]}|{len(stack)}"
    pid = _short_id(seed)
    # Avoid id collision inside the (small) stack
    while any(p.get("id") == pid for p in stack):
        seed += "x"
        pid = _short_id(seed)

    entry = {
        "id": pid,
        "from_uid": from_uid,
        "from_role": from_role,
        "text": body,
        "ts": now,
        "via": via,
    }
    stack.append(entry)
    data["proposals"] = stack
    _save_store(path, data)

    _audit("proposal.added",
           channel=channel, chat_key=str(chat_key),
           details={
               "id": pid, "from_uid": from_uid, "from_role": from_role,
               "len": len(body), "truncated": truncated,
               "stack_size": len(stack),
               "dropped": dropped["id"] if dropped else None,
           })
    return {
        "ok": True, "reason": "added",
        "entry": entry,
        "truncated": truncated,
        "dropped": dropped,
        "stack_size": len(stack),
    }


def list_(channel: str, chat_key: str) -> list[dict]:
    """Return the current stack (oldest first)."""
    return _load_store(_store_path(channel, chat_key))["proposals"]


def get(channel: str, chat_key: str, prop_id: str) -> dict | None:
    """Return one entry by id, or None."""
    for p in list_(channel, chat_key):
        if p.get("id") == prop_id:
            return p
    return None


def remove(channel: str, chat_key: str, prop_id: str, *,
           removed_by: str, via: str = "slash") -> bool:
    """Drop one entry by id. Returns True iff something was removed."""
    if not prop_id:
        return False
    path = _store_path(channel, chat_key)
    data = _load_store(path)
    stack = data["proposals"]
    new_stack = [p for p in stack if p.get("id") != prop_id]
    if len(new_stack) == len(stack):
        return False
    data["proposals"] = new_stack
    _save_store(path, data)
    _audit("proposal.removed",
           channel=channel, chat_key=str(chat_key),
           details={"id": prop_id, "removed_by": removed_by, "via": via})
    return True


def clear(channel: str, chat_key: str, *,
          cleared_by: str, via: str = "slash") -> int:
    """Drop the entire stack. Returns the number removed."""
    path = _store_path(channel, chat_key)
    data = _load_store(path)
    n = len(data["proposals"])
    if n == 0:
        return 0
    data["proposals"] = []
    _save_store(path, data)
    _audit("proposal.cleared",
           channel=channel, chat_key=str(chat_key),
           details={"count": n, "cleared_by": cleared_by, "via": via})
    return n


def consume_for_go(channel: str, chat_key: str, *,
                   triggered_by: str, owner_text: str = "",
                   via: str = "slash") -> list[dict]:
    """Atomic read + clear. Returns the entries that were on the stack
    at the moment of the call. Emits ``proposal.executed``.

    The daemon calls this when the owner types ``/go`` and uses the
    return value to construct the LLM prompt.
    """
    path = _store_path(channel, chat_key)
    data = _load_store(path)
    entries = data["proposals"]
    if not entries:
        # Still emit an audit event so /go without proposals is visible
        _audit("proposal.executed",
               channel=channel, chat_key=str(chat_key),
               details={
                   "count": 0, "triggered_by": triggered_by,
                   "owner_text_len": len(owner_text or ""),
                   "via": via,
               })
        return []
    data["proposals"] = []
    _save_store(path, data)
    _audit("proposal.executed",
           channel=channel, chat_key=str(chat_key),
           details={
               "count": len(entries),
               "from_uids": sorted({e.get("from_uid", "?") for e in entries}),
               "triggered_by": triggered_by,
               "owner_text_len": len(owner_text or ""),
               "via": via,
           })
    return entries


def format_for_prompt(entries: list[dict], owner_text: str = "") -> str:
    """Render a stack snapshot into a prompt-friendly block.

    Output shape (when entries non-empty)::

        [PROPOSAL STACK — N items, curated by the owner]
          <id> [<from_uid>, <from_role>]: <text>
          ...
        [END PROPOSAL STACK]

        <owner_text or "Bitte arbeite die obigen Vorschläge ab.">

    When entries is empty: just the owner_text (or a hint if both are
    empty so the daemon can refuse the /go).
    """
    if not entries:
        return (owner_text or "").strip()

    lines = [f"[PROPOSAL STACK — {len(entries)} items, curated by the owner]"]
    for e in entries:
        eid = e.get("id", "?")
        fu = e.get("from_uid", "?")
        fr = e.get("from_role", "?")
        body = (e.get("text") or "").replace("\n", "\n    ")
        lines.append(f"  {eid} [{fu}, {fr}]: {body}")
    lines.append("[END PROPOSAL STACK]")
    lines.append("")
    if owner_text and owner_text.strip():
        lines.append(owner_text.strip())
    else:
        lines.append("Bitte arbeite die obigen Vorschläge ab.")
    return "\n".join(lines)


def status(channel: str, chat_key: str) -> dict:
    entries = list_(channel, chat_key)
    return {
        "channel": channel,
        "chat_key": chat_key,
        "stack_size": len(entries),
        "max_stack_size": MAX_STACK_SIZE,
        "max_text_chars": MAX_TEXT_CHARS,
        "from_uids": sorted({e.get("from_uid", "?") for e in entries}),
        "oldest_ts": entries[0]["ts"] if entries else None,
        "newest_ts": entries[-1]["ts"] if entries else None,
    }


# ── CLI ────────────────────────────────────────────────────────────────

def _cli_add(channel: str, chat_key: str, from_uid: str,
             from_role: str, text: str) -> int:
    r = add(channel, chat_key, from_uid=from_uid,
            from_role=from_role, text=text, via="cli")
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


def _cli_list(channel: str, chat_key: str) -> int:
    print(json.dumps(list_(channel, chat_key),
                     ensure_ascii=False, indent=2))
    return 0


def _cli_remove(channel: str, chat_key: str, prop_id: str,
                removed_by: str) -> int:
    existed = remove(channel, chat_key, prop_id,
                     removed_by=removed_by, via="cli")
    print(json.dumps({"ok": True, "existed": existed}))
    return 0


def _cli_clear(channel: str, chat_key: str, cleared_by: str) -> int:
    n = clear(channel, chat_key, cleared_by=cleared_by, via="cli")
    print(json.dumps({"ok": True, "removed": n}))
    return 0


def _cli_consume(channel: str, chat_key: str,
                 triggered_by: str, owner_text: str) -> int:
    entries = consume_for_go(channel, chat_key,
                             triggered_by=triggered_by,
                             owner_text=owner_text, via="cli")
    payload = format_for_prompt(entries, owner_text=owner_text)
    print(json.dumps({
        "ok": True, "count": len(entries),
        "entries": entries, "prompt": payload,
    }, ensure_ascii=False, indent=2))
    return 0


def _cli_status(channel: str, chat_key: str) -> int:
    print(json.dumps(status(channel, chat_key),
                     ensure_ascii=False, indent=2))
    return 0


def _cli_format(channel: str, chat_key: str, owner_text: str) -> int:
    """Read-only: render the current stack as a prompt block (does NOT
    consume). Used by the daemon for /proposals preview."""
    entries = list_(channel, chat_key)
    print(format_for_prompt(entries, owner_text=owner_text))
    return 0


def _cli_main(argv: list[str]) -> int:
    """Subcommands:

      add     <channel> <chat_key> <from_uid> <from_role> <text...>
      list    <channel> <chat_key>
      remove  <channel> <chat_key> <prop_id> <removed_by>
      clear   <channel> <chat_key> <cleared_by>
      consume <channel> <chat_key> <triggered_by> [<owner_text...>]
      status  <channel> <chat_key>
      format  <channel> <chat_key> [<owner_text...>]
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_cli_main.__doc__ or "")
        return 0
    sub = argv[0].lower()
    if sub == "add":
        if len(argv) < 6:
            print(json.dumps({"ok": False,
                              "error": "usage: add <channel> <chat_key> <from_uid> <from_role> <text...>"}))
            return 1
        text = " ".join(argv[5:])
        return _cli_add(argv[1], argv[2], argv[3], argv[4], text)
    if sub == "list":
        if len(argv) < 3:
            print(json.dumps({"ok": False, "error": "usage: list <channel> <chat_key>"}))
            return 1
        return _cli_list(argv[1], argv[2])
    if sub == "remove":
        if len(argv) < 5:
            print(json.dumps({"ok": False, "error": "usage: remove <channel> <chat_key> <prop_id> <removed_by>"}))
            return 1
        return _cli_remove(argv[1], argv[2], argv[3], argv[4])
    if sub == "clear":
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: clear <channel> <chat_key> <cleared_by>"}))
            return 1
        return _cli_clear(argv[1], argv[2], argv[3])
    if sub == "consume":
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: consume <channel> <chat_key> <triggered_by> [<owner_text...>]"}))
            return 1
        owner_text = " ".join(argv[4:]) if len(argv) >= 5 else ""
        return _cli_consume(argv[1], argv[2], argv[3], owner_text)
    if sub == "status":
        if len(argv) < 3:
            print(json.dumps({"ok": False, "error": "usage: status <channel> <chat_key>"}))
            return 1
        return _cli_status(argv[1], argv[2])
    if sub == "format":
        if len(argv) < 3:
            print(json.dumps({"ok": False, "error": "usage: format <channel> <chat_key> [<owner_text...>]"}))
            return 1
        owner_text = " ".join(argv[3:]) if len(argv) >= 4 else ""
        return _cli_format(argv[1], argv[2], owner_text)
    print(json.dumps({"ok": False, "error": f"unknown subcommand: {sub!r}"}))
    return 1


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
