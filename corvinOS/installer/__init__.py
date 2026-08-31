"""Corvin universal installer package.

Pip-based, self-contained, cross-platform (Linux/macOS/Windows).
Entry point: python -m operator.installer [install|uninstall|status]
"""

import sys

__version__ = "0.1.0"


def main_install():
    """Entry point for 'corvin-install' command."""
    sys.argv = [sys.argv[0], "install"] + sys.argv[1:]
    from corvinOS.installer.__main__ import main
    main()


def main_uninstall():
    """Entry point for 'corvin-uninstall' command."""
    sys.argv = [sys.argv[0], "uninstall"] + sys.argv[1:]
    from corvinOS.installer.__main__ import main
    main()


def main_restore():
    """Entry point for 'corvin-restore' command."""
    sys.argv = [sys.argv[0], "restore"] + sys.argv[1:]
    from corvinOS.installer.__main__ import main
    main()
