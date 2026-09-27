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
    str(_REPO / "corvin_operator" / "bridges"),   # enables `import shared` (the package)
    str(_REPO / "corvin_operator" / "forge"),     # forge.paths / security_events
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Live-install isolation (incident 2026-09-27) ──────────────────────────────
# ``corvin_operator/rag-integration/pytest.ini`` makes THIS directory the pytest
# rootdir, so the repo-root ``conftest.py`` (sandbox CORVIN_HOME / XDG_CONFIG_HOME /
# CORVIN_AUDIT_ANCHOR_KEY + the live-chain tripwire) is never loaded for it.
# Load it by path and re-export its hooks and autouse fixtures.
import importlib.util as _ilu  # noqa: E402

_root_spec = _ilu.spec_from_file_location("_corvin_root_conftest", _REPO / "conftest.py")
_root_conftest = _ilu.module_from_spec(_root_spec)
_root_spec.loader.exec_module(_root_conftest)
for _name in dir(_root_conftest):
    if _name.startswith("pytest_") or _name in (
        "_isolated_bridge_outbox", "_live_state_tripwire", "_corvin_live_isolation_env",
    ):
        globals()[_name] = getattr(_root_conftest, _name)
