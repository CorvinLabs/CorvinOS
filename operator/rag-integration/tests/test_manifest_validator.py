"""Tests for RAG Provider Manifest Validator."""
import json
from pathlib import Path

import pytest

from ..validator.manifest_validator import ManifestValidator, ValidationResult


@pytest.fixture
def validator():
    """Initialize validator with test schema."""
    return ManifestValidator()


@pytest.fixture
def valid_manifest():
    """Minimal valid manifest."""
    return {
        "api_version": "rag.corvin.io/v1",
        "kind": "RAGProvider",
        "metadata": {
            "id": "test-provider",
            "name": "Test Provider",
            "version": "1.0.0",
        },
        "spec": {
            "retrieval": {
                "type": "http-api",
                "endpoint": "https://test.example.com/api",
                "timeout_ms": 5000,
                "auth": {
                    "type": "bearer-token",
                    "token_env_var": "RAG_TEST_TOKEN",
                },
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "results": {"type": "array"},
                    },
                },
            },
            "classification": {
                "data_type": "INTERNAL",
            },
        },
    }


class TestSchemaValidation:
    """Test JSON Schema validation."""

    def test_valid_manifest(self, validator, valid_manifest):
        """Valid manifest passes validation."""
        result = validator.validate_dict(valid_manifest)
        assert result.valid is True
        assert result.errors is None

    def test_missing_api_version(self, validator, valid_manifest):
        """Missing api_version fails."""
        del valid_manifest["api_version"]
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False
        assert any("api_version" in str(e) for e in (result.errors or []))

    def test_invalid_api_version(self, validator, valid_manifest):
        """Invalid api_version fails."""
        valid_manifest["api_version"] = "rag.corvin.io/v2"
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False

    def test_missing_kind(self, validator, valid_manifest):
        """Missing kind fails."""
        del valid_manifest["kind"]
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False

    def test_missing_metadata(self, validator, valid_manifest):
        """Missing metadata fails."""
        del valid_manifest["metadata"]
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False

    def test_missing_spec(self, validator, valid_manifest):
        """Missing spec fails."""
        del valid_manifest["spec"]
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False

    def test_invalid_provider_id(self, validator, valid_manifest):
        """Invalid provider ID fails."""
        valid_manifest["metadata"]["id"] = "Invalid-ID!"
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False

    def test_provider_id_too_short(self, validator, valid_manifest):
        """Provider ID < 3 chars fails."""
        valid_manifest["metadata"]["id"] = "ab"
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False


class TestComplianceChecks:
    """Test compliance validation."""

    def test_hardcoded_secret_detected(self, validator, valid_manifest):
        """Hardcoded API key detected."""
        valid_manifest["spec"]["some_field"] = "api_key=secret123"
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False
        assert any("secret" in str(e).lower() for e in (result.errors or []))

    def test_auth_token_env_var_required(self, validator, valid_manifest):
        """Auth requires token_env_var."""
        del valid_manifest["spec"]["retrieval"]["auth"]["token_env_var"]
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False

    def test_invalid_token_env_var_format(self, validator, valid_manifest):
        """Invalid env var format fails."""
        valid_manifest["spec"]["retrieval"]["auth"]["token_env_var"] = "invalid-var-name"
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False

    def test_zone_gate_requires_regions(self, validator, valid_manifest):
        """Zone gate without regions fails."""
        valid_manifest["spec"]["compliance_zone"] = {"required": True}
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False
        assert any("allowed_regions" in str(e) for e in (result.errors or []))

    def test_zone_gate_valid(self, validator, valid_manifest):
        """Valid zone gate passes."""
        valid_manifest["spec"]["compliance_zone"] = {
            "required": True,
            "allowed_regions": ["EU", "DE"],
        }
        result = validator.validate_dict(valid_manifest)
        assert result.valid is True

    def test_high_classification_requires_approval_warning(self, validator, valid_manifest):
        """CONFIDENTIAL without approval triggers warning."""
        valid_manifest["spec"]["classification"]["data_type"] = "CONFIDENTIAL"
        result = validator.validate_dict(valid_manifest)
        assert result.valid is True
        assert result.warnings is not None
        assert any("requires_approval" in str(w) for w in result.warnings)

    def test_no_erasure_handler_warning(self, validator, valid_manifest):
        """Missing erasure handler triggers warning."""
        result = validator.validate_dict(valid_manifest)
        assert result.valid is True
        assert result.warnings is not None
        assert any("erasure" in str(w).lower() for w in result.warnings)

    def test_erasure_handler_suppresses_warning(self, validator, valid_manifest):
        """Erasure handler defined suppresses warning."""
        valid_manifest["spec"]["erasure_handler"] = {
            "type": "http-api",
            "endpoint": "https://test.example.com/delete",
            "request_schema": {
                "type": "object",
                "properties": {"subject_id": {"type": "string"}},
            },
        }
        result = validator.validate_dict(valid_manifest)
        assert result.valid is True
        assert not any("erasure" in str(w).lower() for w in (result.warnings or []))


class TestResponseSchema:
    """Test response schema validation."""

    def test_missing_response_schema(self, validator, valid_manifest):
        """Missing response_schema fails."""
        del valid_manifest["spec"]["retrieval"]["response_schema"]
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False
        assert any("response_schema" in str(e) for e in (result.errors or []))

    def test_response_schema_without_type(self, validator, valid_manifest):
        """Response schema without type fails."""
        valid_manifest["spec"]["retrieval"]["response_schema"] = {
            "properties": {"results": {"type": "array"}},
        }
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False


class TestProviderIdExtraction:
    """Test provider ID extraction."""

    def test_provider_id_extracted(self, validator, valid_manifest):
        """Provider ID extracted correctly."""
        result = validator.validate_dict(valid_manifest)
        assert result.provider_id == "test-provider"

    def test_provider_id_missing(self, validator, valid_manifest):
        """Provider ID can be missing (should fail schema)."""
        del valid_manifest["metadata"]["id"]
        result = validator.validate_dict(valid_manifest)
        assert result.valid is False


class TestFileValidation:
    """Test file-based validation."""

    def test_validate_yaml_file(self, validator, tmp_path):
        """Validate YAML file."""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(
            """
api_version: rag.corvin.io/v1
kind: RAGProvider
metadata:
  id: test-provider
  name: Test
  version: 1.0.0
spec:
  retrieval:
    type: http-api
    endpoint: https://test.example.com
    timeout_ms: 5000
    auth:
      type: bearer-token
      token_env_var: RAG_TEST_TOKEN
    response_schema:
      type: object
  classification:
    data_type: INTERNAL
"""
        )

        result = validator.validate_file(yaml_file)
        assert result.valid is True

    def test_validate_json_file(self, validator, tmp_path, valid_manifest):
        """Validate JSON file."""
        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps(valid_manifest))

        result = validator.validate_file(json_file)
        assert result.valid is True

    def test_file_not_found(self, validator):
        """Non-existent file fails."""
        result = validator.validate_file("/nonexistent/file.yaml")
        assert result.valid is False
        assert any("not found" in str(e).lower() for e in (result.errors or []))

    def test_invalid_file_format(self, validator, tmp_path):
        """Unsupported file format fails."""
        bad_file = tmp_path / "test.xml"
        bad_file.write_text("<xml/>")

        result = validator.validate_file(bad_file)
        assert result.valid is False
