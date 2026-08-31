"""Entry-point shim for the corvin-voice CLI (ADR-0185 M5).

operator/ shadows the Python stdlib 'operator' module, so we use a shim that
adds the voice/scripts and bridges/shared directories to sys.path before
importing the actual implementation — same pattern as corvin-a2a /
corvin-wdat-report. In a wheel install the operator bootstrap vendored
paths are used instead.
"""
import os
import sys


def main() -> None:
    try:
        from corvin_console._operator_bootstrap import ensure_operator_on_path
        ensure_operator_on_path()
    except ImportError:
        pass

    _scripts = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..",
        "operator", "voice", "scripts",
    ))
    _shared = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..",
        "operator", "bridges", "shared",
    ))
    _op = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..",
        "operator",
    ))
    for p in (_scripts, _shared, _op):
        if p not in sys.path:
            sys.path.insert(0, p)
    from voice_doctor import main as _main  # type: ignore[import-untyped]
    raise SystemExit(_main())
