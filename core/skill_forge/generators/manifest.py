"""Skill Manifest Schema — v2.0 (LLM-ready, template-safe)."""

from dataclasses import dataclass, asdict
from typing import Optional, List
import json
from enum import Enum


class SkillType(str, Enum):
    """Skill type — determines generation path."""
    LEARNED_EXPERIENCE = "learned-experience"  # Template: best practices, patterns
    REASONING = "reasoning"                      # Template: dialectical reasoning, how-tos
    REFERENCE = "reference"                      # Template: API docs, checklists
    AUTOMATION = "automation"                    # LLM: code generation


class SkillScope(str, Enum):
    """Scope — determines visibility and lifecycle."""
    TASK = "task"
    SESSION = "session"
    PROJECT = "project"
    USER = "user"


@dataclass
class SkillManifest:
    """Skill Manifest (v2.0) — LLM-ready, template-compatible."""

    name: str                          # skill_id (snake_case)
    skill_type: SkillType              # Type determines generation path
    title: str                         # Human-readable title
    description: str                   # One-paragraph summary
    scope: SkillScope                  # Visibility scope

    # Content
    body_md: str                       # Markdown body (or template)
    examples: Optional[List[str]] = None  # Usage examples

    # Metadata
    version: str = "1.0.0"             # Semantic version
    tags: List[str] = None             # Search tags

    # Generation meta
    generated_at: Optional[str] = None # ISO timestamp
    generator_phase: Optional[str] = None  # "skeleton" or "full"

    def to_dict(self) -> dict:
        """Convert to dict (for JSON serialization)."""
        d = asdict(self)
        d['skill_type'] = self.skill_type.value
        d['scope'] = self.scope.value
        if self.tags is None:
            d['tags'] = []
        return d

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "SkillManifest":
        """Deserialize from dict."""
        data = data.copy()
        data['skill_type'] = SkillType(data['skill_type'])
        data['scope'] = SkillScope(data['scope'])
        return cls(**data)


class SkillManifestSchema:
    """Validator + schema definition for manifests."""

    SCHEMA = {
        "type": "object",
        "required": ["name", "skill_type", "title", "description", "scope", "body_md"],
        "properties": {
            "name": {"type": "string", "pattern": "^[a-z_][a-z0-9_]*$"},
            "skill_type": {"enum": ["learned-experience", "reasoning", "reference", "automation"]},
            "title": {"type": "string", "minLength": 5, "maxLength": 100},
            "description": {"type": "string", "minLength": 10, "maxLength": 500},
            "scope": {"enum": ["task", "session", "project", "user"]},
            "body_md": {"type": "string", "minLength": 50},
            "examples": {"type": "array", "items": {"type": "string"}},
            "version": {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$"},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
            "generated_at": {"type": "string", "format": "date-time"},
            "generator_phase": {"enum": ["skeleton", "full"]},
        }
    }

    @staticmethod
    def validate(data: dict) -> tuple[bool, Optional[str]]:
        """Validate manifest dict. Returns (is_valid, error_msg)."""
        # Required fields
        required = {"name", "skill_type", "title", "description", "scope", "body_md"}
        missing = required - set(data.keys())
        if missing:
            return False, f"Missing required fields: {missing}"

        # Name pattern
        if not isinstance(data["name"], str) or not data["name"].replace("_", "").isalnum():
            return False, "name must be snake_case alphanumeric"

        # Lengths
        if len(data.get("title", "")) < 5:
            return False, "title too short (min 5 chars)"
        if len(data.get("body_md", "")) < 50:
            return False, "body_md too short (min 50 chars)"

        # Enum values
        valid_types = {"learned-experience", "reasoning", "reference", "automation"}
        valid_scopes = {"task", "session", "project", "user"}

        if data.get("skill_type") not in valid_types:
            return False, f"Invalid skill_type: {data.get('skill_type')}"
        if data.get("scope") not in valid_scopes:
            return False, f"Invalid scope: {data.get('scope')}"

        return True, None
