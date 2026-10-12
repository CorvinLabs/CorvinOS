"""Every AuditTrail in these tests writes the tenant's CORE chain under a
scratch CORVIN_HOME (the trail no longer accepts a private chain file)."""
import pytest


@pytest.fixture(autouse=True)
def _scratch_corvin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))
    return tmp_path / "corvin_home"


def _unshadow_core_audit() -> None:
    """Defence in depth: give the top-level name ``audit`` back to the core writer.

    Until 2026-10-12 ``core/skills/os_skills/`` had no ``__init__.py``, so pytest's rootdir import
    registered THIS package as the top-level module ``audit`` (and put ``core/skills/os_skills/`` on
    sys.path); ``core.learning.event_persistence`` resolves the core hash-chain writer
    (``corvin_operator/bridges/shared/audit.py``) as exactly ``import audit`` and every learning test
    in the same session failed. With the regular package that cause is gone (guarded by
    ``tests/skills/test_no_shadowing_syspath_inserts.py``); this hook only acts if some other path
    re-creates the alias, and is a no-op otherwise.
    """
    import sys
    from pathlib import Path

    mod = sys.modules.get("audit")
    here = Path(__file__).resolve().parents[1]
    if mod is not None and Path(getattr(mod, "__file__", "") or "").resolve().parent == here:
        del sys.modules["audit"]
        # A bare ``import audit`` would find this package again via sys.path:
        # put the core writer's directory first (what corvin_console does).
        shared = here.parents[3] / "corvin_operator" / "bridges" / "shared"
        if str(shared) in sys.path:
            sys.path.remove(str(shared))
        sys.path.insert(0, str(shared))


def pytest_collection_modifyitems(session, config, items):
    _unshadow_core_audit()


@pytest.fixture(scope="package", autouse=True)
def _unshadow_after_package():
    # pytest re-imports this package's __init__ (as top-level ``audit``) when
    # it sets the package up to run, so release the name again afterwards.
    yield
    _unshadow_core_audit()
