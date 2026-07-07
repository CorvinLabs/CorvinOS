"""mcp_config_builder.py — Layer 30.2/30.3 MCP-pass-through for delegate spawns.

Materialises per-spawn MCP-server configs in the worker's hermetic
tempdir (Layer 29.2a) so Codex / OpenCode / Claude Code workers can
reach the **forge** and **skill_forge** MCP servers from inside the
delegated subprocess.

Three engine-specific ``materialise_*`` helpers handle the different
on-disk config formats:

* **Claude Code** — JSON ``mcp_config.json`` consumed via
  ``--mcp-config`` (existing engine flag at
  ``agents/claude_code.py:157``). Returns ``mcp_config_path``.
* **Codex CLI** — TOML ``config.toml`` placed in a ``$CODEX_HOME``
  override so codex's per-process config resolution picks up the
  per-spawn setup without touching the operator's
  ``~/.codex/config.toml``. Returns ``env={"CODEX_HOME": ...}``.
* **OpenCode** — JSON ``opencode.json`` written into the worker's
  ``working_dir`` (which is the hermetic tempdir from Layer 29.2a
  unless the caller passed an explicit ``working_dir``). OpenCode
  resolves config from cwd-first, so this gets picked up
  automatically. Returns ``{}`` (no env / kwargs needed).

The ``McpServerSpec`` dataclass is the engine-neutral shape every
materialiser starts from. ``build_mcp_specs`` is the entry point that
decides which servers to expose based on the persona's
``forge_enabled`` / ``skill_forge_enabled`` flags.

Persistence story
-----------------

The MCP-config files are **per-spawn ephemera** in the hermetic
tempdir (Layer 29.2a) — they vanish when ``run_delegate`` exits.
**The forge / skill-forge servers themselves write to the canonical
on-disk forge tree** (under ``<corvin_home>/...``), so any tool
created by a worker via ``mcp__forge__forge_tool(...)`` survives
the spawn and is reachable from the next OS-turn or worker-spawn
through the same persona. That's the load-bearing property: tools
and skills created at runtime by a worker are persistent first-class
artifacts of the unified forge tree, not throwaway delegate-state.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Canonical engine ids — must match ``delegation.AVAILABLE_ENGINES``.
ENGINE_CLAUDE_CODE = "claude_code"
ENGINE_CODEX = "codex_cli"
ENGINE_OPENCODE = "opencode"

SERVER_FORGE = "forge"
SERVER_SKILL_FORGE = "skill_forge"


# ---------------------------------------------------------------------------
# Spec dataclass + builder
# ---------------------------------------------------------------------------


@dataclass
class McpServerSpec:
    """Engine-neutral description of one MCP server to spawn.

    Mirrors the on-disk format the bridge's cowork resolver writes
    (see ``cowork/lib/resolver.py::_inject_forge_capability``). All
    three engine-specific materialisers consume this same shape.
    """
    name: str
    # Default to the running interpreter (sys.executable) rather than the
    # literal "python3": on Windows there is usually no ``python3`` on PATH,
    # and even on Linux a bare-PATH ``python3`` may differ from the venv/wheel
    # interpreter that actually has the CorvinOS deps. sys.executable is the
    # SAME interpreter running this process — the one that can import forge /
    # skill_forge. See resolver.py for the mirror of this on the cowork side.
    command: str = sys.executable
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


def _forge_spec(persona: str, repo_root: Path) -> McpServerSpec:
    """Spec for the forge MCP server. Mirrors the cowork resolver."""
    return McpServerSpec(
        name=SERVER_FORGE,
        command=sys.executable,
        args=[
            str(repo_root / "operator" / "forge" / "forge.py"),
            "mcp",
            "--permission-mode",
            "yes",
        ],
        env={
            # Persona attribution — forge.scope reads this to route
            # tool-writes to the right persona-namespace.
            "FORGE_PERSONA": persona,
            # CORVIN_CALLER_PERSONA is the unified-chain audit
            # attribution. Layer 29.2b already sets it for the
            # delegate-MCP env; we forward to the forge-MCP env so
            # `tool.created` events carry the right persona.
            "CORVIN_CALLER_PERSONA": persona,
            # PYTHONPATH so the spawned forge.py can import its own
            # package modules.
            "PYTHONPATH": str(repo_root / "operator" / "forge"),
        },
    )


def _skill_forge_spec(persona: str, repo_root: Path) -> McpServerSpec:
    """Spec for the skill-forge MCP server. Mirrors the cowork resolver."""
    return McpServerSpec(
        name=SERVER_SKILL_FORGE,
        command=sys.executable,
        args=["-m", "skill_forge.mcp_server"],
        env={
            "SKILL_FORGE_PERSONA": persona,
            "CORVIN_CALLER_PERSONA": persona,
            "PYTHONPATH": (
                f"{repo_root / 'operator' / 'skill-forge'}"
                f":{repo_root / 'operator' / 'forge'}"
            ),
        },
    )


def build_mcp_specs(
    *,
    persona: str,
    forge_enabled: bool,
    skill_forge_enabled: bool,
    repo_root: Path | None = None,
) -> list[McpServerSpec]:
    """Build the MCP-server spec list for one delegate spawn.

    ``repo_root`` defaults to walking up from this file for a
    ``plugins/`` marker. Tests can pass an explicit override.
    """
    root = repo_root or _detect_repo_root()
    out: list[McpServerSpec] = []
    persona = (persona or "").strip() or "delegate"
    if forge_enabled:
        out.append(_forge_spec(persona, root))
    if skill_forge_enabled:
        out.append(_skill_forge_spec(persona, root))
    return out


def _detect_repo_root() -> Path:
    """Walk up from this file for a `.corvin_repo` marker.

    The `.corvin_repo` file is the canonical signal. We also accept a
    legacy `plugins/voice/` directory (the pre-rename layout), but only
    when it is unambiguously the runtime plugins root — a bare `plugins/`
    directory can match `core/plugins` and would resolve to the wrong
    root.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists():
            return parent
        if (parent / "plugins" / "voice").is_dir():
            return parent
    # Fallback: assume the package's parent's parent is the repo root.
    # (core/delegate/corvin_delegate/mcp_config_builder.py
    #  → ../../.. = repo root)
    return here.parents[3]


