"""
Skills CLI: `corvin skills` subcommand.

Replaces legacy `corvin flag` and `corvin plugin` CLIs.
Unified interface to manage OS-Skills (enable/disable/config/show).

Supports:
- List installed Skills
- Enable/disable individual Skills
- Configure Skill parameters
- Show audit trail for a Skill
- Show convergence metrics (learning loop integration)

Phase 1 (Weeks 1–4):
- Add this CLI as new interface
- Old `corvin flag` still works (backward-compat shim)
- Deprecation warnings printed to old CLI

Phase 3 (Weeks 19–24):
- Delete old CLI commands
- This becomes the standard interface

ADR-0543: Phase 1 Feature Flags Deprecation
ADR-0532: OS-Skills Architecture

Author: Corvin OS Team + Haiku 4.5
Date: 2026-09-01
"""

import click
import json
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime

# These imports will be available once the Skill infrastructure is built (Phase 2a)
# For now, they're mocked in tests.
try:
    from core.skills.skill_registry import SkillRegistry
    from core.skills.skill_manifest import SkillManifest
except ImportError:
    # Phase 1: Skill infrastructure not yet built; mock it for CLI testing
    SkillRegistry = None
    SkillManifest = None


@dataclass
class SkillSummary:
    """Summary of a Skill for display."""
    id: str
    name: str
    version: str
    enabled: bool
    description: str
    origin: str  # builtin / vetted / community
    confidence: Optional[float] = None  # From learning loop (if available)


class SkillOrigin(Enum):
    BUILTIN = "builtin"
    VETTED = "vetted"
    COMMUNITY = "community"


@click.group(name="skills", help="Manage OS-Skills (features and subsystems)")
def skills_group():
    """
    Skills CLI: unified interface to manage and configure OS-Skills.

    Replaces legacy `corvin flag` and `corvin plugin` commands.

    Examples:
        corvin skills list                          # List all Skills
        corvin skills enable os.vibe_engineering    # Enable a Skill
        corvin skills disable os.vibe_engineering   # Disable a Skill
        corvin skills config os.vibe_engineering --learning-rate=0.85
        corvin skills show os.vibe_engineering --audit-trail
    """
    pass


@skills_group.command(name="list", help="List all installed Skills")
@click.option(
    "--enabled-only",
    is_flag=True,
    default=False,
    help="Show only enabled Skills"
)
@click.option(
    "--format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format (text or JSON)"
)
def list_skills(enabled_only: bool, format: str):
    """
    List installed Skills.

    Text format shows a table with ID, name, version, enabled status, origin.
    JSON format outputs full manifest data.

    Compliance: Queries Skill registry (audit-logged as SKILL_LIST query)
    """
    try:
        registry = get_skill_registry()
        skills = registry.list_skills()

        if enabled_only:
            skills = [s for s in skills if s.enabled]

        if format == "json":
            click.echo(json.dumps([asdict(s) for s in skills], indent=2))
        else:
            # Text table format
            click.echo(f"{'ID':<30} {'Name':<20} {'Version':<10} {'Enabled':<8} {'Origin':<10}")
            click.echo("-" * 80)
            for skill in skills:
                click.echo(
                    f"{skill.id:<30} {skill.name:<20} {skill.version:<10} "
                    f"{'yes' if skill.enabled else 'no':<8} {skill.origin:<10}"
                )

            click.echo(f"\nTotal: {len(skills)} Skills")

    except Exception as e:
        click.secho(f"Error listing Skills: {e}", fg="red", err=True)
        sys.exit(1)


@skills_group.command(name="enable", help="Enable a Skill")
@click.argument("skill_id", required=True)
@click.option("--confirm", is_flag=True, default=False, help="Skip confirmation prompt")
def enable_skill(skill_id: str, confirm: bool):
    """
    Enable a Skill.

    If the Skill is marked `origin=community`, prompts for consent (ADR-0543).
    Emits audit event: SKILL_ENABLED (immutable, hash-chained)

    Compliance:
    - GDPR Art. 32 (security): Gated by consent + path-gate (L10)
    - EU AI Act Art. 50 (disclosure): Operator disclosure logged
    """
    try:
        registry = get_skill_registry()
        skill = registry.get_skill(skill_id)

        if not skill:
            click.secho(f"Skill not found: {skill_id}", fg="red", err=True)
            sys.exit(1)

        # Check origin: community Skills require explicit consent
        if skill.origin == "community" and not confirm:
            click.secho(
                f"\n⚠️  WARNING: Community Skill '{skill_id}'\n"
                f"   Origin: {skill.origin} (not reviewed by Corvin team)\n"
                f"   Risks: prompt injection, malicious config, unauthorized composition\n\n"
                f"Enable this Skill? [y/N]: ",
                fg="yellow",
                nl=False
            )
            response = input().strip().lower()
            if response != "y":
                click.echo("Cancelled.")
                sys.exit(0)

        registry.enable_skill(skill_id)
        click.secho(f"✓ Enabled: {skill_id}", fg="green")

    except Exception as e:
        click.secho(f"Error enabling Skill: {e}", fg="red", err=True)
        sys.exit(1)


