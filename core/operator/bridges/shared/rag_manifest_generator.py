"""RAG Manifest Generator — Build valid manifests from form input.

Converts user input (API endpoint, auth, field mapping) into a valid
v1alpha1 RAG provider manifest. Validates schema before returning YAML.
"""
from __future__ import annotations

import yaml
from dataclasses import dataclass, asdict
from typing import Optional, Literal
from datetime import datetime, timezone

# Import the import_export validator
from .rag_import_export import RAGProviderImportExport


# ── Data Classes for Form Input ────────────────────────────────

@dataclass
class BasicInfoInput:
    """Step 1: Basic provider information."""
    provider_id: str
    name: str
    description: str
    author: str
    version: str


@dataclass
class APIConfigInput:
    """Step 2: API endpoint configuration."""
    endpoint: str
    method: Literal["GET", "POST"]
    timeout_ms: int
    auth_type: Literal["bearer-token", "api-key", "basic", "oauth2"]
    auth_token_env_var: str
    query_format_sample: str  # Template with {query} and {limit}


@dataclass
class ResponseMappingInput:
    """Step 3: JSON response field extraction."""
    content_path: str  # JSONPath, e.g., "results[].body"
    score_path: str    # JSONPath, e.g., "results[].score"
    metadata_path: str # JSONPath, e.g., "results[]"
    source_url_path: Optional[str] = None


@dataclass
class ComplianceInput:
    """Step 4: Compliance & capabilities."""
    capabilities: list[str]
    data_classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"]
    compliance_zone: Literal["EU", "US", "APAC", "HYBRID"]
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout_seconds: int = 60
    max_retries: int = 3


# ── Manifest Generator ─────────────────────────────────────────

class RAGManifestGenerator:
    """Generate valid RAG provider manifests from form input."""

    @staticmethod
    def generate(
        basic: BasicInfoInput,
        api_config: APIConfigInput,
        response_mapping: ResponseMappingInput,
        compliance: ComplianceInput,
    ) -> tuple[bool, str, Optional[str]]:
        """Generate a complete manifest YAML.

        Args:
            basic: Step 1 input
            api_config: Step 2 input
            response_mapping: Step 3 input
            compliance: Step 4 input

        Returns:
            (success, manifest_yaml, error_message)
        """
        try:
            # Build manifest structure
            manifest = {
                "apiVersion": "rag.corvin.io/v1alpha1",
                "kind": "RAGProvider",
                "metadata": {
                    "name": basic.provider_id,
                    "namespace": "user-created",
                    "description": basic.description,
                    "version": basic.version,
                    "author": basic.author,
                    "createdAt": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
                },
                "spec": {
                    "retrieval": {
                        "endpoint": api_config.endpoint,
                        "method": api_config.method,
                        "timeout_ms": api_config.timeout_ms,
                        "auth": {
                            "type": api_config.auth_type,
                            "token_env_var": api_config.auth_token_env_var,
                        },
                        "query_format": {
                            "type": "custom-http",
                            "sample": api_config.query_format_sample,
                        },
                    },
                    "response_format": {
                        "content_path": response_mapping.content_path,
                        "score_path": response_mapping.score_path,
                        "metadata_path": response_mapping.metadata_path,
                    },
                    "dataClassification": compliance.data_classification,
                    "complianceZone": compliance.compliance_zone,
                    "capabilities": compliance.capabilities,
                    "resilience": {
                        "circuit_breaker": {
                            "failure_threshold": compliance.circuit_breaker_threshold,
                            "timeout_seconds": compliance.circuit_breaker_timeout_seconds,
                            "half_open_requests": 1,
                        },
                        "retry_strategy": "exponential",
                        "max_retries": compliance.max_retries,
                        "backoff_ms": 100,
                    },
                    "quotas": {
                        "requests_per_second": 100,
                        "concurrent_requests": 10,
                        "daily_limit": 1000000,
                    },
                    "healthCheck": {
                        "endpoint": api_config.endpoint,
                        "interval_seconds": 60,
                        "timeout_seconds": 5,
                        "success_http_codes": [200, 201],
                    },
                },
            }

            # Add source URL if provided
            if response_mapping.source_url_path:
                manifest["spec"]["response_format"]["source_url_path"] = response_mapping.source_url_path

            # Convert to YAML
            manifest_yaml = yaml.dump(
                manifest,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

            # Validate the generated manifest
            valid, error = RAGProviderImportExport.validate_manifest(manifest_yaml)
            if not valid:
                return False, "", f"Generated manifest is invalid: {error}"

            return True, manifest_yaml, None

        except Exception as e:
            return False, "", f"Failed to generate manifest: {str(e)}"

    @staticmethod
    def validate_query_format_sample(sample: str) -> tuple[bool, Optional[str]]:
        """Validate query format sample contains required placeholders."""
        if "{query}" not in sample:
            return False, "Query format must contain {query} placeholder"
        if "{limit}" not in sample:
            return False, "Query format must contain {limit} placeholder"
        return True, None

    @staticmethod
    def validate_jsonpath(path: str) -> tuple[bool, Optional[str]]:
        """Basic JSONPath validation."""
        if not path or len(path) < 1:
            return False, "JSONPath cannot be empty"
        if path.startswith("$"):
            return True, None  # $.field syntax
        if "[" in path and "]" in path:
            return True, None  # results[] syntax
        if "." in path:
            return True, None  # field.subfield syntax
        return True, None  # Simple field name

    @staticmethod
    def manifest_to_dict(manifest_yaml: str) -> Optional[dict]:
        """Parse manifest YAML to dict."""
        try:
            return yaml.safe_load(manifest_yaml)
        except Exception:
            return None
