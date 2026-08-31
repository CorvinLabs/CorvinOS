"""quota.py — Layer 20: per-(chat, uid) message + token quotas.

Layer 18 (roles) lets the owner delegate trigger-rights to non-whitelist
users. That delegation needs a budget — otherwise a delegated member can
silently burn the owner's LLM-spend, hit a rate-limit-induced outage, or
DOS the bot through volume. This module is the budget layer.

Two countable quantities per (channel, chat, uid):

  * **messages**   — count of bridge turns the user triggered today.
  * **tokens**     — sum of completion tokens used by those turns.

Both reset rolling-24-hour from the user's first counted action of the
period (`day_anchor`). The day-anchor mechanism avoids the wall-clock-
midnight cliff: a user who only ever uses the bot in the evening has
their window roll forward smoothly instead of resetting at 00:00 local.

Per-bundle defaults (override per (channel, chat, uid) via `set_limit`):

| Bundle    | Messages/day | Tokens/day |
|-----------|--------------|------------|
| owner     | unlimited    | unlimited  |
| admin     | 500          | 100,000    |
| member    | 100          | 20,000     |
| observer  | 0 (no-trigger; observers don't reach the gate) |

Storage
-------
Single JSON file per (channel, chat) at
``<corvin_home>/global/quota/<safe_channel>__<safe_chat>.json``::

    {
      "<uid>": {
        "messages":     17,
        "tokens":       4321,
        "day_anchor":   1778204770.0,
        "limit_msgs":   100,        # null → use bundle default
        "limit_tokens": 20000,      # null → use bundle default
        "channel":      "telegram"
      },
      ...
    }

Audit
-----
``quota.over_limit`` (WARNING) when ``check`` blocks; ``quota.recorded``
(INFO, on every ``record``); ``quota.reset`` (INFO) on operator reset
or rolling window roll-over. All events land in the unified hash chain
at ``<corvin_home>/global/forge/audit.jsonl``.

Design notes
------------
* The check / record split mirrors the rate-limiter pattern: the daemon
  asks "may I admit this?" (``check``) and only after the bridge run
  succeeds does it write the result back via ``record``. Failed runs do
  not consume budget — the user shouldn't pay for our bugs.

* Owner is a hard bypass: ``check`` and ``record`` short-circuit when
  ``effective_role`` returns ``owner``. The store entry is still written
  so the owner can inspect their own usage via ``/quota`` — limits are
  reported as ``unlimited``.

* Limits are NOT enforced for ``observer`` because observers cannot
  trigger the bot in the first place (Layer 16 Phase 4 consent gate
  filters them out before quota is consulted). A defensive check
  (defence-in-depth) returns ``allowed=False`` regardless.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

# ADR-0052 F7 — per-(channel, uid) quota lock.
# Serialises check() and record() for the same (channel, uid) pair to close
# the race where two concurrent requests both pass check() before either
# calls record(). In-process only (best-effort across processes); restarts
# clear the dict, which is acceptable — the window re-opens briefly but no
# budget is permanently leaked.
_QUOTA_LOCK_TIMEOUT_S = 5.0   # 5 s guard — prevents deadlock DoS
_quota_locks: dict[str, threading.Lock] = {}
_quota_locks_meta: threading.Lock = threading.Lock()


def _quota_lock(channel: str, uid: str) -> threading.Lock:
    """Return the per-(channel, uid) lock, creating it if needed."""
    key = f"{channel}\x00{uid}"
    with _quota_locks_meta:
        if key not in _quota_locks:
            _quota_locks[key] = threading.Lock()
        return _quota_locks[key]


def _quota_lock_acquire(channel: str, uid: str) -> threading.Lock | None:
    """Acquire the quota lock for (channel, uid) with a timeout.

    Returns the locked Lock on success, or None on timeout (caller should
    proceed without the lock — emit audit warning, but do not deadlock).
    """
    lock = _quota_lock(channel, uid)
    acquired = lock.acquire(timeout=_QUOTA_LOCK_TIMEOUT_S)
    if not acquired:
        _audit("quota.lock_timeout",
               channel=channel, chat_key="", uid=uid,
               details={"timeout_s": _QUOTA_LOCK_TIMEOUT_S},
               severity="WARNING")
        return None
    return lock

# Per-bundle defaults. ``None`` means unlimited.
DEFAULT_LIMITS: dict[str, dict[str, int | None]] = {
    "owner":    {"messages": None,  "tokens": None},
    "admin":    {"messages": 500,   "tokens": 100_000},
    "member":   {"messages": 100,   "tokens": 20_000},
    "observer": {"messages": 0,     "tokens": 0},
    "none":     {"messages": 0,     "tokens": 0},
}

WINDOW_S = 24 * 60 * 60   # 24 hours rolling


# ── Path resolution ────────────────────────────────────────────────────

def _corvin_home() -> Path:
    """Resolve CORVIN_HOME: env var first, then repo-relative .corvin, then ~/.corvin."""
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _safe_component(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:64] or "anon"


def _store_path(channel: str, chat_key: str, *, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        base = home / "global" / "quota"
    else:
        base = home / "tenants" / tenant_id / "global" / "quota"
    return (base / f"{_safe_component(channel or 'unknown')}__"
            f"{_safe_component(str(chat_key) if chat_key is not None else 'anon')}.json")


def _audit_path(*, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        return home / "global" / "forge" / "audit.jsonl"
    return home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


# ── Audit helper ───────────────────────────────────────────────────────

def _audit(event_type: str, *, channel: str, chat_key: str, uid: str,
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
    body: dict[str, Any] = {
        "channel": channel, "chat_key": chat_key, "uid": uid,
    }
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

def _load_store(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_store(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tmp = path.with_suffix(path.suffix + ".tmp")
        # Create with 0o600 before writing — avoids world-readable window.
        tmp_fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(tmp_fd, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())
        finally:
            os.close(tmp_fd)
        os.replace(tmp, path)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ── Window roll-over (lazy) ────────────────────────────────────────────

def _maybe_roll(entry: dict, now: float, *,
                channel: str, chat_key: str, uid: str) -> dict:
    """If ``entry``'s day_anchor is older than WINDOW_S, reset the
    counters and update the anchor. Emits ``quota.reset`` so the
    operator sees rollovers in the chain."""
    anchor = entry.get("day_anchor", 0) or 0
    if now - anchor < WINDOW_S:
        return entry
    prior = {
        "messages": entry.get("messages", 0),
        "tokens": entry.get("tokens", 0),
    }
    entry["messages"] = 0
    entry["tokens"] = 0
    entry["day_anchor"] = now
    if prior["messages"] or prior["tokens"]:
        _audit("quota.reset",
               channel=channel, chat_key=str(chat_key), uid=uid,
               details={"reason": "window-rollover", "prior": prior})
    return entry


# ── Effective limits resolution ────────────────────────────────────────

def _effective_limits(channel: str, chat_key: str, uid: str,
                      role: str, entry: dict) -> tuple[int | None, int | None]:
    """Return (limit_msgs, limit_tokens) for ``uid``.

    Per-(chat, uid) override (in the entry) > bundle default
    (DEFAULT_LIMITS[role]). ``None`` means unlimited.
    """
    bundle_default = DEFAULT_LIMITS.get(role, DEFAULT_LIMITS["none"])
    msg_override = entry.get("limit_msgs")
    tok_override = entry.get("limit_tokens")
    return (
        msg_override if msg_override is not None else bundle_default["messages"],
        tok_override if tok_override is not None else bundle_default["tokens"],
    )


# ── Public API ─────────────────────────────────────────────────────────

def _resolve_role(channel: str, chat_key: str, uid: str) -> str:
    """Best-effort role lookup via the roles module. Returns 'none' if
    the module is unavailable (test isolation, missing forge install)."""
    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import roles  # type: ignore
        return roles.effective_role(channel, chat_key, uid)
    except Exception:
        return "none"


def check(channel: str, chat_key: str, uid: str, *,
          role: str | None = None, tokens: int = 0) -> dict:
    """Pre-flight: may ``uid`` send another message of ``tokens`` size?

    Returns ``{"allowed": bool, "reason": str, "limit_msgs": int|None,
    "limit_tokens": int|None, "remaining_msgs": int|None,
    "remaining_tokens": int|None, "role": str}``.

    Owner short-circuits to ``allowed=True`` regardless of counters.
    Observer short-circuits to ``allowed=False`` (defensive — observers
    never reach trigger anyway). All other roles consult the entry.
    """
    if role is None:
        role = _resolve_role(channel, chat_key, uid)

    if role == "owner":
        return {
            "allowed": True, "reason": "owner-bypass",
            "limit_msgs": None, "limit_tokens": None,
            "remaining_msgs": None, "remaining_tokens": None,
            "role": role,
        }
    if role == "observer" or role == "none":
        return {
            "allowed": False, "reason": "no-trigger-role",
            "limit_msgs": 0, "limit_tokens": 0,
            "remaining_msgs": 0, "remaining_tokens": 0,
            "role": role,
        }

    now = time.time()
    path = _store_path(channel, chat_key)
    data = _load_store(path)
    entry = data.get(uid, {})
    entry = _maybe_roll(entry, now, channel=channel,
                        chat_key=chat_key, uid=uid)
    limit_msgs, limit_tokens = _effective_limits(channel, chat_key, uid,
                                                  role, entry)
    msg_count = entry.get("messages", 0) or 0
    tok_count = entry.get("tokens", 0) or 0

    if limit_msgs is not None and msg_count + 1 > limit_msgs:
        _audit("quota.over_limit",
               channel=channel, chat_key=str(chat_key), uid=uid,
               details={"role": role, "metric": "messages",
                        "count": msg_count, "limit": limit_msgs},
               severity="WARNING")
        return {
            "allowed": False, "reason": "messages-exceeded",
            "limit_msgs": limit_msgs, "limit_tokens": limit_tokens,
            "remaining_msgs": 0,
            "remaining_tokens": (limit_tokens - tok_count) if limit_tokens is not None else None,
            "role": role,
        }
    if limit_tokens is not None and tok_count + tokens > limit_tokens:
        _audit("quota.over_limit",
               channel=channel, chat_key=str(chat_key), uid=uid,
               details={"role": role, "metric": "tokens",
                        "count": tok_count, "request": tokens,
                        "limit": limit_tokens},
               severity="WARNING")
        return {
            "allowed": False, "reason": "tokens-exceeded",
            "limit_msgs": limit_msgs, "limit_tokens": limit_tokens,
            "remaining_msgs": (limit_msgs - msg_count) if limit_msgs is not None else None,
            "remaining_tokens": 0,
            "role": role,
        }

    return {
        "allowed": True, "reason": "ok",
        "limit_msgs": limit_msgs, "limit_tokens": limit_tokens,
        "remaining_msgs": (limit_msgs - msg_count) if limit_msgs is not None else None,
        "remaining_tokens": (limit_tokens - tok_count) if limit_tokens is not None else None,
        "role": role,
    }


def record(channel: str, chat_key: str, uid: str, *,
           role: str | None = None, tokens: int = 0) -> dict:
    """Post-flight: register that a successful turn happened.

    Owners are still recorded (so ``/quota`` works for them) but no
    audit event is written. Observers / none short-circuit — they
    never had a trigger, so they have nothing to record.

    ADR-0052 F7: holds the per-(channel, uid) lock during the entire
    read-modify-write to close the TOCTOU race where two concurrent
    requests both pass check() and then both record.
    """
    if role is None:
        role = _resolve_role(channel, chat_key, uid)

    if role in ("observer", "none"):
        # Defensive: don't account against observers.
        return {"recorded": False, "reason": "no-trigger-role", "role": role}

    # ADR-0052 F7: acquire lock before read so concurrent check()+record()
    # pairs serialize their read-modify-write correctly.
    lock = _quota_lock_acquire(channel, uid)
    try:
        now = time.time()
        path = _store_path(channel, chat_key)
        data = _load_store(path)
        entry = data.get(uid, {})
        entry = _maybe_roll(entry, now, channel=channel,
                            chat_key=chat_key, uid=uid)
        if "day_anchor" not in entry:
            entry["day_anchor"] = now
        if "channel" not in entry:
            entry["channel"] = channel

        entry["messages"] = (entry.get("messages", 0) or 0) + 1
        entry["tokens"] = (entry.get("tokens", 0) or 0) + max(0, int(tokens))
        data[uid] = entry
        _save_store(path, data)
    finally:
        if lock is not None:
            lock.release()

    if role != "owner":
        _audit("quota.recorded",
               channel=channel, chat_key=str(chat_key), uid=uid,
               details={
                   "role": role, "tokens": tokens,
                   "messages_total": entry["messages"],
                   "tokens_total": entry["tokens"],
               })
    return {
        "recorded": True, "role": role,
        "messages_total": entry["messages"],
        "tokens_total": entry["tokens"],
    }


def get_usage(channel: str, chat_key: str, uid: str, *,
              role: str | None = None) -> dict:
    """Detailed usage status for one uid (powers ``/quota``).

    Persists rolled-over state before audit emission to close audit-reality
    drift: when _maybe_roll() emits quota.reset, the rolled values must be
    on-disk so other processes see consistent state.
    """
    if role is None:
        role = _resolve_role(channel, chat_key, uid)
    now = time.time()
    path = _store_path(channel, chat_key)
    data = _load_store(path)
    entry = data.get(uid, {})
    prior_anchor = entry.get("day_anchor")
    entry = _maybe_roll(entry, now, channel=channel,
                        chat_key=chat_key, uid=uid)
    # Persist rolled-over state before audit emission (which happens inside _maybe_roll).
    if entry.get("day_anchor") != prior_anchor:
        data[uid] = entry
        _save_store(path, data)
    limit_msgs, limit_tokens = _effective_limits(channel, chat_key, uid,
                                                  role, entry)
    return {
        "channel": channel, "chat_key": chat_key, "uid": uid,
        "role": role,
        "messages_today": entry.get("messages", 0) or 0,
        "tokens_today": entry.get("tokens", 0) or 0,
        "limit_msgs": limit_msgs,
        "limit_tokens": limit_tokens,
        "remaining_msgs": (limit_msgs - (entry.get("messages", 0) or 0))
                          if limit_msgs is not None else None,
        "remaining_tokens": (limit_tokens - (entry.get("tokens", 0) or 0))
                            if limit_tokens is not None else None,
        "day_anchor": entry.get("day_anchor"),
        "window_remaining_s": int(WINDOW_S - (now - (entry.get("day_anchor") or now))),
        "limit_msgs_overridden": entry.get("limit_msgs") is not None,
        "limit_tokens_overridden": entry.get("limit_tokens") is not None,
    }


def set_limit(channel: str, chat_key: str, uid: str, *,
              limit_msgs: int | None = None,
              limit_tokens: int | None = None,
              set_by: str = "") -> dict:
    """Operator override of the per-bundle default for ``uid``.

    Pass ``None`` for either field to KEEP the current override / fall
    back to bundle default (no change). Pass ``-1`` to clear the
    override and revert to the bundle default.

    ADR-0052 F7: holds the per-(channel, uid) lock during the entire
    read-modify-write to close the race where set_limit and record
    run concurrently.
    """
    lock = _quota_lock_acquire(channel, uid)
    try:
        path = _store_path(channel, chat_key)
        data = _load_store(path)
        entry = data.get(uid, {})
        if "day_anchor" not in entry:
            entry["day_anchor"] = time.time()
        if "channel" not in entry:
            entry["channel"] = channel

        if limit_msgs is not None:
            if limit_msgs == -1:
                entry.pop("limit_msgs", None)
            else:
                entry["limit_msgs"] = max(0, int(limit_msgs))
        if limit_tokens is not None:
            if limit_tokens == -1:
                entry.pop("limit_tokens", None)
            else:
                entry["limit_tokens"] = max(0, int(limit_tokens))

        data[uid] = entry
        _save_store(path, data)
    finally:
        if lock is not None:
            lock.release()

    _audit("quota.set_limit",
           channel=channel, chat_key=str(chat_key), uid=uid,
           details={
               "set_by": set_by,
               "limit_msgs": entry.get("limit_msgs"),
               "limit_tokens": entry.get("limit_tokens"),
           })
    return entry


def reset(channel: str, chat_key: str, uid: str, *,
          reset_by: str = "") -> bool:
    """Force-reset the rolling-window counters for ``uid``.

    Returns True iff an entry actually existed.

    ADR-0052 F7: holds the per-(channel, uid) lock during the entire
    read-modify-write to close the race where reset and record
    run concurrently.
    """
    lock = _quota_lock_acquire(channel, uid)
    try:
        path = _store_path(channel, chat_key)
        data = _load_store(path)
        if uid not in data:
            return False
        prior = {
            "messages": data[uid].get("messages", 0),
            "tokens": data[uid].get("tokens", 0),
        }
        data[uid]["messages"] = 0
        data[uid]["tokens"] = 0
        data[uid]["day_anchor"] = time.time()
        _save_store(path, data)
    finally:
        if lock is not None:
            lock.release()

    _audit("quota.reset",
           channel=channel, chat_key=str(chat_key), uid=uid,
           details={"reason": "operator-reset", "reset_by": reset_by,
                    "prior": prior})
    return True


def list_usage(channel: str, chat_key: str) -> dict:
    """All entries currently in the store. Powers `/quota all` for
    owner / admin views."""
    return {
        "channel": channel, "chat_key": chat_key,
        "entries": _load_store(_store_path(channel, chat_key)),
    }


# ── CLI ────────────────────────────────────────────────────────────────

def _cli_check(channel: str, chat_key: str, uid: str, tokens: str) -> int:
    print(json.dumps(check(channel, chat_key, uid, tokens=int(tokens or 0)),
                     ensure_ascii=False))
    return 0


def _cli_record(channel: str, chat_key: str, uid: str, tokens: str) -> int:
    print(json.dumps(record(channel, chat_key, uid, tokens=int(tokens or 0)),
                     ensure_ascii=False))
    return 0


def _cli_usage(channel: str, chat_key: str, uid: str) -> int:
    print(json.dumps(get_usage(channel, chat_key, uid),
                     ensure_ascii=False, indent=2))
    return 0


def _cli_set(channel: str, chat_key: str, uid: str,
             msgs: str, tokens: str, set_by: str) -> int:
    def _parse_or_none(s):
        if s == "" or s == "keep":
            return None
        if s == "clear" or s == "default":
            return -1
        return int(s)
    e = set_limit(channel, chat_key, uid,
                  limit_msgs=_parse_or_none(msgs),
                  limit_tokens=_parse_or_none(tokens),
                  set_by=set_by)
    print(json.dumps({"ok": True, "entry": e}, ensure_ascii=False))
    return 0


def _cli_reset(channel: str, chat_key: str, uid: str,
               reset_by: str) -> int:
    existed = reset(channel, chat_key, uid, reset_by=reset_by)
    print(json.dumps({"ok": True, "existed": existed}))
    return 0


def _cli_list(channel: str, chat_key: str) -> int:
    print(json.dumps(list_usage(channel, chat_key),
                     ensure_ascii=False, indent=2))
    return 0


def _cli_main(argv: list[str]) -> int:
    """Subcommands:

      check  <channel> <chat_key> <uid> [<tokens>]
      record <channel> <chat_key> <uid> [<tokens>]
      usage  <channel> <chat_key> <uid>
      set    <channel> <chat_key> <uid> <msgs|keep|clear> <tokens|keep|clear> [<set_by>]
      reset  <channel> <chat_key> <uid> [<reset_by>]
      list   <channel> <chat_key>
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_cli_main.__doc__ or "")
        return 0
    sub = argv[0].lower()
    if sub == "check":
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: check <channel> <chat_key> <uid> [<tokens>]"}))
            return 1
        return _cli_check(argv[1], argv[2], argv[3], argv[4] if len(argv) >= 5 else "0")
    if sub == "record":
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: record <channel> <chat_key> <uid> [<tokens>]"}))
            return 1
        return _cli_record(argv[1], argv[2], argv[3], argv[4] if len(argv) >= 5 else "0")
    if sub == "usage":
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: usage <channel> <chat_key> <uid>"}))
            return 1
        return _cli_usage(argv[1], argv[2], argv[3])
    if sub == "set":
        if len(argv) < 6:
            print(json.dumps({"ok": False, "error": "usage: set <channel> <chat_key> <uid> <msgs|keep|clear> <tokens|keep|clear> [<set_by>]"}))
            return 1
        set_by = argv[6] if len(argv) >= 7 else ""
        return _cli_set(argv[1], argv[2], argv[3], argv[4], argv[5], set_by)
    if sub == "reset":
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: reset <channel> <chat_key> <uid> [<reset_by>]"}))
            return 1
        reset_by = argv[4] if len(argv) >= 5 else ""
        return _cli_reset(argv[1], argv[2], argv[3], reset_by)
    if sub == "list":
        if len(argv) < 3:
            print(json.dumps({"ok": False, "error": "usage: list <channel> <chat_key>"}))
            return 1
        return _cli_list(argv[1], argv[2])
    print(json.dumps({"ok": False, "error": f"unknown subcommand: {sub!r}"}))
    return 1


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
