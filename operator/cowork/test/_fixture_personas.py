"""Fixture personas for the cowork resolver tests.

The bundled persona JSONs (``operator/cowork/personas/*.json``) were removed
in e7e3560e — 100 % of traffic runs on the new Skills. The resolver itself is
still live: ``adapter.py`` resolves ``chat_profiles[<chat>].persona`` through
``resolver.resolve()`` for personas the OPERATOR ships under
``$COWORK_USER_DIR/personas/``. These tests therefore exercise the resolver's
real mechanics (user-dir load, override merge, forge/skill-forge/delegate/
orchestration/capability injection, MCP materialisation) against fixture
personas written into a temp user dir — never against a bundle that no
longer exists.

Every helper is deliberately tiny: a test file calls ``sandbox()`` FIRST
(before importing ``resolver``, which reads ``COWORK_USER_DIR`` at import)
and ``write_personas(user_dir, {...})`` to lay down what it needs.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"


def sandbox(prefix: str = "cowork-test-") -> tuple[Path, Path]:
    """Create a temp COWORK_USER_DIR + COWORK_MCP_CACHE and export them.

    Returns ``(sandbox_root, user_dir)``. Drops any cached ``resolver`` /
    capability modules so the next ``import resolver`` sees the env.
    """
    root = Path(tempfile.mkdtemp(prefix=prefix))
    user_dir = root / "user"
    (user_dir / "personas").mkdir(parents=True)
    os.environ["COWORK_USER_DIR"] = str(user_dir)
    os.environ["COWORK_MCP_CACHE"] = str(root / "mcp")
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    for mod in ("resolver", "capability_map", "capability_registry"):
        sys.modules.pop(mod, None)
    return root, user_dir


def write_personas(user_dir: Path, personas: dict[str, dict]) -> None:
    """Write ``{name: persona-json}`` into ``<user_dir>/personas/``."""
    d = user_dir / "personas"
    d.mkdir(parents=True, exist_ok=True)
    for name, data in personas.items():
        (d / f"{name}.json").write_text(json.dumps({"name": name, **data}),
                                        encoding="utf-8")


def reload_resolver():
    """Re-import ``resolver`` so it re-reads ``COWORK_USER_DIR``."""
    for mod in ("resolver", "capability_map", "capability_registry"):
        sys.modules.pop(mod, None)
    import resolver  # type: ignore  # noqa: PLC0415
    return resolver


# A representative operator-shipped persona set. Flags mirror what the
# removed bundle used to carry so the injectors' contracts stay pinned.
CODER = {
    "description": "Coding agent",
    "permission_mode": "bypassPermissions",
    "zero_config": True,
    "allowed_tools": [],
    "forge_enabled": True,
    "forge_default_scope": "project",
    "skill_forge_enabled": True,
    "capability_aware": True,
    "orchestration_enabled": True,
    "tool_namespace": "code",
    "append_system": "You are a coding agent.",
}
RESEARCH = {
    "description": "Web research agent",
    "permission_mode": "bypassPermissions",
    "zero_config": True,
    "allowed_tools": [],
    "forge_enabled": True,
    "forge_default_scope": "session",
    "forge_network": "allow",
    "add_dirs": ["{{HOME}}/cowork/research"],
    "mcp_servers": {"playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]}},
    "append_system": "You are a web research agent. Use WebSearch and the browser.",
}
ASSISTANT = {
    "description": "Generalist assistant",
    "permission_mode": "bypassPermissions",
    "zero_config": True,
    "allowed_tools": [],
    "forge_enabled": True,
    "forge_default_scope": "session",
    "capability_aware": True,
    "orchestration_enabled": True,
    "mcp_servers": {"playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]}},
    "tts_voice": "alloy",
    "append_system": "You are a generalist with all tools.",
}
ORCHESTRATOR = {
    "description": "OS process that delegates",
    "permission_mode": "bypassPermissions",
    "zero_config": True,
    "allowed_tools": [],
    "delegate_enabled": True,
    "orchestration_enabled": True,
    "capability_aware": True,
    "append_system": "You orchestrate.",
}
HOMEASSISTANT = {
    "description": "Constrained smart-home persona",
    "permission_mode": "bypassPermissions",
    "zero_config": False,
    "allowed_tools": [],
    "append_system": "You control the home.",
}
FORGE = {
    "description": "Forge persona — dedicated tool + skill generator",
    "permission_mode": "bypassPermissions",
    "zero_config": True,
    "allowed_tools": ["mcp__forge__forge_tool", "mcp__forge__forge_promote"],
    "skill_forge_enabled": True,
    "mcp_servers": {"forge": {
        "command": "{{PYTHON}}",
        "args": ["{{REPO_ROOT}}/operator/forge/forge.py", "mcp"],
        "env": {"FORGE_PERSONA": "forge"},
    }},
    "append_system": "Forge persona: generate tools.",
}

INBOX = {
    "description": "Mail/calendar persona (Google OAuth — manual setup)",
    "permission_mode": "bypassPermissions",
    "zero_config": False,
    "allowed_tools": [],
    "forge_enabled": True,
    "forge_default_scope": "user",
    "append_system": "You handle the inbox.",
}

DEFAULT_SET = {
    "coder": CODER, "research": RESEARCH, "assistant": ASSISTANT,
    "orchestrator": ORCHESTRATOR, "homeassistant": HOMEASSISTANT, "forge": FORGE,
    "inbox": INBOX,
}
