"""Custom Provider Setup — Web-integrated provider creation.

Endpoints:
- POST /custom-provider/test-api — Test endpoint connectivity
- POST /custom-provider/validate — Validate form input
- POST /custom-provider/create — Generate + register provider
"""
from __future__ import annotations

import asyncio
import logging
import sys
import httpx
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_csrf
from ..audit import _emit

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))
from forge import paths as _forge_paths  # noqa: E402

_OPERATOR = _REPO / "operator"
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))
try:
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
    from license.limits import LicenseLimitError as _LicLimitError  # type: ignore[import]
except ImportError:
    try:
        from license.limits import FREE_TIER as _FREE_TIER, LicenseLimitError as _LicLimitError  # type: ignore[import]
    except ImportError:
        _FREE_TIER: dict = {}
        class _LicLimitError(Exception): pass  # type: ignore[assignment,misc]
    _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-provider", tags=["console-custom-provider"])


# ── Request models ────────────────────────────────────────

class TestApiRequest(BaseModel):
    endpoint: str = Field(..., max_length=2048)
    method: str = Field(default="POST", max_length=10)
    auth_type: str = Field(default="", max_length=64)
    auth_token: str = Field(default="", max_length=512)
    test_query: str = Field(default="test", max_length=1024)
    timeout_ms: int = Field(default=5000, ge=100, le=30000)
    model_config = {"extra": "forbid"}


class ValidateProviderRequest(BaseModel):
    provider_id: str = Field(default="", max_length=64)
    query_format_sample: str = Field(default="", max_length=4096)
    content_path: str = Field(default="", max_length=256)
    score_path: str = Field(default="", max_length=256)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    model_config = {"extra": "forbid"}


class CreateProviderRequest(BaseModel):
    provider_id: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=512)
    author: str = Field(default="", max_length=128)
    version: str = Field(default="1.0", max_length=32)
    endpoint: str = Field(default="", max_length=2048)
    method: str = Field(default="POST", max_length=10)
    timeout_ms: int = Field(default=5000, ge=100, le=30000)
    auth_type: str = Field(default="bearer-token", max_length=64)
    auth_token_env_var: str = Field(default="", max_length=128)
    query_format_sample: str = Field(default="", max_length=4096)
    content_path: str = Field(default="", max_length=256)
    score_path: str = Field(default="", max_length=256)
    metadata_path: str = Field(default="", max_length=256)
    source_url_path: str = Field(default="", max_length=256)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    data_classification: str = Field(default="INTERNAL", max_length=32)
    compliance_zone: str = Field(default="EU", max_length=32)
    circuit_breaker_threshold: int = Field(default=5, ge=0, le=100)
    circuit_breaker_timeout_seconds: int = Field(default=60, ge=0, le=3600)
    max_retries: int = Field(default=3, ge=0, le=10)
    model_config = {"extra": "forbid"}



# ── API Connectivity Testing ───────────────────────────────

