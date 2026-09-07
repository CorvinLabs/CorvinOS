"""R2-A8: the console's audit allowlist must be registered with the core writer.

Two allowlists guard every console record and they were never introduced to each
other. ``corvin_console.audit`` checks ``details`` against its own
``_ALLOWED_FIELDS`` and raises on anything unexpected. It then hands the record
to ``forge.security_events.write_event``, whose default-deny floor (ADR-0640)
admits only keys from a REGISTERED per-event allowlist or, absent one, the
universal vocabulary. Nothing registered the console's — so the floor quietly
moved the console-specific keys into ``_dropped_fields``:

  * ``console.action_performed`` lost ``target_kind`` (the writer's literal
    knows ``target_type``; the console emits ``target_kind``),
  * ``console.session_started`` lost ``token_fingerprint`` / ``user_agent_class``.

The result was a hash-chained record saying an action was performed without
saying on what — in the surface whose whole purpose is operator attribution
(ADR-0015). These tests read the record back OFF THE CHAIN, through the real
writer, rather than asserting on the allowlist dict.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "operator" / "forge", _REPO / "operator" / "bridges" / "shared",
           _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


@pytest.fixture
def chain(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    from forge import security_events as se
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    return home


def _records(home: Path, event_type: str) -> list[dict]:
    path = (home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl")
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("event_type") == event_type:
            out.append(rec)
    return out


def test_action_performed_keeps_target_kind_on_the_chain(chain):
    from corvin_console import audit as console_audit

    console_audit.action_performed(
        tenant_id="_default", sid_fingerprint="sidfp",
        action="workflow.created", target_kind="workflow", target_id="wf-1",
    )
    recs = _records(chain, "console.action_performed")
    assert len(recs) == 1, recs
    details = recs[0]["details"]
    assert details.get("target_kind") == "workflow", details
    assert details.get("target_id") == "wf-1", details
    assert details.get("action") == "workflow.created", details
    assert "_dropped_fields" not in details, details


def test_session_started_keeps_its_fingerprints_on_the_chain(chain):
    from corvin_console import audit as console_audit

    console_audit.session_started(
        tenant_id="_default", token_fingerprint="tokfp", sid_fingerprint="sidfp",
        user_agent="Mozilla/5.0 (X11; Linux x86_64)",
    )
    recs = _records(chain, "console.session_started")
    assert len(recs) == 1, recs
    details = recs[0]["details"]
    assert details.get("token_fingerprint") == "tokfp", details
    assert details.get("user_agent_class") == "browser-desktop", details
    assert "_dropped_fields" not in details, details


def test_the_registration_did_not_re_admit_content_keys(chain):
    """Registering an allowlist EXEMPTS its keys from the M1 denylist floor, so
    the console's list must not name a content/PII/secret key. The writer now
    refuses such a registration outright (R2-A6); this asserts the console's own
    list is clean, which is what makes that refusal a no-op here."""
    from forge import security_events as se
    from corvin_console import audit as console_audit

    for event_type, fields in console_audit._ALLOWED_FIELDS.items():
        bad = sorted(f for f in fields if se._audit_key_forbidden(str(f).lower()))
        assert not bad, f"{event_type} names denylisted field(s) {bad}"


def test_the_chain_still_verifies(chain):
    from corvin_console import audit as console_audit
    from forge import security_events as se

    console_audit.action_performed(
        tenant_id="_default", sid_fingerprint="sidfp",
        action="plugin.enabled", target_kind="plugin", target_id="p-1",
    )
    path = chain / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    ok, problems = se.verify_chain(path)
    assert ok, problems