# ---------------------------------------------------------------------------
# Asymmetric env-floor resolution (mirror of skill_context)
# ---------------------------------------------------------------------------


_ENV_FORGE_ENABLED = "CORVIN_DELEGATE_FORGE_ENABLED"
_ENV_SKILL_FORGE_ENABLED = "CORVIN_DELEGATE_SKILL_FORGE_ENABLED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "none"})


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE_VALUES:
            return True
        if v in _FALSE_VALUES:
            return False
    return None


def env_floor_forge_enabled() -> bool | None:
    return _coerce_bool(os.environ.get(_ENV_FORGE_ENABLED))


def env_floor_skill_forge_enabled() -> bool | None:
    return _coerce_bool(os.environ.get(_ENV_SKILL_FORGE_ENABLED))


def resolve_capability(
    *,
    env_floor: bool | None,
    tool_arg: bool | None,
    persona_default: bool | None = None,
) -> bool:
    """Asymmetric resolution: env-floor wins when stricter (False).

    Mirror of ``skill_context.resolve_inject_skills``.
    """
    if env_floor is False:
        return False
    if env_floor is True:
        return True
    if tool_arg is not None:
        return bool(tool_arg)
    if persona_default is not None:
        return bool(persona_default)
    return False


# ---------------------------------------------------------------------------
# Engine-specific materialisers
# ---------------------------------------------------------------------------


