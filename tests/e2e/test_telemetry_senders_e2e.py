"""The three senders that had no receiver until 2026-09-20, each proven
against a REAL local HTTP server (the transport boundary), never by calling
the function that builds the payload:

1. error signatures — the outbox is folded into ONE batch, posted with the
   HMAC token pair, consumed files are removed, the batch is kept under
   ``sent/`` and ``error_reports_state.json`` records the outcome; a refused
   post moves nothing;
2. OTLP export — ``export_heartbeat_metrics`` pushes an
   ``ExportMetricsServiceRequest`` over OTLP/HTTP with the auth headers; the
   four gauges arrive with the allowlisted attributes; a 5xx answer falls
   back to the local JSON file and records the failure;
3. stability digest — ``send_digest`` posts the digest with the token pair
   and the daemon records the outcome; ``is_enabled`` feeds the counters.

Plus the console channel view over the same state files.
"""
from __future__ import annotations

import dataclasses
import gzip
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console import auth as session_auth
from core.console.corvin_console import deps as console_deps


class _Intake(BaseHTTPRequestHandler):
    """Records every POST; answers with the status the test configured."""

    status = 200
    received: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        type(self).received.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):  # silence
        pass


@pytest.fixture
def intake():
    srv = HTTPServer(("127.0.0.1", 0), _Intake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Intake.received = []
    _Intake.status = 200
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "corvin-home"
    tele = h / "aco" / "telemetry"
    (tele / "outbox").mkdir(parents=True)
    (h / "tenants" / "_default" / "global").mkdir(parents=True)
    (tele / ".telemetry_token").write_text("t" * 64)
    (tele / "htrace-token.txt").write_text("i" * 64)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    for k in ("CORVIN_TELEMETRY_URL", "CORVIN_TELEMETRY_OPTIN", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    import forge.paths as fp

    monkeypatch.setattr(fp, "corvin_home", lambda: h, raising=False)
    return h


REPORT = {
    "schema": "aco.telemetry/1", "instance": "anon", "corvin_version": "2.0.0",
    "signatures": [{"signature": "3d74d3f659df32f2", "exc_type": "RuntimeError",
                    "top_repo_file": "core/gateway/corvin_gateway/app.py", "func": "_lifespan",
                    "frames": ["core/gateway/corvin_gateway/app.py:_lifespan"], "count": 1}],
}


def _seed_outbox(home: Path, n: int = 3) -> None:
    for i in range(n):
        (home / "aco" / "telemetry" / "outbox" / f"report-{1789600000 + i}.json").write_text(json.dumps(REPORT))


# ── 1. error signatures ──────────────────────────────────────────────────────

def test_error_signature_batch_posts_once_and_consumes_the_outbox(home, intake, monkeypatch):
    from corvin_core.aco import telemetry as tel

    _seed_outbox(home, 3)
    monkeypatch.setenv("CORVIN_TELEMETRY_URL", f"{intake}/v1/telemetry/error-signatures")
    # the hardened opener refuses plain http — the test server is local, so allow it here only
    from corvin_core.aco import htrace_consent as hc
    import urllib.request

    monkeypatch.setattr(hc, "_open_no_redirect", lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))

    result = tel.submit_batch(home)
    assert result["sent"] == 3 and result["signatures"] == 1, result
    assert len(_Intake.received) == 1
    req = _Intake.received[0]
    assert req["path"] == "/v1/telemetry/error-signatures"
    assert req["headers"]["authorization"] == "Bearer " + "t" * 64
    assert req["headers"]["x-httrace-instance-token"] == "i" * 64
    body = json.loads(req["body"])
    assert body["reports_merged"] == 3 and body["signatures"][0]["count"] == 3
    assert body["span"]["from"] < body["span"]["to"]
    assert list((home / "aco" / "telemetry" / "outbox").glob("report-*.json")) == []
    batches = list((home / "aco" / "telemetry" / "sent").glob("batch-*.json"))
    assert len(batches) == 1 and json.loads(batches[0].read_text())["reports_merged"] == 3
    st = tel.read_state(home)
    assert st["batches"] == 1 and st["reports_sent"] == 3 and st["last_detail"] == "http 200" and st["consecutive_failures"] == 0

    # a refused post moves nothing
    _seed_outbox(home, 2)
    _Intake.status = 503
    result = tel.submit_batch(home)
    assert result["sent"] == 0 and result["failed"] == 2
    assert len(list((home / "aco" / "telemetry" / "outbox").glob("report-*.json"))) == 2
    assert tel.read_state(home)["consecutive_failures"] == 1


def test_submit_if_due_is_hourly_and_stamps(home, monkeypatch):
    from corvin_core.aco import telemetry as tel

    calls = []
    monkeypatch.setattr(tel, "submit_batch", lambda h: (calls.append(1), {"sent": 1})[1])
    assert tel.submit_if_due(home) is True and len(calls) == 1
    assert tel.submit_if_due(home) is True and len(calls) == 1, "second call within the hour must not post"
    stamp = home / "aco" / "telemetry" / ".last_error_submit"
    os.utime(stamp, (time.time() - 7200, time.time() - 7200))
    assert tel.submit_if_due(home) is True and len(calls) == 2


# ── 2. OTLP export ───────────────────────────────────────────────────────────

def test_otlp_export_pushes_the_gauges_with_auth_and_falls_back_on_failure(home, intake, monkeypatch):
    from corvin_core.aco import otel_bridge as ob
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", f"{intake}/v1/telemetry/otlp/v1/metrics")
    ob.reset_for_tests()
    out = ob.export_heartbeat_metrics(home, is_alive=True)
    assert out["ok"] is True, out
    assert len(_Intake.received) == 1
    req = _Intake.received[0]
    assert req["path"] == "/v1/telemetry/otlp/v1/metrics"
    assert req["headers"]["content-type"] == "application/x-protobuf"
    assert req["headers"]["authorization"] == "Bearer " + "t" * 64
    assert req["headers"]["x-httrace-instance-token"] == "i" * 64
    msg = ExportMetricsServiceRequest()
    msg.ParseFromString(req["body"])
    names = {m.name for rm in msg.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
    assert names == set(ob.METRIC_NAMES)
    attrs = {kv.key for rm in msg.resource_metrics for sm in rm.scope_metrics for m in sm.metrics for dp in m.gauge.data_points for kv in dp.attributes}
    assert attrs == set(ob.ATTRIBUTE_KEYS)
    res = {kv.key: kv.value.string_value for kv in msg.resource_metrics[0].resource.attributes}
    assert res["service.name"] == "corvinOS" and res["tenant_id"] == "_default"
    st = ob.read_state(home)
    assert st["successes"] == 1 and st["transport"] == "otlp/http" and st["last_detail"] == "exported"

    # the intake fails → JSON fallback keeps the record, state says so
    _Intake.status = 500
    out = ob.export_heartbeat_metrics(home, is_alive=True)
    assert out["ok"] is False
    st = ob.read_state(home)
    assert st["consecutive_failures"] == 1 and st["fallback_writes"] == 1
    fallback = list(ob.fallback_dir(home).glob("heartbeat-*.jsonl"))
    assert len(fallback) == 1 and json.loads(fallback[0].read_text().splitlines()[0])["is_alive"] is True


# ── 3. stability digest ──────────────────────────────────────────────────────

def test_stability_digest_is_fed_by_flag_evaluations_and_posted_with_auth(home, intake, monkeypatch):
    from corvin_core import feature_flags as ff
    from corvin_core.aco import stability_sender as ss
    from core.telemetry import stability_metrics as sm
    from core.telemetry import telemetry_daemon as td
    from corvin_core.aco import htrace_consent as hc
    import urllib.request

    monkeypatch.setattr(hc, "_open_no_redirect", lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
    sm.reset_metrics()
    some_flag = next(iter(ff._BY_ID))
    for _ in range(3):
        ff.is_enabled(some_flag)
    digest = sm.compute_digest(**ss.digest_kwargs(home)).to_dict()
    row = next(f for f in digest["flags_enabled"] if f["flag_id"] == some_flag)
    assert row["invocation_count_24h"] == 3 and len(digest["instance_id"]) == 36

    status = ss.send_digest(home, digest, url=f"{intake}/v1/telemetry/feature-stability")
    assert status == 200
    req = _Intake.received[0]
    assert req["headers"]["authorization"] == "Bearer " + "t" * 64
    assert json.loads(req["body"])["event_type"] == "feature_stability_digest"

    # the daemon records what happened
    ss.record_result(home, 200, digest)
    st = ss.read_state(home)
    assert st["successes"] == 1 and st["last_flags"] >= 1

    # opt-out holds at send time
    (home / "tenants" / "_default" / "global" / "tenant.corvin.yaml").write_text("spec:\n  telemetry:\n    ping_enabled: false\n")
    assert ss.send_digest(home, digest, url=f"{intake}/v1/telemetry/feature-stability") == 0
    assert len(_Intake.received) == 1

    # the daemon wiring: identity comes from the sender, first delay is short, hourly after
    d = td.TelemetryDaemon(send_fn=lambda p: 200, enabled=True, first_delay_seconds=0, digest_kwargs=lambda: ss.digest_kwargs(home))
    assert d.first_delay_seconds == 0 and d.interval_seconds == 3600


# ── console view ─────────────────────────────────────────────────────────────

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


def test_console_channels_report_the_three_from_their_state_files(home, intake, monkeypatch):
    from corvin_core.aco import otel_bridge as ob, stability_sender as ss, telemetry as tel
    from core.console.corvin_console.routes import telemetry_overview as tv

    (home / "telemetry").mkdir(exist_ok=True)
    ob.state_path(home).write_text(json.dumps({"last_success": "2026-09-20T10:00:00Z", "attempts": 4, "successes": 4, "transport": "otlp/http", "endpoint": "https://x/v1/telemetry/otlp/v1/metrics"}))
    ss.state_path(home).write_text(json.dumps({"started_at": "2026-09-20T09:55:00Z", "last_success": "2026-09-20T10:00:00Z", "last_status": 200, "attempts": 1, "successes": 1}))
    tel.state_path(home).write_text(json.dumps({"last_success": "2026-09-20T10:00:00Z", "batches": 1, "reports_sent": 10164, "attempts": 1, "last_detail": "http 200"}))

    app = FastAPI()
    app.include_router(tv.router, prefix="/v1/console")
    app.dependency_overrides[console_deps.require_session] = lambda: _fake_session_record("_default")
    with TestClient(app) as c:
        ch = {x["id"]: x for x in c.get("/v1/console/telemetry/channels").json()["channels"]}
    assert ch["otlp_export"]["status"] == "sent" and ch["otlp_export"]["wired"] is True and ch["otlp_export"]["sdk_installed"] is True
    assert ch["otlp_export"]["payload_fields"] == list(ob.METRIC_NAMES)
    assert ch["stability"]["status"] == "sent" and ch["stability"]["last_detail"] == "http 200"
    assert ch["error_reports"]["status"] == "sent" and ch["error_reports"]["sent_reports"] == 10164
    assert ch["error_reports"]["endpoint_host"] == "corvin-features-production.up.railway.app"
