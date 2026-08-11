"""
File Permission Hardener — fail-closed write protection

All file writes checked against allowed zones. Writes outside zones rejected.
"""

import os
from pathlib import Path
from typing import Set


class PermissionError(Exception):
    """Raised when file write is outside allowed zones."""

    pass


class PermissionHardener:
    """Fail-closed file-write protection."""

    def __init__(self):
        """Initialize with default allowed zones."""
        self._allowed_zones: Set[Path] = set()
        self._initialize_default_zones()

    def _initialize_default_zones(self) -> None:
        """Set up default allowed zones."""
        corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        temp_dir = os.getenv("TMPDIR", "/tmp")

        # Add default zones
        self.allow_zone(Path(corvin_home))
        self.allow_zone(Path(temp_dir))

        # Add pytest temporary directory if in test
        if "pytest" in os.getenv("_", ""):
            self.allow_zone(Path(temp_dir) / "pytest")

    def allow_zone(self, path: Path) -> None:
        """Mark a path as allowed for writes."""
        self._allowed_zones.add(Path(path).resolve())

    def is_allowed(self, path: Path) -> bool:
        """Check if path is in allowed zone."""
        path_resolved = Path(path).resolve()

        for zone in self._allowed_zones:
            try:
                path_resolved.relative_to(zone)
                return True
            except ValueError:
                # path is not relative to zone
                continue

        return False

    def check_write(self, path: Path) -> None:
        """
        Check file write permission. Raises if not allowed.

        Fail-closed: if not in allowed zones, raise immediately.
        """
        if not self.is_allowed(path):
            raise PermissionError(
                f"File write denied (not in allowed zones): {path}\n"
                f"Allowed zones: {', '.join(str(z) for z in self._allowed_zones)}"
            )


# Global singleton
HARDENER = PermissionHardener()


def check_write(path: Path) -> None:
    """Module-level convenience function."""
    HARDENER.check_write(path)


def is_write_allowed(path: Path) -> bool:
    """Module-level convenience function."""
    return HARDENER.is_allowed(path)


def allow_zone(path: Path) -> None:
    """Module-level convenience function."""
    HARDENER.allow_zone(path)
