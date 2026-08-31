"""Entry point for the ``corvin-serve`` console script.

Equivalent to ``corvin serve`` but available as a standalone command so
users with a pip install can type::

    corvin-serve              # start on port 8765, open browser
    corvin-serve --port 9000  # custom port
    corvin-serve --no-browser # headless / server mode
"""
from __future__ import annotations

import argparse
import sys

from . import serve_backend


def main() -> None:
    p = argparse.ArgumentParser(
        prog="corvin-serve",
        description="Start the CorvinOS console locally (no Docker required).",
    )
    p.add_argument("--port", "-p", type=int, default=8765, metavar="PORT",
                   help="TCP port to listen on (default: 8765)")
    p.add_argument("--host", default="127.0.0.1", metavar="HOST",
                   help="Bind address (default: 127.0.0.1, localhost only)")
    p.add_argument("--no-browser", action="store_true",
                   help="Do not open the browser automatically")
    args = p.parse_args()

    reason, detail = serve_backend.unavailable_reason()
    if reason == "imports":
        print("  Console backend not importable (corvin_console / uvicorn missing).")
        print("  Fix:  pip install --upgrade corvinos")
        sys.exit(1)
    if reason == "spa":
        # The Python package is installed but the React SPA has not been
        # compiled. Point at the real build step — matching mount_static().
        print("  Console backend is installed, but the SPA dist is missing.")
        print("  Fix:")
        print(f"    cd {detail}")
        print("    npm install")
        print("    npm run build")
        print("  Then re-run corvin-serve.")
        sys.exit(1)

    relaunch_argv = [
        "corvin-serve", f"--port={args.port}", f"--host={args.host}",
    ] + (["--no-browser"] if args.no_browser else [])
    if serve_backend.maybe_pypi_autoupdate(relaunch_argv=relaunch_argv):
        # Windows self-update handoff in progress: a detached updater is
        # waiting for THIS process to exit before it can upgrade + relaunch.
        sys.exit(0)

    print(f"\n  CorvinOS Console")
    print(f"  Starting on http://localhost:{args.port}/console/ …")
    print(f"  Press Ctrl-C to stop.\n")

    sys.exit(serve_backend.start(
        port=args.port,
        host=args.host,
        open_browser=not args.no_browser,
    ))


if __name__ == "__main__":
    # PATH-independent launch: `python -m ops.launcher.corvin.serve_entry`.
    # Useful on Windows where `pip install` may place the `corvin-serve` script
    # in the per-user Scripts dir (%APPDATA%\Python\Python3xx\Scripts), which is
    # not on PATH by default.
    main()
