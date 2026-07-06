"""Regression tests for two latent validator bugs surfaced by the ADR-0154 review.

Both are the "broken on a real condition" class — pre-existing, but live in the
validator and would silently/loudly fail on genuine license-enforcement paths.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_LIC_DIR = Path(__file__).resolve().parents[1]
if str(_LIC_DIR) not in sys.path:
    sys.path.insert(0, str(_LIC_DIR))
_SHARED = _LIC_DIR.parent / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from license import validator as V  # type: ignore  # noqa: E402


def test_audit_with_domain_field_is_written_not_dropped(tmp_path, monkeypatch):
    """review HIGH: _audit forwarded domain kwargs (tier/jti/...) straight to
    audit_event, which has no such params -> TypeError -> swallowed -> event
    DROPPED. They must now be written via details=."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(audit_path))

    V._audit("license.expired", jti="abcd1234", tier="member")
    V._audit("license.free_tier")  # kwarg-less control (always worked)

    assert audit_path.exists(), "license audit events must be written"
    events = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
    types = {e["event_type"] for e in events}
    assert "license.expired" in types, "domain-field event was silently dropped"
    assert "license.free_tier" in types
    expired = next(e for e in events if e["event_type"] == "license.expired")
    assert expired["details"].get("tier") == "member"
    assert expired["details"].get("jti") == "abcd1234"


def test_audit_canonicalizes_legacy_tier(tmp_path, monkeypatch):
    """R6 #7: _audit must surface the CANONICAL tier into the L16 chain — a raw
    legacy 'universal' must be written as 'member', and an unknown tier as the
    fail-closed 'free'."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(audit_path))

    V._audit("license.loaded", jti="ffff0000", tier="universal")
    V._audit("license.limit_exceeded", feature="compute", tier="garbage_tier")

    events = [json.loads(l) for l in audit_path.read_text().splitlines() if l.strip()]
    loaded = next(e for e in events if e["event_type"] == "license.loaded")
    assert loaded["details"].get("tier") == "member", "legacy 'universal' must canonicalize"
    over = next(e for e in events if e["event_type"] == "license.limit_exceeded")
    assert over["details"].get("tier") == "free", "unknown tier must fail closed to free"


def test_find_token_permissive_key_does_not_nameerror(tmp_path, monkeypatch):
    """review MEDIUM (original regression): the permissive-mode branch used
    `_log` (undefined) -> NameError aborted license load on a chmod-644
    global/license.key. Must not crash.

    Adversarial review follow-up: a permissive global/license.key must now be
    REJECTED (mode parity with session.key — a warn-but-still-trust token is
    a readable-by-anyone-local credential that never self-heals). Confirms
    both _find_token() and _find_token_disk_only() agree."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_LICENSE_KEY", raising=False)
    # Isolate the config dir so _find_token doesn't pick up the host's real
    # ~/.config/corvin-voice/session.key (which precedes global/license.key in
    # the discovery order) — keeps the test hermetic on a licensed dev host.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    gk = tmp_path / "global" / "license.key"
    gk.parent.mkdir(parents=True, exist_ok=True)
    gk.write_text("dummy.token.sig")
    os.chmod(gk, 0o644)  # too permissive -> triggers the reject branch

    token = V._find_token()            # must NOT raise NameError (the regression)
    assert token is None, "a permissive global/license.key must be rejected, not trusted"
    assert V._find_token_disk_only() is None  # same branch on the disk-only path
