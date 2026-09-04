#!/usr/bin/env python3
"""
Periodic Tenant → GitHub Synchronization Script.

Runs every 5 minutes (via systemd timer or cron).
Syncs skills + config to GitHub on 'main' branch.

Usage:
  - Manual: python3 scripts/tenant_sync.py
  - Systemd: systemctl --user start corvin-sync
  - Cron: */5 * * * * python3 /path/to/scripts/tenant_sync.py
"""

import sys
import json
import logging
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(Path.home() / '.corvin' / 'tenants' / '_default' / 'sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Config
TENANT_ID = "_default"
TENANT_PATH = Path.home() / '.corvin' / 'tenants' / TENANT_ID
CONFIG_FILE = TENANT_PATH / 'github-config.json'
SYNC_STATE_FILE = TENANT_PATH / 'github-sync-state.json'
SKILLS_DIR = TENANT_PATH / 'skills'


def load_config() -> Optional[Dict[str, Any]]:
    """Load GitHub configuration."""
    if not CONFIG_FILE.exists():
        logger.warning(f"No config file at {CONFIG_FILE}")
        return None

    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return None


def sync_to_github(config: Dict[str, Any]) -> bool:
    """Sync tenant skills to GitHub."""
    repo_url = config.get('url')
    if not repo_url:
        logger.error("No GitHub URL configured")
        return False

    try:
        with tempfile.TemporaryDirectory(prefix="corvin-sync-") as tmpdir:
            tmpdir = Path(tmpdir)
            work_repo = tmpdir / "repo"

            # Clone repository
            logger.info(f"Cloning {repo_url}...")
            result = subprocess.run(
                ["git", "clone", "-b", "main", repo_url, str(work_repo)],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.error(f"Clone failed: {result.stderr[:200]}")
                return False

            # Prepare skills
            skills_out = work_repo / "skills"
            skills_out.mkdir(exist_ok=True)

            # Write manifest
            skills_list = []
            if SKILLS_DIR.exists():
                for skill_file in SKILLS_DIR.glob("*.md"):
                    skills_list.append({
                        "name": skill_file.stem,
                        "size": skill_file.stat().st_size
                    })

            manifest = {
                "tenant_id": TENANT_ID,
                "synced_at": datetime.utcnow().isoformat() + "Z",
                "skills_count": len(skills_list),
                "skills": skills_list,
                "branch": "main",
                "repo": config.get('repo'),
                "owner": config.get('owner')
            }

            manifest_file = skills_out / "manifest.json"
            with open(manifest_file, 'w') as f:
                json.dump(manifest, f, indent=2)

            # Copy skill files
            files_written = ["skills/manifest.json"]
            if SKILLS_DIR.exists():
                for skill_file in SKILLS_DIR.glob("*.md"):
                    dest = skills_out / skill_file.name
                    shutil.copy(skill_file, dest)
                    files_written.append(f"skills/{skill_file.name}")

            # Configure git
            subprocess.run(
                ["git", "-C", str(work_repo), "config", "user.email", "corvin-sync@local"],
                capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(work_repo), "config", "user.name", "Corvin Sync"],
                capture_output=True
            )

            # Stage and commit
            subprocess.run(
                ["git", "-C", str(work_repo), "add", "skills/"],
                capture_output=True,
                timeout=10
            )

            # Check if there's anything to commit
            status = subprocess.run(
                ["git", "-C", str(work_repo), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=10
            )

            commit_sha = None
            if status.stdout.strip():
                # There are changes to commit
                result = subprocess.run(
                    ["git", "-C", str(work_repo), "commit", "-m",
                     f"[Corvin Sync] Tenant {TENANT_ID}: {len(files_written)} files"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                if result.returncode != 0:
                    logger.error(f"Commit failed: {result.stderr[:100]}")
                    return False

                # Get commit SHA
                result = subprocess.run(
                    ["git", "-C", str(work_repo), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                commit_sha = result.stdout.strip()[:12]
                logger.info(f"Committed: {len(files_written)} files ({commit_sha}...)")

                # Push
                result = subprocess.run(
                    ["git", "-C", str(work_repo), "push", "origin", "main"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode != 0:
                    logger.error(f"Push failed: {result.stderr[:200]}")
                    return False

                logger.info(f"Pushed to main")

                # Create release tag
                tag_name = f"release-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
                subprocess.run(
                    ["git", "-C", str(work_repo), "tag", "-a", tag_name, "-m",
                     f"Tenant {TENANT_ID} sync\nSkills: {len(skills_list)}\nFiles: {len(files_written)}"],
                    capture_output=True,
                    timeout=10
                )
                subprocess.run(
                    ["git", "-C", str(work_repo), "push", "origin", tag_name],
                    capture_output=True,
                    timeout=30
                )
                logger.info(f"Created tag: {tag_name}")
            else:
                # No changes, just log
                logger.info(f"No changes to sync (skills already up-to-date)")
                commit_sha = "HEAD"

            # Save sync state
            sync_state = {
                "success": True,
                "repo_url": repo_url,
                "branch": "main",
                "commit": commit_sha,
                "files_written": files_written,
                "skills_synced": len(skills_list),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

            with open(SYNC_STATE_FILE, 'w') as f:
                json.dump(sync_state, f, indent=2)

            logger.info(f"✓ Sync complete: {len(skills_list)} skills, {len(files_written)} files")
            return True

    except Exception as e:
        logger.error(f"Sync exception: {e}")
        return False


def main():
    """Main entry point."""
    logger.info(f"Starting tenant sync for {TENANT_ID}")

    config = load_config()
    if not config:
        logger.warning("Skipping sync: no configuration")
        return 1

    if not config.get('auto_sync'):
        logger.info("Skipping sync: auto_sync disabled")
        return 0

    if not sync_to_github(config):
        logger.error("Sync failed")
        return 1

    logger.info(f"Sync successful")
    return 0


if __name__ == '__main__':
    sys.exit(main())
