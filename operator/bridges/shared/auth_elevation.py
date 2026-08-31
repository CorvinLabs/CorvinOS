"""auth_elevation.py — short-lived PIN-gated capability for destructive ops.

The bridge whitelist is single-factor: anyone who controls the user's
Discord/Telegram/WhatsApp/Slack identity can talk to the bot. For the
common case that's fine — the bot serves its owner. For *destructive*
operations (forge_promote, skill_promote at user-scope, cowork-bind to
a more permissive persona, dangerous Bash) we want a second factor.

This module is the second factor. The user runs ``/auth-up <pin>``,
the PIN matches the bridge's ``pin`` field in settings.json, and an
*elevation* is granted for ``ttl_s`` seconds (default 600). While
elevated the chat may invoke gated MCP tools / accept gated hook
decisions; once the TTL expires (or the user runs ``/auth-down``)
the gate snaps shut again.

Storage
-------
Single JSON file at ``<corvin_home>/global/auth/elevation.json``::

    {
      "<chat_key>": {"expires_at": 1778205370.0, "granted_at": 1778204770.0,
                     "ttl_s": 600},
      ...
    }

Stale entries are pruned lazily on every read. Concurrent writes use a
``.lock`` sidecar (POSIX flock).

Audit
-----
Every grant / revoke / "needed but missing" emits a ``auth.*`` event
into the unified hash chain at ``<corvin_home>/global/forge/audit.jsonl``.
Same chain forge / skill-forge / path-gate use; one ``voice-audit verify``
covers all of it.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import threading as _threading
import time
import time as _time
from pathlib import Path
from typing import Any

DEFAULT_TTL_S = 600  # 10 minutes

# In-memory failed-attempt tracker. Keys: chat_key strings.
# Values: (count: int, window_start: float).
# Not persisted — restart resets (acceptable: clears in-flight state).
_pin_failures: dict[str, tuple[int, float]] = {}
_pin_lock = _threading.Lock()

_PIN_FAIL_THRESHOLD = 5      # attempts before lockout
_PIN_FAIL_WINDOW_S  = 60     # sliding window in seconds
_PIN_LOCKOUT_S      = 300    # lockout duration in seconds


def _record_pin_failure(chat_key: str) -> int:
    """Record a failed PIN attempt. Returns current fail count in window."""
    now = _time.monotonic()
    with _pin_lock:
        count, window_start = _pin_failures.get(chat_key, (0, now))
        if now - window_start > _PIN_FAIL_WINDOW_S:
            count, window_start = 0, now  # reset stale window
        count += 1
        _pin_failures[chat_key] = (count, window_start)
        return count


def _is_pin_locked_out(chat_key: str) -> bool:
    """True if chat_key has exceeded the PIN failure threshold recently."""
    now = _time.monotonic()
    with _pin_lock:
        count, window_start = _pin_failures.get(chat_key, (0, now))
        if now - window_start > _PIN_LOCKOUT_S:
            return False  # lockout expired
        return count >= _PIN_FAIL_THRESHOLD


def _clear_pin_failures(chat_key: str) -> None:
    """Clear failure state on successful PIN entry."""
    with _pin_lock:
        _pin_failures.pop(chat_key, None)


def _corvin_home() -> Path:
    """CORVIN_HOME canonical env var; on disk ``~/.corvin/`` default."""
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _store_path(*, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        return home / "global" / "auth" / "elevation.json"
    return home / "tenants" / tenant_id / "global" / "auth" / "elevation.json"


def _audit_path(*, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        return home / "global" / "forge" / "audit.jsonl"
    return home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


def _audit(event_type: str, *, channel: str, chat_key: str,
           details: dict[str, Any] | None = None) -> None:
    """Best-effort audit write — silent on failure."""
    try:
        # Make forge.security_events importable when called from the bridge.
        import sys
        here = Path(__file__).resolve().parent
        repo = None
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is not None:
            forge_pkg = repo / "operator" / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))
        from forge.security_events import write_event  # type: ignore
    except Exception:
        return
    body = {"channel": channel, "chat_key": chat_key}
    if details:
        body.update(details)
    try:
        write_event(_audit_path(), event_type, details=body)
    except Exception:
        pass


def _load_store() -> dict[str, dict]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_store(data: dict[str, dict]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(p.suffix + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, p)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _prune(data: dict[str, dict]) -> dict[str, dict]:
    now = time.time()
    return {k: v for k, v in data.items()
            if isinstance(v, dict) and v.get("expires_at", 0) > now}


def grant(*, chat_key: str, pin: str, settings_pin: str | None,
          ttl_s: int = DEFAULT_TTL_S, channel: str = "") -> tuple[bool, str]:
    """Grant elevation for ``chat_key`` if ``pin`` matches ``settings_pin``.

    Returns (ok, reason). ``ok=True`` writes the entry to the store and
    emits an ``auth.elevation_grant`` audit event. Wrong / missing PIN
    emits an ``auth.elevation_required`` warning event so brute-force
    attempts show up in the chain.
    """
    if _is_pin_locked_out(chat_key):
        _audit("auth.elevation_lockout",
               channel=channel, chat_key=chat_key,
               details={"reason": "too-many-failures", "lockout_s": _PIN_LOCKOUT_S})
        return False, "pin-lockout"
    if not settings_pin:
        _audit("auth.elevation_required",
               channel=channel, chat_key=chat_key,
               details={"reason": "no-pin-configured"})
        return False, "no-pin-configured"
    if not pin or pin != settings_pin:
        _audit("auth.elevation_required",
               channel=channel, chat_key=chat_key,
               details={"reason": "wrong-pin"})
        count = _record_pin_failure(chat_key)
        if count >= _PIN_FAIL_THRESHOLD:
            _audit("auth.elevation_lockout_started",
                   channel=channel, chat_key=chat_key,
                   details={"reason": "threshold-reached", "lockout_s": _PIN_LOCKOUT_S,
                            "fail_count": count})
        return False, "wrong-pin"

    now = time.time()
    data = _prune(_load_store())
    data[chat_key] = {
        "granted_at": now,
        "expires_at": now + ttl_s,
        "ttl_s": ttl_s,
    }
    _save_store(data)
    _clear_pin_failures(chat_key)
    _audit("auth.elevation_grant",
           channel=channel, chat_key=chat_key,
           details={"ttl_s": ttl_s})
    return True, "ok"


def revoke(*, chat_key: str, channel: str = "") -> bool:
    """Drop elevation for ``chat_key`` early. Returns True if an entry
    actually existed."""
    data = _prune(_load_store())
    existed = chat_key in data
    if existed:
        data.pop(chat_key, None)
        _save_store(data)
        _audit("auth.elevation_revoke",
               channel=channel, chat_key=chat_key, details={})
    return existed


def is_elevated(chat_key: str) -> bool:
    """True iff ``chat_key`` has an unexpired elevation entry."""
    if not chat_key:
        return False
    data = _prune(_load_store())
    entry = data.get(chat_key)
    if not isinstance(entry, dict):
        return False
    return entry.get("expires_at", 0) > time.time()


def remaining_ttl(chat_key: str) -> int:
    """Seconds left in the current elevation, or 0 if not elevated."""
    if not chat_key:
        return 0
    data = _prune(_load_store())
    entry = data.get(chat_key)
    if not isinstance(entry, dict):
        return 0
    delta = int(entry.get("expires_at", 0) - time.time())
    return max(0, delta)


# Tools that REQUIRE elevation. Pre-tool-hook checks this list.
ELEVATION_REQUIRED_TOOLS: tuple[str, ...] = (
    "mcp__forge__forge_promote",
    "mcp__skill_forge__skill_promote",
)


def needs_elevation(tool_name: str) -> bool:
    return tool_name in ELEVATION_REQUIRED_TOOLS
