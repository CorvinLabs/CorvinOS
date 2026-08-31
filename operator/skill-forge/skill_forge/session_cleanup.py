"""SkillForge session cleanup — purge skills when resetting a session.

Part of Layer 8 Session Lifecycle (layer-voice-ldd.md).
Called by adapter.reset_session when cleaning up a chat.
"""
from __future__ import annotations

import shutil
from pathlib import Path


def purge_session_skills(channel: str, chat_key: str) -> None:
    """Delete the skill-forge workspace for a specific session.

    Called during /new /clear /reset to clean up all session-scope skills.
    Does not raise if the directory doesn't exist (idempotent).

    Args:
        channel: Bridge channel ID (e.g., "discord", "whatsapp")
        chat_key: Chat identifier within the channel
    """
    from forge.paths import corvin_home  # Import lazily to avoid circular deps

    session_name = f"{channel}:{chat_key}"
    skill_forge_dir = corvin_home() / "sessions" / session_name / "skill-forge"

    if skill_forge_dir.exists():
        shutil.rmtree(skill_forge_dir, ignore_errors=True)
