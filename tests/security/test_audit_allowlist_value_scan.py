"""An event's positive allowlist decides WHICH keys may appear — never that
their VALUES are safe.

Regression (adversarial review round 5, 2026-09-28): ``filter_audit_details``
ran the e-mail/phone value scan only for events WITHOUT an allowlist (or for a
free-text key). The ~250 allowlists registered in rounds 2–4 therefore turned
the scan OFF for their fields, and a PII-shaped value that the vocabulary floor
used to drop was chained verbatim into the append-only, never-redactable audit
chain. Measured on the base vs round-4 HEAD for the four cases below.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FORGE = str(_REPO / "corvin_operator" / "forge")
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from forge import security_events as se  # noqa: E402

_EMAIL = "jane.doe@acme.example"

_LEAKS = [
    ("package.installed", "name", f"Invoice bot for {_EMAIL}"),
    ("datasource.connected", "name", _EMAIL),
    ("egress.blocked", "host", _EMAIL),
    ("config.set_rejected", "key", _EMAIL),
    ("package.installed", "name", "call +49 170 1234567"),
]


@pytest.mark.parametrize("event,key,value", _LEAKS)
def test_pii_value_on_an_allowlisted_key_is_dropped(event, key, value):
    assert key in se._EVENT_ALLOWLIST.get(event, frozenset()), (
        f"precondition: {event}.{key} must be allowlisted for this test to mean anything")
    cleaned, dropped = se.filter_audit_details({key: value}, event_type=event)
    assert key in dropped and key not in cleaned, cleaned


@pytest.mark.parametrize("event,key,value", [
    ("egress.blocked", "host", "api.anthropic.com"),
    ("package.installed", "name", "invoice-bot"),
    ("datasource.connected", "name", "warehouse_eu"),
    ("config.set_rejected", "key", "spec.engine_models.claude_code.os_model"),
    ("package.installed", "name", "0f3a9c1d2e4b5a6978"),  # hash/fingerprint shape
])
def test_content_free_values_on_allowlisted_keys_survive(event, key, value):
    cleaned, dropped = se.filter_audit_details({key: value}, event_type=event)
    assert cleaned.get(key) == value and not dropped, cleaned


def test_reserved_spine_is_still_fingerprinted_not_dropped():
    cleaned, _ = se.filter_audit_details({"user": _EMAIL, "name": "x"},
                                         event_type="package.installed")
    assert cleaned["user"] != _EMAIL and len(cleaned["user"]) == 8
    assert "user" in cleaned["_pii_fingerprinted"]


def test_leak_never_reaches_the_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "k" / "anchor.key"))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    chain = tmp_path / "audit.jsonl"
    se.write_event(chain, "package.installed",
                   details={"name": f"Invoice bot for {_EMAIL}", "version": "1.0.0"})
    raw = chain.read_text()
    assert _EMAIL not in raw
    rec = json.loads(raw.splitlines()[-1])
    assert "name" in rec["details"].get("_dropped_fields", [])
    assert se.verify_chain(chain) == (True, [])


@pytest.mark.parametrize("key", ["created_by", "deleted_by", "operator_ref", "decided_by"])
def test_actor_keys_are_pseudonymised_not_dropped(key):
    se.register_event_allowlist("r5.actor_probe", {key})
    cleaned, dropped = se.filter_audit_details({key: _EMAIL}, event_type="r5.actor_probe")
    assert not dropped and cleaned[key] != _EMAIL and len(cleaned[key]) == 8
    assert key in cleaned["_pii_fingerprinted"]
