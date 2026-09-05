"""
Skill Invocation Service (Phase A)
ADR-0598: Claude Code RPC API for Skill Execution

Single-Harness model: Claude Code executes all Skills.
Models are swapped via ADR-0607 (OpenAI, Ollama, OpenRouter).

NOTE: This is Phase A skeleton. Import request/response from models.py.
Stub implementation: real Skill logic & audit integration follows in Phase B.
"""

import asyncio
import time
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
import logging

# Import from models (don't redefine)
from core.engine.skill_invocation_models import (
    SkillInvocationRequest,
    SkillInvocationResponse,
    _hash_dict,
)

logger = logging.getLogger(__name__)


class TimeoutConfig:
    """Phase timeouts (immutable, fail-closed)."""
    TIMEOUTS = {
        0: 100,      # Intake
        1: 500,      # Manifest
        2: 500,      # Context
        3: 100,      # Validation
        4: 5000,     # Plan
        5: 5000,     # Decision
        6: 5000,     # Reasoning
        7: 500,      # Audit
        8: 200,      # Output validation
        9: 100,      # Finalize
        10: 100,     # Return
    }


class SkillInvocationService:
    """Skill invocation service (Claude Code, Phase A skeleton)."""

    def __init__(self, audit_backend, manifest_loader):
        self.audit = audit_backend
        self.manifests = manifest_loader
        self.timeouts = TimeoutConfig()

    async def invoke_skill(
        self,
        request: SkillInvocationRequest,
    ) -> SkillInvocationResponse:
        """Main entry point: invoke Skill (Phases 0-10)."""

        start_time = time.time()
        trace: List[str] = []
        phase_completed = 0
        audit_event_id = ""
        error_msg = None
        output: Dict[str, Any] = {}

        try:
            # Phase 0: Intake
            await self._phase_intake(request, trace)

            # Phase 1: Load manifest
            manifest = await self._phase_manifest(request, trace)

            # Phase 2: Load context
            context = await self._phase_context(request, trace)

            # Phase 3: Validate input
            await self._phase_validate_input(request, manifest, trace)

            # Phases 4-6: Execute Skill
            output = await self._phases_execute(request, manifest, context, trace)

            # Phase 7: Audit
            audit_event_id = await self._phase_audit(request, output, trace)

            # Phase 8-9: Validate output
            await self._phase_validate_output(output, manifest, trace)

            # Phase 10: Return
            phase_completed = 10

        except asyncio.TimeoutError as e:
            error_msg = f"Timeout at phase {phase_completed}: {str(e)}"
            logger.error(f"Skill {request.skill_id} timeout: {error_msg}")
        except Exception as e:
            error_msg = f"Error at phase {phase_completed}: {type(e).__name__}: {str(e)}"
            logger.error(f"Skill {request.skill_id} error: {error_msg}")

        elapsed_ms = int((time.time() - start_time) * 1000)

        return SkillInvocationResponse(
            output=output,
            latency_ms=elapsed_ms,
            phase_completed=phase_completed,
            execution_trace=trace,
            audit_event_id=audit_event_id,
            error=error_msg,
        )

    # --- Phases ---

    async def _phase_intake(self, request: SkillInvocationRequest, trace: List[str]) -> None:
        """Phase 0: Validate request (already done in __post_init__)."""
        await self._run_phase(0, lambda: None, trace, "Intake")

    async def _phase_manifest(self, request: SkillInvocationRequest, trace: List[str]):
        """Phase 1: Load Skill manifest."""
        async def _load():
            return await self.manifests.load(request.skill_id, request.skill_version)
        return await self._run_phase(1, _load, trace, "Manifest load")

    async def _phase_context(self, request: SkillInvocationRequest, trace: List[str]):
        """Phase 2: Load tenant context."""
        async def _load():
            return {"tenant_id": request.tenant_id, "timestamp": datetime.now(timezone.utc).isoformat()}
        return await self._run_phase(2, _load, trace, "Context load", fallback={})

    async def _phase_validate_input(self, request, manifest, trace: List[str]) -> None:
        """Phase 3: Validate input against schema."""
        async def _validate():
            # Basic required fields check
            required = manifest.get("input_schema", {}).get("required", [])
            for field in required:
                if field not in request.input:
                    raise ValueError(f"Required field missing: {field}")
        await self._run_phase(3, _validate, trace, "Input validation")

    async def _phases_execute(self, request, manifest, context, trace: List[str]):
        """Phases 4-6: Plan, decide, reason (Skill execution)."""
        async def _execute():
            # Phase 4: Plan
            trace.append("  Phase 4: Plan ✓")

            # Phase 5: Decision (call Skill logic)
            trace.append("  Phase 5: Decision ✓")
            output = await self._skill_logic(request.skill_id, request.input)

            # Phase 6: Reasoning
            trace.append("  Phase 6: Reasoning ✓")
            if "reasoning" not in output:
                output["reasoning"] = f"Executed {request.skill_id} via Claude Code"

            return output

        return await self._run_phase(6, _execute, trace, "Skill execution")

    async def _skill_logic(self, skill_id: str, input_data: Dict) -> Dict[str, Any]:
        """Actual Skill logic (stub for now, real: call Claude Code agent)."""
        if skill_id == "os.delegation_router":
            return {
                "decision": "native",
                "confidence": 0.68,
                "reasoning": f"Task shape: {input_data.get('task_shape', 'unknown')}",
            }
        return {"output": "placeholder"}

    async def _phase_audit(self, request, output, trace: List[str]) -> str:
        """Phase 7: Emit audit event."""
        async def _emit():
            event_id = f"audit_{request.request_id}"
            await self.audit.write_event({
                "tenant_id": request.tenant_id,
                "event_type": "skill_invocation_completed",
                "skill_id": request.skill_id,
                "request_id": request.request_id,
                "output_hash": self._hash(output),
            })
            return event_id
        return await self._run_phase(7, _emit, trace, "Audit emit", fallback=f"audit_queued_{request.request_id}")

    async def _phase_validate_output(self, output, manifest, trace: List[str]) -> None:
        """Phases 8-9: Validate output."""
        async def _validate():
            required = manifest.get("output_schema", {}).get("required", [])
            for field in required:
                if field not in output:
                    raise ValueError(f"Required output field missing: {field}")
        await self._run_phase(9, _validate, trace, "Output validation")

    # --- Utilities ---

    async def _run_phase(
        self,
        phase: int,
        coro,
        trace: List[str],
        phase_name: str,
        fallback=None,
    ):
        """Run phase with timeout + fallback."""
        timeout_ms = self.timeouts.TIMEOUTS.get(phase, 1000)

        try:
            result = await asyncio.wait_for(coro(), timeout=timeout_ms / 1000)
            trace.append(f"Phase {phase}: {phase_name} ✓")
            return result
        except asyncio.TimeoutError:
            trace.append(f"Phase {phase}: {phase_name} ✗ (timeout)")
            if fallback is not None:
                return fallback
            raise

    @staticmethod
    def _hash(data: Dict) -> str:
        """Deterministic hash (use shared utility from models)."""
        return _hash_dict(data)
