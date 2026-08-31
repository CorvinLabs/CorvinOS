"""Regression: the voice-config resolvers (profile / topic-memory / BYOK-vault)
must resolve to the SAME canonical ~/.config/corvin-voice location regardless of
whether XDG_CONFIG_HOME is exported.

The bug class: these three modules used to fall back to ``voice_dir()`` (a
CORVIN_HOME / tenant-home path) when XDG_CONFIG_HOME was UNSET, but used
``$XDG_CONFIG_HOME/corvin-voice`` when it WAS set. Interactive shells export
XDG_CONFIG_HOME; systemd --user services do not — so the console (XDG set) wrote
one file and the systemd bridges (XDG unset) read another. Reader != writer:
Learning/Metaphern set in the console never reached the runtime, and BYOK keys /
topic notes silently split across two stores. The fix: XDG Base Directory spec —
default to $HOME/.config when XDG_CONFIG_HOME is unset, never to voice_dir().
"""
import importlib
import os
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parent


def _resolve(monkeypatch, *, xdg, fn_name, module_name):
    if xdg is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    # Pin a CORVIN_HOME that is clearly NOT ~/.config so a voice_dir() regression
    # would resolve somewhere obviously wrong and fail the assertion.
    monkeypatch.setenv("CORVIN_HOME", "/tmp/corvin-home-regression")
    import sys
    if str(_SHARED) not in sys.path:
        sys.path.insert(0, str(_SHARED))
    mod = importlib.import_module(module_name)
    return Path(getattr(mod, fn_name)())


CASES = [
    ("profile", "_profile_path"),
    ("memory", "_memory_root"),
    ("vault", "_vault_root"),
]


@pytest.mark.parametrize("module_name,fn_name", CASES)
def test_unset_xdg_defaults_to_home_config_not_voice_dir(monkeypatch, module_name, fn_name):
    p = _resolve(monkeypatch, xdg=None, fn_name=fn_name, module_name=module_name)
    expected_root = Path(os.path.expanduser("~")) / ".config" / "corvin-voice"
    assert str(p).startswith(str(expected_root)), (
        f"{module_name}.{fn_name}() = {p} — must be under {expected_root} when "
        f"XDG_CONFIG_HOME is unset, NOT under CORVIN_HOME/voice_dir"
    )
    assert "/tmp/corvin-home-regression" not in str(p), (
        f"{module_name}.{fn_name}() leaked the tenant-home path — voice_dir() "
        f"fallback regression"
    )


@pytest.mark.parametrize("module_name,fn_name", CASES)
def test_set_xdg_is_honoured(monkeypatch, module_name, fn_name):
    p = _resolve(monkeypatch, xdg="/tmp/xdg-regression",
                 fn_name=fn_name, module_name=module_name)
    assert str(p).startswith("/tmp/xdg-regression/corvin-voice"), (
        f"{module_name}.{fn_name}() = {p} — must honour an explicit XDG_CONFIG_HOME"
    )
