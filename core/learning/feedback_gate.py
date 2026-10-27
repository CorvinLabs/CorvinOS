"""ADR-0534 — the one trust gate every FEEDBACK learning event passes.

``event_store.EventStore.write_event`` calls ``admit()`` for every
``EventType.FEEDBACK`` event BEFORE the audit-first write. That store is the
single place feedback becomes learnable state (operator ratings, the
security-orchestrator and workflow-optimizer feedback handlers all write
there), so gating the primitive covers every producer, present and future —
gating sixteen console endpoints one by one would miss the seventeenth.

Order: throttle (Layer 1) → reality check (Layer 2) → trust weight (Layer 3).
Fail-closed: a rejection is audited and raises ``FeedbackRejected``; nothing
reaches the store. If the audit record itself cannot commit, the
``RuntimeError`` from ``core_audit_event`` propagates — feedback is never
accepted (or silently dropped) without its chain record.

**Subject.** One canonical id per subject (``canonical_subject``): Layer 2
already maps ``skill:<id>`` and a bare ``<id>`` to the same skill, so the
throttle, the trust weight and the ledger key on that same form — two
spellings never get two rate budgets or two trust histories.

**Source.** The trust weight belongs to the PRODUCER of the signal, not to
the subject: one faulty producer must not lower the weight of an honest one
on the same skill. A caller that knows the authenticated principal passes
``source=`` (the console passes its consent subject); otherwise the event's
``lom`` (the producing code location) is the source. Only a tenant-scoped
sha256 of it (``source_key``) is stored or audited, never the raw value.

**Ledger.** Decisions are appended to a content-free per-tenant ledger
(``<tenant>/learning/feedback_gate/decisions.jsonl``: ts, subject, source_key,
decision, reason; read by the anomaly monitor) AND to a per-subject index
(``feedback_gate/subjects/<sha256(subject)>/all.jsonl`` plus one
``<source_key>.jsonl`` per producer). Layer 1 counts over the subject's own
file, Layer 3 over the (subject, source) file, each reading a bounded tail —
so junk on other subjects, or another producer's flood, cannot push a
subject's or a producer's history out of view. All files are append-only;
the audit chain stays the record of truth.

**Atomicity.** read → decide → append runs under an exclusive file lock on
the subject (``subjects/<h>/.lock``; ``fcntl.flock`` / ``msvcrt.locking``),
so concurrent threads and processes (bridge and console) are serialised per
subject. A lock that cannot be taken refuses the feedback (fail-closed).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import time
from time import monotonic as _monotonic, sleep as _sleep
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from core.learning import adversarial_detector, feedback_throttle, reality_check
from core.learning.learning_events import EventType, LearningEvent

LEDGER_TAIL = 500  # global-ledger lines read back by the anomaly monitor
SUBJECT_TAIL = 256  # bounded per-subject / per-source read; ≥ max_per_window and ≥ trust WINDOW
LOCK_TIMEOUT_S = 10.0

SOURCE_SIGNAL_KEY = "producer"  # in-process producer attribution; stripped before persisting
UNATTRIBUTED = "unattributed"

# Audit event names (registered in security_events EVENT_SEVERITY + allowlist).
EV_THROTTLED = "learning.feedback_throttled"
EV_REJECTED = "learning.feedback_rejected"
EV_MISMATCH = "learning.feedback_reality_mismatch"
EV_ADVERSARIAL = "learning.adversarial_feedback_detected"
EV_ACCEPTED = "learning.feedback_accepted"


class FeedbackRejected(ValueError):
    """The gate refused a feedback signal; ``reason`` is a closed enum."""

    def __init__(self, reason: str, audit_ref: str):
        super().__init__(f"feedback rejected: {reason}")
        self.reason = reason
        self.audit_ref = audit_ref


def canonical_subject(subject: str) -> str:
    """The one id the gate keys a subject on: ``skill:X`` and ``X`` are both ``X``.

    A subject Layer 2 cannot map to a skill (``tool:…``, pattern ids) keeps its
    raw form — it is rejected as unverifiable anyway, but it is still throttled.
    """
    subject = subject or ""
    skill = reality_check.skill_of(subject)
    return skill if skill is not None else subject


def source_key_of(tenant_id: str, source: Optional[str]) -> str:
    """Tenant-scoped pseudonymous key of a producer; the raw source is never stored."""
    raw = source if isinstance(source, str) and source else UNATTRIBUTED
    digest = hashlib.sha256(f"{tenant_id}\x00{raw}".encode("utf-8")).hexdigest()
    return "src_" + digest[:24]


def _subject_hash(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]


def _tail_lines(path: Path, n: int) -> list[str]:
    """The last ``n`` lines of ``path``, reading backwards in blocks (bounded)."""
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        return []
    with fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        buf = b""
        while pos > 0 and buf.count(b"\n") <= n:
            step = min(8192, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
    lines = buf.decode("utf-8", errors="replace").splitlines()
    return lines[-n:] if n > 0 else []


def _parse(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _append_line(path: Path, line: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # One O_APPEND write per record: concurrent writers interleave whole lines.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


class DecisionLedger:
    def __init__(self, learning_root: Path):
        self.root = Path(learning_root) / "feedback_gate"
        self.path = self.root / "decisions.jsonl"
        self.index_dir = self.root / "subjects"

    def _subject_dir(self, subject: str) -> Path:
        return self.index_dir / _subject_hash(canonical_subject(subject))

    def tail(self, n: int = LEDGER_TAIL) -> list[dict]:
        """The tenant-wide tail (all subjects) — for the anomaly monitor, not the gate."""
        return _parse(_tail_lines(self.path, n))

    def subject_history(self, subject: str, n: int = SUBJECT_TAIL) -> list[dict]:
        """The last ``n`` decisions on ``subject`` (any producer)."""
        return _parse(_tail_lines(self._subject_dir(subject) / "all.jsonl", n))

    def source_history(self, subject: str, src_key: str, n: int = SUBJECT_TAIL) -> list[dict]:
        """The last ``n`` decisions on ``subject`` by the producer ``src_key``."""
        return _parse(_tail_lines(self._subject_dir(subject) / f"{src_key}.jsonl", n))

    @contextmanager
    def locked(self, subject: str, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
        """Exclusive cross-thread, cross-process lock on one subject's history.

        Raises ``RuntimeError`` when the lock cannot be taken within
        ``timeout_s`` — the caller refuses the feedback then (fail-closed).
        """
        d = self._subject_dir(subject)
        d.mkdir(parents=True, exist_ok=True)
        fh = open(d / ".lock", "a+b")
        try:
            try:
                import fcntl  # noqa: PLC0415
            except ImportError:  # Windows
                fcntl = None  # type: ignore[assignment]
            deadline = _monotonic() + timeout_s
            while True:
                try:
                    if fcntl is not None:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:  # pragma: no cover — Windows
                        import msvcrt  # noqa: PLC0415
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if _monotonic() >= deadline:
                        raise RuntimeError("feedback gate: subject lock busy") from None
                    _sleep(0.005)
            yield
        finally:
            fh.close()  # closing drops the flock / msvcrt lock

    def append(self, *, ts: float, subject: str, decision: str, reason: str,
               source_key: str = "") -> None:
        subject = canonical_subject(subject)
        src = source_key or source_key_of("", None)
        line = (json.dumps({"ts": ts, "subject": subject, "source_key": src,
                            "decision": decision, "reason": reason},
                           separators=(",", ":")) + "\n").encode("utf-8")
        d = self._subject_dir(subject)
        _append_line(d / f"{src}.jsonl", line)
        _append_line(d / "all.jsonl", line)
        _append_line(self.path, line)


_ADMITTED_MAX = 1024
_admitted: "OrderedDict[str, float]" = OrderedDict()
_admitted_lock = threading.Lock()


def _remember_admitted(event_id: str, weight: float) -> None:
    with _admitted_lock:
        _admitted[event_id] = weight
        while len(_admitted) > _ADMITTED_MAX:
            _admitted.popitem(last=False)


def admitted_trust_weight(event_id: str) -> Optional[float]:
    """The trust weight this process's gate stamped on an accepted event, or ``None``.

    For a caller that writes through ``EventStore.write_event`` (which returns
    only the ``audit_ref``) and must scale what it does next by the weight —
    ``method_discovery_api.submit_feedback`` scales its optimizer step. ``None``
    means "not admitted here": the caller must not act as if the weight were 1.
    """
    with _admitted_lock:
        return _admitted.get(event_id)


def _audit(event: str, tenant_id: str, details: dict) -> str:
    from core.learning.event_persistence import core_audit_event  # noqa: PLC0415
    return core_audit_event(event, tenant_id=tenant_id, details=details)


def _resolve_source(event: LearningEvent, source: Optional[str]) -> Optional[str]:
    if isinstance(source, str) and source:
        return source
    claimed = (event.signal or {}).get(SOURCE_SIGNAL_KEY)
    if isinstance(claimed, str) and claimed:
        return claimed
    return event.lom or None


def admit(event: LearningEvent, store, *, now: Optional[float] = None,
          rate: Optional[feedback_throttle.RateLimitConfig] = None,
          subject_verified: bool = False,
          source: Optional[str] = None,
          ) -> LearningEvent:
    """Return the event to persist (with ``signal.trust_weight``) or raise.

    ``subject_verified`` is an in-process attestation by a caller that has
    checked the subject against its own ground truth (``grade_pattern``
    checks the pattern node exists). It replaces Layer 2 only; throttle and
    trust weight still apply. In-process code is attributed, not distrusted
    (CLAUDE.md, plugin perimeter); the gate's adversary is the SIGNAL.

    ``source`` is the authenticated producer (the console passes its consent
    subject). Without it, ``signal["producer"]`` and then ``event.lom`` are
    used. The raw value is hashed into ``source_key`` and never persisted:
    ``signal["producer"]`` is removed from the returned event.
    """
    if event.event_type is not EventType.FEEDBACK:
        return event
    rate = rate or feedback_throttle.DEFAULT_RATE_LIMIT
    tenant = event.tenant_id
    subject = canonical_subject(event.skill_id)
    src = source_key_of(tenant, _resolve_source(event, source))
    ledger = DecisionLedger(store.tenant_home / "learning")
    base = {"tenant_id": tenant, "skill_id": subject, "feedback_event_id": event.event_id,
            "source_key": src}

    with ledger.locked(subject):
        # The clock is read UNDER the lock: a waiter that read it before the lock
        # would see the decisions written meanwhile as "in the future" and skip them.
        now = time.time() if now is None else now
        def reject(audit_name: str, reason: str, extra: dict) -> FeedbackRejected:
            ref = _audit(audit_name, tenant, {**base, "reason": reason, **extra})
            ledger.append(ts=now, subject=subject, decision="rejected", reason=reason,
                          source_key=src)
            return FeedbackRejected(reason, ref)

        throttle = feedback_throttle.check_rate(
            ledger.subject_history(subject), subject=subject, now=now, config=rate)
        if not throttle.allowed:
            raise reject(EV_THROTTLED, "rate_limit_exceeded", {
                "signals_in_window": throttle.signals_in_window,
                "max_allowed": rate.max_per_window, "window_sec": rate.window_sec})

        reality = (reality_check.RealityResult(True, "verified_by_caller") if subject_verified
                   else reality_check.validate(event, store))
        if not reality.is_valid:
            name = EV_MISMATCH if reality.reason == "output_hash_mismatch" else EV_REJECTED
            raise reject(name, reality.reason, {"executions_in_window": reality.executions_in_window})

        trust = adversarial_detector.assess(ledger.source_history(subject, src), subject=subject)
        if trust.alert:
            _audit(EV_ADVERSARIAL, tenant, {**base, "trust_weight": trust.trust_weight,
                                            "rejection_count": trust.rejection_count,
                                            "accepted_count": trust.accepted_count,
                                            "reason": "review_feedback_source"})
        _audit(EV_ACCEPTED, tenant, {**base, "trust_weight": trust.trust_weight,
                                     "executions_in_window": reality.executions_in_window})
        ledger.append(ts=now, subject=subject, decision="accepted", reason="valid", source_key=src)
    _remember_admitted(event.event_id, trust.trust_weight)
    signal = dict(event.signal or {})
    signal.pop(SOURCE_SIGNAL_KEY, None)
    signal["trust_weight"] = trust.trust_weight
    return dataclasses.replace(event, signal=signal)
