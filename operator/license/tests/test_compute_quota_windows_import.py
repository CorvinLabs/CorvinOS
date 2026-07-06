"""Regression: compute_quota.py must import cleanly on Windows (no `fcntl`
module) — adversarial review finding.

Before this fix, `compute_quota.py` had an unconditional top-level
`import fcntl`. Unlike `forge/` and `core/console/`, `operator/license/` has
no `_wincompat` shim installer, and this module is reached from
adapter.py/chat_runtime.py/dispatcher.py/routes/compute*.py via lazy,
function-local imports with no guaranteed ordering against those other
packages' shims — so the first Windows caller to reach it hit
`ModuleNotFoundError: No module named 'fcntl'` (same bug class as the
17-module Windows sweep; this file was missed).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

_OPERATOR_ROOT = str(Path(__file__).resolve().parents[2])
if _OPERATOR_ROOT not in sys.path:
    sys.path.insert(0, _OPERATOR_ROOT)


def test_module_imports_without_a_real_fcntl_module():
    """Simulate Windows: `import fcntl` unavailable. compute_quota must
    still import and provide a working (no-op-lock) flock shim."""
    for key in list(sys.modules):
        if key == "license.compute_quota" or key.startswith("license.compute_quota"):
            del sys.modules[key]
    real_fcntl = sys.modules.pop("fcntl", None)
    try:
        with mock.patch.dict(sys.modules, {"fcntl": None}):
            # mock.patch.dict with a None value makes `import fcntl` raise
            # ImportError inside the module under test, mirroring Windows.
            import importlib
            mod = importlib.import_module("license.compute_quota")
            assert hasattr(mod.fcntl, "flock")
            assert mod.fcntl.flock(None, mod.fcntl.LOCK_EX) == 0
    finally:
        if real_fcntl is not None:
            sys.modules["fcntl"] = real_fcntl
        for key in list(sys.modules):
            if key.startswith("license.compute_quota"):
                del sys.modules[key]


def test_charge_and_check_still_works_with_shimmed_fcntl(tmp_path):
    """End-to-end: charge_and_check() must still correctly enforce the quota
    even when running under the Windows no-op flock shim (no real locking,
    but the atomic-write-based counter logic must be unaffected)."""
    for key in list(sys.modules):
        if key.startswith("license.compute_quota"):
            del sys.modules[key]
    real_fcntl = sys.modules.pop("fcntl", None)
    try:
        with mock.patch.dict(sys.modules, {"fcntl": None}):
            import importlib
            mod = importlib.import_module("license.compute_quota")
            with mock.patch("license.validator.get_limit", return_value=2), \
                 mock.patch("license.validator.active_tier", return_value="free"):
                mod.increment_and_check(tmp_path, channel="test", chat_key="k1")
                mod.increment_and_check(tmp_path, channel="test", chat_key="k1")
                with pytest.raises(mod.LicenseLimitError):
                    mod.increment_and_check(tmp_path, channel="test", chat_key="k1")
    finally:
        if real_fcntl is not None:
            sys.modules["fcntl"] = real_fcntl
        for key in list(sys.modules):
            if key.startswith("license.compute_quota"):
                del sys.modules[key]
