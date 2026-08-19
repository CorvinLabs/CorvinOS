"""CLI commands for skill management."""

import click
import json
from pathlib import Path
from typing import Optional

from core.skill_management.directory_init import init_tenant_skills
from core.skill_management.migrator import migrate_skills
from core.skill_management.validator import MetadataValidator, DependencyValidator
from core.skill_management.resolver import SkillDependencyResolver


@click.group("skill", help="Skill management commands")
def skill_group():
    """Skill management CLI."""
    pass


@skill_group.command("list")
@click.option("--tenant", default="_default", help="Tenant ID")
@click.option("--scope", type=click.Choice(["all", "_platform", "_shared", "_local"]), default="all", help="Filter by scope")
@click.option("--format", type=click.Choice(["text", "json", "table"]), default="table", help="Output format")
def list_skills(tenant: str, scope: str, format: str):
    """List all skills in a tenant."""
    from core.skill_management.resolver import SkillDependencyResolver

    resolver = SkillDependencyResolver(tenant)

    scopes = ["_platform", "_shared", "_local"] if scope == "all" else [scope]
    skills = []

    tenant_path = Path.home() / ".corvin" / "tenants" / tenant
    for s in scopes:
        skills_dir = tenant_path / s / "skills"
        if not skills_dir.exists():
            continue

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and not skill_dir.name.startswith('.'):
                meta_path = skill_dir / "meta.json"
                if meta_path.exists():
                    with open(meta_path) as f:
                        meta = json.load(f)
                    skills.append({
                        "id": meta["id"],
                        "version": meta["version"],
                        "scope": s,
                        "created": meta.get("created", "unknown")
                    })

    if format == "json":
        click.echo(json.dumps(skills, indent=2))
    elif format == "table":
        if not skills:
            click.echo("No skills found.")
        else:
            click.echo(f"{'ID':<40} {'Version':<10} {'Scope':<10} {'Created':<20}")
            click.echo("-" * 80)
            for skill in skills:
                created = skill["created"][:10] if skill["created"] != "unknown" else "unknown"
                click.echo(f"{skill['id']:<40} {skill['version']:<10} {skill['scope']:<10} {created:<20}")
    else:  # text
        for skill in skills:
            click.echo(f"{skill['scope']}/{skill['id']}@{skill['version']}")


@skill_group.command("info")
@click.argument("skill-id")
@click.option("--tenant", default="_default", help="Tenant ID")
@click.option("--scope", default="_shared", help="Skill scope")
def skill_info(skill_id: str, tenant: str, scope: str):
    """Show skill metadata and dependencies."""
    meta_path = Path.home() / ".corvin" / "tenants" / tenant / scope / "skills" / skill_id / "meta.json"

    if not meta_path.exists():
        click.echo(f"❌ Skill not found: {scope}/{skill_id}", err=True)
        return

    with open(meta_path) as f:
        metadata = json.load(f)

    click.echo(f"\n📦 {metadata['id']}@{metadata['version']}")
    click.echo(f"Scope: {metadata['scope']}")
    click.echo(f"Created: {metadata.get('created', 'unknown')}")
    click.echo(f"Modified: {metadata.get('last_modified', 'unknown')}")

    if metadata.get("dependencies"):
        click.echo(f"\nDependencies ({len(metadata['dependencies'])}):")
        for dep in metadata["dependencies"]:
            min_ver = dep.get("min_version", "any")
            click.echo(f"  - {dep['scope']}/{dep['id']} ≥{min_ver}")
    else:
        click.echo("\nNo dependencies")

    if metadata.get("tags"):
        click.echo(f"\nTags: {', '.join(metadata['tags'])}")


@skill_group.command("validate")
@click.option("--tenant", default="_default", help="Tenant ID")
@click.option("--scope", type=click.Choice(["all", "_platform", "_shared", "_local"]), default="_shared", help="Scope to validate")
@click.option("--fix", is_flag=True, help="Auto-fix issues if possible")
def validate_skills(tenant: str, scope: str, fix: bool):
    """Validate skill metadata and dependencies."""
    validator = MetadataValidator(tenant)
    dep_validator = DependencyValidator(tenant)

    scopes = ["_platform", "_shared", "_local"] if scope == "all" else [scope]
    total_errors = 0
    total_warnings = 0

    for s in scopes:
        click.echo(f"\n🔍 Validating {s}...")
        results = validator.validate_all_skills(s)

        if not results:
            click.echo(f"  (no skills in {s})")
            continue

        for skill_id, result in results.items():
            status = "✅" if result.valid else "❌"
            click.echo(f"  {status} {skill_id}")

            if result.errors:
                total_errors += len(result.errors)
                for err in result.errors:
                    click.echo(f"     ERROR: {err.field}: {err.error}")

            if result.warnings:
                total_warnings += len(result.warnings)
                for warn in result.warnings:
                    click.echo(f"     ⚠️  {warn}")

        # Check for circular deps
        cycles = dep_validator.validate_circular_dependencies()
        if cycles:
            click.echo(f"\n  🔴 Circular dependencies found:")
            for cycle in cycles:
                click.echo(f"     {' -> '.join(cycle)}")
                total_errors += 1

    click.echo(f"\n📊 Summary: {total_errors} errors, {total_warnings} warnings")

    if total_errors == 0:
        click.echo("✅ All validations passed!")
    else:
        raise click.ClickException(f"Validation failed with {total_errors} errors")


