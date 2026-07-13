"""Regression: hatch_build.py's wheel-vendoring ignore filter must exclude
`.pytest_cache/` and `.ldd/` (adversarial review finding), AND (2026-07-13
adversarial release-readiness review) both the wheel's vendor-copy step and
the sdist's default file selection must ship ONLY git-tracked files.

pyproject.toml's own top-level wheel `exclude` list already covers these for
the non-vendored part of the package, but Hatchling's `exclude` never applies
to force-included files — the vendored `operator/*` subtrees are copied via
`shutil.copytree(..., ignore=self._ignore)`, so `_is_test_path()` is the ONLY
filter that can catch them there. `.pytest_cache`/`.ldd` used a different
literal string than the pre-existing ("tests", "test", "__pycache__") set, so
real dev/CI artifacts (confirmed present today: operator/bridges/.pytest_cache,
operator/bridges/shared/.pytest_cache, operator/bridges/.ldd/heartbeat) could
ship inside the public wheel's vendored copy.

A denylist of known test-file patterns is necessarily incomplete: the
published 0.10.33 wheel shipped a stray UNTRACKED audio file
(`operator/voice/scripts/Testnachricht mit Nova.`) that happened to be sitting
in a developer's working tree at build time, and the sdist -- built straight
from the raw working tree with no git-awareness at all -- picked up further
untracked scratch files the same way. `_load_git_tracked_files` /
`_tracked_dir_prefixes` / `_install_git_tracked_filter` close that gap by
making `git ls-files` the source of truth for "would actually ship", for
BOTH targets. The tests below cover that mechanism directly (unit-level,
using disposable `tmp_path` git repos) rather than driving a full wheel/sdist
build, which is exercised manually instead (see the task's verification
transcript for `uv build --wheel` / `--sdist` against the live checkout).
"""
from __future__ import annotations

import importlib.util
import subprocess
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


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def test_load_git_tracked_files_returns_none_without_a_git_checkout(tmp_path):
    """Building a wheel FROM an extracted sdist tarball has no `.git` at all --
    that must mean "skip the filter", never "nothing is tracked"."""
    assert hb._load_git_tracked_files(tmp_path) is None


def test_load_git_tracked_files_lists_only_what_git_actually_tracks(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "tracked.py").write_text("x = 1\n")
    (tmp_path / "stray_scratch.txt").write_text("scratch, never committed\n")
    _git("add", "tracked.py", cwd=tmp_path)

    tracked = hb._load_git_tracked_files(tmp_path)
    assert tracked == frozenset({"tracked.py"})


def test_tracked_dir_prefixes_covers_every_ancestor():
    prefixes = hb._tracked_dir_prefixes(frozenset({"a/b/c.py", "top.py"}))
    assert prefixes == frozenset({"a", "a/b"})


def test_ignore_callback_excludes_untracked_file_and_untracked_dir(tmp_path):
    """Direct regression for the 0.10.33 finding: an untracked file (the
    stray audio file's shape) AND an untracked directory (the
    `operator/bridges/coworktest/` shape) sitting next to real vendored
    source must both be dropped from the vendor-copy, even though neither
    matches any `_is_test_path` denylist pattern."""
    _git("init", "-q", cwd=tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "real.py").write_text("x = 1\n")
    (pkg / "Testnachricht mit Nova.").write_text("fake-audio-bytes\n")
    stray_dir = pkg / "coworktest"
    stray_dir.mkdir()
    (stray_dir / "settings.json").write_text("{}\n")
    _git("add", "pkg/real.py", cwd=tmp_path)

    tracked = hb._load_git_tracked_files(tmp_path)
    tracked_dirs = hb._tracked_dir_prefixes(tracked)

    skipped = hb.VendorOperatorHook._ignore(
        str(pkg),
        ["real.py", "Testnachricht mit Nova.", "coworktest"],
        root=tmp_path,
        tracked=tracked,
        tracked_dirs=tracked_dirs,
    )
    assert skipped == {"Testnachricht mit Nova.", "coworktest"}


def test_ignore_callback_without_tracked_kwargs_is_unchanged():
    """Backward-compat: the plain 2-positional-arg call this file's other
    tests already make (no git-tracked info at all) must keep behaving
    exactly as before -- only the denylist patterns apply."""
    skipped = hb.VendorOperatorHook._ignore(
        "/repo/operator/bridges",
        [".pytest_cache", ".ldd", "shared", "adapter.py"],
    )
    assert skipped == {".pytest_cache", ".ldd"}


def test_install_git_tracked_filter_only_tightens_never_loosens(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "real.py").write_text("x = 1\n")
    (tmp_path / "stray.py").write_text("y = 2\n")
    _git("add", "real.py", cwd=tmp_path)

    class _FakeConfig:
        def path_is_excluded(self, relative_path: str) -> bool:
            return relative_path == "already_excluded.py"

    config = _FakeConfig()
    hb._install_git_tracked_filter(config, tmp_path)

    assert config.path_is_excluded("real.py") is False
    assert config.path_is_excluded("stray.py") is True
    # A path the static exclude config already rejected stays rejected.
    assert config.path_is_excluded("already_excluded.py") is True


def test_install_git_tracked_filter_is_a_noop_without_a_git_checkout(tmp_path):
    class _FakeConfig:
        def path_is_excluded(self, relative_path: str) -> bool:
            return False

    config = _FakeConfig()
    original_bound_method = config.path_is_excluded
    hb._install_git_tracked_filter(config, tmp_path)
    # No live git checkout -- must not have patched anything at all.
    assert config.path_is_excluded == original_bound_method


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
