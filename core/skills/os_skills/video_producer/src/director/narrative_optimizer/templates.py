"""Story structure templates for narrative organization.

Defines three proven narrative structures:
1. Hero's Journey: For inspiring action and personal transformation
2. Three-Act Structure: For technical explanations and problem-solving
3. Problem-Solution: For pitches and persuasive content
"""

from dataclasses import dataclass
from typing import Dict, List, Any
from enum import Enum


class NarrativeTemplate(Enum):
    """Available narrative structures"""
    HERO_JOURNEY = "hero_journey"
    THREE_ACT = "three_act"
    PROBLEM_SOLUTION = "problem_solution"


@dataclass
class NarrativeScene:
    """Represents a scene in a narrative structure"""
    name: str
    purpose: str
    duration_pct: float
    key_elements: List[str]

    def validate(self) -> bool:
        """Validate scene has valid duration"""
        return 0.0 <= self.duration_pct <= 1.0


class StoryTemplate:
    """Base class for narrative structures"""

    HERO_JOURNEY: Dict[str, Dict[str, Any]] = {
        "hook": {
            "duration_pct": 0.10,
            "purpose": "emotional_engagement",
            "key_elements": ["attention_grab", "curiosity"],
            "description": "Capture attention and create emotional hook"
        },
        "context": {
            "duration_pct": 0.20,
            "purpose": "education",
            "key_elements": ["background", "problem_statement"],
            "description": "Establish context and background"
        },
        "action": {
            "duration_pct": 0.50,
            "purpose": "learning",
            "key_elements": ["main_content", "transformation"],
            "description": "Deliver core message and transformation"
        },
        "payoff": {
            "duration_pct": 0.15,
            "purpose": "inspiration",
            "key_elements": ["results", "emotional_impact"],
            "description": "Show results and emotional satisfaction"
        },
        "cta": {
            "duration_pct": 0.05,
            "purpose": "call_to_action",
            "key_elements": ["next_steps", "engagement"],
            "description": "Clear call to action"
        }
    }

    THREE_ACT: Dict[str, Dict[str, Any]] = {
        "setup": {
            "duration_pct": 0.25,
            "purpose": "exposition",
            "key_elements": ["problem", "characters", "world"],
            "description": "Establish the problem and context"
        },
        "confrontation": {
            "duration_pct": 0.50,
            "purpose": "development",
            "key_elements": ["rising_action", "complications", "climax"],
            "description": "Develop problem and build to climax"
        },
        "resolution": {
            "duration_pct": 0.25,
            "purpose": "conclusion",
            "key_elements": ["falling_action", "resolution"],
            "description": "Resolve the problem and conclude"
        }
    }

    PROBLEM_SOLUTION: Dict[str, Dict[str, Any]] = {
        "problem_statement": {
            "duration_pct": 0.15,
            "purpose": "problem_definition",
            "key_elements": ["pain_point", "urgency"],
            "description": "Clearly state the problem"
        },
        "why_matters": {
            "duration_pct": 0.10,
            "purpose": "relevance",
            "key_elements": ["impact", "consequences"],
            "description": "Explain why the problem matters"
        },
        "solution_intro": {
            "duration_pct": 0.10,
            "purpose": "introduction",
            "key_elements": ["solution_overview", "benefits"],
            "description": "Introduce the solution approach"
        },
        "solution_detail": {
            "duration_pct": 0.50,
            "purpose": "detailed_explanation",
            "key_elements": ["implementation", "features", "advantages"],
            "description": "Explain solution in detail"
        },
        "proof": {
            "duration_pct": 0.10,
            "purpose": "validation",
            "key_elements": ["evidence", "results", "testimonials"],
            "description": "Provide proof of solution effectiveness"
        },
        "closing": {
            "duration_pct": 0.05,
            "purpose": "call_to_action",
            "key_elements": ["next_steps", "contact"],
            "description": "Clear closing and next steps"
        }
    }

    @classmethod
    def get_template(cls, template_type: NarrativeTemplate) -> Dict[str, Dict[str, Any]]:
        """Get a narrative template by type"""
        templates = {
            NarrativeTemplate.HERO_JOURNEY: cls.HERO_JOURNEY,
            NarrativeTemplate.THREE_ACT: cls.THREE_ACT,
            NarrativeTemplate.PROBLEM_SOLUTION: cls.PROBLEM_SOLUTION,
        }
        return templates[template_type]

    @classmethod
    def validate_template_duration(cls, template: Dict[str, Dict[str, Any]]) -> bool:
        """Validate that template durations sum to 1.0"""
        total = sum(scene["duration_pct"] for scene in template.values())
        return abs(total - 1.0) < 0.001  # Allow floating point rounding

    @classmethod
    def list_all_templates(cls) -> List[NarrativeTemplate]:
        """List all available templates"""
        return list(NarrativeTemplate)

    @classmethod
    def get_template_description(cls, template_type: NarrativeTemplate) -> str:
        """Get human-readable description of a template"""
        descriptions = {
            NarrativeTemplate.HERO_JOURNEY: "Best for inspiring action and personal transformation stories",
            NarrativeTemplate.THREE_ACT: "Best for technical explanations with rising tension and resolution",
            NarrativeTemplate.PROBLEM_SOLUTION: "Best for pitches and persuasive marketing content",
        }
        return descriptions[template_type]
