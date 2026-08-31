"""Entry-point shim for corvin-wdat-report CLI (ADR-0109 M5).

operator/ shadows the Python stdlib 'operator' module, so we cannot use a dotted
import path like operator.bridges.shared.wdat_report. This shim adds the shared
directory to sys.path and delegates to the actual implementation.
"""
import os
import sys


def main() -> None:
    # Wheel install: operator/ is vendored under corvin_console/_vendor, not
    # top-level — this puts the vendored bridges/shared on sys.path so the bare
    # `from wdat_report import main` below resolves. No-op in a source checkout.
    try:
        from corvin_console._operator_bootstrap import ensure_operator_on_path
        ensure_operator_on_path()
    except ImportError:
        pass
    _shared = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),  # ops/launcher/
        "..", "..",                                   # project root
        "operator", "bridges", "shared",
    )
    if _shared not in sys.path:
        sys.path.insert(0, os.path.normpath(_shared))
    from wdat_report import main as _main  # type: ignore[import-untyped]
    _main()