@router.post("/test-api")
async def test_api_connectivity(
    req: TestApiRequest,
    _session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Test API endpoint connectivity and response format.

    Expected request body:
    {
        "endpoint": "https://api.example.com/search",
        "method": "POST",
        "auth_type": "bearer-token",
        "auth_token": "token-value",
        "test_query": "test",
        "timeout_ms": 5000
    }

    Returns:
    {
        "status": "connected",
        "http_status": 200,
        "response_preview": {...},  # First 500 chars of response
        "fields_detected": ["results", "items", ...],
        "error": null
    }
    """
    try:
        endpoint = req.endpoint
        method = req.method.upper()
        auth_type = req.auth_type
        auth_token = req.auth_token
        test_query = req.test_query
        timeout_ms = req.timeout_ms

        if not endpoint:
            return {"status": "failed", "error": "No endpoint provided"}

        # Build headers
        headers = {"Content-Type": "application/json"}
        if auth_type == "bearer-token":
            headers["Authorization"] = f"Bearer {auth_token}"
        elif auth_type == "api-key":
            headers["X-API-Key"] = auth_token
        elif auth_type == "basic":
            import base64
            creds = base64.b64encode(auth_token.encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

        # Build request body
        if method == "POST":
            body = {
                "query": test_query,
                "limit": 5,
            }
        else:
            body = None

        # Make request
        MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10 MB limit
        async with httpx.AsyncClient(timeout=timeout_ms / 1000.0) as client:
            if method == "POST":
                response = await client.post(endpoint, json=body, headers=headers)
            else:
                response = await client.get(endpoint, headers=headers)

        # Check response size (DoS mitigation)
        if len(response.content) > MAX_RESPONSE_SIZE:
            return {
                "status": "failed",
                "error": f"Response too large ({len(response.content)} bytes, limit is {MAX_RESPONSE_SIZE})",
            }

        # Parse response
        try:
            response_data = response.json()
        except Exception:
            response_data = {"raw_text": response.text[:500]}

        # Detect top-level fields
        fields = []
        if isinstance(response_data, dict):
            fields = list(response_data.keys())[:10]

        return {
            "status": "connected",
            "http_status": response.status_code,
            "response_preview": str(response_data)[:500],
            "fields_detected": fields,
            "error": None,
        }

    except httpx.TimeoutException:
        return {
            "status": "failed",
            "error": f"Timeout after {timeout_ms}ms",
        }
    except httpx.ConnectError:
        return {
            "status": "failed",
            "error": "connection failed",
        }
    except Exception as e:
        logger.error("API test failed: %s", e)
        return {
            "status": "failed",
            "error": "internal error",
        }


# ── Form Validation ────────────────────────────────────────

@router.post("/validate")
async def validate_form(
    req: ValidateProviderRequest,
    _session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Validate all form steps before creating provider."""
    try:
        from bridges.shared.rag_manifest_generator import (
            RAGManifestGenerator,
            BasicInfoInput,
        )

        errors = {}

        # Validate step 1: Basic info
        basic_id = req.provider_id.strip()
        if not basic_id or len(basic_id) < 2:
            errors["provider_id"] = "Provider ID must be 2+ characters"
        if not basic_id.replace("-", "").replace("_", "").isalnum():
            errors["provider_id"] = "Provider ID must be alphanumeric (- and _ allowed)"

        # Validate step 2: API config
        query_fmt = req.query_format_sample
        valid_fmt, fmt_err = RAGManifestGenerator.validate_query_format_sample(query_fmt)
        if not valid_fmt:
            errors["query_format"] = fmt_err

        # Validate step 3: JSONPath
        content_path = req.content_path
        score_path = req.score_path
        if not content_path:
            errors["content_path"] = "Content field path required"
        if not score_path:
            errors["score_path"] = "Score field path required"

        # Validate step 4: Compliance
        capabilities = req.capabilities
        if not capabilities or len(capabilities) == 0:
            errors["capabilities"] = "Select at least one capability"

        if errors:
            return {
                "status": "invalid",
                "errors": errors,
            }

        return {
            "status": "valid",
            "errors": {},
        }

    except Exception as e:
        logger.error("Validation failed: %s", e)
        return {
            "status": "failed",
            "error": "internal error",
        }


# ── Provider Creation ──────────────────────────────────────

@router.post("/create")
async def create_custom_provider(
    req: CreateProviderRequest,
    _session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Create and register a custom provider.

    Generates manifest, validates it, saves locally, registers with RAG system.
    """
    # ADR-0094 / ADR-0144 CON-01: enforce rag_providers_max before any manifest
    # work, via the SHARED single-source gate also used by rag_hub.import_provider
    # so the two write paths into tenant_global_dir(tid)/rag cannot drift.
    _tid = _session.tenant_id
    from ._rag_license_gate import enforce_rag_providers_max  # noqa: PLC0415

    enforce_rag_providers_max(
        _tid,
        _session.sid_fingerprint,
        requested_id=req.provider_id or "pending",
        audit_action="rag.provider_create",
    )

    try:
        from bridges.shared.rag_manifest_generator import (
            RAGManifestGenerator,
            BasicInfoInput,
            APIConfigInput,
            ResponseMappingInput,
            ComplianceInput,
        )
        from bridges.shared.rag_import_export import RAGProviderImportExport

        # Extract and build input objects
        basic = BasicInfoInput(
            provider_id=req.provider_id.strip(),
            name=req.name.strip(),
            description=req.description.strip(),
            author=req.author.strip(),
            version=req.version.strip(),
        )

        api_config = APIConfigInput(
            endpoint=req.endpoint.strip(),
            method=req.method.upper(),
            timeout_ms=req.timeout_ms,
            auth_type=req.auth_type,
            auth_token_env_var=req.auth_token_env_var.strip(),
            query_format_sample=req.query_format_sample.strip(),
        )

        response_mapping = ResponseMappingInput(
            content_path=req.content_path.strip(),
            score_path=req.score_path.strip(),
            metadata_path=req.metadata_path.strip(),
            source_url_path=req.source_url_path.strip() or None,
        )

        compliance = ComplianceInput(
            capabilities=req.capabilities,
            data_classification=req.data_classification,
            compliance_zone=req.compliance_zone,
            circuit_breaker_threshold=req.circuit_breaker_threshold,
            circuit_breaker_timeout_seconds=req.circuit_breaker_timeout_seconds,
            max_retries=req.max_retries,
        )

        # Generate manifest
        success, manifest_yaml, error = RAGManifestGenerator.generate(
            basic, api_config, response_mapping, compliance
        )

        if not success:
            tenant_id = _session.tenant_id
            _emit(
                event_type="rag.provider_failed",
                details={
                    "action": "create",
                    "target_kind": "provider",
                    "target_id": req.provider_id or "",
                    "status": "failed",
                    "reason": "manifest_generation_error",
                },
                tenant_id=tenant_id,
            )
            return {
                "status": "failed",
                "error": "manifest generation failed",
            }

        # Compute hash for Hub dedup
        manifest_hash = RAGProviderImportExport.compute_manifest_hash(manifest_yaml)

        # Audit-first: write event BEFORE import (audit-first invariant per L16)
        tenant_id = _session.tenant_id
        _emit(
            event_type="rag.provider_created",
            details={
                "action": "create",
                "target_kind": "provider",
                "target_id": basic.provider_id,
                "status": "success",
                "reason": None,
            },
            tenant_id=tenant_id,
        )

        # Import (save locally)
        registry_dir = _forge_paths.tenant_global_dir(tenant_id) / "rag"

        import_success, imported_id, import_error = RAGProviderImportExport.import_manifest(
            manifest_yaml,
            registry_dir,
            basic.provider_id,
        )

        if not import_success:
            _emit(
                event_type="rag.provider_failed",
                details={
                    "action": "create",
                    "target_kind": "provider",
                    "target_id": basic.provider_id,
                    "status": "failed",
                    "reason": "import_error",
                },
                tenant_id=tenant_id,
            )
            return {
                "status": "failed",
                "error": "registration failed",
            }

        logger.info(f"✅ Custom provider created: {imported_id}")

        return {
            "status": "created",
            "provider_id": imported_id,
            "manifest_hash": manifest_hash,
            "file_path": str(registry_dir / f"{imported_id}.yaml"),
            "next_step": "Verify with: corvin-rag health <provider_id>",
        }

    except Exception as e:
        logger.error("Failed to create provider: %s", e)
        tenant_id = _session.tenant_id
        provider_id = req.provider_id or "unknown"
        try:
            _emit(
                event_type="rag.provider_failed",
                details={
                    "action": "create",
                    "target_kind": "provider",
                    "target_id": provider_id,
                    "status": "failed",
                    "reason": "unexpected_error",
                },
                tenant_id=tenant_id,
            )
        except Exception as audit_err:
            logger.error(f"Failed to emit audit event: {audit_err}")
        return {
            "status": "failed",
            "error": "internal error",
        }
