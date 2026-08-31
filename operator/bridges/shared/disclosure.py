"""disclosure.py — Layer 19: bot-disclosure card + /join self-service.

Closes the "passive consent" gap that the architecture review surfaced: a
new uid in a chat is silently dropped or silently observed; nobody is ever
*told* "there is an AI bot here, and here's what it does, and here's how
you opt in or out." The legal term for this is **bot-disclosure**, and
under the EU AI Act the operator is required to provide it actively for
many configurations.

This module provides three things:

1. **Per-(channel, chat, uid) seen-tracker**. Records the first time we
   ever saw a uid speak in a chat. The daemon checks this on every
   incoming message *before* the read-only / consent / whitelist gates
   and emits the disclosure card on first-encounter.

2. **Card text generator** (DE / EN). A short, structured introduction
   that explains: who runs the bot, what it does, what the available
   actions are (``/join`` / ``/leave`` / ``/consent on``), and where the
   consent / role state lives. Designed to be a single chat message
   (≤ 1500 chars) so it doesn't shred phone screens.

3. **/join handler**. Self-service registration as ``observer`` for
   read-only-side senders. The action ``mark_seen(action="joined")``
   pairs the disclosure ack with a ``roles.grant`` call (via the
   ``roles`` module) so the user appears in ``/roles`` as a
   "consenting observer."

Storage
-------
Single JSON file per (channel, chat) at::

    <corvin_home>/global/disclosure/<safe_channel>__<safe_chat>.json

::

    {
      "<uid>": {
        "first_seen": 1778204770.0,
        "card_shown_at": 1778204770.0,
        "action": "joined" | "passed" | "left" | "pending",
        "channel": "telegram"
      },
      ...
    }

Lazy: nothing expires here — disclosure is a one-time-per-uid contract.
The store grows monotonically; an operator-side ``/disclosure-reset``
command is intentionally NOT exposed to the LLM, only to the maintainer
via direct file edit.

Audit
-----
Every card-show, /join, /leave-via-disclosure, and /pass emits a
``disclosure.*`` event into the unified hash chain at
``<corvin_home>/global/forge/audit.jsonl``. ``voice-audit verify``
covers the new event-types automatically.

Design notes
------------
* The **seen check** is the daemon's gate-zero — runs BEFORE
  ``readOnlyOk``, BEFORE ``authOk``. The card goes to the chat (so the
  new participant actually sees it); the uid is then silently dropped /
  consented / authed depending on the rest of the pipeline.

* ``/join`` has two integration points: the read-only-side dispatcher
  (mirroring ``dispatchReadOnlyConsent``), AND the owner-side
  dispatcher (where it's a no-op + hint, mirroring how owners get a
  redirect when they type ``/consent on``).

* The owner is *implicitly* known to be in the chat at boot — disclosure
  is opt-in for new participants only. The owner never sees the card
  for themselves; ``mark_seen`` on the owner returns the existing entry
  with ``action="owner-implicit"`` and emits no audit event.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import re
import time
from pathlib import Path
from typing import Any

# ── Card length budget ────────────────────────────────────────────────
# The card is one chat message; phone screens are narrow. ≤ 1500 chars
# keeps it readable on a single scroll. The renderer enforces this.
MAX_CARD_CHARS = 1500


# ── Path resolution (mirror of consent.py / roles.py) ────────────────

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
    safe_channel = _safe_component(channel or "unknown")
    safe_chat = _safe_component(str(chat_key) if chat_key is not None else "anon")
    home = _corvin_home()
    if tenant_id is None:
        base = home / "global" / "disclosure"
    else:
        base = home / "tenants" / tenant_id / "global" / "disclosure"
    return base / f"{safe_channel}__{safe_chat}.json"


def _audit_path(*, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        return home / "global" / "forge" / "audit.jsonl"
    return home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


def _channel_settings_path(channel: str) -> Path:
    here = Path(__file__).resolve().parent
    return here.parent / channel / "settings.json"


class ChainIntegrityFailureGateUnavailable(RuntimeError):
    """Raised when the CLAG integrity gate is expected but cannot be loaded.

    A silently self-disabled integrity gate is itself the self-modification
    risk CLAG (ADR-0133) exists to prevent, so we surface it as a blocking
    error rather than skipping the check. The name embeds
    ``ChainIntegrityFailure`` so call sites that detect integrity failures by
    type-name substring treat this as fail-closed.
    """


def _clag_gate(layer_id: str) -> None:
    """ADR-0133 CLAG — verify chain integrity before a disclosure event.

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
            _logging.getLogger("corvin.disclosure").critical(
                "CLAG integrity gate present but unimportable (%s) — failing "
                "CLOSED for %s", _imp_exc, layer_id,
            )
            raise ChainIntegrityFailureGateUnavailable(
                f"clag unimportable despite forge present: {_imp_exc}"
            ) from _imp_exc
        _logging.getLogger("corvin.disclosure").warning(
            "CLAG integrity gate unavailable (forge package absent) — chain "
            "pre-check skipped for %s", layer_id,
        )
        return
    _gate(_audit_path(), layer_id)


