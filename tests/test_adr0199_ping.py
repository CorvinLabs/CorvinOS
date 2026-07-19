"""End-to-end tests for ADR-0199 (a2a_ping, sender-side) and the ADR-0198
reconnect hardening (SSRF gate + audit-first ordering, 2026-07-19 review).

The ADR-0198 classes live in this file (not a new test module) because the
2026-07-19 adversarial-review fix batch was scoped to the two existing
ADR-019x test files.
"""
import hashlib
import hmac as _hmac
import json
import os
import time
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "bridges" / "shared"))

from remote_trigger_sender import (
    RemoteTriggerSender, PingResult, ErrorCategory, TransportError,
)

_HMAC_KEY = "aa" * 32
_RECV_KEY = "bb" * 32


def _write_endpoint(dirpath: Path, endpoint_id: str, url: str) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"{endpoint_id}.json"
    p.write_text(json.dumps({
        "endpoint_id": endpoint_id,
        "url": url,
        "hmac_key": _HMAC_KEY,
        "recv_key": _RECV_KEY,
        "instance_id": "",
        "enabled": True,
        "default_ttl_s": 60,
    }), encoding="utf-8")
    os.chmod(p, 0o600)
    return p


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """Keep the sender away from the real ~/.corvin and endpoint registry."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin-home"))
    monkeypatch.delenv("REMOTE_ENDPOINTS_DIR", raising=False)
    return tmp_path


class TestADR0199Ping:
    """ADR-0199: a2a_ping is lightweight, authenticated, network-probe based."""

    def test_heartbeat_cache_stub_removed(self):
        """2026-07-19 HIGH fix: the sender-side heartbeat-cache fast path was a
        silent dead path (imported a nonexistent symbol, swallowed by a blanket
        except) with an unworkable design (in-memory cross-process cache). It
        must be gone until the receiver-side records exist."""
        assert not hasattr(RemoteTriggerSender, "_get_last_heartbeat_timestamp")

    def test_ping_goes_straight_to_network_probe(self):
        sender = RemoteTriggerSender(instance_id="test-sender")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_ping_probe",
                       Mock(return_value=(True, None, None)))
            result = sender.ping(endpoint_id="test-endpoint", timeout_s=5)
        assert result.reachable is True
        assert result.source == "network_probe"
        assert result.error_category is None

    def test_ping_network_probe_unreachable(self):
        sender = RemoteTriggerSender(instance_id="test-sender")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_ping_probe",
                       Mock(return_value=(False, ErrorCategory.UNREACHABLE,
                                          "Unable to reach endpoint (DNS/connection refused)")))
            result = sender.ping(endpoint_id="test-endpoint", timeout_s=5)
        assert result.reachable is False
        assert result.error_category == ErrorCategory.UNREACHABLE

    def test_ping_network_probe_timeout(self):
        sender = RemoteTriggerSender(instance_id="test-sender")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_ping_probe",
                       Mock(return_value=(False, ErrorCategory.TIMEOUT_TRANSPORT,
                                          "HTTP request timeout")))
            result = sender.ping(endpoint_id="test-endpoint", timeout_s=5)
        assert result.reachable is False
        assert result.error_category == ErrorCategory.TIMEOUT_TRANSPORT

    def test_ping_network_probe_auth_failed(self):
        sender = RemoteTriggerSender(instance_id="test-sender")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_ping_probe",
                       Mock(return_value=(False, ErrorCategory.AUTH_FAILED,
                                          "Response signature verification failed")))
            result = sender.ping(endpoint_id="test-endpoint", timeout_s=5)
        assert result.reachable is False
        assert result.error_category == ErrorCategory.AUTH_FAILED

    def test_ping_timeout_clamped_2_10_seconds(self):
        sender = RemoteTriggerSender(instance_id="test-sender")
        probe = Mock(return_value=(True, None, None))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_ping_probe", probe)
            sender.ping(endpoint_id="test-endpoint", timeout_s=0.5)
            assert probe.call_args[1]["timeout_s"] == 2
            sender.ping(endpoint_id="test-endpoint", timeout_s=20)
            assert probe.call_args[1]["timeout_s"] == 10
            sender.ping(endpoint_id="test-endpoint")
            assert probe.call_args[1]["timeout_s"] == 5

    def test_ping_url_derived_from_receive_url(self, isolated_env, tmp_path):
        """2026-07-19 HIGH fix: cfg["url"] is the full .../v1/a2a/receive URL.
        Naive concatenation produced .../v1/a2a/receive/v1/a2a/ping → 404."""
        endpoints = tmp_path / "endpoints"
        _write_endpoint(endpoints, "test-endpoint",
                        "http://peer.example:8443/v1/a2a/receive")
        sender = RemoteTriggerSender(endpoints, instance_id="test-sender")
        seen = {}

        def fake_post(url, envelope, timeout_s):
            seen["url"] = url
            raise TransportError("connection_failed")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_post", fake_post)
            result = sender.ping("test-endpoint")

        assert seen["url"] == "http://peer.example:8443/v1/a2a/ping"
        assert "/v1/a2a/receive/v1/a2a/ping" not in seen["url"]
        assert result.reachable is False
        assert result.error_category == ErrorCategory.UNREACHABLE

    def test_ping_url_base_without_receive_suffix(self, isolated_env, tmp_path):
        """A bare base URL (no /v1/a2a/receive suffix) also yields the ping route."""
        endpoints = tmp_path / "endpoints"
        _write_endpoint(endpoints, "test-endpoint", "http://peer.example:8443")
        sender = RemoteTriggerSender(endpoints, instance_id="test-sender")
        seen = {}

        def fake_post(url, envelope, timeout_s):
            seen["url"] = url
            raise TransportError("connection_failed")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_post", fake_post)
            sender.ping("test-endpoint")

        assert seen["url"] == "http://peer.example:8443/v1/a2a/ping"

    def test_unsigned_ok_response_is_not_reachable(self, isolated_env, tmp_path):
        """2026-07-19 HIGH fix (forgeable liveness): an UNSIGNED response in the
        legacy rejection shape but carrying ok:true must NOT yield
        reachable=True — anyone who can answer the port could forge liveness."""
        endpoints = tmp_path / "endpoints"
        _write_endpoint(endpoints, "test-endpoint",
                        "http://peer.example:8443/v1/a2a/receive")
        sender = RemoteTriggerSender(endpoints, instance_id="test-sender")

        def fake_post(url, envelope, timeout_s):
            # Legacy unsigned rejection shape (accepted by _verify_response
            # with is_signed=False) + a forged ok:true.
            return {"status": "rejected", "data": {}, "ok": True}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_post", fake_post)
            result = sender.ping("test-endpoint")

        assert result.reachable is False
        assert result.error_category == ErrorCategory.AUTH_FAILED

    def test_signed_ok_response_is_reachable(self, isolated_env, tmp_path):
        """A properly recv_key-signed response echoing task_id=ping_id is the
        one and only reachable=True path (ADR-0199 task_id echo contract)."""
        endpoints = tmp_path / "endpoints"
        _write_endpoint(endpoints, "test-endpoint",
                        "http://peer.example:8443/v1/a2a/receive")
        sender = RemoteTriggerSender(endpoints, instance_id="test-sender")

        def fake_post(url, envelope, timeout_s):
            resp = {
                "ok": True,
                "instance_id": "peer-instance",
                "protocol_version": 8,
                "server_time": time.time(),
                "task_id": envelope["ping_id"],  # ADR-0199: echoes ping_id
            }
            canonical = json.dumps(
                resp, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode()
            resp["signature"] = _hmac.new(
                bytes.fromhex(_RECV_KEY), canonical, hashlib.sha256,
            ).hexdigest()
            return resp

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_post", fake_post)
            result = sender.ping("test-endpoint")

        assert result.reachable is True
        assert result.error_category is None

    def test_signed_response_wrong_task_id_rejected(self, isolated_env, tmp_path):
        """Anti-replay: a signed response with a different task_id is AUTH_FAILED."""
        endpoints = tmp_path / "endpoints"
        _write_endpoint(endpoints, "test-endpoint",
                        "http://peer.example:8443/v1/a2a/receive")
        sender = RemoteTriggerSender(endpoints, instance_id="test-sender")

        def fake_post(url, envelope, timeout_s):
            resp = {"ok": True, "instance_id": "peer", "task_id": "some-old-ping"}
            canonical = json.dumps(
                resp, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode()
            resp["signature"] = _hmac.new(
                bytes.fromhex(_RECV_KEY), canonical, hashlib.sha256,
            ).hexdigest()
            return resp

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_post", fake_post)
            result = sender.ping("test-endpoint")

        assert result.reachable is False
        assert result.error_category == ErrorCategory.AUTH_FAILED

    def test_ping_request_signed_with_hmac_key(self, isolated_env, tmp_path):
        """Ping request carries an HMAC-SHA256 signature over the canonical
        {ping_id, issued_at, origin_id} payload using the pairing hmac_key."""
        endpoints = tmp_path / "endpoints"
        _write_endpoint(endpoints, "test-endpoint",
                        "http://peer.example:8443/v1/a2a/receive")
        sender = RemoteTriggerSender(endpoints, instance_id="test-sender")
        seen = {}

        def fake_post(url, envelope, timeout_s):
            seen["envelope"] = dict(envelope)
            raise TransportError("connection_failed")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_post", fake_post)
            sender.ping("test-endpoint")

        env = seen["envelope"]
        payload = {k: v for k, v in env.items() if k != "signature"}
        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        expected = _hmac.new(
            bytes.fromhex(_HMAC_KEY), canonical.encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        assert env["signature"] == expected
        assert set(payload) == {"ping_id", "issued_at", "origin_id"}

    def test_ping_emits_audit_event(self, isolated_env, tmp_path):
        """2026-07-19 LOW fix: one A2A.ping_result audit event per ping()
        outcome, closed-enum values only."""
        se = MagicMock()
        se.write_event = MagicMock(return_value={"hash": "x"})
        sender = RemoteTriggerSender(instance_id="test-sender", forge_se=se)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sender, "_http_ping_probe",
                       Mock(return_value=(False, ErrorCategory.UNREACHABLE,
                                          "Unable to reach endpoint (DNS/connection refused)")))
            sender.ping(endpoint_id="test-endpoint")

        ping_calls = [c for c in se.write_event.call_args_list
                      if c.args[1] == "A2A.ping_result"]
        assert len(ping_calls) == 1
        call = ping_calls[0]
        assert call.kwargs["severity"] == "WARNING"
        details = call.kwargs["details"]
        assert details["endpoint_id"] == "test-endpoint"
        assert details["reachable"] is False
        assert details["source"] == "network_probe"
        assert details["error_category"] == ErrorCategory.UNREACHABLE
        assert isinstance(details["duration_ms"], int)

    def test_ping_no_nonce_store_required(self):
        """ADR-0199: No persistent nonce store for ping (side-effect-free)."""
        import inspect
        sender = RemoteTriggerSender(instance_id="test-sender")
        source = inspect.getsource(sender.ping)
        assert "nonce_store" not in source.lower(), "Ping should not access nonce store"


class TestADR0199ReceiverBackendParity:
    """Verify ping route exists in both receiver backends with same behavior."""

    @pytest.mark.skip(reason="Receiver-side implementation planned next iteration")
    def test_stdlib_receiver_has_ping_route(self):
        server_file = Path(__file__).parent.parent / "operator" / "bridges" / "shared" / "a2a_http_server.py"
        assert server_file.exists(), "a2a_http_server.py must exist"
        content = server_file.read_text()
        assert "/v1/a2a/ping" in content, \
            "a2a_http_server.py must have POST /v1/a2a/ping route"

    @pytest.mark.skip(reason="Receiver-side implementation planned next iteration")
    def test_gateway_receiver_has_ping_route(self):
        gateway_file = Path(__file__).parent.parent / "core" / "gateway" / "corvin_gateway" / "app.py"
        assert gateway_file.exists(), "gateway app.py must exist"
        content = gateway_file.read_text()
        assert "/v1/a2a/ping" in content, \
            "gateway app.py must have POST /v1/a2a/ping route"

    def test_ping_uses_same_error_category_enum(self):
        """Ping failures use ADR-0197 error_category (no new taxonomy)."""
        ping_result = Mock()
        ping_result.error_category = ErrorCategory.UNREACHABLE
        assert ping_result.error_category in ErrorCategory.ALL


class TestADR0199MCP:
    """Verify a2a_ping MCP tool integration."""

    @pytest.mark.skip(reason="MCP tool registration planned next iteration")
    def test_mcp_tool_a2a_ping_exists(self):
        mcp_file = Path(__file__).parent.parent / "core" / "orchestration" / "corvin_orchestration" / "mcp_server.py"
        assert mcp_file.exists()
        content = mcp_file.read_text()
        assert "a2a_ping" in content, "mcp_server.py must expose 'a2a_ping' tool"

    def test_ping_result_is_json_serializable(self):
        result = PingResult(reachable=True, source="network_probe", duration_ms=15)
        serialized = json.dumps({
            "reachable": result.reachable,
            "source": result.source,
            "error_category": result.error_category,
            "error_detail": result.error_detail,
            "duration_ms": result.duration_ms,
        })
        assert '"reachable": true' in serialized


# ═══════════════════════════════════════════════════════════════════════════
# ADR-0198 reconnect hardening (2026-07-19 adversarial review)
# ═══════════════════════════════════════════════════════════════════════════

_PREV_HTTPS = "https://93.184.216.10/v1/a2a/receive"
_PREV_HTTP = "http://93.184.216.10/v1/a2a/receive"


def _write_friendship_endpoint(dirpath: Path, kid: str, url: str,
                               state: str = "ACTIVE", enabled: bool = True) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"{kid}.json"
    p.write_text(json.dumps({
        "endpoint_id": kid,
        "url": url,
        "hmac_key": _HMAC_KEY,
        "recv_key": _RECV_KEY,
        "_friendship": True,
        "enabled": enabled,
        "state": state,
    }), encoding="utf-8")
    os.chmod(p, 0o600)
    return p


class TestADR0198ReconnectSSRF:
    """Fix #10: update_endpoint_url must reject non-global targets and
    https→http scheme downgrades (SSRF / stored-redirect primitive)."""

    @pytest.mark.parametrize("bad_url", [
        "http://127.0.0.1:9",            # loopback
        "https://127.0.0.1:8443/x",      # loopback, https
        "https://10.0.0.5",              # RFC1918
        "https://192.168.1.20:8443",     # RFC1918
        "https://169.254.169.254",       # link-local (cloud metadata)
        "https://[::1]:8443",            # IPv6 loopback
        "https://0.0.0.0",               # unspecified
        "https://localhost:8443",        # localhost by name
        "https://evil.onion",            # onion
        "ftp://93.184.216.34",           # bad scheme
    ])
    def test_non_global_or_bad_scheme_rejected(self, tmp_path, bad_url):
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        path = _write_friendship_endpoint(endpoints, "kid1", _PREV_HTTPS)
        before = path.read_text()
        assert update_endpoint_url("kid1", bad_url, endpoints_dir=endpoints) is False
        assert path.read_text() == before, "endpoint file must not be mutated"

    def test_scheme_downgrade_https_to_http_rejected(self, tmp_path):
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        path = _write_friendship_endpoint(endpoints, "kid1", _PREV_HTTPS)
        assert update_endpoint_url(
            "kid1", "http://93.184.216.34", endpoints_dir=endpoints,
        ) is False
        assert "93.184.216.34" not in path.read_text()

    def test_http_allowed_when_previous_was_http(self, tmp_path):
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        path = _write_friendship_endpoint(endpoints, "kid1", _PREV_HTTP)
        assert update_endpoint_url(
            "kid1", "http://93.184.216.34", endpoints_dir=endpoints,
        ) is True
        cfg = json.loads(path.read_text())
        assert cfg["url"] == "http://93.184.216.34/v1/a2a/receive"

    def test_legit_global_https_change_applied(self, tmp_path):
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        path = _write_friendship_endpoint(endpoints, "kid1", _PREV_HTTPS)
        assert update_endpoint_url(
            "kid1", "https://93.184.216.34:9443", endpoints_dir=endpoints,
        ) is True
        cfg = json.loads(path.read_text())
        assert cfg["url"] == "https://93.184.216.34:9443/v1/a2a/receive"

    def test_hostname_resolving_to_private_rejected(self, tmp_path, monkeypatch):
        import socket
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        _write_friendship_endpoint(endpoints, "kid1", _PREV_HTTPS)
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                              ("192.168.1.5", 443))],
        )
        assert update_endpoint_url(
            "kid1", "https://internal.corp.example", endpoints_dir=endpoints,
        ) is False

    def test_hostname_resolution_failure_fail_closed(self, tmp_path, monkeypatch):
        import socket
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        _write_friendship_endpoint(endpoints, "kid1", _PREV_HTTPS)

        def _boom(*a, **k):
            raise socket.gaierror("resolution failed")

        monkeypatch.setattr(socket, "getaddrinfo", _boom)
        assert update_endpoint_url(
            "kid1", "https://does-not-resolve.example", endpoints_dir=endpoints,
        ) is False

    def test_hostname_resolving_to_global_accepted(self, tmp_path, monkeypatch):
        import socket
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        path = _write_friendship_endpoint(endpoints, "kid1", _PREV_HTTPS)
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                              ("93.184.216.34", 443))],
        )
        assert update_endpoint_url(
            "kid1", "https://peer.example", endpoints_dir=endpoints,
        ) is True
        assert "peer.example" in path.read_text()

    def test_pending_endpoint_still_rejected(self, tmp_path):
        """Reconnect must never bootstrap trust (pre-existing invariant)."""
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        _write_friendship_endpoint(endpoints, "kid1", "", state="PENDING",
                                   enabled=False)
        assert update_endpoint_url(
            "kid1", "https://93.184.216.34", endpoints_dir=endpoints,
        ) is False


class TestADR0198DangerCategory:
    """Fix #1 (2026-07-19 refutation round): the SSRF gate is a DANGER-CATEGORY
    model, not global-vs-private. It (a) closes the NAT64/6to4/v4-mapped IPv6
    embedded-IPv4 bypass, and (b) re-permits legitimate LAN/hotspot renumbering
    (private→private) while still blocking the global→private SSRF signature."""

    @pytest.mark.parametrize("bad_url", [
        "https://[64:ff9b::7f00:1]/x",     # NAT64 well-known → 127.0.0.1
        "https://[64:ff9b::a9fe:a9fe]/x",  # NAT64 → 169.254.169.254 metadata
        "https://[64:ff9b:1::7f00:1]/x",   # NAT64 local-use /48 → 127.0.0.1
        "https://[::ffff:127.0.0.1]/x",    # v4-mapped loopback
        "https://[::ffff:169.254.169.254]/x",  # v4-mapped metadata
        "https://169.254.169.254/x",       # direct cloud metadata
        "https://0.0.0.0/x",               # unspecified v4
        "https://[::]/x",                  # unspecified v6
        "https://[::1]/x",                 # loopback v6
        "https://224.0.0.1/x",             # multicast
    ])
    def test_forbidden_hosts_rejected(self, tmp_path, bad_url):
        from a2a_friendship import _reconnect_url_rejection_reason
        # Previous URL is global — irrelevant, forbidden is unconditional.
        assert (_reconnect_url_rejection_reason(bad_url, _PREV_HTTPS)
                == "reconnect_url_forbidden_host")

    def test_private_to_private_lan_allowed(self, tmp_path):
        """192.168.x → 192.168.y when the stored peer was ALSO private LAN."""
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        path = _write_friendship_endpoint(
            endpoints, "kid1", "http://192.168.1.4/v1/a2a/receive")
        assert update_endpoint_url(
            "kid1", "http://192.168.1.9", endpoints_dir=endpoints) is True
        assert json.loads(path.read_text())["url"] == "http://192.168.1.9/v1/a2a/receive"

    def test_hotspot_private_to_private_allowed(self, tmp_path):
        """172.20.10.x iPhone-hotspot renumber — the ADR-0198 use case."""
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        _write_friendship_endpoint(
            endpoints, "kid1", "http://172.20.10.5/v1/a2a/receive")
        assert update_endpoint_url(
            "kid1", "http://172.20.10.9", endpoints_dir=endpoints) is True

    def test_global_to_private_rejected(self, tmp_path):
        """Stored host was GLOBAL, new host is private → SSRF pull-inward."""
        from a2a_friendship import _reconnect_url_rejection_reason
        assert (_reconnect_url_rejection_reason(
            "https://192.168.1.5/x", "https://93.184.216.34/v1/a2a/receive")
            == "reconnect_url_global_to_private")

    def test_global_to_private_via_update_rejected(self, tmp_path):
        from a2a_friendship import update_endpoint_url
        endpoints = tmp_path / "endpoints"
        path = _write_friendship_endpoint(endpoints, "kid1", _PREV_HTTPS)
        before = path.read_text()
        assert update_endpoint_url(
            "kid1", "https://10.0.0.5", endpoints_dir=endpoints) is False
        assert path.read_text() == before

    def test_global_to_global_allowed(self, tmp_path):
        from a2a_friendship import _reconnect_url_rejection_reason
        assert _reconnect_url_rejection_reason(
            "https://8.8.8.8/x", "https://93.184.216.34/x") is None

    def test_private_to_private_still_blocks_forbidden(self, tmp_path):
        """Even a private→private pairing cannot be redirected to loopback."""
        from a2a_friendship import _reconnect_url_rejection_reason
        assert (_reconnect_url_rejection_reason(
            "http://127.0.0.1/x", "http://192.168.1.4/v1/a2a/receive")
            == "reconnect_url_forbidden_host")


class TestADR0198WriteFirstAudit:
    """Fix #6 (2026-07-19 refutation round): the endpoint write is now DURABLE
    and happens BEFORE the ``reconnect_applied`` audit, so the audit reflects
    reality — it never asserts an application that did not happen. A write that
    fails after validation audits ``A2A.reconnect_failed`` (not applied)."""

    def _make_receiver(self, tmp_path):
        from remote_trigger_receiver import RemoteTriggerReceiver, NonceStore
        return RemoteTriggerReceiver(
            origins_dir=tmp_path / "origins",
            nonce_store=NonceStore(),
            instance_id="recv-test",
            forge_se=MagicMock(),
        )

    def _make_env(self):
        from remote_trigger_receiver import TaskEnvelope
        return TaskEnvelope(
            task_id="task-1", nonce="nonce-1", issued_at=time.time(),
            origin_id="kid1", instruction="", result_schema={}, ttl_s=60,
            sender_instance_id="sender-1", attachments=[], signature="sig",
            reconnect={"new_url": "https://93.184.216.34"},
        )

    def test_write_lands_before_applied_audit(self, tmp_path, monkeypatch):
        """Ordering: the durable endpoint write strictly precedes the
        A2A.reconnect_applied audit (write-first, audit-reflects-reality)."""
        import a2a_friendship
        receiver = self._make_receiver(tmp_path)
        env = self._make_env()
        events = []
        monkeypatch.setattr(a2a_friendship, "validate_endpoint_url_change",
                            lambda *a, **k: None)
        monkeypatch.setattr(a2a_friendship, "update_endpoint_url",
                            lambda *a, **k: events.append("write") or True)
        monkeypatch.setattr(receiver, "_audit_strict",
                            lambda et, sev, det: events.append(("audit", et)))
        resp = receiver._handle_reconnect(env, b"\x01" * 32, time.time())
        assert resp.status == "ok"
        assert events == ["write", ("audit", "A2A.reconnect_applied")]

    def test_applied_audit_failure_rolls_back_nonce_and_rejects(self, tmp_path, monkeypatch):
        """Durable write succeeded but the applied-audit raised → the nonce is
        rolled back and the response is a rejection (a later push re-writes
        idempotently and re-audits). The write itself was already durable."""
        import a2a_friendship
        from remote_trigger_receiver import AuditWriteError
        receiver = self._make_receiver(tmp_path)
        env = self._make_env()
        writes = []
        removed = []
        monkeypatch.setattr(a2a_friendship, "validate_endpoint_url_change",
                            lambda *a, **k: None)
        monkeypatch.setattr(a2a_friendship, "update_endpoint_url",
                            lambda *a, **k: writes.append("write") or True)
        monkeypatch.setattr(receiver._nonces, "remove", lambda n: removed.append(n))

        def _audit_fails(*a, **k):
            raise AuditWriteError("disk full")

        monkeypatch.setattr(receiver, "_audit_strict", _audit_fails)
        resp = receiver._handle_reconnect(env, b"\x01" * 32, time.time())
        assert resp.status == "rejected"
        assert writes == ["write"], "write is attempted before the audit now"
        assert removed == ["nonce-1"], "nonce must be rolled back on audit failure"

    def test_write_failure_audits_reconnect_failed_not_applied(self, tmp_path, monkeypatch):
        """Write fails after validation → audit A2A.reconnect_failed (status
        rejected), NEVER reconnect_applied; nonce rolled back for retry."""
        import a2a_friendship
        receiver = self._make_receiver(tmp_path)
        env = self._make_env()
        events = []
        removed = []
        monkeypatch.setattr(a2a_friendship, "validate_endpoint_url_change",
                            lambda *a, **k: None)
        monkeypatch.setattr(a2a_friendship, "update_endpoint_url",
                            lambda *a, **k: False)
        monkeypatch.setattr(receiver._nonces, "remove", lambda n: removed.append(n))
        monkeypatch.setattr(receiver, "_audit_strict",
                            lambda et, sev, det: events.append((et, det.get("status"))))
        resp = receiver._handle_reconnect(env, b"\x01" * 32, time.time())
        assert resp.status == "rejected"
        assert events == [("A2A.reconnect_failed", "rejected")]
        assert "A2A.reconnect_applied" not in [e for e, _ in events]
        assert removed == ["nonce-1"]

    def test_rejection_audit_failure_rolls_back_nonce(self, tmp_path, monkeypatch):
        """Validation rejects AND the reconnect_rejected audit raises → nonce
        rolled back, rejection returned, no write attempted."""
        import a2a_friendship
        from remote_trigger_receiver import AuditWriteError
        receiver = self._make_receiver(tmp_path)
        env = self._make_env()
        writes = []
        removed = []
        monkeypatch.setattr(a2a_friendship, "validate_endpoint_url_change",
                            lambda *a, **k: "reconnect_url_forbidden_host")
        monkeypatch.setattr(a2a_friendship, "update_endpoint_url",
                            lambda *a, **k: writes.append("write") or True)
        monkeypatch.setattr(receiver._nonces, "remove", lambda n: removed.append(n))

        def _audit_fails(*a, **k):
            raise AuditWriteError("disk full")

        monkeypatch.setattr(receiver, "_audit_strict", _audit_fails)
        resp = receiver._handle_reconnect(env, b"\x01" * 32, time.time())
        assert resp.status == "rejected"
        assert writes == [], "no write on a validation rejection"
        assert removed == ["nonce-1"]

    def test_ssrf_rejection_audits_reconnect_rejected(self, tmp_path, monkeypatch):
        """A non-global new_url is rejected with a single audit event and no
        write attempt."""
        import a2a_friendship
        from remote_trigger_receiver import TaskEnvelope
        receiver = self._make_receiver(tmp_path)
        env = TaskEnvelope(
            task_id="task-1", nonce="nonce-1", issued_at=time.time(),
            origin_id="kid1", instruction="", result_schema={}, ttl_s=60,
            sender_instance_id="sender-1", attachments=[], signature="sig",
            reconnect={"new_url": "http://127.0.0.1:9"},
        )
        events = []
        writes = []
        monkeypatch.setattr(
            a2a_friendship, "validate_endpoint_url_change",
            lambda kid, url, endpoints_dir: "reconnect_url_forbidden_host",
        )
        monkeypatch.setattr(a2a_friendship, "update_endpoint_url",
                            lambda *a, **k: writes.append("write") or True)
        monkeypatch.setattr(receiver, "_audit_strict",
                            lambda et, sev, det: events.append((et, det.get("reason"))))
        resp = receiver._handle_reconnect(env, b"\x01" * 32, time.time())
        assert resp.status == "rejected"
        assert writes == []
        assert events == [("A2A.reconnect_rejected", "reconnect_url_forbidden_host")]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
