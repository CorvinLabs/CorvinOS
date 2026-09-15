"""Phase 1: Task Classifier — Orchestrates Routers and Scorer.

This module brings together the five graph routers and confidence scorer
to produce a ClassifiedTask. The classifier:

    1. Runs all five routers in parallel (conceptually)
    2. Scores each router's results
    3. Computes global confidence
    4. Determines recommended graphs
    5. Injects skills

The core decision: if confidence >= 0.7, recommend only high-confidence
graphs. If confidence < 0.7, recommend all graphs (belt-and-suspenders).

This is the public API for Phase 1: feed a NormalizedTask, get back a
ClassifiedTask with scores, recommendations, and skills to inject.

ADR:
    ADR-0267 — Task Engine: Router Layer Architecture
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple
import logging

from .graph_routing import (
    CallGraphRouter,
    TestGraphRouter,
    ADRGraphRouter,
    LayerGraphRouter,
    CodeDiffGraphRouter,
    GraphMatch,
)
from .confidence_scorer import ConfidenceScorer, ScoredRouters
from .skill_injector import SkillInjector

logger = logging.getLogger(__name__)


@dataclass
class ClassifiedTask:
    """A task after Phase 1 classification.

    Attributes:
        normalized: The NormalizedTask input
        classification: Dict mapping graph_name → (score, metadata)
        scored_routers: Aggregated scores from ConfidenceScorer
        confidence: Global 0.0–1.0 confidence
        recommended_graphs: List of router names to recommend downstream
        recommended_graph_scores: Dict of {name: score} for recommended graphs
        skills_to_inject: List of skill names for LDD orchestration
    """

    normalized: "NormalizedTask"
    classification: Dict[str, Tuple[float, Dict]] = field(default_factory=dict)
    scored_routers: ScoredRouters = field(default_factory=ScoredRouters)
    confidence: float = 0.0
    recommended_graphs: List[str] = field(default_factory=list)
    recommended_graph_scores: Dict[str, float] = field(default_factory=dict)
    skills_to_inject: List[str] = field(default_factory=list)


class TaskClassifier:
    """Orchestrates task classification via routing and scoring.

    Entry point: TaskClassifier().classify(normalized_task)

    Pipeline:
        1. Initialize five routers
        2. Run each router (all results are deterministic)
        3. Score each result
        4. Compute global confidence
        5. Determine recommended graphs (confidence >= 0.7 → selective,
           confidence < 0.7 → all graphs)
        6. Inject skills via SkillInjector
        7. Return ClassifiedTask

    All operations are pure (no state mutation); can be called repeatedly
    on the same task without side effects.
    """

    def __init__(self):
        """Initialize routers and scorer."""
        self.call_graph_router = CallGraphRouter()
        self.test_graph_router = TestGraphRouter()
        self.adr_graph_router = ADRGraphRouter()
        self.layer_graph_router = LayerGraphRouter()
        self.code_diff_router = CodeDiffGraphRouter()
        self.scorer = ConfidenceScorer()
        self.skill_injector = SkillInjector()

    def classify(self, normalized_task) -> ClassifiedTask:
        """Classify a normalized task.

        Args:
            normalized_task: NormalizedTask from Phase 0

        Returns:
            ClassifiedTask with scores, recommendations, and skills

        Raises:
            Exception: Only if a router raises and we can't continue
                      (mitigated by try/except in each router)
        """
        try:
            # Run all five routers
            graph_matches = self._run_routers(normalized_task)

            # Score each router
            scored_routers = self.scorer.compute_all(graph_matches, normalized_task)

            # Build classification dict
            classification = {}
            for router_name, match in graph_matches.items():
                score = getattr(scored_routers, router_name, 0.0)
                classification[router_name] = (score, match.metadata)

            # Determine recommended graphs
            recommended_graphs = self._recommend_graphs(scored_routers)
            recommended_graph_scores = {
                name: getattr(scored_routers, name, 0.0)
                for name in recommended_graphs
            }

            # Inject skills
            skills = self.skill_injector.inject_skills(
                normalized_task.type,
                normalized_task.severity,
                len(recommended_graphs),
            )

            # Build result
            return ClassifiedTask(
                normalized=normalized_task,
                classification=classification,
                scored_routers=scored_routers,
                confidence=scored_routers.global_confidence,
                recommended_graphs=recommended_graphs,
                recommended_graph_scores=recommended_graph_scores,
                skills_to_inject=skills,
            )

        except Exception as e:
            logger.error(f"Task classification failed: {e}", exc_info=True)
            raise

    def _run_routers(self, task) -> Dict[str, GraphMatch]:
        """Run all five routers.

        Args:
            task: NormalizedTask

        Returns:
            Dict mapping router name → GraphMatch
        """
        results = {}

        # Run each router with error handling
        try:
            results["call_graph"] = self.call_graph_router.route(task)
        except Exception as e:
            logger.warning(f"CallGraphRouter error: {e}")
            results["call_graph"] = GraphMatch("call_graph", 0.0)

        try:
            results["test_graph"] = self.test_graph_router.route(task)
        except Exception as e:
            logger.warning(f"TestGraphRouter error: {e}")
            results["test_graph"] = GraphMatch("test_graph", 0.0)

        try:
            results["adr_graph"] = self.adr_graph_router.route(task)
        except Exception as e:
            logger.warning(f"ADRGraphRouter error: {e}")
            results["adr_graph"] = GraphMatch("adr_graph", 0.0)

        try:
            results["layer_graph"] = self.layer_graph_router.route(task)
        except Exception as e:
            logger.warning(f"LayerGraphRouter error: {e}")
            results["layer_graph"] = GraphMatch("layer_graph", 0.0)

        try:
            results["code_diff"] = self.code_diff_router.route(task)
        except Exception as e:
            logger.warning(f"CodeDiffGraphRouter error: {e}")
            results["code_diff"] = GraphMatch("code_diff", 0.0)

        return results

    def _recommend_graphs(self, scored_routers: ScoredRouters) -> List[str]:
        """Determine which graphs to recommend.

        Algorithm:
            if confidence >= 0.7:
                recommend graphs with score >= 0.5
            else:
                recommend all graphs (fallback: belt-and-suspenders)

        Args:
            scored_routers: ScoredRouters from scorer

        Returns:
            List of router names to recommend
        """
        recommended = []

        if scored_routers.global_confidence >= 0.7:
            # Selective mode: recommend high-confidence graphs
            threshold = 0.5
            if scored_routers.call_graph >= threshold:
                recommended.append("call_graph")
            if scored_routers.test_graph >= threshold:
                recommended.append("test_graph")
            if scored_routers.adr_graph >= threshold:
                recommended.append("adr_graph")
            if scored_routers.layer_graph >= threshold:
                recommended.append("layer_graph")
            if scored_routers.code_diff >= threshold:
                recommended.append("code_diff")

            # Fallback: if no graphs selected, pick the highest-scoring ones
            # BUT ONLY if they have non-zero scores
            if not recommended:
                scores = {
                    "call_graph": scored_routers.call_graph,
                    "test_graph": scored_routers.test_graph,
                    "adr_graph": scored_routers.adr_graph,
                    "layer_graph": scored_routers.layer_graph,
                    "code_diff": scored_routers.code_diff,
                }
                # Only recommend if top score is > 0.0 (avoid recommending 0.0-scored graphs)
                top_two = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:2]
                if top_two and top_two[0][1] > 0.0:
                    recommended = [name for name, _ in top_two]
                # else: no recommendation (better than recommending 0.0 graphs)
        else:
            # Fallback mode: recommend all graphs (low confidence)
            recommended = [
                "call_graph",
                "test_graph",
                "adr_graph",
                "layer_graph",
                "code_diff",
            ]

        return recommended
