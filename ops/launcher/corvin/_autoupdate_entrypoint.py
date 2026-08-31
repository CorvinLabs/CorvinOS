"""Standalone auto-update check entrypoint (WA-19).

Invoked as ``ExecStartPre``/pre-exec by the Stufe-1 and Stufe-2 autostart
service managers (corvinOS/installer/service_manager.py,
corvinOS/installer/system_service_manager.py) so a newer CorvinOS release
actually reaches users who rely on autostart and never invoke the CLI
directly. A plain two-token ``<python> <this file>`` command needs no
shell quoting, unlike inlining the equivalent ``python -c "..."`` directly
into a systemd unit / launchd plist — safer across all three platforms'
differing command-line parsing.

Deliberately a thin wrapper: all real logic (PyPI check, uv-vs-pip
detection, Windows self-update handoff, convergence guard) lives in
``serve_backend.maybe_pypi_autoupdate`` and is exercised by its own tests;
duplicating none of it here.
"""
from __future__ import annotations


def main() -> None:
    from ops.launcher.corvin.serve_backend import maybe_pypi_autoupdate

    maybe_pypi_autoupdate()


if __name__ == "__main__":
    main()
