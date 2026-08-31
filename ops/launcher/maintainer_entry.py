"""Entry-point shim for the corvin-maintainer CLI (ADR-0178 Tier CONTRIBUTOR)."""
import os
import sys


def main() -> None:
    try:
        from corvin_console._operator_bootstrap import ensure_operator_on_path
        ensure_operator_on_path()
    except ImportError:
        pass

    _here = os.path.dirname(os.path.abspath(__file__))
    # source-tree mode: put core/console (for corvin_console) + operator subtrees
    # on sys.path. In a wheel install corvin_console is already importable and the
    # operator bootstrap above handles the vendored subtrees.
    _console = os.path.normpath(os.path.join(_here, "..", "..", "core", "console"))
    _shared = os.path.normpath(os.path.join(_here, "..", "..", "operator", "bridges", "shared"))
    _forge = os.path.normpath(os.path.join(_here, "..", "..", "operator", "forge"))
    for p in (_console, _shared, _forge):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    from corvin_console.aco.maintainer_cli import main as _main  # type: ignore
    raise SystemExit(_main())


if __name__ == "__main__":
    main()
