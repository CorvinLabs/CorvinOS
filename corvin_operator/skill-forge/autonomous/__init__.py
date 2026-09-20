"""Autonomous Skill Forge — validation suite + Loss-Driven Trigger Detection.

Layers 1–2: structural validation + test-based validation
Trigger Detection: Loss signal detection for confidence < 0.70 (ADR-0613)
"""

# Validation exports
from .result import ValidationResult
from .validator import SkillValidator
from .validator_layer1 import StructuralValidator
from .validator_layer2 import TestValidator

# Trigger detection exports
from .trigger_detector import (
    LossTrigger,
    SkillLossTriggerDetector,
)

__all__ = [
    # Validation
    "SkillValidator",
    "ValidationResult",
    "StructuralValidator",
    "TestValidator",
    # Trigger detection
    "SkillLossTriggerDetector",
    "LossTrigger",
]
