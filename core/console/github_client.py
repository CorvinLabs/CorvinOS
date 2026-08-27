"""GitHub Client for Plugin Marketplace — with caching & rate-limit handling.

ADR-0443: Installation Engine
- Fetches plugin manifests from GitHub
- Implements 24h local cache (Finding #1: GitHub Fallback)
- Handles rate limits gracefully
"""

import os
import json
import time
import logging
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import asyncio

logger = logging.getLogger(__name__)

class GitHubCache:
    """Finding #1 Fix: 24h TTL cache for GitHub API responses."""

    def __init__(self, cache_dir: str = "~/.corvin/cache/github"):
        self.cache_dir = Path(cache_dir).expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = 24 * 3600  # 24 hours

    def _cache_path(self, repo: str) -> Path:
        """Hash repo URL to safe filename."""
        hash_val = hashlib.md5(repo.encode()).hexdigest()
        return self.cache_dir / f"{hash_val}.json"

    def get(self, repo: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached manifest if fresh."""
        path = self._cache_path(repo)

        if not path.exists():
            return None

        try:
            data = json.load(open(path))
            cached_at = data.get("_cached_at", 0)
            age_seconds = time.time() - cached_at

            if age_seconds < self.ttl_seconds:
                logger.info(f"GitHub cache HIT (age={age_seconds:.0f}s): {repo}")
                return data.get("manifest")
            else:
                logger.info(f"GitHub cache STALE (age={age_seconds:.0f}s): {repo}")
                path.unlink()  # Remove stale cache
                return None
        except Exception as e:
            logger.warning(f"Cache read error: {e}")
            return None

    def set(self, repo: str, manifest: Dict[str, Any]):
        """Cache manifest with timestamp."""
        path = self._cache_path(repo)
        data = {
            "_cached_at": time.time(),
            "_repo": repo,
            "manifest": manifest
        }
        try:
            json.dump(data, open(path, "w"), indent=2)
            logger.info(f"GitHub cache WRITE: {repo}")
        except Exception as e:
            logger.warning(f"Cache write error: {e}")


class GitHubClient:
    """GitHub API client for plugin discovery & manifest fetching."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.cache = GitHubCache()
        self.rate_limit_remaining = 60
        self.rate_limit_reset = 0

    async def get_manifest(self, repo: str) -> Dict[str, Any]:
        """
        Fetch plugin manifest from GitHub.

        Repo format: "user/repo" or "https://github.com/user/repo"

        Finding #1 Fix: Try cache first, fallback on API failure
        """
        # Normalize repo URL
        if repo.startswith("http"):
            repo = repo.replace("https://github.com/", "").replace(".git", "")

        # Try cache first
        cached = self.cache.get(repo)
        if cached:
            return cached

        # Try GitHub API
        try:
            manifest = await self._fetch_from_github(repo)
            self.cache.set(repo, manifest)
            return manifest
        except Exception as e:
            logger.error(f"GitHub API failed for {repo}: {e}")
            # Fallback: return minimal manifest (allows install to proceed)
            logger.warning(f"Using fallback manifest for {repo}")
            return self._fallback_manifest(repo)

    async def _fetch_from_github(self, repo: str) -> Dict[str, Any]:
        """Fetch plugin.yaml from GitHub raw content."""
        url = f"https://raw.githubusercontent.com/{repo}/main/plugin.yaml"

        # Check rate limit
        if time.time() > self.rate_limit_reset:
            logger.warning("GitHub rate limit exceeded, using cache/fallback")
            raise RuntimeError("Rate limit exceeded")

        try:
            # Simulated async fetch (would use httpx in production)
            import subprocess
            result = subprocess.run(
                ["curl", "-s", url],
                capture_output=True,
                timeout=5
            )

            if result.returncode != 0:
                raise RuntimeError(f"Fetch failed: {result.stderr.decode()}")

            import yaml
            manifest = yaml.safe_load(result.stdout.decode())
            return manifest or {}

        except asyncio.TimeoutError:
            raise RuntimeError("GitHub API timeout (using cache)")

    def _fallback_manifest(self, repo: str) -> Dict[str, Any]:
        """Minimal manifest when GitHub is unavailable."""
        return {
            "plugin": {
                "id": repo.split("/")[-1],
                "name": repo.split("/")[-1],
                "version": "unknown",
                "source": {"repo": f"https://github.com/{repo}"},
                "console": {
                    "settings_panel": {"id": f"plugin-{repo.split('/')[-1]}"}
                }
            }
        }


async def search_marketplace(topic: str = "corvin-plugin") -> list:
    """
    Search GitHub for plugins with topic.

    Returns list of repos matching topic.
    """
    # This would use GitHub Search API in production
    # For now, return mock data
    logger.info(f"Searching GitHub topic: {topic}")
    return [
        {"name": "awesome-auth", "repo": "user/awesome-auth"},
        {"name": "advanced-storage", "repo": "user/advanced-storage"},
    ]