@skills_group.command(name="disable", help="Disable a Skill")
@click.argument("skill_id", required=True)
@click.option("--confirm", is_flag=True, default=False, help="Skip confirmation prompt")
def disable_skill(skill_id: str, confirm: bool):
    """
    Disable a Skill.

    Disabled Skills are still installed but not loaded/executed.
    Emits audit event: SKILL_DISABLED (immutable, hash-chained)

    Note: Compliance/security meta-Skills cannot be disabled (fail-closed).
    """
    try:
        registry = get_skill_registry()
        skill = registry.get_skill(skill_id)

        if not skill:
            click.secho(f"Skill not found: {skill_id}", fg="red", err=True)
            sys.exit(1)

        # Prevent disabling meta-Skills (compliance, security, audit)
        if skill.id.startswith("os.meta_"):
            click.secho(
                f"Cannot disable meta-Skill: {skill_id} (compliance/security critical)",
                fg="red",
                err=True
            )
            sys.exit(1)

        if not confirm:
            click.secho(f"Disable Skill '{skill_id}'? [y/N]: ", nl=False)
            response = input().strip().lower()
            if response != "y":
                click.echo("Cancelled.")
                sys.exit(0)

        registry.disable_skill(skill_id)
        click.secho(f"✓ Disabled: {skill_id}", fg="green")

    except Exception as e:
        click.secho(f"Error disabling Skill: {e}", fg="red", err=True)
        sys.exit(1)


@skills_group.command(name="config", help="Configure a Skill")
@click.argument("skill_id", required=True)
@click.option(
    "--set",
    multiple=True,
    help="Set config parameter (e.g., --set learning_rate=0.85)"
)
@click.option(
    "--show",
    is_flag=True,
    default=False,
    help="Show current config"
)
def configure_skill(skill_id: str, set: List[str], show: bool):
    """
    Configure Skill parameters.

    Examples:
        corvin skills config os.delegation_router --show
        corvin skills config os.delegation_router --set confidence_threshold=0.75
        corvin skills config os.context_adapter --set context_window=8192
        corvin skills config os.context_adapter --set learning_rate=0.9

    Compliance: All config changes audit-logged (SKILL_CONFIG_UPDATED)
    """
    try:
        registry = get_skill_registry()
        skill = registry.get_skill(skill_id)

        if not skill:
            click.secho(f"Skill not found: {skill_id}", fg="red", err=True)
            sys.exit(1)

        if show:
            # Display current config
            click.echo(f"Config for {skill_id}:")
            click.echo(json.dumps(skill.config or {}, indent=2))

        if set:
            # Update config parameters
            for param in set:
                if "=" not in param:
                    click.secho(f"Invalid parameter format: {param} (expected: key=value)", fg="red", err=True)
                    sys.exit(1)

                key, value = param.split("=", 1)

                # Try to parse value as JSON (int, float, bool, null)
                try:
                    parsed_value = json.loads(value)
                except json.JSONDecodeError:
                    # Treat as string if JSON parsing fails
                    parsed_value = value

                registry.update_skill_config(skill_id, key, parsed_value)
                click.secho(f"✓ Set {key}={parsed_value}", fg="green")

    except Exception as e:
        click.secho(f"Error configuring Skill: {e}", fg="red", err=True)
        sys.exit(1)