@skill_group.command("deps")
@click.argument("skill-id")
@click.option("--tenant", default="_default", help="Tenant ID")
@click.option("--scope", default="_shared", help="Skill scope")
@click.option("--graph", is_flag=True, help="Output as JSON graph")
def show_dependencies(skill_id: str, tenant: str, scope: str, graph: bool):
    """Show dependency tree for a skill."""
    resolver = SkillDependencyResolver(tenant)

    if graph:
        # Export as JSON
        graph_data = resolver.build_dependency_graph_json(scope)
        click.echo(json.dumps(graph_data, indent=2))
    else:
        # Tree view
        result = resolver.resolve(skill_id, scope)

        if result.error:
            click.echo(f"❌ Error: {result.error}", err=True)
            return

        click.echo(f"\n📊 Dependency tree for {scope}/{skill_id}:\n")
        for i, skill in enumerate(result.resolved_skills):
            indent = "  " * (i % 3)
            click.echo(f"{indent}├── {skill.id}@{skill.version}")


@skill_group.command("migrate")
@click.option("--tenant", default="_default", help="Tenant ID")
@click.option("--dry-run", is_flag=True, help="Preview migration without changes")
@click.option("--confirm", is_flag=True, help="Execute migration")
@click.option("--rollback", is_flag=True, help="Rollback to backup")
def migrate(tenant: str, dry_run: bool, confirm: bool, rollback: bool):
    """Migrate skills from ~/.claude/ to tenant structure."""
    if dry_run:
        click.echo("🔍 Pre-migration validation (dry-run)...\n")

        from core.skill_management.directory_init import SkillDirectoryInitializer
        initializer = SkillDirectoryInitializer(tenant)
        validation = initializer.validate_structure()

        if all(validation.values()):
            click.echo("✅ Tenant structure is ready")
        else:
            click.echo("⚠️  Tenant structure needs initialization:")
            for dir_name, exists in validation.items():
                status = "✅" if exists else "❌"
                click.echo(f"   {status} {dir_name}")
            return

        # Check source
        source_dir = Path.home() / ".claude" / "skills"
        if source_dir.exists():
            skill_count = len([d for d in source_dir.iterdir() if d.is_dir()])
            click.echo(f"✅ Found {skill_count} skills to migrate")
        else:
            click.echo("ℹ️  No ~/.claude/skills/ found (already migrated?)")

        click.echo("\n✅ Safe to proceed. Run with --confirm to execute.")

    elif confirm:
        click.echo("🚀 Migrating skills...\n")

        # Initialize structure first
        from core.skill_management.directory_init import init_tenant_skills
        init_result = init_tenant_skills(tenant)
        if init_result.status != "success":
            click.echo(f"❌ Failed to initialize tenant structure", err=True)
            return

        # Migrate
        report = migrate_skills(tenant)

        click.echo(f"✅ Migrated {len(report.migrated_skills)} skills")
        if report.warnings:
            for warning in report.warnings:
                click.echo(f"⚠️  {warning}")

        if report.backup_path:
            click.echo(f"\n📦 Backup: {report.backup_path}")

        click.echo("\n✅ Migration complete!")

    elif rollback:
        click.echo("⏮️  Rollback not yet implemented. Use backup to restore manually.")

    else:
        click.echo("Use --dry-run, --confirm, or --rollback")


@skill_group.command("init")
@click.option("--tenant", default="_default", help="Tenant ID")
def init_structure(tenant: str):
    """Initialize tenant skill directory structure."""
    click.echo(f"📁 Initializing skill structure for tenant '{tenant}'...\n")

    result = init_tenant_skills(tenant)

    if result.status == "success":
        click.echo("✅ Directories created:")
        for dir_path in result.created_dirs:
            click.echo(f"   {dir_path}")
        click.echo("\n✅ Initialization complete!")
    else:
        click.echo(f"⚠️  Partial initialization: {result.errors}")


# Register group with main CLI
def register_skill_commands(cli_group):
    """Register skill commands with main CLI."""
    cli_group.add_command(skill_group)
