"""Entry-point shim for corvin-flow CLI (ADR-0121 M3).

operator/ shadows the Python stdlib 'operator' module, so we use a shim
that adds the shared directory to sys.path before importing flow_cli.
"""
import os
import sys


def main() -> None:
    # Wheel install: operator/ is vendored under corvin_console/_vendor, not
    # top-level — this puts the vendored bridges/shared on sys.path so the bare
    # `from flow_cli import main` below resolves. No-op in a source checkout.
    try:
        from corvin_console._operator_bootstrap import ensure_operator_on_path
        ensure_operator_on_path()
    except ImportError:
        pass
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
    for p in (_shared, _op):
        if p not in sys.path:
            sys.path.insert(0, p)
    from flow_cli import main as _main  # type: ignore[import-untyped]
    _main()
