"""Method Discovery — observation, pattern recognition, event handling (ADR-0548).

Phase 1 of CONCEPT-0029. Three levels, bottom-up:

1. :class:`MethodObservation` — one immutable, hash-chained record of "the user
   ran these skills, in this order, on this kind of task, and it worked".
2. :class:`PatternRecognition` — groups observations into
   :class:`WorkstylePattern` candidates, **always keyed by
   ``(task_type, skill_sequence)``** and never globally (CONCEPT-0029
   Constraint 1: a workflow that is right for a feature is wrong for a
   security review, and conflating them is Attack 3).
3. :class:`MethodDiscovery` — the event-handling facade: observe a completed
   task, re-derive patterns, and announce the ones that cross the confidence
   threshold. It owns no audit logic of its own; every write goes through
   ``observability.MethodAuditSink`` and is fail-closed there.

Immutability is load-bearing, not stylistic. Every dataclass here is frozen AND
hashable, which is why sequence fields are ``tuple`` rather than ``list`` and
``outcome_details`` is carried as canonical JSON text rather than a ``dict``:
an observation that can be mutated after it was hashed is not evidence.

This module makes no recommendations and changes no behaviour — it only
observes and reports. Acting on a discovered pattern is ADR-0549 (Phase 2).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from core.tenants.validation import validate_tenant_id

from .confidence_scorer import (
    DISCOVERY_THRESHOLD,
    ConfidenceBreakdown,
    ConfidenceScorer,
)
from .observability import (
    GENESIS_HASH,
    MethodAuditSink,
    canonical_json,
    sha256_hex,
)

__all__ = [
    "TASK_TYPES",
    "OUTCOMES",
    "MethodObservation",
    "WorkstylePattern",
    "PatternRecognition",
    "MethodDiscovery",
]

#: The task types an observation may declare. Closed set on purpose: an
#: open-ended string would let two spellings of the same intent ("bugfix" /
#: "bug-fix") split one pattern into two under-powered ones, and the whole
#: stratification guarantee rests on this key being canonical.
TASK_TYPES: frozenset[str] = frozenset(
    {
        "feature",
        "refactor",
        "bugfix",
        "security",
        "learning",
        "documentation",
        "infrastructure",
        "performance",
        "investigation",
    }
)

#: Terminal outcomes of an observed task.
OUTCOMES: frozenset[str] = frozenset({"success", "failure", "partial"})

#: Outcomes that count towards ``success_rate``. ``partial`` deliberately does
#: NOT count as success: a method that half-works should not accrue confidence
#: as if it worked, and rounding it up is how an overfitted pattern gets
#: promoted (CONCEPT-0029 Attack 3).
_SUCCESS_OUTCOMES: frozenset[str] = frozenset({"success"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


# ── Level 1: Observation ────────────────────────────────────────────────────


@dataclass(frozen=True)
class MethodObservation:
    """One immutable observation of a completed task (ADR-0548 Level 1).

    Frozen and hashable: it is a link in a SHA256 chain, and a mutable link is
    not a link. ``hash`` covers every other field, so editing any of them after
    the fact is detectable by ``MethodAuditSink.verify_chain``.

    Construct with :meth:`create`, which computes ``hash`` for you. The
    constructor is usable directly only when you already have a valid hash
    (e.g. rehydrating from the audit trail via :meth:`from_payload`).
    """

    tenant_id: str
    timestamp: datetime
    task_id: str
    task_type: str
    task_complexity: int  # 1-5
    skill_sequence: tuple[str, ...]
    skill_latencies_ms: tuple[int, ...]
    outcome: str
    outcome_details_json: str  # canonical JSON; see .outcome_details
    prev_hash: str
    lom: str
    hash: str = ""
    user_feedback_received: bool = False
    user_feedback_score: Optional[float] = None

    def __post_init__(self) -> None:
        # Coerce sequences to tuples so a caller passing a list still gets a
        # hashable object rather than a confusing TypeError at hash() time.
        object.__setattr__(self, "skill_sequence", tuple(self.skill_sequence))
        object.__setattr__(self, "skill_latencies_ms", tuple(int(x) for x in self.skill_latencies_ms))
        object.__setattr__(self, "timestamp", _as_utc(self.timestamp))

        # Fail-closed validation. Every one of these is a condition under which
        # a downstream confidence score would be meaningless, so we refuse the
        # observation rather than record a misleading one.
        validate_tenant_id(self.tenant_id)
        if not self.task_id:
            raise ValueError("task_id is required")
        if self.task_type not in TASK_TYPES:
            raise ValueError(f"task_type must be one of {sorted(TASK_TYPES)}, got {self.task_type!r}")
        if not isinstance(self.task_complexity, int) or not 1 <= self.task_complexity <= 5:
            raise ValueError(f"task_complexity must be an int in 1..5, got {self.task_complexity!r}")
        if not self.skill_sequence:
            raise ValueError("skill_sequence must not be empty (a method with no steps is not a method)")
        if len(self.skill_latencies_ms) != len(self.skill_sequence):
            raise ValueError(
                f"skill_latencies_ms has {len(self.skill_latencies_ms)} entries "
                f"but skill_sequence has {len(self.skill_sequence)}"
            )
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}, got {self.outcome!r}")
        if not _is_hex64(self.prev_hash):
            raise ValueError(f"prev_hash must be 64 lowercase hex chars, got {self.prev_hash!r}")
        if self.user_feedback_score is not None and not 0.0 <= self.user_feedback_score <= 1.0:
            raise ValueError(f"user_feedback_score must be in [0, 1], got {self.user_feedback_score!r}")
        # Reject non-canonical JSON early — the hash must be reproducible.
        json.loads(self.outcome_details_json)

    # ── derived views ───────────────────────────────────────────────────

    @property
    def outcome_details(self) -> dict:
        """The free-form detail dict (decoded from its canonical JSON form)."""
        return json.loads(self.outcome_details_json)

    @property
    def observation_id(self) -> str:
        """Content-addressed id — the observation's own hash.

        Using the hash as the id means a pattern's ``observation_ids`` are
        themselves tamper-evident: you cannot swap in a different observation
        without the id changing.
        """
        return self.hash

    @property
    def is_success(self) -> bool:
        return self.outcome in _SUCCESS_OUTCOMES

    # ── serialisation + hashing ─────────────────────────────────────────

    def to_payload(self) -> dict:
        """Audit/disk representation. ``hash`` covers everything but itself."""
        return {
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp.isoformat(),
            "task_id": self.task_id,
            "task_type": self.task_type,
            "task_complexity": self.task_complexity,
            "skill_sequence": list(self.skill_sequence),
            "skill_latencies_ms": list(self.skill_latencies_ms),
            "outcome": self.outcome,
            "outcome_details": self.outcome_details,
            "prev_hash": self.prev_hash,
            "lom": self.lom,
            "user_feedback_received": self.user_feedback_received,
            "user_feedback_score": self.user_feedback_score,
            "hash": self.hash,
        }

    def compute_hash(self) -> str:
        """SHA256 over every field except ``hash`` itself.

        Mirrors ``observability._hashable_view``; the two must agree or
        verification silently passes on tampered records.
        """
        view = {k: v for k, v in self.to_payload().items() if k != "hash"}
        return sha256_hex(canonical_json(view))

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        task_id: str,
        task_type: str,
        task_complexity: int,
        skill_sequence: Sequence[str],
        skill_latencies_ms: Sequence[int],
        outcome: str,
        outcome_details: Optional[dict] = None,
        prev_hash: str = GENESIS_HASH,
        lom: str = "assistant.method_discovery::observe",
        timestamp: Optional[datetime] = None,
        user_feedback_received: bool = False,
        user_feedback_score: Optional[float] = None,
    ) -> "MethodObservation":
        """Build a fully-validated, self-hashed observation."""
        draft = cls(
            tenant_id=tenant_id,
            timestamp=timestamp or _utcnow(),
            task_id=task_id,
            task_type=task_type,
            task_complexity=task_complexity,
            skill_sequence=tuple(skill_sequence),
            skill_latencies_ms=tuple(skill_latencies_ms),
            outcome=outcome,
            outcome_details_json=canonical_json(outcome_details or {}),
            prev_hash=prev_hash,
            lom=lom,
            hash="",
            user_feedback_received=user_feedback_received,
            user_feedback_score=user_feedback_score,
        )
        return replace(draft, hash=draft.compute_hash())

    @classmethod
    def from_payload(cls, payload: dict) -> "MethodObservation":
        """Rehydrate from an audit payload (inverse of :meth:`to_payload`)."""
        return cls(
            tenant_id=payload["tenant_id"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            task_id=payload["task_id"],
            task_type=payload["task_type"],
            task_complexity=payload["task_complexity"],
            skill_sequence=tuple(payload["skill_sequence"]),
            skill_latencies_ms=tuple(payload["skill_latencies_ms"]),
            outcome=payload["outcome"],
            outcome_details_json=canonical_json(payload.get("outcome_details") or {}),
            prev_hash=payload["prev_hash"],
            lom=payload.get("lom", ""),
            hash=payload.get("hash", ""),
            user_feedback_received=bool(payload.get("user_feedback_received", False)),
            user_feedback_score=payload.get("user_feedback_score"),
        )


# ── Level 2: Pattern ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkstylePattern:
    """A candidate workflow pattern for ONE task type (ADR-0548 Level 2).

    The identity of a pattern is ``(tenant_id, task_type, skill_sequence)``.
    ``task_type`` is part of the key, not a label on it — that is what makes
    CONCEPT-0029 Constraint 1 structural rather than a convention someone can
    forget.
    """

    pattern_id: str
    pattern_name: str
    tenant_id: str
    task_type: str
    skill_sequence: tuple[str, ...]
    success_rate: float
    observation_count: int
    confidence_score: float
    first_observed: datetime
    last_observed: datetime
    observation_ids: tuple[str, ...]
    user_confirmed: bool = False
    user_confirmation_timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "skill_sequence", tuple(self.skill_sequence))
        object.__setattr__(self, "observation_ids", tuple(self.observation_ids))
        object.__setattr__(self, "first_observed", _as_utc(self.first_observed))
        object.__setattr__(self, "last_observed", _as_utc(self.last_observed))
        validate_tenant_id(self.tenant_id)
        if self.task_type not in TASK_TYPES:
            raise ValueError(f"task_type must be one of {sorted(TASK_TYPES)}, got {self.task_type!r}")
        if not self.skill_sequence:
            raise ValueError("skill_sequence must not be empty")
        if not 0.0 <= self.success_rate <= 1.0:
            raise ValueError(f"success_rate must be in [0, 1], got {self.success_rate!r}")
        if self.observation_count < 0:
            raise ValueError("observation_count must be >= 0")

    @staticmethod
    def make_id(tenant_id: str, task_type: str, skill_sequence: Sequence[str]) -> str:
        """Deterministic id for the ``(tenant, task_type, sequence)`` triple.

        Deterministic so the same pattern re-derived tomorrow is recognised as
        the same pattern (and therefore not announced twice), and tenant-scoped
        so two tenants' identical workflows never collide into one record.
        """
        digest = sha256_hex(canonical_json([tenant_id, task_type, list(skill_sequence)]))
        return f"{task_type}-{digest[:16]}"

    @staticmethod
    def make_name(task_type: str, skill_sequence: Sequence[str]) -> str:
        """Human-readable name, e.g. ``feature: dialectical -> loop -> e2e``."""
        steps = " -> ".join(s.lstrip("/") for s in skill_sequence)
        return f"{task_type}: {steps}"

    def to_payload(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "pattern_name": self.pattern_name,
            "tenant_id": self.tenant_id,
            "task_type": self.task_type,
            "skill_sequence": list(self.skill_sequence),
            "success_rate": self.success_rate,
            "observation_count": self.observation_count,
            "confidence_score": self.confidence_score,
            "first_observed": self.first_observed.isoformat(),
            "last_observed": self.last_observed.isoformat(),
            "observation_ids": list(self.observation_ids),
            "user_confirmed": self.user_confirmed,
            "user_confirmation_timestamp": (
                self.user_confirmation_timestamp.isoformat()
                if self.user_confirmation_timestamp
                else None
            ),
        }


class PatternRecognition:
    """Turn a set of observations into scored patterns (ADR-0548 Level 2).

    Pure aggregation: it reads observations and returns patterns. It does not
    write, audit, or decide anything — :class:`MethodDiscovery` does that. That
    split is what lets the recognition logic be unit-tested exhaustively
    without an audit chain in the loop.
    """

    def __init__(self, *, scorer: Optional[ConfidenceScorer] = None):
        self.scorer = scorer or ConfidenceScorer()

    def group(
        self, observations: Iterable[MethodObservation]
    ) -> dict[tuple[str, tuple[str, ...]], list[MethodObservation]]:
        """Bucket observations by ``(task_type, skill_sequence)``.

        This is the stratification boundary. Two runs of the same skills on
        different task types land in different buckets and can never merge.
        """
        buckets: dict[tuple[str, tuple[str, ...]], list[MethodObservation]] = {}
        for obs in observations:
            key = (obs.task_type, tuple(obs.skill_sequence))
            buckets.setdefault(key, []).append(obs)
        return buckets

    def recognize(
        self,
        observations: Iterable[MethodObservation],
        *,
        min_observations: int = 1,
        confirmed_pattern_ids: Iterable[str] = (),
    ) -> list[tuple[WorkstylePattern, ConfidenceBreakdown]]:
        """Derive every candidate pattern, scored, highest confidence first.

        Args:
            observations: The observation set (any order; must all belong to
                one tenant — mixing tenants raises).
            min_observations: Buckets smaller than this are skipped entirely.
                Defaults to 1 so the caller can see weak candidates; the
                *announcement* gate is the confidence threshold, not this.
            confirmed_pattern_ids: Patterns the user has explicitly confirmed.

        Returns:
            ``(pattern, breakdown)`` pairs. The breakdown is kept alongside the
            pattern rather than folded into it so the audit event can carry the
            full derivation without recomputing it (and possibly differing).
        """
        confirmed = set(confirmed_pattern_ids)
        results: list[tuple[WorkstylePattern, ConfidenceBreakdown]] = []

        observations = list(observations)
        # Tenant uniformity is checked across the WHOLE input, not per bucket.
        # Per-bucket was the first implementation and it was wrong: two tenants
        # whose observations happened to have different skill sequences landed
        # in different buckets, each internally uniform, and the mixed set was
        # accepted — returning patterns for both tenants from one call.
        tenants = {o.tenant_id for o in observations}
        if len(tenants) > 1:
            raise ValueError(f"observations span multiple tenants: {sorted(tenants)}")

        buckets = self.group(observations)
        for (task_type, sequence), group in buckets.items():
            if len(group) < min_observations:
                continue

            tenant_id = group[0].tenant_id
            ordered = sorted(group, key=lambda o: o.timestamp)
            successes = sum(1 for o in ordered if o.is_success)
            success_rate = successes / len(ordered)
            pattern_id = WorkstylePattern.make_id(tenant_id, task_type, sequence)

            breakdown = self.scorer.score_parts(
                skill_sequence=sequence,
                success_rate=success_rate,
                observation_count=len(ordered),
                last_observed=ordered[-1].timestamp,
            )
            pattern = WorkstylePattern(
                pattern_id=pattern_id,
                pattern_name=WorkstylePattern.make_name(task_type, sequence),
                tenant_id=tenant_id,
                task_type=task_type,
                skill_sequence=sequence,
                success_rate=success_rate,
                observation_count=len(ordered),
                confidence_score=breakdown.confidence,
                first_observed=ordered[0].timestamp,
                last_observed=ordered[-1].timestamp,
                observation_ids=tuple(o.observation_id for o in ordered),
                user_confirmed=pattern_id in confirmed,
            )
            results.append((pattern, breakdown))

        results.sort(key=lambda pair: (-pair[0].confidence_score, pair[0].pattern_id))
        return results


# ── Level 3: Event handling ─────────────────────────────────────────────────


class MethodDiscovery:
    """Observe completed tasks and announce patterns that clear the bar.

    Tenant-bound end to end: the sink is bound at construction, every read is
    tenant-filtered by the ADR-0314 store, and a null tenant raises before any
    I/O happens.
    """

    def __init__(
        self,
        tenant_id: str,
        *,
        sink: Optional[MethodAuditSink] = None,
        scorer: Optional[ConfidenceScorer] = None,
        threshold: float = DISCOVERY_THRESHOLD,
    ):
        self.tenant_id = validate_tenant_id(tenant_id)
        self.sink = sink if sink is not None else MethodAuditSink(self.tenant_id)
        if self.sink.tenant_id != self.tenant_id:
            raise ValueError("sink tenant does not match MethodDiscovery tenant")
        self.recognizer = PatternRecognition(scorer=scorer)
        self.threshold = threshold

    # ── observe ─────────────────────────────────────────────────────────

    async def observe(
        self,
        *,
        task_id: str,
        task_type: str,
        task_complexity: int,
        skill_sequence: Sequence[str],
        skill_latencies_ms: Sequence[int],
        outcome: str,
        outcome_details: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
        user_feedback_received: bool = False,
        user_feedback_score: Optional[float] = None,
    ) -> MethodObservation:
        """Record one completed task.

        The observation links to the current chain head and is audited before
        the head advances. If the audit write fails, this raises and NOTHING is
        recorded — the chain never gains a link whose audit record is missing.
        """
        observation = MethodObservation.create(
            tenant_id=self.tenant_id,
            task_id=task_id,
            task_type=task_type,
            task_complexity=task_complexity,
            skill_sequence=skill_sequence,
            skill_latencies_ms=skill_latencies_ms,
            outcome=outcome,
            outcome_details=outcome_details,
            prev_hash=self.sink.chain_head(),
            lom="assistant.method_discovery::observe",
            timestamp=timestamp,
            user_feedback_received=user_feedback_received,
            user_feedback_score=user_feedback_score,
        )
        await self.sink.record_observation(observation)
        return observation

    # ── discover ────────────────────────────────────────────────────────

    async def load_observations(self) -> list[MethodObservation]:
        """Every observation for this tenant, oldest first."""
        payloads = await self.sink.read_observation_payloads(limit=100000)
        return [MethodObservation.from_payload(p) for p in payloads]

    async def current_patterns(self) -> list[tuple[WorkstylePattern, ConfidenceBreakdown]]:
        """Re-derive all patterns from the audit trail (read-only, no writes).

        Patterns are always recomputed from observations rather than read from
        a cache, so the dashboard cannot show a pattern the audit trail does
        not support. ``patterns.json`` is only ever a snapshot of this.
        """
        observations = await self.load_observations()
        confirmed = self._load_confirmations()
        return self.recognizer.recognize(observations, confirmed_pattern_ids=confirmed)

    async def discover(self) -> list[tuple[WorkstylePattern, ConfidenceBreakdown]]:
        """Recompute patterns and audit any that newly cross the threshold.

        A pattern is announced at most once (keyed by ``pattern_id``): the
        already-announced set is read back from the audit trail, not from local
        state, so a lost cache cannot cause a duplicate announcement and a
        deleted cache cannot suppress a real one.

        Returns:
            The newly discovered ``(pattern, breakdown)`` pairs.
        """
        scored = await self.current_patterns()
        already = {p.get("pattern_id") for p in await self.sink.read_discovered_payloads(limit=100000)}

        newly: list[tuple[WorkstylePattern, ConfidenceBreakdown]] = []
        for pattern, breakdown in scored:
            if pattern.pattern_id in already:
                continue
            if not ConfidenceScorer.is_discoverable(
                pattern.confidence_score, user_confirmed=pattern.user_confirmed
            ):
                continue
            await self.sink.record_discovery(pattern, breakdown)
            newly.append((pattern, breakdown))

        self._write_snapshot(scored)
        return newly

    async def observe_and_discover(
        self, **observe_kwargs
    ) -> tuple[MethodObservation, list[tuple[WorkstylePattern, ConfidenceBreakdown]]]:
        """:meth:`observe` then :meth:`discover` — the normal task-completion hook."""
        observation = await self.observe(**observe_kwargs)
        return observation, await self.discover()

    # ── user confirmation (CONCEPT-0029 Constraint 4) ───────────────────

    def confirm_pattern(self, pattern_id: str) -> None:
        """Record that the user explicitly confirmed a pattern.

        Confirmation is stored, never inferred from behaviour — inferring it is
        Attack 2. It lets a pattern be surfaced below the statistical threshold
        (:meth:`ConfidenceScorer.is_discoverable`) but never lets one be
        applied autonomously on its own.
        """
        if not pattern_id:
            raise ValueError("pattern_id is required")
        confirmed = set(self._load_confirmations())
        confirmed.add(pattern_id)
        self._atomic_write(
            self._confirmations_file,
            canonical_json({"tenant_id": self.tenant_id, "confirmed": sorted(confirmed)}),
        )

    def _load_confirmations(self) -> list[str]:
        try:
            data = json.loads(self._confirmations_file.read_text())
        except (OSError, ValueError):
            return []
        if data.get("tenant_id") != self.tenant_id:
            return []
        return [c for c in data.get("confirmed", []) if isinstance(c, str)]

    # ── local snapshot (derived cache, never a source of truth) ─────────

    @property
    def _state_dir(self) -> Path:
        return self.sink._state_dir

    @property
    def _confirmations_file(self) -> Path:
        return self._state_dir / "confirmations.json"

    @property
    def _patterns_file(self) -> Path:
        return self._state_dir / "patterns.json"

    def _write_snapshot(self, scored: Sequence[tuple[WorkstylePattern, ConfidenceBreakdown]]) -> None:
        """Persist a read-optimised snapshot of the current patterns.

        Purely derived: deleting this file loses nothing, because
        :meth:`current_patterns` rebuilds it from the audit trail.
        """
        body = canonical_json(
            {
                "tenant_id": self.tenant_id,
                "generated": _utcnow().isoformat(),
                "threshold": self.threshold,
                "patterns": [
                    {**p.to_payload(), "confidence_derivation": b.to_payload()} for p, b in scored
                ],
            }
        )
        self._atomic_write(self._patterns_file, body)

    @staticmethod
    def _atomic_write(path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
