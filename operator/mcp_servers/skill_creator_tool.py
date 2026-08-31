"""MCP Tool: create_skill — Generate reusable skills via async orchestration.

Tool Definition:
  name: create_skill
  description: Generate a new reusable skill using autonomous 6-phase LDD

Invocation:
  /skill create "validate JSON files"
  Discord: "erzeuge mir einen skill der JSON validiert"
  A2A: tool_call(create_skill, prompt="...")
"""

import aiohttp
import json
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class SkillCreationRequest:
    """Request to create a new skill."""
    prompt: str
    async_mode: bool = True
    return_run_id: bool = True


@dataclass
class SkillCreationResponse:
    """Response from skill creation."""
    run_id: str
    status: str  # pending | running | success | failed
    phase: str
    progress: int
    message: str
    skill: Optional[Dict[str, Any]] = None


class SkillCreatorTool:
    """MCP Tool handler for skill generation."""

    def __init__(self, console_url: str = "http://localhost:8765"):
        self.console_url = console_url
        self.endpoint = f"{console_url}/api/quality/skill-creator"

    async def create_skill(
        self,
        prompt: str,
        async_mode: bool = True,
        return_run_id: bool = True
    ) -> SkillCreationResponse:
        """Generate a new skill.

        Args:
            prompt: Description of the skill to create
            async_mode: Run async (returns immediately with run_id)
            return_run_id: Return run_id for status polling

        Returns:
            SkillCreationResponse with run_id and status
        """
        if not prompt or len(prompt.strip()) < 10:
            raise ValueError("Prompt must be at least 10 characters")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.endpoint}/generate",
                    json={
                        "user_request": prompt,
                        "async": async_mode,
                        "return_run_id": return_run_id,
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"API error: {resp.status}")

                    data = await resp.json()

                    return SkillCreationResponse(
                        run_id=data.get("run_id", ""),
                        status=data.get("status", "pending"),
                        phase=data.get("phase", "planning"),
                        progress=data.get("progress", 0),
                        message=data.get("message", "Initializing..."),
                        skill=data.get("skill"),
                    )

            except aiohttp.ClientError as e:
                raise RuntimeError(f"Network error: {str(e)}")

    async def get_status(self, run_id: str) -> SkillCreationResponse:
        """Poll skill generation status."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{self.endpoint}/status/{run_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Status check failed: {resp.status}")

                    data = await resp.json()

                    return SkillCreationResponse(
                        run_id=run_id,
                        status=data.get("status", "unknown"),
                        phase=data.get("phase", ""),
                        progress=data.get("progress", 0),
                        message=data.get("message", ""),
                        skill=data.get("skill"),
                    )

            except aiohttp.ClientError as e:
                raise RuntimeError(f"Network error: {str(e)}")


# MCP Tool Registration Schema
MCP_TOOL_SCHEMA = {
    "name": "create_skill",
    "description": "Generate a new reusable skill using autonomous 6-phase LDD orchestration",
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Description of the skill to create (e.g., 'Create a skill that validates JSON files')",
                "minLength": 10,
            },
            "async_mode": {
                "type": "boolean",
                "description": "Run asynchronously and return run_id for monitoring",
                "default": True,
            },
            "return_run_id": {
                "type": "boolean",
                "description": "Return run_id for status polling",
                "default": True,
            },
        },
        "required": ["prompt"],
    },
}


# Handler for MCP Tool invocation
async def handle_create_skill(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle create_skill tool invocation from MCP."""
    tool = SkillCreatorTool()

    try:
        response = await tool.create_skill(
            prompt=params.get("prompt", ""),
            async_mode=params.get("async_mode", True),
            return_run_id=params.get("return_run_id", True),
        )

        return {
            "status": "success",
            "run_id": response.run_id,
            "generation_status": response.status,
            "phase": response.phase,
            "progress": response.progress,
            "message": response.message,
            "skill": response.skill,
        }

    except (ValueError, RuntimeError) as e:
        return {
            "status": "error",
            "error": str(e),
        }
