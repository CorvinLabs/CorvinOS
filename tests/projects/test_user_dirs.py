"""T-0383 / ADR-2321 Decision 7 and 10 - the platform data-directory module ``core/paths/user_dirs.py``.

Three OS env fixtures (``sys.platform`` + env monkeypatched): linux (with and without ``XDG_DATA_HOME``), darwin, win32 with
``LOCALAPPDATA``. On a host whose own OS differs from the fixture the module answers with the fixture's path flavour
(``PureWindowsPath`` for win32 on POSIX), so the Windows rules are exercised on Linux.
"""
from __future__ import annotations

import importlib.util
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from core.paths import user_dirs as U

_REPO = Path(__file__).resolve().parents[2]
_GATE = _REPO / "corvin_operator" / "voice" / "hooks" / "path_gate.py"


def _looks_protected():
    # the way the hook's own tests import it: the hooks directory on sys.path, ``import path_gate``
    sys.path.insert(0, str(_GATE.parent))
    try:
        import path_gate  # noqa: PLC0415
    finally:
        sys.path.remove(str(_GATE.parent))
    return path_gate._looks_protected


@pytest.fixture
def linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", "/home/u")
    for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(U, "_own_user", lambda: "u")
    return "linux"


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", "/Users/u")
    for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(U, "_own_user", lambda: "u")
    return "darwin"


@pytest.fixture
def win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\u")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\u\AppData\Roaming")
    for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(U, "_fixed_drive_letters", lambda: ["C", "D"])
    return "win32"


# ── AC1 data_dir per platform ──────────────────────────────────────────────

def test_data_dir_linux_default(linux):
    assert str(U.data_dir()) == "/home/u/.local/share/corvin"


