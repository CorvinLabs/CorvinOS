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
the audit chain stays the record of truth. ``decisions.jsonl`` is written
FIRST, the per-subject index and the producer file only after it took the line
(review c120 r3_s1-6); a torn tail line is skipped and never swallows the next
record, and a failed or short write to a per-subject file is cut back to its
previous size. If a ledger append fails after ``learning.feedback_accepted``
committed, ``learning.feedback_not_stored`` (ids and enums only) is chained,
the feedback is refused (``ledger_write_failed``) and a ``void`` line naming
the entry's id is appended to every file that already took the "accepted"
line — readers drop the voided entry, so the throttle, the producer's trust
and the anomaly monitor never count refused feedback as accepted (best
effort: a void that cannot be written either leaves the accepted line).

**The caller's write is part of the admission (LRN-9).** "accepted" is written
before the caller stores the feedback. ``EventStore.write_event`` therefore runs
its chain + disk write inside :func:`admission`, and the stream4 route calls
:func:`void_admission` when its own audit record or history append fails: the
accepted entry is voided in every ledger file and ``learning.feedback_not_stored``
is chained with the failing ``stage``. Feedback that was never stored is never
counted as accepted.

**Retention and erasure.** :func:`prune_ledgers` drops lines older than
``RETENTION_DAYS`` (90; run daily by ``core/background/learning_host.py``, chained
as ``learning.retention`` scope ``feedback_gate``). :func:`erase_producer` removes a
GDPR Art. 17 subject's lines (their ``source_key`` is recomputable from the subject
id, so it is personal data); the L36 ``LearningEventHandler`` calls it. Both
rewrite a file under the lock its writers take — ``decisions.jsonl`` appends take
``feedback_gate/.decisions.lock`` for that reason.

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
EV_NOT_STORED = "learning.feedback_not_stored"
REASON_NOT_STORED = "ledger_write_failed"


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


#: Decision value of a line that cancels an earlier entry (``voids``: its id).
DECISION_VOID = "void"


def _parse(lines: list[str]) -> list[dict]:
    """Parsed decisions; torn lines skipped, voided entries and the void lines
    themselves dropped (review c120 r3_s1-6)."""
    out = []
    voided: set[str] = set()
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("decision") == DECISION_VOID:
            if isinstance(rec.get("voids"), str):
                voided.add(rec["voids"])
            continue
        out.append(rec)
    return [r for r in out if r.get("id") not in voided] if voided else out


def _append_line(path: Path, line: bytes, *, exclusive: bool = False) -> None:
    """Append one record line (O_APPEND; concurrent writers interleave whole lines).

    A file whose tail is a torn line (a writer that died on a full disk) gets a
    newline first, so the torn bytes stay their own unparseable line and never
    swallow this record (review c120 r2_s1-9c). ``exclusive``: the caller holds
    the lock that serialises every writer of this file (the per-subject index),
    so a failed or short write is cut back to the previous size — the file never
    keeps half a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        start = os.fstat(fd).st_size
        if start > 0:
            os.lseek(fd, start - 1, os.SEEK_SET)  # O_APPEND: writes still go to the end
            if os.read(fd, 1) != b"\n":
                line = b"\n" + line
        try:
            view = memoryview(line)
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise OSError("feedback ledger append wrote 0 bytes")
                view = view[n:]
        except BaseException:
            if exclusive:
                try:
                    os.ftruncate(fd, start)
                except OSError:
                    pass
            raise
    finally:
        os.close(fd)


@contextmanager
def _flock(lock_path: Path, timeout_s: float, busy: str) -> Iterator[None]:
    """Exclusive cross-thread, cross-process lock on ``lock_path`` (``fcntl.flock`` /
    ``msvcrt.locking``); ``RuntimeError(busy)`` when it is not taken within ``timeout_s``."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
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
                    raise RuntimeError(busy) from None
                _sleep(0.005)
        yield
    finally:
        fh.close()  # closing drops the flock / msvcrt lock


