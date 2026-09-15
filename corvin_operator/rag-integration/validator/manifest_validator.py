"""RAG Provider Manifest Validator.

Validates RAG provider manifests (YAML/JSON) against JSON Schema
with additional compliance checks (Zone gate, classification, secrets).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import jsonschema
    from jsonschema import Draft7Validator, ValidationError
except ImportError:
    print("ERROR: jsonschema not installed. pip install jsonschema")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. pip install pyyaml")
    sys.exit(1)


# ── Validation Results ────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Result of manifest validation."""
    valid: bool
    provider_id: str | None = None
    errors: list[str] | None = None
    warnings: list[str] | None = None

    def __str__(self) -> str:
        lines = []
        if self.valid:
            lines.append(f"✅ VALID — Provider: {self.provider_id}")
        else:
            lines.append(f"❌ INVALID")
            if self.errors:
                lines.append("Errors:")
                for err in self.errors:
                    lines.append(f"  • {err}")
        if self.warnings:
            lines.append("Warnings:")
            for warn in self.warnings:
                lines.append(f"  ⚠️  {warn}")
        return "\n".join(lines)


# ── Compliance Checks ────────────────────────────────────────────

class ComplianceValidator:
    """RAG-specific compliance checks."""

    # Secrets patterns to detect in manifest
    SECRET_PATTERNS = [
        r"api[_-]?key",
        r"secret",
        r"password",
        r"token",
        r"private[_-]?key",
        r"authorization",
        r"bearer",
        r"sk_",
        r"sk-",
    ]

    @staticmethod
    def check_no_hardcoded_secrets(manifest: dict) -> list[str]:
        """Verify no hardcoded secrets in manifest."""
        errors = []

        # Convert manifest to string and check for secret patterns
        manifest_str = json.dumps(manifest, indent=2).lower()

        for pattern in ComplianceValidator.SECRET_PATTERNS:
            if re.search(pattern, manifest_str):
                # Check if it's in token_env_var (which is OK)
                if "token_env_var" in manifest_str:
                    continue  # Expected pattern

                # Otherwise flag as error
                if pattern not in [
                    "authorization",
                    "bearer",
                ]:  # Expected in descriptions
                    errors.append(
                        f"Potential hardcoded secret detected (pattern: {pattern}). "
                        f"Use token_env_var pointing to vault instead."
                    )

        return errors

    @staticmethod
    def check_zone_consistency(manifest: dict) -> list[str]:
        """Verify compliance_zone is properly configured."""
        errors = []

        spec = manifest.get("spec", {})
        zone = spec.get("compliance_zone", {})

        if zone.get("required"):
            if not zone.get("allowed_regions"):
                errors.append(
                    "compliance_zone.required=true but allowed_regions is empty. "
                    "Must specify at least one region (EU, DE, FR, etc.)"
                )

        return errors

    @staticmethod
    def check_auth_config(manifest: dict) -> list[str]:
        """Verify auth is properly configured."""
        errors = []

        spec = manifest.get("spec", {})
        retrieval = spec.get("retrieval", {})
        auth = retrieval.get("auth", {})

        auth_type = auth.get("type")
        token_env_var = auth.get("token_env_var")

        if auth_type != "none":
            if not token_env_var:
                errors.append(
                    f"Auth type '{auth_type}' requires token_env_var. "
                    f"Use env var name like 'RAG_<PROVIDER>_TOKEN' (not literal secret!)."
                )

            # Validate env var name format
            if token_env_var and not re.match(r"^[A-Z_][A-Z0-9_]*$", token_env_var):
                errors.append(
                    f"token_env_var '{token_env_var}' invalid. "
                    f"Must be uppercase letters/numbers/underscore."
                )

        return errors

    @staticmethod
    def check_classification(manifest: dict) -> list[str]:
        """Verify data classification is appropriate."""
        errors = []
        warnings = []

        spec = manifest.get("spec", {})
        classification = spec.get("classification", {})

        data_type = classification.get("data_type")
        pii_risk = classification.get("pii_risk", "low")

        # CONFIDENTIAL/SECRET requires approval
        if data_type in ["CONFIDENTIAL", "SECRET"]:
            if not classification.get("requires_approval"):
                warnings.append(
                    f"data_type={data_type} should set requires_approval=true. "
                    f"High-risk data should require tenant approval."
                )

        # High PII risk should be flagged
        if pii_risk == "high":
            warnings.append(
                "pii_risk=high. Ensure PII redaction is enabled on consumer side."
            )

        return errors, warnings

    @staticmethod
    def check_response_schema(manifest: dict) -> list[str]:
        """Verify response_schema is properly defined."""
        errors = []

        spec = manifest.get("spec", {})
        retrieval = spec.get("retrieval", {})
        response_schema = retrieval.get("response_schema", {})

        if not response_schema:
            errors.append("retrieval.response_schema must be defined.")
            return errors

        # Response schema should have type
        if "type" not in response_schema:
            errors.append(
                "retrieval.response_schema must have 'type' property. "
                "Typically: {type: 'object', properties: {...}}"
            )

        return errors

    @staticmethod
    def check_erasure_handler(manifest: dict) -> list[str]:
        """Verify erasure handler (GDPR Art. 17)."""
        warnings = []

        spec = manifest.get("spec", {})
        erasure = spec.get("erasure_handler", {})

        if not erasure or erasure.get("type") == "none":
            warnings.append(
                "No erasure_handler defined. "
                "This provider does not support GDPR Art. 17 (Right to be Forgotten). "
                "Data cannot be deleted on user request."
            )

        return warnings


