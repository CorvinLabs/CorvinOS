"""LLM-Driven Video Synthesis Module (ADR-0955)

Phase 1: Video spec generation (LLM → JSON) + validation (fail-closed)

Three components:
1. spec_schema: Pydantic VideoSpec with full validation
2. spec_validator: Fail-closed validator (semantic checks)
3. spec_generator: LLM spec generation + caching

Usage:
    from core.skills.video_producer_skill_2_0.llm_synthesis import SpecGenerator

    generator = SpecGenerator()
    spec = generator.generate_spec(
        brief="Create a 60-second ACS demo",
        duration_sec=60,
        language="de",
    )
    # Returns: VideoSpec (immutable, validated, auditable)

ADR Reference: ADR-0955 — Video Producer LLM Synthesis Architecture
"""

from .spec_schema import (
    VideoSpec,
    VideoMetadata,
    Scene,
    VisualElement,
    GenerationMetadata,
    estimate_tts_duration_ms,
    validate_narration_timing,
)
from .spec_validator import (
    SpecValidator,
    SpecValidationError,
    validate_spec,
    validate_and_raise,
)
from .spec_generator import SpecGenerator

__all__ = [
    "VideoSpec",
    "VideoMetadata",
    "Scene",
    "VisualElement",
    "GenerationMetadata",
    "SpecGenerator",
    "SpecValidator",
    "SpecValidationError",
    "estimate_tts_duration_ms",
    "validate_narration_timing",
    "validate_spec",
    "validate_and_raise",
]

__version__ = "1.0.0"
