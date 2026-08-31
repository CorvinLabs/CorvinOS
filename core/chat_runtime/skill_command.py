"""Chat Command Handler: /skill create <prompt>

Integrates skill generation into chat commands.
Detects "/skill create" trigger → invokes create_skill MCP tool.
"""

import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class SkillCommand:
    """Parsed skill command."""
    subcommand: str  # create | status | list
    prompt: Optional[str] = None
    run_id: Optional[str] = None


def parse_skill_command(text: str) -> Optional[SkillCommand]:
    """Parse /skill command from chat text.

    Examples:
      /skill create "validate JSON files"
      /skill status <run_id>
      /skill list
    """
    if not text.startswith("/skill"):
        return None

    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        return None

    subcommand = parts[1].lower()

    if subcommand == "create":
        if len(parts) < 3:
            raise ValueError("Usage: /skill create <prompt>")
        prompt = parts[2].strip('"\'')
        return SkillCommand(subcommand="create", prompt=prompt)

    elif subcommand == "status":
        if len(parts) < 3:
            raise ValueError("Usage: /skill status <run_id>")
        run_id = parts[2]
        return SkillCommand(subcommand="status", run_id=run_id)

    elif subcommand == "list":
        return SkillCommand(subcommand="list")

    else:
        raise ValueError(f"Unknown subcommand: {subcommand}")


async def handle_skill_command(command: SkillCommand) -> Dict[str, Any]:
    """Handle parsed skill command.

    Routes to appropriate handler based on subcommand.
    """
    from operator.mcp_servers.skill_creator_tool import SkillCreatorTool

    tool = SkillCreatorTool()

    if command.subcommand == "create":
        if not command.prompt:
            return {"error": "Prompt required for /skill create"}

        response = await tool.create_skill(command.prompt, async_mode=True)
        return {
            "type": "skill_creation_started",
            "run_id": response.run_id,
            "message": f"Skill generation started: {response.message}",
            "watch": f"Use `/skill status {response.run_id}` to monitor progress",
        }

    elif command.subcommand == "status":
        if not command.run_id:
            return {"error": "run_id required for /skill status"}

        response = await tool.get_status(command.run_id)
        return {
            "type": "skill_status",
            "run_id": command.run_id,
            "status": response.status,
            "phase": response.phase,
            "progress": response.progress,
            "message": response.message,
            "skill": response.skill,
        }

    elif command.subcommand == "list":
        async with __import__("aiohttp").ClientSession() as session:
            async with session.get(
                "http://localhost:8765/api/quality/skill-creator/skills"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "type": "skill_list",
                        "skills": data.get("skills", []),
                        "count": len(data.get("skills", [])),
                    }
                else:
                    return {"error": "Failed to fetch skills"}

    else:
        return {"error": f"Unknown subcommand: {command.subcommand}"}


# Natural language trigger detection (for bridges)
def detect_skill_creation_trigger(message: str) -> Optional[str]:
    """Detect natural language skill creation requests.

    Examples:
      "erzeuge mir einen skill der JSON validiert"
      "create a skill for code analysis"
      "ich möchte einen skill der logs parsed"
    """
    message_lower = message.lower()

    # German triggers
    if any(
        phrase in message_lower
        for phrase in [
            "erzeuge mir einen skill",
            "erstelle einen skill",
            "generiere einen skill",
            "ich brauche einen skill",
        ]
    ):
        # Extract the description part
        for phrase in ["der", "dass", "um", "für"]:
            if phrase in message_lower:
                idx = message_lower.find(phrase)
                desc = message[idx + len(phrase) :].strip()
                if desc:
                    return desc

    # English triggers
    if any(
        phrase in message_lower
        for phrase in [
            "create a skill",
            "create skill",
            "generate a skill",
            "i need a skill",
            "build a skill",
        ]
    ):
        for phrase in ["that", "for", "to"]:
            if phrase in message_lower:
                idx = message_lower.find(phrase)
                desc = message[idx + len(phrase) :].strip()
                if desc:
                    return desc

    return None
