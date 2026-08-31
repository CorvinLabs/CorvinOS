"""ADR-0087 M1: Per-engine context continuation logic.

Given a checkpoint, format the next prompt/system-prompt for each engine.
Enables seamless context transfer across engine boundaries.

Claude Code: uses native --resume if available (checkpoint is fallback)
Codex/OpenCode/Hermes: prepend checkpoint summary to system prompt
Copilot: single-turn limitation (error message)
"""

from __future__ import annotations

from typing import Any, Optional


def create_continuation_prompt(
    engine_id: str,
    checkpoint: dict[str, Any],
    new_user_input: str,
) -> str:
    """Format next prompt based on engine and checkpoint.

    Args:
        engine_id: which engine will spawn next
        checkpoint: loaded context_checkpoint dict
        new_user_input: fresh user message for this turn

    Returns:
        Formatted prompt; for Claude Code, same as input (uses --resume flag).
        For others, includes prepended context summary.

    Raises:
        ValueError: if engine_id is unknown
    """
    if engine_id == "claude_code":
        # Claude Code uses native --resume; checkpoint is not prepended to prompt
        return new_user_input

    elif engine_id in ("codex_cli", "opencode_cli"):
        # Codex and OpenCode: prepend checkpoint summary
        summary = _checkpoint_summary(checkpoint)
        return f"{summary}\n\n{new_user_input}"

    elif engine_id == "hermes_engine":
        # Hermes: prepend checkpoint summary (same as Codex for now; M2 will add sidecar)
        summary = _checkpoint_summary(checkpoint)
        return f"{summary}\n\n{new_user_input}"

    elif engine_id == "copilot_cli":
        # Copilot: single-turn limitation; error instead of prepending
        # Caller should check for this and reject multi-turn requests gracefully
        raise NotImplementedError(
            "Copilot single-turn limitation: multi-turn resumption not supported. "
            "Use task_wrapper for sequential execution."
        )

    else:
        raise ValueError(f"Unknown engine_id: {engine_id}")


def _checkpoint_summary(checkpoint: dict[str, Any]) -> str:
    """Format checkpoint as a context block to prepend to next prompt."""
    lines = [
        "## Prior Context (restored from checkpoint)",
        "",
        f"**System Prompt:** {checkpoint.get('system_prompt_summary', '(none)')}",
        f"**Last Message:** {checkpoint.get('last_message_summary', '(none)')}",
        f"**Tools Used:** {checkpoint.get('tool_results_digest', '(none)')}",
        f"**Conversation Length:** ~{checkpoint.get('conversation_length_tokens', 0)} tokens",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)
