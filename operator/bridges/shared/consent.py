"""consent.py — Layer 17: per-user consent gate for observer-transcript.

Layer 16 introduced the observer-transcript flow: read-only senders in a chat
where the owner enabled `observer_visibility = "transcript"` have their
messages buffered and prepended to the next OWNER turn. The flow is
chat-scoped: once the owner flips the flag, *every* observer's text lands in
the LLM context, without those observers having ever consented.

This module is the per-USER gate that closes that gap. Default is *deny*: an
observer's text never reaches the buffer until they themselves grant consent
via a slash-command (`/consent on`, `/consent <ttl>`) or per-message
opt-in (`/share <text>`).

Three modes:

* **durable**       — `/consent on` / `/consent yes`. Until revoked.
* **time_bounded**  — `/consent 1h` / `/consent 30m` / `/consent 7d`. TTL.
* **per_message**   — `/share <text>`. Single message admitted, no state
                       persisted; consumed at the daemon side as a one-shot
                       admit token.

Storage
-------
Single JSON file per (channel, chat) at::

    <corvin_home>/global/consent/<safe_channel>__<safe_chat>.json

::

    {
      "<uid>": {"mode": "durable",       "granted_at": 1778204770.0,
                "expires_at": null,      "channel": "telegram",
                "granted_via": "slash"},
      "<uid>": {"mode": "time_bounded",  "granted_at": ...,
                "expires_at": 1778298170.0, ...}
    }

Stale (expired) entries are pruned lazily on every read. Concurrent writes
use a ``.lock`` sidecar (POSIX flock).

Audit
-----
Every grant / revoke / drop emits a ``consent.*`` event into the unified
hash chain at ``<corvin_home>/global/forge/audit.jsonl`` — the same
chain forge / skill-forge / path-gate / auth-elevation use; one
``voice-audit verify`` covers all of it.

Design notes
------------
* The gate is *consulted* by the bridge adapter (write-time) and by the
  consume-path (TOCTOU re-validation). The slash-command UI lives in
  ``shared/js/in_chat_commands.js``; the daemon-side ``/share`` parser
  lives in each channel's ``daemon.js``.

* Identity binding is platform-enforced: the slash-command carries the
  sender's platform uid, set by the daemon from the bridge protocol
  (Telegram ``msg.from.id``, Discord ``message.author.id``, WhatsApp
  ``m.key.participant``). The owner cannot grant on behalf of another
  user because the slash-command runs in the granter's own message.

* Path-gate (``operator/voice/hooks/path_gate.py``) protects this storage
  from direct ``Write``/``Edit``/``Bash`` writes — only the bridge daemon
  process (which spawns slash-command handlers) and this module itself
  can mutate the file.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import hashlib as _hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

# ── ADR-0141 Tier 3 — self-register this security capability at import time ──
try:  # pragma: no cover - exercised at adapter boot / self-test
    from security_capabilities import (  # noqa: E402
        register_capability as _reg_cap,
        module_self_hash as _self_hash,
    )

    _reg_cap("consent_gate", version="1.4", file_hash=_self_hash(__file__))
except Exception:  # pragma: no cover - fail-closed: absent capability blocks spawn
    pass


def _uid_hash(uid: str) -> str:
    """SHA-256[:8] pseudonymisation — mirrors disclosure.py pattern."""
    return _hashlib.sha256(uid.encode()).hexdigest()[:8] if uid else ""


class ConsentStoreCorrupted(RuntimeError):
    """Raised when the consent JSON file exists but cannot be parsed."""


# Public TTL clamps. Operators can override via slash-command, but the
# parser caps at 30 days so a typo doesn't grant forever-consent.
MIN_TTL_S = 60                    # 1 minute
MAX_TTL_S = 30 * 24 * 60 * 60     # 30 days
DEFAULT_TTL_S = 60 * 60           # 1 hour (the canonical "/consent 1h")

# /share <text> — per-message opt-in prefix. Case-insensitive, matches at
# the very start of the trimmed message body. Captures the payload (which
# may itself be empty -> daemon should reject).
SHARE_PREFIX_RE = re.compile(r"^/share(?:\s+([\s\S]+))?\s*$", re.IGNORECASE)

# /consent <arg> — operator-side toggle. Parsed by in_chat_commands.js
# but the *effect* lives here; this regex is exposed for tests and for
# the (future) Python CLI wrapper. The actual durations are parsed by
# parse_ttl().
CONSENT_PREFIX_RE = re.compile(r"^/consent(?:\s+(.+))?\s*$", re.IGNORECASE)

# Duration tokens accepted by parse_ttl(): 30s / 5m / 1h / 7d.
_TTL_TOKEN_RE = re.compile(r"^(\d+)([smhd])$", re.IGNORECASE)
_TTL_UNIT_S = {"s": 1, "m": 60, "h": 60 * 60, "d": 24 * 60 * 60}


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
    """Mirror of adapter._safe_id — alnum-only, length-capped."""
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:64] or "anon"


def _store_path(channel: str, chat_key: str, *, tenant_id: str | None = None) -> Path:
    safe_channel = _safe_component(channel or "unknown")
    safe_chat = _safe_component(str(chat_key) if chat_key is not None else "anon")
    home = _corvin_home()
    if tenant_id is None:
        # ADR-0007 Phase 1.3: legacy path preserved for backward compat
        base = home / "global" / "consent"
    else:
        base = home / "tenants" / tenant_id / "global" / "consent"
    return base / f"{safe_channel}__{safe_chat}.json"


def _audit_path(*, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        return home / "global" / "forge" / "audit.jsonl"
    return home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


class ChainIntegrityFailureGateUnavailable(RuntimeError):
    """Raised when the CLAG integrity gate is expected but cannot be loaded.

    A silently self-disabled integrity gate is itself the self-modification
    risk CLAG (ADR-0133) exists to prevent, so we surface it as a blocking
    error rather than skipping the check. The name embeds
    ``ChainIntegrityFailure`` so call sites that detect integrity failures by
    type-name substring treat this as fail-closed.
    """


def _clag_gate(layer_id: str) -> None:
    """ADR-0133 CLAG — verify chain integrity before a consent decision.

    Raises ``ChainIntegrityFailure`` when the audit chain is broken.

    Fail-closed: when the ``clag`` module is *expected* (the forge package is
    present on disk) but cannot be imported, raise
    ``ChainIntegrityFailureGateUnavailable`` — a silently self-disabled
    integrity gate is itself a security failure. Only when the forge package
    is genuinely absent (minimal deployment) do we fail-open, and then loudly
    (WARNING) so the skipped check is never invisible.
    """
    _forge_inner = None
    try:
        import sys as _sys
        _here = Path(__file__).resolve()
        for _parent in _here.parents:
            if (_parent / ".corvin_repo").exists() or (_parent / "plugins").is_dir():
                _forge_inner = _parent / "operator" / "forge" / "forge"
                if str(_forge_inner) not in _sys.path:
                    _sys.path.insert(0, str(_forge_inner))
                break
        from clag import gate as _gate  # type: ignore  # noqa: PLC0415
    except ImportError as _imp_exc:
        import importlib.util as _ilu
        import logging as _logging
        # Forge is "expected" if either the directory marker is found OR the
        # package is already known to the interpreter.  find_spec raises
        # ModuleNotFoundError when sys.modules[name]=None (explicitly blocked),
        # so we catch all exceptions and treat them as "not found".
        def _spec_known(name: str) -> bool:
            try:
                return _ilu.find_spec(name) is not None
            except Exception:  # noqa: BLE001
                return False
        _forge_expected = (
            (_forge_inner is not None and _forge_inner.exists())
            or _spec_known("clag")
            or _spec_known("forge")
        )
        if _forge_expected:
            _logging.getLogger("corvin.consent").critical(
                "CLAG integrity gate present but unimportable (%s) — failing "
                "CLOSED for %s", _imp_exc, layer_id,
            )
            raise ChainIntegrityFailureGateUnavailable(
                f"clag unimportable despite forge present: {_imp_exc}"
            ) from _imp_exc
        _logging.getLogger("corvin.consent").warning(
            "CLAG integrity gate unavailable (forge package absent) — chain "
            "pre-check skipped for %s", layer_id,
        )
        return
    _gate(_audit_path(), layer_id)


# ── Audit emission (best-effort, mirrors auth_elevation pattern) ──────

def _audit(event_type: str, *, channel: str, chat_key: str, uid: str,
           details: dict[str, Any] | None = None,
           severity: str | None = None) -> None:
    """Best-effort audit write — silent on failure.

    The bridge adapter is the same process that calls us, so the forge
    package is already on sys.path in the production case. For
    standalone tests we walk-up to find ``operator/forge``.
    """
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
        "uid_hash": _uid_hash(uid),
    }
    if details:
        body.update(details)
    try:
        if severity:
            write_event(_audit_path(), event_type,
                        details=body, severity=severity)
        else:
            write_event(_audit_path(), event_type, details=body)
        # ADR-0133 CLAG — advance the L16.consent_gate shadow after each audit
        # write so subsequent gate() calls see the current chain tail, not the
        # stale tail from the last cit_issued event.
        try:
            from clag import advance_layer_shadow as _clag_adv  # type: ignore  # noqa: PLC0415
            _clag_adv("L16.consent_gate", _audit_path())
        except Exception as _adv_exc:  # noqa: BLE001
            # Non-fatal: a stale shadow errs toward a (safe) false
            # shadow_mismatch on the next gate(), never toward fail-open —
            # but log it so the degraded state is observable.
            import logging as _logging
            _logging.getLogger("corvin.consent").warning(
                "CLAG shadow advance failed for L16.consent_gate: %s", _adv_exc
            )
    except Exception as _exc:
        import logging as _logging
        _logging.getLogger("corvin.consent").warning(
            "audit write failed for %s: %s", event_type, _exc
        )


# ── Store I/O ──────────────────────────────────────────────────────────

def _load_store(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        raw = path.read_text()
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as exc:
        raise ConsentStoreCorrupted(
            f"consent store at {path} is corrupted: {exc}"
        ) from exc
    except OSError:
        return {}


def _locked_update(path: Path, update_fn):
    """Hold the store lock across read-prune-update-write.

    Calls ``update_fn(data)`` (mutates data in-place) while the lock
    is held, then atomically writes. Returns ``(expired_uids, fn_result)``
    so callers see both what was pruned and whatever update_fn returned.

    This closes the TOCTOU race in grant()/revoke() where two concurrent
    callers could each read the same snapshot, both modify, and the last
    write would silently overwrite the first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data, expired = _prune(_load_store(path))
        fn_result = update_fn(data)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
        return expired, fn_result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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


