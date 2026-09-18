"""Import-time audit-chain isolation for SCRIPT-style tests in this directory.

pytest runs get the same protection from ``conftest.py::_isolated_audit_chain``;
a file executed as ``python test_x.py`` never loads conftest, so the test files
that provably wrote stub-engine spans into the live chain (2026-09-19 finding —
see the conftest docstring) import this module at the top instead.

Chain only, never the whole home (see the conftest docstring for the
measurement). It only acts when nothing has redirected the chain yet: an
explicit ``VOICE_AUDIT_PATH`` is respected.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


def isolate() -> Path:
    current = os.environ.get("VOICE_AUDIT_PATH", "").strip()
    if current:
        return Path(current)
    sandbox = Path(tempfile.mkdtemp(prefix="corvin-test-audit-"))
    os.environ["VOICE_AUDIT_PATH"] = str(sandbox / "global" / "forge" / "audit.jsonl")
    atexit.register(shutil.rmtree, sandbox, True)
    return sandbox


isolate()
