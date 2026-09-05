"""
CLI UI-Layer Adapter (ADR-0608)
Stateless local CLI integration.
"""

from .ui_adapter import UILayer, UIRequest, UIResponse
from typing import Any, Dict, Optional
import logging
import sys

logger = logging.getLogger(__name__)


class CLIUILayer(UILayer):
    """CLI adapter (formerly OpenCode)."""

    def __init__(self, tenant_id: str = "_default"):
        super().__init__("cli")
        self.tenant_id = tenant_id

    async def parse_input(self, raw_input: Any) -> UIRequest:
        """Parse CLI arguments into UIRequest."""
        # raw_input = sys.argv[1:] or click.Context
        if isinstance(raw_input, list):
            args = raw_input
        else:
            args = raw_input.get("args", [])

        if not args:
            raise ValueError("Usage: corvin-cli <skill_id> [key=value ...]")

        skill_id = args[0]
        input_data = {}

        for arg in args[1:]:
            if "=" in arg:
                key, val = arg.split("=", 1)
                input_data[key] = val

        return UIRequest(
            tenant_id=self.tenant_id,
            user_id=None,  # CLI is local, no user context
            skill_id=skill_id,
            input_data=input_data,
        )

    async def send_response(self, request: UIRequest, response: UIResponse) -> None:
        """Print response to stdout (don't sys.exit, raise exception instead)."""
        if response.is_success:
            print(f"✓ Success")
            print(f"Output: {response.content}")
            if response.metadata:
                print(f"Metadata: {response.metadata}")
        else:
            # Raise exception instead of sys.exit() to allow caller to handle
            print(f"✗ Error: {response.error}", file=sys.stderr)
            raise RuntimeError(response.error)
