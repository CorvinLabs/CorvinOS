"""Audit + persistence for Method Discovery (ADR-0548, Phase 1).

Everything Method Discovery learns has to be provable later. This module is the
only place that writes, and it writes through the ADR-0314 ``EventStore``
(``core.learning.event_persistence``) — the store that puts every record on the
CORE hash-chained audit writer FIRST and refuses the disk record if that chain
write did not commit (ADR-0232/0233). There is no second store and no local
"audit" file: forking the chain is exactly how a proof system stops being one.

Two chains are in play and they are not the same thing:

1. The **core audit chain** (``audit.jsonl``) — hash-chained by the platform,
   verified by the boot tripwire. ``EventStore.write_event`` writes to it and
   raises if it does not commit.
2. The **method-observation chain** — a per-tenant SHA256 chain over the
   observations themselves (``prev_hash`` -> ``hash``), so an auditor can prove
   that the observation set behind a discovered pattern is complete and in
   order, not just that each record was individually audited. Chain 1 proves
   "this was written"; chain 2 proves "and nothing was removed from between".

Sparseness (CONCEPT-0029 Constraint 5) is enforced structurally: this module
exposes exactly two writers, ``record_observation`` and ``record_discovery``.
There is no generic ``write()``.

Tenant isolation (ADR-0007, GDPR Art. 5/32): the sink is bound to one tenant at
construction, ``EventStore`` re-checks the binding on every call, and an empty
or invalid tenant is rejected before any I/O.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.learning.event_persistence import EventStore
from core.learning.event_schema import LearningEvent, LearningEventType
from core.tenants.validation import validate_tenant_id

__all__ = [
    "GENESIS_HASH",
    "MethodAuditSink",
    "ChainVerification",
    "canonical_json",
    "sha256_hex",
]

#: ``prev_hash`` of the first observation in a tenant's chain.
GENESIS_HASH: str = "0" * 64

#: Skill identity stamped on every event, so the audit trail attributes the
#: record to this subsystem rather than to whichever skill happened to run.
SKILL_ID: str = "os.method_discovery"


def canonical_json(payload: Any) -> str:
    """Deterministic JSON for hashing.

    Sorted keys, no insignificant whitespace, non-ASCII escaped. Two processes
    on two machines must produce byte-identical output for the same data or the
    chain cannot be re-verified anywhere but where it was written.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def sha256_hex(text: str) -> str:
    """SHA256 of ``text`` as lowercase hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ChainVerification:
    """Result of walking a tenant's observation chain."""

    __slots__ = ("ok", "count", "error", "head")

    def __init__(self, ok: bool, count: int, head: str, error: Optional[str] = None):
        self.ok = ok
        self.count = count
        self.head = head
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ChainVerification ok={self.ok} count={self.count} error={self.error!r}>"

    def to_payload(self) -> dict:
        return {"ok": self.ok, "count": self.count, "head": self.head, "error": self.error}


