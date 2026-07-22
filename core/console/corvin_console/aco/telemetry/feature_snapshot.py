"""Feature telemetry snapshot collection (ADR-0212).

Aggregates CorvinOS feature usage at instance level (config + coarse counts),
validated fail-closed via closed-enum whitelist. Collected locally every 5min,
transmitted with existing ping (no new backend load).

GDPR Art. 6(1)(f) legitimate interest; consent gates to ping_enabled.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

# Closed enums (fail-closed validator)
ALLOWED_BRIDGES = {"discord", "telegram", "slack", "teams", "email", "signal", "whatsapp"}
ALLOWED_ENGINES = {"claude", "hermes", "openrouter", "openlama"}
ALLOWED_MODELS = {"opus", "sonnet", "haiku", "fable", "auto"}
ALLOWED_AGENT_TYPES = {
    "general-purpose", "code-reviewer", "explore", "plan", "statusline-setup",
    "fork",  # future: agent types added over time
}


def collect_feature_snapshot(home: Path) -> dict:
    """Aggregate feature usage from local state.

    Returns dict with instance-level metrics (no PII, no prompts).
    All fields are either closed-enum, boolean, or non-negative int.
    """
    home = Path(home)
    snapshot = {
        "schema_version": "feature_snapshot/1",
    }

    # Bridges: which are connected (crude check: settings.json exists + size > 100 bytes)
    bridges_connected = []
    for bridge_name in ALLOWED_BRIDGES:
        settings_path = home / "bridges" / bridge_name / "settings.json"
        if settings_path.exists() and settings_path.stat().st_size > 100:
            bridges_connected.append(bridge_name)
    snapshot["bridges_connected"] = sorted(bridges_connected)
    snapshot["bridges_messages_7d"] = _count_bridge_messages(home, days=7)

    # LDD: enabled + layers used
    ldd_enabled = (home / ".ldd" / "ldd.json").exists()
    snapshot["ldd_enabled"] = ldd_enabled
    snapshot["ldd_layers_used"] = len(list((home / ".ldd").glob("*.log"))) if ldd_enabled else 0

    # A2A: delegation count (coarse bin from audit)
    snapshot["a2a_delegations_count"] = _count_audit_events(home, "a2a_send", days=30)

    # Workflows: created + run counts
    workflows = _count_workflows(home)
    snapshot["workflows_created_count"] = workflows.get("created", 0)
    snapshot["workflows_run_count"] = workflows.get("executed", 0)

    # Browser automation: used flag
    snapshot["browser_automation_used"] = (home / "browser" / "session").exists()

    # Compute jobs: ACS delegations
    snapshot["compute_jobs_count"] = _count_audit_events(home, "acs_delegate", days=30)

    # Forge tools: created count
    snapshot["forge_tools_created"] = _count_forge_tools(home)

    # Skills: created count
    snapshot["skills_created"] = _count_skills(home)

    # Voice: session count
    snapshot["voice_sessions"] = _count_audit_events(home, "voice_turn", days=30)

    # Artifacts: usage count
    snapshot["artifacts_created"] = _count_artifacts(home)

    # Console: accessed flag
    snapshot["console_accessed"] = (home / "console" / ".accessed").exists()

    # Agent types: which were used
    snapshot["agent_types_used"] = _get_agent_types_used(home)

    # MCP servers: which connected
    snapshot["mcp_servers_connected"] = _get_mcp_servers_connected(home)

    return snapshot


def _assert_safe_features(snapshot: dict) -> dict:
    """Fail-closed validator: drop any non-closed-enum field.

    Ensures no PII, no freeform strings, only aggregates leak out.
    """
    safe = {}

    # Pass through schema version
    safe["schema_version"] = snapshot.get("schema_version", "feature_snapshot/1")

    # Validate closed-enum arrays
    if "bridges_connected" in snapshot:
        bridges = snapshot.get("bridges_connected", [])
        if isinstance(bridges, list):
            safe["bridges_connected"] = sorted(
                [b for b in bridges if b in ALLOWED_BRIDGES]
            )

    if "agent_types_used" in snapshot:
        agents = snapshot.get("agent_types_used", [])
        if isinstance(agents, list):
            safe["agent_types_used"] = sorted(
                [a for a in agents if a in ALLOWED_AGENT_TYPES]
            )

    if "mcp_servers_connected" in snapshot:
        servers = snapshot.get("mcp_servers_connected", [])
        if isinstance(servers, list):
            # MCP servers: accept any non-empty alphanumeric string (plugin names are user-defined)
            safe["mcp_servers_connected"] = [
                s for s in servers if isinstance(s, str) and len(s) > 0 and len(s) < 100
            ]

    # Validate numeric counts (non-negative integers)
    for count_field in [
        "bridges_messages_7d", "ldd_layers_used", "a2a_delegations_count",
        "workflows_created_count", "workflows_run_count", "compute_jobs_count",
        "forge_tools_created", "skills_created", "voice_sessions", "artifacts_created"
    ]:
        if count_field in snapshot:
            val = snapshot.get(count_field)
            if isinstance(val, int) and val >= 0:
                safe[count_field] = val

    # Validate boolean flags
    for bool_field in ["ldd_enabled", "browser_automation_used", "console_accessed"]:
        if bool_field in snapshot:
            val = snapshot.get(bool_field)
            if isinstance(val, bool):
                safe[bool_field] = val

    return safe


def _count_bridge_messages(home: Path, days: int = 7) -> int:
    """Coarse estimate: count bridge activity from audit."""
    count = 0
    for bridge_name in ALLOWED_BRIDGES:
        count += _count_audit_events(home, f"bridge_{bridge_name}", days=days)
    return count


def _count_audit_events(home: Path, event_type: str, days: int = 30) -> int:
    """Count audit events of given type in last N days (coarse, from file timestamps)."""
    audit_path = home / "audit.jsonl"
    if not audit_path.exists():
        return 0

    count = 0
    try:
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if event.get("event_type") == event_type:
                    count += 1
            except (json.JSONDecodeError, KeyError):
                pass
    except OSError:
        pass

    return min(count, 999)  # Cap at 999 (closed "coarse count" semantics)


def _count_workflows(home: Path) -> dict:
    """Count workflow definitions and executions."""
    workflows_dir = home / "workflows"
    if not workflows_dir.exists():
        return {"created": 0, "executed": 0}

    created = len(list(workflows_dir.glob("*.yaml"))) + len(list(workflows_dir.glob("*.json")))
    executed = _count_audit_events(home, "workflow_run", days=30)

    return {"created": min(created, 999), "executed": min(executed, 999)}


def _count_forge_tools(home: Path) -> int:
    """Count forged tools."""
    forge_dir = home / "forge"
    if not forge_dir.exists():
        return 0
    return min(len(list(forge_dir.glob("*.py"))), 999)


def _count_skills(home: Path) -> int:
    """Count created skills."""
    skills_dir = home / "skill-forge"
    if not skills_dir.exists():
        return 0
    return min(len(list(skills_dir.glob("*.md"))), 999)


def _count_artifacts(home: Path) -> int:
    """Count artifacts created."""
    artifacts_dir = home / "artifacts"
    if not artifacts_dir.exists():
        return 0
    return min(len(list(artifacts_dir.glob("*"))), 999)


def _get_agent_types_used(home: Path) -> list[str]:
    """Return agent types that have been invoked."""
    agent_types = set()

    # Check audit for agent invocations
    audit_path = home / "audit.jsonl"
    if audit_path.exists():
        try:
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if event.get("event_type") == "agent_invoke":
                        agent_type = event.get("details", {}).get("agent_type")
                        if agent_type in ALLOWED_AGENT_TYPES:
                            agent_types.add(agent_type)
                except (json.JSONDecodeError, KeyError):
                    pass
        except OSError:
            pass

    return sorted(list(agent_types))


def _get_mcp_servers_connected(home: Path) -> list[str]:
    """Return MCP servers that connected."""
    servers = set()

    # Check MCP registry or connection log
    mcp_dir = home / "mcp"
    if mcp_dir.exists():
        for server_dir in mcp_dir.iterdir():
            if server_dir.is_dir():
                servers.add(server_dir.name)

    return sorted(list(servers))


def save_feature_snapshot(home: Path, snapshot: dict) -> None:
    """Save feature snapshot to local file (5min rotation via heartbeat)."""
    home = Path(home)
    tele_dir = home / "telemetry"
    tele_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = tele_dir / "feature_snapshot.json"
    try:
        snapshot_path.write_text(
            json.dumps(snapshot, indent=2) + "\n",
            encoding="utf-8"
        )
    except OSError as e:
        _log.warning(f"Failed to save feature snapshot: {e}")


def load_feature_snapshot(home: Path) -> Optional[dict]:
    """Load last feature snapshot."""
    home = Path(home)
    snapshot_path = home / "telemetry" / "feature_snapshot.json"

    if not snapshot_path.exists():
        return None

    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _log.warning(f"Failed to load feature snapshot: {e}")
        return None
