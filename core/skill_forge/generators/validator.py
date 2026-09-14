"""Manifest Validator — fail-closed checks."""

from typing import List, Tuple
from .manifest import SkillManifest, SkillManifestSchema


class ManifestValidator:
    """Comprehensive manifest validation."""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate(self, manifest: SkillManifest) -> Tuple[bool, List[str], List[str]]:
        """Validate manifest (fail-closed). Returns (is_valid, errors, warnings)."""
        self.errors = []
        self.warnings = []

        # Schema validation
        is_valid, schema_error = SkillManifestSchema.validate(manifest.to_dict())
        if not is_valid:
            self.errors.append(f"Schema: {schema_error}")
            return False, self.errors, self.warnings

        # Content checks
        self._check_body(manifest)
        self._check_naming(manifest)
        self._check_metadata(manifest)

        return len(self.errors) == 0, self.errors, self.warnings

    def _check_body(self, manifest: SkillManifest) -> None:
        """Check body content for safety and completeness."""

        # Fail-closed: require certain sections based on type
        required_sections = {
            "learned-experience": ["Pattern", "When to Use", "Examples"],
            "reasoning": ["Thesis", "Antithesis", "Synthesis"],
            "reference": ["Overview", "Examples"],
            "automation": ["Algorithm", "Input Contract", "Output Contract"],
        }

        sections = required_sections.get(manifest.skill_type.value, [])
        for section in sections:
            if section not in manifest.body_md:
                self.errors.append(f"Missing required section: '{section}'")

        # Check for dangerous patterns
        dangerous = [
            "ignore previous instructions",
            "disregard the above",
            "you are now",
            "<|im_start|>",
            "<|im_end|>",
            "bypass",
        ]
        for pattern in dangerous:
            if pattern.lower() in manifest.body_md.lower():
                self.errors.append(f"Dangerous pattern detected: '{pattern}'")

        # Warn if body is mostly code
        lines = manifest.body_md.split('\n')
        code_lines = sum(1 for l in lines if l.strip().startswith('```') or l.strip().startswith('    '))
        code_ratio = code_lines / max(len(lines), 1)
        if code_ratio > 0.4:
            self.warnings.append("High code density (>40%) — consider using forge tool instead")

    def _check_naming(self, manifest: SkillManifest) -> None:
        """Check name and title consistency."""

        if not manifest.name.replace("_", "").isalnum():
            self.errors.append("Name must be alphanumeric with underscores only")

        if manifest.name.startswith("_"):
            self.errors.append("Name must not start with underscore")

        # Warn if name and title are too different
        name_words = set(manifest.name.lower().split("_"))
        title_words = set(manifest.title.lower().split())
        overlap = name_words & title_words
        if not overlap:
            self.warnings.append("Name and title have no common words — might be confusing")

    def _check_metadata(self, manifest: SkillManifest) -> None:
        """Check metadata completeness."""

        if not manifest.version:
            self.errors.append("Version required (e.g., '1.0.0')")

        if manifest.tags and len(manifest.tags) > 10:
            self.errors.append("Too many tags (max 10)")

        if not manifest.generated_at and manifest.generator_phase:
            self.warnings.append("Generated skill missing timestamp")


def validate_manifest_dict(data: dict) -> Tuple[bool, str]:
    """Quick validation of dict. Returns (is_valid, error_msg)."""
    is_valid, error = SkillManifestSchema.validate(data)
    return is_valid, error or ""
