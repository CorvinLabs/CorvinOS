"""ADR-0217: the TDE orchestration tree must be reachable on WHEEL installs.

Regression guard for the 2026-07-24 review finding: `operator/orchestration`
was vendored into the wheel (hatch_build.py) but never added to
`_operator_bootstrap._OPERATOR_SUBTREES`, so `import tde.*` failed on every
pip install → `chat_runtime._tde_available()` returned False → the ADR-0217
default (TDE for all non-big-data delegation) silently degraded to ACS on the
primary distribution channel. This test proves the vendored orchestration dir
is put on sys.path so `import tde` resolves.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_BOOTSTRAP = (
    Path(__file__).resolve().parents[1]
    / "corvin_console" / "_operator_bootstrap.py"
)


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("_ob_test", _BOOTSTRAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_orchestration_in_operator_subtrees():
    ob = _load_bootstrap()
    assert "orchestration" in ob._OPERATOR_SUBTREES


def test_vendored_orchestration_lands_on_syspath(tmp_path, monkeypatch):
    ob = _load_bootstrap()
    root = tmp_path / "corvin_console"
    orch = root / "_vendor" / "operator" / "orchestration"
    (orch / "tde").mkdir(parents=True)
    (orch / "__init__.py").write_text("")
    (orch / "tde" / "__init__.py").write_text("MARK = 42\n")
    ob.__file__ = str(root / "_operator_bootstrap.py")

    monkeypatch.syspath_prepend(str(orch))  # ensure cleanup of sys.path noise
    applied = ob.ensure_operator_on_path()
    assert applied is True
    assert str(orch) in sys.path

    monkeypatch.delitem(sys.modules, "tde", raising=False)
    import tde  # resolves from the vendored copy
    assert tde.MARK == 42
    monkeypatch.delitem(sys.modules, "tde", raising=False)


def test_source_tree_is_noop(tmp_path):
    """No _vendor dir → pure no-op, sys.path untouched."""
    ob = _load_bootstrap()
    ob.__file__ = str(tmp_path / "corvin_console" / "_operator_bootstrap.py")
    before = list(sys.path)
    assert ob.ensure_operator_on_path() is False
    assert sys.path == before
