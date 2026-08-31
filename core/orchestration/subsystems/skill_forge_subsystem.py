"""Skill Forge Subsystem: Auto-grading and Brain integration (ADR-0360).

Features:
- AsyncSkillRegistry wrapper with ThreadPoolExecutor for non-blocking access
- Auto-grading from LoopEngineer strategy outcomes (success +1, failure -0.5)
- Auto-promotion when mean_score > 0.7, uses >= 5, confidence_lower > 0.6
- ContextAPI integration for decision recording
- Event-driven skill creation and grading from Brain
"""

import asyncio
import logging
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.context_engineering.context_api import ContextAPI
from core.context_engineering.context_bus import ContextBus
from core.learning.adaptive_strategy import (
    SKILL_CONFIDENCE_DECAY_PER_WEEK,
    SKILL_MIN_GRADE_AGE_DAYS,
)
from core.learning.auto_grading import auto_grade, ConfidenceGrade
from .base import Subsystem
from .forge_apis import NamespacePolicy, ForgeQuota
from .forge_api_impl import ForgedSkillAPIImpl

logger = logging.getLogger(__name__)


@dataclass
class SkillForgeMetrics:
    """Track metrics for observability (ADR-0360, observability extension)."""
    skill_create_count: int = 0
    skill_create_latency_ms: List[float] = field(default_factory=list)
    skill_grade_count: int = 0
    skill_grade_latency_ms: List[float] = field(default_factory=list)
    skill_promote_count: int = 0
    skill_promote_latency_ms: List[float] = field(default_factory=list)
    auto_grade_count: int = 0
    auto_grade_failures: int = 0

    def record_create(self, latency_ms: float) -> None:
        self.skill_create_count += 1
        self.skill_create_latency_ms.append(latency_ms)
        if self.skill_create_latency_ms and len(self.skill_create_latency_ms) > 1000:
            self.skill_create_latency_ms = self.skill_create_latency_ms[-1000:]

    def record_grade(self, latency_ms: float) -> None:
        self.skill_grade_count += 1
        self.skill_grade_latency_ms.append(latency_ms)
        if self.skill_grade_latency_ms and len(self.skill_grade_latency_ms) > 1000:
            self.skill_grade_latency_ms = self.skill_grade_latency_ms[-1000:]

    def record_promote(self, latency_ms: float) -> None:
        self.skill_promote_count += 1
        self.skill_promote_latency_ms.append(latency_ms)

    def get_stats(self) -> Dict[str, Any]:
        """Return current metrics summary."""
        return {
            "skill_create_count": self.skill_create_count,
            "skill_create_latency_p95_ms": self._percentile(self.skill_create_latency_ms, 0.95),
            "skill_grade_count": self.skill_grade_count,
            "skill_grade_latency_p95_ms": self._percentile(self.skill_grade_latency_ms, 0.95),
            "skill_promote_count": self.skill_promote_count,
            "auto_grade_count": self.auto_grade_count,
            "auto_grade_failures": self.auto_grade_failures,
        }

    @staticmethod
    def _percentile(data: List[float], p: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p)
        return sorted_data[min(idx, len(sorted_data) - 1)]


@dataclass
class SkillGrade:
    """A single skill grade from auto-grading."""
    run_id: str
    score: float
    reason: str
    timestamp: float


