"""ADR-0166 — Session Participation Gate (SPG).

Dynamic, ephemeral, owner-controlled participation layer for shared-chat
bridges (WhatsApp groups, Discord channels, etc.).

Default: private (only whitelisted owners can interact).
Runtime state: <session_dir>/spg_state.json (mode 0600, session-scoped).
GDPR: raw UIDs stored only in session file (purged on L8 reset).
      Audit chain entries use hashed prefixes only — never raw UIDs.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ── Audit integration (best-effort, never blocks the gate) ────────────────────
# MUST NOT import anthropic (CI AST lint enforces).
_HERE = Path(__file__).resolve().parent

try:
    _FORGE = _HERE.parents[1] / "forge"
    if _FORGE.is_dir() and str(_FORGE) not in sys.path:
        sys.path.insert(0, str(_FORGE))
    from forge.security_events import write_event as _write_audit  # type: ignore
except Exception:
    try:
        from security_events import write_event as _write_audit  # type: ignore
    except Exception:
        _write_audit = None  # type: ignore[assignment]

try:
    from audit import audit_path as _audit_path  # type: ignore
except Exception:
    _audit_path = None  # type: ignore[assignment]


def _emit(event_type: str, channel: str, chat_key: str, details: dict) -> None:
    """Write one SPG audit event to the L16 hash chain. Best-effort only."""
    if _write_audit is None:
        return
    try:
        if _audit_path is not None:
            path = _audit_path()
        else:
            corvin_home = Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))
            tenant_id = os.environ.get("CORVIN_TENANT_ID", "_default")
            path = corvin_home / "tenants" / tenant_id / "global" / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_audit(
            path, event_type,
            tool="",
            run_id="",
            details={"channel": channel, "chat_key": chat_key, **details},
            hash_chain=True,
        )
    except Exception:
        pass

# ── Constants ─────────────────────────────────────────────────────────────────

_STATE_FILE = "spg_state.json"
_MODES = {"private", "invited", "open"}
_DEFAULT_MODE = "private"
# 30 days max TTL
_MAX_TTL_S = 30 * 24 * 3600

# ── Helpers ───────────────────────────────────────────────────────────────────


def _uid_hash(uid: str) -> str:
    """8-char sha256 prefix of the raw uid — for audit chain only."""
    return hashlib.sha256(uid.encode()).hexdigest()[:8]


def _now() -> float:
    return time.time()


def _state_path(session_dir: Path) -> Path:
    return session_dir / _STATE_FILE


def _load(session_dir: Path) -> dict[str, Any]:
    """Load spg_state.json; return default private state if absent/corrupt.

    Does NOT prune expired entries — callers that need clean state call _prune().
    is_sender_allowed checks TTL directly so it can return "invitation_expired".
    """
    p = _state_path(session_dir)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_state()
        return data
    except Exception:
        return _default_state()


def _prune(state: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of state with expired invitations removed."""
    invs = state.get("invitations", {})
    now = _now()
    pruned = {
        uid: rec for uid, rec in invs.items()
        if isinstance(rec, dict)
        and (rec.get("expires_at") is None or rec["expires_at"] > now)
    }
    return {**state, "invitations": pruned}


def _default_state() -> dict[str, Any]:
    return {"mode": _DEFAULT_MODE, "invitations": {}}


def _save(session_dir: Path, state: dict[str, Any]) -> None:
    """Atomic write of spg_state.json with mode 0600. Prunes expired entries."""
    import tempfile  # noqa: PLC0415
    clean = _prune(state)
    session_dir.mkdir(parents=True, exist_ok=True)
    p = _state_path(session_dir)
    fd, tmp_name = tempfile.mkstemp(dir=session_dir, suffix=".tmp")
    try:
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(clean, indent=2))
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise



# ── Public gate API ────────────────────────────────────────────────────────────


def is_sender_allowed(session_dir: Path, sender: str) -> tuple[bool, str]:
    """Check whether a non-whitelisted sender is allowed by SPG.

    Returns (allowed: bool, reason: str).
    Raises nothing — caller must handle exceptions and fail-closed.
    """
    state = _load(session_dir)
    mode = state.get("mode", _DEFAULT_MODE)

    if mode == "open":
        return True, "open"

    if mode == "invited":
        invs = state.get("invitations", {})
        rec = invs.get(sender)
        if rec and isinstance(rec, dict):
            exp = rec.get("expires_at")
            if exp is not None and exp <= _now():
                return False, "invitation_expired"
            return True, "invited"
        return False, "not_invited"

    # mode == "private" or unknown
    return False, f"private:{mode}"