# ── Main Validator ────────────────────────────────────────────

class ManifestValidator:
    """Validates RAG provider manifests."""

    def __init__(self, schema_path: Path | str | None = None):
        """Initialize with JSON schema."""
        if schema_path is None:
            # Use default schema in same directory
            schema_path = Path(__file__).parent.parent / "schemas" / "rag-provider-manifest.schema.json"

        self.schema_path = Path(schema_path)
        self.schema = self._load_schema()
        self.validator = Draft7Validator(self.schema)

    def _load_schema(self) -> dict:
        """Load JSON schema from file."""
        if not self.schema_path.exists():
            raise FileNotFoundError(f"Schema not found: {self.schema_path}")

        with open(self.schema_path) as f:
            return json.load(f)

    def validate_file(self, manifest_path: Path | str) -> ValidationResult:
        """Validate a manifest file (YAML or JSON)."""
        manifest_path = Path(manifest_path)

        if not manifest_path.exists():
            return ValidationResult(
                valid=False,
                errors=[f"File not found: {manifest_path}"],
            )

        # Load manifest
        try:
            if manifest_path.suffix in [".yaml", ".yml"]:
                with open(manifest_path) as f:
                    manifest = yaml.safe_load(f)
            elif manifest_path.suffix == ".json":
                with open(manifest_path) as f:
                    manifest = json.load(f)
            else:
                return ValidationResult(
                    valid=False,
                    errors=[f"Unsupported file format: {manifest_path.suffix}"],
                )
        except Exception as e:
            return ValidationResult(
                valid=False,
                errors=[f"Failed to parse manifest: {e}"],
            )

        # Validate
        return self.validate_dict(manifest)

    def validate_dict(self, manifest: dict) -> ValidationResult:
        """Validate a manifest dict."""
        errors = []
        warnings = []

        # 1. JSON Schema validation
        try:
            self.validator.validate(manifest)
        except ValidationError as e:
            errors.append(f"Schema validation: {e.message}")

        # Early exit if schema is invalid
        if errors:
            return ValidationResult(valid=False, errors=errors)

        provider_id = manifest.get("metadata", {}).get("id")

        # 2. Compliance checks
        errors.extend(ComplianceValidator.check_no_hardcoded_secrets(manifest))
        errors.extend(ComplianceValidator.check_zone_consistency(manifest))
        errors.extend(ComplianceValidator.check_auth_config(manifest))
        errors.extend(ComplianceValidator.check_response_schema(manifest))

        class_errors, class_warnings = ComplianceValidator.check_classification(manifest)
        errors.extend(class_errors)
        warnings.extend(class_warnings)

        warnings.extend(ComplianceValidator.check_erasure_handler(manifest))

        # Return result
        valid = len(errors) == 0

        return ValidationResult(
            valid=valid,
            provider_id=provider_id,
            errors=errors if errors else None,
            warnings=warnings if warnings else None,
        )


# ── CLI ────────────────────────────────────────────────────────

def main():
    """CLI: validate manifest file(s)."""
    if len(sys.argv) < 2:
        print("Usage: python manifest_validator.py <manifest.yaml> [...]")
        sys.exit(1)

    validator = ManifestValidator()
    exit_code = 0

    for manifest_file in sys.argv[1:]:
        result = validator.validate_file(manifest_file)
        print(f"\n{result}")
        if not result.valid:
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
