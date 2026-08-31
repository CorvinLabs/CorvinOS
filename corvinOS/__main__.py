"""Allow ``python -m corvinOS`` as a PATH-independent fallback for Windows users.

When ``corvin-serve`` is not found in CMD because the Python Scripts directory
is not on PATH yet, users can run::

    python -m corvinOS
    python -m corvinOS serve
    python -m corvinOS serve --port 9000

``serve`` is the default sub-command when no argument is given.
"""
from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] == "serve":
        from ops.launcher.corvin.serve_entry import main as serve_main
        sys.argv = [sys.argv[0]] + args[1:]  # strip "serve" sub-command if present
        serve_main()
    else:
        from ops.launcher.corvin.cli import main as cli_main
        cli_main()


main()
