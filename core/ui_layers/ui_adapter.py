"""
UI-Layer Adapter (ADR-0608)
Stateless base for Discord, GitHub, CLI, API.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import logging
# Import at module level (not deferred to runtime)
from core.engine.skill_invocation_models import SkillInvocationRequest

logger = logging.getLogger(__name__)


@dataclass
class UIRequest:
    """Incoming request from UI layer."""
    tenant_id: str
    user_id: Optional[str]
    skill_id: str
    input_data: Dict[str, Any]
    channel_id: Optional[str] = None  # Discord channel, GitHub issue, etc.


@dataclass(frozen=True)
class UIResponse:
    """Response to send back to UI layer (immutable)."""
    content: str
    is_success: bool
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class UILayer(ABC):
    """Abstract base for UI adapters."""

    def __init__(self, layer_name: str):
        self.layer_name = layer_name

    @abstractmethod
    async def parse_input(self, raw_input: Any) -> UIRequest:
        """Parse raw input from UI (Discord message, GitHub issue, CLI args, etc.) into UIRequest."""
        pass

    @abstractmethod
    async def send_response(self, request: UIRequest, response: UIResponse) -> None:
        """Send response back to UI (Discord reply, GitHub comment, CLI stdout, etc.)."""
        pass

    async def invoke_skill(
        self,
        request: UIRequest,
        skill_service,
        router,
    ) -> UIResponse:
        """Generic Skill invocation from any UI layer."""
        try:
            # Call Skill service (SkillInvocationRequest imported at module level)
            skill_request = SkillInvocationRequest(
                tenant_id=request.tenant_id,
                skill_id=request.skill_id,
                skill_version="1.0",  # TODO: load from manifest
                input=request.input_data,
            )

            response = await skill_service.invoke_skill(skill_request)

            if response.is_success:
                return UIResponse(
                    content=str(response.output),
                    is_success=True,
                    metadata={"latency_ms": response.latency_ms},
                )
            else:
                return UIResponse(
                    content="",
                    is_success=False,
                    error=response.error,
                )

        except Exception as e:
            logger.error(f"Skill invocation failed: {e}")
            return UIResponse(
                content="",
                is_success=False,
                error=str(e),
            )
