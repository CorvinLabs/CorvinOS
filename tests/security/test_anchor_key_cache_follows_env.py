"""The audit writer's anchor-key cache is valid for ONE ``CORVIN_AUDIT_ANCHOR_KEY`` only.

Until 2026-11-05 ``security_events._anchor_key()`` cached the first key it loaded
for the life of the process. A test that pointed the variable at its own key (and
restored it) left one ``security_events`` instance MAC-ing with that stale key while
another instance in the same process used the current one, so records written by
the first failed ``verify_chain`` as ``mac_tampered`` - order-dependent failures in
``corvin_operator/skill-forge/tests/test_scope_shadowing_manifest.py`` after
``tests/learning`` had run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_FORGE = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))

from forge import security_events as se  # noqa: E402


def _key(path: Path, byte: bytes) -> Path:
    path.write_bytes(byte * 32)
    os.chmod(path, 0o600)
    return path


def test_cached_key_is_reloaded_when_the_key_location_changes(tmp_path, monkeypatch):
    a = _key(tmp_path / "a.key", b"\x11")
    b = _key(tmp_path / "b.key", b"\x22")
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_ENV", None)

    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(a))
    assert se._anchor_key() == b"\x11" * 32
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(b))
    assert se._anchor_key() == b"\x22" * 32
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(a))
    assert se._anchor_key() == b"\x11" * 32


def test_chain_written_across_an_env_switch_verifies_under_the_current_key(tmp_path, monkeypatch):
    a = _key(tmp_path / "a.key", b"\x33")
    b = _key(tmp_path / "b.key", b"\x44")
    chain = tmp_path / "audit.jsonl"
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_ENV", None)

    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(a))
    se._anchor_key()  # an earlier user of the cache, under another key
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(b))
    for i in range(3):
        se.write_event(chain, "test.event", details={"seq": i})
    ok, problems = se.verify_chain(chain)
    assert ok, problems
