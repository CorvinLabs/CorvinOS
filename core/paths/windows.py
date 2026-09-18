"""Windows-specific path handling for CorvinOS (cross-platform compatibility).

Provides Windows-safe path normalization, environment variable handling, and
symlink/junction abstractions for cross-platform installer and runtime code.

ADR-0010: Windows path delimiter issue fix (cross-platform paths).
"""

import os
import platform
from pathlib import Path, WindowsPath, PosixPath
from typing import Union


def is_windows() -> bool:
    """Check if running on Windows."""
    return platform.system() == "Windows"


def normalize_path(path: Union[str, Path]) -> Path:
    """Normalize a path string or Path object for the current platform.

    Handles:
    - Mixed forward slashes and backslashes
    - Environment variable expansion ($HOME, %USERPROFILE%, etc.)
    - User home directory expansion (~)

    Args:
        path: String or Path to normalize

    Returns:
        Normalized Path object (cross-platform safe)
    """
    if isinstance(path, str):
        # Expand environment variables and ~
        expanded = os.path.expandvars(os.path.expanduser(path))
        # Create Path (normalizes separators for platform)
        return Path(expanded)
    return Path(path)


def get_home_dir() -> Path:
    """Get user home directory (Windows-safe).

    On Windows: uses %USERPROFILE% (C:\\Users\\user)
    On Unix: uses $HOME (/home/user)

    Returns:
        Path to user home directory
    """
    if is_windows():
        home = os.environ.get("USERPROFILE")
        if home:
            return Path(home)

    home = os.environ.get("HOME")
    if home:
        return Path(home)

    return Path.home()


def get_temp_dir() -> Path:
    """Get platform-specific temporary directory.

    On Windows: tries %TEMP%, %TMP%, falls back to %USERPROFILE%\\AppData\\Local\\Temp
    On Unix: tries $TMPDIR, $TEMP, falls back to /tmp

    Returns:
        Path to temporary directory
    """
    if is_windows():
        # Windows priority: TEMP > TMP > USERPROFILE\AppData\Local\Temp
        for env_var in ("TEMP", "TMP"):
            temp = os.environ.get(env_var)
            if temp:
                return Path(temp)
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            return Path(userprofile) / "AppData" / "Local" / "Temp"
    else:
        # Unix priority: TMPDIR > TEMP > /tmp
        for env_var in ("TMPDIR", "TEMP"):
            temp = os.environ.get(env_var)
            if temp:
                return Path(temp)
        return Path("/tmp")

    # Absolute fallback
    return Path.home() / ".temp"


def create_symlink(source: Path, link_path: Path, is_dir: bool = False) -> None:
    """Create a symlink (Unix) or junction (Windows).

    On Windows, creates a directory junction (mklink /J) which doesn't require
    admin privileges, unlike directory symlinks (mklink /D).

    On Unix, creates a regular symlink (ln -s).

    Args:
        source: Path to the target
        link_path: Path where the link should be created
        is_dir: Whether the link is for a directory

    Raises:
        OSError: If link creation fails
    """
    source = normalize_path(source)
    link_path = normalize_path(link_path)

    # Remove existing link if present
    if link_path.exists() or link_path.is_symlink():
        try:
            link_path.unlink()
        except (OSError, FileNotFoundError):
            pass

    if is_windows() and is_dir:
        # Use junction (mklink /J) instead of symlink (mklink /D)
        # Junctions don't require admin rights and work better on Windows
        import subprocess
        try:
            subprocess.run(
                ["mklink", "/J", str(link_path), str(source)],
                check=True,
                capture_output=True,
                shell=True  # Required on Windows for mklink
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise OSError(f"Failed to create junction {link_path} -> {source}: {e}")
    else:
        # Unix symlink or Windows file link
        try:
            link_path.symlink_to(source)
        except (OSError, NotImplementedError) as e:
            raise OSError(f"Failed to create symlink {link_path} -> {source}: {e}")


def join_path_segments(*segments: Union[str, Path]) -> Path:
    """Join path segments in a cross-platform way.

    Unlike string concatenation with "/" or "\\", this handles all cases:
    - Mixed separators
    - Environment variable expansion
    - None/empty segments

    Args:
        *segments: Path segments to join

    Returns:
        Joined Path (normalized for platform)
    """
    if not segments:
        return Path()

    result = normalize_path(segments[0])
    for segment in segments[1:]:
        if segment:
            result = result / str(segment)

    return result


class PlatformPath:
    """Context-aware path builder for cross-platform paths.

    Simplifies path construction without worrying about separators or platform.

    Example:
        pp = PlatformPath()
        path = pp.home / ".corvin" / "tenants" / "_default"
        # On Windows: C:\\Users\\user\\.corvin\\tenants\\_default
        # On Unix: /home/user/.corvin/tenants/_default
    """

    @property
    def home(self) -> Path:
        """User home directory."""
        return get_home_dir()

    @property
    def temp(self) -> Path:
        """Platform-specific temp directory."""
        return get_temp_dir()

    @property
    def corvin_home(self) -> Path:
        """CorvinOS runtime root: ``$CORVIN_HOME``, else repo-local ``.corvin``
        (source checkout), else ``~/.corvin``.

        Mirrors ``core/paths/tenant.py::corvin_home`` — the canonical resolver
        (see that module's docstring for why the repo-local check matters: a
        source checkout with its own ``.corvin`` is the live root, and this
        property used to skip that check, diverging from every other copy).
        """
        env_override = os.environ.get("CORVIN_HOME")
        if env_override:
            return normalize_path(env_override)
        repo_local = Path(__file__).resolve().parents[2] / ".corvin"
        if repo_local.is_dir():
            return repo_local
        return self.home / ".corvin"

    def path(self, *segments: Union[str, Path]) -> Path:
        """Build a path from segments."""
        return join_path_segments(*segments)

    @staticmethod
    def normalize(path: Union[str, Path]) -> Path:
        """Normalize a path."""
        return normalize_path(path)


# Singleton instance for convenience
platform_paths = PlatformPath()


__all__ = [
    "is_windows",
    "normalize_path",
    "get_home_dir",
    "get_temp_dir",
    "create_symlink",
    "join_path_segments",
    "PlatformPath",
    "platform_paths",
]
