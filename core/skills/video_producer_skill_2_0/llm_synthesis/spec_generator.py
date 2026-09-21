"""LLM Spec Generator (Phase 1) — ADR-0955

Generates VideoSpec from natural language brief using LLM.
Integrates with caching and validation.
"""

import json
import hashlib
from typing import Optional
import logging

from anthropic import Anthropic
from .spec_schema import VideoSpec, estimate_tts_duration_ms
from .spec_validator import validate_and_raise

logger = logging.getLogger(__name__)

# LLM Prompt Template
PROMPT_TEMPLATE = """You are a professional video scriptwriter and designer creating structured video specifications.

TASK:
Generate a video specification (JSON) for:

Brief: {brief}
Duration: {duration_sec} seconds
Target audience: {audience}
Language: {language}
Tone: {tone}

REQUIREMENTS:
1. Structure into 4–8 scenes (roughly {scene_duration_sec} seconds each)
2. Each scene MUST have:
   - id: unique identifier (lowercase, underscores: scene_001, intro, etc.)
   - type: 'slide' (text/graphics) | 'svg_diagram' (flowchart) | 'blender_render' (3D)
   - duration_seconds: exact duration for this scene
   - elements: array of visual elements (text boxes, shapes, colors)
   - narration: complete German text for this scene
   - narration_duration_ms: {narration_ms_estimate} ± 2000 ms (±2 sec tolerance)
3. Total narration duration: {duration_sec}s × 1000 = {duration_ms} ms (tolerance ±5000 ms)
4. Color palette (use hex codes):
   - accent_blue: #4287F5
   - accent_green: #34D399
   - accent_orange: #F97316
   - accent_red: #EF4444
   - text_white: #FFFFFF
   - text_gray: #9CA3AF
5. No hallucinated Blender scenes or models (stick to common .blend files or none)
6. Total scene durations must sum to {duration_sec} seconds (±5s tolerance)

SCHEMA:
{{
  "spec_version": "1.0",
  "generation_metadata": {{
    "llm_model": "claude-opus-5",
    "llm_temperature": 0.3,
    "prompt_hash": "",
    "request_hash": ""
  }},
  "video_metadata": {{
    "duration_seconds": {duration_sec},
    "fps": 30,
    "resolution": [1920, 1080],
    "language": "{language}",
    "narrator_voice": "nova"
  }},
  "scenes": [
    {{
      "id": "intro",
      "type": "slide",
      "duration_seconds": X,
      "elements": [
        {{"type": "text", "text": "...", "size": 72, "color": "#4287F5", "x": 100, "y": 250}}
      ],
      "narration": "Full German text here...",
      "narration_duration_ms": NNNN
    }}
  ]
}}

OUTPUT:
Respond ONLY with valid JSON (no markdown, no explanation).
"""

class SpecGenerator:
    """Generate VideoSpec from natural language"""

    def __init__(self, api_key: str = None):
        self.client = Anthropic(api_key=api_key)

    def generate_spec(
        self,
        brief: str,
        duration_sec: int = 60,
        audience: str = "general",
        language: str = "de",
        tone: str = "professional",
    ) -> VideoSpec:
        """Generate video spec from brief (with validation)

        Args:
            brief: Natural language description
            duration_sec: Target duration (1–600 seconds)
            audience: Target audience (general, technical, sales, educational)
            language: Language code (de, en)
            tone: Tone (professional, casual, technical, storytelling)

        Returns:
            Validated VideoSpec

        Raises:
            SpecValidationError if generated spec is invalid
        """

        # Estimate narration pacing
        scene_count = max(4, min(8, duration_sec // 10))
        scene_duration_sec = duration_sec // scene_count
        narration_ms_estimate = estimate_tts_duration_ms(
            "Placeholder narration text for duration estimation"  # Generic estimate
        )

        # Format prompt
        prompt = PROMPT_TEMPLATE.format(
            brief=brief,
            duration_sec=duration_sec,
            audience=audience,
            language=language,
            tone=tone,
            scene_duration_sec=scene_duration_sec,
            narration_ms_estimate=narration_ms_estimate,
            duration_ms=duration_sec * 1000,
        )

        logger.info(f"🎬 Generating video spec: {brief[:50]}... ({duration_sec}s)")

        # Call LLM
        try:
            response = self.client.messages.create(
                model="claude-opus-5",
                max_tokens=4000,
                temperature=0.3,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )

            spec_json_str = response.content[0].text.strip()

            # Parse JSON
            spec_dict = json.loads(spec_json_str)

            # Add metadata
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
            request_hash = hashlib.sha256(f"{brief}_{duration_sec}_{language}".encode()).hexdigest()

            spec_dict["generation_metadata"]["prompt_hash"] = prompt_hash
            spec_dict["generation_metadata"]["request_hash"] = request_hash

            # Create VideoSpec (Pydantic will validate)
            spec = VideoSpec(**spec_dict)

            # Validate (fail-closed)
            spec = validate_and_raise(spec)

            logger.info(f"✅ Spec generated: {len(spec.scenes)} scenes, {spec.video_metadata.duration_seconds}s")

            return spec

        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON from LLM: {e}")
            raise ValueError(f"LLM response is not valid JSON: {e}")

        except Exception as e:
            logger.error(f"❌ Spec generation failed: {e}")
            raise


if __name__ == "__main__":
    import os

    # Test: Generate a spec
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set")
        exit(1)

    generator = SpecGenerator(api_key=api_key)

    spec = generator.generate_spec(
        brief="ACS Runner demo: autonomous context selection for LLM routing",
        duration_sec=30,
        language="de",
        tone="professional",
    )

    print(f"✅ Generated spec: {len(spec.scenes)} scenes")
    print(json.dumps(spec.dict(), indent=2, default=str))
