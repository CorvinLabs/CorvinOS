"""Stub implementations for Phase A (k=3) — ready for full implementation."""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import json


@dataclass
class SkillManifest:
    """Stub: Skill manifest (loaded from SKILL-bundle)."""
    skill_id: str
    version: str
    input_schema: Dict[str, Any]  # JSON Schema
    output_schema: Dict[str, Any]
    supported_engines: list  # [WorkerEngine, ...]
    boot_layer: str  # core, compliance, bundled, installed


class SkillManifestLoader:
    """Stub: Load Skill manifests from registry."""

    async def load_manifest(self, skill_id: str, version: str) -> SkillManifest:
        """Load manifest for skill_id@version (cached)."""
        # TODO: Load from ~/.corvin/skills/registry.yaml or remote marketplace
        # For now, return stub manifest
        return SkillManifest(
            skill_id=skill_id,
            version=version,
            input_schema={
                "type": "object",
                "properties": {
                    "task_shape": {"type": "string"},
                    "context_size": {"type": "integer"},
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
            supported_engines=["claude_code", "hermes", "copilot", "opencode"],
            boot_layer="bundled",
        )


class AuditBackend:
    """Stub: Write events to audit chain (immutable, hash-chained)."""

    async def write_event(self, event: Dict[str, Any]) -> str:
        """Write event to audit backend. Return event_id."""
        # TODO: Call core/compliance/audit_chain/unified_chain.py
        # For now, return stub event_id
        return f"audit_event_{event.get('request_id', 'unknown')}"


# Stub skill logic (Phase A placeholder)
async def stub_skill_logic(skill_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Placeholder: actual Skill logic (SKILL.md execution)."""
    if skill_id == "os.delegation_router":
        return {
            "decision": "native",
            "confidence": 0.68,
            "reasoning": "Task is small_code; native is fastest",
        }
    elif skill_id == "os.context_adapter":
        return {
            "adapted_context": {"reduced": True, "size_mb": 2.5},
        }
    else:
        return {"output": "placeholder"}