def materialise_claude_code_config(
    *,
    specs: list[McpServerSpec],
    tempdir: Path,
) -> dict[str, Any]:
    """Write a Claude-Code-shaped MCP config to ``tempdir/mcp_config.json``.

    The ClaudeCodeEngine (``agents/claude_code.py:157``) consumes
    the path via the ``mcp_config_path`` spawn kwarg. Returns the
    spawn-kwargs dict that ``run_delegate`` should merge into the
    engine's ``spawn(**kwargs)`` call.

    The on-disk shape matches the cowork resolver's
    ``materialize_mcp`` output (``{"mcpServers": {...}}``) so
    Claude Code's existing flag works unchanged.
    """
    if not specs:
        return {}
    payload = {"mcpServers": _specs_to_dict_for_claude(specs)}
    out_path = tempdir / "mcp_config.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass
    return {"mcp_config_path": str(out_path)}


def materialise_codex_config(
    *,
    specs: list[McpServerSpec],
    tempdir: Path,
) -> dict[str, str]:
    """Write a Codex-shaped MCP config inside ``tempdir/.codex_home/``.

    Codex CLI reads ``$CODEX_HOME/config.toml``. We write a minimal
    TOML registering the MCP servers and return the env-overlay that
    ``run_delegate`` should merge into the engine's spawn ``env``.

    Codex's TOML schema is documented at
    ``codex/docs/config.md::mcp_servers`` — we follow the
    ``[mcp_servers.<name>] command, args, env`` pattern.
    """
    if not specs:
        return {}
    codex_home = tempdir / ".codex_home"
    codex_home.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(codex_home, 0o700)
    except OSError:
        pass
    config_path = codex_home / "config.toml"
    config_path.write_text(
        _specs_to_toml_for_codex(specs),
        encoding="utf-8",
    )
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass
    return {"CODEX_HOME": str(codex_home)}


def materialise_opencode_config(
    *,
    specs: list[McpServerSpec],
    working_dir: Path,
) -> None:
    """Write an OpenCode-shaped MCP config at ``working_dir/opencode.json``.

    OpenCode resolves config via cwd-first, so writing into the
    worker's working_dir (which is the hermetic tempdir from Layer
    29.2a unless the caller passed an explicit working_dir) gets
    picked up automatically with no env / kwarg plumbing.

    Schema: ``{"$schema": "...", "mcp": {<name>: {type, command, environment, enabled}}}``
    per https://opencode.ai/docs/config/.
    """
    if not specs:
        return
    payload: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": _specs_to_dict_for_opencode(specs),
    }
    out_path = working_dir / "opencode.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Per-engine on-disk shape helpers
# ---------------------------------------------------------------------------


def _specs_to_dict_for_claude(specs: list[McpServerSpec]) -> dict[str, Any]:
    """Claude Code's mcpServers JSON shape (one entry per server)."""
    out: dict[str, Any] = {}
    for spec in specs:
        out[spec.name] = {
            "command": spec.command,
            "args": list(spec.args),
            "env": dict(spec.env),
        }
    return out


