"""RAG integration test path setup.

The RAG implementation modules live in ``operator/bridges/shared/`` and import
each other package-relatively (``from .rag_query_engine import ...``), so they
must be imported via the ``shared`` package — NOT as ``operator.bridges.shared.X``
(``operator`` is a stdlib module, so that dotted path can never resolve) and NOT
flat (the relative imports would break). This conftest puts ``operator/bridges``
on ``sys.path`` so ``from shared.rag_X import ...`` works, mirroring the console
runtime (corvin_console._operator_bootstrap).
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (
    str(_REPO / "operator" / "bridges"),   # enables `import shared` (the package)
    str(_REPO / "operator" / "forge"),     # forge.paths / security_events
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)
