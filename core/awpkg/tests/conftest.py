"""Test-suite conftest for AWPKG.

Provides a session-wide autouse fixture that redirects audit-chain writes to a
disposable temp file, so install/remove/inspect tests cannot pollute the real
``~/.corvin/global/forge/audit.jsonl`` (or repo-local ``.corvin/.../audit.jsonl``).
Without this, every test run leaves ``broken_chain`` entries because each
package.installed / package.removed event appends with an empty ``prev_hash``
relative to whatever was last in the live chain.

Individual tests that need to inspect the audit chain explicitly (the
``TestAuditChain`` class) still set ``VOICE_AUDIT_PATH`` themselves to a
per-test path — those overrides win because pytest re-applies env after each
test via the same try/finally pattern.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_audit_chain():
    tmp_root = Path(tempfile.mkdtemp(prefix="awpkg-test-audit-"))
    audit_path = tmp_root / "audit.jsonl"
    prev = os.environ.get("VOICE_AUDIT_PATH")
    os.environ["VOICE_AUDIT_PATH"] = str(audit_path)
    try:
        yield audit_path
    finally:
        if prev is None:
            os.environ.pop("VOICE_AUDIT_PATH", None)
        else:
            os.environ["VOICE_AUDIT_PATH"] = prev