# ── Mutation API (called by CLI) ───────────────────────────────────────────────


def set_mode(session_dir: Path, mode: str) -> None:
    if mode not in _MODES:
        raise ValueError(f"Invalid mode: {mode!r}. Must be one of {sorted(_MODES)}")
    state = _load(session_dir)
    state["mode"] = mode
    _save(session_dir, state)


def add_guest(session_dir: Path, uid: str, ttl_s: float | None, granted_by: str) -> None:
    """Invite a guest. ttl_s=None means session lifetime (no expiry)."""
    state = _load(session_dir)
    if state.get("mode") == "private":
        state["mode"] = "invited"
    invitations = state.setdefault("invitations", {})
    expires_at = (_now() + min(ttl_s, _MAX_TTL_S)) if ttl_s is not None else None
    invitations[uid] = {
        "granted_by": granted_by,
        "granted_at": _now(),
        "expires_at": expires_at,
    }
    _save(session_dir, state)


def remove_guest(session_dir: Path, uid: str) -> bool:
    """Remove a guest invitation. Returns True if it existed."""
    state = _load(session_dir)
    invs = state.setdefault("invitations", {})
    existed = uid in invs
    if existed:
        del invs[uid]
    if not invs and state.get("mode") == "invited":
        state["mode"] = "private"
    _save(session_dir, state)
    return existed


def list_guests(session_dir: Path) -> dict[str, Any]:
    """Return a JSON-serialisable summary for /who."""
    state = _load(session_dir)
    mode = state.get("mode", _DEFAULT_MODE)
    invitations = state.get("invitations", {})
    now = _now()
    guests = []
    for uid, rec in invitations.items():
        exp = rec.get("expires_at") if isinstance(rec, dict) else None
        remaining = None
        if exp is not None:
            remaining = max(0, int(exp - now))
        guests.append({
            "uid": uid,
            "uid_hash": _uid_hash(uid),
            "granted_at": rec.get("granted_at") if isinstance(rec, dict) else None,
            "expires_at": exp,
            "remaining_s": remaining,
        })
    return {"mode": mode, "guests": guests, "guest_count": len(guests)}


# ── TTL parsing ────────────────────────────────────────────────────────────────

_TTL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_ttl(token: str) -> float | None:
    """Parse duration string like '5m', '1h', '24h', '7d'. Returns seconds.
    Returns None if token is empty or 'session'/'forever'/'null'.
    """
    if not token or token.lower() in ("session", "forever", "null", "none"):
        return None
    token = token.strip().lower()
    for unit, mult in _TTL_UNITS.items():
        if token.endswith(unit):
            try:
                return float(token[:-1]) * mult
            except ValueError:
                pass
    try:
        return float(token)
    except ValueError:
        raise ValueError(f"Cannot parse TTL: {token!r}. "
                         "Use e.g. '30s', '5m', '1h', '24h', '7d'.")


# ── Session dir resolver ──────────────────────────────────────────────────────


def _resolve_session_dir(channel: str, chat_key: str) -> Path:
    """Resolve per-chat session dir using the same logic as adapter._session_dir.

    Tries the tenant-aware path first; falls back to the legacy flat path.
    Never creates directories.
    """
    import re as _re  # noqa: PLC0415

    def _safe(s: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in s)[:64] or "anon"

    corvin_home = Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))
    tenant_id = os.environ.get("CORVIN_TENANT_ID", "_default")

    # Tenant-aware path: <corvin_home>/tenants/<tid>/sessions/<channel>/<safe_chat_key>
    tenant_path = (corvin_home / "tenants" / tenant_id
                   / "sessions" / channel / _safe(chat_key))
    if tenant_path.exists():
        return tenant_path

    # Voice bridge: <corvin_home>/tenants/<tid>/sessions/voice/<channel>/<safe_chat_key>
    voice_path = (corvin_home / "tenants" / tenant_id
                  / "sessions" / "voice" / channel / _safe(chat_key))
    if voice_path.exists():
        return voice_path

    # Legacy flat path: <corvin_home>/tenants/<tid>/sessions/<channel>:<safe_chat_key>
    legacy_path = (corvin_home / "tenants" / tenant_id
                   / "sessions" / f"{_safe(channel)}:{_safe(chat_key)}")
    if legacy_path.exists():
        return legacy_path

    # No existing dir found — return the voice path (will be created on first write)
    return voice_path


