"""proactive_consent.py — Phase 0.5: proactive-contact consent primitive.

**Purpose-separated from Layer-17 ``consent.py`` (GDPR Art. 6/7 Zweckbindung).**

``consent.py`` is the INBOUND observer-transcript gate: it decides whether a
read-only sender's *incoming* messages may be buffered into the owner's next
LLM turn. That is a *different legal purpose* from the one this module serves.

This module is the OUTBOUND gate: it decides whether the bot may **proactively
contact** a user out-of-band — task-progress pings, completion notifications,
scheduled nudges, and any future proactive-contact-layer (PCL) envelope. A
grant here says nothing about inbound transcript consent, and an inbound
``consent.py`` grant says nothing about proactive contact. The two stores are
deliberately never aliased, cross-read, or merged (purpose limitation).

Model
-----
* **deny-by-default** — a non-owner uid with no grant record is NOT contactable.
* **Owner carve-out** — the intrinsic owner (``disclosure._is_intrinsic_owner``)
  is always contactable; no record is needed and none is written. The owner is
  the operator of their own install; proactive contact to themselves needs no
  opt-in.
* **hard-kill revoke** — ``revoke`` removes the grant AND best-effort purges any
  still-pending *proactive* envelopes for that uid from the outbox, so a user
  who says "stop" does not receive an already-queued ping. Normal reply
  envelopes are never touched.

Scope: keyed on ``(tenant_id, channel, uid)``. ``tenant_id`` is REQUIRED and
explicit — there is no env-var fallback (ADR-0007 console-routing rule: the
writer here and any future reader must resolve to the SAME tenant store).

Storage
-------
One JSON file per (tenant_id, channel) at::

    <corvin_home>/tenants/<tenant_id>/global/proactive_consent/<safe_channel>.json

::

    {
      "<uid>": {"granted_at": 1778204770.0, "channel": "discord",
                "granted_via": "slash"}
    }

Audit
-----
Every grant / revoke emits a ``proactive_consent.*`` event into the unified
hash chain. Best-effort, never raises.

Everything in this module is **never-raise**: a read failure denies (except the
owner carve-out, which still grants), a write failure is logged and swallowed.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op on Windows
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("corvin.proactive_consent")

# Proactive-envelope markers. An envelope carrying ANY of these is a proactive
# outbound message (task-progress / completion / future PCL) and is subject to
# hard-kill purge on revoke. A normal reply envelope carries none of these and
# is NEVER purged.
PROACTIVE_ENVELOPE_MARKERS = (
    "_task_progress",       # task_progress.py
    "_completion_notify",   # completion_notify.py
    "_proactive_contact",   # future proactive-contact-layer (PCL) field
)


# ── Path resolution (tenant-scoped, NO env fallback for tenant_id) ────────

def _corvin_home() -> Path:
    """Locate the runtime root. Mirrors consent.py / disclosure.py.

    Only CORVIN_HOME may relocate the *home*; the tenant axis itself never
    reads the env here — callers pass tenant_id explicitly.
    """
    env = os.environ.get("CORVIN_HOME")
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
    """Filesystem-safe path component. A component longer than 64 chars is HASHED
    (prefix + sha1[:12]) rather than truncated, so two DISTINCT long ids sharing
    a 64-char prefix never collapse to the SAME consent store path (which would
    cross-contaminate their grants). Never raises."""
    import hashlib
    raw = "".join(ch if ch.isalnum() else "_" for ch in str(s))
    if not raw:
        return "anon"
    if len(raw) <= 64:
        return raw
    return raw[:51] + "_" + hashlib.sha1(str(s).encode("utf-8")).hexdigest()[:12]


def _store_path(tenant_id: str, channel: str) -> Path:
    """Per-(tenant, channel) grant store. tenant_id is REQUIRED (no fallback)."""
    safe_tenant = _safe_component(tenant_id or "_default")
    safe_channel = _safe_component(channel or "unknown")
    home = _corvin_home()
    base = home / "tenants" / safe_tenant / "global" / "proactive_consent"
    return base / f"{safe_channel}.json"


def _audit_path(tenant_id: str) -> Path:
    safe_tenant = _safe_component(tenant_id or "_default")
    return _corvin_home() / "tenants" / safe_tenant / "global" / "forge" / "audit.jsonl"


def _default_outbox_dir() -> Path:
    """The shared outbox every messenger daemon polls.

    Matches ``operator/bridges/shared/outbox`` (bg_monitor.py:103).
    """
    return Path(__file__).resolve().parent / "outbox"


# ── Owner carve-out (delegates to disclosure, the existing owner check) ───

def _is_owner(channel: str, uid: str) -> bool:
    """True iff uid is the intrinsic owner of this channel — FAIL-CLOSED for the
    proactive carve-out.

    ``disclosure._is_intrinsic_owner`` fails OPEN when no channel whitelist is
    configured: it returns True for EVERY uid (DEV-mode parity with auth.js).
    That is safe for the inbound gate but WRONG for an OUTBOUND proactive-contact
    carve-out — it would auto-grant proactive contact to any uid on a fresh
    install with no whitelist, silently defeating deny-by-default. So the
    carve-out applies ONLY when a whitelist actually EXISTS and lists ``uid``;
    with no whitelist there is no owner, deny-by-default holds, and an explicit
    grant is required. Never raises."""
    if not uid:
        return False
    try:
        import sys as _sys
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
        import disclosure  # type: ignore
        # No whitelist → no owner carve-out for proactive (fail-closed).
        if not disclosure._read_channel_whitelist(channel):
            return False
        return bool(disclosure._is_intrinsic_owner(channel, uid))
    except Exception as exc:  # noqa: BLE001 — deny-by-default on failure
        _LOG.warning("owner check failed for %s/%s: %s", channel, uid, exc)
        return False


# ── Audit (best-effort, never-raise; mirrors consent/disclosure) ──────────

def _uid_hash(uid: str) -> str:
    import hashlib
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8]


def _audit(event_type: str, *, tenant_id: str, channel: str, uid: str,
           details: dict[str, Any] | None = None) -> None:
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
        "tenant_id": tenant_id,
        "channel": channel,
        "uid_hash": _uid_hash(uid) if uid else "",
    }
    if details:
        body.update(details)
    try:
        write_event(_audit_path(tenant_id), event_type, details=body)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("audit write failed for %s: %s", event_type, exc)


# ── Store I/O (atomic, never-raise) ───────────────────────────────────────

def _load_store(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_store(path: Path, data: dict[str, dict]) -> bool:
    """Atomic write under a flock sidecar. Returns True on success; never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(path.suffix + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp_fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(tmp_fd, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())
            finally:
                os.close(tmp_fd)
            os.replace(tmp, path)
            return True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except OSError as exc:
        _LOG.warning("proactive_consent store write failed: %s", exc)
        return False


# ── Public API ─────────────────────────────────────────────────────────────

def is_granted(tenant_id: str, channel: str, uid: str) -> bool:
    """True iff the bot may proactively contact ``uid`` on ``channel``.

    Owner carve-out: the intrinsic owner is always contactable (True) with no
    record. Otherwise: True only when a grant record exists. deny-by-default;
    never raises (a read failure denies).
    """
    try:
        if not uid:
            return False
        if _is_owner(channel, uid):
            return True
        data = _load_store(_store_path(tenant_id, channel))
        return uid in data
    except Exception as exc:  # noqa: BLE001 — deny-by-default
        _LOG.warning("is_granted failed for %s/%s/%s: %s",
                     tenant_id, channel, uid, exc)
        return False


def grant(tenant_id: str, channel: str, uid: str, *, via: str = "slash") -> dict:
    """Grant proactive-contact consent for ``uid``. Never raises.

    Owner is a no-op (already contactable). Returns
    ``{"ok": bool, "reason": str, ...}``.
    """
    try:
        if not uid:
            return {"ok": False, "reason": "invalid-uid"}
        if _is_owner(channel, uid):
            return {"ok": True, "reason": "owner-implicit"}
        path = _store_path(tenant_id, channel)
        data = _load_store(path)
        if uid in data:
            return {"ok": True, "reason": "already-granted"}
        data[uid] = {
            "granted_at": time.time(),
            "channel": channel,
            "granted_via": via,
        }
        if not _save_store(path, data):
            return {"ok": False, "reason": "write-failed"}
        _audit("proactive_consent.granted",
               tenant_id=tenant_id, channel=channel, uid=uid,
               details={"via": via})
        return {"ok": True, "reason": "granted"}
    except Exception as exc:  # noqa: BLE001 — never raise
        _LOG.warning("grant failed for %s/%s/%s: %s",
                     tenant_id, channel, uid, exc)
        return {"ok": False, "reason": f"error:{exc}"}


def revoke(tenant_id: str, channel: str, uid: str, *,
           chat_id: str | int | None = None,
           outbox_dir: str | Path | None = None) -> dict:
    """Revoke proactive-contact consent for ``uid`` (HARD KILL). Never raises.

    Removes the grant record AND best-effort purges any still-pending
    *proactive* envelopes for this chat from the outbox (only envelopes carrying
    a proactive marker — normal replies stay).

    ``chat_id`` (the ``/proactive off`` chat context): proactive envelopes route
    by ``chat_id`` (the CHANNEL), not by ``uid``. In a group channel
    ``chat_id != uid``, so matching purely on ``chat_id/to == uid`` would miss
    every group envelope (only DMs, where ``chat_id == uid``, were purged). When
    ``chat_id`` is given the purge matches THAT chat; it always also matches
    ``uid`` as a fallback (DM parity + back-compat).

    Owner cannot be revoked (they are contactable by carve-out, not by record).
    Returns ``{"ok": bool, "reason": str, "purged": int, ...}``.
    """
    try:
        if not uid:
            return {"ok": False, "reason": "invalid-uid", "purged": 0}
        if _is_owner(channel, uid):
            # Owner has no record to remove; still hard-kill any queued proactive
            # envelopes so "stop" is honoured even for the owner.
            purged = _purge_proactive_envelopes(uid, outbox_dir, chat_id=chat_id)
            return {"ok": True, "reason": "owner-implicit", "purged": purged}

        path = _store_path(tenant_id, channel)
        data = _load_store(path)
        existed = uid in data
        if existed:
            del data[uid]
            _save_store(path, data)  # best-effort; purge proceeds regardless

        purged = _purge_proactive_envelopes(uid, outbox_dir, chat_id=chat_id)

        if existed:
            _audit("proactive_consent.revoked",
                   tenant_id=tenant_id, channel=channel, uid=uid,
                   details={"purged_envelopes": purged})
        return {
            "ok": True,
            "reason": "revoked" if existed else "no-record",
            "purged": purged,
        }
    except Exception as exc:  # noqa: BLE001 — never raise
        _LOG.warning("revoke failed for %s/%s/%s: %s",
                     tenant_id, channel, uid, exc)
        return {"ok": False, "reason": f"error:{exc}", "purged": 0}


def _is_proactive_envelope(env: dict) -> bool:
    """True iff the envelope is a proactive outbound message (not a reply)."""
    if not isinstance(env, dict):
        return False
    return any(env.get(m) for m in PROACTIVE_ENVELOPE_MARKERS)


def _envelope_targets_uid(env: dict, uid: str,
                          chat_id: str | int | None = None) -> bool:
    """True iff the envelope routes to ``uid`` — OR to ``chat_id`` when given.

    Proactive envelopes route by ``chat_id`` (the channel), so a group-channel
    purge must match the chat, not the uid. ``uid`` is always accepted too (DM
    parity + back-compat with callers that pass no chat_id)."""
    if not isinstance(env, dict):
        return False
    targets = {str(uid)}
    if chat_id is not None and str(chat_id) != "":
        targets.add(str(chat_id))
    for key in ("chat_id", "to"):
        val = env.get(key)
        if val is not None and str(val) in targets:
            return True
    return False


def _purge_proactive_envelopes(uid: str, outbox_dir: str | Path | None,
                               *, chat_id: str | int | None = None) -> int:
    """Best-effort hard-kill: delete pending proactive envelopes for this chat.

    Only removes envelopes that are BOTH proactive (marker present) AND routed
    to ``chat_id`` (when given) or ``uid`` (chat_id / to). Normal reply envelopes
    are left untouched. Never raises; returns the count purged.
    """
    purged = 0
    try:
        outbox = Path(outbox_dir) if outbox_dir is not None else _default_outbox_dir()
        if not outbox.is_dir():
            return 0
        for f in outbox.glob("*.json"):
            try:
                env = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if _is_proactive_envelope(env) and _envelope_targets_uid(env, uid, chat_id):
                try:
                    f.unlink(missing_ok=True)
                    purged += 1
                except OSError as exc:
                    _LOG.warning("outbox purge unlink failed for %s: %s", f.name, exc)
    except Exception as exc:  # noqa: BLE001 — never raise
        _LOG.warning("outbox purge failed for uid %s: %s", uid, exc)
    return purged


# ── CLI (called by the JS slash-command handler for /proactive) ───────────

def _cli_main(argv: list[str]) -> int:
    """Subcommands:

      status  <tenant_id> <channel> <uid>
      on      <tenant_id> <channel> <uid>    (alias: grant)
      off     <tenant_id> <channel> <uid>    (alias: revoke)

    The JS handler maps ``/proactive on`` → ``on`` (grant) and
    ``/proactive off`` → ``off`` (revoke), passing the caller's platform uid
    and the explicit tenant_id.
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_cli_main.__doc__ or "")
        return 0
    sub = argv[0].lower()
    if sub == "status":
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: status <tenant_id> <channel> <uid>"}))
            return 1
        print(json.dumps({"ok": True, "granted": is_granted(argv[1], argv[2], argv[3])}))
        return 0
    if sub in ("on", "grant"):
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: on <tenant_id> <channel> <uid>"}))
            return 1
        r = grant(argv[1], argv[2], argv[3], via="slash")
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    if sub in ("off", "revoke"):
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: off <tenant_id> <channel> <uid> [chat_id]"}))
            return 1
        # Optional chat_id (the /proactive off chat context) so the hard-kill
        # purge reaches GROUP-channel proactive envelopes (chat_id != uid), not
        # only DMs. The JS handler passes ctx.chatKey here.
        chat_id = argv[4] if len(argv) > 4 and argv[4] else None
        r = revoke(argv[1], argv[2], argv[3], chat_id=chat_id)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    print(json.dumps({"ok": False, "error": f"unknown subcommand: {sub!r}"}))
    return 1


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
