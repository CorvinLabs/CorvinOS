"""LLM-based narrative structure suggestion engine.

Analyzes content and suggests optimal narrative structure while
ensuring all facts are preserved.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import json

from core.skills.os_skills.video_producer.src.director.narrative_optimizer.templates import NarrativeTemplate, StoryTemplate
from core.skills.os_skills.video_producer.src.director.narrative_optimizer.fact_extractor import Fact


@dataclass
class StructureSuggestion:
    """Represents a suggested narrative structure"""
    suggested_template: NarrativeTemplate
    reasoning: str
    facts_preserved: bool
    estimated_pacing: Dict[str, float]
    confidence_score: float
    alternative_templates: List[NarrativeTemplate]


class NarrativeSuggester:
    """LLM-based narrative structure suggestion"""

    def __init__(self, llm_provider: Optional[str] = None):
        """Initialize suggester.

        Args:
            llm_provider: LLM provider ('claude', 'openai', etc). Defaults to mock.
        """
        self.llm_provider = llm_provider or "mock"
        self.suggestion_history: List[StructureSuggestion] = []

    def suggest_structure(self, topic: str, facts: List[Fact],
                         duration_seconds: int, audience: str) -> StructureSuggestion:
        """Suggest optimal narrative structure based on content.

        Args:
            topic: The main topic/title of the content
            facts: Key facts to be preserved
            duration_seconds: Target duration
            audience: Target audience description

        Returns:
            StructureSuggestion with recommended template and reasoning
        """
        # Analyze content characteristics
        analysis = self._analyze_content(topic, facts, audience)

        # Get LLM recommendation
        llm_suggestion = self._get_llm_suggestion(topic, facts, duration_seconds,
                                                  audience, analysis)

        # Validate facts will be preserved
        facts_preserved = self._validate_facts_preserved(facts, llm_suggestion)

        # Build suggestion object
        suggestion = StructureSuggestion(
            suggested_template=llm_suggestion["template"],
            reasoning=llm_suggestion["reasoning"],
            facts_preserved=facts_preserved,
            estimated_pacing=self._calculate_pacing(
                llm_suggestion["template"],
                duration_seconds
            ),
            confidence_score=llm_suggestion.get("confidence", 0.8),
            alternative_templates=llm_suggestion.get("alternatives", [])
        )

        self.suggestion_history.append(suggestion)
        return suggestion

    def _analyze_content(self, topic: str, facts: List[Fact],
                        audience: str) -> Dict[str, Any]:
        """Analyze content characteristics"""
        return {
            "topic_length": len(topic.split()),
            "num_facts": len(facts),
            "num_statistics": sum(1 for f in facts if "%" in f.text or "$" in f.text),
            "num_technical_facts": sum(1 for f in facts if f.fact_type == "asset"),
            "audience_type": "technical" if "engineer" in audience.lower() else "general",
            "average_fact_importance": sum(f.importance_score for f in facts) / len(facts) if facts else 0.5
        }

    def _get_llm_suggestion(self, topic: str, facts: List[Fact],
                           duration_seconds: int, audience: str,
                           analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Get suggestion from LLM (or mock implementation)"""

        if self.llm_provider == "mock":
            return self._mock_llm_suggestion(topic, facts, duration_seconds,
                                           audience, analysis)

        # Production LLM implementation would go here
        raise NotImplementedError(f"LLM provider {self.llm_provider} not implemented")

    def _mock_llm_suggestion(self, topic: str, facts: List[Fact],
                            duration_seconds: int, audience: str,
                            analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Mock LLM suggestion for testing"""

        # Decision logic based on content analysis
        if "problem" in topic.lower() and "solution" in topic.lower():
            template = NarrativeTemplate.PROBLEM_SOLUTION
            reasoning = "Content is clearly problem-focused; Problem-Solution structure fits perfectly"
        elif analysis["audience_type"] == "technical" and analysis["num_technical_facts"] > 3:
            template = NarrativeTemplate.THREE_ACT
            reasoning = "Technical audience with substantial technical content; Three-Act structure provides clear progression"
        else:
            template = NarrativeTemplate.HERO_JOURNEY
            reasoning = "General audience with inspiring message; Hero's Journey creates emotional engagement"

        alternatives = [
            t for t in NarrativeTemplate
            if t != template
        ]

        return {
            "template": template,
            "reasoning": reasoning,
            "confidence": 0.85,
            "alternatives": alternatives,
            "notes": f"Analyzed {len(facts)} facts, identified {analysis['num_statistics']} statistics"
        }

    def _validate_facts_preserved(self, facts: List[Fact],
                                 suggestion: Dict[str, Any]) -> bool:
        """Check if suggested structure will preserve all facts"""
        template = StoryTemplate.get_template(suggestion["template"])
        num_scenes = len(template)
        facts_per_scene = len(facts) / num_scenes if num_scenes > 0 else 0

        # Rough validation: ensure we have enough "space" for facts
        return facts_per_scene < 20  # Arbitrary threshold

    def _calculate_pacing(self, template: NarrativeTemplate,
                         duration_seconds: int) -> Dict[str, float]:
        """Calculate pacing for each scene in the template"""
        template_def = StoryTemplate.get_template(template)
        pacing = {}

        for scene_name, scene_def in template_def.items():
            pacing[scene_name] = scene_def["duration_pct"] * duration_seconds

        return pacing

    def get_suggestions_summary(self) -> Dict[str, Any]:
        """Get summary of all suggestions made"""
        if not self.suggestion_history:
            return {"total": 0, "suggestions": []}

        templates_used = {}
        for sugg in self.suggestion_history:
            template_name = sugg.suggested_template.value
            templates_used[template_name] = templates_used.get(template_name, 0) + 1

        return {
            "total": len(self.suggestion_history),
            "templates_used": templates_used,
            "average_confidence": sum(s.confidence_score for s in self.suggestion_history) / len(self.suggestion_history),
            "facts_preserved_rate": sum(1 for s in self.suggestion_history if s.facts_preserved) / len(self.suggestion_history)
        }