def _toml_escape_str(s: str) -> str:
    """Escape a string for TOML basic-string quoting.

    TOML basic strings allow most chars; we escape backslash, double
    quote, and control chars conservatively. Newlines aren't expected
    in our MCP config values (paths, env keys) but we guard anyway.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _specs_to_toml_for_codex(specs: list[McpServerSpec]) -> str:
    """Codex CLI config.toml shape: ``[mcp_servers.<name>]`` sections.

    We hand-roll the TOML to avoid pulling in tomli_w as a dep — the
    config is structurally simple (no nested arrays of tables, no
    multi-line values) and the escape function above covers the
    edge cases that matter.
    """
    lines: list[str] = [
        "# Auto-generated by corvin-delegate (Layer 30.2 — per-spawn MCP config)",
        "# Do not edit; this file is rewritten on every delegate spawn.",
        "",
    ]
    for spec in specs:
        lines.append(f"[mcp_servers.{spec.name}]")
        lines.append(f'command = "{_toml_escape_str(spec.command)}"')
        # args = ["a", "b", "c"]
        if spec.args:
            arg_list = ", ".join(
                f'"{_toml_escape_str(str(a))}"' for a in spec.args
            )
            lines.append(f"args = [{arg_list}]")
        else:
            lines.append("args = []")
        # env table — inline-table form: env = {KEY = "value", ...}
        if spec.env:
            entries = ", ".join(
                f'{k} = "{_toml_escape_str(str(v))}"'
                for k, v in spec.env.items()
            )
            lines.append(f"env = {{ {entries} }}")
        else:
            lines.append("env = {}")
        lines.append("")
    return "\n".join(lines)


def _specs_to_dict_for_opencode(specs: list[McpServerSpec]) -> dict[str, Any]:
    """OpenCode opencode.json mcp block shape.

    OpenCode's local-MCP form: ``type=local, command=[...], environment={...}, enabled=true``.
    The command is a single list (binary + args) rather than the
    Claude/Codex split into command + args.
    """
    out: dict[str, Any] = {}
    for spec in specs:
        out[spec.name] = {
            "type": "local",
            "command": [spec.command, *list(spec.args)],
            "environment": dict(spec.env),
            "enabled": True,
        }
    return out


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------


def materialise_for_engine(
    *,
    engine_id: str,
    specs: list[McpServerSpec],
    tempdir: Path,
    working_dir: Path | None = None,
) -> dict[str, Any]:
    """Engine-neutral entry point.

    Returns a dict with up to two top-level keys:

    * ``spawn_kwargs`` — kwargs to merge into ``engine.spawn(...)``
      (currently only ``mcp_config_path`` for Claude Code).
    * ``env_overlay`` — env-vars to merge into the worker's ``env=``
      overlay (currently only ``CODEX_HOME`` for Codex).

    For OpenCode the materialiser writes directly into ``working_dir``
    (the hermetic tempdir or operator-supplied dir), so neither
    spawn_kwarg nor env-overlay is needed — the empty dict signals
    "all done, opencode picks it up via cwd-resolution".
    """
    if not specs:
        return {"spawn_kwargs": {}, "env_overlay": {}, "mcp_servers": []}

    spawn_kwargs: dict[str, Any] = {}
    env_overlay: dict[str, str] = {}

    if engine_id == ENGINE_CLAUDE_CODE:
        spawn_kwargs.update(
            materialise_claude_code_config(specs=specs, tempdir=tempdir)
        )
    elif engine_id == ENGINE_CODEX:
        env_overlay.update(
            materialise_codex_config(specs=specs, tempdir=tempdir)
        )
    elif engine_id == ENGINE_OPENCODE:
        # OpenCode reads config from cwd. Use the explicit working_dir
        # if the caller passed one, else fall back to the tempdir
        # (which is what Layer 29.2a's hermetic mode supplies to the
        # engine via its working_dir kwarg).
        target_dir = working_dir or tempdir
        materialise_opencode_config(
            specs=specs, working_dir=target_dir
        )
    else:
        # Unknown engine — caller-side validation should have caught
        # this, but fail cleanly: return empty result, no config
        # written. Caller can then proceed without MCP wiring.
        return {"spawn_kwargs": {}, "env_overlay": {}, "mcp_servers": []}

    return {
        "spawn_kwargs": spawn_kwargs,
        "env_overlay": env_overlay,
        "mcp_servers": [s.name for s in specs],
    }


__all__ = [
    "ENGINE_CLAUDE_CODE",
    "ENGINE_CODEX",
    "ENGINE_OPENCODE",
    "McpServerSpec",
    "SERVER_FORGE",
    "SERVER_SKILL_FORGE",
    "build_mcp_specs",
    "env_floor_forge_enabled",
    "env_floor_skill_forge_enabled",
    "materialise_claude_code_config",
    "materialise_codex_config",
    "materialise_for_engine",
    "materialise_opencode_config",
    "resolve_capability",
]
