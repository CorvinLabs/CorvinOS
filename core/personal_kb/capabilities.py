"""Capability probe (ADR-2326 item 2, AC-O17): can this system protect a device key file?

On POSIX a key file is chmod 0600 and the mode is read back. On Windows the inherited ACL is
removed and only the current user is granted access with ``icacls``; if that fails the probe
says so and ``convenient`` mode is refused (never a silent fallback).
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

_cache: dict[str, bool] | None = None


def restrict_to_owner(p: Path) -> bool:
    try:
        if os.name == "nt":  # pragma: no cover - Windows CI
            user = os.environ.get("USERNAME", "")
            if not user:
                return False
            r = subprocess.run(["icacls", str(p), "/inheritance:r", "/grant:r", f"{user}:F"],
                               capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        os.chmod(p, 0o600)
        mode = stat.S_IMODE(os.stat(p).st_mode)
        return mode & 0o077 == 0
    except (OSError, subprocess.SubprocessError):
        return False


def probe(*, refresh: bool = False) -> dict[str, bool]:
    global _cache
    if _cache is not None and not refresh:
        return dict(_cache)
    ok = False
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "probe.key"
            p.write_bytes(b"\0" * 32)
            ok = restrict_to_owner(p)
    except OSError:
        ok = False
    _cache = {"secure_files": ok}
    return dict(_cache)


def _set_for_tests(value: dict[str, bool] | None) -> None:
    global _cache
    _cache = value
