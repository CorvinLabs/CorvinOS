"""a2a_audit_head reads (and writes) THE tenant chain (A2A review r4, finding 4).

It used to compose ``<home>/global/forge/audit.jsonl`` by hand — the legacy
location. On a tenant-native install the receiver writes the tenant chain
``<home>/tenants/<tid>/global/forge/audit.jsonl``, so the published head never
advanced, and ``a2a.peer_audit_anomaly`` was appended to a sibling chain the
tripwire reports as ``audit_chain_split``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
for p in (_here, _here.parents[1] / "forge"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import a2a_audit_head as ah  # noqa: E402
import audit  # noqa: E402


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)


def test_audit_head_path_is_the_writer_chain(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    p = ah._audit_path()
    assert p == Path(audit.audit_path())
    assert p == tmp_path / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    assert p != tmp_path / "global" / "forge" / "audit.jsonl"


def test_read_audit_head_sees_tenant_chain_records(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    chain = tmp_path / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    chain.write_text("".join(json.dumps({"hash": f"h{i}", "ts": i}) + "\n" for i in range(3)))
    # A stale legacy file must not be what is reported.
    legacy = tmp_path / "global" / "forge" / "audit.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"hash": "legacy", "ts": 0}) + "\n")
    head = ah.read_audit_head()
    assert head["event_count"] == 3 and head["chain_head"] == "h2"


def test_voice_audit_path_override_still_wins(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "x.jsonl"))
    assert ah._audit_path() == tmp_path / "x.jsonl"


def test_anomaly_event_never_lands_in_legacy_chain(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    captured = []
    import forge.security_events as se
    monkeypatch.setattr(se, "write_event", lambda path, *a, **k: captured.append(Path(path)))
    endpoint = "https://peer.example:8775"
    for _ in range(6):
        ah.check_peer_audit_head(endpoint, {"chain_head": "same", "event_count": 5})
    assert captured, "anomaly never emitted — test did not reach the writer"
    legacy = tmp_path / "global" / "forge" / "audit.jsonl"
    assert all(p == Path(audit.audit_path()) and p != legacy for p in captured)
