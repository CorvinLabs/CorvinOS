"""proactive.py — Proactive Communication Layer primitive + governance gate (ADR-0553, Phase 1).

ONE governed choke point for every bot-INITIATED (proactive) out-of-band
message: task-progress pings, completion notes, digests, follow-ups,
heartbeats, system notices. A proactive message is one the user did NOT just
ask for in the current turn — it reaches them out-of-band, so it is subject to
a fail-closed governance gate that a normal reply is not.

:func:`emit_proactive` resolves routing once, runs the gate in a fixed order,
writes AT MOST ONE outbox envelope, audits the outcome content-free, and NEVER
raises — any internal failure degrades to ``EmitResult.ERROR`` with no send.

Gate order (fail-closed — the first failing step decides, no later step can
re-open a denied one):

  0. **Ship-dark.** ``proactive_communication`` flag OFF → ``denied`` /
     reason ``flag-off``, ZERO outbox writes. A default install cannot emit.
  1. **kind** validated against a CLOSED enum → unknown → ``denied`` /
     ``bad-kind``.
  2. **Consent.** ``proactive_consent.is_granted(tenant_id, channel, uid)``
     (Phase 0.5, owner carve-out) False → ``denied`` / ``no-consent``.
  3. **House-rules (L44).** The acceptable-use gate classifies the outbound
     text; not allowed (or the gate cannot run) → ``denied`` / ``house-rules``.
  4. **Disclosure.** The bot-disclosure card must have been shown to ``uid`` in
     this chat (owner is implicitly seen) → not seen → ``denied`` /
     ``no-disclosure``.
  5. **Rate / flood / quiet-hours.** A durable per-(tenant, channel, uid)
     rolling-window counter: over ``MAX_PER_WINDOW`` or inside quiet-hours →
     ``rate_limited``; a repeated ``dedup_key`` within the window coalesces
     (``rate_limited`` / ``coalesced``, no second send). Quiet-hours is a durable
     per-(tenant, channel, uid) window (``set_quiet_hours`` / ``clear_quiet_hours``,
     default OFF); a legacy global window (``QUIET_HOURS_START/END``) is also
     honoured.

On a full PASS: exactly one envelope carrying ``_proactive_contact: True`` +
``kind`` (+ ``voice_path`` when supplied) is written atomically into the shared
outbox the messenger daemons poll. → ``EmitResult.EMITTED``.

**Audit is ALWAYS attempted** (except the internal ``error`` degrade, which is a
no-send + log, no event): every gate outcome emits a content-free, hash-chained
``proactive.{emitted,denied,rate_limited}`` event carrying ``tenant_id``,
``channel``, ``kind``, the ``dedup_key`` **as a full sha256 hash** (never the raw
key), ``voice`` (bool), ``decision`` and a reason-CODE and ``lom`` — NEVER the
message text (GDPR/PII floor, L16/L34 convention).

**Voice — the single synthesis site (Phase 4, ADR-0554).** A caller declares a
``VoiceSpec`` (``mode: off|summary|verbatim``). When the gate passes, no
pre-computed ``voice_path`` was supplied and the mode is not ``off``,
emit_proactive is the ONE place TTS runs: it condenses/strips the text
(``build_voice_summary`` / ``<voice>`` override) and synthesizes ONE note,
auditing ``proactive.voice_synthesized`` (content-free: chars, mode, backend). A
pre-set ``voice_path`` (Phase 2, from the durable record) ALWAYS wins — no double
synthesis. TTS failure degrades to text-only + ``proactive.voice_skipped`` and
NEVER blocks the text. :func:`send_proactive` is the thin unsolicited sender
(``solicited=False``, ``voice="summary"`` by default) for free digests/follow-ups.

**tenant_id is REQUIRED and explicit** — there is no env-var fallback for the
consent / rate routing key (ADR-0007 console-routing rule: the writer here and
any reader must resolve to the SAME tenant store).

Phase 1 does NOT migrate the existing producers — it lands the primitive + gate
+ tests only. Producer migration onto this primitive is Phase 2 (ADR-0554).

**Phase 2 — solicited vs unsolicited (ADR-0553 amendment).** ``emit_proactive``
now accepts ``solicited: bool`` (default False). A *solicited* message is the
bot's response to an EXPLICIT user action in the recent past — a ``/task``
completion, a task-progress line, a mid-turn heartbeat: the user's own command
IS the consent. So ``solicited=True`` SKIPS the ship-dark flag gate (step 0),
the proactive-consent gate (step 2) AND the disclosure gate (step 4) — a
solicited response must arrive even on a default (flag-OFF, no-proactive-consent)
install, exactly as it did before it was routed through this choke point. It
STILL runs house-rules (step 3), rate/flood (step 5) and the content-free audit.
An *unsolicited* message (``solicited=False``, the default — digests, follow-ups,
free proactive contact, Phase 4) runs the FULL gate including flag + consent +
disclosure.

The poller-side delivery paths (``completion_notify.deliver_ready``,
``task_progress.deliver_progress``) route their already-built envelope through
here with ``solicited=True`` and ``envelope=<pre-built>`` so the exact
per-channel envelope shape (markers, provenance, ``_final``, ``cn_``/``tp_``
msg_id + filename) is preserved byte-for-byte; ``emit_proactive`` runs the gate,
attaches the record's pre-synthesized ``voice_path`` as the SINGLE delivery site,
writes exactly one envelope and audits — never re-synthesizing voice.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op on Windows
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("corvin.proactive")

# Make the sibling shared modules importable (consent / disclosure / house_rules
# live next to this file). Guarded insert — this process is long-running.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ── Result + closed kind enum ───────────────────────────────────────────────

class EmitResult(str, Enum):
    """The outcome of one :func:`emit_proactive` call.

    A ``str`` enum so ``EmitResult.EMITTED == "emitted"`` for ergonomic
    assertions and JSON serialisation. The per-call reason-CODE is not on the
    return value — it is recorded in the audit event (content-free).
    """
    EMITTED = "emitted"
    DENIED = "denied"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


# The CLOSED set of proactive message kinds. An unknown kind fails the gate
# (fail-closed) rather than being emitted with an unclassified purpose — a new
# kind is an explicit, reviewed addition here, never free-form caller input.
VALID_KINDS: frozenset[str] = frozenset({
    "completion",   # a background /task finished
    "progress",     # an intermediate progress/status update
    "digest",       # a scheduled/coalesced summary
    "follow_up",    # a proactive follow-up nudge
    "heartbeat",    # a liveness "still working" ping
    "system",       # an operator/system notice
})

# The ship-dark flag id (registered in feature_flags.REGISTRY, default OFF).
FLAG_ID = "proactive_communication"


# ── VoiceSpec — the single voice-synthesis contract (ADR-0554) ──────────────

# The CLOSED set of voice modes. ``off`` never synthesizes; ``summary`` condenses
# the text with build_voice_summary before TTS; ``verbatim`` speaks the text (or a
# ``<voice>…</voice>`` override) as-is. Any other value is treated as ``off``.
VALID_VOICE_MODES: frozenset[str] = frozenset({"off", "summary", "verbatim"})


@dataclass(frozen=True)
class VoiceSpec:
    """How ``emit_proactive`` should attach a voice note to this message.

    ``mode``: ``off`` | ``summary`` | ``verbatim``. Immutable — a caller declares
    intent, the primitive is the SINGLE synthesis site that acts on it. A
    pre-computed ``voice_path`` (Phase 2, from a durable record) ALWAYS wins over
    a spec: it is attached verbatim and no synthesis runs (no double synthesis).
    """
    mode: str = "off"


def _voice_mode(voice: "VoiceSpec | str | None") -> str:
    """Normalise a caller's ``voice`` argument to a valid mode string.

    Accepts a :class:`VoiceSpec`, a bare mode string (``"summary"`` — how
    ``send_proactive`` passes it), any object with a ``.mode`` attribute, or
    ``None`` (→ ``off``, the back-compat default for voice_path-only callers).
    Unknown values fail closed to ``off`` (no synthesis).
    """
    if voice is None:
        return "off"
    if isinstance(voice, VoiceSpec):
        mode = voice.mode
    elif isinstance(voice, str):
        mode = voice
    else:
        mode = getattr(voice, "mode", "off")
    mode = (mode or "off").strip().lower()
    return mode if mode in VALID_VOICE_MODES else "off"

# Rate / flood governance.
MAX_PER_WINDOW = int(os.environ.get("PROACTIVE_MAX_PER_WINDOW", "12") or 12)
RATE_WINDOW_S = float(os.environ.get("PROACTIVE_RATE_WINDOW_S", str(3600)) or 3600)
# Quiet hours: OFF by default (both None). When set (local-time hours 0-23) a
# proactive send inside [start, end) is rate_limited. A wrap-around window
# (start > end, e.g. 22..7) spans midnight.
QUIET_HOURS_START: int | None = None
QUIET_HOURS_END: int | None = None


# ── Path resolution (tenant-scoped; NO env fallback for tenant_id) ──────────

def _corvin_home() -> Path:
    """Locate the runtime root. Mirrors proactive_consent / disclosure."""
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
    """Filesystem-safe path component. A component longer than 64 chars is
    HASHED (prefix + sha1[:12]) instead of blindly truncated — two DISTINCT long
    ids (tenant / channel / uid) that share a 64-char prefix would otherwise
    collapse to the SAME store path and cross-contaminate each other's rate /
    consent / quiet-hours state. Never raises."""
    raw = "".join(ch if ch.isalnum() else "_" for ch in str(s))
    if not raw:
        return "anon"
    if len(raw) <= 64:
        return raw
    return raw[:51] + "_" + hashlib.sha1(str(s).encode("utf-8")).hexdigest()[:12]


def _ratelimit_path(tenant_id: str, channel: str) -> Path:
    """Per-(tenant, channel) durable rate-limit store. tenant_id REQUIRED."""
    safe_tenant = _safe_component(tenant_id or "_default")
    safe_channel = _safe_component(channel or "unknown")
    return (_corvin_home() / "tenants" / safe_tenant / "global"
            / "proactive_ratelimit" / f"{safe_channel}.json")


def _audit_path(tenant_id: str) -> Path:
    safe_tenant = _safe_component(tenant_id or "_default")
    return _corvin_home() / "tenants" / safe_tenant / "global" / "forge" / "audit.jsonl"


def _default_outbox_dir() -> Path:
    """The shared outbox every messenger daemon polls (mirrors the producers)."""
    return _HERE / "outbox"


# ── Hashing ─────────────────────────────────────────────────────────────────

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ── Audit (content-free, hash-chained, best-effort/never-raise) ─────────────

def _write_audit_event(tenant_id: str, event_type: str, details: dict[str, Any]) -> None:
    """Append ONE content-free proactive audit event to the tenant chain.

    Isolated behind this single function so a test can spy on it (reason /
    content-free assertions) and prove the mutation invariant: silence this and
    the audit tests go red. Best-effort — a write failure is logged, never
    raised.
    """
    try:
        repo = None
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is not None:
            forge_pkg = repo / "operator" / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))
        from forge.security_events import write_event  # type: ignore
    except Exception as exc:  # noqa: BLE001 — forge absent (minimal deploy) → skip
        _LOG.warning("proactive audit unavailable for %s: %s", event_type, exc)
        return
    try:
        write_event(_audit_path(tenant_id), event_type, details=details)
    except Exception as exc:  # noqa: BLE001 — audit is best-effort, never blocks
        _LOG.warning("proactive audit write failed for %s: %s", event_type, exc)


def _audit_outcome(*, tenant_id: str, channel: str, kind: str, dedup_key: str | None,
                   voice: bool, decision: str, reason: str, lom: str,
                   solicited: bool = False) -> None:
    """Build the content-free detail body and emit the outcome event.

    NEVER carries the message text. ``dedup_key`` is recorded ONLY as a full
    sha256 hash (or "" when absent) — never the raw key. ``solicited`` (a bool)
    records whether this emission answered an explicit user action (skipping the
    flag/consent/disclosure gates) — content-free, so an auditor can tell a
    consented digest apart from a /task completion.
    """
    details: dict[str, Any] = {
        "tenant_id": tenant_id,
        "channel": channel,
        "kind": kind,
        # str-coerce BEFORE sha256 — a non-str dedup_key (e.g. an int) must never
        # raise here: this audit runs AFTER the envelope is written, so a raise
        # would flip a delivered EMITTED into ERROR and trigger a resend.
        "dedup_key_hash": _sha256(str(dedup_key)) if dedup_key else "",
        "voice": bool(voice),
        "solicited": bool(solicited),
        "decision": decision,
        "reason": reason,
        "lom": lom,
    }
    event_type = {
        EmitResult.EMITTED.value: "proactive.emitted",
        EmitResult.DENIED.value: "proactive.denied",
        EmitResult.RATE_LIMITED.value: "proactive.rate_limited",
    }.get(decision, "proactive.denied")
    _write_audit_event(tenant_id, event_type, details)


# ── Voice as the SINGLE synthesis site (ADR-0554) ───────────────────────────
#
# emit_proactive is the ONE place a proactive voice note is synthesized. A caller
# declares intent via a VoiceSpec (mode summary|verbatim); when no pre-computed
# ``voice_path`` is supplied the primitive condenses/strips the text and runs TTS
# exactly once, right before the envelope is built (so a denied/rate-limited
# message never synthesizes). A pre-set ``voice_path`` (Phase 2, from the durable
# record) ALWAYS wins — no double synthesis. TTS is best-effort / never-raise: on
# any failure the message is delivered TEXT-ONLY (voice is an enhancement, never a
# delivery precondition). Both outcomes are audited content-free.
#
# The three helpers below are thin, module-level indirections so a test can patch
# them without importing the heavy ``adapter`` module.

def _extract_voice_override(text: str) -> tuple[str, str | None]:
    """Pull an optional ``<voice>…</voice>`` block out of *text* (never raises)."""
    try:
        from voice_tag import extract_voice_override  # type: ignore
        return extract_voice_override(text or "")
    except Exception as exc:  # noqa: BLE001 — override is optional, never fatal
        _LOG.warning("proactive voice-override extract failed: %s", exc)
        return text or "", None


def _build_voice_summary(text: str, *, override: str | None = None) -> str:
    """Condense *text* to a spoken summary (delegates to adapter). Patchable."""
    import adapter as _ad  # type: ignore  # heavy — imported only when synthesizing
    return _ad.build_voice_summary(text, override=override)


def _synthesize_voice_note(spoken: str) -> str | None:
    """Run TTS on *spoken* and return the OGG path, or None (delegates to adapter)."""
    import adapter as _ad  # type: ignore
    try:
        lang = _ad._resolve_voice_output_language(spoken) or "de"
    except Exception:  # noqa: BLE001 — language detection is best-effort
        lang = "de"
    path = _ad.synthesize_voice_note(spoken, lang=lang)
    return str(path) if path else None


def _audit_voice(tenant_id: str, channel: str, kind: str, *, decision: str,
                 mode: str, chars: int, backend: str, reason: str, lom: str) -> None:
    """Emit the content-free voice outcome event (synthesized / skipped).

    Carries ONLY coarse, content-free fields: the mode, the spoken CHAR COUNT
    (never the text), the backend hint and a reason-code. NEVER the message text
    or the summary.
    """
    details: dict[str, Any] = {
        "tenant_id": tenant_id,
        "channel": channel,
        "kind": kind,
        "mode": mode,
        "chars": int(chars),
        "backend": backend,
        "decision": decision,
        "reason": reason,
        "lom": lom,
    }
    event_type = ("proactive.voice_synthesized" if decision == "synthesized"
                  else "proactive.voice_skipped")
    _write_audit_event(tenant_id, event_type, details)


def _synthesize_voice_single_site(text: str, *, mode: str, tenant_id: str,
                                  channel: str, kind: str) -> str | None:
    """Synthesize ONE proactive voice note (single site). Never raises.

    ``mode`` is ``summary`` (condense via build_voice_summary) or ``verbatim``
    (speak the text / ``<voice>`` override as-is). Returns the voice_path on
    success (auditing ``proactive.voice_synthesized``) or None on any failure
    (auditing ``proactive.voice_skipped``) — the caller then delivers text-only.
    """
    lom = "proactive.voice_synth"
    try:
        visible, override = _extract_voice_override(text or "")
        if mode == "summary":
            spoken = _build_voice_summary(visible, override=override)
        else:  # verbatim — the override wins, else the visible text, spoken as-is
            spoken = override if override else visible
        spoken = (spoken or "").strip()
        if not spoken:
            _audit_voice(tenant_id, channel, kind, decision="skipped", mode=mode,
                         chars=0, backend="", reason="empty-summary", lom=lom)
            return None
        path = _synthesize_voice_note(spoken)
        if path:
            backend = (Path(str(path)).suffix.lstrip(".") or "unknown")
            _audit_voice(tenant_id, channel, kind, decision="synthesized",
                         mode=mode, chars=len(spoken), backend=backend,
                         reason="ok", lom=lom)
            return str(path)
        _audit_voice(tenant_id, channel, kind, decision="skipped", mode=mode,
                     chars=len(spoken), backend="", reason="no-engine", lom=lom)
        return None
    except Exception as exc:  # noqa: BLE001 — TTS failure ⇒ text-only, NEVER block
        _LOG.warning("proactive voice synth failed (text-only): %s", exc)
        _audit_voice(tenant_id, channel, kind, decision="skipped", mode=mode,
                     chars=0, backend="", reason="error", lom=lom)
        return None


# ── Gate helpers (each never-raises; failure = fail-closed deny) ────────────

def _consent_ok(tenant_id: str, channel: str, uid: str) -> bool:
    """Proactive-contact consent (Phase 0.5). Deny-by-default on any failure."""
    try:
        import proactive_consent  # type: ignore
        return bool(proactive_consent.is_granted(tenant_id, channel, uid))
    except Exception as exc:  # noqa: BLE001 — deny-by-default
        _LOG.warning("proactive consent check failed: %s", exc)
        return False


def _house_rules_allows(text: str, *, channel: str, chat_key: str) -> bool:
    """L44 acceptable-use gate over the outbound text. Fail-CLOSED: a gate that
    cannot run denies (the gate module itself fails to a deny-all policy)."""
    try:
        import house_rules  # type: ignore
        gate = house_rules.HouseRulesGate.from_repo()
        decision = gate.classify(text or "", channel=channel, chat_key=chat_key)
        return bool(decision.allowed)
    except Exception as exc:  # noqa: BLE001 — a broken gate must deny, never allow
        _LOG.warning("proactive house-rules check failed (fail-closed deny): %s", exc)
        return False


def _disclosure_shown(channel: str, chat_key: str, uid: str) -> bool:
    """Bot-disclosure card must have been shown to uid in this chat (owner is
    implicitly seen). Deny-by-default on any failure."""
    try:
        import disclosure  # type: ignore
        return bool(disclosure.has_seen(channel, chat_key, uid))
    except Exception as exc:  # noqa: BLE001 — deny-by-default
        _LOG.warning("proactive disclosure check failed: %s", exc)
        return False


# ── Rate-limit store (durable, atomic, tenant-scoped) ───────────────────────

def _load_rate_store(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_rate_store_atomic(path: Path, data: dict[str, dict]) -> bool:
    """Atomic tmp-replace write WITHOUT taking the flock (the caller already
    holds it — used inside :func:`_rate_check_and_record`'s single-lock RMW).
    Never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp_fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(tmp_fd, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())
        finally:
            os.close(tmp_fd)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        _LOG.warning("proactive rate store atomic write failed: %s", exc)
        return False


def _save_rate_store(path: Path, data: dict[str, dict]) -> bool:
    """Atomic write under a flock sidecar (mirrors proactive_consent). Used by
    the quiet-hours writers (an independent store). Never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(path.suffix + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return _write_rate_store_atomic(path, data)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except OSError as exc:
        _LOG.warning("proactive rate store write failed: %s", exc)
        return False


def _hour_in_window(hour: int, start: int, end: int) -> bool:
    """True iff local-time ``hour`` falls inside [start, end). Supports a
    wrap-around window spanning midnight (start > end, e.g. 22..7). An empty
    window (start == end) is never active."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _in_quiet_hours(now: float) -> bool:
    """True iff local-time hour of ``now`` is inside the GLOBAL quiet window.
    OFF by default (both bounds None). Kept single-arg for back-compat — the
    per-(tenant,channel,uid) window is resolved separately by
    :func:`_scope_quiet_hours_active`."""
    if QUIET_HOURS_START is None or QUIET_HOURS_END is None:
        return False
    return _hour_in_window(time.localtime(now).tm_hour,
                           QUIET_HOURS_START, QUIET_HOURS_END)


# ── Per-(tenant, channel, uid) quiet-hours window (durable, default OFF) ─────

def _quiet_hours_path(tenant_id: str, channel: str) -> Path:
    """Per-(tenant, channel) quiet-hours store. tenant_id REQUIRED (no fallback)."""
    safe_tenant = _safe_component(tenant_id or "_default")
    safe_channel = _safe_component(channel or "unknown")
    return (_corvin_home() / "tenants" / safe_tenant / "global"
            / "proactive_quiet_hours" / f"{safe_channel}.json")


def set_quiet_hours(tenant_id: str, channel: str, uid: str,
                    start: int, end: int) -> bool:
    """Configure a per-(tenant, channel, uid) quiet-hours window (local-time
    hours 0-23). A ``uid`` of ``"*"`` sets the channel-wide default. During the
    window a proactive send is ``rate_limited`` (reason ``quiet-hours``). Never
    raises; returns True on a successful write."""
    try:
        path = _quiet_hours_path(tenant_id, channel)
        store = _load_rate_store(path)
        store[str(uid)] = {"start": int(start) % 24, "end": int(end) % 24}
        return _save_rate_store(path, store)
    except Exception as exc:  # noqa: BLE001 — never raise
        _LOG.warning("set_quiet_hours failed: %s", exc)
        return False


def clear_quiet_hours(tenant_id: str, channel: str, uid: str) -> bool:
    """Remove a per-(tenant, channel, uid) quiet-hours window. Never raises."""
    try:
        path = _quiet_hours_path(tenant_id, channel)
        store = _load_rate_store(path)
        if str(uid) in store:
            del store[str(uid)]
            return _save_rate_store(path, store)
        return True
    except Exception as exc:  # noqa: BLE001 — never raise
        _LOG.warning("clear_quiet_hours failed: %s", exc)
        return False


def _scope_quiet_window(tenant_id: str, channel: str, uid: str) -> tuple[int, int] | None:
    """Resolve the effective quiet window for a uid (uid entry, else the ``"*"``
    channel default), or None when no window is configured. Never raises."""
    try:
        store = _load_rate_store(_quiet_hours_path(tenant_id, channel))
        rec = store.get(str(uid)) or store.get("*")
        if not isinstance(rec, dict):
            return None
        start, end = rec.get("start"), rec.get("end")
        if start is None or end is None:
            return None
        return int(start), int(end)
    except Exception:  # noqa: BLE001 — no window on any failure
        return None


def _scope_quiet_hours_active(now: float, *, tenant_id: str, channel: str,
                              uid: str) -> bool:
    """True iff a per-(tenant, channel, uid) quiet window is configured AND the
    local-time hour of ``now`` is inside it. Never raises."""
    win = _scope_quiet_window(tenant_id, channel, uid)
    if win is None:
        return False
    return _hour_in_window(time.localtime(now).tm_hour, win[0], win[1])


def _rate_check_and_record(tenant_id: str, channel: str, uid: str,
                           dedup_key: str | None, *, now: float) -> tuple[bool, str]:
    """Check + atomically record one proactive emission under the rolling window.

    Returns ``(allowed, reason)``. When ``allowed`` is False the reason is one of
    ``coalesced`` / ``quiet-hours`` / ``flood``. When True the emission has been
    recorded (count incremented, dedup_key remembered). Never raises — on any
    store failure it ALLOWS (fail-open on the rate layer only: a broken counter
    must not silently muzzle a consented, house-rules-cleared, disclosed
    message; the stricter gates already passed).
    """
    try:
        if _in_quiet_hours(now) or _scope_quiet_hours_active(
                now, tenant_id=tenant_id, channel=channel, uid=uid):
            return False, "quiet-hours"
        path = _ratelimit_path(tenant_id, channel)
        # Hold ONE flock across the ENTIRE read-modify-write (load → check →
        # record). A previous split (lock-free _load_rate_store, then a separate
        # locked _save_rate_store) let two concurrent emits both read count=N-1,
        # both pass the flood check, and both write count=N — overshooting
        # MAX_PER_WINDOW. The single lock makes check+record atomic.
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(path.suffix + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            store = _load_rate_store(path)
            rec = store.get(uid) or {}
            window_start = float(rec.get("window_start", 0) or 0)
            if now - window_start > RATE_WINDOW_S:
                # window rolled over — reset
                rec = {"window_start": now, "count": 0, "dedup_keys": {}}
            dedup_keys = rec.get("dedup_keys") or {}
            if dedup_key and dedup_key in dedup_keys:
                # same key in the window → coalesce (do not send, do not increment)
                return False, "coalesced"
            if int(rec.get("count", 0)) >= MAX_PER_WINDOW:
                return False, "flood"
            rec["count"] = int(rec.get("count", 0)) + 1
            if dedup_key:
                dedup_keys[dedup_key] = now
                rec["dedup_keys"] = dedup_keys
            rec.setdefault("window_start", now)
            store[uid] = rec
            _write_rate_store_atomic(path, store)  # no flock — we hold it
            return True, "ok"
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except Exception as exc:  # noqa: BLE001 — rate layer fails OPEN (see docstring)
        _LOG.warning("proactive rate check failed (allowing): %s", exc)
        return True, "rate-error"


# ── Outbox envelope (atomic write, poller-independent) ──────────────────────

def _build_envelope(*, channel: str, chat_id: str | int | None, to: str | None,
                    tenant_id: str, text: str, kind: str,
                    voice_path: str | None) -> dict:
    """Build the outbox envelope with the correct per-channel routing key.

    Carries ``_proactive_contact: True`` (so proactive_consent's hard-kill purge
    recognises it) + ``kind``; attaches ``voice_path`` when supplied.
    """
    env: dict = {
        "msg_id": f"pc_{uuid.uuid4().hex[:16]}",
        "channel": channel,
        "text": text,
        "_proactive_contact": True,
        "kind": kind,
        "ts": time.time(),
    }
    if voice_path:
        env["voice_path"] = str(voice_path)
    # Route: chat_id for most channels, `to` (JID) for whatsapp. chat_id stays a
    # STRING — never int-coerce (a Discord snowflake > 2^53 loses precision as a
    # JSON number). Mirrors completion_notify._envelope_for.
    if chat_id is not None and chat_id != "":
        env["chat_id"] = str(chat_id)
    if to:
        env["to"] = to
    elif channel == "whatsapp" and chat_id:
        env["to"] = str(chat_id)
    if tenant_id:
        env["tenant_id"] = tenant_id
    return env


def _write_envelope(outbox_dir: str | Path, env: dict,
                    *, file_name: str | None = None) -> bool:
    """Atomically write ONE envelope into the outbox. Never raises.

    ``file_name`` lets a migrated delivery path pin the exact outbox filename it
    used before (e.g. ``cn_<id>_<hex>.json`` / ``tp_<id>_<hex>.json``) so the
    consumer daemons + the existing tests see a byte-identical file name; when
    omitted the primitive's own ``<msg_id>.json`` is used (Phase-4 unsolicited).
    """
    try:
        outbox = Path(outbox_dir)
        outbox.mkdir(parents=True, exist_ok=True)
        out_file = outbox / (file_name or f"{env['msg_id']}.json")
        tmp = out_file.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # envelope carries routing PII
        except OSError:
            pass
        tmp.replace(out_file)
        return True
    except Exception as exc:  # noqa: BLE001 — never raise
        _LOG.warning("proactive outbox write failed: %s", exc)
        return False


# ── The primitive ───────────────────────────────────────────────────────────

def emit_proactive(*, channel: str, chat_id: str | int | None = None,
                   to: str | None = None, tenant_id: str, uid: str, text: str,
                   kind: str, voice_path: str | None = None,
                   voice: "VoiceSpec | str | None" = None,
                   dedup_key: str | None = None,
                   outbox_dir: str | Path | None = None,
                   solicited: bool = False,
                   envelope: dict | None = None,
                   out_file_name: str | None = None) -> EmitResult:
    """Governed proactive-message emission. Fail-closed, content-free-audited,
    NEVER raises.

    See the module docstring for the full gate order. Returns an
    :class:`EmitResult`; the reason-CODE is recorded in the audit event, never
    on the return value.

    ``tenant_id`` and ``uid`` are REQUIRED and explicit (no env fallback for the
    consent / rate routing key). ``chat_id`` routes most channels; ``to`` routes
    whatsapp. ``voice_path`` (optional) is a pre-computed note path attached onto
    the envelope (SINGLE delivery site — never re-synthesized). ``dedup_key``
    coalesces repeats within the rate window. ``outbox_dir`` overrides the shared
    outbox (tests).

    ``solicited`` (Phase 2): when True this emission answers an EXPLICIT user
    action (a /task completion/progress line, a heartbeat) — the command IS the
    consent, so the flag (0), consent (2) and disclosure (4) gates are SKIPPED;
    house-rules (3), rate (5) and audit still apply. Default False runs the FULL
    gate (unsolicited digests/follow-ups).

    ``envelope`` (Phase 2 migration): a pre-built outbox envelope from a migrated
    delivery path. When given it is written verbatim (with ``voice_path``
    attached if not already present) INSTEAD of the primitive's own
    ``_build_envelope`` — so the caller's exact per-channel shape (markers,
    provenance, ``_final``, msg_id) is preserved. ``out_file_name`` pins the exact
    outbox filename the caller used before.

    ``voice`` (Phase 4, ADR-0554): a :class:`VoiceSpec` (or a bare mode string
    ``"summary"`` / ``"verbatim"`` / ``"off"``). emit_proactive is the SINGLE
    voice-synthesis site: when the gate passes, ``voice_path`` is None and the
    mode is not ``off``, the text is condensed/spoken and TTS runs exactly once
    (auditing ``proactive.voice_synthesized``). A pre-set ``voice_path`` ALWAYS
    wins (no double synthesis). TTS failure ⇒ text-only + ``proactive.voice_skipped``
    (never blocks the text). Default None ⇒ off (voice_path-only callers unchanged).
    """
    lom = "proactive.emit_proactive"
    voice_present = bool(voice_path)
    now = time.time()
    outbox = outbox_dir if outbox_dir is not None else _default_outbox_dir()
    # A safe chat_key for the disclosure/store scope (the routing id).
    chat_key = str(chat_id) if chat_id not in (None, "") else str(to or "")

    def _audit(decision: str, reason: str) -> None:
        _audit_outcome(tenant_id=tenant_id, channel=channel, kind=str(kind),
                       dedup_key=dedup_key, voice=voice_present, decision=decision,
                       reason=reason, lom=lom, solicited=solicited)

    try:
        # 0. Ship-dark — flag OFF → deny, ZERO outbox writes. SKIPPED when the
        #    message is solicited (an explicit user action is its own consent).
        if not solicited and not _flag_on(tenant_id):
            _audit(EmitResult.DENIED.value, "flag-off")
            return EmitResult.DENIED

        # 1. kind — closed enum, fail-closed (always).
        if kind not in VALID_KINDS:
            _audit(EmitResult.DENIED.value, "bad-kind")
            return EmitResult.DENIED

        # 2. Consent (deny-by-default; owner carve-out). SKIPPED when solicited.
        if not solicited and not _consent_ok(tenant_id, channel, uid):
            _audit(EmitResult.DENIED.value, "no-consent")
            return EmitResult.DENIED

        # 3. House-rules (L44), fail-closed — ALWAYS, solicited or not.
        if not _house_rules_allows(text, channel=channel, chat_key=chat_key):
            _audit(EmitResult.DENIED.value, "house-rules")
            return EmitResult.DENIED

        # 4. Disclosure — card must have been shown. SKIPPED when solicited.
        if not solicited and not _disclosure_shown(channel, chat_key, uid):
            _audit(EmitResult.DENIED.value, "no-disclosure")
            return EmitResult.DENIED

        # 5. Rate / flood / quiet-hours + dedup coalescing — ALWAYS.
        allowed, reason = _rate_check_and_record(tenant_id, channel, uid, dedup_key, now=now)
        if not allowed:
            _audit(EmitResult.RATE_LIMITED.value, reason)
            return EmitResult.RATE_LIMITED

        # PASS — voice single synthesis site (ADR-0554). A pre-set voice_path
        # (Phase 2, from the durable record) ALWAYS wins → no synthesis. Otherwise
        # a VoiceSpec mode summary|verbatim synthesizes exactly once here; TTS
        # failure degrades to text-only (never blocks). Runs AFTER the gate so a
        # denied/rate-limited message never synthesizes.
        final_voice_path = voice_path
        if final_voice_path is None:
            mode = _voice_mode(voice)
            if mode != "off":
                final_voice_path = _synthesize_voice_single_site(
                    text, mode=mode, tenant_id=tenant_id, channel=channel,
                    kind=str(kind))
        voice_present = bool(final_voice_path)

        # Write exactly ONE envelope, then audit emitted. A migrated delivery
        # path supplies its own pre-built envelope; attach voice here (single
        # site) and pin the caller's original filename.
        if envelope is not None:
            env = dict(envelope)
            if final_voice_path and not env.get("voice_path"):
                env["voice_path"] = str(final_voice_path)
        else:
            env = _build_envelope(channel=channel, chat_id=chat_id, to=to,
                                  tenant_id=tenant_id, text=text, kind=kind,
                                  voice_path=final_voice_path)
        if not _write_envelope(outbox, env, file_name=out_file_name):
            # The envelope could not be written — no message left the system, so
            # this is an internal error (no-send + log), NOT an emitted event.
            _LOG.warning("proactive emit: envelope write failed after gate pass")
            return EmitResult.ERROR
        # The envelope is ALREADY written — the message HAS left the system. The
        # outcome audit is best-effort from here: an audit failure must NOT flip
        # a delivered EMITTED into ERROR (which the caller would treat as a
        # not-delivered retry → duplicate send). Log and still return EMITTED.
        try:
            _audit(EmitResult.EMITTED.value, "ok")
        except Exception as _aexc:  # noqa: BLE001 — delivered; audit is best-effort
            _LOG.warning("proactive post-write audit failed (delivered anyway): %s",
                         _aexc)
        return EmitResult.EMITTED

    except Exception as exc:  # noqa: BLE001 — never-raise: any fault → error, logged.
        _LOG.warning("proactive emit_proactive internal error: %s", exc)
        return EmitResult.ERROR


def send_proactive(*, channel: str, chat_id: str | int | None = None,
                   uid: str, tenant_id: str, text: str, kind: str,
                   voice: "VoiceSpec | str | None" = "summary",
                   dedup_key: str | None = None, to: str | None = None,
                   outbox_dir: str | Path | None = None) -> EmitResult:
    """Send ONE free, UNSOLICITED proactive message (Phase 4, ADR-0553/0554).

    This is how Corvin contacts a user *on its own* — a scheduled digest, a
    follow-up nudge — with NO user action in the current turn to authorise it. It
    is a thin wrapper over :func:`emit_proactive` pinned to ``solicited=False``,
    so it runs the FULL fail-closed gate: ship-dark flag (``proactive_communication``,
    default OFF) → kind → proactive-consent (owner carve-out) → house-rules (L44)
    → disclosure → rate/flood + per-scope quiet-hours + dedup coalescing. With the
    flag OFF every unsolicited send is ``denied`` with ZERO outbox writes
    (ship-dark). ``voice`` defaults to ``"summary"`` — the single synthesis site
    condenses the text and attaches a voice note (best-effort; TTS failure ⇒
    text-only). ``dedup_key`` coalesces repeats within the rate window (a digest
    keyed by day is delivered once). Returns an :class:`EmitResult`; never raises.
    """
    return emit_proactive(channel=channel, chat_id=chat_id, to=to,
                          tenant_id=tenant_id, uid=uid, text=text, kind=kind,
                          voice=voice, dedup_key=dedup_key, outbox_dir=outbox_dir,
                          solicited=False)


def _flag_on(tenant_id: str = "_default") -> bool:
    """Resolve the ship-dark ``proactive_communication`` flag for the EXPLICIT
    ``tenant_id`` of this emission (ADR-0007 console-routing rule: the flag is
    resolved against the SAME tenant the consent / rate / audit stores route to,
    NOT an ambient ``CORVIN_TENANT_ID`` env var — a background poller may run in a
    process whose env tenant differs from the record's originating tenant).

    Absent/unreadable console package → OFF (fail-to-off), matching the bridge
    ``_bg_flag`` contract. Never raises."""
    try:
        from corvin_core import feature_flags as _ff  # type: ignore
        tid = tenant_id or "_default"
        return bool(_ff.is_enabled(FLAG_ID, tid))
    except Exception as exc:  # noqa: BLE001 — console package absent → feature off
        _LOG.warning("proactive flag unresolved (%s) — OFF", exc)
        return False
