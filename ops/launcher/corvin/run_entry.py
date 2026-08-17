"""Entry point for the ``corvin-run`` / ``corvinos-run`` console script — HEADLESS.

Starts the CorvinOS OS without the browser Console (ADR-0352 P2.3b): the compliance
boot, bridges, A2A and the API all run exactly as in ``serve`` mode, but no SPA /
HTML browser surface is mounted (``/`` answers ``{"status":"ok","ui":"headless"}``).
For servers, containers, CI, or any deployment that wants the OS but not a web UI —
the concrete "Corvin with and without the Console" of ADR-0352.

    corvinos-run              # headless on port 8765
    corvinos-run --port 9000
"""
from __future__ import annotations

import argparse
import sys

from . import serve_backend


def main() -> None:
    p = argparse.ArgumentParser(
        prog="corvinos-run",
        description="Start CorvinOS headless — OS + API + bridges, NO browser console.",
    )
    p.add_argument("--port", "-p", type=int, default=8765, metavar="PORT",
                   help="TCP port for the API/A2A surface (default: 8765)")
    p.add_argument("--host", default="127.0.0.1", metavar="HOST",
                   help="Bind address (default: 127.0.0.1)")
    args = p.parse_args()

    reason, _detail = serve_backend.unavailable_reason()
    if reason == "imports":
        print("  Backend not importable (corvin_console / uvicorn missing).")
        print("  Fix:  pip install --upgrade corvinos")
        sys.exit(1)
    # A missing SPA dist ("spa") is FINE headless — no browser surface is served.

    print("\n  CorvinOS — headless")
    print(f"  OS + API + bridges on http://{args.host}:{args.port}  (no browser console)")
    print("  Press Ctrl-C to stop.\n")
    sys.exit(serve_backend.start(
        port=args.port, host=args.host, open_browser=False, headless=True,
    ))


if __name__ == "__main__":
    main()
