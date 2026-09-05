"""
Discord UI-Layer Adapter (ADR-0608)
Stateless Discord bot integration.
"""

from .ui_adapter import UILayer, UIRequest, UIResponse
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


class DiscordUILayer(UILayer):
    """Discord bot adapter (formerly Hermes)."""

    def __init__(self):
        super().__init__("discord")

    async def parse_input(self, raw_input: Any) -> UIRequest:
        """Parse Discord message into UIRequest."""
        # raw_input = dict (parsed from discord.Message by calling function)
        # Never call .get() on discord.Message directly—handle that in caller
        if isinstance(raw_input, dict):
            tenant_id = raw_input.get("guild_id", "_default")
            user_id = raw_input.get("user_id")
            channel_id = raw_input.get("channel_id")
        else:
            # Assume caller passes properly parsed dict, not raw discord.Message
            raise TypeError(f"Expected dict, got {type(raw_input)}")

        # Parse skill command from message content
        # Example: "/skill os.delegation_router task_shape=small"
        content = raw_input.get("content", "")
        parts = content.split()

        skill_id = parts[0].lstrip("/") if parts else "unknown"
        input_data = {}
        for part in parts[1:]:
            if "=" in part:
                key, val = part.split("=", 1)
                input_data[key] = val

        return UIRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            skill_id=skill_id,
            input_data=input_data,
            channel_id=channel_id,
        )

    async def send_response(self, request: UIRequest, response: UIResponse) -> None:
        """Send response back to Discord (stub: TODO wire real Discord API)."""
        if not request.channel_id:
            logger.warning("No channel_id for Discord response")
            return

        message = f"✓ {response.content}" if response.is_success else f"✗ Error: {response.error}"
        logger.info(f"Discord response to {request.channel_id}: {message}")

        # Phase C TODO: integrate discord.py client to post message
        # For now: logged only (caller must implement actual Discord posting)