def _rewrite_lines(path: Path, keep) -> int:
    """Rewrite ``path`` with the lines ``keep(record_or_None)`` accepts; return lines dropped.

    Atomic and private: ``mkstemp`` in the same directory, fsync, 0600, ``os.replace``; an
    empty remainder removes the file. The caller holds the lock every writer of the file
    takes. A torn line (``None``) is offered to ``keep`` like any other.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return 0
    kept: list[str] = []
    dropped = 0
    for line in raw:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            rec = None
        if keep(rec if isinstance(rec, dict) else None):
            kept.append(line)
        else:
            dropped += 1
    if not dropped:
        return 0
    if not kept:
        path.unlink()
        return dropped
    import tempfile  # noqa: PLC0415
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(kept) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dropped


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
        with _flock(d / ".lock", timeout_s, "feedback gate: subject lock busy"):
            yield

    @contextmanager
    def _decisions_locked(self, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
        """The tenant-wide ``decisions.jsonl`` is shared by every subject: an append
        takes this lock too, so the retention / erasure rewrite (temp file + replace)
        can never drop a line appended to the old inode meanwhile (LRN-13/LRN-24).
        Order is always subject lock → decisions lock; nothing takes them reversed."""
        with _flock(self.root / ".decisions.lock", timeout_s, "feedback gate: ledger lock busy"):
            yield

    def _append_to(self, path: Path, line: bytes, exclusive: bool) -> None:
        if path == self.path:
            with self._decisions_locked():
                _append_line(path, line, exclusive=exclusive)
        else:
            _append_line(path, line, exclusive=exclusive)

    def append(self, *, ts: float, subject: str, decision: str, reason: str,
               source_key: str = "", void_on_failure: bool = False) -> str:
        """Append one decision to decisions.jsonl, then all.jsonl, then the
        producer's file (review c120 r3_s1-6: the per-subject files only after
        the tenant-wide ledger took the line). ``void_on_failure`` (an
        "accepted" that a failure turns into a refusal): every file that took
        the line gets a ``void`` line naming its id, then the error propagates.
        Returns the entry's id (what a later :meth:`void` names).
        """
        import uuid  # noqa: PLC0415
        subject = canonical_subject(subject)
        src = source_key or source_key_of("", None)
        entry_id = uuid.uuid4().hex[:16]
        def _line(rec: dict) -> bytes:
            return (json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8")
        line = _line({"ts": ts, "subject": subject, "source_key": src,
                      "decision": decision, "reason": reason, "id": entry_id})
        d = self._subject_dir(subject)
        # The two per-subject files are written under the subject lock
        # (exclusive: a failed write is cut back); the tenant-wide
        # decisions.jsonl is shared by every subject.
        targets = ((self.path, False), (d / "all.jsonl", True),
                   (d / f"{src}.jsonl", True))
        took: list[tuple[Path, bool]] = []
        try:
            for path, exclusive in targets:
                self._append_to(path, line, exclusive)
                took.append((path, exclusive))
        except BaseException:
            if void_on_failure:
                self._void_lines(took, ts=ts, subject=subject, src=src, entry_id=entry_id)
            raise
        return entry_id

    def _targets(self, subject: str, src: str) -> tuple[tuple[Path, bool], ...]:
        d = self._subject_dir(subject)
        return ((self.path, False), (d / "all.jsonl", True), (d / f"{src}.jsonl", True))

    def _void_lines(self, took, *, ts: float, subject: str, src: str, entry_id: str) -> int:
        """Append a ``void`` line naming ``entry_id`` to every file in ``took``.

        Returns how many files took the void. A file that refuses it keeps the
        accepted line (best effort; the caller's error is what propagates)."""
        void = (json.dumps({"ts": ts, "subject": subject, "source_key": src,
                            "decision": DECISION_VOID, "reason": REASON_NOT_STORED,
                            "voids": entry_id}, separators=(",", ":")) + "\n").encode("utf-8")
        done = 0
        for path, exclusive in took:
            try:
                self._append_to(path, void, exclusive)
                done += 1
            except (OSError, RuntimeError):
                continue  # the original error wins; this file keeps its accepted line
        return done

    def void(self, *, ts: float, subject: str, source_key: str, entry_id: str) -> int:
        """Void an earlier "accepted" entry in all three files (caller holds the subject lock)."""
        subject = canonical_subject(subject)
        return self._void_lines(self._targets(subject, source_key), ts=ts, subject=subject,
                                src=source_key, entry_id=entry_id)


_ADMITTED_MAX = 1024
_admitted: "OrderedDict[str, float]" = OrderedDict()
_admitted_lock = threading.Lock()


@dataclasses.dataclass(frozen=True)
class _AcceptedEntry:
    """Where an "accepted" decision was written - what :func:`void_admission` cancels."""

    learning_root: Path
    tenant_id: str
    subject: str
    source_key: str
    entry_id: str
    ts: float
    accepted_ref: str


_entries: "OrderedDict[str, _AcceptedEntry]" = OrderedDict()


def _remember_admitted(event_id: str, weight: float, entry: Optional[_AcceptedEntry] = None) -> None:
    with _admitted_lock:
        _admitted[event_id] = weight
        while len(_admitted) > _ADMITTED_MAX:
            _admitted.popitem(last=False)
        if entry is not None:
            _entries[event_id] = entry
            while len(_entries) > _ADMITTED_MAX:
                _entries.popitem(last=False)


def void_admission(event_id: str, *, stage: str, error_type: str = "") -> bool:
    """Cancel the gate's "accepted" for a feedback event the CALLER then failed to store.

    ``admit()`` chains ``learning.feedback_accepted`` and appends an "accepted" line
    before the caller's own write (the store's chain + disk record, a route's audit
    record). When that write fails nothing was stored, yet the line stayed: the
    producer's trust history, the throttle and the anomaly monitor counted feedback
    that does not exist (review 2026-11-05, LRN-9). This appends a ``void`` line to
    every ledger file under the subject lock and chains ``learning.feedback_not_stored``
    with ``stage`` (closed token: ``store`` | ``route_audit`` | ``route_store``).

    Returns False when this process holds no admission for ``event_id`` (nothing to void).
    Raises what the chain writer raises - a void that cannot be chained must not be
    reported as compensated.
    """
    with _admitted_lock:
        entry = _entries.pop(event_id, None)
        _admitted.pop(event_id, None)
    if entry is None:
        return False
    ledger = DecisionLedger(entry.learning_root)
    with ledger.locked(entry.subject):
        ledger.void(ts=entry.ts, subject=entry.subject, source_key=entry.source_key,
                    entry_id=entry.entry_id)
    _audit(EV_NOT_STORED, entry.tenant_id, {
        "tenant_id": entry.tenant_id, "skill_id": entry.subject, "feedback_event_id": event_id,
        "source_key": entry.source_key, "stage": stage[:32], "error_type": error_type[:64]})
    return True


@contextmanager
def admission(event: LearningEvent, store, *, stage: str = "store", **kwargs) -> Iterator[LearningEvent]:
    """``admit()`` whose "accepted" is voided when the body raises (LRN-9).

    Use it around the write that makes the admitted feedback real::

        with admission(event, store) as admitted:
            persist(admitted)

    Whatever the body raises propagates unchanged after the void.
    """
    admitted = admit(event, store, **kwargs)
    try:
        yield admitted
    except BaseException as exc:
        if admitted.event_type is EventType.FEEDBACK:
            void_admission(event.event_id, stage=stage,
                           error_type=type(exc).__name__)
        raise


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
        ref = _audit(EV_ACCEPTED, tenant, {**base, "trust_weight": trust.trust_weight,
                                           "executions_in_window": reality.executions_in_window})
        try:
            entry_id = ledger.append(ts=now, subject=subject, decision="accepted", reason="valid",
                                     source_key=src, void_on_failure=True)
        except Exception as exc:
            # The chain says "accepted" but the gate's own ledger did not take it:
            # chain the compensation and refuse the feedback (review c120 r2_s1-9a).
            try:
                _audit(EV_NOT_STORED, tenant, {**base, "audit_ref": ref, "stage": "ledger",
                                               "error_type": type(exc).__name__[:64]})
            except Exception:  # noqa: BLE001 — the refusal below still happens
                pass
            raise FeedbackRejected(REASON_NOT_STORED, ref) from exc
    _remember_admitted(event.event_id, trust.trust_weight, _AcceptedEntry(
        learning_root=Path(store.tenant_home) / "learning", tenant_id=tenant, subject=subject,
        source_key=src, entry_id=entry_id, ts=now, accepted_ref=ref))
    signal = dict(event.signal or {})
    signal.pop(SOURCE_SIGNAL_KEY, None)
    signal["trust_weight"] = trust.trust_weight
    return dataclasses.replace(event, signal=signal)


#: Default retention of the gate's ledgers (CLAUDE.md § Phase 3: 90-day default).
RETENTION_DAYS = 90


@dataclasses.dataclass(frozen=True)
class LedgerSweep:
    lines_removed: int = 0
    files_rewritten: int = 0
    decisions_skipped: bool = False  # decisions.jsonl lock busy: retried on the next pass


def _sweep(ledger: DecisionLedger, keep, *, drop_files=None) -> LedgerSweep:
    """Apply ``keep`` to every ledger file, each under the lock its writers take."""
    removed = rewritten = 0
    if ledger.index_dir.is_dir():
        for d in sorted(p for p in ledger.index_dir.iterdir() if p.is_dir()):
            with _flock(d / ".lock", LOCK_TIMEOUT_S, "feedback gate: subject lock busy"):
                for f in sorted(d.glob("*.jsonl")):
                    if drop_files is not None and drop_files(f):
                        n = len([ln for ln in f.read_text(encoding="utf-8", errors="replace")
                                 .splitlines() if ln.strip()])
                        f.unlink()
                    else:
                        n = _rewrite_lines(f, keep)
                    if n:
                        removed += n
                        rewritten += 1
    skipped = False
    if ledger.path.exists():
        try:
            with ledger._decisions_locked():
                n = _rewrite_lines(ledger.path, keep)
        except RuntimeError:
            skipped, n = True, 0
        if n:
            removed += n
            rewritten += 1
    return LedgerSweep(removed, rewritten, skipped)


def prune_ledgers(tenant_home: Path, tenant_id: str, *, retention_days: int = RETENTION_DAYS,
                  now: Optional[float] = None) -> LedgerSweep:
    """Drop ledger lines older than ``retention_days`` (LRN-13); audited, counts only.

    ``decisions.jsonl`` and every ``subjects/<h>/*.jsonl`` grew without limit. A line is
    kept while its ``ts`` lies inside the window; a void line carries the ts of the entry
    it voids, so the pair expires together. A torn fragment is dropped (no reader can
    parse it). The pass is chained as ``learning.retention`` with ``scope=feedback_gate``.
    """
    if not isinstance(retention_days, int) or retention_days < 1:
        raise ValueError("retention_days must be a positive int")
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400

    def keep(rec: Optional[dict]) -> bool:
        if rec is None:
            return False
        ts = rec.get("ts")
        return isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts >= cutoff

    sweep = _sweep(DecisionLedger(Path(tenant_home) / "learning"), keep)
    _audit("learning.retention", tenant_id, {
        "scope": "feedback_gate", "retention_days": retention_days,
        "deleted_count": sweep.lines_removed, "partitions_rewritten": sweep.files_rewritten})
    return sweep


def erase_producer(tenant_home: Path, tenant_id: str, subject_id: str) -> int:
    """GDPR Art. 17: remove every ledger line keyed by ``subject_id`` as a producer (LRN-24).

    ``source_key`` is ``sha256(tenant + "\\0" + source)`` - unsalted, so anyone holding the
    subject's id can recompute it: pseudonymous personal data. The console passes
    ``"console:" + consent_subject(rec)`` as the source, other callers the raw principal, so
    both forms of the subject id are erased. The producer's own ``<source_key>.jsonl`` files
    are deleted; its lines leave ``all.jsonl`` and ``decisions.jsonl``. Returns lines removed
    (the per-subject index duplicates each decision, so one decision counts up to 3 times).
    """
    if not isinstance(subject_id, str) or not subject_id:
        raise ValueError("subject_id is required")
    keys = {source_key_of(tenant_id, raw) for raw in (subject_id, "console:" + subject_id)}

    def keep(rec: Optional[dict]) -> bool:
        return rec is None or rec.get("source_key") not in keys

    sweep = _sweep(DecisionLedger(Path(tenant_home) / "learning"), keep,
                   drop_files=lambda f: f.stem in keys)
    if sweep.decisions_skipped:
        raise RuntimeError("feedback gate ledger busy: decisions.jsonl not erased")
    return sweep.lines_removed

