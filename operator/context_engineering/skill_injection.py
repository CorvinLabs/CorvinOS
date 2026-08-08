"""Skill Injection module (Phase 2).

Recommends and injects relevant skills into task context.
Extends Phase 2 Graph Traversal with actionable guidance.

Features:
- Score skills by relevance + success rate
- Embed top 3 skills into RichTaskBrief
- Track adoption (did agent use skill?)
- Cache recommendations
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
import time

logger = logging.getLogger(__name__)

# Optional package skill loader (for ADR-0268 Phase 5 integration)
try:
    from operator.context_engineering.package_skill_loader import PackageSkillLoader
    HAS_PACKAGE_SKILLS = True
except (ImportError, ModuleNotFoundError):
    HAS_PACKAGE_SKILLS = False


@dataclass
class RecommendedSkill:
    """Single recommended skill for a task."""

    skill_id: str
    """Unique skill identifier."""

    title: str
    """Skill name/title."""

    relevance_score: float
    """Relevance [0.0, 1.0]. Higher = more applicable."""

    success_rate: float
    """Historical success rate [0.0, 1.0] when skill was used."""

    category: str
    """Skill category (e.g., 'debugging', 'refactoring', 'testing')."""

    description: str
    """Brief skill description."""

    def __post_init__(self):
        """Validate scores are in [0.0, 1.0]."""
        if not 0.0 <= self.relevance_score <= 1.0:
            raise ValueError(f"Relevance must be [0.0, 1.0], got {self.relevance_score}")
        if not 0.0 <= self.success_rate <= 1.0:
            raise ValueError(f"Success rate must be [0.0, 1.0], got {self.success_rate}")


@dataclass
class SkillInjectionResult:
    """Result of skill injection."""

    task_id: str
    """Original task identifier."""

    recommended_skills: List[RecommendedSkill]
    """Top 3 recommended skills, ranked by combined score."""

    search_duration_ms: float
    """Time to find and rank skills."""

    adoption_tracked: bool
    """Whether adoption is being tracked for this injection."""


class SkillInjection:
    """Recommend and inject skills into task context.

    Implements Phase 2 extension to CEL.
    Maps decisions → skills → task context.
    """

    def __init__(self, cache_ttl_minutes: int = 30, tenant_id: str = "_default"):
        """Initialize skill injection.

        Args:
            cache_ttl_minutes: Cache TTL in minutes (default: 30).
            tenant_id: Tenant ID for package skill discovery (ADR-0268 Phase 5).
        """
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._injection_cache: Dict[int, Tuple[List[RecommendedSkill], datetime]] = {}
        self.tenant_id = tenant_id

        # Optional: Initialize package skill loader for ADR-0268 Phase 5
        self.package_skill_loader: Optional[PackageSkillLoader] = None
        if HAS_PACKAGE_SKILLS:
            try:
                self.package_skill_loader = PackageSkillLoader(tenant_id=tenant_id)
                logger.info(f"Package skill loader initialized (tenant={tenant_id})")
            except Exception as e:
                logger.warning(f"Failed to initialize package skill loader: {e}")

        logger.info(f"SkillInjection initialized (TTL: {cache_ttl_minutes}min, tenant={tenant_id})")

    def recommend_skills(
        self,
        task: object,
        related_decisions: Optional[List] = None,
        top_n: int = 3,
    ) -> SkillInjectionResult:
        """Recommend skills based on task and related decisions.

        Pipeline:
        1. Map decisions to skills
        2. Score by relevance + success_rate
        3. Rank and select top N
        4. Cache results
        5. Track adoption

        Args:
            task: Task object (EnrichedTask or similar).
            related_decisions: Related decisions from GraphTraversal (optional).
            top_n: Number of skills to recommend (default: 3).

        Returns:
            SkillInjectionResult with recommended skills.
        """
        start = time.perf_counter()

        # Check cache (use task_id not object identity to avoid GC reuse collisions)
        task_id = self._get_task_id(task)
        cache_key = hash((task_id, top_n))
        if cache_key in self._injection_cache:
            cached_skills, timestamp = self._injection_cache[cache_key]
            age = datetime.now() - timestamp
            if age < self.cache_ttl:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.debug(
                    f"Skill injection cache hit: {len(cached_skills)} skills, "
                    f"age={age.total_seconds():.0f}s"
                )
                return SkillInjectionResult(
                    task_id=self._get_task_id(task),
                    recommended_skills=cached_skills[:top_n],
                    search_duration_ms=elapsed_ms,
                    adoption_tracked=True,
                )
            else:
                del self._injection_cache[cache_key]

        logger.debug(f"Skill injection cache miss: recommending (top_n={top_n})")

        # Map decisions to skills (placeholder)
        all_skills = self._map_decisions_to_skills(related_decisions)

        # Score by relevance + success_rate
        scored = self._score_skills(all_skills, task)

        # Rank (highest combined score first)
        ranked = sorted(
            scored,
            key=lambda s: (s.relevance_score * 0.6 + s.success_rate * 0.4),
            reverse=True,
        )

        # Cache results
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._injection_cache[cache_key] = (ranked, datetime.now())

        logger.info(
            f"Skill injection complete: {len(ranked)} skills recommended, "
            f"returning top {min(len(ranked), top_n)}, "
            f"latency={elapsed_ms:.0f}ms"
        )

        return SkillInjectionResult(
            task_id=self._get_task_id(task),
            recommended_skills=ranked[:top_n],
            search_duration_ms=elapsed_ms,
            adoption_tracked=True,
        )

    def _get_task_id(self, task: object) -> str:
        """Extract task identifier."""
        if hasattr(task, "id"):
            return str(task.id)
        if hasattr(task, "task_id"):
            return str(task.task_id)
        return f"task_{id(task)}"

    def _map_decisions_to_skills(self, decisions: Optional[List]) -> List[Dict]:
        """Map decisions to associated skills.

        Combines:
        1. ADR-driven skills (from graph traversal decisions)
        2. Package skills (from ADR-0268 installed packages)

        Args:
            decisions: Related decisions from GraphTraversal.

        Returns:
            List of skill dicts (combined from all sources).
        """
        all_skills: List[Dict] = []

        # Get package skills (ADR-0268 Phase 5)
        if self.package_skill_loader:
            try:
                package_skills = self.package_skill_loader.get_skills_for_task(None)
                all_skills.extend(package_skills)
                logger.debug(f"Added {len(package_skills)} package skills")
            except Exception as e:
                logger.warning(f"Error getting package skills: {e}")

        # TODO: Add ADR-driven skills from decisions (Phase 2 extension)
        # For now, placeholder for future integration with ADRLoader

        logger.debug(f"_map_decisions_to_skills: {len(all_skills)} total skills")
        return all_skills

    def _score_skills(self, skills: List[Dict], task: object) -> List[RecommendedSkill]:
        """Score skills by relevance and success rate.

        Scoring: combined = (relevance * 0.6) + (success_rate * 0.4)
        - relevance: how applicable skill is to task
        - success_rate: historical success when skill was used

        Args:
            skills: List of raw skill dicts.
            task: Current task for relevance scoring.

        Returns:
            List of RecommendedSkill objects with scores.
        """
        if not skills:
            return []

        recommended = []
        for skill in skills:
            # Placeholder scoring
            relevance = 0.7
            success = 0.8

            try:
                recommended.append(
                    RecommendedSkill(
                        skill_id=skill.get("id", "unknown"),
                        title=skill.get("title", "Unknown"),
                        relevance_score=relevance,
                        success_rate=success,
                        category=skill.get("category", "unknown"),
                        description=skill.get("description", ""),
                    )
                )
            except ValueError:
                logger.warning(f"Invalid scores for skill {skill.get('id')}")
                continue

        return recommended

    def rank(self, skills: List[RecommendedSkill]) -> List[RecommendedSkill]:
        """Re-rank skills by combined score (relevance + success_rate).

        Args:
            skills: List of RecommendedSkill objects.

        Returns:
            Re-ranked skills (highest combined score first).
        """
        return sorted(
            skills,
            key=lambda s: (s.relevance_score * 0.6 + s.success_rate * 0.4),
            reverse=True,
        )
