"""CLI entry point for CorvinOS installer.

Usage:
  python -m operator.installer install [--yes]
  python -m operator.installer uninstall
  python -m operator.installer status
"""

import argparse
import sys

from corvinOS.installer.core import CorvinInstaller


def main():
    parser = argparse.ArgumentParser(
        description="CorvinOS universal installer",
        prog="corvin-installer",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Install command
    install_parser = subparsers.add_parser("install", help="Install CorvinOS")
    install_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive mode (use defaults)",
    )
    install_parser.add_argument(
        "--browser",
        action="store_true",
        help="Provision browser automation only (Playwright package + "
             "Chromium download) — the one-line remedy printed when the "
             "browser step failed or was never run",
    )

    # Uninstall command
    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall CorvinOS")
    uninstall_parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete all data directories without prompting (API keys, audit logs, sessions)",
    )

    # Restore command
    subparsers.add_parser(
        "restore",
        help="Force-rebuild the web console and restart all services",
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Check installation status")

    # Version
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # interactive=True only when stdin is a real TTY and --yes was not passed.
    # Piped stdin (e.g. curl | bash) would make input() return "" which the
    # `or "y"` default turns into auto-yes — treat that as non-interactive.
    interactive = sys.stdin.isatty() and not getattr(args, "yes", False)

    # `corvin-install --browser`: re-run ONLY the browser provisioning step
    # (I1, 2026-07-20). This is the remedy every browser-setup failure message
    # prints — it must not drag the user through the full wizard, and it runs
    # with the right interpreter (sys.executable = this venv's python), which
    # a bare `playwright install chromium` on the canonical uv-tool install
    # cannot (playwright is not on the user PATH there).
    if args.command == "install" and getattr(args, "browser", False):
        from corvinOS.installer.steps import browser as _browser_step
        _browser_step.ensure_browser(interactive=interactive)
        return

    installer = CorvinInstaller(interactive=interactive)

    if args.command == "install":
        installer.install()
    elif args.command == "restore":
        installer.restore()
    elif args.command == "uninstall":
        installer.uninstall(purge=getattr(args, "purge", False))
    elif args.command == "status":
        print("Status check not yet implemented")
        sys.exit(1)


if __name__ == "__main__":
    main()
