"""Sandbox for the delegate test modules when they run as plain scripts.

``run-all-tests.sh`` runs ``test_delegation.py`` & co. as ``python3 <file>``:
no conftest, so nothing isolates them unless the caller did. A run with an
unset ``CORVIN_HOME`` resolves the repo-marker home — the live install when
run from the main checkout — and the live venv's editable ``.pth`` can import
product code from a DIFFERENT checkout than the test file. On 2026-09-27 such a
run appended 34 foreign-MAC records to the live tenant chain, and the forge
MCP server (registry root from ``FORGE_ROOT`` or the git checkout) wrote into
``<checkout>/.corvin/forge/audit.jsonl``.

``enter()`` must be called BEFORE any product module is imported (several
resolve their home/chain at import time). It points every runtime root at a
fresh temp tree, removed at exit. Under pytest it is never called — the root
conftest sandboxes the session.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile


def enter() -> str:
    sandbox = tempfile.mkdtemp(prefix="corvin-delegate-script-")
    atexit.register(shutil.rmtree, sandbox, True)
    for key, sub in (("CORVIN_HOME", "corvin_home"),
                     ("XDG_CONFIG_HOME", "xdg_config"),
                     ("FORGE_ROOT", "forge_root")):
        path = os.path.join(sandbox, sub)
        os.makedirs(path, exist_ok=True)
        os.environ[key] = path
    os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = os.path.join(sandbox, "audit_anchor.key")
    # A chain redirect inherited from the caller would bypass the sandbox.
    os.environ.pop("VOICE_AUDIT_PATH", None)
    return sandbox
