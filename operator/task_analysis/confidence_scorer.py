"""Phase 1: Confidence Scoring — Aggregate Router Scores to Global Confidence.

This module takes GraphMatch outputs from the five routers and applies
heuristic scoring to convert raw match counts into normalized 0.0–1.0
confidence scores that answer: "How confident are we that we've identified
all affected areas of the codebase?"

Each score method applies router-specific heuristics and thresholding:
- Raw counts (files, tests, ADRs) are normalized against expectations
- Bounds are clamped to [0.0, 1.0]
- Fallback logic handles missing data (empty components, no tests, etc.)

Global confidence is the mean of all five scores, with a fallback strategy:
- If score >= 0.7: use the classifier's normal routing
- If score < 0.7: recommend ALL graphs (belt-and-suspenders analysis)

ADR:
    ADR-0267 — Task Engine: Router Layer Architecture
"""

import logging
from typing import Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScoredRouters:
    """Aggregated scores from all five routers.

    Attributes:
        call_graph: CallGraphRouter score
        test_graph: TestGraphRouter score
        adr_graph: ADRGraphRouter score
        layer_graph: LayerGraphRouter score
        code_diff: CodeDiffGraphRouter score
        global_confidence: Mean of all five
    """

    call_graph: float = 0.0
    test_graph: float = 0.0
    adr_graph: float = 0.0
    layer_graph: float = 0.0
    code_diff: float = 0.0
    global_confidence: float = 0.0