class MethodAuditSink:
    """Tenant-bound writer/reader for method-discovery events.

    Args:
        tenant_id: The one tenant this sink may touch. Validated immediately;
            a null/empty tenant raises rather than defaulting to ``_default``,
            because a silent default is a cross-tenant write waiting to happen.
        store: Injectable ``EventStore`` (tests pass a sandboxed one). When
            omitted, the real ADR-0314 store for ``tenant_id`` is used.
    """

    def __init__(self, tenant_id: str, *, store: Optional[EventStore] = None):
        self.tenant_id = validate_tenant_id(tenant_id)
        self._store = store if store is not None else EventStore(self.tenant_id)
        if self._store.tenant_id != self.tenant_id:
            raise ValueError(
                f"store is bound to {self._store.tenant_id!r}, sink to {self.tenant_id!r}"
            )

    # ── chain head ──────────────────────────────────────────────────────

    @property
    def _state_dir(self) -> Path:
        d = self._store.events_dir.parent / "method_discovery"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def _head_file(self) -> Path:
        return self._state_dir / "chain_head.json"

    def chain_head(self) -> str:
        """Hash of the most recent observation, or ``GENESIS_HASH`` if none.

        A missing or unparseable head file reads as genesis rather than
        raising: losing the pointer must not stop new observations from being
        recorded, and :meth:`verify_chain` re-derives the true head from the
        event store anyway.
        """
        try:
            data = json.loads(self._head_file.read_text())
        except (OSError, ValueError):
            return GENESIS_HASH
        head = data.get("head")
        if not isinstance(head, str) or len(head) != 64:
            return GENESIS_HASH
        if data.get("tenant_id") != self.tenant_id:
            # A head file for another tenant is never usable here.
            return GENESIS_HASH
        return head

    def _advance_head(self, new_head: str) -> None:
        """Persist the new chain head atomically (temp file + os.replace)."""
        body = canonical_json(
            {"tenant_id": self.tenant_id, "head": new_head, "updated": _utcnow().isoformat()}
        )
        fd, tmp = tempfile.mkstemp(prefix=".chain_head.", suffix=".tmp", dir=str(self._state_dir))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._head_file)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── writes (exactly two — Constraint 5) ─────────────────────────────

    async def record_observation(self, observation) -> str:
        """Audit one :class:`MethodObservation`, then advance the chain head.

        Order is load-bearing and fail-closed: the ADR-0314 store writes the
        core hash chain first and raises if it did not commit, so a failure
        here leaves the head un-advanced and NOTHING recorded. The alternative
        (record locally, audit later) is precisely the fail-open ADR-0232
        forbids.

        Args:
            observation: A ``MethodObservation`` whose ``prev_hash`` already
                equals :meth:`chain_head` and whose ``hash`` is self-consistent.

        Returns:
            The ``audit_ref`` of the core-chain record.

        Raises:
            ValueError: tenant mismatch, or the observation does not link to
                the current head (a fork attempt).
            RuntimeError: the core audit writer is unavailable or did not
                commit — propagated unchanged from ``EventStore``.
        """
        if observation.tenant_id != self.tenant_id:
            raise ValueError(
                f"observation tenant {observation.tenant_id!r} != sink tenant {self.tenant_id!r}"
            )
        head = self.chain_head()
        if observation.prev_hash != head:
            raise ValueError(
                f"chain fork: observation.prev_hash={observation.prev_hash[:12]}… "
                f"but current head={head[:12]}…"
            )
        if observation.hash != observation.compute_hash():
            raise ValueError("observation hash does not match its content (tampered or stale)")

        audit_ref = await self._write(
            LearningEventType.METHOD_OBSERVATION,
            session_id=observation.task_id,
            payload=observation.to_payload(),
            tags=["method-discovery", f"task_type:{observation.task_type}"],
        )
        self._advance_head(observation.hash)
        return audit_ref

    async def record_discovery(self, pattern, breakdown) -> str:
        """Audit a pattern crossing the discovery threshold.

        Carries the confidence derivation (EU AI Act Art. 50) and the ids of
        the observations it was computed from, so "prove this was learned" is
        answerable by following ``observation_ids`` back into the chain.
        """
        if pattern.tenant_id != self.tenant_id:
            raise ValueError(
                f"pattern tenant {pattern.tenant_id!r} != sink tenant {self.tenant_id!r}"
            )
        payload = pattern.to_payload()
        payload["confidence_derivation"] = breakdown.to_payload()
        payload["confidence_explanation"] = breakdown.explain()
        return await self._write(
            LearningEventType.METHOD_DISCOVERED,
            session_id=pattern.pattern_id,
            payload=payload,
            tags=["method-discovery", f"task_type:{pattern.task_type}"],
        )

    async def _write(
        self,
        event_type: LearningEventType,
        *,
        session_id: str,
        payload: dict,
        tags: list[str],
    ) -> str:
        event = LearningEvent(
            event_type=event_type,
            tenant_id=self.tenant_id,
            instance_id=_instance_id(),
            skill_name=SKILL_ID,
            session_id=session_id,
            timestamp_utc=_utcnow().replace(tzinfo=None),
            payload=payload,
            tags=tags,
        )
        return await self._store.write_event(event, self.tenant_id)

    # ── reads (tenant-filtered by the store itself) ─────────────────────

    async def read_observation_payloads(self, *, limit: int = 1000) -> list[dict]:
        """Observation payloads for this tenant, oldest first."""
        events = await self._store.read_events(
            tenant_id=self.tenant_id,
            event_type=LearningEventType.METHOD_OBSERVATION,
            limit=limit,
        )
        payloads = [e.payload for e in events]
        payloads.sort(key=lambda p: (p.get("timestamp", ""), p.get("hash", "")))
        return payloads

    async def read_discovered_payloads(self, *, limit: int = 1000) -> list[dict]:
        """Discovered-pattern payloads for this tenant, newest first."""
        events = await self._store.read_events(
            tenant_id=self.tenant_id,
            event_type=LearningEventType.METHOD_DISCOVERED,
            limit=limit,
        )
        return [e.payload for e in events]

    # ── verification ────────────────────────────────────────────────────

    async def verify_chain(self) -> ChainVerification:
        """Walk the observation chain and check every link.

        Checks, in order, that: the first record links to genesis, each record's
        ``prev_hash`` equals its predecessor's ``hash``, and each record's
        ``hash`` still matches a re-hash of its own content. The third check is
        what catches an edited payload — the first two only catch deletions and
        re-orderings.
        """
        payloads = await self.read_observation_payloads(limit=100000)
        expected_prev = GENESIS_HASH
        for index, payload in enumerate(payloads):
            prev = payload.get("prev_hash")
            own = payload.get("hash")
            if prev != expected_prev:
                return ChainVerification(
                    False,
                    index,
                    expected_prev,
                    f"broken link at #{index} (task_id={payload.get('task_id')!r}): "
                    f"prev_hash={str(prev)[:12]}… expected {expected_prev[:12]}…",
                )
            recomputed = sha256_hex(canonical_json(_hashable_view(payload)))
            if own != recomputed:
                return ChainVerification(
                    False,
                    index,
                    expected_prev,
                    f"content tampered at #{index} (task_id={payload.get('task_id')!r}): "
                    f"hash={str(own)[:12]}… recomputed {recomputed[:12]}…",
                )
            expected_prev = own
        return ChainVerification(True, len(payloads), expected_prev)


def _hashable_view(payload: dict) -> dict:
    """The subset of an observation payload that its ``hash`` covers.

    ``hash`` itself is excluded (it cannot cover itself); everything else is
    included, so any edit to any recorded field breaks verification. Kept here
    next to the verifier — and mirrored by ``MethodObservation.compute_hash`` —
    because a drift between the two would silently disable tamper detection.
    """
    return {k: v for k, v in payload.items() if k != "hash"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _instance_id() -> str:
    """Instance identity for the event record (never PII)."""
    return os.environ.get("CORVIN_INSTANCE_ID") or "local"
