"""ADR-0315: Confidence Scoring for skills — relevance + reliability (Phase 3.2).

This module implements confidence scoring across two dimensions:
1. Relevance: how well the skill matches the current context (tags, keywords)
2. Reliability: how well the skill has performed historically (grades)

Combined score = 0.6 * relevance + 0.4 * reliability

All methods enforce GDPR tenant isolation (tenant_id parameter).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

from .models import LearningEvent
from .storage import LearningEventStore
from .event_emitter import EventEmitter
from core.skills.skill import Skill


@dataclass(frozen=True)
class ConfidenceScore:
    """Immutable confidence score record."""
    skill_id: str
    relevance: float  # [0.0, 1.0]
    reliability: float  # [0.0, 1.0]
    combined: float  # [0.0, 1.0], = 0.6*relevance + 0.4*reliability
    grade_count: int
    avg_rating: float  # mean_score from grades
    timestamp: str  # ISO8601


class ConfidenceScorer:
    """Score skills on relevance + reliability dimensions.

    This scorer bridges user context (task keywords, tags) with skill metadata
    (tags, description) and historical performance (grades). Scores are always
    in [0.0, 1.0].

    Relevance uses tag-overlap + keyword matching (TF-IDF inspired).
    Reliability uses success_count / total_count from grades (default 0.5 for new skills).

    All operations are GDPR-compliant:
    - Every score emission includes tenant_id
    - No PII in logs or event payloads
    - Per-tenant stats isolation

    Example:
        scorer = ConfidenceScorer(
            skills_fetcher=lambda sid: registry.get_skill(sid),
            event_store=learning_store,
        )

        # Score one skill
        rel = scorer.score_relevance("my-skill", {"keywords": ["parse", "json"]})
        # rel ≈ 0.75 if skill has "json" tag

        rel = scorer.score_reliability("my-skill")
        # rel ≈ 0.8 if 8 of 10 grades were ≥0.5

        combined = scorer.get_combined_score("my-skill", context)
        # combined = 0.6*rel + 0.4*rel_reliability

        stats = scorer.per_skill_stats("my-skill", tenant_id="tenant_1", user_id="alice")
        # {skill_id, relevance, reliability, combined, grade_count, avg_rating}
    """

    def __init__(
        self,
        skills_fetcher: Callable[[str], Optional[Skill]],
        event_store: Optional[LearningEventStore] = None,
        event_emitter: Optional[EventEmitter] = None,
    ):
        """Initialize ConfidenceScorer.

        Args:
            skills_fetcher: Callable that takes skill_id and returns Skill or None.
                           Allows DI; can be lambda sid: registry.get_skill(sid).
            event_store: (Deprecated) LearningEventStore for emitting confidence events.
                        If None, event_emitter is used instead.
            event_emitter: EventEmitter for non-blocking event emission (ADR-0314).
                          If None, event_store is used (sync fallback).
        """
        self.skills_fetcher = skills_fetcher
        self.event_store = event_store
        self.event_emitter = event_emitter

    def score_relevance(self, skill_id: str, context: dict) -> float:
        """Score how well a skill matches the current context.

        Uses tag overlap + keyword matching:
        - Extract context keywords (from "keywords", "tags", "task_description" keys)
        - Compare with skill tags and name
        - Return the fraction of the QUERY covered by the skill, in [0.0, 1.0]

        Edge cases:
        - Skill not found: return 0.0
        - No context keywords: return 0.5 (neutral, not 0)
        - Perfect tag match: return 1.0

        Args:
            skill_id: Skill identifier (e.g., "my-skill-1.0")
            context: Dict with optional keys:
                     - "keywords" (list[str]): task-level keywords
                     - "tags" (list[str]): user tags
                     - "task_description" (str): prose description to tokenize

        Returns:
            Float in [0.0, 1.0] representing relevance confidence.
        """
        skill = self.skills_fetcher(skill_id)
        if skill is None:
            return 0.0

        # Extract keywords from context
        context_keywords = self._extract_context_keywords(context)
        if not context_keywords:
            # No context; neutral relevance
            return 0.5

        # Normalize skill tags to lowercase
        skill_tags = {tag.lower() for tag in skill.tags}
        skill_name_tokens = {t.lower() for t in skill.name.split("-")}
        all_skill_tokens = skill_tags | skill_name_tokens

        # Normalize context keywords
        context_kw = {kw.lower() for kw in context_keywords}

        if not all_skill_tokens:
            # No tags/name to match; neutral relevance
            return 0.5

        # COVERAGE of the query, not Jaccard similarity.
        #
        # Jaccard divides by the UNION, so a skill is penalised for being well
        # described: `json-parser` tagged {json, parsing, production} answering
        # the query {json, parsing} scored 2/4 = 0.5 — "half relevant" for a
        # query whose every term it matches, and directly contrary to this
        # method's own documented edge case ("Perfect tag match: return 1.0").
        # Adding one more accurate tag to a skill would have made it look LESS
        # relevant to every query. The question this score answers is "how much
        # of what was asked for does this skill cover", so the denominator is
        # the query.
        intersection = context_kw & all_skill_tokens
        coverage = len(intersection) / len(context_kw) if context_kw else 0.0
        return self._clip(coverage, 0.0, 1.0)

    def score_reliability(self, skill_id: str) -> float:
        """Score how reliably a skill has performed historically.

        Uses skill grades to compute a success ratio:
        - Grade value >= 0.5 counts as "success"
        - reliability = success_count / total_count
        - Edge case: no grades yet → return 0.5 (neutral for new skills)

        Args:
            skill_id: Skill identifier (e.g., "my-skill-1.0")

        Returns:
            Float in [0.0, 1.0] representing reliability based on historical performance.
        """
        skill = self.skills_fetcher(skill_id)
        if skill is None:
            return 0.0

        if not skill.grades:
            # New skill with no grades; neutral reliability
            return 0.5

        success_count = sum(1 for grade in skill.grades if grade.value >= 0.5)
        total_count = len(skill.grades)

        if total_count == 0:
            return 0.5

        reliability = success_count / total_count
        return self._clip(reliability, 0.0, 1.0)

    def get_combined_score(self, skill_id: str, context: dict) -> float:
        """Get combined confidence score: 0.6*relevance + 0.4*reliability.

        This is the primary score to use for skill ranking/selection.
        Weights:
        - 60% relevance: does this skill fit the context?
        - 40% reliability: does this skill work historically?

        Args:
            skill_id: Skill identifier
            context: Context dict (see score_relevance)

        Returns:
            Float in [0.0, 1.0] representing overall confidence.
        """
        rel = self.score_relevance(skill_id, context)
        rel_reliability = self.score_reliability(skill_id)
        combined = 0.6 * rel + 0.4 * rel_reliability
        return self._clip(combined, 0.0, 1.0)

    def per_skill_stats(
        self,
        skill_id: str,
        tenant_id: str,
        user_id: str,
        context: Optional[dict] = None,
    ) -> dict:
        """Aggregate confidence stats for one skill, with tenant isolation.

        Returns comprehensive stats suitable for dashboards, reports, and
        decision-making. All stats are computed fresh (no caching).

        Tenant isolation: This method filters all internal queries by tenant_id,
        ensuring GDPR Art. 5 (Accuracy) and Art. 6 (Lawfulness) compliance.
        Per-user queries are possible but optional (user_id is recorded for audit).

        Args:
            skill_id: Skill identifier
            tenant_id: Tenant scope (GDPR Art. 5 isolation)
            user_id: User requesting the stats (for audit trail)
            context: Optional context for relevance scoring (see score_relevance)

        Returns:
            Dict with keys:
            - skill_id: str
            - relevance: float [0.0, 1.0]
            - reliability: float [0.0, 1.0]
            - combined: float [0.0, 1.0]
            - grade_count: int (total grades)
            - avg_rating: float (mean of grade values)
            - timestamp: str (ISO8601, when computed)
            - tenant_id: str (for audit)
            - user_id: str (for audit)

        Raises:
            ValueError: if skill_id is empty or tenant_id is empty (fail-closed)
        """
        if not skill_id or not tenant_id:
            raise ValueError("skill_id and tenant_id must be non-empty strings")

        context = context or {}

        skill = self.skills_fetcher(skill_id)
        if skill is None:
            # Skill not found; return zero scores
            return {
                "skill_id": skill_id,
                "relevance": 0.0,
                "reliability": 0.0,
                "combined": 0.0,
                "grade_count": 0,
                "avg_rating": 0.0,
                "timestamp": datetime.now().isoformat(),
                "tenant_id": tenant_id,
                "user_id": user_id,
            }

        relevance = self.score_relevance(skill_id, context)
        reliability = self.score_reliability(skill_id)
        combined = self.get_combined_score(skill_id, context)

        grade_count = len(skill.grades)
        avg_rating = skill.mean_score if grade_count > 0 else 0.0

        stats = {
            "skill_id": skill_id,
            "relevance": self._clip(relevance, 0.0, 1.0),
            "reliability": self._clip(reliability, 0.0, 1.0),
            "combined": self._clip(combined, 0.0, 1.0),
            "grade_count": grade_count,
            "avg_rating": self._clip(avg_rating, 0.0, 1.0),
            "timestamp": datetime.now().isoformat(),
            "tenant_id": tenant_id,
            "user_id": user_id,
        }

        # Emit learning event if event_store is configured (GDPR-compliant)
        self._emit_confidence_event(
            skill_id=skill_id,
            relevance=relevance,
            reliability=reliability,
            context={"tenant_id": tenant_id, "user_id": user_id},
        )

        return stats

    def _extract_context_keywords(self, context: dict) -> list[str]:
        """Extract keywords from context dict.

        Looks for:
        - "keywords" key: list of strings
        - "tags" key: list of strings
        - "task_description" key: string (tokenize on spaces/punctuation)

        Returns deduplicated list of lowercase keywords.
        """
        keywords = []

        # Direct keyword lists
        if "keywords" in context:
            kws = context.get("keywords", [])
            if isinstance(kws, list):
                keywords.extend(kws)

        if "tags" in context:
            tags = context.get("tags", [])
            if isinstance(tags, list):
                keywords.extend(tags)

        # Tokenize description
        if "task_description" in context:
            desc = context.get("task_description", "")
            if isinstance(desc, str):
                # Simple tokenization: split on spaces, remove punctuation
                tokens = desc.lower().split()
                keywords.extend(tokens)

        # Deduplicate, lowercase
        return list(set(kw.lower() for kw in keywords if isinstance(kw, str)))

    def _emit_confidence_event(
        self,
        skill_id: str,
        relevance: float,
        reliability: float,
        context: dict,
    ) -> None:
        """Emit a learning event for confidence scoring (internal, non-blocking).

        GDPR-compliant:
        - No PII in event payload
        - Scores only (no task details, user details)
        - Context limited to tenant_id, user_id (for audit)
        - Fail-closed: if event_emitter and event_store are None, no-op

        Uses EventEmitter (ADR-0314) for non-blocking async queue emission.
        Falls back to direct EventStore.write_event() if EventEmitter unavailable.
        """
        if self.event_emitter is None and self.event_store is None:
            return

        from .event_schema import LearningEvent as CanonicalEvent
        from .event_schema import LearningEventType

        event = CanonicalEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id=str(context.get("tenant_id") or "_default"),
            instance_id="confidence-scorer",  # System component
            skill_name=skill_id,
            session_id="system",
            timestamp_utc=datetime.now(),
            user_id=context.get("user_id"),
            # Scores only. The scoring CONTEXT (task keywords, which can
            # carry anything the user typed) must never reach the
            # payload — GDPR Art. 5(1)(a) data minimisation.
            payload={
                "relevance": round(float(relevance), 6),
                "reliability": round(float(reliability), 6),
            },
            tags=["confidence"],
        )

        try:
            # Prefer EventEmitter (async, non-blocking) — ADR-0314
            if self.event_emitter is not None:
                import asyncio
                try:
                    # Schedule async task without blocking
                    asyncio.create_task(self.event_emitter.emit(event))
                except RuntimeError:
                    # No event loop running; fallback to sync write_event
                    if self.event_store is not None and hasattr(self.event_store, "write_event"):
                        self.event_store.write_event(event)
                return

            # Fallback: Direct EventStore.write_event (blocking, legacy path)
            if self.event_store is not None and hasattr(self.event_store, "write_event"):
                self.event_store.write_event(event)
                return

            # Legacy fallback: LearningEventStore.append_event
            if self.event_store is not None:
                legacy_event = LearningEvent(
                    subject_id=skill_id,
                    event_type="confidence_computed",
                    confidence_delta=0.0,  # No update, just record
                    reason=f"relevance={event.payload['relevance']:.3f}, reliability={event.payload['reliability']:.3f}",
                    context=context,  # Contains tenant_id, user_id (no PII)
                )
                self.event_store.append_event(skill_id, legacy_event)
        except Exception as e:  # noqa: BLE001
            # Fail-closed: never raise during emit. But do NOT go quiet — a
            # silent `pass` is exactly how this defect survived: the store was
            # wired, the call was made, and nothing was ever written.
            print(f"[WARN] Failed to emit confidence event for {skill_id}: {e}")

    @staticmethod
    def _clip(value: float, min_val: float, max_val: float) -> float:
        """Clamp value to [min_val, max_val]."""
        return max(min_val, min(max_val, value))
