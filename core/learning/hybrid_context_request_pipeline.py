"""Request Pipeline Integration: Hybrid Context Model (Phase 4, ADR-0555).

Wires HybridContextModel + ContextSelectorSkill into the LLM request path.

Flow:
1. RequestPipeline.enrich_request() called with raw request
2. ContextSelectorSkill selects quality mode + context items
3. HybridContextModel builds Tier 1 (from the Phase 3 adapters) + Tier 2 + merge
4. Merged context injected into system prompt
5. Request sent to LLM (Claude)
6. Enrichment audited on the tenant CORE hash chain (ADR-0232/0233)

Adversarial review L-11 — what this version guarantees:
- Tier 1 comes ONLY from the Phase 3 adapters (``DecisionAdapter`` /
  ``OutcomeAdapter`` / ``ProfileAdapter`` / ``AttentionAdapter`` Protocols in
  ``hybrid_context.py``). A field with no live source is NOT injected into the
  prompt — no hard-coded dummy decisions / 0.7 success rate / 5000-token budget.
  (``ImmutableContextBase`` still carries its neutral defaults — 0.5 is the
  ADR-0317 small-n value, 0 tokens = unknown — but ``_serialize_context`` only
  renders fields listed in ``tier1_sources``.)
- ``_build_tier2_layers`` returns ONLY the layers injected for THIS request;
  ``layers_injected`` counts those. The merge still verifies the user's whole
  hash chain (``merge_with_fallback`` requires continuity from the first
  layer), and the merged dict holds one entry per layer NAME (latest wins),
  so the prompt never carries stale duplicates.
- ``_emit_audit_event`` writes a real, content-free record to the core chain
  through ``core_audit_event`` — fail-closed: an un-auditable enrichment raises.
- ``get_request_pipeline(tenant_id)`` is keyed per (validated) tenant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.learning.event_persistence import core_audit_event
from core.learning.hybrid_context import (
    AttentionAdapter,
    DecisionAdapter,
    HybridContextModel,
    ImmutableContextBase,
    InjectedLayer,
    OutcomeAdapter,
    ProfileAdapter,
)
from core.skills.os_skills.context_selector import ContextSelectorSkill
from core.tenants.validation import validate_tenant_id

logger = logging.getLogger(__name__)

_LOM = "core.learning.hybrid_context_request_pipeline:HybridContextRequestPipeline.enrich_request"

# Tier 1 fields and the adapter each one is sourced from.
_TIER1_FIELDS = ("recent_decisions", "user_profile", "success_rate", "attention_budget_remaining")


@dataclass
class EnrichedRequest:
    """Request enriched with hybrid context."""

    original_request: Dict[str, Any]
    system_prompt: str
    context_metadata: Dict[str, Any]  # Selection decision, metrics, etc.
    quality_mode: str
    layers_injected: int
    tier1_sources: list[str] = field(default_factory=list)  # which Tier 1 fields had a live source
    audit_ref: str = ""  # core-chain reference of the enrichment record


# ── Phase 3 adapters (concrete, thin) ───────────────────────────────────────


class DecisionHistoryAdapter:
    """``DecisionAdapter`` over ``decision_history.DecisionHistoryStore`` (ADR-0316)."""

    def __init__(self, store: Any, window_days: int = 30):
        self.store = store
        self.window_days = window_days

    def get_recent_decisions(self, user_id: str, tenant_id: str, limit: int = 10) -> list[dict]:
        from datetime import datetime, timedelta

        end = datetime.utcnow()
        start = end - timedelta(days=self.window_days)
        records = self.store.get_decisions_by_date_range(tenant_id, start, end, limit=10000)
        mine = [r for r in records if getattr(r, "user_id", None) == user_id]
        mine.sort(key=lambda r: r.timestamp_utc)
        return [
            {
                "decision_id": r.decision_id,
                "choice_type": r.choice_type,
                "chosen": r.chosen,
                "confidence_score": r.confidence_score,
                "timestamp_utc": r.timestamp_utc.isoformat(),
            }
            for r in mine[-limit:]
        ]


class OutcomeFeedbackAdapter:
    """``OutcomeAdapter`` over ``outcome_feedback.OutcomeFeedbackStore`` (ADR-0317).

    Scoped to the user's own decisions (via the decision adapter); the store
    applies the small-n suppression (N < 10 → 0.5).
    """

    def __init__(self, store: Any, decision_adapter: Optional[DecisionAdapter] = None):
        self.store = store
        self.decision_adapter = decision_adapter

    def get_success_rate(self, user_id: str, tenant_id: str) -> float:
        decision_ids: Optional[list[str]] = None
        if self.decision_adapter is not None:
            decision_ids = [
                d["decision_id"]
                for d in self.decision_adapter.get_recent_decisions(user_id, tenant_id, limit=1000)
            ]
        return float(self.store.compute_success_rate(tenant_id, decision_ids))


class UserProfileAdapter:
    """``ProfileAdapter`` over ``user_profile.UserProfileManager`` (ADR-0318)."""

    _FIELDS = ("decision_style", "conciseness_preference", "skill_weights", "preferred_models", "operator_override")

    def __init__(self, manager: Any):
        self.manager = manager

    def get_profile(self, user_id: str, tenant_id: str) -> dict:
        profile = self.manager.get_profile(user_id, tenant_id)
        data = profile.to_dict() if hasattr(profile, "to_dict") else dict(profile)
        return {k: data[k] for k in self._FIELDS if k in data}


class AttentionTrackerAdapter:
    """``AttentionAdapter`` over per-user ``attention_budget.AttentionTracker`` objects (ADR-0319)."""

    def __init__(self, trackers: Optional[Dict[tuple[str, str], Any]] = None):
        self.trackers: Dict[tuple[str, str], Any] = trackers if trackers is not None else {}

    def register(self, tracker: Any) -> None:
        self.trackers[(tracker.budget.user_id, tracker.budget.tenant_id)] = tracker

    def get_remaining_budget(self, user_id: str, tenant_id: str) -> int:
        tracker = self.trackers.get((user_id, tenant_id))
        if tracker is None:
            raise LookupError(f"no attention tracker for user in tenant {tenant_id!r}")
        return int(tracker.get_remaining_context())


# ── Pipeline ────────────────────────────────────────────────────────────────


class HybridContextRequestPipeline:
    """Pipeline: enrich requests with hybrid context before LLM call."""

    def __init__(
        self,
        tenant_id: str = "_default",
        context_model: Optional[HybridContextModel] = None,
        context_selector: Optional[ContextSelectorSkill] = None,
        *,
        decision_adapter: Optional[DecisionAdapter] = None,
        outcome_adapter: Optional[OutcomeAdapter] = None,
        profile_adapter: Optional[ProfileAdapter] = None,
        attention_adapter: Optional[AttentionAdapter] = None,
    ):
        """Initialize pipeline.

        Args:
            tenant_id: Tenant identifier (validated)
            context_model: HybridContextModel instance (creates a tenant-bound one if None)
            context_selector: ContextSelectorSkill instance (creates a tenant-bound one if
                None — NOT the tenant-ignoring global ``get_context_selector`` singleton)
            decision_adapter / outcome_adapter / profile_adapter / attention_adapter:
                Phase 3 sources for Tier 1. Explicit arguments win over the ones
                carried by ``context_model``; a missing adapter means the field is
                simply not injected.
        """
        self.tenant_id = validate_tenant_id(tenant_id)
        if context_model is not None and context_model.tenant_id != self.tenant_id:
            raise ValueError("context_model is bound to a different tenant")
        self.context_model = context_model or HybridContextModel(
            self.tenant_id,
            decision_adapter=decision_adapter,
            outcome_adapter=outcome_adapter,
            profile_adapter=profile_adapter,
        )
        if decision_adapter is not None:
            self.context_model.decision_adapter = decision_adapter
        if outcome_adapter is not None:
            self.context_model.outcome_adapter = outcome_adapter
        if profile_adapter is not None:
            self.context_model.profile_adapter = profile_adapter
        self.attention_adapter = attention_adapter
        self.context_selector = context_selector or ContextSelectorSkill(self.tenant_id)
        self._tier1_sources: Dict[str, list[str]] = {}  # "user:session" -> sourced fields

    async def enrich_request(
        self,
        request: Dict[str, Any],
        user_id: str,
        session_id: str,
        system_load_p99_ms: Optional[int] = None,
    ) -> EnrichedRequest:
        """Enrich request with hybrid context.

        Args:
            request: Raw request dict with keys:
                - messages: list[{role, content}]
                - metadata: {task_type, ...}
                - system_prompt: (optional) existing system prompt
            user_id: User identifier
            session_id: Session identifier
            system_load_p99_ms: Current system P99 latency (for load adjustment)

        Returns:
            EnrichedRequest with injected context

        Raises:
            RuntimeError: when the enrichment could not be audited on the core
                chain (fail-closed, ADR-0232/0233)
        """
        if not user_id or not session_id:
            raise ValueError("user_id and session_id required")
        task_type = request.get("metadata", {}).get("task_type", "general")

        # Step 1: Call ContextSelectorSkill
        selection = self.context_selector.execute(
            task_type=task_type,
            user_id=user_id,
            time_budget_ms=1000,
            system_load_p99_ms=system_load_p99_ms,
        )

        logger.info(
            f"Context selection: mode={selection.quality_mode.value}, "
            f"confidence={selection.confidence:.2f}"
        )

        # Step 2: Tier 1 base — sourced fields only (no dummy data)
        try:
            tier1_base, tier1_sources = self._build_tier1_base(user_id, session_id)
        except Exception as e:  # noqa: BLE001 — Tier 1 unavailable ⇒ no base, no prompt context
            logger.warning(f"Failed to build Tier 1 base: {e}")
            tier1_base, tier1_sources = None, []

        # Step 3: Tier 2 layers for THIS request only
        tier2_layers = self._build_tier2_layers(
            user_id, selection.selected_adr_ids, selection.selected_memory_ids
        )

        # Step 4: Merge with fallback — the WHOLE verified chain of the user goes
        # in (continuity is checked from the first layer); one entry per layer
        # name comes out, so earlier requests' layers are superseded, not repeated.
        merged_context: Optional[dict] = None
        if tier1_base is not None:
            try:
                merged_context = self.context_model.merge_with_fallback(
                    tier1_base, self._chain_dicts(user_id)
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Merge failed: {e}")
                merged_context = None

        # Step 5: Inject into system prompt (sourced Tier 1 fields + layers)
        system_prompt = request.get("system_prompt", "")
        if merged_context is not None:
            context_str = self._serialize_context(
                merged_context, selection.quality_mode.value, tier1_sources
            )
            system_prompt = f"{system_prompt}\n\n{context_str}".strip()

        enriched = EnrichedRequest(
            original_request=request,
            system_prompt=system_prompt,
            context_metadata={
                "selection_decision": {
                    "quality_mode": selection.quality_mode.value,
                    "confidence": selection.confidence,
                    "reasoning": selection.reasoning,
                    "execution_time_ms": selection.execution_time_ms,
                },
                "tier1_present": tier1_base is not None,
                "tier1_sources": list(tier1_sources),
                "tier2_layers": len(tier2_layers),
                "tier2_layer_names": [l.layer_name for l in tier2_layers],
                "selected_adrs": selection.selected_adr_ids,
                "selected_memory": selection.selected_memory_ids,
            },
            quality_mode=selection.quality_mode.value,
            layers_injected=len(tier2_layers),
            tier1_sources=list(tier1_sources),
        )

        # Step 6: Audit on the core chain (fail-closed)
        enriched.audit_ref = self._emit_audit_event(enriched, user_id, session_id)
        enriched.context_metadata["audit_ref"] = enriched.audit_ref

        return enriched

    # ── Tier 1 ──────────────────────────────────────────────────────────────

    def _build_tier1_base(
        self, user_id: str, session_id: str
    ) -> tuple[ImmutableContextBase, list[str]]:
        """Build (or reuse) the immutable Tier 1 base from the Phase 3 adapters.

        Snapshotted ONCE per (user, session) — ADR-0555: the base is captured at
        session start and never modified. A field whose adapter is absent or
        fails keeps the neutral default and is NOT listed in the returned
        sources (so it is never rendered into the prompt).
        """
        key = f"{user_id}:{session_id}"
        existing = self.context_model.base_snapshots.get(key)
        if existing is not None:
            return existing, list(self._tier1_sources.get(key, []))

        model = self.context_model
        sources: list[str] = []

        decisions: list[dict] = []
        adapter = model.decision_adapter
        if adapter is not None:
            try:
                decisions = list(adapter.get_recent_decisions(user_id, self.tenant_id, limit=10))
                sources.append("recent_decisions")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"decision adapter failed (field not injected): {e}")

        profile: dict = {}
        adapter = model.profile_adapter
        if adapter is not None:
            try:
                profile = dict(adapter.get_profile(user_id, self.tenant_id))
                sources.append("user_profile")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"profile adapter failed (field not injected): {e}")

        success_rate = 0.5  # ADR-0317 small-n neutral value; rendered only when sourced
        adapter = model.outcome_adapter
        if adapter is not None:
            try:
                success_rate = float(adapter.get_success_rate(user_id, self.tenant_id))
                sources.append("success_rate")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"outcome adapter failed (field not injected): {e}")

        attention_budget = 0  # unknown; rendered only when sourced
        if self.attention_adapter is not None:
            try:
                attention_budget = int(self.attention_adapter.get_remaining_budget(user_id, self.tenant_id))
                sources.append("attention_budget_remaining")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"attention adapter failed (field not injected): {e}")

        model.snapshot_base_context(
            user_id=user_id,
            session_id=session_id,
            decisions=decisions,
            profile=profile,
            success_rate=success_rate,
            attention_budget=attention_budget,
        )
        self._tier1_sources[key] = list(sources)
        return model.base_snapshots[key], sources

    # ── Tier 2 ──────────────────────────────────────────────────────────────

    def _build_tier2_layers(
        self, user_id: str, selected_adr_ids: list[str], selected_memory_ids: list[str]
    ) -> list[InjectedLayer]:
        """Inject the Tier 2 layers for THIS request and return exactly those.

        Args:
            user_id: User identifier
            selected_adr_ids: ADR IDs to include
            selected_memory_ids: Memory item IDs to include

        Returns:
            list[InjectedLayer] — only the layers injected by this call (never
            the user's whole history, which grows with every request)
        """
        layers: list[InjectedLayer] = []
        lom = "hybrid_context_request_pipeline.py:_build_tier2_layers"

        candidates: list[tuple[str, dict]] = []
        if selected_adr_ids:
            candidates.append(("adr_references", {"adr_ids": list(selected_adr_ids), "count": len(selected_adr_ids)}))
        if selected_memory_ids:
            candidates.append(("user_memory", {"memory_ids": list(selected_memory_ids), "count": len(selected_memory_ids)}))

        for layer_name, data in candidates:
            try:
                layer_hash = self.context_model.inject_layer(
                    user_id=user_id, layer_name=layer_name, data=data, lom=lom
                )
            except Exception as e:  # noqa: BLE001 — a rejected layer is dropped, never faked
                logger.warning(f"Failed to inject {layer_name} layer: {e}")
                continue
            history = self.context_model.injected_layers.get(user_id, [])
            if history and history[-1].hash == layer_hash:
                layers.append(history[-1])
        return layers

    def _chain_dicts(self, user_id: str) -> list[dict]:
        """The user's full layer chain as merge input (hash + prev_hash included)."""
        return [
            {
                "layer_name": l.layer_name,
                "version": l.version,
                "data": l.data,
                "hash": l.hash,
                "prev_hash": l.prev_hash,
                "status": l.status,
            }
            for l in self.context_model.injected_layers.get(user_id, [])
        ]

    # ── Prompt + audit ──────────────────────────────────────────────────────

    @staticmethod
    def _serialize_context(
        merged_context: Dict[str, Any], quality_mode: str, tier1_sources: list[str] = ()
    ) -> str:
        """Convert merged context to prompt string (sourced Tier 1 fields + layers only)."""
        lines = [f"## Context (Hybrid Model, Phase 4, mode={quality_mode})"]
        sourced = set(tier1_sources)

        if "recent_decisions" in sourced:
            decisions = merged_context.get("recent_decisions") or []
            lines.append(f"Recent decisions: {len(decisions)} decision(s)")

        if "user_profile" in sourced:
            profile = merged_context.get("user_profile") or {}
            if profile:
                lines.append(f"User profile: {profile}")

        if "success_rate" in sourced:
            lines.append(f"Success rate: {merged_context['success_rate']:.1%}")

        if "attention_budget_remaining" in sourced:
            lines.append(f"Attention budget: {merged_context['attention_budget_remaining']} tokens")

        # Tier 2 layers (one entry per layer name — latest wins in the merge)
        for key, value in merged_context.items():
            if key not in _TIER1_FIELDS:
                lines.append(f"{key}: {value}")

        return "\n".join(lines)

    def _emit_audit_event(
        self, enriched: EnrichedRequest, user_id: str, session_id: str
    ) -> str:
        """Write the enrichment record to the tenant CORE hash chain (content-free).

        Raises ``RuntimeError`` when the writer is unavailable or the record did
        not commit — an enrichment that cannot be audited is not returned.
        """
        sel = enriched.context_metadata.get("selection_decision", {})
        return core_audit_event(
            "hybrid_context_request_enriched",
            tenant_id=self.tenant_id,
            user=user_id,
            details={
                "component": "hybrid_context_request_pipeline",
                "session_id": session_id,
                "quality_mode": enriched.quality_mode,
                "confidence": sel.get("confidence"),
                "tier1_present": enriched.context_metadata.get("tier1_present", False),
                "tier1_sources": list(enriched.tier1_sources),
                "layers_injected": enriched.layers_injected,
                "layer_names": list(enriched.context_metadata.get("tier2_layer_names", [])),
                "selected_adr_count": len(enriched.context_metadata.get("selected_adrs", [])),
                "selected_memory_count": len(enriched.context_metadata.get("selected_memory", [])),
                "lom": _LOM,
            },
        )


# ── Per-tenant registry ─────────────────────────────────────────────────────

_pipelines: Dict[str, HybridContextRequestPipeline] = {}


def get_request_pipeline(tenant_id: str = "_default") -> HybridContextRequestPipeline:
    """Get or create the request pipeline of ONE tenant (keyed by validated id).

    The previous singleton ignored ``tenant_id`` after the first call, so a
    second tenant silently got the first tenant's model and layers (L-11d).
    """
    tid = validate_tenant_id(tenant_id)
    pipeline = _pipelines.get(tid)
    if pipeline is None:
        pipeline = HybridContextRequestPipeline(tid)
        _pipelines[tid] = pipeline
    return pipeline


def reset_request_pipelines() -> None:
    """Drop all cached pipelines (tests / tenant teardown)."""
    _pipelines.clear()
