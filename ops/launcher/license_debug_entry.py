"""Entry-point shim for corvin-license-debug (ADR-0154 OTA diagnostics).

Shim needed because operator/ shadows the Python stdlib 'operator' module name;
we put the license + shared dirs on sys.path before importing the CLI module.
"""
import os
import sys


def main() -> None:
    try:
        from corvin_console._operator_bootstrap import ensure_operator_on_path
        ensure_operator_on_path()
    except ImportError:
        pass

    base = os.path.dirname(os.path.abspath(__file__))
    # operator/ itself MUST be on the path: validator.py uses package-relative
    # imports (`from .limits import ...`), so it resolves ONLY as the package
    # `license.validator`, which needs operator/ (the parent of license/) on the
    # path. Without it _load_license_quietly() silently fails to import the
    # validator and the CLI reports tier=free on a paid install — masking the
    # very tier this diagnostic exists to surface (review MEDIUM). Mirrors
    # shard_verifier._shared_on_path (here.parents[1] == operator/).
    _operator = os.path.normpath(os.path.join(base, "..", "..", "operator"))
    _license = os.path.join(_operator, "license")
    _shared = os.path.join(_operator, "bridges", "shared")
    _forge = os.path.join(_operator, "forge", "forge")
    for p in (_operator, _license, _shared, _forge):
        if p not in sys.path:
            sys.path.insert(0, p)
    from license_debug_cli import main as _main  # type: ignore[import-untyped]
    raise SystemExit(_main())
