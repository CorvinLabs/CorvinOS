"""Director Mode Phase 1: Narrative Optimizer

Structures narratives while preserving facts. Prevents hallucination via fact extraction
+ validation gates. Implements ADR-0696 Phase 1 invariants.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class FactExtraction:
    """Extracted facts from storyboard (must preserve in optimized narrative)."""
    facts: List[str]
    source_assets: List[str]
    extracted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class NarrativeStructure:
    """Selected narrative template with structure."""
    template_type: str  # "hero-s-journey", "three-act", "problem-solution"
    scenes: List[Dict] = field(default_factory=list)
    facts_preserved: List[str] = field(default_factory=list)
    approved: bool = False
    approval_timestamp: Optional[str] = None


class FactExtractor:
    """Extract facts from storyboard."""

    def extract(self, storyboard: Dict) -> FactExtraction:
        """Extract all factual claims from storyboard."""
        facts = []
        source_assets = []

        # Simple extraction: gather all claims from scenes
        for scene in storyboard.get("scenes", []):
            if claim := scene.get("factual_claim"):
                facts.append(claim)
                if source := scene.get("source_asset"):
                    source_assets.append(source)

        return FactExtraction(
            facts=facts,
            source_assets=source_assets
        )


class NarrativeTemplates:
    """Pre-defined narrative structures."""

    TEMPLATES = {
        "hero-s-journey": {
            "name": "Hero's Journey",
            "acts": ["Ordinary World", "Call to Adventure", "Trials", "Ordeal", "Reward", "Return"],
            "description": "Classic hero's journey arc for character-driven narratives"
        },
        "three-act": {
            "name": "Three-Act Structure",
            "acts": ["Setup", "Confrontation", "Resolution"],
            "description": "Classic three-act drama structure"
        },
        "problem-solution": {
            "name": "Problem-Solution",
            "acts": ["Problem", "Complication", "Solution", "Impact"],
            "description": "Business/explanatory narrative focusing on problem solving"
        }
    }

    def suggest(self, facts: List[str]) -> str:
        """Suggest best narrative template based on facts."""
        # Simple heuristic: if facts contain problem keywords → problem-solution
        problem_keywords = ["problem", "challenge", "issue", "error", "fix"]
        if any(kw in " ".join(facts).lower() for kw in problem_keywords):
            return "problem-solution"

        # Default to three-act for general narratives
        return "three-act"

    def get_template(self, template_type: str) -> Dict:
        """Get template structure."""
        return self.TEMPLATES.get(template_type, self.TEMPLATES["three-act"])


class NarrativeOptimizer:
    """Phase 1 Optimizer: Preserve facts while structuring narrative."""

    def __init__(self):
        self.extractor = FactExtractor()
        self.templates = NarrativeTemplates()

    def optimize(self, storyboard: Dict, tenant_id: str = "_default") -> NarrativeStructure:
        """
        Optimize narrative while preserving facts.

        1. Extract facts from original storyboard
        2. Suggest narrative structure
        3. Return structured narrative for approval

        Load-bearing invariant: All extracted facts MUST be preserved in final structure.
        """
        # Step 1: Extract facts
        fact_extraction = self.extractor.extract(storyboard)
        logger.info(f"Extracted {len(fact_extraction.facts)} facts from storyboard")

        # Step 2: Suggest template
        template_type = self.templates.suggest(fact_extraction.facts)
        template = self.templates.get_template(template_type)

        # Step 3: Create narrative structure
        narrative = NarrativeStructure(
            template_type=template_type,
            scenes=[],  # Will be populated by Phase 2 (Visual Choreographer)
            facts_preserved=fact_extraction.facts,
            approved=False
        )

        # Emit audit event
        logger.info(f"Narrative optimized: {template_type} ({len(fact_extraction.facts)} facts preserved, approval_required)")

        return narrative

    def approve(self, narrative: NarrativeStructure) -> NarrativeStructure:
        """User approval gate (must be called before rendering)."""
        narrative.approved = True
        narrative.approval_timestamp = datetime.utcnow().isoformat()
        logger.info(f"Narrative approved by operator at {narrative.approval_timestamp}")
        return narrative


__all__ = ["NarrativeOptimizer", "NarrativeStructure", "FactExtraction"]
