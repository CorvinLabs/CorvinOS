"""Regression: the corvin-license-debug launcher must put operator/ on sys.path.

ADR-0154 round-3 review: the launcher added operator/license + .../shared +
.../forge but NOT operator/ itself. validator.py uses package-relative imports
(`from .limits import ...`), so it only resolves as the package `license.validator`
— which needs operator/ on the path. Without it, _load_license_quietly() silently
failed and the diagnostic reported tier=free on a paid install (masking the very
tier the tool exists to surface). This locks operator/ into the launcher's path set.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_OPERATOR = str((_REPO / "operator").resolve())


def _load_entry_module():
    entry_path = _REPO / "ops" / "launcher" / "license_debug_entry.py"
    spec = importlib.util.spec_from_file_location("license_debug_entry_under_test", entry_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_launcher_adds_operator_dir_so_validator_imports_as_package(monkeypatch):
    entry = _load_entry_module()
    # Stub the CLI body so main() does no real work and exits cleanly.
    import license_debug_cli  # importable via the test PYTHONPATH (operator/license)
    monkeypatch.setattr(license_debug_cli, "main", lambda argv=None: 0)
    # Remove operator/ to prove the launcher itself re-adds it.
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != _OPERATOR])
    assert _OPERATOR not in sys.path

    with pytest.raises(SystemExit):
        entry.main()

    assert _OPERATOR in sys.path, (
        "launcher must add operator/ so `import license.validator` resolves as a "
        "package; otherwise the CLI reports tier=free on paid installs"
    )
    # And the package import actually works now.
    import importlib
    v = importlib.import_module("license.validator")
    assert v.__package__ == "license"
    assert callable(v.load_license_from_env)