# ── CLI interface (called from in_chat_commands.js via spawnSync) ─────────────
# Usage (channel + chat_key form — preferred from JS):
#   spg.py set-mode   <channel> <chat_key> <mode>
#   spg.py add-guest  <channel> <chat_key> <uid> [<ttl>] [<granted_by>]
#   spg.py rm-guest   <channel> <chat_key> <uid>
#   spg.py list       <channel> <chat_key>
#   spg.py mode       <channel> <chat_key>
#   spg.py is-allowed <channel> <chat_key> <uid>
# All commands print JSON to stdout.


def _cli() -> None:
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "no command"}))
        sys.exit(1)

    cmd = args[0]
    if len(args) < 3:
        print(json.dumps({"error": "channel and chat_key required"}))
        sys.exit(1)

    channel = args[1]
    chat_key = args[2]
    session_dir = _resolve_session_dir(channel, chat_key)
    # Remaining args start at index 3
    extra = args[3:]

    try:
        if cmd == "set-mode":
            if not extra:
                print(json.dumps({"error": "mode required"}))
                sys.exit(1)
            mode = extra[0]
            if mode not in _MODES:
                print(json.dumps({"error": f"invalid mode: {mode!r}. "
                                  f"Must be one of {sorted(_MODES)}"}))
                sys.exit(1)
            # Optional <changed_by> uid (extra[1]) — hashed for the audit chain so
            # the GDPR Art.30 record attributes the mode change to the granting
            # owner, not the literal "cli" (security review 2026-06-27, F9).
            _changed_by = extra[1] if len(extra) > 1 else ""
            _emit("spg.mode_changed", channel, chat_key, {
                "mode": mode,
                "changed_by_hash": _uid_hash(_changed_by) if _changed_by else "cli",
                # EU AI Act Art. 50: /open must include disclosure card; record
                # in audit so forensic queries can confirm the obligation was met.
                "disclosure_sent": mode == "open",
            })
            set_mode(session_dir, mode)
            print(json.dumps({"ok": True, "mode": mode}))

        elif cmd == "add-guest":
            if not extra:
                print(json.dumps({"error": "uid required"}))
                sys.exit(1)
            uid = extra[0]
            # extra[1] is TTL ('' = session lifetime, from JS always-pass protocol).
            # extra[2] is grantor. Old callers that omit both still get safe defaults.
            _raw_ttl = extra[1] if len(extra) > 1 else ""
            ttl_s = parse_ttl(_raw_ttl) if _raw_ttl else None
            granted_by = extra[2] if len(extra) > 2 else "owner"
            _emit("spg.guest_invited", channel, chat_key, {
                "uid_hash": _uid_hash(uid),
                "ttl_s": ttl_s,
                "granted_by_hash": _uid_hash(granted_by),
            })
            add_guest(session_dir, uid, ttl_s, granted_by)
            print(json.dumps({
                "ok": True,
                "uid_hash": _uid_hash(uid),
                "ttl_s": ttl_s,
            }))

        elif cmd == "rm-guest":
            if not extra:
                print(json.dumps({"error": "uid required"}))
                sys.exit(1)
            uid = extra[0]
            # Peek to check existence before emitting — only record real removals
            _peek_state = _load(session_dir)
            _will_exist = uid in _peek_state.get("invitations", {})
            if _will_exist:
                _emit("spg.guest_removed", channel, chat_key, {
                    "uid_hash": _uid_hash(uid),
                })
            existed = remove_guest(session_dir, uid)
            print(json.dumps({"ok": True, "existed": existed,
                               "uid_hash": _uid_hash(uid)}))

        elif cmd == "list":
            result = list_guests(session_dir)
            print(json.dumps(result))

        elif cmd == "mode":
            state = _load(session_dir)
            print(json.dumps({"mode": state.get("mode", _DEFAULT_MODE)}))

        elif cmd == "is-allowed":
            if not extra:
                print(json.dumps({"error": "uid required"}))
                sys.exit(1)
            sender = extra[0]
            try:
                allowed, reason = is_sender_allowed(session_dir, sender)
            except Exception as e:
                print(json.dumps({"allowed": False, "reason": "error", "error": str(e)[:120]}))
                sys.exit(0)
            print(json.dumps({"allowed": allowed, "reason": reason}))

        else:
            print(json.dumps({"error": f"unknown command: {cmd!r}"}))
            sys.exit(1)

    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    _cli()
