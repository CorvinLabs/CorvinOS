"""roles.py — Layer 18: capability-bundle role system with delegation chain.

The bridge whitelist is binary (owner-or-deny). The read-only role (Layer 16
Phase 2) split that into "in the chat" vs "may trigger". This module is the
next step: it adds **capability bundles** that the owner — and admins
delegated by the owner — can grant to specific (chat, uid) pairs, with a TTL
and a full delegation chain in the audit log.

Four canonical bundles, intrinsic ordering high → low:

  * ``owner``     — implicit from the channel whitelist. Cannot be granted
                    or revoked through this module; owners change via direct
                    edits to ``settings.json``.
  * ``admin``     — may grant ``member`` and ``observer`` bundles, may pin
                    personas, may read the chat-wide audit. Cannot grant
                    ``admin`` (the "no kerze hotter than the flame" rule).
  * ``member``    — may trigger the bot, uses the standard tool set, may
                    pin personas only for the duration of their own turn.
                    Subject to the per-(chat, uid) quota that ships with
                    Phase 3.
  * ``observer``  — read-only with consent gate (Layer 16 Phase 4). The
                    bundle exists so the audit log carries an explicit
                    "I accepted being an observer" signal that pairs with
                    ``consent.granted``.

Three capability domains:

  * Triggering    — may the user invoke the bot at all?
  * Delegation    — may the user issue ``/grant`` for which target bundles?
  * Tooling       — which tool set unlocks for the user's turns?

Storage
-------
Single JSON file per (channel, chat) at::

    <corvin_home>/global/roles/<safe_channel>__<safe_chat>.json

::

    {
      "<uid>": {
        "bundle":      "admin",
        "granted_at":  1778204770.0,
        "expires_at":  1778809570.0,        # null for indefinite
        "granted_by":  "<owner_or_admin_uid>",
        "channel":     "discord",
        "via":         "slash",
        "reason":      "vacation cover"     # optional free text
      },
      ...
    }

Stale (expired) entries are pruned lazily on every read. Concurrent writes
use a ``.lock`` sidecar (POSIX ``flock``).

Audit
-----
Every grant / revoke / leave / lookup-denied emits a ``grant.*`` event into
the unified hash chain at ``<corvin_home>/global/forge/audit.jsonl`` —
the same chain that ``forge`` / ``skill-forge`` / ``path-gate`` /
``auth-elevation`` / ``consent`` use. One ``voice-audit verify`` covers
all of it.

Design notes
------------
* The slash-command UI lives in ``shared/js/in_chat_commands.js`` and
  shells out to this module via ``spawnSync('python3', ['roles.py', ...])``,
  the same pattern that ``consent.py`` uses.
* Owners are determined intrinsically from the channel whitelist — never
  written into the roles store. The ``classify()`` helper re-reads the
  channel's ``settings.json`` per call (TOCTOU consistency with the
  daemon's ``auth.js``).
* Path-gate (``operator/voice/hooks/path_gate.py``) is intended to protect
  the roles storage in a follow-up pass; until then the contract is
  "operator-only via slash-commands or this CLI".
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import re
import time
from pathlib import Path
from typing import Any

# ── Bundle catalog ────────────────────────────────────────────────────

# Ordering: higher index = more privileged. Used by ``can_grant`` to
# enforce the "kerze nicht heißer als die flamme" rule.
BUNDLES: tuple[str, ...] = ("observer", "member", "admin", "owner")

# Capability matrix. Each bundle inherits *only* its explicit set; there
# is no automatic inheritance to keep the audit story crystal clear.
CAPABILITIES: dict[str, frozenset[str]] = {
    "owner": frozenset({
        "trigger", "tools_all", "personas_pin",
        "delegate_admin", "delegate_member", "delegate_observer",
        "audit_chat", "audit_self",
        "forge_promote", "skill_promote", "elevate_pin",
        "revoke_any",
    }),
    "admin": frozenset({
        "trigger", "tools_all", "personas_pin",
        "delegate_member", "delegate_observer",
        "audit_chat", "audit_self",
        "revoke_subordinate",
    }),
    "member": frozenset({
        "trigger", "tools_basic", "personas_pin_turn",
        "audit_self",
    }),
    "observer": frozenset({
        "audit_self",
    }),
}

# What each bundle is allowed to grant. Mirrors the delegate_* capabilities
# above but in a directly-iterable form for the slash-command path.
GRANTABLE_BY: dict[str, frozenset[str]] = {
    "owner":    frozenset({"admin", "member", "observer"}),
    "admin":    frozenset({"member", "observer"}),
    "member":   frozenset(),
    "observer": frozenset(),
}

# Public TTL clamps. Operators can override via slash-command, but the
# parser caps at 30 days so a typo doesn't grant forever-rights.
MIN_TTL_S = 60                    # 1 minute
MAX_TTL_S = 30 * 24 * 60 * 60     # 30 days
DEFAULT_TTL_S = 7 * 24 * 60 * 60  # 7 days (the recommended UI default)

# Duration tokens accepted by parse_ttl(): 30s / 5m / 1h / 7d.
_TTL_TOKEN_RE = re.compile(r"^(\d+)([smhd])$", re.IGNORECASE)
_TTL_UNIT_S = {"s": 1, "m": 60, "h": 60 * 60, "d": 24 * 60 * 60}

# UID shape: alnum, dot, dash, underscore, plus, colon. Capped at 96 chars.
# Liberal enough to cover Telegram numeric IDs, Discord snowflakes,
# WhatsApp JIDs (`12345@s.whatsapp.net`), Slack U0..., and email-like ids.
_UID_RE = re.compile(r"^[A-Za-z0-9._\-+:@]{1,96}$")


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
    """Mirror of consent._safe_component / adapter._safe_id."""
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:64] or "anon"


def _store_path(channel: str, chat_key: str, *, tenant_id: str | None = None) -> Path:
    safe_channel = _safe_component(channel or "unknown")
    safe_chat = _safe_component(str(chat_key) if chat_key is not None else "anon")
    home = _corvin_home()
    if tenant_id is None:
        # ADR-0007 Phase 1.3: legacy path preserved for backward compat
        base = home / "global" / "roles"
    else:
        # Tenant-aware path — opt-in via explicit kwarg; Phase 1.4 will
        # flip default callers to tenants/_default/ via the migration helper.
        base = home / "tenants" / tenant_id / "global" / "roles"
    return base / f"{safe_channel}__{safe_chat}.json"


def _audit_path(*, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        return home / "global" / "forge" / "audit.jsonl"
    return home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


def _channel_settings_path(channel: str) -> Path:
    """The bridge channel's settings.json — used to read the whitelist for
    intrinsic owner classification. Mirrors the JS-side path resolution
    (one dir above shared/, then into <channel>/)."""
    here = Path(__file__).resolve().parent  # bridges/shared/
    return here.parent / channel / "settings.json"


# ── Audit emission (best-effort, mirrors consent / auth_elevation) ────

def _audit(event_type: str, *, channel: str, chat_key: str,
           details: dict[str, Any] | None = None,
           severity: str | None = None) -> None:
    """Best-effort audit write — silent on failure."""
    try:
        import sys
        here = Path(__file__).resolve()
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
    body: dict[str, Any] = {
        "channel": channel,
        "chat_key": chat_key,
    }
    if details:
        body.update(details)
    try:
        if severity:
            write_event(_audit_path(), event_type,
                        details=body, severity=severity)
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
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _prune(data: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """Drop expired entries. Returns (kept, expired_uids)."""
    now = time.time()
    kept: dict[str, dict] = {}
    expired: list[str] = []
    for uid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        exp = entry.get("expires_at")
        if isinstance(exp, (int, float)) and exp <= now:
            expired.append(uid)
            continue
        kept[uid] = entry
    return kept, expired


# ── Parsing helpers ────────────────────────────────────────────────────

def parse_ttl(arg: str | None) -> int | None:
    """Parse a TTL argument into seconds, or ``None`` for indefinite,
    or ``-1`` for invalid input.

    Accepts: ``30s`` / ``5m`` / ``1h`` / ``7d``, or the literal keywords
    ``never`` / ``forever`` / ``inf`` / ``infinite`` (→ None).
    """
    if arg is None:
        return None
    a = str(arg).strip().lower()
    if not a:
        return None
    if a in ("never", "forever", "inf", "infinite", "indefinite"):
        return None
    m = _TTL_TOKEN_RE.match(a)
    if not m:
        return -1
    n = int(m.group(1))
    unit = m.group(2).lower()
    secs = n * _TTL_UNIT_S[unit]
    if secs < MIN_TTL_S:
        return MIN_TTL_S
    if secs > MAX_TTL_S:
        return MAX_TTL_S
    return secs


def is_valid_uid(uid: str) -> bool:
    if not isinstance(uid, str):
        return False
    return bool(_UID_RE.match(uid))


def is_valid_bundle(bundle: str) -> bool:
    return bundle in BUNDLES


# ── Whitelist intrinsic-owner check ────────────────────────────────────

def _read_channel_whitelist(channel: str) -> list[str]:
    """Returns the channel's whitelist as a list of strings. Empty when
    the file is missing, malformed, or the field is absent."""
    p = _channel_settings_path(channel)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    wl = data.get("whitelist") if isinstance(data, dict) else None
    if not isinstance(wl, list):
        return []
    return [str(x) for x in wl if isinstance(x, (str, int))]


def is_intrinsic_owner(channel: str, uid: str) -> bool:
    """``uid`` is on the channel's whitelist => owner. Empty whitelist =
    DEV-mode (matches auth.js fail-open behaviour) — every uid is owner.
    """
    if not uid:
        return False
    wl = _read_channel_whitelist(channel)
    if not wl:
        return True   # DEV-mode parity with auth.js
    return uid in wl


# ── Public API ─────────────────────────────────────────────────────────

def effective_role(channel: str, chat_key: str, uid: str) -> str:
    """Return the most-privileged role currently held by ``uid`` in
    ``(channel, chat_key)``.

    Resolution order:
      1. Whitelist (intrinsic owner).
      2. Roles store, after lazy prune.
      3. ``"none"`` if neither.
    """
    if not uid:
        return "none"
    if is_intrinsic_owner(channel, uid):
        return "owner"
    path = _store_path(channel, chat_key)
    data, expired = _prune(_load_store(path))
    if expired:
        _save_store(path, data)
        for ex_uid in expired:
            _audit("grant.expired",
                   channel=channel, chat_key=str(chat_key),
                   details={"uid": ex_uid, "reason": "ttl-expired"})
    entry = data.get(uid)
    if not entry:
        return "none"
    bundle = entry.get("bundle")
    if bundle in BUNDLES and bundle != "owner":
        return bundle
    return "none"


def can(role: str, capability: str) -> bool:
    """True iff ``role`` has ``capability``."""
    return capability in CAPABILITIES.get(role, frozenset())


def can_grant(grantor_role: str, target_bundle: str) -> bool:
    """True iff a user holding ``grantor_role`` may grant ``target_bundle``
    to someone else. Owner cannot grant `owner` (whitelist-only); admins
    can never grant `admin` (the kerze rule)."""
    if target_bundle not in BUNDLES:
        return False
    if target_bundle == "owner":
        return False
    return target_bundle in GRANTABLE_BY.get(grantor_role, frozenset())


def grant(channel: str, chat_key: str, target_uid: str, *,
          bundle: str, granted_by: str,
          ttl_s: int | None = DEFAULT_TTL_S,
          reason: str = "",
          via: str = "slash") -> dict:
    """Grant ``bundle`` to ``target_uid`` in ``(channel, chat_key)``.

    Validates: target_uid shape, bundle name, grantor's authority, no
    self-grant, no owner-grant. Raises ``ValueError`` with a stable
    message tag on each failure (so the JS side can localise the user
    text without re-parsing English).

    Returns the stored entry dict on success and emits ``grant.issued``.
    """
    if not is_valid_uid(target_uid):
        raise ValueError("invalid-uid")
    if not is_valid_bundle(bundle):
        raise ValueError("invalid-bundle")
    if bundle == "owner":
        raise ValueError("owner-not-grantable")
    if not granted_by:
        raise ValueError("missing-grantor")
    if granted_by == target_uid:
        raise ValueError("self-grant")
    grantor_role = effective_role(channel, chat_key, granted_by)
    if not can_grant(grantor_role, bundle):
        # Audit the denial before raising — we want repeated attempts
        # to show up in the chain.
        _audit("grant.denied",
               channel=channel, chat_key=str(chat_key),
               details={
                   "grantor": granted_by,
                   "grantor_role": grantor_role,
                   "target": target_uid,
                   "bundle": bundle,
                   "reason": "insufficient-authority",
               },
               severity="WARNING")
        raise ValueError("insufficient-authority")

    now = time.time()
    if ttl_s is None:
        expires_at: float | None = None
    else:
        clamped = max(MIN_TTL_S, min(MAX_TTL_S, int(ttl_s)))
        expires_at = now + clamped

    entry = {
        "bundle": bundle,
        "granted_at": now,
        "expires_at": expires_at,
        "granted_by": granted_by,
        "channel": channel,
        "via": via,
        "reason": (reason or "").strip()[:200],
    }
    path = _store_path(channel, chat_key)
    data, _expired = _prune(_load_store(path))
    data[target_uid] = entry
    _save_store(path, data)
    _audit("grant.issued",
           channel=channel, chat_key=str(chat_key),
           details={
               "grantor": granted_by,
               "grantor_role": grantor_role,
               "target": target_uid,
               "bundle": bundle,
               "ttl_s": ttl_s if ttl_s else None,
               "expires_at": expires_at,
               "reason": entry["reason"],
               "via": via,
           })
    return entry


def revoke(channel: str, chat_key: str, target_uid: str, *,
           revoked_by: str, via: str = "slash") -> bool:
    """Drop the entry for ``target_uid``. Returns True iff something was
    actually removed.

    Authority rules:
      * Owner may revoke anyone (``revoke_any``).
      * Admin may revoke entries strictly *below* their own bundle —
        i.e. member and observer (``revoke_subordinate``). Cannot
        revoke another admin (no fratricide); the owner has to do that.
      * No-one else may revoke.

    Raises ``ValueError`` (``"insufficient-authority"`` or
    ``"cannot-revoke-peer"``) on policy violation; emits the matching
    ``grant.revoke_denied`` audit event before raising.
    """
    if not is_valid_uid(target_uid):
        raise ValueError("invalid-uid")
    if not revoked_by:
        raise ValueError("missing-revoker")
    revoker_role = effective_role(channel, chat_key, revoked_by)
    target_role = effective_role(channel, chat_key, target_uid)

    if revoker_role == "owner":
        # Owner may revoke any non-owner. Owners themselves can only be
        # demoted by editing the channel whitelist directly.
        if target_role == "owner":
            _audit("grant.revoke_denied",
                   channel=channel, chat_key=str(chat_key),
                   details={
                       "revoker": revoked_by,
                       "target": target_uid,
                       "reason": "owner-not-revocable",
                   },
                   severity="WARNING")
            raise ValueError("owner-not-revocable")
    elif revoker_role == "admin":
        if target_role not in ("member", "observer"):
            _audit("grant.revoke_denied",
                   channel=channel, chat_key=str(chat_key),
                   details={
                       "revoker": revoked_by,
                       "revoker_role": revoker_role,
                       "target": target_uid,
                       "target_role": target_role,
                       "reason": "admin-cannot-revoke-peer-or-up",
                   },
                   severity="WARNING")
            raise ValueError("cannot-revoke-peer")
    else:
        _audit("grant.revoke_denied",
               channel=channel, chat_key=str(chat_key),
               details={
                   "revoker": revoked_by,
                   "revoker_role": revoker_role,
                   "target": target_uid,
                   "reason": "insufficient-authority",
               },
               severity="WARNING")
        raise ValueError("insufficient-authority")

    path = _store_path(channel, chat_key)
    data, _expired = _prune(_load_store(path))
    existed = target_uid in data
    if existed:
        prior = data.pop(target_uid)
        _save_store(path, data)
        _audit("grant.revoked",
               channel=channel, chat_key=str(chat_key),
               details={
                   "revoker": revoked_by,
                   "revoker_role": revoker_role,
                   "target": target_uid,
                   "target_role": target_role,
                   "prior_bundle": prior.get("bundle"),
                   "via": via,
               })
    return existed


def leave(channel: str, chat_key: str, uid: str, *, via: str = "slash") -> dict:
    """Self-service: ``uid`` drops their own entry.

    Owners cannot ``/leave`` (they would lose their own self-revoke ability
    one second later). Returns ``{"ok": bool, "reason": str}`` so the JS
    side can render a precise message.
    """
    if not is_valid_uid(uid):
        return {"ok": False, "reason": "invalid-uid"}
    role = effective_role(channel, chat_key, uid)
    if role == "owner":
        return {"ok": False, "reason": "owner-cannot-leave"}
    if role == "none":
        return {"ok": False, "reason": "no-entry"}
    path = _store_path(channel, chat_key)
    data, _expired = _prune(_load_store(path))
    if uid not in data:
        return {"ok": False, "reason": "no-entry"}
    prior = data.pop(uid)
    _save_store(path, data)
    _audit("grant.left",
           channel=channel, chat_key=str(chat_key),
           details={
               "uid": uid,
               "prior_bundle": prior.get("bundle"),
               "via": via,
           })
    return {"ok": True, "reason": "left", "prior_bundle": prior.get("bundle")}


def status(channel: str, chat_key: str, uid: str) -> dict:
    """Detailed status for one uid (for ``/role <uid>``)."""
    role = effective_role(channel, chat_key, uid)
    path = _store_path(channel, chat_key)
    data, _expired = _prune(_load_store(path))
    entry = data.get(uid) or {}
    remaining = 0
    if isinstance(entry.get("expires_at"), (int, float)):
        remaining = max(0, int(entry["expires_at"] - time.time()))
    return {
        "channel": channel,
        "chat_key": chat_key,
        "uid": uid,
        "role": role,
        "intrinsic_owner": is_intrinsic_owner(channel, uid),
        "bundle": entry.get("bundle"),
        "granted_at": entry.get("granted_at"),
        "granted_by": entry.get("granted_by"),
        "expires_at": entry.get("expires_at"),
        "remaining_s": remaining,
        "reason": entry.get("reason"),
        "capabilities": sorted(CAPABILITIES.get(role, frozenset())),
    }


def list_roles(channel: str, chat_key: str) -> dict:
    """All entries currently in the store, plus the intrinsic owners
    derived from the channel whitelist. Returns
    ``{"intrinsic_owners": [...], "granted": {uid: entry, ...}}``."""
    wl = _read_channel_whitelist(channel)
    path = _store_path(channel, chat_key)
    data, _expired = _prune(_load_store(path))
    return {
        "channel": channel,
        "chat_key": chat_key,
        "intrinsic_owners": wl,
        "granted": data,
    }


# ── CLI (called by the JS slash-command handler via spawnSync) ─────────

def _human_ttl(seconds: int) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _cli_role(channel: str, chat_key: str, uid: str) -> int:
    print(json.dumps(status(channel, chat_key, uid),
                     ensure_ascii=False, indent=2))
    return 0


def _cli_roles(channel: str, chat_key: str) -> int:
    print(json.dumps(list_roles(channel, chat_key),
                     ensure_ascii=False, indent=2))
    return 0


def _cli_grant(channel: str, chat_key: str, target_uid: str,
               bundle: str, granted_by: str,
               ttl_arg: str | None, reason: str) -> int:
    ttl_s: int | None = DEFAULT_TTL_S
    if ttl_arg is not None:
        parsed = parse_ttl(ttl_arg)
        if parsed == -1:
            print(json.dumps({"ok": False,
                              "error": "invalid-ttl",
                              "hint": "use 30s / 5m / 1h / 7d / never"}))
            return 1
        ttl_s = parsed
    try:
        entry = grant(channel, chat_key, target_uid,
                      bundle=bundle, granted_by=granted_by,
                      ttl_s=ttl_s, reason=reason, via="cli")
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    out = {
        "ok": True,
        "target": target_uid,
        "bundle": entry["bundle"],
        "ttl_human": _human_ttl(int(entry["expires_at"] - entry["granted_at"]))
                     if entry.get("expires_at") else "indefinite",
        "expires_at": entry.get("expires_at"),
        "granted_by": entry.get("granted_by"),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


def _cli_revoke(channel: str, chat_key: str, target_uid: str,
                revoked_by: str) -> int:
    try:
        existed = revoke(channel, chat_key, target_uid,
                         revoked_by=revoked_by, via="cli")
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"ok": True, "existed": existed}))
    return 0


def _cli_leave(channel: str, chat_key: str, uid: str) -> int:
    result = leave(channel, chat_key, uid, via="cli")
    result.setdefault("ok", False)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def _cli_can(role: str, capability: str) -> int:
    print(json.dumps({"ok": True, "can": can(role, capability)}))
    return 0


def _cli_can_grant(grantor_role: str, target_bundle: str) -> int:
    print(json.dumps({"ok": True,
                      "can_grant": can_grant(grantor_role, target_bundle)}))
    return 0


def _cli_main(argv: list[str]) -> int:
    """Subcommands:

      role     <channel> <chat_key> <uid>
      roles    <channel> <chat_key>
      grant    <channel> <chat_key> <target_uid> <bundle> <granted_by> [<ttl>] [<reason...>]
      revoke   <channel> <chat_key> <target_uid> <revoked_by>
      leave    <channel> <chat_key> <uid>
      can      <role> <capability>           (helper for tests / JS)
      can-grant <grantor_role> <target_bundle>

    All output is JSON on stdout; exit code 0 = ok, 1 = parse / policy
    error. The JS caller relies on the JSON shape, not on the exit code,
    to render user-facing messages.
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_cli_main.__doc__ or "")
        return 0
    sub = argv[0].lower()
    if sub == "role":
        if len(argv) < 4:
            print(json.dumps({"ok": False,
                              "error": "usage: role <channel> <chat_key> <uid>"}))
            return 1
        return _cli_role(argv[1], argv[2], argv[3])
    if sub == "roles":
        if len(argv) < 3:
            print(json.dumps({"ok": False,
                              "error": "usage: roles <channel> <chat_key>"}))
            return 1
        return _cli_roles(argv[1], argv[2])
    if sub == "grant":
        if len(argv) < 6:
            print(json.dumps({"ok": False,
                              "error": "usage: grant <channel> <chat_key> <target_uid> <bundle> <granted_by> [<ttl>] [<reason...>]"}))
            return 1
        ttl_arg = argv[6] if len(argv) >= 7 else None
        reason = " ".join(argv[7:]) if len(argv) >= 8 else ""
        return _cli_grant(argv[1], argv[2], argv[3], argv[4], argv[5],
                          ttl_arg, reason)
    if sub == "revoke":
        if len(argv) < 5:
            print(json.dumps({"ok": False,
                              "error": "usage: revoke <channel> <chat_key> <target_uid> <revoked_by>"}))
            return 1
        return _cli_revoke(argv[1], argv[2], argv[3], argv[4])
    if sub == "leave":
        if len(argv) < 4:
            print(json.dumps({"ok": False,
                              "error": "usage: leave <channel> <chat_key> <uid>"}))
            return 1
        return _cli_leave(argv[1], argv[2], argv[3])
    if sub == "can":
        if len(argv) < 3:
            print(json.dumps({"ok": False,
                              "error": "usage: can <role> <capability>"}))
            return 1
        return _cli_can(argv[1], argv[2])
    if sub == "can-grant":
        if len(argv) < 3:
            print(json.dumps({"ok": False,
                              "error": "usage: can-grant <grantor_role> <target_bundle>"}))
            return 1
        return _cli_can_grant(argv[1], argv[2])
    print(json.dumps({"ok": False, "error": f"unknown subcommand: {sub!r}"}))
    return 1


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
