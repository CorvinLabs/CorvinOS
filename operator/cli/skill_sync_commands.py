"""CLI commands for GitHub skill synchronization."""

import click
import json
from pathlib import Path

from core.skill_management.github_exporter import GitHubExporter
from core.skill_management.github_importer import GitHubImporter, ConflictResolution


@click.group("skill-sync", help="GitHub skill synchronization")
def skill_sync_group():
    """Skill GitHub sync commands."""
    pass


@skill_sync_group.command("push")
@click.option("--tenant", default="_default", help="Tenant ID")
@click.option("--repo", required=True, help="GitHub repo (github:owner/repo)")
@click.option("--branch", default="main", help="Branch name")
@click.option("--dry-run", is_flag=True, help="Preview without pushing")
def sync_push(tenant: str, repo: str, branch: str, dry_run: bool):
    """Export skills to GitHub."""
    click.echo(f"🚀 Exporting skills to {repo}/{branch}...\n")

    exporter = GitHubExporter(repo, branch, tenant)

    try:
        result = exporter.export_shared_skills(dry_run=dry_run)

        if result.success:
            click.echo(f"✅ Exported {len(result.exported_skills)} skills")
            if result.exported_skills:
                for skill in result.exported_skills:
                    click.echo(f"   - {skill}")

            if dry_run:
                click.echo(f"\n📦 Tarball: {result.tarball_path}")
                click.echo(f"📝 Manifest: {result.manifest_path}")
                click.echo("\n✅ Dry-run complete. Run without --dry-run to push to GitHub.")
            else:
                click.echo(f"\n✅ Pushed to GitHub")
                click.echo(f"📝 Commit: {result.git_commit_sha[:7] if result.git_commit_sha else 'N/A'}")
        else:
            click.echo(f"❌ Export failed: {result.error}", err=True)
            raise click.Abort()

    finally:
        exporter.cleanup()


@skill_sync_group.command("pull")
@click.option("--tenant", default="_default", help="Tenant ID")
@click.option("--tarball", required=True, type=click.Path(exists=True), help="Tarball path (from export)")
@click.option("--merge", type=click.Choice(["operator_wins", "github_wins", "manual"]), default="operator_wins", help="Conflict resolution")
@click.option("--dry-run", is_flag=True, help="Preview without importing")
def sync_pull(tenant: str, tarball: str, merge: str, dry_run: bool):
    """Import skills from tarball."""
    click.echo(f"📥 Importing skills...\n")

    tarball_path = Path(tarball)
    importer = GitHubImporter(tenant)

    resolution = ConflictResolution(merge)
    result = importer.import_from_tarball(tarball_path, resolution, dry_run=dry_run)

    if result.success:
        click.echo(f"✅ Imported {len(result.imported_skills)} skills")
        if result.imported_skills:
            for skill in result.imported_skills:
                click.echo(f"   - {skill}")

        if result.conflicts:
            click.echo(f"\n⚠️  Conflicts resolved ({merge}):")
            for conflict in result.conflicts:
                click.echo(f"   - {conflict.skill_id}: {conflict.local_version} → {conflict.imported_version}")

        if dry_run:
            click.echo("\n✅ Dry-run complete. Run without --dry-run to import.")
        else:
            click.echo("\n✅ Import complete!")
    else:
        click.echo(f"❌ Import failed: {result.error}", err=True)
        raise click.Abort()


@skill_sync_group.command("configure")
@click.option("--tenant", default="_default", help="Tenant ID")
@click.option("--repo", required=True, help="GitHub repo (github:owner/repo)")
@click.option("--branch", default="main", help="Branch name")
@click.option("--enable-sync", is_flag=True, help="Enable auto-sync")
@click.option("--push-frequency", type=click.Choice(["daily", "weekly", "manual"]), default="manual", help="Push frequency")
def configure_sync(tenant: str, repo: str, branch: str, enable_sync: bool, push_frequency: str):
    """Configure GitHub sync settings."""
    click.echo(f"⚙️  Configuring GitHub sync for tenant '{tenant}'...\n")

    tenant_path = Path.home() / ".corvin" / "tenants" / tenant
    config_file = tenant_path / "config" / "github-sync.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)

    config = {
        "repo": repo,
        "branch": branch,
        "enabled": enable_sync,
        "push_frequency": push_frequency if enable_sync else "manual",
        "last_sync": None
    }

    try:
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        click.echo(f"✅ GitHub sync configured")
        click.echo(f"   Repository: {repo}")
        click.echo(f"   Branch: {branch}")
        click.echo(f"   Auto-sync: {'enabled' if enable_sync else 'disabled'}")
        if enable_sync:
            click.echo(f"   Push frequency: {push_frequency}")
    except Exception as e:
        click.echo(f"❌ Configuration failed: {str(e)}", err=True)
        raise click.Abort()


@skill_sync_group.command("status")
@click.option("--tenant", default="_default", help="Tenant ID")
def sync_status(tenant: str):
    """Show GitHub sync status."""
    tenant_path = Path.home() / ".corvin" / "tenants" / tenant
    config_file = tenant_path / "config" / "github-sync.json"

    if not config_file.exists():
        click.echo("ℹ️  GitHub sync not configured. Run: corvinOS skill-sync configure --repo github:owner/repo --enable-sync")
        return

    try:
        with open(config_file) as f:
            config = json.load(f)

        click.echo(f"📊 GitHub Sync Status\n")
        click.echo(f"Repository: {config['repo']}")
        click.echo(f"Branch: {config['branch']}")
        click.echo(f"Status: {'✅ Enabled' if config['enabled'] else '⏸️  Disabled'}")

        if config['enabled']:
            click.echo(f"Push frequency: {config['push_frequency']}")
        if config['last_sync']:
            click.echo(f"Last sync: {config['last_sync']}")

    except Exception as e:
        click.echo(f"❌ Failed to read config: {str(e)}", err=True)


def register_sync_commands(cli_group):
    """Register sync commands with main CLI."""
    cli_group.add_command(skill_sync_group)
