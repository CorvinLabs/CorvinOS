"""Skill validator orchestrator — runs all validation layers (1–5) with layer mask."""
from __future__ import annotations

import time
from pathlib import Path

from .result import ValidationResult
from .validator_layer1 import StructuralValidator
from .validator_layer2 import TestValidator


class SkillValidator:
    """Multi-layer skill validator — orchestrates all validation layers."""

    def __init__(self):
        self.layer1 = StructuralValidator()
        self.layer2 = TestValidator()

    def validate_all_layers(
        self,
        skill_dir: Path,
        layer_mask: int = 0b11111,  # Bitmask to select which layers run
    ) -> list[ValidationResult]:
        """Run all enabled layers (selected by layer_mask).

        Args:
            skill_dir: Path to skill directory (contains skill.json, tests/, etc.)
            layer_mask: Bitmask selecting which layers run:
                - bit 0 (1): Layer 1 (structural)
                - bit 1 (2): Layer 2 (test-based)
                - bits 2–4: Reserved for future layers

        Returns:
            List of ValidationResult per layer, in order.
            If any layer fails, overall validation = FAILED (fail-closed).
        """
        results: list[ValidationResult] = []

        # Layer 1: Structural validation
        if layer_mask & 0b00001:
            start = time.time()
            result = self.layer1.validate(skill_dir)
            result.duration_ms = (time.time() - start) * 1000
            results.append(result)
            if not result.passed:
                # Fail-closed: stop here, don't run downstream layers
                return results

        # Layer 2: Test-based validation
        if layer_mask & 0b00010:
            start = time.time()
            result = self.layer2.validate(skill_dir)
            result.duration_ms = (time.time() - start) * 1000
            results.append(result)
            if not result.passed:
                return results

        # Layers 3–5: Reserved for future expansion
        # (stub here for now)

        return results

    def validate_all_layers_verbose(
        self,
        skill_dir: Path,
        layer_mask: int = 0b11111,
    ) -> tuple[bool, list[ValidationResult]]:
        """Run all layers and return (overall_passed, results).

        Args:
            skill_dir: Path to skill directory.
            layer_mask: Bitmask selecting layers.

        Returns:
            (overall_passed, results) where overall_passed is True iff
            all enabled layers passed.
        """
        results = self.validate_all_layers(skill_dir, layer_mask)
        overall = all(r.passed for r in results)
        return overall, results
