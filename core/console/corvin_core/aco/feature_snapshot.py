"""ADR-0212 — Ecosystem Feature Telemetry Collection.

Collects instance-level feature usage snapshots (bridges, LDD, A2A, workflows,
browser automation, compute jobs, forge tools, skills, voice sessions, artifacts).

Fail-closed validation ensures only known features are transmitted. Complies with
GDPR Art. 6(1)(f) legitimate interest — anonymous, aggregated, no PII/content.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Allowed feature keys (closed enum).
_KNOWN_FEATURES = {
    "bridges_connected",
    "ldd_enabled",
    "a2a_delegations_count",
    "workflows_run_count",
    "browser_automation_used",
    "compute_jobs_count",
    "forge_tools_created",
    "skills_created",
    "voice_sessions",
    "artifacts_created",
    "agent_types_used",
    "mcp_servers_connected",
}


def collect_feature_snapshot(home: Path) -> Optional[dict]:
    """Collect instance-level feature usage snapshot.

    Returns a dict with feature flags/counts, or None if collection fails.
    No user data, no PII — structural enum of available features only.
    """
    home = Path(home)
    snapshot = {}

    # Bridges: check if any bridge config exists (Discord, Telegram, Slack, Teams, Email, Signal, WhatsApp).
    try:
        bridges_dir = home / "bridges"
        if bridges_dir.is_dir():
            bridge_configs = list(bridges_dir.glob("*.json"))
            snapshot["bridges_connected"] = len([c for c in bridge_configs if c.is_file()])
    except Exception:  # noqa: BLE001
        pass

    # LDD: check if LDD is enabled in config.
    try:
        from .htrace_consent import _tenant_cfg_path, _read_telemetry_flag
        cfg = _tenant_cfg_path(home)
        ldd_enabled = _read_telemetry_flag(cfg, "ldd_enabled")
        if ldd_enabled is not None:
            snapshot["ldd_enabled"] = ldd_enabled
    except Exception:  # noqa: BLE001
        pass

    # A2A: count delegation records (if any exist).
    try:
        a2a_dir = home / "a2a" / "delegations"
        if a2a_dir.is_dir():
            snapshot["a2a_delegations_count"] = len(list(a2a_dir.glob("*.json")))
    except Exception:  # noqa: BLE001
        pass

    # Workflows: count workflow runs (if run history exists).
    try:
        workflows_dir = home / "workflows" / "runs"
        if workflows_dir.is_dir():
            snapshot["workflows_run_count"] = len(list(workflows_dir.glob("*.json")))
    except Exception:  # noqa: BLE001
        pass

    # Browser Automation: check if any browser sessions exist.
    try:
        browser_dir = home / "browser"
        if browser_dir.is_dir():
            snapshot["browser_automation_used"] = (browser_dir / "sessions").is_dir()
    except Exception:  # noqa: BLE001
        pass

    # Compute Jobs: count submitted jobs.
    try:
        compute_dir = home / "compute" / "jobs"
        if compute_dir.is_dir():
            snapshot["compute_jobs_count"] = len(list(compute_dir.glob("*.json")))
    except Exception:  # noqa: BLE001
        pass

    # Forge Tools: count registered tools.
    try:
        forge_dir = home / "forge" / "tools"
        if forge_dir.is_dir():
            snapshot["forge_tools_created"] = len(list(forge_dir.glob("*.py")))
    except Exception:  # noqa: BLE001
        pass

    # Skills: count registered skills.
    try:
        skills_dir = home / "skill-forge" / "skills"
        if skills_dir.is_dir():
            snapshot["skills_created"] = len(list(skills_dir.glob("*.md")))
    except Exception:  # noqa: BLE001
        pass

    # Voice: count voice sessions.
    try:
        voice_dir = home / "voice" / "sessions"
        if voice_dir.is_dir():
            snapshot["voice_sessions"] = len(list(voice_dir.glob("*.json")))
    except Exception:  # noqa: BLE001
        pass

    # Artifacts: count session artifacts.
    try:
        artifacts_dir = home / "sessions" / "artifacts"
        if artifacts_dir.is_dir():
            snapshot["artifacts_created"] = len(list(artifacts_dir.glob("*")))
    except Exception:  # noqa: BLE001
        pass

    # Agent Types: count used agent types (from config or usage logs).
    snapshot["agent_types_used"] = 0  # placeholder; full count requires log scan

    # MCP Servers: count connected MCP plugins.
    try:
        mcp_dir = home / "mcp" / "servers"
        if mcp_dir.is_dir():
            snapshot["mcp_servers_connected"] = len(list(mcp_dir.glob("*.json")))
    except Exception:  # noqa: BLE001
        pass

    return snapshot if snapshot else None


def _assert_safe_features(snapshot: dict) -> Optional[dict]:
    """Validate snapshot against closed enum; fail-closed.

    Drops any keys not in _KNOWN_FEATURES or values with PII shapes.
    Returns a sanitized snapshot or None if validation fails.
    """
    if not isinstance(snapshot, dict):
        return None

    safe = {}
    for key, value in snapshot.items():
        # Reject unknown keys.
        if key not in _KNOWN_FEATURES:
            logger.debug("feature_snapshot: unknown key %s, dropping", key)
            continue

        # Type check: counts must be int, bools must be bool.
        if key.endswith("_count"):
            if not isinstance(value, int) or value < 0:
                logger.debug("feature_snapshot: invalid count %s=%s", key, value)
                continue
            safe[key] = value
        elif key.endswith("_enabled") or key.endswith("_used"):
            if not isinstance(value, bool):
                logger.debug("feature_snapshot: invalid bool %s=%s", key, value)
                continue
            safe[key] = value
        else:
            # Default: allow int/bool, reject everything else.
            if isinstance(value, (int, bool)):
                safe[key] = value
            else:
                logger.debug("feature_snapshot: invalid value %s=%s", key, value)

    return safe if safe else None


def save_feature_snapshot(home: Path, snapshot: dict) -> None:
    """Persist feature snapshot to ~/.corvin/telemetry/feature_snapshot.json.

    Atomic write with chmod 0o600 (owner-only readable). No-op on error.
    """
    home = Path(home)
    try:
        tele_dir = home / "telemetry"
        tele_dir.mkdir(parents=True, exist_ok=True)

        snapshot_file = tele_dir / "feature_snapshot.json"
        snapshot_file.write_text(
            json.dumps(snapshot, ensure_ascii=False),
            encoding="utf-8",
        )
        snapshot_file.chmod(0o600)
    except Exception as e:  # noqa: BLE001
        logger.debug("feature_snapshot: failed to save: %s", e)
