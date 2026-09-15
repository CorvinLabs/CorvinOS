# conftest.py — pytest configuration for operator/forge/tests/
#
# test_mcp.py is a standalone test driver (run as: python3 test_mcp.py).
# It uses a custom @with_client() decorator that wraps test functions so they
# have no parameters at call time, but functools.wraps copies the original
# signature (fn(client, root)), causing pytest to misidentify 'client' and
# 'root' as pytest fixture names and fail with "fixture not found".
#
# The correct way to run test_mcp.py is: python3 operator/forge/tests/test_mcp.py
# or via run-all-tests.sh, which invokes it that way.
collect_ignore = ["test_mcp.py"]


# ── Never write into the operator's LIVE audit chain from a test process ─────
# The forge runner emits ``forge.tool_executed`` on every tool run through the
# default chain path (``corvin_home()/global/forge/audit.jsonl``). Without a
# redirect, this test tree appended hundreds of records to the live GDPR chain
# per run (observed 2026-09-07: 234 mac-less ``forge.tool_executed`` records in
# the live tail, written by a test process whose anchor key had been swapped
# for a throwaway one). Redirect both the chain and the anchor key to a temp
# dir for the whole session unless the caller pinned them explicitly.
import os as _os
import tempfile as _tempfile

import pytest as _pytest


@_pytest.fixture(scope="session", autouse=True)
def _isolate_audit_chain_from_live_install():
    tmp = _tempfile.mkdtemp(prefix="forge-tests-audit-")
    prev = {k: _os.environ.get(k) for k in ("VOICE_AUDIT_PATH", "CORVIN_AUDIT_ANCHOR_KEY")}
    _os.environ.setdefault("VOICE_AUDIT_PATH", _os.path.join(tmp, "audit.jsonl"))
    _os.environ.setdefault("CORVIN_AUDIT_ANCHOR_KEY", _os.path.join(tmp, "audit_anchor.key"))
    yield
    for k, v in prev.items():
        if v is None:
            _os.environ.pop(k, None)
        else:
            _os.environ[k] = v
