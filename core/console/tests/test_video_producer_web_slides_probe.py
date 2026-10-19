"""The panel's "web slides available" answer needs a real Chromium build, not just the playwright package."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corvin_console.routes import video_producer_api as api  # noqa: E402


def test_the_package_without_a_browser_is_not_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert api._web_slides_available() is False                      # empty browsers dir
    (tmp_path / "ffmpeg-1011").mkdir()
    assert api._web_slides_available() is False                      # some other Playwright download is not Chromium


def test_a_chromium_build_makes_it_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    (tmp_path / "chromium-1223").mkdir()
    assert api._web_slides_available() is True


def test_a_headless_shell_alone_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    (tmp_path / "chromium_headless_shell-1223").mkdir()
    assert api._web_slides_available() is True


def test_a_missing_browsers_dir_is_not_available_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "does-not-exist"))
    assert api._web_slides_available() is False


def test_without_the_package_it_is_not_available(tmp_path, monkeypatch):
    (tmp_path / "chromium-1223").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(api.importlib.util, "find_spec", lambda name, *a, **k: None if name == "playwright" else object())
    assert api._web_slides_available() is False


@pytest.mark.parametrize("platform,rel", [("linux", (".cache", "ms-playwright")), ("darwin", ("Library", "Caches", "ms-playwright"))])
def test_the_default_dir_follows_playwrights_own_rules(tmp_path, monkeypatch, platform, rel):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(api.sys, "platform", platform)
    monkeypatch.setattr(api.Path, "home", classmethod(lambda cls: tmp_path))
    assert api._playwright_browsers_dir() == tmp_path.joinpath(*rel)