# ── Audit emission (best-effort, mirrors consent / roles) ─────────────

def _uid_hash(uid: str) -> str:
    """ADR-0052 F6 — SHA-256 prefix (8 hex chars) of a UID for audit coverage.

    Maps each UID to a stable, non-reversible token that operators can use to
    confirm disclosure coverage across all active UIDs without exposing the raw
    platform identifier (GDPR Art. 5 data minimization).

    Format: first 8 hex chars of SHA-256(uid.encode('utf-8')).
    """
    import hashlib as _hashlib
    return _hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8]


def _audit(event_type: str, *, channel: str, chat_key: str, uid: str,
           details: dict[str, Any] | None = None,
           severity: str | None = None) -> None:
    """Best-effort audit write — silent on failure.

    ADR-0052 F6: raw UID is replaced with uid_hash (SHA-256[:8]) in every
    disclosure.* audit event. This allows coverage queries without PII leakage.
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
        # ADR-0052 F6: uid_hash instead of raw uid
        "uid_hash": _uid_hash(uid) if uid else "",
    }
    if details:
        body.update(details)
    try:
        if severity:
            write_event(_audit_path(), event_type,
                        details=body, severity=severity)
        else:
            write_event(_audit_path(), event_type, details=body)
        # ADR-0133 CLAG — advance the L19.disclosure_gate shadow after each
        # audit write so subsequent gate() calls see the current chain tail.
        try:
            from clag import advance_layer_shadow as _clag_adv  # type: ignore  # noqa: PLC0415
            _clag_adv("L19.disclosure_gate", _audit_path())
        except Exception as _adv_exc:  # noqa: BLE001
            # Non-fatal: a stale shadow errs toward a (safe) false
            # shadow_mismatch on the next gate(), never toward fail-open —
            # but log it so the degraded state is observable.
            import logging as _logging
            _logging.getLogger("corvin.disclosure").warning(
                "CLAG shadow advance failed for L19.disclosure_gate: %s", _adv_exc
            )
    except Exception as _exc:
        import logging as _logging
        _logging.getLogger("corvin.disclosure").warning(
            "audit write failed for %s: %s", event_type, _exc
        )


# ── Store I/O ──────────────────────────────────────────────────────────

def _load_store(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_SAVE_RETRY_DELAYS = (0.1, 0.3, 1.0)  # V-004: seconds between write retries


def _save_store(path: Path, data: dict[str, dict]) -> None:
    """Atomically write the disclosure store.

    V-004/V-011: Retries on OSError up to 3 times (delays 0.1 / 0.3 / 1.0 s).
    On all retries exhausted raises OSError so the caller can queue a retry.
    """
    import logging as _log_ds
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    last_exc: OSError | None = None
    for attempt, delay in enumerate((*_SAVE_RETRY_DELAYS, None)):
        try:
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
                return  # success
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        except OSError as exc:
            last_exc = exc
            _log_ds.getLogger("corvin.disclosure").warning(
                "disclosure store write failed (attempt %d/%d): %s",
                attempt + 1, len(_SAVE_RETRY_DELAYS) + 1, exc,
            )
            if delay is not None:
                time.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ── Whitelist intrinsic-owner check (mirror of roles.is_intrinsic_owner) ─

def _read_channel_whitelist(channel: str) -> list[str]:
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


def _is_intrinsic_owner(channel: str, uid: str) -> bool:
    if not uid:
        return False
    wl = _read_channel_whitelist(channel)
    if not wl:
        return True   # DEV-mode parity (auth.js fail-open)
    return uid in wl


# ── Public API ─────────────────────────────────────────────────────────

# Action enum. The store records the most-recent terminal action per uid.
ACTION_PENDING       = "pending"           # card shown, no decision yet
ACTION_JOINED        = "joined"            # /join → observer role
ACTION_PASSED        = "passed"            # /pass → silent stay (no role)
ACTION_LEFT          = "left"              # /leave → role dropped
ACTION_OWNER_IMPLICIT = "owner-implicit"   # never shown a card

VALID_ACTIONS = frozenset({
    ACTION_PENDING, ACTION_JOINED, ACTION_PASSED,
    ACTION_LEFT, ACTION_OWNER_IMPLICIT,
})


def has_seen(channel: str, chat_key: str, uid: str) -> bool:
    """True iff this uid has been recorded in this chat's disclosure store
    (regardless of which action they took). Owners are always considered
    'seen' (implicit) — the card is for new participants only."""
    if not uid:
        return False
    if _is_intrinsic_owner(channel, uid):
        return True
    data = _load_store(_store_path(channel, chat_key))
    return uid in data


def mark_seen(channel: str, chat_key: str, uid: str, *,
              action: str = ACTION_PENDING) -> dict:
    """Record (or update) the seen-state for ``uid``.

    Returns the stored entry. Owners trigger no audit and a no-op store
    write (the entry is kept in memory only) — disclosure does not apply
    to owners.

    For non-owners: emits ``disclosure.shown`` on first-time create,
    ``disclosure.action`` on subsequent action transitions.
    """
    if not uid:
        raise ValueError("invalid-uid")
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid-action:{action}")

    if _is_intrinsic_owner(channel, uid):
        return {
            "uid": uid,
            "channel": channel,
            "first_seen": time.time(),
            "card_shown_at": None,
            "action": ACTION_OWNER_IMPLICIT,
        }

    now = time.time()
    path = _store_path(channel, chat_key)
    data = _load_store(path)
    existing = data.get(uid)
    if existing is None:
        # ADR-0133 CLAG M2 — gate before first disclosure event (fail-closed).
        # ChainIntegrityFailure propagates to the daemon; disclosure is blocked
        # if the chain has been tampered (EU AI Act Art. 50 structural protection).
        _clag_gate("L19.disclosure_gate")
        # First contact — full record + disclosure.shown audit
        entry = {
            "first_seen": now,
            "card_shown_at": now,
            "action": action,
            "channel": channel,
        }
        data[uid] = entry
        _save_store(path, data)
        _audit("disclosure.shown",
               channel=channel, chat_key=str(chat_key), uid=uid,
               details={"action": action})
        return entry

    # Subsequent visit — update only if action changes
    prior_action = existing.get("action", ACTION_PENDING)
    if action != prior_action:
        existing["action"] = action
        existing["last_action_at"] = now
        data[uid] = existing
        _save_store(path, data)
        _audit("disclosure.action",
               channel=channel, chat_key=str(chat_key), uid=uid,
               details={
                   "prior_action": prior_action,
                   "action": action,
               })
    return existing


def get_state(channel: str, chat_key: str, uid: str) -> dict:
    """Return the current disclosure state for ``uid``."""
    is_owner = _is_intrinsic_owner(channel, uid)
    if is_owner:
        return {
            "uid": uid,
            "channel": channel,
            "chat_key": chat_key,
            "intrinsic_owner": True,
            "seen": True,
            "action": ACTION_OWNER_IMPLICIT,
        }
    data = _load_store(_store_path(channel, chat_key))
    entry = data.get(uid) or {}
    return {
        "uid": uid,
        "channel": channel,
        "chat_key": chat_key,
        "intrinsic_owner": False,
        "seen": uid in data,
        "first_seen": entry.get("first_seen"),
        "card_shown_at": entry.get("card_shown_at"),
        "action": entry.get("action"),
        "last_action_at": entry.get("last_action_at"),
    }


def list_seen(channel: str, chat_key: str) -> dict:
    """All entries currently in the store, plus the intrinsic owners."""
    return {
        "channel": channel,
        "chat_key": chat_key,
        "intrinsic_owners": _read_channel_whitelist(channel),
        "seen": _load_store(_store_path(channel, chat_key)),
    }


# ── Card text rendering ────────────────────────────────────────────────

def _card_de(*, owner_label: str, channel: str, has_observer_transcript: bool) -> str:
    bot_term = {
        "telegram": "Telegram-Bot",
        "discord":  "Discord-Bot",
        "whatsapp": "WhatsApp-Bot",
        "slack":    "Slack-Bot",
        "email":    "E-Mail-Bot",
    }.get((channel or "").lower(), "Bot")
    transcript_para = (
        "• Wenn du nichts tust, lese ich deine Nachrichten **nicht** mit "
        "und beantworte sie nicht. Du bist nur im Chat anwesend.\n"
        if not has_observer_transcript else
        "• Der Eigentümer hat aktiviert, dass Mitleser-Nachrichten als "
        "Hintergrund-Kontext sichtbar sind — aber **nur**, wenn du selbst "
        "dafür `/consent on` tippst. Default ist *aus*.\n"
    )
    text = (
        f"👋 Hi! Hier läuft ein KI-{bot_term}, betrieben von **{owner_label}**.\n\n"
        "Was du wissen solltest:\n"
        f"• Die KI kann Nachrichten lesen, beantworten und Aktionen ausführen — "
        f"aber nur, wenn der Eigentümer das für deine Person freigegeben hat.\n"
        f"{transcript_para}"
        "• Alle Aktionen werden protokolliert (Audit-Log).\n\n"
        "Was du tun kannst:\n"
        "• `/join`     — du registrierst dich als *Observer* (sichtbar in `/roles`,\n"
        "                aber keine Trigger-Rechte; der Eigentümer kann dich später hochstufen)\n"
        "• `/consent on` — wenn der Owner Transkript-Modus aktiviert hat: deine Nachrichten "
        "fließen in den nächsten Owner-Turn ein\n"
        "• `/leave`    — du gibst alle Rollen + Consent zurück\n"
        "• `/pass`     — du nimmst die Karte zur Kenntnis, machst aber nichts aktiv\n\n"
        "Diese Karte erscheint nur **einmal** pro Chat und Person. Jeder Schritt "
        "ist freiwillig und identitätsgebunden — niemand kann in deinem Namen zustimmen."
    )
    return text[:MAX_CARD_CHARS]


def _card_en(*, owner_label: str, channel: str, has_observer_transcript: bool) -> str:
    bot_term = {
        "telegram": "Telegram bot",
        "discord":  "Discord bot",
        "whatsapp": "WhatsApp bot",
        "slack":    "Slack bot",
        "email":    "email bot",
    }.get((channel or "").lower(), "bot")
    transcript_para = (
        "• If you do nothing, I will **not** read your messages or reply "
        "to you. You are simply present in this chat.\n"
        if not has_observer_transcript else
        "• The owner has enabled observer-transcript mode, but your "
        "messages still flow into context **only** if you yourself type "
        "`/consent on`. Default is *off*.\n"
    )
    text = (
        f"👋 Hi! There is an AI {bot_term} in this chat, operated by **{owner_label}**.\n\n"
        "What you should know:\n"
        f"• The AI can read messages, reply, and perform actions — but only "
        f"for users the owner has explicitly authorised.\n"
        f"{transcript_para}"
        "• Every action is recorded in an audit log.\n\n"
        "What you can do:\n"
        "• `/join`     — register as an *observer* (visible in `/roles`,\n"
        "                no trigger rights; the owner can promote you later)\n"
        "• `/consent on` — when transcript-mode is on: your messages will be "
        "fed into the owner's next turn as context\n"
        "• `/leave`    — drop any role + consent\n"
        "• `/pass`     — acknowledge the card without taking any action\n\n"
        "This card is shown **once** per chat and person. Every step is "
        "voluntary and identity-bound — nobody can consent on your behalf."
    )
    return text[:MAX_CARD_CHARS]


def get_card_text(*, owner_label: str, channel: str,
                  has_observer_transcript: bool = False,
                  lang: str = "de") -> str:
    """Render the disclosure card. Returns one ≤ 1500-char chat message.

    ``owner_label`` is the human-friendly identifier of the bot operator
    (e.g. "Silvio J." or a chat handle). The renderer never includes
    raw uids — those are debugging artefacts.

    ``has_observer_transcript`` flips one paragraph: when True, the
    card explains that observer-transcript is on but consent is still
    opt-in. Default False = the strictest, most welcoming default.

    ``lang`` is "de" (default) or "en".
    """
    fn = _card_en if lang.lower().startswith("en") else _card_de
    return fn(owner_label=owner_label or "(unknown)",
              channel=channel or "",
              has_observer_transcript=has_observer_transcript)


# ── Self-service join / leave / pass ───────────────────────────────────

def join(channel: str, chat_key: str, uid: str, *, via: str = "slash") -> dict:
    """Self-service: register ``uid`` as ``observer``. Returns
    ``{"ok": bool, "reason": str, ...}``.

    Owners trying to /join get a no-op + "owner-already" reason.
    Users who are already non-observer roles (member/admin) get
    "already-elevated" — promotion only happens via /grant.
    """
    if not uid:
        return {"ok": False, "reason": "invalid-uid"}
    if _is_intrinsic_owner(channel, uid):
        return {"ok": False, "reason": "owner-already"}

    # Look up current role via the roles module.
    try:
        import sys as _sys
        sys_path_added = False
        here = Path(__file__).resolve().parent
        if str(here) not in _sys.path:
            _sys.path.insert(0, str(here))
            sys_path_added = True
        try:
            import roles  # type: ignore
        finally:
            # Don't actually clean up — it's a sibling module; keeping
            # it on sys.path is fine for the rest of the process.
            pass
    except Exception as e:
        return {"ok": False, "reason": f"roles-unavailable:{e}"}

    current = roles.effective_role(channel, chat_key, uid)
    if current in ("admin", "member"):
        return {"ok": False, "reason": "already-elevated", "current": current}
    if current == "observer":
        # Idempotent — already observer; just refresh the disclosure record
        mark_seen(channel, chat_key, uid, action=ACTION_JOINED)
        return {"ok": True, "reason": "already-observer", "current": "observer"}

    # Self-grant observer (the only bundle for which self-grant is
    # allowed — bypasses the normal grant() check by going through a
    # dedicated audit event rather than ``grant.issued``).
    now = time.time()
    store = roles._store_path(channel, chat_key)
    data = roles._load_store(store)
    data[uid] = {
        "bundle": "observer",
        "granted_at": now,
        "expires_at": None,    # observer is indefinite by default
        "granted_by": uid,     # self-granted; explicit so audit is honest
        "channel": channel,
        "via": f"self-join:{via}",
        "reason": "self-join via /join",
    }
    roles._save_store(store, data)
    _audit("disclosure.joined",
           channel=channel, chat_key=str(chat_key), uid=uid,
           details={"bundle": "observer", "via": via})
    mark_seen(channel, chat_key, uid, action=ACTION_JOINED)
    return {"ok": True, "reason": "joined", "current": "observer"}


def pass_card(channel: str, chat_key: str, uid: str) -> dict:
    """User acknowledges the card without taking action. Records the
    decision so the daemon doesn't re-show the card on every message."""
    if not uid:
        return {"ok": False, "reason": "invalid-uid"}
    if _is_intrinsic_owner(channel, uid):
        return {"ok": False, "reason": "owner-already"}
    mark_seen(channel, chat_key, uid, action=ACTION_PASSED)
    return {"ok": True, "reason": "passed"}