@skills_group.command(name="show", help="Show Skill details")
@click.argument("skill_id", required=True)
@click.option(
    "--audit-trail",
    is_flag=True,
    default=False,
    help="Show recent audit events for this Skill"
)
@click.option(
    "--metrics",
    is_flag=True,
    default=False,
    help="Show learning metrics (convergence, confidence)"
)
def show_skill(skill_id: str, audit_trail: bool, metrics: bool):
    """
    Show details about a Skill.

    Includes: manifest, current config, enable/disable status, audit trail (if requested),
    learning metrics (if available in Phase 2a+).

    Compliance: Audit trail only shows sanitized events (no PII, LoM-bound)
    """
    try:
        registry = get_skill_registry()
        skill = registry.get_skill(skill_id)

        if not skill:
            click.secho(f"Skill not found: {skill_id}", fg="red", err=True)
            sys.exit(1)

        # Display basic info
        click.echo(f"\n{'='*60}")
        click.echo(f"Skill: {skill.id}")
        click.echo(f"Name: {skill.name}")
        click.echo(f"Version: {skill.version}")
        click.echo(f"Origin: {skill.origin}")
        click.echo(f"Status: {'Enabled' if skill.enabled else 'Disabled'}")
        click.echo(f"Description: {skill.description}")
        click.echo(f"{'='*60}\n")

        # Config
        if skill.config:
            click.echo("Config:")
            click.echo(json.dumps(skill.config, indent=2))
            click.echo()

        # Audit trail (if requested)
        if audit_trail:
            click.echo("Recent Audit Events:")
            try:
                events = registry.get_audit_trail(skill_id, limit=10)
                for event in events:
                    click.echo(f"  {event['timestamp']}: {event['event_type']} ({event.get('reason', 'N/A')})")
            except Exception as e:
                click.echo(f"  (Audit trail unavailable: {e})")
            click.echo()

        # Learning metrics (if available)
        if metrics:
            click.echo("Learning Metrics:")
            try:
                metric_data = registry.get_learning_metrics(skill_id)
                if metric_data:
                    click.echo(json.dumps(metric_data, indent=2))
                else:
                    click.echo("  (No learning data yet; Phase 2a+ will populate this)")
            except Exception as e:
                click.echo(f"  (Metrics unavailable: {e})")
            click.echo()

    except Exception as e:
        click.secho(f"Error showing Skill: {e}", fg="red", err=True)
        sys.exit(1)


def get_skill_registry() -> "SkillRegistry":
    """
    Get the global Skill registry instance.

    Mocked in Phase 1 (will return real registry once Phase 2a builds it).
    """
    # TODO: Wire to real SkillRegistry once available
    # For Phase 1, return a mock that demonstrates the API shape
    if SkillRegistry is None:
        # Return mock registry
        click.secho(
            "⚠️  Warning: Skill registry not yet available (Phase 2a in progress)",
            fg="yellow",
            err=True
        )
        return MockSkillRegistry()
    return SkillRegistry()


class MockSkillRegistry:
    """Mock Skill registry for Phase 1 (before Phase 2a is built)."""

    def list_skills(self) -> List[SkillSummary]:
        """Return mock Skills."""
        return [
            SkillSummary(
                id="os.delegation_router",
                name="Delegation Router",
                version="0.1.0",
                enabled=False,  # Not yet enabled; Phase 2a will enable
                description="Routes tasks to appropriate engine based on complexity",
                origin="builtin",
                confidence=None
            ),
            SkillSummary(
                id="os.context_adapter",
                name="Context Adapter",
                version="0.1.0",
                enabled=False,
                description="Adapts context based on agent type and learns from feedback",
                origin="builtin",
                confidence=None
            ),
        ]

    def get_skill(self, skill_id: str) -> Optional[SkillSummary]:
        """Get a single Skill."""
        for skill in self.list_skills():
            if skill.id == skill_id:
                return skill
        return None

    def enable_skill(self, skill_id: str) -> None:
        click.secho(f"[MOCK] Enabling {skill_id}", fg="gray")

    def disable_skill(self, skill_id: str) -> None:
        click.secho(f"[MOCK] Disabling {skill_id}", fg="gray")

    def update_skill_config(self, skill_id: str, key: str, value: Any) -> None:
        click.secho(f"[MOCK] Updating {skill_id}.{key} = {value}", fg="gray")

    def get_audit_trail(self, skill_id: str, limit: int = 10) -> List[Dict]:
        return [
            {
                "timestamp": datetime.now().isoformat(),
                "event_type": "SKILL_ENABLED",
                "reason": "Phase 1 testing"
            }
        ]

    def get_learning_metrics(self, skill_id: str) -> Optional[Dict]:
        return None  # No learning metrics yet; Phase 2a will add this


# Register the skills group with the main CLI
def register_skills_cli(cli_group):
    """Register the skills CLI subcommand with the main Corvin CLI."""
    cli_group.add_command(skills_group)


if __name__ == "__main__":
    skills_group()
