"""Test suite for Settings Validator (ADR-0XXX k=4)."""

import pytest

from core.orchestration.plugin_system.models import SettingsValidator, ValidationError


class TestSettingsValidator:
    """Tests for JSON Schema-based settings validation."""

    def test_validate_valid_settings(self):
        """Test validating settings that match schema."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string", "enum": ["haiku", "sonnet", "opus"]},
                "depth": {"type": "integer", "minimum": 1, "maximum": 5}
            },
            "required": ["model"]
        }

        validator = SettingsValidator(schema)
        assert validator.validate({"model": "sonnet", "depth": 3})

    def test_validate_invalid_enum(self):
        """Test validation fails for invalid enum value."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string", "enum": ["haiku", "sonnet"]}
            }
        }

        validator = SettingsValidator(schema)

        with pytest.raises(ValidationError):
            validator.validate({"model": "invalid"})

    def test_validate_invalid_integer_range(self):
        """Test validation fails for integer out of range."""
        schema = {
            "type": "object",
            "properties": {
                "depth": {"type": "integer", "minimum": 1, "maximum": 5}
            }
        }

        validator = SettingsValidator(schema)

        with pytest.raises(ValidationError):
            validator.validate({"depth": 10})

    def test_validate_missing_required_field(self):
        """Test validation fails when required field is missing."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string"}
            },
            "required": ["model"]
        }

        validator = SettingsValidator(schema)

        with pytest.raises(ValidationError):
            validator.validate({})

    def test_validate_wrong_type(self):
        """Test validation fails for wrong type."""
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"}
            }
        }

        validator = SettingsValidator(schema)

        with pytest.raises(ValidationError):
            validator.validate({"count": "not an integer"})

    def test_validate_allows_additional_properties(self):
        """Test validation with additionalProperties not restricted."""
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string"}
            }
        }

        validator = SettingsValidator(schema)
        # Should pass even with extra properties
        assert validator.validate({"model": "sonnet", "extra": "field"})

    def test_validate_empty_schema(self):
        """Test validation with empty schema."""
        schema = {}
        validator = SettingsValidator(schema)
        assert validator.validate({})
        assert validator.validate({"anything": "goes"})


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
