"""Unified path resolver for A2A remote origins/endpoints (ADR-0258 canonical paths).

Solves: Path-divergence between a2a_pair.py and remote_trigger_receiver.py
Issue: Both calculated default paths independently → diverged in installed envs
Solution: Single canonical resolver, both modules defer to it
"""

import os
from pathlib import Path
from typing import Optional


def get_remote_origins_dir(override_env_var: Optional[str] = None) -> Path:
    """Get canonical path for A2A friendship origins storage.

    Resolves in order:
    1. REMOTE_ORIGINS_DIR env var (if set)
    2. Repository-relative: <repo_root>/operator/cowork/remote_origins/
    3. Fallback (installed env): ~/.corvin/remote_origins/

    Args:
        override_env_var: Optional env var name to check first (default: REMOTE_ORIGINS_DIR)

    Returns:
        Path to remote origins directory (parent created if needed)

    Raises:
        ValueError: if cannot determine path
    """
    env_var = override_env_var or "REMOTE_ORIGINS_DIR"

    # Priority 1: Explicit env var
    if env_var in os.environ:
        path = Path(os.environ[env_var])
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # Priority 2: Repository-relative (checkout environment)
    try:
        repo_root = _find_repo_root()
        if repo_root:
            path = repo_root / "operator" / "cowork" / "remote_origins"
            path.mkdir(parents=True, exist_ok=True)
            return path
    except Exception:
        pass

    # Priority 3: Home directory (installed environment)
    home_origins = Path.home() / ".corvin" / "remote_origins"
    home_origins.mkdir(parents=True, exist_ok=True)
    return home_origins


def get_remote_endpoints_dir(override_env_var: Optional[str] = None) -> Path:
    """Get canonical path for A2A peer endpoints storage.

    Same resolution as get_remote_origins_dir(), but for endpoints.

    Args:
        override_env_var: Optional env var name to check first (default: REMOTE_ENDPOINTS_DIR)

    Returns:
        Path to remote endpoints directory
    """
    env_var = override_env_var or "REMOTE_ENDPOINTS_DIR"

    # Priority 1: Explicit env var
    if env_var in os.environ:
        path = Path(os.environ[env_var])
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # Priority 2: Repository-relative
    try:
        repo_root = _find_repo_root()
        if repo_root:
            path = repo_root / "operator" / "cowork" / "remote_endpoints"
            path.mkdir(parents=True, exist_ok=True)
            return path
    except Exception:
        pass

    # Priority 3: Home directory
    home_endpoints = Path.home() / ".corvin" / "remote_endpoints"
    home_endpoints.mkdir(parents=True, exist_ok=True)
    return home_endpoints


def _find_repo_root() -> Optional[Path]:
    """Find CorvinOS repository root by walking up from this file.

    Markers: .git, CLAUDE.md, CorvinOS-specific structure

    Returns:
        Path to repo root or None
    """
    current = Path(__file__).parent.absolute()

    # Walk up max 10 levels
    for _ in range(10):
        # Check for repo markers
        if (current / ".git").exists():
            return current
        if (current / "CLAUDE.md").exists() and (current / "core").exists():
            return current
        if (current / "core" / "concurrency").exists():  # Very specific marker
            return current

        current = current.parent
        if current == current.parent:  # Reached root
            break

    return None
