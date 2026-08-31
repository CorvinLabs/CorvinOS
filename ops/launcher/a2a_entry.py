"""Entry-point shim for the corvin-a2a CLI (L38 A2A).

operator/ shadows the Python stdlib 'operator' module, so we use a shim that
adds the voice/scripts directory to sys.path before importing corvin_a2a.
In a wheel install the operator bootstrap vendored paths are used instead.
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
    from corvin_a2a import main as _main  # type: ignore[import-untyped]
    raise SystemExit(_main())
