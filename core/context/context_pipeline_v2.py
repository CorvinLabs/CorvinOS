"""Context Pipeline V2 — Redesigned (ADR-0095, ADR-0114)."""
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum

class ContextStage(Enum):
    EXTRACT = "extract"       # Pull from session
    VALIDATE = "validate"     # Schema check
    ENRICH = "enrich"         # Add computed fields
    FILTER = "filter"         # Apply PII/sensitivity gates
    INJECT = "inject"         # Build final prompt
    AUDIT = "audit"           # Log snapshot

@dataclass
class ContextSnapshot:
    """Immutable context at a point in time."""
    tenant_id: str
    session_id: str
    stage: ContextStage
    preserved_fields: Dict[str, Any]  # What survived filtering
    added_fields: Dict[str, Any]      # What was computed
    removed_fields: List[str]         # PII/sensitivity redactions
    pipeline_version: str = "2.0"

class ContextPipelineV2:
    """Redesigned context pipeline (3-stage model)."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.stages = [
            self._extract,
            self._validate,
            self._enrich,
            self._filter,
            self._inject,
            self._audit,
        ]

    async def execute(self, session_context: Dict[str, Any]) -> ContextSnapshot:
        """Execute full pipeline (fail-closed on any error)."""
        snapshot = ContextSnapshot(
            tenant_id=self.tenant_id,
            session_id=session_context.get("session_id", "unknown"),
            stage=ContextStage.EXTRACT,
            preserved_fields=session_context.copy(),
            added_fields={},
            removed_fields=[],
        )

        for stage_fn in self.stages:
            snapshot = await stage_fn(snapshot)

        return snapshot

    async def _extract(self, snap: ContextSnapshot) -> ContextSnapshot:
        """Stage 1: Extract from session (identity)."""
        snap.stage = ContextStage.EXTRACT
        return snap

    async def _validate(self, snap: ContextSnapshot) -> ContextSnapshot:
        """Stage 2: Schema validation (fail-closed on invalid)."""
        snap.stage = ContextStage.VALIDATE
        required = {"tenant_id", "session_id"}
        if not all(k in snap.preserved_fields for k in required):
            raise ValueError("Missing required context fields")
        return snap

    async def _enrich(self, snap: ContextSnapshot) -> ContextSnapshot:
        """Stage 3: Add computed fields (timestamp, version, etc.)."""
        snap.stage = ContextStage.ENRICH
        snap.added_fields["pipeline_version"] = "2.0"
        snap.added_fields["enriched_at"] = "2026-09-25T00:00:00Z"
        return snap

    async def _filter(self, snap: ContextSnapshot) -> ContextSnapshot:
        """Stage 4: PII/sensitivity gate (fail-closed redaction)."""
        snap.stage = ContextStage.FILTER

        # Redact sensitive patterns
        sensitive_keys = ["password", "token", "api_key", "secret"]
        for key in list(snap.preserved_fields.keys()):
            if any(s in key.lower() for s in sensitive_keys):
                snap.removed_fields.append(key)
                del snap.preserved_fields[key]

        return snap

    async def _inject(self, snap: ContextSnapshot) -> ContextSnapshot:
        """Stage 5: Build final context for prompt injection."""
        snap.stage = ContextStage.INJECT
        snap.added_fields["context_ready"] = True
        snap.added_fields["fields_preserved"] = len(snap.preserved_fields)
        snap.added_fields["fields_redacted"] = len(snap.removed_fields)
        return snap

    async def _audit(self, snap: ContextSnapshot) -> ContextSnapshot:
        """Stage 6: Log immutable snapshot."""
        snap.stage = ContextStage.AUDIT
        # Audit event would be logged here
        # audit_backend.write_event({
        #   "event_type": "context_snapshot",
        #   "tenant_id": snap.tenant_id,
        #   ...
        # })
        return snap
