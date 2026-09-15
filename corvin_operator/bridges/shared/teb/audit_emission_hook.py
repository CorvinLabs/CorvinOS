"""Audit Emission Pre-Hook — ADR-0087 M4 + L16.

Emits audit events BEFORE tool execution (L16 audit-first invariant).
This hook runs as the FIRST pre-hook, before path-gate check.

Audit event schema (metadata-only, compliant with L16):
  {
    "event_type": "tool_call.requested",
    "engine_id": "<engine>",
    "tool_name": "<tool>",
    "chat_key": "<bridge:chat>",
    "timestamp": <epoch_s>
  }

The hook does NOT emit the actual tool args (no prompts, secrets, paths).
All sensitive fields are omitted (fail-closed observability).

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import HookContext


def audit_emission_pre_hook(ctx: HookContext) -> None:
    """Emit audit event BEFORE tool execution.

    This hook emits metadata-only audit events for all tool calls,
    regardless of engine type. It runs first in the pre-hook chain,
    before path-gate or other validations.

    The hook never blocks execution (denial=False).
    """
    # Metadata-only audit event (no args, paths, or secrets)
    audit_event = {
        "event_type": "tool_call.requested",
        "engine_id": ctx.engine_id,
        "tool_name": ctx.tool_name,
        "chat_key": ctx.chat_key,
        "timestamp": time.time(),
    }

    # Store event reference on context for post-hook retrieval
    # (In a real implementation, this would emit to the L16 audit chain)
    if not hasattr(ctx, "_audit_events"):
        ctx._audit_events = []  # type: ignore
    ctx._audit_events.append(audit_event)  # type: ignore
