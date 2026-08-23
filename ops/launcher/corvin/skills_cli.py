"""Operator CLI for Skill System management (Phase 8 k=2).

Commands:
  - corvin skills cache-stats [--tenant TENANT_ID]
  - corvin skills cache-clear [--tenant TENANT_ID]
  - corvin skills health
  - corvin skills circuit-breaker
"""

import click
import json
from datetime import datetime
from pathlib import Path

from core.skills.corvin_skills.resolver import SkillDependencyResolver
from core.skills.corvin_skills.hardening import SkillServiceHardening


@click.group(name="skills", help="Skill System management and diagnostics")
def skills_group():
    """Skill System operator CLI."""
    pass


@skills_group.command(name="cache-stats", help="Show cache statistics")
@click.option(
    "--tenant",
    default="_default",
    help="Tenant ID (default: _default)",
)
@click.option(
    "--format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
def cache_stats(tenant: str, format: str):
    """Display cache hit-rate, size, eviction metrics."""
    try:
        resolver = SkillDependencyResolver(tenant_id=tenant)
        stats = resolver.stats()

        if format == "json":
            click.echo(json.dumps(stats, indent=2, default=str))
        else:
            click.echo(f"Cache Statistics (tenant={tenant}):")
            click.echo(f"  Size: {stats.get('size')}/{stats.get('max_size')} entries")
            click.echo(f"  Hit Rate: {stats.get('hit_rate', 0):.1%}")
            click.echo(f"  Hits: {stats.get('hits')}, Misses: {stats.get('misses')}")
            click.echo(f"  Evictions: {stats.get('evictions')}")
            click.echo(f"  Invalidations: {stats.get('invalidations')}")

            if stats.get("hit_rate", 0) < 0.7:
                click.secho(
                    "  ⚠️  Hit rate below 70% target",
                    fg="yellow",
                )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        raise click.Abort()


@skills_group.command(name="cache-clear", help="Clear skill cache")
@click.option(
    "--tenant",
    default="_default",
    help="Tenant ID (default: _default)",
)
@click.confirmation_option(
    prompt="Clear cache? This will force reload from disk on next query."
)
def cache_clear(tenant: str):
    """Clear cache for a tenant."""
    try:
        resolver = SkillDependencyResolver(tenant_id=tenant)
        stats_before = resolver.stats()
        resolver.invalidate()

        click.secho(
            f"✓ Cleared {stats_before.get('size')} cached entries (tenant={tenant})",
            fg="green",
        )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        raise click.Abort()


@skills_group.command(name="health", help="Check overall health")
def health():
    """Check Skill System health (cache + circuit-breaker)."""
    try:
        hardening = SkillServiceHardening()
        resolver = SkillDependencyResolver()

        health_info = hardening.health_status()
        cache_stats = resolver.stats()

        # Status indicators
        cb_state = health_info["circuit_breaker"]["state"]
        hit_rate = cache_stats.get("hit_rate", 0)

        click.echo("Skill System Health:")
        click.echo(f"  Cache Hit Rate: {hit_rate:.1%}")
        click.echo(f"  Circuit Breaker: {cb_state}")

        if cb_state == "CLOSED" and hit_rate > 0.7:
            click.secho("  Status: ✓ HEALTHY", fg="green")
        elif cb_state == "OPEN":
            click.secho("  Status: ⚠️  DEGRADED (circuit breaker open)", fg="yellow")
        else:
            click.secho("  Status: ⚠️  DEGRADED (check cache hit rate)", fg="yellow")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        raise click.Abort()


@skills_group.command(name="circuit-breaker", help="Check circuit-breaker state")
def circuit_breaker():
    """Display circuit-breaker diagnostics."""
    try:
        hardening = SkillServiceHardening()
        state = hardening.circuit_breaker.state_info()

        click.echo("Circuit Breaker State:")
        click.echo(f"  State: {state['state']}")
        click.echo(f"  Failure Count: {state['failure_count']}")
        click.echo(f"  Success Count: {state['success_count']}")

        if state["last_failure_time"]:
            click.echo(f"  Last Failure: {state['last_failure_time']}")

        if state["state"] == "OPEN":
            click.secho(
                "  ⚠️  Manifest loading failing — check disk I/O and permissions",
                fg="red",
            )
        elif state["state"] == "HALF_OPEN":
            click.secho(
                "  ⚠️  Recovery in progress — monitoring next requests",
                fg="yellow",
            )
        else:
            click.secho(
                "  ✓ Healthy",
                fg="green",
            )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        raise click.Abort()


if __name__ == "__main__":
    skills_group()
