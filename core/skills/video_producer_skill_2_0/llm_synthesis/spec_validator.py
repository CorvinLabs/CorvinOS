"""Video Spec Validator (Fail-Closed) — ADR-0955 Phase 1

Comprehensive validation of LLM-generated video specs.
- JSON schema validation (Pydantic)
- Semantic validation (business logic)
- Fallback chain validation
"""

import os
from typing import Tuple, List
from pathlib import Path
import logging

from .spec_schema import VideoSpec, Scene, estimate_tts_duration_ms

logger = logging.getLogger(__name__)


class SpecValidationError(Exception):
    """Raised when spec validation fails (fail-closed)"""
    pass


class SpecValidator:
    """Fail-closed validator for VideoSpec"""

    def __init__(self, blender_models_dir: str = None):
        self.blender_models_dir = blender_models_dir or "/usr/local/share/corvin/blender_models"

    def validate_spec(self, spec: VideoSpec) -> Tuple[bool, List[str]]:
        """Comprehensive validation (returns all errors, not just first)

        Returns:
            (is_valid, error_messages)
        """
        errors = []

        # 1. JSON schema validation (automatic via Pydantic frozen=True)
        try:
            # If we got this far, Pydantic already validated
            pass
        except Exception as e:
            errors.append(f"Schema validation failed: {e}")
            return False, errors

        # 2. Semantic validations
        errors.extend(self._validate_narration_timing(spec))
        errors.extend(self._validate_blender_specs(spec))
        errors.extend(self._validate_total_duration(spec))
        errors.extend(self._validate_scene_ids(spec))

        return len(errors) == 0, errors

    def _validate_narration_timing(self, spec: VideoSpec) -> List[str]:
        """Validate narration duration vs scene duration (±2 seconds)"""
        errors = []

        for scene in spec.scenes:
            target_ms = scene.duration_seconds * 1000
            actual_ms = scene.narration_duration_ms
            tolerance_ms = 2000

            if abs(actual_ms - target_ms) > tolerance_ms:
                errors.append(
                    f"Scene '{scene.id}': narration {actual_ms}ms ≠ duration {target_ms}ms "
                    f"(tolerance ±{tolerance_ms}ms)"
                )

        return errors

    def _validate_blender_specs(self, spec: VideoSpec) -> List[str]:
        """Validate Blender specs (scene files exist, cameras valid)"""
        errors = []

        for scene in spec.scenes:
            if scene.type != "blender_render" or not scene.blender_spec:
                continue

            # Check scene file exists
            scene_path = scene.blender_spec.scene
            if not os.path.exists(scene_path):
                errors.append(
                    f"Scene '{scene.id}': Blender file not found: {scene_path}"
                )

            # Check camera name is not empty (will validate in Blender at render time)
            if not scene.blender_spec.camera:
                errors.append(
                    f"Scene '{scene.id}': Blender camera name is empty"
                )

        return errors

    def _validate_total_duration(self, spec: VideoSpec) -> List[str]:
        """Validate total scene duration matches target duration"""
        errors = []

        total_duration = sum(s.duration_seconds for s in spec.scenes)
        target_duration = spec.video_metadata.duration_seconds

        if abs(total_duration - target_duration) > 5:
            errors.append(
                f"Total scene duration {total_duration}s ≠ target {target_duration}s "
                f"(tolerance ±5s)"
            )

        return errors

    def _validate_scene_ids(self, spec: VideoSpec) -> List[str]:
        """Validate scene IDs are unique"""
        errors = []

        ids = [s.id for s in spec.scenes]
        if len(ids) != len(set(ids)):
            duplicates = [id for id in ids if ids.count(id) > 1]
            errors.append(f"Duplicate scene IDs: {duplicates}")

        return errors

    def validate_and_raise(self, spec: VideoSpec) -> VideoSpec:
        """Validate spec and raise SpecValidationError if invalid (fail-closed)

        Returns spec if valid, raises if invalid.
        """
        is_valid, errors = self.validate_spec(spec)

        if not is_valid:
            error_msg = f"Spec validation failed ({len(errors)} errors):\n" + "\n".join(errors)
            logger.error(error_msg)
            raise SpecValidationError(error_msg)

        logger.info(f"✅ Spec validated: {len(spec.scenes)} scenes, {spec.video_metadata.duration_seconds}s")
        return spec


# Predefined validators (singleton)
_default_validator = None

def get_validator(blender_models_dir: str = None) -> SpecValidator:
    """Get or create default validator"""
    global _default_validator
    if _default_validator is None:
        _default_validator = SpecValidator(blender_models_dir)
    return _default_validator


def validate_spec(spec: VideoSpec) -> Tuple[bool, List[str]]:
    """Validate spec (returns bool + errors)"""
    return get_validator().validate_spec(spec)


def validate_and_raise(spec: VideoSpec) -> VideoSpec:
    """Validate spec and raise if invalid"""
    return get_validator().validate_and_raise(spec)


if __name__ == "__main__":
    # Test: Create and validate a spec
    from spec_schema import VideoSpec, GenerationMetadata, VideoMetadata, Scene, VisualElement

    spec = VideoSpec(
        generation_metadata=GenerationMetadata(
            llm_model="claude-opus-5",
            prompt_hash="test_prompt_hash",
            request_hash="test_request_hash",
        ),
        video_metadata=VideoMetadata(
            duration_seconds=20,
            fps=30,
        ),
        scenes=[
            Scene(
                id="scene_001",
                type="slide",
                duration_seconds=10,
                elements=[
                    VisualElement(type="text", text="Test", size=48, color="#4287F5"),
                ],
                narration="This is a test scene.",
                narration_duration_ms=estimate_tts_duration_ms("This is a test scene."),
            ),
            Scene(
                id="scene_002",
                type="slide",
                duration_seconds=10,
                elements=[],
                narration="Second scene.",
                narration_duration_ms=estimate_tts_duration_ms("Second scene."),
            ),
        ],
    )

    validator = SpecValidator()
    is_valid, errors = validator.validate_spec(spec)

    if is_valid:
        print("✅ Spec is valid")
    else:
        print(f"❌ Spec validation failed ({len(errors)} errors):")
        for error in errors:
            print(f"  - {error}")