def _save_store_with_retry(path: Path, data: dict[str, dict], *,
                           max_attempts: int = 3) -> None:
    """_save_store with exponential backoff retry on OSError (best-effort)."""
    delay = 0.1
    for attempt in range(max_attempts):
        try:
            _save_store(path, data)
            return
        except OSError:
            if attempt == max_attempts - 1:
                import logging as _log
                _log.getLogger("corvin.consent").warning(
                    "consent prune-write failed after %d attempts: %s",
                    max_attempts, path,
                )
                return  # best-effort: prune didn't persist, will retry next call
            time.sleep(delay)
            delay *= 3


def _prune(data: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """Drop expired time_bounded entries. Returns (kept, expired_uids)."""
    now = time.time()
    kept: dict[str, dict] = {}
    expired: list[str] = []
    for uid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        mode = entry.get("mode")
        exp = entry.get("expires_at")
        if mode == "time_bounded" and isinstance(exp, (int, float)) and exp <= now:
            expired.append(uid)
            continue
        kept[uid] = entry
    return kept, expired


# ── Public API ─────────────────────────────────────────────────────────

def parse_ttl(arg: str) -> int | None:
    """Parse a /consent argument into a TTL in seconds, or None for
    durable / off / status keywords. Returns ``-1`` for invalid input
    so the caller can distinguish "no TTL given" (None) from "garbage".
    """
    if not arg:
        return None
    a = arg.strip().lower()
    if a in ("on", "yes", "ja", "true", "always"):
        return None  # durable — no TTL
    if a in ("off", "no", "nein", "false", "revoke"):
        return None  # signaling revoke; caller distinguishes via the keyword
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


def parse_share_prefix(text: str) -> tuple[bool, str]:
    """Recognise ``/share <payload>`` at the start of a message body.

    Returns ``(is_share, payload)``. ``payload`` is empty when the user
    typed ``/share`` alone — the daemon should respond with a usage hint
    rather than admitting an empty message.
    """
    if not text:
        return False, ""
    m = SHARE_PREFIX_RE.match(text.strip())
    if not m:
        return False, ""
    payload = (m.group(1) or "").strip()
    return True, payload


def is_granted(channel: str, chat_key: str, uid: str
               ) -> tuple[bool, str]:
    """Return (granted, reason).

    ``granted=True`` when the uid currently holds a non-expired durable
    or time_bounded consent entry. The ``reason`` carries a machine-tag:

      * ``"durable"`` — durable consent
      * ``"ttl:<seconds_remaining>"`` — time-bounded, still valid
      * ``"no-entry"`` — uid has never granted consent in this chat
      * ``"expired"`` — entry was expired and lazy-pruned
      * ``"revoked"`` — entry exists but ``mode == "none"``
        (explicit revoke that we kept for audit)
    """
    if not uid:
        return False, "no-uid"
    # ADR-0133 CLAG M2 — chain integrity gate before consent decision (fail-closed).
    try:
        _clag_gate("L16.consent_gate")
    except Exception:
        return False, "chain-integrity-failed"
    path = _store_path(channel, chat_key)
    try:
        data = _load_store(path)
    except ConsentStoreCorrupted:
        import shutil as _shutil
        import time as _t
        backup = path.with_name(path.name + f".corrupt.{int(_t.time())}")
        try:
            _shutil.copy2(path, backup)
            path.unlink()
        except OSError:
            pass
        _audit(
            "consent.store_corrupted",
            channel=channel, chat_key=str(chat_key), uid="",
            details={"reason": "json-parse-error", "action": "deny-all"},
            severity="CRITICAL",
        )
        return False, "store-corrupted"
    raw_entry = data.get(uid)
    kept, expired_uids = _prune(data)
    # Persist the prune if anything actually expired.
    if expired_uids:
        _save_store_with_retry(path, kept)
        for ex_uid in expired_uids:
            _audit(
                "consent.expired",
                channel=channel, chat_key=str(chat_key), uid=ex_uid,
                details={"reason": "ttl-expired"},
            )
    entry = kept.get(uid)
    if entry is None:
        if raw_entry is not None and uid in expired_uids:
            return False, "expired"
        return False, "no-entry"
    mode = entry.get("mode")
    if mode == "durable":
        return True, "durable"
    if mode == "time_bounded":
        exp = entry.get("expires_at") or 0
        remaining = max(0, int(exp - time.time()))
        return True, f"ttl:{remaining}"
    if mode == "none":
        return False, "revoked"
    return False, "unknown-mode"


# ADR-0052 F4 — TOCTOU consent epoch validation.
# Default 30s: generous for a healthy system under load, tight enough to
# catch deliberately delayed envelopes. Configurable via settings.json.
DEFAULT_CONSENT_TOCTOU_MAX_S: int = 30


def is_granted_with_epoch(
    channel: str,
    chat_key: str,
    uid: str,
    *,
    consent_epoch: float | None = None,
    toctou_max_s: int = DEFAULT_CONSENT_TOCTOU_MAX_S,
) -> tuple[bool, str]:
    """TOCTOU-aware consent check.

    Extends :func:`is_granted` with an epoch-staleness guard. If
    ``consent_epoch`` is provided:

    * If ``time.time() - consent_epoch < toctou_max_s``: trust the epoch
      (consent was recently verified by the daemon), skip re-validation.
    * If stale: re-validate against the live consent store.
    * If stale AND not granted: emit ``consent.toctou_drop`` WARNING.

    Returns the same ``(granted, reason)`` shape as :func:`is_granted`.
    """
    now = time.time()
    if consent_epoch is not None and (now - consent_epoch) < toctou_max_s:
        # Epoch is fresh — the daemon recently verified consent; skip disk re-read.
        return True, "epoch_ok"

    # Stale or absent epoch — re-validate from disk.
    granted, reason = is_granted(channel, chat_key, uid)
    if not granted and consent_epoch is not None:
        age_s = int(now - consent_epoch)
        _audit(
            "consent.toctou_drop",
            channel=channel,
            chat_key=str(chat_key),
            uid="",  # never log uid — PII
            details={
                "age_s": age_s,
                "toctou_max_s": toctou_max_s,
                "reason": reason,
            },
            severity="WARNING",
        )
    return granted, reason


def grant(channel: str, chat_key: str, uid: str, *,
          ttl_s: int | None = None,
          via: str = "slash") -> dict:
    """Grant consent for ``uid`` in ``(channel, chat_key)``.

    ``ttl_s=None`` → durable; otherwise time-bounded clamped to
    ``[MIN_TTL_S, MAX_TTL_S]``. Returns the stored entry dict.
    Emits ``consent.granted`` audit event.
    """
    if not uid:
        raise ValueError("uid required")
    now = time.time()
    if ttl_s is None:
        entry = {
            "mode": "durable",
            "granted_at": now,
            "expires_at": None,
            "channel": channel,
            "granted_via": via,
        }
    else:
        clamped = max(MIN_TTL_S, min(MAX_TTL_S, int(ttl_s)))
        entry = {
            "mode": "time_bounded",
            "granted_at": now,
            "expires_at": now + clamped,
            "channel": channel,
            "granted_via": via,
            "ttl_s": clamped,
        }
    path = _store_path(channel, chat_key)
    # _locked_update holds the lock across read-prune-write to prevent the
    # lost-update race where two concurrent grant() calls overwrite each other.
    _locked_update(path, lambda data: data.update({uid: entry}))
    _audit(
        "consent.granted",
        channel=channel, chat_key=str(chat_key), uid=uid,
        details={
            "mode": entry["mode"],
            "ttl_s": entry.get("ttl_s"),
            "granted_via": via,
        },
    )
    return entry


def revoke(channel: str, chat_key: str, uid: str, *,
           via: str = "slash") -> bool:
    """Drop the consent entry for ``uid``. Returns True iff an entry
    actually existed (so the caller can distinguish "revoked" from
    "nothing was on file"). Emits ``consent.revoked`` audit event."""
    if not uid:
        return False
    path = _store_path(channel, chat_key)

    def _do_revoke(data: dict) -> bool:
        ex = uid in data
        if ex:
            data.pop(uid, None)
        return ex

    _expired, existed = _locked_update(path, _do_revoke)
    if existed:
        _audit(
            "consent.revoked",
            channel=channel, chat_key=str(chat_key), uid=uid,
            details={"granted_via": via},
        )
    return existed


def status(channel: str, chat_key: str, uid: str) -> dict:
    """Return a status dict for an uid: ``{granted, mode, remaining_s,
    granted_at, granted_via}`` — safe for serialisation into a chat
    reply via ``/consent status``."""
    granted, reason = is_granted(channel, chat_key, uid)
    path = _store_path(channel, chat_key)
    data, _expired = _prune(_load_store(path))  # raises ConsentStoreCorrupted to caller
    entry = data.get(uid) or {}
    remaining = 0
    if entry.get("mode") == "time_bounded":
        remaining = max(0, int(entry.get("expires_at", 0) - time.time()))
    return {
        "granted": granted,
        "reason": reason,
        "mode": entry.get("mode") or "none",
        "remaining_s": remaining,
        "granted_at": entry.get("granted_at"),
        "granted_via": entry.get("granted_via"),
        "channel": channel,
        "chat_key": chat_key,
        "uid": uid,
    }


def list_consents(channel: str, chat_key: str) -> dict[str, dict]:
    """Return all currently-valid (non-expired) consent entries for the
    chat. Used by ``/consent list`` for the owner to see who consented.
    """
    path = _store_path(channel, chat_key)
    data, _expired = _prune(_load_store(path))  # raises ConsentStoreCorrupted to caller
    return data


def admit_observer_drop(channel: str, chat_key: str, uid: str,
                        *, msg_id: str = "", text_len: int = 0) -> None:
    """Audit-only helper: record that an observer's message was dropped
    for lack of consent. Records text length only (GDPR Art. 5 minimisation)."""
    _audit(
        "consent.observer_dropped",
        channel=channel, chat_key=str(chat_key), uid=uid,
        details={
            "msg_id": msg_id,
            "text_len": text_len,
            "reason": "no-consent",
        },
    )


def admit_share_one_shot(channel: str, chat_key: str, uid: str,
                         *, msg_id: str = "", text_len: int = 0) -> None:
    """Audit-only helper: record that a ``/share`` per-message admit
    let an otherwise-non-consenting observer through. Records text
    length only (GDPR Art. 5 minimisation)."""
    _audit(
        "consent.share_admitted",
        channel=channel, chat_key=str(chat_key), uid=uid,
        details={
            "msg_id": msg_id,
            "text_len": text_len,
            "via": "share-prefix",
        },
    )


def consume_buffer_drift(channel: str, chat_key: str, uid: str,
                         *, text_len: int = 0) -> None:
    """Audit-only helper: record that a buffered observer line was
    dropped at *consume* time because consent was revoked or expired.
    Records text length only (GDPR Art. 5 minimisation)."""
    _audit(
        "consent.consume_drift",
        channel=channel, chat_key=str(chat_key), uid=uid,
        details={
            "text_len": text_len,
            "reason": "consent-drift-at-consume",
        },
    )


# ── CLI (called by the JS slash-command handler via spawnSync) ─────────

def _human_ttl(seconds: int) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _cli_status(channel: str, chat_key: str, uid: str) -> int:
    s = status(channel, chat_key, uid)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


def _cli_on(channel: str, chat_key: str, uid: str,
            ttl_arg: str | None) -> int:
    """on/yes/<duration> all map to grant. ttl_arg=None means durable."""
    ttl_s: int | None = None
    if ttl_arg is not None:
        parsed = parse_ttl(ttl_arg)
        if parsed == -1:
            print(json.dumps({"ok": False,
                              "error": f"invalid duration: {ttl_arg!r}",
                              "hint": "use 30s / 5m / 1h / 7d"}))
            return 1
        ttl_s = parsed  # may be None for durable keywords
    entry = grant(channel, chat_key, uid, ttl_s=ttl_s, via="cli")
    out = {
        "ok": True,
        "mode": entry["mode"],
        "ttl_human": _human_ttl(entry["ttl_s"]) if entry.get("ttl_s") else None,
        "expires_at": entry.get("expires_at"),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


def _cli_off(channel: str, chat_key: str, uid: str) -> int:
    existed = revoke(channel, chat_key, uid, via="cli")
    print(json.dumps({"ok": True, "existed": existed}))
    return 0


def _cli_list(channel: str, chat_key: str) -> int:
    data = list_consents(channel, chat_key)
    out = {"ok": True, "count": len(data), "entries": data}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cli_main(argv: list[str]) -> int:
    """Subcommands:

      status   <channel> <chat_key> <uid>
      on       <channel> <chat_key> <uid> [<duration>]
      off      <channel> <chat_key> <uid>
      list     <channel> <chat_key>
      parse-ttl <duration>            (utility for tests)
      parse-share <text...>           (utility for daemons + tests)

    All output is JSON on stdout; errors land on stdout too with
    ``ok=false`` so the JS caller has a single parse path. Exit codes:
    0 = ok, 1 = bad args / parse error.
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_cli_main.__doc__ or "")
        return 0
    sub = argv[0].lower()
    if sub == "status":
        if len(argv) < 4:
            print(json.dumps({"ok": False,
                              "error": "usage: status <channel> <chat_key> <uid>"}))
            return 1
        return _cli_status(argv[1], argv[2], argv[3])
    if sub == "on":
        if len(argv) < 4:
            print(json.dumps({"ok": False,
                              "error": "usage: on <channel> <chat_key> <uid> [<duration>]"}))
            return 1
        ttl_arg = argv[4] if len(argv) >= 5 else None
        return _cli_on(argv[1], argv[2], argv[3], ttl_arg)
    if sub == "off":
        if len(argv) < 4:
            print(json.dumps({"ok": False,
                              "error": "usage: off <channel> <chat_key> <uid>"}))
            return 1
        return _cli_off(argv[1], argv[2], argv[3])
    if sub == "list":
        if len(argv) < 3:
            print(json.dumps({"ok": False,
                              "error": "usage: list <channel> <chat_key>"}))
            return 1
        return _cli_list(argv[1], argv[2])
    if sub == "parse-ttl":
        if len(argv) < 2:
            print(json.dumps({"ok": False, "error": "usage: parse-ttl <duration>"}))
            return 1
        ttl = parse_ttl(argv[1])
        print(json.dumps({"ok": ttl != -1, "ttl_s": ttl}))
        return 0 if ttl != -1 else 1
    if sub == "parse-share":
        text = " ".join(argv[1:])
        is_share, payload = parse_share_prefix(text)
        print(json.dumps({"ok": True, "is_share": is_share,
                          "payload": payload}))
        return 0
    print(json.dumps({"ok": False, "error": f"unknown subcommand: {sub!r}"}))
    return 1


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