class ConfidenceScorer:
    """Score router matches to normalized confidence 0.0–1.0.

    Each score method takes a GraphMatch and the original NormalizedTask,
    applies router-specific heuristics, and returns a confidence value.

    Heuristics:
        - CallGraphRouter: score = (import_count / expected) clamped to [0, 1]
        - TestGraphRouter: score = (test_files found / expected) clamped to [0, 1]
        - ADRGraphRouter: score = (ADRs found / expected) clamped to [0, 1]
        - LayerGraphRouter: score = (overlap ratio) clamped to [0, 1]
        - CodeDiffGraphRouter: score = router.score as-is (already normalized)

    Fallback:
        - Empty components → 0.0 across all routers
        - No matches in a graph → 0.0 for that router
        - Global confidence < 0.7 → recommend all graphs
    """

    def __init__(self, task=None):
        """Initialize scorer.

        Args:
            task: Optional NormalizedTask for context
        """
        self.task = task

    def score_call_graph(self, graph_match, task) -> float:
        """Score CallGraphRouter result.

        Heuristic:
            score = min(1.0, import_count / expected_count)
            expected = (# components) * 2 (expect ~2 transitive imports per component)

        Args:
            graph_match: GraphMatch from CallGraphRouter
            task: NormalizedTask

        Returns:
            Confidence 0.0–1.0
        """
        if not task.components:
            return 0.0

        metadata = graph_match.metadata
        import_count = metadata.get("import_count", 0)
        expected = len(task.components) * 2

        if expected == 0:
            return 0.0

        score = min(1.0, import_count / expected)
        return float(score)

    def score_test_graph(self, graph_match, task) -> float:
        """Score TestGraphRouter result.

        Heuristic:
            score = min(1.0, test_files_found / expected_test_files)
            expected = # components (expect 1 test per component, but often missing)

        Args:
            graph_match: GraphMatch from TestGraphRouter
            task: NormalizedTask

        Returns:
            Confidence 0.0–1.0
        """
        if not task.components:
            return 0.0

        metadata = graph_match.metadata
        found = metadata.get("found", 0)
        expected = metadata.get("expected", len(task.components))

        if expected == 0:
            return 0.0

        # Reduce expected (not all components have dedicated tests)
        expected_adjusted = max(1, len(task.components) // 2)
        score = min(1.0, found / expected_adjusted)
        return float(score)

    def score_adr_graph(self, graph_match, task) -> float:
        """Score ADRGraphRouter result.

        Heuristic:
            score = min(1.0, adrs_matched / expected_adrs)
            expected = (# affected_layers) (expect ~1 ADR per layer)

        Args:
            graph_match: GraphMatch from ADRGraphRouter
            task: NormalizedTask

        Returns:
            Confidence 0.0–1.0
        """
        if not task.affected_layers:
            return 0.0

        metadata = graph_match.metadata
        matched = metadata.get("matched", 0)
        expected = len(task.affected_layers)

        if expected == 0:
            return 0.0

        score = min(1.0, matched / expected)
        return float(score)

    def score_layer_graph(self, graph_match, task) -> float:
        """Score LayerGraphRouter result.

        Heuristic:
            score = overlap_ratio = (matched_layers / total_components)
            clamped to [0, 1]

        Args:
            graph_match: GraphMatch from LayerGraphRouter
            task: NormalizedTask

        Returns:
            Confidence 0.0–1.0
        """
        if not task.components:
            return 0.0

        metadata = graph_match.metadata
        matched = metadata.get("matched", 0)
        total_components = len(task.components)

        if total_components == 0:
            return 0.0

        # Not all components map to layers (some are generic utility)
        # Expect ~50% match rate
        expected_matches = total_components // 2
        score = min(1.0, matched / max(1, expected_matches))
        return float(score)

    def score_code_diff(self, graph_match, task) -> float:
        """Score CodeDiffGraphRouter result.

        Heuristic:
            CodeDiffGraphRouter already provides a normalized confidence.
            Return it as-is (already 0.0–1.0).

        Args:
            graph_match: GraphMatch from CodeDiffGraphRouter
            task: NormalizedTask

        Returns:
            Confidence 0.0–1.0 (pass-through from router)
        """
        return min(1.0, max(0.0, graph_match.score))

    def global_confidence(self, scores: Dict[str, float]) -> float:
        """Aggregate five router scores to global confidence.

        Algorithm:
            1. Collect scores from all five routers
            2. Mean the scores
            3. Clamp to [0.0, 1.0]

        Args:
            scores: Dict mapping router name → score
                    Expected keys: 'call_graph', 'test_graph', 'adr_graph',
                                   'layer_graph', 'code_diff'

        Returns:
            Global confidence 0.0–1.0
        """
        if not scores:
            return 0.0

        valid_scores = [s for s in scores.values() if isinstance(s, (int, float))]
        if not valid_scores:
            return 0.0

        mean_score = sum(valid_scores) / len(valid_scores)
        return float(min(1.0, max(0.0, mean_score)))

    def compute_all(
        self, graph_matches: Dict[str, "GraphMatch"], task
    ) -> ScoredRouters:
        """Compute all five scores and global confidence.

        Args:
            graph_matches: Dict mapping router name → GraphMatch
                          Expected keys: 'call_graph', 'test_graph', 'adr_graph',
                                       'layer_graph', 'code_diff'
            task: NormalizedTask

        Returns:
            ScoredRouters dataclass with all scores
        """
        scores = {}

        # Score each router
        call_graph_match = graph_matches.get("call_graph")
        if call_graph_match:
            scores["call_graph"] = self.score_call_graph(call_graph_match, task)
        else:
            scores["call_graph"] = 0.0

        test_graph_match = graph_matches.get("test_graph")
        if test_graph_match:
            scores["test_graph"] = self.score_test_graph(test_graph_match, task)
        else:
            scores["test_graph"] = 0.0

        adr_graph_match = graph_matches.get("adr_graph")
        if adr_graph_match:
            scores["adr_graph"] = self.score_adr_graph(adr_graph_match, task)
        else:
            scores["adr_graph"] = 0.0

        layer_graph_match = graph_matches.get("layer_graph")
        if layer_graph_match:
            scores["layer_graph"] = self.score_layer_graph(layer_graph_match, task)
        else:
            scores["layer_graph"] = 0.0

        code_diff_match = graph_matches.get("code_diff")
        if code_diff_match:
            scores["code_diff"] = self.score_code_diff(code_diff_match, task)
        else:
            scores["code_diff"] = 0.0

        # Global confidence
        global_score = self.global_confidence(scores)

        # Return structured result
        return ScoredRouters(
            call_graph=scores["call_graph"],
            test_graph=scores["test_graph"],
            adr_graph=scores["adr_graph"],
            layer_graph=scores["layer_graph"],
            code_diff=scores["code_diff"],
            global_confidence=global_score,
        )
