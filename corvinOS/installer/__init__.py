"""Corvin universal installer package.

Pip-based, self-contained, cross-platform (Linux/macOS/Windows).
Entry point: python -m corvin_operator.installer [install|uninstall|status]
"""

import sys

try:
    from importlib.metadata import version as _dist_version

    __version__ = _dist_version("corvinos")
except Exception:  # noqa: BLE001 — not installed as a distribution (source checkout)
    __version__ = "0.0.0+unknown"


def main_install():
    """Entry point for 'corvin-install' command."""
    sys.argv = [sys.argv[0], "install"] + sys.argv[1:]
    from corvinOS.installer.__main__ import main
    main()


def main_uninstall():
    """Entry point for 'corvin-uninstall' command.

    On Linux/macOS a source install hands over to ``uninstall.sh`` next to the
    package: it is self-contained, backs up first, and can delete the very
    tool venv this interpreter runs from (exec replaces this process).
    Wheel installs and Windows use the Python implementation below.
    """
    import os
    import shutil
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "uninstall.sh"
    if sys.platform != "win32" and script.is_file() and shutil.which("bash"):
        args = ["--yes" if a in ("--purge", "--force", "-y") else a for a in sys.argv[1:]]
        os.execvp("bash", ["bash", str(script), *args])
    sys.argv = [sys.argv[0], "uninstall"] + sys.argv[1:]
    from corvinOS.installer.__main__ import main
    main()


def main_restore():
    """Entry point for 'corvin-restore' command."""
    sys.argv = [sys.argv[0], "restore"] + sys.argv[1:]
    from corvinOS.installer.__main__ import main
    main()
