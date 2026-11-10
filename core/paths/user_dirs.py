"""The platform's per-user places: the data directory that holds the managed project workspace, the user's own places and drives,
and the lists the one location rule judges a path against (ADR-2321 Decisions 7 and 10, ADR-2322 Decision 9).

Stdlib only (no third-party platform-dirs package) and Python 3.11-safe (none of the drive-listing or junction APIs that arrived in 3.12). The platform is read from
``sys.platform`` and the environment at CALL time, so tests switch platforms with ``monkeypatch``. When the platform asked for is
not the host's own (a ``win32`` fixture on Linux) the module answers with that platform's pure path flavour and never touches the
filesystem for it.

| Platform | ``data_dir()`` |
|---|---|
| Linux | ``$XDG_DATA_HOME/corvin`` (default ``~/.local/share/corvin``) |
| macOS | ``~/Library/Application Support/Corvin`` |
| Windows | ``%LOCALAPPDATA%\\Corvin`` |

The managed workspace of a tenant is ``data_dir()/projects/<key>``; ``<key>`` is an opaque ``w-<10 hex>`` (never a tenant id,
never a hint word of the path gate) stored in the tenant's ``settings.json`` (``core.projects.registry``).
"""
from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Optional

WORKSPACE_KEY_RE = re.compile(r"^w-[0-9a-f]{10}$")

_POSIX_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/boot", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64",
                   "/libx32", "/var", "/root", "/tmp", "/var/tmp", "/snap", "/nix")
_DARWIN_PREFIXES = ("/System", "/Library", "/Applications", "/private/etc", "/private/tmp", "/private/var", "/cores")
_WIN_DRIVE_PREFIXES = ("Windows", "Program Files", "Program Files (x86)", "ProgramData", "$Recycle.Bin",
                       "System Volume Information")

#: GetDriveTypeW results
_DRIVE_REMOVABLE, _DRIVE_FIXED, _DRIVE_REMOTE, _DRIVE_CDROM, _DRIVE_RAMDISK = 2, 3, 4, 5, 6


# ── platform and flavour ──────────────────────────────────────────────────────────────────────

def _plat() -> str:
    p = sys.platform
    return "win32" if p.startswith("win") else "darwin" if p == "darwin" else "linux"


def _native() -> bool:
    """The platform asked for is the host's own (only then may the filesystem be asked about it)."""
    return (_plat() == "win32") == (os.name == "nt")


def _P(s: "str | PurePath") -> PurePath:
    """A path of the asked-for platform: a real ``Path`` on the host's own platform, the pure flavour otherwise."""
    if _native():
        return Path(s)
    return PureWindowsPath(s) if _plat() == "win32" else PurePosixPath(s)


def _home() -> PurePath:
    if _plat() == "win32":
        v = os.environ.get("USERPROFILE")
        if v:
            return _P(v)
        return _P(Path.home()) if _native() else PureWindowsPath("C:\\Users\\Default")
    v = os.environ.get("HOME")
    return _P(v) if v else _P(Path.home())


def _own_user() -> str:
    try:
        import pwd  # noqa: PLC0415

        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError, AttributeError):
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def _env_dir(name: str) -> Optional[PurePath]:
    v = os.environ.get(name, "").strip()
    if v and _P(v).is_absolute():
        return _P(v)
    return None


def _fold(s: str) -> str:
    """Case-insensitive on Windows and macOS (their default filesystems fold case)."""
    return s.casefold() if _plat() in ("win32", "darwin") else s


def _below_or_equal(p: PurePath, prefix: PurePath) -> bool:
    a = [_fold(x) for x in p.parts]
    b = [_fold(x) for x in prefix.parts]
    if _plat() == "win32" and a and b:
        a[0], b[0] = a[0].rstrip("\\/"), b[0].rstrip("\\/")
    return len(a) >= len(b) and a[: len(b)] == b


def _resolved(p: PurePath) -> PurePath:
    """The resolved form of *p* (links followed, missing tail appended) - only on the host's own platform."""
    if not _native():
        return p
    try:
        return Path(os.path.realpath(str(p)))
    except (OSError, ValueError):
        return p


# ── the data directory and the managed workspace ─────────────────────────────────────────────

def data_dir() -> PurePath:
    plat = _plat()
    if plat == "win32":
        base = _env_dir("LOCALAPPDATA") or (_home() / "AppData" / "Local")
        return base / "Corvin"
    if plat == "darwin":
        return _home() / "Library" / "Application Support" / "Corvin"
    return (_env_dir("XDG_DATA_HOME") or (_home() / ".local" / "share")) / "corvin"


