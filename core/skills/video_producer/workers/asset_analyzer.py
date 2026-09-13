"""Asset Analyzer Worker: Phase 1 Analysis and Validation

Validates narration and assets for production readiness:
1. Parse narration into facts
2. Verify sources (no unsourced claims)
3. Detect contradictions
4. Return pass/warn/fail status with recommendations
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
from enum import Enum


class AnalysisStatus(Enum):
    """Analysis result status"""
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class AnalysisResult:
    """Result of asset analysis phase"""
    status: AnalysisStatus
    facts_extracted: List[str]
    sources_verified: bool
    contradictions: List[str]
    recommendations: List[str]
    confidence: float  # 0.0 to 1.0
    success: bool = True


class AssetAnalyzerWorker:
    """Worker Skill: Analyze narration + assets for production readiness

    Enforces:
    - All claims must be sourced (no hallucination)
    - No logical contradictions
    - Complete and accurate narration

    Load-Bearing: This worker gates Phase 2 (Voice Synthesis).
    If analysis fails, production cannot proceed.
    """

    def __init__(self):
        self.name = "asset_analyzer"
        self.version = "2.0.0"

    def execute(self, job) -> AnalysisResult:
        """Execute asset analysis phase

        Returns:
            AnalysisResult with status (PASS/WARN/FAIL) and recommendations

        Raises:
            ValueError: If narration is invalid
        """

        # Phase 1: Extract facts from narration
        facts = self._extract_facts(job.narration)

        # Phase 2: Verify sources
        sources_verified = self._verify_sources(facts)

        # Phase 3: Detect contradictions
        contradictions = self._detect_contradictions(facts)

        # Phase 4: Gate enforcement
        if not sources_verified and len(job.narration) > 0:
            return AnalysisResult(
                status=AnalysisStatus.FAIL,
                facts_extracted=facts,
                sources_verified=False,
                contradictions=contradictions,
                recommendations=["Verify all claims are sourced before proceeding"],
                confidence=0.3,
            )

        if contradictions:
            return AnalysisResult(
                status=AnalysisStatus.WARN,
                facts_extracted=facts,
                sources_verified=sources_verified,
                contradictions=contradictions,
                recommendations=[f"Resolve contradiction: {c}" for c in contradictions],
                confidence=0.6,
            )

        return AnalysisResult(
            status=AnalysisStatus.PASS,
            facts_extracted=facts,
            sources_verified=sources_verified,
            contradictions=[],
            recommendations=[],
            confidence=0.95,
        )

    def _extract_facts(self, narration: List[str]) -> List[str]:
        """Extract factual claims from narration

        Phase 1: Simple sentence splitting
        Phase 2: NLP-based fact extraction via LLM
        """
        facts = []
        for scene_text in narration:
            # Split by common sentence delimiters
            sentences = scene_text.replace(".", ". ").split(". ")
            for sentence in sentences:
                cleaned = sentence.strip()
                if cleaned and len(cleaned) > 5:
                    facts.append(cleaned)
        return facts

    def _verify_sources(self, facts: List[str]) -> bool:
        """Verify that facts have sources

        Phase 1: Check for obvious unsourced language markers
        Phase 2: Cross-reference with knowledge base / source documents

        Returns:
            True if all facts appear sourced, False otherwise
        """

        unsourced_phrases = [
            "i believe",
            "i think",
            "i guess",
            "probably",
            "maybe",
            "allegedly",
            "supposedly",
            "it seems",
            "might be",
            "could be",
        ]

        for fact in facts:
            fact_lower = fact.lower()
            for phrase in unsourced_phrases:
                if phrase in fact_lower:
                    return False

        return True

    def _detect_contradictions(self, facts: List[str]) -> List[str]:
        """Detect logical contradictions in facts

        Phase 1: Simple pattern matching for obvious opposites
        Phase 2: LLM-based semantic contradiction detection

        Returns:
            List of contradiction descriptions
        """
        contradictions = []

        # Simple contradiction detection: opposing claims
        for i, fact1 in enumerate(facts):
            for fact2 in facts[i + 1 :]:
                if self._are_contradictory(fact1, fact2):
                    contradictions.append(
                        f'Fact {i}: "{fact1[:50]}..." contradicts fact {i+1}'
                    )

        return contradictions

    def _are_contradictory(self, claim1: str, claim2: str) -> bool:
        """Check if two claims contradict each other

        Phase 1: Exact opposite detection
        Phase 2: Semantic similarity + negation
        """

        claim1_lower = claim1.lower()
        claim2_lower = claim2.lower()

        # Check for explicit opposites
        opposites = {
            "yes": "no",
            "enabled": "disabled",
            "true": "false",
            "on": "off",
            "open": "closed",
            "public": "private",
            "required": "optional",
        }

        for word, opposite in opposites.items():
            if word in claim1_lower and opposite in claim2_lower:
                return True
            if opposite in claim1_lower and word in claim2_lower:
                return True

        return False
