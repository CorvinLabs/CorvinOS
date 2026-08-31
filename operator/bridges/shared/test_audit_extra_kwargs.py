"""Regression: audit_event() folds unknown domain kwargs into details instead of
raising TypeError (which callers' broad `except` would swallow -> silent event
drop). This eradicates the recurring bad-kwarg-drop class at the root
(compute_quota, sob, manifest, validator, tamper_response all forwarded domain
kwargs). GDPR Art. 30/32 audit floor: a security/enforcement event must never be
silently dropped.
"""
import json
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import audit  # type: ignore  # noqa: E402


def _events(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def test_unknown_domain_kwargs_are_recorded_not_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    ap = tmp_path / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(ap))

    # The exact shape that used to TypeError-drop (compute.quota_exceeded).
    audit.audit_event("compute.quota_exceeded", channel="c", chat_key="k",
                      feature="compute_units_per_day", requested_value=5,
                      limit_value=3, tier="free")

    evs = _events(ap)
    hit = [e for e in evs if e["event_type"] == "compute.quota_exceeded"]
    assert len(hit) == 1, "bad-kwarg audit event must be recorded, not dropped"
    d = hit[0]["details"]
    assert d.get("feature") == "compute_units_per_day"
    assert d.get("tier") == "free"
    assert d.get("limit_value") == 3


def test_reserved_keys_in_extra_do_not_override_positional(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    ap = tmp_path / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(ap))
    # 'channel' as an explicit param must win; a stray reserved key is stripped.
    audit.audit_event("x.test", channel="real", details={"channel": "spoof"}, foo="bar")
    evs = _events(ap)
    e = next(e for e in evs if e["event_type"] == "x.test")
    assert e["details"]["channel"] == "real"
    assert e["details"].get("foo") == "bar"
