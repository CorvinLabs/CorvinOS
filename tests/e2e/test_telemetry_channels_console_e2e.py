"""Telemetry transparency endpoint — ``GET /v1/console/telemetry/channels``.

Over HTTP (TestClient), session overridden, against a TEMP ``CORVIN_HOME``
seeded with the exact state files the senders write: the ping stamp, a
heartbeat state file, healing-trace files (pending + sent), error reports in
the outbox, a tenant.corvin.yaml with opt-outs. Every number the page shows
must come from those files — never from an estimate.

1. seeded install: ping "sent" with last/next, heartbeat outcomes, healing
   traces pending/sent counts + last upload, error reports "collected, never
   sent" with the top signature, geo tier;
2. opt-outs in tenant.corvin.yaml flip the channels to "disabled";
3. an empty install reports "never"/"unknown", never zeros dressed as sends;
4. tokens are never in the response.
"""
from __future__ import annotations

import dataclasses
import gzip
import json
import os
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console import auth as session_auth
from core.console.corvin_console import deps as console_deps


def _fake_session_record(tenant_id: str) -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


HTRACE = {
    "schema": "htrace/1", "corvin_version": "2.0.0", "platform": "linux/x86_64", "python": "3.11",
    "error_fingerprint": "", "error_type": "", "error_module_ns": "", "error_function": "", "error_line": 0,
    "stack_frames": [], "event_sequence": ["heal.triggered", "heal.action", "heal.success"],
    "heal_action": "stale_lock", "heal_outcome": "success", "config_profile_hash": "", "tenant_shape": "single",
    "ts_day": "2026-09-20", "consent_act_id": "", "instance_token": "a" * 64,
}
REPORT = {
    "schema": "aco.telemetry/1", "instance": "anon", "corvin_version": "2.0.0",
    "signatures": [{"signature": "3d74d3f659df32f2", "exc_type": "RuntimeError",
                    "top_repo_file": "core/gateway/corvin_gateway/app.py", "func": "_lifespan",
                    "frames": ["core/gateway/corvin_gateway/app.py:_lifespan"], "count": 2}],
}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "corvin-home"
    (h / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.delenv("CORVIN_TELEMETRY_URL", raising=False)
    monkeypatch.delenv("CORVIN_TELEMETRY_OPTIN", raising=False)
    monkeypatch.delenv("CORVIN_GEO_OPT_OUT", raising=False)
    return h


def _seed(h: Path) -> None:
    tele = h / "aco" / "telemetry"
    (tele / "outbox").mkdir(parents=True)
    (tele / "last_ping").write_text("x")
    os.utime(tele / "last_ping", (time.time() - 3600, time.time() - 3600))
    (tele / ".telemetry_token").write_text("t" * 64)
    (tele / "htrace-token.txt").write_text("i" * 64)
    (tele / "active_engine").write_text("claude_code")
    (tele / "heartbeat_state.json").write_text(json.dumps({
        "attempts": 12, "successes": 11, "consecutive_failures": 1,
        "last_attempt": "2026-09-20T10:00:00Z", "last_success": "2026-09-20T09:55:00Z", "last_detail": "http 502",
    }))
    for i, stamp in enumerate((1789600000, 1789600100, 1789600200)):
        (tele / "outbox" / f"report-{stamp}.json").write_text(json.dumps(REPORT))
    (h / "telemetry").mkdir()
    (h / "telemetry" / "feature_snapshot.json").write_text(json.dumps({"bridges_connected": 1, "ldd_enabled": True}))
    ht = h / "healing-traces"
    (ht / "sent").mkdir(parents=True)
    (ht / "2026-09-20.jsonl").write_text("\n".join(json.dumps(HTRACE) for _ in range(3)) + "\n")
    with gzip.open(ht / "sent" / "2026-09-19.jsonl.gz", "wb") as f:
        f.write(b"{}\n")
    (ht / ".last_upload").write_text("2026-09-19,1")


@pytest.fixture
def client(home):
    from core.console.corvin_console.routes import telemetry_overview as tv

    app = FastAPI()
    app.include_router(tv.router, prefix="/v1/console")
    app.dependency_overrides[console_deps.require_session] = lambda: _fake_session_record("_default")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


URL = "/v1/console/telemetry/channels"


def _by_id(body: dict) -> dict:
    return {c["id"]: c for c in body["channels"]}


def test_seeded_install_reports_each_channel_from_its_own_state(client, home):
    _seed(home)
    r = client.get(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == {} and body["corvin_home"] == str(home)
    ch = _by_id(body)
    assert set(ch) == {"ping", "heartbeat", "healing_traces", "error_reports", "geo", "otlp_export", "stability"}

    ping = ch["ping"]
    assert ping["status"] == "sent" and ping["enabled"] is True
    assert ping["last_sent"] and ping["next_due"] > ping["last_sent"]
    assert ping["payload"]["active_engine"] == "claude_code"
    assert set(ping["payload"]) == {"corvin_version", "platform", "python_minor", "active_engine"}
    assert ping["identity"]["instance_token_present"] and ping["identity"]["telemetry_token_present"]
    assert ping["endpoint_host"] == "corvin-labs.com"

    hb = ch["heartbeat"]
    assert hb["status"] == "sent" and hb["attempts"] == 12 and hb["successes"] == 11
    assert hb["consecutive_failures"] == 1 and hb["last_detail"] == "http 502"
    assert hb["payload"] == {"features": {"bridges_connected": 1, "ldd_enabled": True}}

    htr = ch["healing_traces"]
    assert htr["status"] == "sent" and htr["pending_records"] == 3 and htr["sent_bundles"] == 1
    assert htr["last_upload"] == {"bundle_day": "2026-09-19", "bundles": 1}
    assert htr["sample_record"]["heal_action"] == "stale_lock"
    assert "instance_token" not in htr["sample_record"]
    assert "heal_outcome" in htr["payload_fields"]

    er = ch["error_reports"]
    assert er["status"] == "collected_never_sent" and er["intake_configured"] is True
    assert er["endpoint_host"] == "corvin-features-production.up.railway.app"
    assert er["outbox"]["reports"] == 3 and er["sent_reports"] == 0
    assert er["top_signatures"][0] == {"exc_type": "RuntimeError", "top_repo_file": "core/gateway/corvin_gateway/app.py", "func": "_lifespan", "count": 6}
    assert "first hourly batch" in er["note"]

    assert ch["geo"]["effective_tier"] in (1, 2, 3) and ch["geo"]["carried_by"] == ["ping", "heartbeat"]
    # no state yet in a seeded install: wired, nothing pushed yet — never "not wired"
    assert ch["otlp_export"]["status"] == "unknown" and ch["otlp_export"]["wired"] is True
    assert ch["otlp_export"]["endpoint_host"] == "corvin-features-production.up.railway.app"
    assert ch["stability"]["status"] == "not_wired" and ch["stability"]["endpoint_host"] == "corvin-features-production.up.railway.app"

    # secrets never travel: the tokens are only reported as present/absent
    text = r.text
    assert "t" * 64 not in text and "i" * 64 not in text and "a" * 64 not in text


def test_opt_outs_in_tenant_yaml_disable_the_channels(client, home):
    _seed(home)
    (home / "tenants" / "_default" / "global" / "tenant.corvin.yaml").write_text(
        "spec:\n  telemetry:\n    ping_enabled: false\n    healing_traces: false\n    error_traces: false\n    geo_tracking_tier: 1\n"
    )
    ch = _by_id(client.get(URL).json())
    assert ch["ping"]["status"] == "disabled" and ch["ping"]["enabled"] is False
    assert ch["heartbeat"]["status"] == "disabled"
    assert ch["healing_traces"]["status"] == "disabled"
    assert ch["error_reports"]["status"] == "disabled"
    assert ch["otlp_export"]["status"] == "disabled" and ch["stability"]["status"] == "disabled"
    assert ch["geo"]["effective_tier"] == 1 and "Nothing extra" in ch["geo"]["what_leaves"]


def test_empty_install_says_never_and_unknown_not_zero_sends(client, home):
    ch = _by_id(client.get(URL).json())
    assert ch["ping"]["status"] == "never" and ch["ping"]["last_sent"] is None
    assert ch["heartbeat"]["status"] == "unknown" and ch["heartbeat"]["attempts"] == 0 and ch["heartbeat"]["note"]
    assert ch["healing_traces"]["status"] == "never" and ch["healing_traces"]["pending"] == []
    assert ch["error_reports"]["status"] == "never" and ch["error_reports"]["outbox"]["reports"] == 0
    assert ch["otlp_export"]["status"] == "unknown" and ch["stability"]["status"] == "not_wired"


def test_heartbeat_records_its_outcome_in_the_state_file(home, monkeypatch):
    """The sender writes the outcome the endpoint reads — one contract, both ends."""
    from corvin_core.aco import heartbeat as hb

    _seed(home)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getcode(self):
            return 204

    monkeypatch.setattr(hb, "_open_no_redirect", lambda req, timeout: _Resp())
    assert hb.send_heartbeat(home) is True
    st = hb.read_state(home)
    assert st["attempts"] == 13 and st["successes"] == 12 and st["consecutive_failures"] == 0
    assert st["last_detail"] == "http 204" and st["last_success"] == st["last_attempt"]

    def _boom(req, timeout):
        raise OSError("network down")

    monkeypatch.setattr(hb, "_open_no_redirect", _boom)
    assert hb.send_heartbeat(home) is False
    st = hb.read_state(home)
    assert st["attempts"] == 14 and st["consecutive_failures"] == 1 and st["last_detail"] == "OSError"
    assert "features" not in json.dumps(st) and "Bearer" not in json.dumps(st)