class AsyncSkillRegistry:
    """Async wrapper for SkillRegistry (Layer 7) using ThreadPoolExecutor.

    Provides non-blocking access to synchronous Skill Forge operations
    like create, grade, promote, and list.
    """

    def __init__(self, registry: Optional[Any] = None, max_workers: int = 4):
        """Initialize async wrapper.

        Args:
            registry: SkillRegistry instance (can be deferred for testing)
            max_workers: ThreadPoolExecutor pool size
        """
        self.registry = registry
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    async def skill_create(
        self,
        name: str,
        body_md: str,
        description: str = "",
        skill_type: str = "learned-experience",
        claim: Optional[Dict[str, Any]] = None,
        scope: str = "session",
    ) -> Dict[str, Any]:
        """Create skill asynchronously.

        Args:
            name: Skill name (alphanumeric + . + _)
            body_md: Markdown skill body
            description: Short description
            skill_type: Type (domain, persona-style, repo-context, learned-experience)
            claim: Optional claim dict
            scope: Scope (session, project, user)

        Returns:
            SkillSpec dict with name, type, description, etc.
        """
        if not self.registry:
            return {
                "name": name,
                "type": skill_type,
                "description": description,
                "scope": scope,
                "error": "registry not initialized",
            }

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                self.executor,
                lambda: self.registry.create(
                    name=name,
                    type=skill_type,
                    body_md=body_md,
                    description=description,
                    claim=claim or {},
                    scope=scope,
                ),
            )
            # Convert SkillSpec dataclass to dict
            if hasattr(result, "__dict__"):
                return result.__dict__
            return result
        except Exception as e:
            logger.error(f"skill_create failed: {e}")
            return {"name": name, "error": str(e)}

    async def skill_grade(
        self,
        name: str,
        run_id: str,
        score: float,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Grade skill asynchronously.

        Args:
            name: Skill name
            run_id: Run ID for this grade
            score: Score 0.0-1.0
            notes: Optional feedback

        Returns:
            Updated SkillSpec dict
        """
        if not self.registry:
            return {"name": name, "score": score, "error": "registry not initialized"}

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                self.executor,
                lambda: self.registry.grade(name, run_id, score, notes=notes),
            )
            if hasattr(result, "__dict__"):
                return result.__dict__
            return result
        except Exception as e:
            logger.error(f"skill_grade failed: {e}")
            return {"name": name, "score": score, "error": str(e)}

    async def skill_promote(
        self,
        name: str,
        from_scope: str,
        to_scope: str,
    ) -> Dict[str, Any]:
        """Promote skill to higher scope asynchronously.

        Args:
            name: Skill name
            from_scope: Current scope
            to_scope: Target scope

        Returns:
            Result dict with success/error
        """
        if not self.registry:
            return {
                "name": name,
                "error": "registry not initialized",
            }

        loop = asyncio.get_event_loop()
        try:
            # For now, we'll try to call a promote method if it exists
            # In real implementation, this would use MultiSkillRegistry
            if hasattr(self.registry, "promote"):
                result = await loop.run_in_executor(
                    self.executor,
                    lambda: self.registry.promote(name, from_scope, to_scope),
                )
                return {"name": name, "from_scope": from_scope, "to_scope": to_scope, "success": True}
            else:
                logger.warning(f"registry does not have promote method")
                return {"name": name, "error": "promote not implemented"}
        except Exception as e:
            logger.error(f"skill_promote failed: {e}")
            return {"name": name, "error": str(e)}

    async def list_skills(
        self,
        namespace: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List skills asynchronously.

        Args:
            namespace: Optional namespace filter
            scope: Optional scope filter

        Returns:
            List of SkillSpec dicts
        """
        if not self.registry:
            return []

        loop = asyncio.get_event_loop()
        try:
            skills = await loop.run_in_executor(
                self.executor,
                lambda: self.registry.list(),
            )
            # Convert to dicts
            result = []
            for skill in skills:
                if hasattr(skill, "__dict__"):
                    result.append(skill.__dict__)
                else:
                    result.append(skill)
            return result
        except Exception as e:
            logger.error(f"list_skills failed: {e}")
            return []

    def shutdown(self) -> None:
        """Shutdown executor."""
        self.executor.shutdown(wait=True)


class SkillForgeSubsystem(Subsystem):
    """Skill Forge subsystem with auto-grading from Brain outcomes.

    Integrates with Layer 7 (Skill Forge) to:
    - Create skills from LoopEngineer strategies
    - Auto-grade skills based on strategy success/failure
    - Auto-promote when confidence threshold met
    - Record decisions in ContextAPI

    Phase C: Tenant-native persistence via ExecutionContext.tenant_id
    """

    def __init__(
        self,
        context: Optional[Any] = None,
        registry: Optional[Any] = None,
        auto_grade_success: float = 1.0,
        auto_grade_failure: float = -0.5,
        min_uses_for_promotion: int = 5,
        min_mean_score_for_promotion: float = 0.7,
        min_confidence_for_promotion: float = 0.6,
        namespace_policy: Optional[NamespacePolicy] = None,
        forge_quota: Optional[ForgeQuota] = None,
    ):
        """Initialize Skill Forge subsystem.

        Args:
            context: ExecutionContext (Phase C) with tenant_id for tenant-scoped operations
            registry: SkillRegistry instance
            auto_grade_success: Score for successful strategy
            auto_grade_failure: Score for failed strategy
            min_uses_for_promotion: Minimum uses before auto-promotion
            min_mean_score_for_promotion: Minimum mean score for auto-promotion
            min_confidence_for_promotion: Minimum confidence interval lower bound
            namespace_policy: NamespacePolicy for validation (created if None)
            forge_quota: ForgeQuota for limits (created if None)
        """
        # Phase C: Store ExecutionContext for tenant-native operations
        self.context = context
        self.tenant_id = context.tenant_id if context else "_default"

        self.registry = registry
        self.async_registry = AsyncSkillRegistry(registry)
        self.auto_grade_success = auto_grade_success
        self.auto_grade_failure = auto_grade_failure
        self.min_uses_for_promotion = min_uses_for_promotion
        self.min_mean_score_for_promotion = min_mean_score_for_promotion
        self.min_confidence_for_promotion = min_confidence_for_promotion

        # Track skill bindings and scores (per-tenant)
        self.strategy_skills: Dict[str, List[str]] = {}  # strategy -> [skills]
        self.skill_scores: Dict[str, List[float]] = {}  # skill_name -> [scores]
        self.skill_score_timestamps: Dict[str, List[float]] = {}  # ADR-0372: skill_name -> [timestamps for decay]
        self.skill_uses: Dict[str, int] = {}  # skill_name -> use count
        self.auto_promotion_count: int = 0

        self.hub: Optional[Any] = None
        self.context_api: Optional[ContextAPI] = None

        # ADR-0361: Policy and quota (per-tenant)
        self.namespace_policy = namespace_policy or NamespacePolicy()
        self.forge_quota = forge_quota or ForgeQuota()

        # Observability: metrics tracking
        self.metrics = SkillForgeMetrics()

    @property
    def name(self) -> str:
        return "skill_forge"

    @property
    def version(self) -> str:
        return "0.1.0"

    def startup(self, hub: Any) -> None:
        """Initialize subsystem and subscribe to events.

        Args:
            hub: SubsystemHub instance
        """
        self.hub = hub
        try:
            context_bus = getattr(hub, 'context_bus', None)
            if context_bus is None:
                # Create a ContextBus for testing if not available
                context_bus = ContextBus()
            self.context_api = ContextAPI("skill_forge", context_bus)
        except Exception as e:
            logger.warning(f"Could not initialize ContextAPI: {e}")
            self.context_api = None

        # ADR-0361: Register ForgedSkillAPI for loose coupling
        try:
            api_impl = ForgedSkillAPIImpl(
                subsystem=self,
                namespace_policy=self.namespace_policy,
                quota=self.forge_quota,
            )
            hub.register_api("forged_skill", api_impl)
            logger.info("SkillForgeSubsystem: ForgedSkillAPI registered")
        except Exception as e:
            logger.warning(f"SkillForgeSubsystem: Failed to register API: {e}")

        # Subscribe to strategy outcome events
        if hasattr(hub, 'subscribe'):
            hub.subscribe("strategy_applied", self.on_strategy_applied)
            hub.subscribe("strategy_succeeded", self.on_strategy_succeeded)
            hub.subscribe("strategy_failed", self.on_strategy_failed)
            hub.subscribe("skill_create_requested", self.on_skill_create_requested)

        logger.info("SkillForgeSubsystem started")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to published events (fire-and-forget).

        Args:
            event_name: Name of event
            event_data: Event payload
        """
        if event_name == "strategy_applied":
            await self.on_strategy_applied(event_name, event_data)
        elif event_name == "strategy_succeeded":
            await self.on_strategy_succeeded(event_name, event_data)
        elif event_name == "strategy_failed":
            await self.on_strategy_failed(event_name, event_data)
        elif event_name == "skill_create_requested":
            await self.on_skill_create_requested(event_name, event_data)

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle synchronous requests from other subsystems.

        Args:
            request_type: Type of request (skill_create, skill_grade, etc.)
            **kwargs: Request parameters

        Returns:
            Request result
        """
        match request_type:
            case "skill_create":
                return await self._skill_create(kwargs)
            case "skill_grade":
                return await self._skill_grade(kwargs)
            case "skill_auto_grade":
                return await self._skill_auto_grade(kwargs)
            case "skill_promote":
                return await self._skill_promote(kwargs)
            case "list_skills":
                return await self._list_skills(kwargs)
            case "get_health":
                return self.get_health()
            case "get_metrics":
                return self.metrics.get_stats()
            case _:
                raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup resources."""
        self.async_registry.shutdown()
        logger.info("SkillForgeSubsystem shutdown")

    # ---- Event handlers ----

    async def on_strategy_applied(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Bind skills to strategy when applied.

        Args:
            event_name: Event name
            event_data: Event data with 'strategy' and optional 'skills_active'
        """
        strategy = event_data.get("strategy", "unknown")
        skills_active = event_data.get("skills_active", [])

        # Remember binding for later grading
        self.strategy_skills[strategy] = list(skills_active)

        if self.context_api:
            try:
                self.context_api.record_decision(
                    "skill_binding",
                    value=f"{strategy} → {len(skills_active)} skills",
                    reasoning=f"Bound skills {skills_active} to strategy {strategy}",
                    confidence=1.0,
                )
            except Exception as e:
                logger.debug(f"context_api.record_decision failed: {e}")

    async def on_strategy_succeeded(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Grade bound skills +1 on strategy success.

        Args:
            event_name: Event name
            event_data: Event data with 'strategy'
        """
        strategy = event_data.get("strategy", "unknown")
        skills = self.strategy_skills.get(strategy, [])

        for skill_name in skills:
            await self._auto_grade_skill(
                skill_name,
                score=self.auto_grade_success,
                reason="strategy_succeeded",
            )
            await self._maybe_auto_promote(skill_name)

        # Publish event
        if self.hub and hasattr(self.hub, 'publish_event'):
            self.hub.publish_event("skills_graded_for_success", {
                "strategy": strategy,
                "skills": skills,
                "score": self.auto_grade_success,
            })

    async def on_strategy_failed(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Grade bound skills -0.5 on strategy failure.

        Args:
            event_name: Event name
            event_data: Event data with 'strategy' and optional 'error_type'
        """
        strategy = event_data.get("strategy", "unknown")
        error_type = event_data.get("error_type", "unknown")
        skills = self.strategy_skills.get(strategy, [])

        for skill_name in skills:
            await self._auto_grade_skill(
                skill_name,
                score=self.auto_grade_failure,
                reason=f"strategy_failed: {error_type}",
            )

        # Publish event
        if self.hub and hasattr(self.hub, 'publish_event'):
            self.hub.publish_event("skills_graded_for_failure", {
                "strategy": strategy,
                "skills": skills,
                "score": self.auto_grade_failure,
                "error_type": error_type,
            })

    async def on_skill_outcome(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Grade skill based on outcome (ADR-0372: closed-loop feedback).

        Args:
            event_name: Event name
            event_data: Event data with 'skill_name', 'outcome' (success/failure)
        """
        skill_name = event_data.get("skill_name", "unknown")
        outcome = event_data.get("outcome", "unknown")

        score = self.auto_grade_success if outcome == "success" else self.auto_grade_failure

        await self._auto_grade_skill(
            skill_name,
            score=score,
            reason=f"skill_outcome: {outcome}",
        )
        await self._maybe_auto_promote(skill_name)

    async def on_skill_create_requested(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Create skill on request from Brain.

        Args:
            event_name: Event name
            event_data: Event data with skill_name, skill_body, etc.
        """
        payload = {
            "name": event_data.get("skill_name"),
            "body_md": event_data.get("skill_body"),
            "description": event_data.get("description", ""),
            "skill_type": event_data.get("skill_type", "learned-experience"),
            "scope": event_data.get("scope", "session"),
        }
        try:
            await self._skill_create(payload)
        except Exception as e:
            logger.error(f"skill_create_requested failed: {e}")

    # ---- Request handlers ----

    async def _skill_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle skill_create request.

        Args:
            payload: Request payload with name, body_md, etc.

        Returns:
            Result dict with skill record or error

        Raises:
            LicenseLimitError: If daily skill_forge quota exceeded.
        """
        start_time = time.time()
        try:
            # ADR-0365: Enforce skill_forge_per_day quota
            from pathlib import Path
            from core.orchestration.quota_gate import increment_and_check
            # Use _default tenant if not available from context
            tenant_id = getattr(self, 'tenant_id', '_default')
            # corvin_home resolved by the gate (honours CORVIN_HOME); hard-coding
            # Path.home() counted quota in a root the install may never read.
            increment_and_check(None, "skill_forge_per_day", tenant_id)

            skill_record = await self.async_registry.skill_create(
                name=payload["name"],
                body_md=payload["body_md"],
                description=payload.get("description", ""),
                skill_type=payload.get("skill_type", "learned-experience"),
                claim=payload.get("claim"),
                scope=payload.get("scope", "session"),
            )

            if self.context_api:
                try:
                    self.context_api.record_decision(
                        "skill_created",
                        value=payload["name"],
                        reasoning="Skill created via request",
                        confidence=1.0,
                    )
                except Exception as e:
                    logger.debug(f"context_api.record_decision failed: {e}")

            if self.hub and hasattr(self.hub, 'publish_event'):
                self.hub.publish_event("skill_created", {
                    "name": payload["name"],
                    "type": payload.get("skill_type"),
                    "scope": payload.get("scope", "session"),
                })

            # Record latency metric
            latency_ms = (time.time() - start_time) * 1000
            self.metrics.record_create(latency_ms)
            logger.info(f"skill_create '{payload['name']}' completed in {latency_ms:.1f}ms")

            return {"skill_record": skill_record, "success": True}
        except Exception as e:
            logger.error(f"_skill_create failed: {e}")
            if self.context_api:
                try:
                    self.context_api.record_decision(
                        "skill_create_failed",
                        value=str(e),
                        reasoning=f"Error creating skill {payload.get('name')}",
                        confidence=0.0,
                    )
                except Exception as ce:
                    logger.debug(f"context_api.record_decision failed: {ce}")
            return {"error": str(e), "success": False}

    async def _skill_grade(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle skill_grade request (manual feedback).

        Args:
            payload: Request payload with name, score, etc.

        Returns:
            Result dict
        """
        skill_name = payload.get("name")
        score = payload.get("score", 0.5)

        if not skill_name:
            return {"error": "name required", "success": False}

        try:
            await self._auto_grade_skill(skill_name, score, reason="manual_feedback")
            return {"success": True, "name": skill_name}
        except Exception as e:
            logger.error(f"_skill_grade failed: {e}")
            return {"error": str(e), "success": False}

    async def _skill_auto_grade(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle skill_auto_grade request (Bayesian auto-grading from task result).

        Args:
            payload: Request payload with:
                - skill_name: str
                - task_result: dict (with success, latency_ms, output_quality, etc.)
                - prior_confidence: float (default 0.5)
                - feedback: Optional[str]

        Returns:
            Result dict with grade score and explanation
        """
        skill_name = payload.get("skill_name")
        task_result = payload.get("task_result", {})
        prior_confidence = payload.get("prior_confidence", 0.5)
        feedback = payload.get("feedback")

        if not skill_name:
            return {"error": "skill_name required", "success": False}

        try:
            # Use Bayesian auto_grade from learning module
            grade: ConfidenceGrade = auto_grade(
                task_result=task_result,
                prior_confidence=prior_confidence,
                feedback=feedback,
            )

            # Grade the skill with the computed score
            await self._auto_grade_skill(
                skill_name=skill_name,
                score=grade.score,
                reason=f"auto_grade: {grade.explanation}",
            )

            # Check for auto-promotion
            await self._maybe_auto_promote(skill_name)

            return {
                "success": True,
                "skill_name": skill_name,
                "score": grade.score,
                "explanation": grade.explanation,
                "features": grade.features,
            }
        except Exception as e:
            logger.error(f"_skill_auto_grade failed: {e}", exc_info=True)
            return {"error": str(e), "success": False}

    async def _skill_promote(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle skill_promote request.

        Args:
            payload: Request payload with name, from_scope, to_scope

        Returns:
            Result dict
        """
        skill_name = payload.get("name")
        from_scope = payload.get("from_scope", "session")
        to_scope = payload.get("to_scope", "project")

        if not skill_name:
            return {"error": "name required", "success": False}

        try:
            result = await self.async_registry.skill_promote(
                skill_name, from_scope, to_scope
            )

            if self.context_api:
                try:
                    self.context_api.record_decision(
                        "skill_promoted",
                        value=f"{skill_name}: {from_scope} → {to_scope}",
                        reasoning="Manual promotion by operator or system",
                        confidence=0.95,
                    )
                except Exception as e:
                    logger.debug(f"context_api.record_decision failed: {e}")

            return {"success": True, "name": skill_name, "from_scope": from_scope, "to_scope": to_scope}
        except Exception as e:
            logger.error(f"_skill_promote failed: {e}")
            return {"error": str(e), "success": False}

    async def _list_skills(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle list_skills request.

        Args:
            payload: Request payload with optional namespace, scope

        Returns:
            Dict with skills and scores
        """
        try:
            skills = await self.async_registry.list_skills(
                namespace=payload.get("namespace"),
                scope=payload.get("scope"),
            )

            # Annotate with scores
            skills_with_scores = []
            for skill in skills:
                skill_name = skill.get("name", "unknown")
                scores = self.skill_scores.get(skill_name, [])
                skills_with_scores.append({
                    "skill": skill,
                    "uses": self.skill_uses.get(skill_name, 0),
                    "mean_score": sum(scores) / len(scores) if scores else 0.0,
                    "scores": scores,
                    "n_grades": len(scores),
                })

            return {
                "skills": skills_with_scores,
                "success": True,
                "total": len(skills_with_scores),
            }
        except Exception as e:
            logger.error(f"_list_skills failed: {e}")
            return {"error": str(e), "success": False, "skills": []}

    # ---- Auto-grading logic ----

    async def _auto_grade_skill(
        self,
        skill_name: str,
        score: float,
        reason: str,
    ) -> None:
        """Grade skill and track score in memory with timestamp (ADR-0372).

        Args:
            skill_name: Name of skill to grade
            score: Score value
            reason: Reason for grade
        """
        if skill_name not in self.skill_scores:
            self.skill_scores[skill_name] = []
            self.skill_score_timestamps[skill_name] = []  # ADR-0372
            self.skill_uses[skill_name] = 0

        self.skill_scores[skill_name].append(score)
        self.skill_score_timestamps[skill_name].append(time.time())  # ADR-0372: timestamp for decay
        self.skill_uses[skill_name] += 1

        # Record grade in registry
        run_id = f"auto_grade_{self.skill_uses[skill_name]}"
        try:
            await self.async_registry.skill_grade(
                skill_name,
                run_id=run_id,
                score=max(0.0, min(1.0, score + 0.5)),  # Normalize to [0, 1]
                notes=reason,
            )
        except Exception as e:
            logger.error(f"Failed to grade skill {skill_name}: {e}")

        if self.context_api:
            try:
                self.context_api.record_decision(
                    "skill_graded",
                    value=f"{skill_name}: {score:+.1f}",
                    reasoning=reason,
                    confidence=0.9,
                )
            except Exception as e:
                logger.debug(f"context_api.record_decision failed: {e}")

        if self.hub and hasattr(self.hub, 'publish_event'):
            self.hub.publish_event("skill_graded", {
                "skill_name": skill_name,
                "score": score,
                "reason": reason,
                "uses": self.skill_uses[skill_name],
                "mean_score": sum(self.skill_scores[skill_name]) / len(self.skill_scores[skill_name]),
            })

    def _decay_factor(self, age_days: float) -> float:
        """Exponential decay: ~10% per week (ADR-0372).

        Args:
            age_days: Age of grade in days

        Returns:
            Decay factor in range [0, 1]
        """
        if age_days <= SKILL_MIN_GRADE_AGE_DAYS:
            return 1.0  # No decay for recent grades
        return math.exp(-SKILL_CONFIDENCE_DECAY_PER_WEEK * (age_days - SKILL_MIN_GRADE_AGE_DAYS) / 7.0)

    async def _maybe_auto_promote(self, skill_name: str) -> None:
        """Check if skill meets promotion threshold (with decay weighting) and auto-promote (ADR-0372).

        Args:
            skill_name: Name of skill to check
        """
        scores = self.skill_scores.get(skill_name, [])
        timestamps = self.skill_score_timestamps.get(skill_name, [])
        uses = self.skill_uses.get(skill_name, 0)

        if not scores or len(scores) < self.min_uses_for_promotion:
            return  # Need minimum uses

        # Handle migration case: timestamps may be empty for pre-ADR-0372 grades
        if not timestamps or len(timestamps) != len(scores):
            # Fallback: use current time for all missing timestamps (conservative)
            now = time.time()
            timestamps = [now - (len(scores) - i) * 86400.0 for i in range(len(scores))]
            logger.debug(f"Reconstructed timestamps for skill '{skill_name}' (migration from pre-ADR-0372)")

        # Apply decay weighting to older grades (ADR-0372)
        now = time.time()
        effective_scores = []
        for score, ts in zip(scores, timestamps):
            age_days = (now - ts) / 86400.0
            decay = self._decay_factor(age_days)
            effective_scores.append(score * decay)

        if not effective_scores:  # Safety check
            return

        mean_score = sum(effective_scores) / len(effective_scores)
        confidence_lower = self._confidence_interval_lower(effective_scores)

        if (mean_score > self.min_mean_score_for_promotion and
            confidence_lower > self.min_confidence_for_promotion):
            # Auto-promote!
            try:
                await self.async_registry.skill_promote(
                    skill_name,
                    from_scope="session",
                    to_scope="project",
                )
                self.auto_promotion_count += 1

                if self.context_api:
                    try:
                        self.context_api.record_decision(
                            "skill_auto_promoted",
                            value=skill_name,
                            reasoning=f"mean_score={mean_score:.2f}, uses={uses}, confidence={confidence_lower:.2f}",
                            confidence=0.95,
                        )
                    except Exception as e:
                        logger.debug(f"context_api.record_decision failed: {e}")

                if self.hub and hasattr(self.hub, 'publish_event'):
                    self.hub.publish_event("skill_auto_promoted", {
                        "skill_name": skill_name,
                        "mean_score": mean_score,
                        "uses": uses,
                        "confidence_lower": confidence_lower,
                        "promoted_to": "project",
                    })

                logger.info(f"Auto-promoted skill {skill_name} with confidence {confidence_lower:.2f}")
            except Exception as e:
                logger.error(f"Failed to auto-promote skill {skill_name}: {e}")

    @staticmethod
    def _confidence_interval_lower(scores: List[float], confidence: float = 0.8) -> float:
        """Calculate lower bound of confidence interval.

        Uses t-distribution approximation for confidence interval.

        Args:
            scores: List of scores
            confidence: Confidence level (0.8 = 80%)

        Returns:
            Lower bound of confidence interval
        """
        if not scores or len(scores) < 2:
            return max(0.0, sum(scores) / len(scores)) if scores else 0.0

        mean = sum(scores) / len(scores)
        try:
            stddev = statistics.stdev(scores)
        except (ValueError, statistics.StatisticsError):
            return mean

        n = len(scores)
        # t-critical approximation: use 1.96 for large n (95% CI approx)
        # For smaller n, use a slightly larger value
        t_critical = 1.96 if n > 30 else 2.045
        se = stddev / (n ** 0.5)
        lower = mean - t_critical * se
        return max(0.0, lower)

    def get_health(self) -> Dict[str, Any]:
        """Return subsystem health status.

        Returns:
            Health dict with status, metrics
        """
        if not self.skill_scores:
            avg_score = 0.0
        else:
            all_scores = []
            for scores in self.skill_scores.values():
                all_scores.extend(scores)
            avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

        return {
            "status": "healthy",
            "skills_created_session": len(self.skill_scores),
            "avg_skill_score": avg_score,
            "auto_promotions": self.auto_promotion_count,
            "version": self.version,
        }