def managed_parent() -> PurePath:
    """``<data>/corvin/projects`` - the parent of every workspace on this OS account (never listed to a client)."""
    return data_dir() / "projects"


def new_workspace_key() -> str:
    return "w-" + secrets.token_hex(5)


def managed_projects_root(workspace_key: str) -> PurePath:
    if not isinstance(workspace_key, str) or not WORKSPACE_KEY_RE.match(workspace_key):
        raise ValueError("a workspace key is w- followed by 10 lowercase hex digits")
    return managed_parent() / workspace_key


# ── the lists the location rule judges against ───────────────────────────────────────────────

def _fixed_drive_letters() -> list[str]:
    """Letters of the fixed drives (Windows); ``["C"]`` when the platform is only simulated."""
    if os.name != "nt":
        return ["C"]
    out = []
    try:
        import ctypes  # noqa: PLC0415

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        mask = k32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                letter = chr(ord("A") + i)
                if k32.GetDriveTypeW(f"{letter}:\\") == _DRIVE_FIXED:
                    out.append(letter)
    except (OSError, AttributeError):
        pass
    return out or [os.environ.get("SystemDrive", "C:")[:1] or "C"]


def system_prefixes() -> tuple[PurePath, ...]:
    """ADR-2321 Decision 10 check 6. ``/opt``, ``/srv`` and ``/media`` are deliberately absent."""
    plat = _plat()
    if plat == "win32":
        out = [PureWindowsPath(f"{d}:\\{n}") if not _native() else Path(f"{d}:\\{n}")
               for d in _fixed_drive_letters() for n in _WIN_DRIVE_PREFIXES]
        out += [_P(r"C:\Users\Public"), _P(r"C:\Users\Default")]
        return tuple(out)
    names = _POSIX_PREFIXES + (_DARWIN_PREFIXES if plat == "darwin" else ())
    return tuple(_P(n) for n in names)


def home_roots() -> tuple[PurePath, ...]:
    """Directories whose direct children are user homes (checks 4 and 7). ``/root`` is a home too (named by the rule itself)."""
    plat = _plat()
    if plat == "win32":
        cands = [_P(r"C:\Users")]
    elif plat == "darwin":
        cands = [_P("/Users")]
    else:
        cands = [_P("/home"), _P("/var/home")]
    if _native():
        cands = [c for c in cands if os.path.isdir(str(c))]
    return tuple(cands)


def app_data_dirs() -> tuple[PurePath, ...]:
    """Application-data trees (check 8): never a project, even when their names do not start with a dot."""
    plat = _plat()
    home = _home()
    out: list[PurePath] = []
    if plat == "win32":
        for k in ("APPDATA", "LOCALAPPDATA"):
            d = _env_dir(k)
            if d is not None:
                out.append(d)
        for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            d = _env_dir(k)
            if d is not None:
                out.append(d)
        return tuple(dict.fromkeys(out))
    if plat == "darwin":
        out.append(home / "Library")
    out.append(_env_dir("XDG_CONFIG_HOME") or home / ".config")
    out.append(_env_dir("XDG_DATA_HOME") or home / ".local" / "share")
    out.append(_env_dir("XDG_STATE_HOME") or home / ".local" / "state")
    out.append(_env_dir("XDG_CACHE_HOME") or home / ".cache")
    return tuple(dict.fromkeys(out))


def platform_links() -> tuple[PurePath, ...]:
    """Links the platform itself places (tolerated by check 10): macOS ``/etc``, ``/tmp``, ``/var`` -> ``/private/...``."""
    return tuple(_P(n) for n in ("/etc", "/tmp", "/var")) if _plat() == "darwin" else ()


def own_home() -> PurePath:
    return _home()


def _below_own_media_mount(p: PurePath) -> bool:
    """Strictly below a mount point ``/run/media/<own user>/<label>`` (udisks2 mounts removable drives there; REVIEW-0062 N2)."""
    if _plat() == "win32":
        return False
    user = _own_user()
    if not user:
        return False
    base = _P("/run/media") / user
    return _below_or_equal(p, base) and len(p.parts) >= len(base.parts) + 2


def is_under_system_prefix(p: "PurePath | str") -> bool:
    """Check 6 of ADR-2321 Decision 10: the given OR the resolved form equals or lies below a listed prefix. Not refused: the own
    home's subtree when the home itself lies below a prefix (``/var/home/u``, a sandbox home under ``/tmp``), and a path strictly
    below a mount point in ``/run/media/<own user>/``."""
    given = _P(p)
    forms = [given, _resolved(given)]
    home = _home()
    homes = [home, _resolved(home)]
    prefixes = system_prefixes()
    for f in forms:
        if any(_below_or_equal(f, h) for h in homes) and any(_below_or_equal(h, x) for h in homes for x in prefixes):
            continue                                      # the own home lies below a prefix: its subtree is the user's
        if _below_own_media_mount(f):
            continue
        if any(_below_or_equal(f, x) for x in prefixes):
            return True
    return False


