"""CLI entry point for the mod generator (CONCEPT-0118).

Kept apart from ``mod.py`` on purpose: ``generators/__init__`` imports ``mod``, so running ``mod`` itself
with ``-m`` would execute it twice (runpy warns about exactly that). Use::

    python -m plugin_builder.generators.mod_cli --store <skill-forge root> --out <dir>
"""
from __future__ import annotations

from .mod import main

if __name__ == "__main__":
    raise SystemExit(main())