def test_data_dir_linux_xdg(linux, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/data/xdg")
    assert str(U.data_dir()) == "/data/xdg/corvin"
    assert str(U.managed_parent()) == "/data/xdg/corvin/projects"


def test_data_dir_darwin(darwin):
    assert str(U.data_dir()) == "/Users/u/Library/Application Support/Corvin"


def test_data_dir_win32(win32):
    assert U.data_dir() == PureWindowsPath(r"C:\Users\u\AppData\Local\Corvin")


# ── AC2 the workspace key ──────────────────────────────────────────────────

@pytest.mark.parametrize("fx", ["linux", "darwin", "win32"])
def test_managed_root_ends_in_a_random_key(fx, request):
    request.getfixturevalue(fx)
    k1, k2 = U.new_workspace_key(), U.new_workspace_key()
    assert re.fullmatch(r"w-[0-9a-f]{10}", k1) and k1 != k2
    root = U.managed_projects_root(k1)
    assert root.parts[-2:] == ("projects", k1)
    for bad in ("_default", "../x", "w-ABCDEF0123", "w-0123", "w-0123456789/..", ""):
        with pytest.raises(ValueError):
            U.managed_projects_root(bad)


# ── AC3 no tenant id, no path-gate hint ────────────────────────────────────

@pytest.mark.parametrize("fx", ["linux", "darwin", "win32"])
def test_no_returned_path_carries_a_tenant_id_or_a_hint(fx, request):
    request.getfixturevalue(fx)
    looks = _looks_protected()
    key = U.new_workspace_key()
    paths = [U.data_dir(), U.managed_parent(), U.managed_projects_root(key), U.managed_projects_root(key) / "my-project"]
    for p in paths:
        s = str(p)
        assert "_default" not in s and "tenants" not in s, s
        assert not looks(s), f"{s} satisfies path_gate._looks_protected"


# ── AC4 system prefixes ────────────────────────────────────────────────────

@pytest.mark.parametrize("p", ["/run/media/u", "/run/media/other/Stick/x", "/etc/x", "/usr/local/x", "/tmp/x",
                               "/run/user/1000/x", "/var/lib/x", "/proc", "/nix/store/x"])
def test_system_prefix_linux_true(linux, p):
    assert U.is_under_system_prefix(PurePosixPath(p)) is True, p


@pytest.mark.parametrize("p", ["/run/media/u/Stick/x", "/home/u/etc", "/srv/data", "/opt/work", "/media/u/Stick/x",
                               "/home/u/code"])
def test_system_prefix_linux_false(linux, p):
    assert U.is_under_system_prefix(PurePosixPath(p)) is False, p


def test_system_prefix_own_home_below_a_prefix_is_exempt(linux, monkeypatch):
    monkeypatch.setenv("HOME", "/var/home/u")
    assert U.is_under_system_prefix(PurePosixPath("/var/home/u/code")) is False
    assert U.is_under_system_prefix(PurePosixPath("/var/home/other/code")) is True
    assert U.is_under_system_prefix(PurePosixPath("/var/lib/x")) is True


def test_system_prefix_sandbox_home_under_tmp(linux, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert U.is_under_system_prefix(tmp_path / "projects" / "x") is False
    assert U.is_under_system_prefix(Path("/tmp/elsewhere-than-home/x")) is True


@pytest.mark.parametrize("p,exp", [("/System/Library", True), ("/private/etc/x", True), ("/etc/x", True),
                                   ("/Library/x", True), ("/Applications/X.app", True), ("/private/var/x", True),
                                   ("/SYSTEM/x", True), ("/Users/u/Library/x", False), ("/Users/u/code", False),
                                   ("/Volumes/Stick/p", False)])
def test_system_prefix_darwin(darwin, p, exp):
    assert U.is_under_system_prefix(PurePosixPath(p)) is exp, p


@pytest.mark.parametrize("p,exp", [(r"C:\Windows\Temp", True), (r"D:\ProgramData\x", True), (r"C:\Users\Public\x", True),
                                   (r"c:\program files\x", True), (r"D:\$Recycle.Bin\x", True), (r"C:\Users\Default\x", True),
                                   (r"D:\System Volume Information", True), (r"C:\Users\u\code", False),
                                   (r"D:\work\projects", False)])
def test_system_prefix_win32(win32, p, exp):
    assert U.is_under_system_prefix(PureWindowsPath(p)) is exp, p


def test_system_prefix_lists_per_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    linux = {str(p) for p in U.system_prefixes()}
    assert linux == {"/proc", "/sys", "/dev", "/run", "/boot", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64",
                     "/libx32", "/var", "/root", "/tmp", "/var/tmp", "/snap", "/nix"}
    assert "/opt" not in linux and "/srv" not in linux and "/media" not in linux
    monkeypatch.setattr(sys, "platform", "darwin")
    darwin = {str(p) for p in U.system_prefixes()}
    assert darwin == linux | {"/System", "/Library", "/Applications", "/private/etc", "/private/tmp", "/private/var", "/cores"}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(U, "_fixed_drive_letters", lambda: ["C", "D"])
    win = {str(p) for p in U.system_prefixes()}
    want = {f"{d}:\\{n}" for d in "CD" for n in ("Windows", "Program Files", "Program Files (x86)", "ProgramData",
                                                  "$Recycle.Bin", "System Volume Information")}
    assert win == want | {r"C:\Users\Public", r"C:\Users\Default"}


# ── AC4a home roots, app-data trees, platform links ────────────────────────

def test_home_roots_app_data_platform_links_linux(linux):
    assert {str(p) for p in U.home_roots()} <= {"/home", "/var/home"}
    assert {str(p) for p in U.app_data_dirs()} == {"/home/u/.config", "/home/u/.local/share", "/home/u/.local/state",
                                                   "/home/u/.cache"}
    assert U.platform_links() == ()


def test_app_data_linux_honours_xdg(linux, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/cfg")
    monkeypatch.setenv("XDG_STATE_HOME", "/st")
    assert {"/cfg", "/st"} <= {str(p) for p in U.app_data_dirs()}


def test_home_roots_app_data_platform_links_darwin(darwin):
    assert {str(p) for p in U.home_roots()} <= {"/Users"}   # existing ones only (a Linux host has none)
    assert "/Users/u/Library" in {str(p) for p in U.app_data_dirs()}
    assert {str(p) for p in U.platform_links()} == {"/etc", "/tmp", "/var"}


def test_home_roots_app_data_platform_links_win32(win32):
    assert [str(p) for p in U.home_roots()] == [r"C:\Users"]
    assert {str(p) for p in U.app_data_dirs()} == {r"C:\Users\u\AppData\Roaming", r"C:\Users\u\AppData\Local"}
    assert U.platform_links() == ()


# ── AC5 drives() never raises ──────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="POSIX mount directories")
def test_drives_never_raises_on_missing_or_unreadable(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    media = tmp_path / "media"
    (media / "u" / "Stick").mkdir(parents=True)
    locked = tmp_path / "mnt"
    (locked / "inner").mkdir(parents=True)
    os.chmod(locked, 0)
    try:
        monkeypatch.setattr(U, "_own_user", lambda: "u")
        monkeypatch.setattr(U, "_POSIX_MOUNT_BASES", lambda user: [media / user, tmp_path / "run-media" / user, locked,
                                                                    tmp_path / "missing"])
        got = U.drives()
        assert {"kind", "label", "path", "available", "note"} <= set(got[0])
        assert any(d["label"] == "Stick" and d["available"] for d in got)
        if os.geteuid() != 0:
            assert any(d["path"] == str(locked) and d["available"] is False for d in got)
    finally:
        os.chmod(locked, stat.S_IRWXU)


def test_user_places_reads_user_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    (tmp_path / ".config").mkdir()
    (tmp_path / "Schreibtisch").mkdir()
    (tmp_path / "Documents").mkdir()
    (tmp_path / ".config" / "user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n')
    places = {p["kind"]: p for p in U.user_places()}
    assert places["home"]["path"] == str(tmp_path)
    assert places["desktop"]["path"] == str(tmp_path / "Schreibtisch")
    assert places["documents"]["path"] == str(tmp_path / "Documents")


# ── AC6 display_path ───────────────────────────────────────────────────────

def test_display_path_three_forms(linux):
    mr = PurePosixPath("/home/u/.local/share/corvin/projects/w-0123456789")
    assert U.display_path(mr / "alpha", managed_root=mr, home=PurePosixPath("/home/u")) == "Corvin workspace \u203a alpha"
    assert U.display_path(PurePosixPath("/home/u/code/alpha"), managed_root=mr, home=PurePosixPath("/home/u")) == "~/code/alpha"
    assert U.display_path(PurePosixPath("/srv/alpha"), managed_root=mr, home=PurePosixPath("/home/u")) == "/srv/alpha"


def test_display_path_windows(win32):
    assert U.display_path(PureWindowsPath(r"D:\work\alpha"), managed_root=None,
                          home=PureWindowsPath(r"C:\Users\u")) == r"D:\work\alpha"


def test_no_3_12_only_api_in_module():
    src = Path(U.__file__).read_text()
    for bad in ("os.listdrives", "isjunction", ".is_junction(", ".walk(", "platformdirs"):
        assert bad not in src, bad