# ── places shown to the user (ADR-2322) ───────────────────────────────────────────────────────

def _place(kind: str, label: str, path: PurePath, note: str = "") -> dict[str, Any]:
    available = False
    if _native():
        try:
            available = os.path.isdir(str(path)) and os.access(str(path), os.R_OK | os.X_OK)
        except (OSError, ValueError):
            available = False
    return {"kind": kind, "label": label, "path": str(path), "available": available, "note": note}


def _xdg_user_dir(name: str) -> Optional[PurePath]:
    """``XDG_<NAME>_DIR`` from ``~/.config/user-dirs.dirs`` (the file xdg-user-dirs writes; ``$HOME`` expanded, nothing else)."""
    cfg = _env_dir("XDG_CONFIG_HOME") or _home() / ".config"
    try:
        text = Path(str(cfg / "user-dirs.dirs")).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    m = re.search(rf'^\s*XDG_{name}_DIR\s*=\s*"([^"\n]*)"', text, re.M)
    if not m:
        return None
    raw = m.group(1).replace("$HOME", str(_home()))
    if not raw.startswith("/"):
        return None
    d = _P(raw)
    return None if d == _home() else d


def user_places() -> list[dict[str, Any]]:
    home = _home()
    out = [_place("home", "Home", home)]
    plat = _plat()
    for kind, label, xdg, default in (("desktop", "Desktop", "DESKTOP", "Desktop"),
                                       ("documents", "Documents", "DOCUMENTS", "Documents")):
        cand = _xdg_user_dir(xdg) if plat == "linux" else None
        cand = cand or home / default
        if not _native() or os.path.isdir(str(cand)):
            out.append(_place(kind, label, cand))
    return out


def _POSIX_MOUNT_BASES(user: str) -> list[PurePath]:
    if _plat() == "darwin":
        return [_P("/Volumes")]
    return [_P("/media") / user, _P("/run/media") / user, _P("/mnt")]


def drives() -> list[dict[str, Any]]:
    """Mounted drives. Never raises: an unreadable place is returned with ``available: false``. A mapped network drive is a
    ``drive`` with note ``network`` in its drive-letter form (never a UNC place) and is NOT probed here."""
    out: list[dict[str, Any]] = []
    if _plat() == "win32":
        if os.name != "nt":
            return out
        try:
            import ctypes  # noqa: PLC0415

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            mask = k32.GetLogicalDrives()
        except (OSError, AttributeError):
            return out
        for i in range(26):
            if not mask & (1 << i):
                continue
            letter = chr(ord("A") + i)
            root = f"{letter}:\\"
            try:
                kind = k32.GetDriveTypeW(root)
            except OSError:
                kind = 0
            note = {_DRIVE_REMOVABLE: "removable", _DRIVE_REMOTE: "network", _DRIVE_CDROM: "optical"}.get(kind, "")
            if kind == _DRIVE_REMOTE:                     # never touched here: the caller probes it off-thread (T-0391)
                out.append({"kind": "drive", "label": f"{letter}:", "path": root, "available": False, "note": note})
            else:
                out.append(_place("drive", f"{letter}:", Path(root), note))
        return out
    user = _own_user()
    for base in _POSIX_MOUNT_BASES(user):
        try:
            entries = sorted(os.scandir(str(base)), key=lambda e: e.name)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            out.append({"kind": "drive", "label": PurePath(str(base)).name, "path": str(base), "available": False, "note": ""})
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            out.append(_place("drive", e.name, _P(e.path), "removable" if "media" in str(base) else ""))
    return out


# ── display ───────────────────────────────────────────────────────────────────────────────────

def display_path(p: "PurePath | str", *, managed_root: "PurePath | str | None", home: "PurePath | str") -> str:
    """ADR-2321 Decision 16: ``Corvin workspace › <rel>`` under the managed root, ``~/…`` under the home on POSIX, the OS form
    otherwise."""
    pp, hh = _P(p), _P(home)
    if managed_root is not None:
        mr = _P(managed_root)
        if _below_or_equal(pp, mr) and pp != mr:
            rel = "/".join(pp.parts[len(mr.parts):])
            return f"Corvin workspace \u203a {rel}"
    if _plat() != "win32" and _below_or_equal(pp, hh):
        rel = "/".join(pp.parts[len(hh.parts):])
        return "~" if not rel else f"~/{rel}"
    return str(pp)
