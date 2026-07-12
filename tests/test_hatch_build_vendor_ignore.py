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


def test_voice_i18n_bundle_dir_is_vendored():
    """Regression (found 2026-07-12 verifying the 0.10.33 build): i18n.py's
    _BUNDLE_DIR resolves to _repo_root()/operator/voice/i18n (de.json,
    en.json, zh-Hans.json). Without a _VENDOR_MAP entry, EVERY wheel install
    to date shipped with no bundle files on disk at all -- i18n.t() always
    fell through its full fallback chain (exact locale -> base locale ->
    English -> literal key) to the LAST tier, so /lang, /consent and the
    welcome-greeting strings showed/spoke the raw dotted key (e.g.
    "welcome.intro") verbatim, in every language, on every real pip
    install. Never caught because dev/source-tree checkouts always find the
    file directly via the repo-relative path -- this asserts the vendor
    map entry exists so a wheel install does too."""
    sources = [src for src, _dest in hb._VENDOR_MAP]
    assert "operator/voice/i18n" in sources, (
        f"operator/voice/i18n missing from _VENDOR_MAP -- wheel installs "
        f"ship with no i18n bundle files at all. Current sources: {sources}"
    )
    # Mirror layout must be exact -- i18n.py's _BUNDLE_DIR computes its path
    # via Path(__file__).resolve().parent.parent.parent / "voice" / "i18n"
    # relative to the vendored operator/bridges/shared/i18n.py, so the
    # destination must land at .../_vendor/operator/voice/i18n exactly.
    dest = dict(hb._VENDOR_MAP)["operator/voice/i18n"]
    assert dest == "corvin_console/_vendor/operator/voice/i18n", dest


def test_voice_i18n_bundle_files_present_on_disk_for_the_vendored_source():
    """The vendor map entry alone doesn't prove the files exist -- confirm
    the real de/en/zh-Hans bundles are actually there to be copied."""
    i18n_dir = _REPO / "operator" / "voice" / "i18n"
    names = {p.name for p in i18n_dir.glob("*.json")}
    assert {"de.json", "en.json", "zh-Hans.json"}.issubset(names), names


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
