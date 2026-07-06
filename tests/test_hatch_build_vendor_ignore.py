"""Regression: hatch_build.py's wheel-vendoring ignore filter must exclude
`.pytest_cache/` and `.ldd/` (adversarial review finding).

pyproject.toml's own top-level wheel `exclude` list already covers these for
the non-vendored part of the package, but Hatchling's `exclude` never applies
to force-included files — the vendored `operator/*` subtrees are copied via
`shutil.copytree(..., ignore=self._ignore)`, so `_is_test_path()` is the ONLY
filter that can catch them there. `.pytest_cache`/`.ldd` used a different
literal string than the pre-existing ("tests", "test", "__pycache__") set, so
real dev/CI artifacts (confirmed present today: operator/bridges/.pytest_cache,
operator/bridges/shared/.pytest_cache, operator/bridges/.ldd/heartbeat) could
ship inside the public wheel's vendored copy.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load_hatch_build():
    spec = importlib.util.spec_from_file_location(
        "hatch_build_under_test", _REPO / "hatch_build.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


hb = _load_hatch_build()


def test_pytest_cache_dir_contents_are_excluded():
    assert hb._is_test_path(Path("bridges/.pytest_cache/CACHEDIR.TAG"))
    assert hb._is_test_path(Path("bridges/shared/.pytest_cache/v/cache/nodeids"))


def test_ldd_dir_contents_are_excluded():
    assert hb._is_test_path(Path("bridges/.ldd/heartbeat"))
    assert hb._is_test_path(Path(".ldd/heartbeats/some-uuid"))


def test_ignore_callback_skips_pytest_cache_and_ldd_entries():
    skipped = hb.VendorOperatorHook._ignore(
        "/repo/operator/bridges",
        [".pytest_cache", ".ldd", "shared", "adapter.py"],
    )
    assert ".pytest_cache" in skipped
    assert ".ldd" in skipped
    assert "shared" not in skipped
    assert "adapter.py" not in skipped


def test_real_source_packages_named_agents_teb_eci_are_not_excluded():
    """Regression guard: the new entries must not accidentally widen the
    filter to swallow legitimate source packages."""
    assert not hb._is_test_path(Path("bridges/shared/agents/claude_code.py"))
    assert not hb._is_test_path(Path("bridges/shared/teb/broker.py"))
    assert not hb._is_test_path(Path("bridges/shared/eci/dispatcher.py"))


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
