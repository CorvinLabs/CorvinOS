"""Refutation round 2026-11-05 against the host guard (commit 9346ba843), RC-1/2/6/8/10.

Every case drives the real hosts (gateway app and standalone console) through a
Starlette TestClient with a crafted Host / Origin, except the parse table, which is
shared with the JS twin (``corvin_operator/bridges/shared/js/host_guard_cases.json``).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from corvin_console import host_guard as hg

LOOPBACK_PEER = ("127.0.0.1", 40000)
EVIL = "evil.example:8765"
CASES = json.loads((Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges"
                    / "shared" / "js" / "host_guard_cases.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("CORVIN_ALLOWED_ORIGINS", "CORVIN_BIND_HOST", "CORVIN_DOMAIN", "CORVIN_TS_HOSTNAME"):
        monkeypatch.delenv(k, raising=False)
    # conftest allowlists the test-only names testserver/test; keep exactly those.
    monkeypatch.setenv("CORVIN_ALLOWED_HOSTS", "testserver,test")
    monkeypatch.setattr(hg.sys, "argv", ["/x/bin/pytest"])  # not a uvicorn process
    hg.reset_audit_throttle()
    p = _yaml_path()
    saved = p.read_bytes() if p.exists() else None
    yield
    hg.reset_audit_throttle()
    if saved is None:
        p.unlink(missing_ok=True)
    else:
        p.write_bytes(saved)


def _yaml_path() -> Path:
    from forge import paths as forge_paths

    return Path(forge_paths.tenant_global_dir("_default")) / "tenant.corvin.yaml"


@pytest.fixture
def writes(monkeypatch):
    from forge import security_events as se

    calls: list[dict] = []
    monkeypatch.setattr(se, "write_event", lambda path, ev, **kw: calls.append(kw["details"]))
    return calls


@pytest.fixture(scope="module")
def gateway_app():
    from corvin_gateway.app import app

    return app


@pytest.fixture(scope="module")
def standalone_app():
    from corvin_console import standalone

    return standalone.create_app()


def _c(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8765", client=LOOPBACK_PEER)


def _tenant_yaml(spec: dict) -> Path:
    p = _yaml_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"spec": spec}), encoding="utf-8")
    p.chmod(0o600)
    return p


# ── RC-1: documented deployment names ─────────────────────────────────────────


@pytest.mark.parametrize("which", ["gateway_app", "standalone_app"])
def test_documented_names_refused_without_config_and_the_body_names_the_setting(which, request,
                                                                                writes):
    c = _c(request.getfixturevalue(which))
    for host in ("corvin:8000", "corvin.tail-1234.ts.net:8000", "corvin.example.com",
                 "my-laptop.local:8765"):
        r = c.get("/v1/console/healthz", headers={"host": host})
        assert r.status_code == 400, host
        assert "CORVIN_ALLOWED_HOSTS" in r.text and "spec.console.allowed_hosts" in r.text


def test_corvin_domain_and_its_www_and_port_variants(gateway_app, monkeypatch, writes):
    monkeypatch.setenv("CORVIN_DOMAIN", "corvin.example.com")
    c = _c(gateway_app)
    for host in ("corvin.example.com", "corvin.example.com:443", "www.corvin.example.com"):
        assert c.get("/v1/console/healthz", headers={"host": host}).status_code == 200, host
    assert c.get("/v1/console/healthz", headers={"host": "evil.example.com"}).status_code == 400
    # local-login stays loopback-only even for the deployment's own name
    r = c.get("/v1/console/auth/local-login", headers={"host": "corvin.example.com"},
              follow_redirects=False)
    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_tailscale_names_when_the_tailscale_deployment_is_configured(gateway_app, monkeypatch,
                                                                     writes):
    c = _c(gateway_app)
    h = {"host": "corvin.tail-1234.ts.net:8000"}
    assert c.get("/v1/console/healthz", headers=h).status_code == 400
    monkeypatch.setenv("CORVIN_TS_HOSTNAME", "corvin")
    for host in ("corvin:8000", "corvin.tail-1234.ts.net:8000"):
        assert c.get("/v1/console/healthz", headers={"host": host}).status_code == 200, host
    # closed suffix: only <name>.<one tailnet label>.ts.net
    for host in ("other.tail-1234.ts.net", "corvin.a.b.ts.net", "corvin.ts.net",
                 "corvin.tail-1234.ts.net.evil.example"):
        assert c.get("/v1/console/healthz", headers={"host": host}).status_code == 400, host


def test_bind_host_name_from_env_argv_and_yaml(gateway_app, monkeypatch, writes):
    c = _c(gateway_app)
    h = {"host": "corvin-box:8765"}
    assert c.get("/v1/console/healthz", headers=h).status_code == 400
    monkeypatch.setenv("CORVIN_BIND_HOST", "corvin-box")
    assert c.get("/v1/console/healthz", headers=h).status_code == 200
    monkeypatch.delenv("CORVIN_BIND_HOST")
    # the argv parser: `python -m uvicorn corvin_gateway.app:app --host corvin-box`
    assert hg._argv_bind_host(["/x/site-packages/uvicorn/__main__.py", "corvin_gateway.app:app",
                               "--host", "corvin-box", "--port", "8765"]) == "corvin-box"
    assert hg._argv_bind_host(["/x/bin/uvicorn", "--host=corvin-box"]) == "corvin-box"
    assert hg._argv_bind_host(["/x/bin/pytest", "--host", "corvin-box"]) == ""
    monkeypatch.setattr(hg.sys, "argv", ["/x/bin/uvicorn", "app:app", "--host", "corvin-box"])
    assert c.get("/v1/console/healthz", headers=h).status_code == 200
    monkeypatch.setattr(hg.sys, "argv", ["/x/bin/pytest"])
    assert c.get("/v1/console/healthz", headers=h).status_code == 400
    _tenant_yaml({"console": {"bind": "corvin-box"}})
    assert c.get("/v1/console/healthz", headers=h).status_code == 200


def test_lan_bind_allows_the_machines_own_and_mdns_name(gateway_app, monkeypatch, writes):
    monkeypatch.setattr(hg, "_own_hostname", lambda: "my-laptop")
    c = _c(gateway_app)
    h = {"host": "my-laptop.local:8765"}
    assert c.get("/v1/console/healthz", headers=h).status_code == 400  # loopback bind
    monkeypatch.setenv("CORVIN_BIND_HOST", "0.0.0.0")
    for host in ("my-laptop.local:8765", "my-laptop:8765"):
        assert c.get("/v1/console/healthz", headers={"host": host}).status_code == 200, host
    assert c.get("/v1/console/healthz", headers={"host": "other.local"}).status_code == 400


def test_yaml_allowlist(gateway_app, writes):
    c = _c(gateway_app)
    _tenant_yaml({"console": {"allowed_hosts": [" corvin.lan ", ".", ".home.arpa", ".x.lan:1"]}})
    assert c.get("/v1/console/healthz", headers={"host": "corvin.lan:8765"}).status_code == 200
    assert c.get("/v1/console/healthz", headers={"host": "nas.home.arpa"}).status_code == 200
    # "." is ignored, never "every trailing-dot name"; a suffix with a port is ignored
    assert c.get("/v1/console/healthz", headers={"host": "evil.example."}).status_code == 400
    assert c.get("/v1/console/healthz", headers={"host": "box.x.lan"}).status_code == 400


@pytest.mark.parametrize("which", ["gateway_app", "standalone_app"])
def test_signed_a2a_post_routes_carry_no_host_check(which, request, writes):
    c = _c(request.getfixturevalue(which))
    for path in ("/v1/a2a/ping", "/v1/a2a/receive", "/v1/a2a/friendship-ack"):
        foreign = c.post(path, headers={"host": "peer.example.net"}, json={})
        local = c.post(path, headers={"host": "127.0.0.1:8765"}, json={})
        assert foreign.status_code == local.status_code, path
        assert foreign.status_code != 400 or "host not allowed" not in foreign.text, path
    # a GET on the same path is not a signed route: still guarded
    assert c.get("/v1/a2a/ping", headers={"host": "peer.example.net"}).status_code == 400


# ── RC-2: the audit throttle ─────────────────────────────────────────────────


def test_rotating_hostnames_are_capped_globally(gateway_app, writes):
    c = _c(gateway_app)
    for i in range(300):
        assert c.get("/x", headers={"host": f"r{i}.rebind.attacker.test"}).status_code == 400
    assert len(writes) == hg.AUDIT_MAX_PER_WINDOW


def test_suppressed_refusals_are_counted_on_the_next_record(writes):
    t0 = 1000.0
    for _ in range(200):
        s = hg.admit_record("host_not_allowed", "evil.example", now=t0)
        if s is not None:
            hg.write_refusal(surface="s", reason="host_not_allowed", scope_type="http", path="/",
                             host="evil.example", origin=None, suppressed_count=s)
    assert len(writes) == 1 and writes[0]["suppressed_count"] == 0
    s = hg.admit_record("host_not_allowed", "evil.example", now=t0 + 61)
    assert s == 199, s


def test_lru_eviction_does_not_reset_the_global_throttle(writes):
    t = 5000.0
    assert hg.admit_record("host_not_allowed", "victim.test", now=t) == 0
    for i in range(hg.AUDIT_KEYS_MAX + 10):
        hg.admit_record("host_not_allowed", f"h{i}.test", now=t + 1)
    assert len(hg._audit_last) <= hg.AUDIT_KEYS_MAX
    # the victim's key may be evicted, the global window still holds within the minute
    assert hg.admit_record("host_not_allowed", "victim.test", now=t + 2) is None


def test_chain_record_carries_suppressed_count(gateway_app):
    from forge import paths as forge_paths

    chain = forge_paths.tenant_audit_chain("_default")
    before = chain.read_text(encoding="utf-8").count("\n") if chain.exists() else 0
    c = _c(gateway_app)
    for _ in range(3):
        assert c.get("/healthz", headers={"host": "rebind.attacker.test"}).status_code == 400
    lines = chain.read_text(encoding="utf-8").splitlines()[before:]
    recs = [json.loads(ln) for ln in lines if '"http.host_rejected"' in ln]
    assert len(recs) == 1
    assert recs[0]["details"]["suppressed_count"] == 0
    assert "_dropped_fields" not in recs[0]["details"]


# ── RC-6: WebSocket Origin behind a TLS-terminating proxy ─────────────────────


def test_https_origin_on_plain_ws_behind_a_tls_proxy(gateway_app, monkeypatch, writes):
    monkeypatch.setenv("CORVIN_DOMAIN", "corvin.example.com")
    c = TestClient(gateway_app, base_url="http://corvin.example.com", client=LOOPBACK_PEER)
    url = "ws://corvin.example.com/v1/console/chat/sessions/does-not-exist/stream"
    # Caddy: browser Origin https://corvin.example.com, Host corvin.example.com, scope ws
    with pytest.raises(WebSocketDisconnect) as ei:
        with c.websocket_connect(url, headers={"host": "corvin.example.com",
                                               "origin": "https://corvin.example.com"}) as ws:
            ws.receive_json()
    assert ei.value.code != 1008, "the host guard refused a same-host https origin"
    # a different host or an explicit other port is still refused
    for origin in ("https://evil.example", "https://corvin.example.com:8443",
                   "http://corvin.example.com:3000"):
        with pytest.raises(WebSocketDisconnect) as ei:
            with c.websocket_connect(url, headers={"host": "corvin.example.com",
                                                   "origin": origin}) as ws:
                ws.receive_json()
        assert ei.value.code == 1008, origin


def test_origin_rule_ignores_the_scheme_and_compares_explicit_ports():
    assert hg.origin_allowed("https://corvin.example.com", "corvin.example.com", "ws")
    assert hg.origin_allowed("http://corvin.example.com", "corvin.example.com", "wss")
    assert hg.origin_allowed("https://box:8443", "box:8443", "ws")
    assert not hg.origin_allowed("http://localhost", "localhost:8765", "ws")
    assert not hg.origin_allowed("http://localhost:3000", "localhost:8765", "ws")


# ── RC-8: the standalone middleware is load-bearing ──────────────────────────


def _non_router_paths(app) -> list[str]:
    return sorted({r.path for r in app.routes if getattr(r, "path", None)
                   and not r.path.startswith("/v1/console") and "{" not in r.path})


def _answers(app, paths) -> dict[str, int]:
    c = _c(app)
    return {p: c.get(p, headers={"host": EVIL}, follow_redirects=False).status_code for p in paths}


def test_standalone_non_router_surfaces_refuse_a_rebound_host(standalone_app, writes):
    paths = _non_router_paths(standalone_app)
    assert "/local-stats" in paths and "/" in paths, paths  # positive control
    answers = _answers(standalone_app, paths)
    assert all(s == 400 for s in answers.values()), answers


def test_removing_the_standalone_middleware_is_noticed(monkeypatch, writes):
    """Mutation check: without ``install(app, surface="standalone")`` the
    /local-stats page and the root redirect answer a rebound Host, so the test
    above would fail."""
    from corvin_console import standalone

    monkeypatch.setattr(hg, "install", lambda app, *, surface: None)
    mutated = standalone.create_app()
    answers = _answers(mutated, ["/local-stats", "/"])
    assert answers["/local-stats"] == 200 and answers["/"] in (302, 307), answers


# ── RC-10: one parse table for both implementations ──────────────────────────


@pytest.mark.parametrize("raw,expected", CASES["entries"])
def test_entry_parse_table(raw, expected):
    got = hg.parse_entry(raw)
    assert (list(got) if got else None) == expected


@pytest.mark.parametrize("host,ok", CASES["hosts"]["cases"])
def test_host_table(host, ok, monkeypatch):
    monkeypatch.setenv("CORVIN_ALLOWED_HOSTS", CASES["hosts"]["allowlist"])
    monkeypatch.setattr(hg, "_tenant_yaml_hosts", lambda: [])
    assert hg.host_allowed(host) is ok


def test_ignored_entries_are_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(hg, "_ignored_logged", set())
    monkeypatch.setenv("CORVIN_ALLOWED_HOSTS", ".,.corvin.lan:8765")
    with caplog.at_level("WARNING", logger=hg.log.name):
        for _ in range(3):
            hg.host_allowed("evil.example")
    msgs = [r.getMessage() for r in caplog.records if "ignoring" in r.getMessage()]
    assert len(msgs) == 2, msgs
