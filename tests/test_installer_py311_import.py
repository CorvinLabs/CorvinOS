"""SRF-2: the installer and the maintainer CLI must import on the oldest
Python ``pyproject.toml`` admits (``requires-python >= 3.11``).

Commit 226d105f8 put ``encoding="utf-8"`` inside double-quoted f-strings
(PEP 701, 3.12+ only); on a host whose newest interpreter is 3.11 every
``corvin-install`` entry point died with ``SyntaxError`` at import. Driven
through a fresh interpreter process, the way the entry points start. The
tree-wide ratchet is ``scripts/zero_caller_sweep.py --check`` (CI runs it on
3.11)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                          capture_output=True, text=True, timeout=120, check=False)


def test_installer_core_imports_in_a_fresh_interpreter():
    r = _run("import corvinOS.installer.core, corvinOS.installer.steps.dependencies")
    assert r.returncode == 0, r.stderr


def test_maintainer_cli_compiles_in_a_fresh_interpreter():
    r = _run("compile(open("
             "'core/console/corvin_core/aco/maintainer_cli.py', encoding='utf-8').read(), "
             "'maintainer_cli.py', 'exec')")
    assert r.returncode == 0, r.stderr
