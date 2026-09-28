"""A RELATIVE ``CORVIN_AUDIT_ANCHOR_KEY`` is refused, never resolved against cwd.

Adversarial review 2026-09-27: a test that set ``CORVIN_AUDIT_ANCHOR_KEY`` to a
relative value minted a 32-byte anchor key (and the ``chain_ids/``,
``chain_tails/``, ``mac_active_chains/`` marker dirs plus the MAC sentinel) in
the repository root, because ``_anchor_key_path()`` resolved it against the
process's working directory. The value is now refused with the same semantics
as an insecure-mode key: nothing is created, records carry no ``mac``, and
``verify_chain`` reports a current-state problem so the boot tripwire fails
closed.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FORGE = _REPO / "corvin_operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.append(str(_FORGE))

from forge import security_events as se  # noqa: E402


@pytest.fixture
def relative_key(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", "rel_anchor.key")
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    return cwd


def test_relative_anchor_key_creates_nothing_in_cwd(relative_key, tmp_path, caplog):
    chain = tmp_path / "chain" / "audit.jsonl"
    chain.parent.mkdir()
    with caplog.at_level(logging.CRITICAL, logger="corvin.audit"):
        se.write_event(chain, "consent.granted", details={"granted_by": "operator"})
    assert list(relative_key.iterdir()) == [], "anchor material written into cwd"
    assert se._anchor_key() is None
    assert se._ANCHOR_KEY_REFUSED == "relative_path"
    assert "mac" not in json.loads(chain.read_text().splitlines()[-1])
    assert any("relative path" in r.getMessage() for r in caplog.records)


def test_relative_anchor_key_fails_verify_closed(relative_key, tmp_path):
    chain = tmp_path / "chain" / "audit.jsonl"
    chain.parent.mkdir()
    se.write_event(chain, "consent.granted", details={"granted_by": "operator"})
    ok, problems = se.verify_chain(chain)
    assert not ok
    assert [p["issue"] for p in problems] == ["anchor_key_relative_path"]
    # Line-less → the tripwire classifies it as a current-state problem.
    assert all("line" not in p for p in problems)
    ok_i, problems_i, _ = se.verify_chain_incremental(chain)
    assert not ok_i and problems_i[0]["issue"] == "anchor_key_relative_path"
    assert list(relative_key.iterdir()) == []


def test_absolute_anchor_key_still_works(tmp_path, monkeypatch):
    key = tmp_path / "keys" / "anchor.key"
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(key))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    chain = tmp_path / "chain" / "audit.jsonl"
    chain.parent.mkdir()
    se.write_event(chain, "consent.granted", details={"granted_by": "operator"})
    assert key.exists() and len(key.read_bytes()) == 32
    assert "mac" in json.loads(chain.read_text().splitlines()[-1])
    assert se.verify_chain(chain) == (True, [])
