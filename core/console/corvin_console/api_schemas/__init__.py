"""Unified Forge API Schemas — Tools, Skills, OS-Skills management.

This package defines the schema contracts for the Forge subsystem:
  - forge_unified.py: Complete Pydantic models for all endpoints

The schemas are frontend-safe:
  - All PII is scrubbed before transmission (GDPR Art. 32)
  - Fail-closed validation (invalid shapes are rejected)
  - Audit trail integration (no endpoint without audit emission)
  - Tenant isolation (ADR-0007)
  - Learning integration (ADR-0314)

Use these schemas to:
  1. Type-hint FastAPI route handlers (core/console/corvin_console/routes/forge_*.py)
  2. Validate request/response bodies
  3. Generate OpenAPI documentation
  4. Communicate with frontend (web-next/src/types/forge.ts)

Example:
  from core.console.corvin_console.api_schemas.forge_unified import (
      ToolListResponse, SkillFeedbackRequest, OSSkillConfigResponse
  )

  @router.get("/tools", response_model=ToolListResponse)
  def list_tools() -> ToolListResponse:
      ...
"""

__all__ = [
    "forge_unified",
]