# ── CLI (called by the JS slash-command handler) ──────────────────────

def _cli_state(channel: str, chat_key: str, uid: str) -> int:
    print(json.dumps(get_state(channel, chat_key, uid),
                     ensure_ascii=False, indent=2))
    return 0


def _cli_list(channel: str, chat_key: str) -> int:
    print(json.dumps(list_seen(channel, chat_key),
                     ensure_ascii=False, indent=2))
    return 0


def _cli_card(channel: str, owner_label: str,
              has_transcript: bool, lang: str) -> int:
    print(get_card_text(owner_label=owner_label, channel=channel,
                        has_observer_transcript=has_transcript, lang=lang))
    return 0


def _cli_seen(channel: str, chat_key: str, uid: str, action: str) -> int:
    try:
        entry = mark_seen(channel, chat_key, uid, action=action)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"ok": True, "entry": entry}, ensure_ascii=False))
    return 0


def _cli_join(channel: str, chat_key: str, uid: str) -> int:
    r = join(channel, chat_key, uid, via="cli")
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


def _cli_pass(channel: str, chat_key: str, uid: str) -> int:
    r = pass_card(channel, chat_key, uid)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


def _cli_main(argv: list[str]) -> int:
    """Subcommands:

      state    <channel> <chat_key> <uid>
      list     <channel> <chat_key>
      card     <channel> <owner_label> [<lang>] [transcript|no-transcript]
      seen     <channel> <chat_key> <uid> <action>
                                      (action ∈ pending|joined|passed|left)
      join     <channel> <chat_key> <uid>
      pass     <channel> <chat_key> <uid>
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_cli_main.__doc__ or "")
        return 0
    sub = argv[0].lower()
    if sub == "state":
        if len(argv) < 4:
            print(json.dumps({"ok": False,
                              "error": "usage: state <channel> <chat_key> <uid>"}))
            return 1
        return _cli_state(argv[1], argv[2], argv[3])
    if sub == "list":
        if len(argv) < 3:
            print(json.dumps({"ok": False,
                              "error": "usage: list <channel> <chat_key>"}))
            return 1
        return _cli_list(argv[1], argv[2])
    if sub == "card":
        if len(argv) < 3:
            print(json.dumps({"ok": False,
                              "error": "usage: card <channel> <owner_label> [<lang>] [transcript|no-transcript]"}))
            return 1
        lang = "de"
        has_transcript = False
        for a in argv[3:]:
            al = a.lower()
            if al in ("de", "en"):
                lang = al
            elif al == "transcript":
                has_transcript = True
            elif al == "no-transcript":
                has_transcript = False
        return _cli_card(argv[1], argv[2], has_transcript, lang)
    if sub == "seen":
        if len(argv) < 5:
            print(json.dumps({"ok": False,
                              "error": "usage: seen <channel> <chat_key> <uid> <action>"}))
            return 1
        return _cli_seen(argv[1], argv[2], argv[3], argv[4])
    if sub == "join":
        if len(argv) < 4:
            print(json.dumps({"ok": False,
                              "error": "usage: join <channel> <chat_key> <uid>"}))
            return 1
        return _cli_join(argv[1], argv[2], argv[3])
    if sub == "pass":
        if len(argv) < 4:
            print(json.dumps({"ok": False,
                              "error": "usage: pass <channel> <chat_key> <uid>"}))
            return 1
        return _cli_pass(argv[1], argv[2], argv[3])
    print(json.dumps({"ok": False, "error": f"unknown subcommand: {sub!r}"}))
    return 1


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
