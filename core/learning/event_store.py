"""Phase 2: EventStore — Learning event persistence (ADR-0314)."""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict

from core.learning.learning_events import LearningEvent, EventType

logger = logging.getLogger(__name__)


def _validate_tenant_id(tenant_id: str) -> None:
    """Validate tenant_id with the canonical rule (no private copy).

    FIX #6: Prevent tenant isolation bypass (GDPR Art. 32). Delegates to
    ``core.tenants.validation.validate_tenant_id``: the private regex here accepted
    ``ACME``, ``-x``, the reserved ``test`` and a trailing newline, all of which
    the core refuses (review 2026-11-05, LRN-15).
    """
    from core.tenants.validation import validate_tenant_id  # noqa: PLC0415

    validate_tenant_id(tenant_id)


def _scrub_pii(text: Optional[str]) -> Optional[str]:
    """Scrub PII from text (F2: PII Leakage Fix).

    Removes:
    - Email addresses (user@domain.com)
    - Phone numbers (+1-234-567-8900, 555-1234, etc.)
    - Credit card numbers (4111 1111 1111 1111, etc.)
    - Social security numbers (123-45-6789, etc.)
    - API keys / tokens (common patterns)

    Returns: Scrubbed text or original if no PII found.
    GDPR Art. 32 requires data security measures; this prevents PII leakage into audit logs.
    """
    if not text or not isinstance(text, str):
        return text

    # PII patterns (fail-closed: when in doubt, redact)
    patterns = [
        (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]'),  # Email
        (r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b', '[PHONE]'),  # Phone
        (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[CARD]'),  # Credit card
        (r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]'),  # Social security number
        (r'\b(?:sk_live_|sk_test_|pk_live_|pk_test_)[A-Za-z0-9]{20,}\b', '[API_KEY]'),  # Stripe keys
        (r'\b[A-Za-z0-9]{40}\b', '[TOKEN]'),  # Generic 40-char tokens (common in APIs)
    ]

    scrubbed = text
    for pattern, replacement in patterns:
        scrubbed = re.sub(pattern, replacement, scrubbed, flags=re.IGNORECASE)

    return scrubbed


def _scrub_pii_deep(obj: Any) -> Any:
    """Recursively scrub PII from nested dicts/lists (F2: PII Leakage Fix).

    GDPR Art. 32 requires data security measures; this prevents PII leakage into audit logs.
    """
    if isinstance(obj, dict):
        return {k: _scrub_pii_deep(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_scrub_pii_deep(item) for item in obj]
    elif isinstance(obj, str):
        return _scrub_pii(obj)
    else:
        return obj


class EventStore:
    """Date-partitioned JSON event storage (GDPR Art. 30, 32).

    Structure:
      {tenant_home}/global/learning/events/YYYY-MM-DD.jsonl
      One JSON line per event, append-only
    """

    _EVENTS_DIR = "learning/events"
    _lock = threading.RLock()

    def __init__(self, tenant_home: Path, tenant_id: Optional[str] = None):
        """Initialize event store for a tenant.

        Args:
            tenant_home: ``<corvin_home>/tenants/<tenant_id>/`` — the directory
                the events land under.
            tenant_id: when given, the store is BOUND to that tenant and
                ``write_event`` rejects an event carrying any other tenant
                (a foreign tenant's record must never land under this tenant's
                directory, GDPR Art. 32). Console routes always bind.
        """
        self.tenant_home = Path(tenant_home)
        if tenant_id is not None:
            _validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.events_dir = self.tenant_home / self._EVENTS_DIR
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def _get_event_file(self, timestamp: str) -> Path:
        """Get path to event file for timestamp (YYYY-MM-DD.jsonl)."""
        date_str = timestamp.split("T")[0]
        return self.events_dir / f"{date_str}.jsonl"

    def _partition_files(self) -> list[Path]:
        """The date-named partitions (``YYYY-MM-DD.jsonl``), oldest first.

        The ONE selector :meth:`query_events` and :meth:`count_events` share: an
        operator copy (``backup.jsonl``) or any other non-date file is neither counted
        nor returned, a pre-2026 partition is both (refutation 2026-11-05, RL-9).
        """
        out = []
        for f in self.events_dir.glob("*.jsonl"):
            try:
                datetime.strptime(f.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if len(f.stem) == 10:
                out.append(f)
        return sorted(out)

    def write_event(self, event: LearningEvent, *, subject_verified: bool = False) -> str:
        """Write event: core audit chain FIRST (fail-closed), then disk.

        Returns the ``audit_ref`` of the committed chain record.

        F2: PII Scrubbing — all payloads scrubbed before disk write (GDPR Art. 32).

        ADR-0314 / CLAUDE.md § Phase 3: "write_event writes the core chain
        FIRST; no chain commit → no disk record". Until 2026-09-06 only the
        sibling ``event_persistence.EventStore`` honoured that; THIS store — the
        one every live producer uses (``core/skills/boot.py``, the console
        emitter, operator ratings) — appended plain JSONL and never touched the
        hash chain, so every ACP ``skill_executed`` learning event was
        unattributed in the audit trail.

        The chain record is CONTENT-FREE (ids, type, skill, lom — never the
        ``signal`` payload); the disk record carries the returned ``audit_ref``
        so an operator can join the two.

        Raises:
            FeedbackRejected: a FEEDBACK event failed the ADR-0534 trust gate
                (the rejection is audited; nothing is written to disk).
            RuntimeError: the core audit writer is unavailable or the chain
                write did not commit — nothing is written to disk then.
            IOError: the disk append failed AFTER the chain committed (the
                chain record stands; the disk copy is the lossy side).
        """
        if self.tenant_id is not None and event.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: store is bound to {self.tenant_id!r}, "
                f"event carries {event.tenant_id!r}"
            )
        if (self.tenant_id is None and self.tenant_home.parent.name == "tenants"
                and event.tenant_id != self.tenant_home.name):
            # An unbound store over ``<home>/tenants/<tid>`` still names its tenant: the
            # process-wide emitter's unbound ``_default`` store wrote every tenant's events
            # into the ``_default`` directory (review 2026-11-05, LRN-2). Process-wide
            # writers use ``TenantEventStores``; this refuses the old shape outright.
            raise ValueError(
                f"Tenant mismatch: store directory belongs to {self.tenant_home.name!r}, "
                f"event carries {event.tenant_id!r}"
            )
        if event.event_type is EventType.FEEDBACK:
            # ADR-0534: every feedback signal passes the trust gate (throttle →
            # reality check → trust weight) before it becomes learnable state.
            # Raises FeedbackRejected (audited) — nothing is written then. The
            # chain + disk write runs INSIDE the admission: when it fails, the
            # gate's "accepted" is voided and learning.feedback_not_stored is
            # chained, so no reader counts feedback that was never stored
            # (review 2026-11-05, LRN-9).
            from core.learning.feedback_gate import admission  # noqa: PLC0415
            with admission(event, self, subject_verified=subject_verified) as admitted:
                return self._write_audited(admitted)
        return self._write_audited(event)

    def _write_audited(self, event: LearningEvent) -> str:
        """The chain record, then the disk line (see :meth:`write_event`)."""
        with self._lock:
            audit_ref = self._audit_chain_first(event)
            event_file = self._get_event_file(event.timestamp)

            try:
                event_dict = event.to_dict()

                # F2: Scrub PII from event payload before writing to disk (GDPR Art. 32).
                # Only the PAYLOAD fields: the identity fields (tenant_id, skill_id,
                # event_id, timestamp, lom, ...) are system identifiers every reader joins
                # on, and scrubbing them turned a valid tenant "customer-0123456789" into
                # "customer-[PHONE]" - a record no reader of that tenant ever saw again
                # (review 2026-11-05, LW-5).
                for _key in _PAYLOAD_FIELDS:
                    event_dict[_key] = _scrub_pii_deep(event_dict.get(_key))
                _keep_execution_output(event, event_dict)

                event_dict["audit_ref"] = audit_ref
                line = json.dumps(event_dict, separators=(",", ":")) + "\n"

                _append_record(event_file, line.encode("utf-8"))

            except IOError as e:
                raise IOError(f"Failed to write learning event: {e}")
            return audit_ref

    @staticmethod
    def _audit_chain_first(event: LearningEvent) -> str:
        """Commit a content-free record to the core hash chain; return audit_ref.

        Shares ``event_persistence.core_audit_event`` (writer resolution +
        commit verification by chain-tail read-back) so both stores have ONE
        fail-closed path, not two.
        """
        from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

        details = {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "skill_id": event.skill_id,
            "skill_version": event.skill_version,
            "lom": event.lom,
        }
        return core_audit_event(
            f"learning.{event.event_type.value}",
            tenant_id=event.tenant_id,
            details=details,
        )

    def query_events(
        self,
        tenant_id: str,
        event_type: Optional[EventType] = None,
        skill_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 10000,  # FIX #21: Prevent OOM on unbounded queries
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[LearningEvent]:
        """Query events with optional filters.

        **Selection and order are two separate things (round-4 review, F3).**

        * ``limit``/``offset`` ALWAYS window from the NEWEST end: ``limit=N``
          means "the N most recent matching events", ``offset=K`` skips the K
          most recent. There is no way to ask for "the oldest N", because no
          consumer in this repo wants that and the previous default silently
          gave it to them: after a tenant's ``limit``-th event, every "recent"
          window computed from a default query was frozen ancient history and
          never advanced again (``outcome_sink.recent_outcomes`` fed exactly
          that constant into the live optimizer as its ground truth).
        * ``newest_first`` only controls the ORDER of the returned list:
          ``False`` (default) → chronological, oldest → newest, so ``[-1]`` is
          the most recent and ``[-n:]`` the last n. ``True`` → newest → oldest.

        Round 3 introduced ``newest_first`` as a *selection* flag and passed it
        at one call site only; making the newest-end window the contract of the
        method removes the class of defect rather than one instance of it.
        """
        # FIX #6: Validate tenant_id upfront (prevent cross-tenant leakage, GDPR Art. 32)
        _validate_tenant_id(tenant_id)

        if limit < 0 or offset < 0:
            raise ValueError("limit and offset must be non-negative")

        with self._lock:
            results: list[LearningEvent] = []
            wanted = offset + limit

            # Date-level window on the partition name. No implicit floor or ceiling:
            # the 2026-01-01 floor and the "today" ceiling made query_events skip
            # partitions count_events counted (refutation 2026-11-05, RL-9). A full
            # ISO timestamp names its day.
            start_date = since[:10] if since else None
            end_date = until[:10] if until else None

            # Newest date file first: the newest ``wanted`` events live at the
            # END of the newest files, so we walk backwards and stop as soon as
            # we hold enough. Older files are never opened.
            for event_file in reversed(self._partition_files()):
                file_date = event_file.stem

                if (start_date and file_date < start_date) or (end_date and file_date > end_date):
                    continue

                file_results: list[LearningEvent] = []
                try:
                    with open(event_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue

                            # One bad line must never discard the rest of the
                            # file: a malformed JSON line used to abort the
                            # whole file, and an unknown ``event_type`` enum
                            # value raised ValueError straight OUT of
                            # query_events, so a single newer-schema record made
                            # every consumer see an empty history (round-3
                            # review, R3-B3).
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError as e:
                                logger.warning(
                                    "Corrupted JSON line in %s (%s at char %d) - event LOST",
                                    event_file.name, e.msg, e.pos,
                                )
                                continue

                            # FIX #13 + LRN-6: only THIS store's records. The partition
                            # directory is shared with event_persistence (its records,
                            # tombstones included, carry a ``learning.``-prefixed type and
                            # no skill_id); those are skipped silently. A record of this
                            # schema that lacks a field is named by its missing FIELDS and
                            # file only - never the record: a foreign line carries user_id
                            # and payload (review 2026-11-05, LRN-5).
                            if not isinstance(data, dict) or _is_foreign_schema(data):
                                continue
                            missing = _missing_fields(data)
                            if missing:
                                logger.warning(
                                    "Skipping malformed learning event in %s: missing fields %s",
                                    event_file.name, sorted(missing))
                                continue

                            if data.get("tenant_id") != tenant_id:
                                continue

                            if event_type and data.get("event_type") != event_type.value:
                                continue
                            if skill_id and data.get("skill_id") != skill_id:
                                continue

                            try:
                                parsed_type = EventType(data["event_type"])
                            except ValueError:
                                logger.warning(
                                    f"Skipping event with unknown event_type "
                                    f"{data.get('event_type')!r} in {event_file}"
                                )
                                continue

                            # FIX #14, #25: Include version in reconstruction (prevent schema drift)
                            event = LearningEvent(
                                event_id=data["event_id"],
                                event_type=parsed_type,
                                skill_id=data["skill_id"],
                                tenant_id=data["tenant_id"],
                                timestamp=data["timestamp"],
                                version=data.get("version", "1.0"),  # Default to 1.0 if missing
                                signal=data.get("signal"),
                                skill_config_delta=data.get("skill_config_delta"),
                                skill_version=data.get("skill_version"),
                                lom=data.get("lom"),
                                prev_hash=data.get("prev_hash"),
                                audit_ref=data.get("audit_ref"),
                            )
                            file_results.append(event)

                            # No early break here: the NEWEST events of a file
                            # live at its END, so the whole file has to be read
                            # before it can be sliced. FIX #21's bound is kept
                            # by stopping after the first file that satisfies
                            # ``wanted`` — older files are never opened at all.

                except IOError as e:
                    logger.error(f"IO error reading {event_file}: {e}")
                    continue

                file_results.reverse()  # newest → oldest within the file
                results.extend(file_results)

                if len(results) >= wanted:
                    break

            # FIX #21: Apply limit + offset to prevent OOM. ``results`` is
            # newest → oldest, so this window is always the newest slice.
            window = results[offset:wanted]
            return window if newest_first else window[::-1]

    def count_events(self, tenant_id: str, event_type: Optional[EventType] = None) -> int:
        """Count events for a tenant (stream-based, O(n) time, O(1) space).

        FIX #22: Don't materialize all results; stream-count instead.
        """
        _validate_tenant_id(tenant_id)
        count = 0

        with self._lock:
            for event_file in self._partition_files():
                try:
                    with open(event_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            # A torn line is skipped on its own: the file-level
                            # except used to drop every record AFTER it from the
                            # count, i.e. exactly the record LW-1 keeps readable
                            # (refutation 2026-11-05).
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            # Same records query_events returns: never a tombstone or an
                            # event_persistence record from the shared directory, never a
                            # record missing a field (review 2026-11-05, LRN-6).
                            if not _is_store_record(data):
                                continue
                            if data.get("tenant_id") != tenant_id:
                                continue
                            if event_type and data.get("event_type") != event_type.value:
                                continue
                            count += 1
                except IOError:
                    continue

        return count


def _append_record(path: Path, line: bytes) -> None:
    """Append one whole record line to a partition (O_APPEND, written to the end in full).

    A partition whose tail is a torn line (a writer that died mid-append, a full disk) gets a
    newline first, so the torn bytes stay their own unparseable line and never swallow THIS
    record: the chain record for it has already committed, and gluing the line onto the torn
    fragment made the disk side unreadable to every reader (review 2026-11-05, LW-1). Same
    rule as ``feedback_gate._append_line``.
    """
    import os  # noqa: PLC0415 - kept local: the module header is pinned by the learning register

    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        size = os.fstat(fd).st_size
        if size > 0:
            os.lseek(fd, size - 1, os.SEEK_SET)  # O_APPEND: the write still goes to the end
            if os.read(fd, 1) != b"\n":
                line = b"\n" + line
        view = memoryview(line)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError("learning event append wrote 0 bytes")
            view = view[n:]
    finally:
        os.close(fd)


#: The record fields that carry producer-supplied content and are PII-scrubbed on write (LW-5).
_PAYLOAD_FIELDS = ("signal", "skill_config_delta")

#: The fields every record of THIS store carries (``query_events`` rebuilds from them).
_REQUIRED_FIELDS = frozenset({"event_id", "event_type", "skill_id", "tenant_id", "timestamp"})
_EVENT_TYPE_VALUES = frozenset(t.value for t in EventType)


def _is_foreign_schema(data: dict) -> bool:
    """A record of the sibling ``event_persistence`` store (or its tombstone).

    Both stores write ``<tenant>/learning/events/YYYY-MM-DD.jsonl`` (review 2026-11-05,
    LRN-6). The sibling's event types are ``learning.``-prefixed
    (``learning.method.observation``, ``learning.tombstone``); this store's are the bare
    ``EventType`` values. Filtering by schema in every reader keeps the files where they
    are - no migration, no second directory, nothing for erasure/retention to miss.
    """
    etype = data.get("event_type")
    return isinstance(etype, str) and etype.startswith("learning.")


def _missing_fields(data: dict) -> set[str]:
    return set(_REQUIRED_FIELDS - data.keys())


def _is_store_record(data: Any) -> bool:
    """A complete record of this store with a known event type."""
    return (isinstance(data, dict) and not _is_foreign_schema(data)
            and not _missing_fields(data) and data.get("event_type") in _EVENT_TYPE_VALUES)


def _keep_execution_output(event: LearningEvent, event_dict: dict) -> None:
    """Store a ``skill_executed`` signal's ``output`` exactly as produced (LRN-17).

    ``output`` is the ADR-0534 Layer-2 join subject: a feedback producer claims
    ``execution_output_hash(signal)`` over it, and ``reality_check`` recomputes the hash
    from the STORED record. The regex scrub rewrote system values inside it (a Unix
    timestamp became ``[PHONE]``, a 40-hex job id ``[TOKEN]``), so every honest claim over
    such an output was refused as ``output_hash_mismatch``. It is excluded from this scrub
    because its producers scrub it at the source - the registry passes
    ``SkillRegistry._scrub_pii_from_output(result.output)``, the video producer builds it
    from ids, counts and enums only - and a second, lossy pass here cannot be undone by a
    reader. Every other signal field is still scrubbed.
    """
    if event.event_type is not EventType.SKILL_EXECUTED:
        return
    original = event.signal if isinstance(event.signal, dict) else None
    scrubbed = event_dict.get("signal")
    if original is not None and "output" in original and isinstance(scrubbed, dict):
        scrubbed["output"] = original["output"]


class TenantEventStores:
    """One BOUND :class:`EventStore` per tenant behind one ``write_event``/``query_events``.

    The process-wide learning emitter (``core/skills/boot.py``, the token hook) used to
    wrap ``EventStore(tenant_home(boot_tenant))`` UNBOUND: an OUTCOME of tenant ``acme``
    landed in the boot tenant's events directory, ``recent_outcomes("acme")`` read the
    boot tenant's directory, and acme's Art. 17 erasure never reached those records
    (review 2026-11-05, LRN-2). This keeps a dict keyed by tenant; each entry is bound to
    its tenant, so it refuses any event that names another one, and every read goes to the
    directory of the tenant it names.
    """

    def __init__(self, home_of: Optional[Any] = None):
        if home_of is None:
            from core.paths.tenant import tenant_home as home_of  # noqa: PLC0415
        self._home_of = home_of
        self._stores: Dict[str, EventStore] = {}
        self._guard = threading.Lock()

    def for_tenant(self, tenant_id: str) -> EventStore:
        _validate_tenant_id(tenant_id)
        with self._guard:
            store = self._stores.get(tenant_id)
            if store is None:
                store = EventStore(Path(self._home_of(tenant_id)), tenant_id=tenant_id)
                self._stores[tenant_id] = store
            return store

    def write_event(self, event: LearningEvent, *, subject_verified: bool = False) -> str:
        return self.for_tenant(event.tenant_id).write_event(event, subject_verified=subject_verified)

    def query_events(self, tenant_id: str, *args: Any, **kwargs: Any) -> list[LearningEvent]:
        return self.for_tenant(tenant_id).query_events(tenant_id, *args, **kwargs)

    def count_events(self, tenant_id: str, event_type: Optional[EventType] = None) -> int:
        return self.for_tenant(tenant_id).count_events(tenant_id, event_type)
