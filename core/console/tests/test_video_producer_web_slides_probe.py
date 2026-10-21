"""The panel's "web slides available" answer needs the Chromium build the installed playwright launches, not just the
playwright package - and not just any Chromium directory (a build left by an older playwright, 2026-10-19).
The probe never installs anything (PLG-11, 2026-10-21): a missing build is reported as ``browser_missing`` and the
install is the operator's ``POST /video/web-slides/install`` (test_video_producer_web_slides_install_e2e.py)."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corvin_console.routes import video_producer_api as api  # noqa: E402

WANTED = ["chromium_headless_shell-1248", "chromium-1248"]
_REAL_CHROMIUM_DIRS = api._playwright_chromium_dirs  # before the autouse fixture replaces it


@pytest.fixture(autouse=True)
def _no_download(monkeypatch):
    """The probe must never start ``playwright install``: every way it could is recorded, and must stay empty."""
    started = []
    monkeypatch.setattr(api, "_start_chromium_install", lambda tenant_id: started.append(tenant_id))
    monkeypatch.setattr(api.subprocess, "run", lambda cmd, **k: started.append(cmd))
    monkeypatch.setattr(api.importlib.util, "find_spec",
                        lambda name, *a, **k: object() if name == "playwright" else None)
    monkeypatch.setattr(api, "_playwright_chromium_dirs", lambda: list(WANTED))
    return started


def test_the_package_without_a_browser_is_not_available_and_nothing_is_fetched(tmp_path, monkeypatch, _no_download):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert api._web_slides_available() is False                      # empty browsers dir
    assert api._web_slides_state() == {"available": False, "reason": "browser_missing"}
    (tmp_path / "ffmpeg-1011").mkdir()
    assert api._web_slides_available() is False                      # some other Playwright download is not Chromium
    assert not _no_download, "a probe started the Chromium download"


def test_the_build_this_playwright_launches_makes_it_available(tmp_path, monkeypatch, _no_download):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    (tmp_path / "chromium_headless_shell-1248").mkdir()
    assert api._web_slides_available() is True
    assert api._web_slides_state() == {"available": True, "reason": None}
    assert not _no_download


def test_a_build_of_an_older_playwright_is_not_available(tmp_path, monkeypatch, _no_download):
    # the 2026-10-19 host: 1243 on disk, the upgraded package launches 1248
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    (tmp_path / "chromium-1243").mkdir()
    (tmp_path / "chromium_headless_shell-1243").mkdir()
    assert api._web_slides_available() is False
    assert not _no_download


def test_without_a_readable_browsers_json_any_chromium_build_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(api, "_playwright_chromium_dirs", lambda: None)
    (tmp_path / "chromium-1223").mkdir()
    assert api._web_slides_available() is True


def test_a_missing_browsers_dir_is_not_available_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "does-not-exist"))
    assert api._web_slides_available() is False
    monkeypatch.setattr(api, "_playwright_chromium_dirs", lambda: None)
    assert api._web_slides_available() is False


def test_without_the_package_it_is_not_available(tmp_path, monkeypatch, _no_download):
    (tmp_path / "chromium_headless_shell-1248").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(api.importlib.util, "find_spec", lambda name, *a, **k: None)
    assert api._web_slides_available() is False
    assert api._web_slides_state() == {"available": False, "reason": "package_missing"}
    assert not _no_download  # nothing to install browsers for


def test_the_wanted_dirs_come_from_the_installed_package():
    pytest.importorskip("playwright")
    got = _REAL_CHROMIUM_DIRS()
    assert got and re.fullmatch(r"chromium_headless_shell-\d+", got[0]) and re.fullmatch(r"chromium-\d+", got[1]), got


@pytest.mark.parametrize("platform,rel", [("linux", (".cache", "ms-playwright")), ("darwin", ("Library", "Caches", "ms-playwright"))])
def test_the_default_dir_follows_playwrights_own_rules(tmp_path, monkeypatch, platform, rel):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(api.sys, "platform", platform)
    monkeypatch.setattr(api.Path, "home", classmethod(lambda cls: tmp_path))
    assert api._playwright_browsers_dir() == tmp_path.joinpath(*rel)
