"""PackageBuilder audit records go through the core writer, content-free, fail-closed.

Until now ``_write_audit_event`` read the chain's last line and appended its own sha256-linked JSON
line to ``tenant_audit_chain()``. The chain is HMAC-linked by ``security_events.write_event``; a line
that writer did not produce fails verification (the live 'mac_tampered' signature).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.plugins.plugin_builder.build_system import PackageBuilder, PackageMetadata
from core.plugins.plugin_builder.build_system.builder import BuildConfig


def _builder(tmp_path: Path) -> PackageBuilder:
    return PackageBuilder(
        tmp_path / "plugin", BuildConfig(tenant_id="_default", skill_id="plugin-test"),
        PackageMetadata(name="test-plugin", version="0.1.0", description="d"),
    )


def _chain() -> Path:
    return Path(os.environ["VOICE_AUDIT_PATH"])


def test_build_events_land_on_the_chain_and_it_still_verifies(tmp_path):
    from corvin_operator.forge.forge import security_events as se

    (tmp_path / "plugin").mkdir()
    b = _builder(tmp_path)
    ids = [
        b._write_audit_event("build_started", {"audit_id": "a1", "manifest_id": "plugin-x-ADR-0001",
                                               "version": "0.1.0"}),
        b._write_audit_event("build_failed", {"audit_id": "a1", "reason": "Manifest validation failed",
                                              "issues": ["SENTINEL_ISSUE_TEXT", "other"],
                                              "error": "SENTINEL_EXCEPTION_TEXT",
                                              "stderr": "SENTINEL_STDERR"}),
    ]
    assert len(set(ids)) == 2
    text = _chain().read_text()
    recs = [json.loads(l) for l in text.splitlines() if l.strip()]
    mine = [r for r in recs if r.get("event_type") == "plugin_builder.build"]
    assert [r["details"]["stage"] for r in mine] == ["build_started", "build_failed"]
    assert mine[1]["details"]["issue_count"] == 2
    for sentinel in ("SENTINEL_ISSUE_TEXT", "SENTINEL_EXCEPTION_TEXT", "SENTINEL_STDERR"):
        assert sentinel not in text
    assert se.verify_chain(_chain())[0] is True


def test_an_uncommitted_record_fails_closed(tmp_path):
    (tmp_path / "plugin").mkdir()
    b = _builder(tmp_path)
    with patch("audit.audit_event", return_value=False):
        with pytest.raises(RuntimeError, match="not committed"):
            b._write_audit_event("build_started", {"audit_id": "a1"})
