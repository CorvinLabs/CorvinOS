"""Regression: on Windows ``fcntl``/``resource`` don't exist, so forge's
module-level ``import fcntl`` crashed ``from forge import ...`` at import — taking
the gateway/license/compliance/console packages down (and silently disabling the
L16 audit chain where the import was wrapped in try/except).

We simulate Windows faithfully in a fresh subprocess: force ``sys.platform`` to
``win32`` and install a meta-path finder that makes ``fcntl``/``resource``
unimportable, THEN import forge. It must succeed because ``forge/__init__`` calls
``_wincompat.install()`` (which pre-registers no-op stand-ins in ``sys.modules``)
before any submodule runs its ``import fcntl``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_FORGE = _REPO / "operator" / "forge"

_SIM_WINDOWS = r"""
import sys, importlib.abc
sys.platform = "win32"                      # pretend we are on Windows
for _m in ("fcntl", "resource"):
    sys.modules.pop(_m, None)

class _Block(importlib.abc.MetaPathFinder):
    # Make the POSIX-only stdlib modules genuinely unimportable, as on Windows.
    def find_spec(self, name, path, target=None):
        if name in ("fcntl", "resource"):
            raise ImportError(f"simulated-windows: no {name}")
        return None

sys.meta_path.insert(0, _Block())
sys.path.insert(0, r"{forge}")

import forge                                  # __init__ must install the shim first
# Every forge submodule that does a bare ``import fcntl`` / ``import resource``:
from forge import paths, security_events, registry, runner, artifacts, permissions
from forge import sandbox  # noqa: F401  — bare ``import resource``
from forge.corvin_data import data_registry  # noqa: F401  — bare ``import fcntl``
# the shim is in place + the submodules imported despite no real fcntl/resource
assert "fcntl" in sys.modules and "resource" in sys.modules
assert sys.modules["fcntl"].flock(0, sys.modules["fcntl"].LOCK_EX) == 0
assert callable(paths.corvin_home)
assert hasattr(security_events, "write_event")
print("WINDOWS_IMPORT_OK")
""".replace("{forge}", str(_FORGE))

# Sibling packages with their own bare POSIX imports — must import on Windows too.
_SIM_WINDOWS_SIBLINGS = r"""
import sys, importlib.abc
sys.platform = "win32"
for _m in ("fcntl", "resource"):
    sys.modules.pop(_m, None)
class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name in ("fcntl", "resource"):
            raise ImportError(f"simulated-windows: no {name}")
        return None
sys.meta_path.insert(0, _Block())
for _p in ({paths}):
    sys.path.insert(0, _p)
import skill_forge.registry          # noqa: F401  bare fcntl → guarded fallback
import mcp_manager.catalog           # noqa: F401  bare fcntl → guarded fallback
import awpkg.audit                   # noqa: F401  bare fcntl → guarded fallback
print("SIBLINGS_IMPORT_OK")
""".replace("{paths}", ", ".join(repr(str(_REPO / p)) for p in (
    "operator/skill-forge", "operator/mcp_manager", "core/awpkg")))


def test_forge_imports_under_simulated_windows():
    proc = subprocess.run(
        [sys.executable, "-c", _SIM_WINDOWS],
        capture_output=True, text=True, timeout=60,
    )
    assert "WINDOWS_IMPORT_OK" in proc.stdout, (
        f"forge failed to import under simulated Windows.\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


def test_sibling_packages_import_under_simulated_windows():
    proc = subprocess.run(
        [sys.executable, "-c", _SIM_WINDOWS_SIBLINGS],
        capture_output=True, text=True, timeout=60,
    )
    assert "SIBLINGS_IMPORT_OK" in proc.stdout, (
        f"a sibling package failed to import under simulated Windows.\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


def test_no_os_environ_home_subscript():
    """`os.environ["HOME"]` raises KeyError on Windows (HOME is unset there;
    USERPROFILE is used). Production code must use Path.home() or
    os.environ.get("HOME"). Keep this class at zero across the repo."""
    import re as _re
    bad = []
    roots = [_REPO / "operator", _REPO / "core", _REPO / "ops", _REPO / "corvinOS"]
    pat = _re.compile(r"""os\.environ\[\s*['"]HOME['"]\s*\]""")
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            sp = str(p)
            parts = set(p.parts)
            if (parts & {"__pycache__", ".venv", "site-packages", "node_modules", "tests"}
                    or p.name.startswith("test_")):
                continue
            try:
                txt = p.read_text(encoding="utf-8")
            except OSError:
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                if pat.search(line):
                    bad.append(f"{p.relative_to(_REPO)}:{i}")
    assert not bad, (
        "os.environ['HOME'] is a KeyError on Windows — use Path.home() / "
        f"os.environ.get('HOME'): {bad}"
    )


def test_posix_uses_real_fcntl():
    # On the real (POSIX) host the shim is a no-op and the genuine module is used.
    if str(_FORGE) not in sys.path:
        sys.path.insert(0, str(_FORGE))
    import forge._wincompat as wc  # noqa: PLC0415
    import fcntl as real  # noqa: PLC0415
    wc.install()  # no-op on POSIX
    assert sys.modules["fcntl"] is real


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
