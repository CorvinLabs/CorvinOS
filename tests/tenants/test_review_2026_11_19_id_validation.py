"""Regression tests for the 2026-11-19 review: id validation and honest stubs (TEN-9, TEN-10)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from core.tenants.validation import (
    validate_channel_id,
    validate_session_id,
    validate_tenant_id,
)


# ----------------------------------------------------------------- TEN-9

@pytest.mark.parametrize("bad", [
    ".", "..", "...", "a/b", "a\\b", "a\x00b", "", "   ", "x" * 129,
    "session\n", "\nsession", "ses sion", "a:b", ".hidden", "-/x", "./x",
    "a\tb", "a;rm", "a$b", "a*", "s‮", 5, None,
])
def test_session_id_rejects(bad):
    with pytest.raises(ValueError):
        validate_session_id(bad)


@pytest.mark.parametrize("good", [
    str(uuid.uuid4()), "session_123456", "discord_12345", "1502103856740302964",
    "x" * 128, "a.b-c_d", "user@example.com", "Chat+1=2",
    # Telegram group / supergroup chat ids are negative (review round 2).
    "-1001234567890", "-4012345678", "-leading",
])
def test_session_id_accepts(good):
    assert validate_session_id(good) == good


@pytest.mark.parametrize("bad", ["discord\n", "\ndiscord", "Discord", "a-b", "", "x" * 65, "a/b"])
def test_channel_id_is_fullmatched(bad):
    with pytest.raises(ValueError):
        validate_channel_id(bad)


def test_channel_and_tenant_accept_the_usual():
    assert validate_channel_id("discord") == "discord"
    assert validate_tenant_id("acme-corp") == "acme-corp"
    with pytest.raises(ValueError):
        validate_tenant_id("acme\n")


def test_a_telegram_group_id_names_its_own_session_directory():
    from core.paths.tenant import tenant_session_dir
    d = tenant_session_dir("_default", "-1001234567890")
    assert d.name == "-1001234567890"
    assert d.parent == tenant_session_dir("_default", "1").parent


def test_session_dir_cannot_be_the_sessions_root():
    from core.paths.tenant import tenant_session_dir
    with pytest.raises(ValueError):
        tenant_session_dir("_default", ".")


def test_ulid_is_fullmatched():
    from core.personal_kb.ids import is_ulid, new_ulid
    good = new_ulid()
    assert is_ulid(good)
    assert not is_ulid(good + "\n")
    assert not is_ulid(good + "X")


def _manifest_module():
    from core.projects import manifest
    return manifest


@pytest.mark.parametrize("name,value", [
    ("PROJECT_ID_RE", "my-project\n"),
    ("FEATURE_ID_RE", "feature_x\n"),
    ("TEMPLATE_RE", "base@1\n"),
    ("_TENANT_RE", "acme\n"),
])
def test_manifest_patterns_reject_a_trailing_newline_when_fullmatched(name, value):
    pattern = getattr(_manifest_module(), name)
    assert pattern.fullmatch(value) is None
    assert pattern.fullmatch(value.strip()) is not None
    assert pattern.match(value) is not None   # the defect: ``match`` + ``$`` admitted it


def test_manifest_source_uses_no_prefix_match():
    import inspect
    src = inspect.getsource(_manifest_module())
    assert "_RE.match(" not in src and "re.match(" not in src


def test_new_project_id_rejects_a_trailing_newline():
    m = _manifest_module()
    assert m.is_new_id_allowed("my-project") is True
    assert m.is_new_id_allowed("my-project\n") is False


# ---------------------------------------------------------------- TEN-10

def test_multi_tenant_manager_no_longer_claims_quota():
    from core.multi_tenant.tenant_isolation import MultiTenantManager, QuotaNotImplemented
    mgr = MultiTenantManager()
    assert asyncio.run(mgr.check_quota("tenant1", 100.0)) is False
    with pytest.raises(QuotaNotImplemented):
        asyncio.run(mgr.deduct_cost("tenant1", 50.0))
    with pytest.raises(ValueError):
        asyncio.run(mgr.check_quota("../escape", 1.0))
    for bad in (float("nan"), float("inf"), -1.0, True, "5"):
        with pytest.raises(ValueError):
            asyncio.run(mgr.check_quota("tenant1", bad))


def test_unknown_credential_is_not_valid(tmp_path):
    from core.compliance.audit_chain_writer import AuditChainWriter
    from core.license.credential_revocation_list import CredentialRevocationList

    chain = tmp_path / "audit.jsonl"
    crl = CredentialRevocationList("_default", AuditChainWriter(chain))
    assert crl.check_credential_valid("never-registered") is False
    assert "credential_unknown" in chain.read_text(encoding="utf-8")

    crl.register_credential("known", datetime.utcnow() + timedelta(days=30))
    assert crl.check_credential_valid("known") is True
    crl.revoke_credential("known")
    assert crl.check_credential_valid("known") is False


def test_stability_window_really_rotates(monkeypatch):
    from core.telemetry import stability_metrics as sm

    hour = {"now": 500_000}
    monkeypatch.setattr(sm, "_current_hour", lambda: hour["now"])
    sm.reset_metrics()
    try:
        for _ in range(3):
            sm.mark_invocation("flag.a")
        sm.mark_error("flag.a", RuntimeError("boom"))
        m = sm.get_flag_metrics("flag.a")
        assert m.get_24h_stats()["invocation_count_24h"] == 3

        hour["now"] += 1
        sm.mark_invocation("flag.a")
        assert list(m.hourly_invocations) == [3, 1]
        assert list(m.hourly_errors) == [1, 0]

        hour["now"] += 22                       # 23 h after the first bucket: still inside
        stats = m.get_24h_stats()
        assert stats["invocation_count_24h"] == 4 and stats["error_count_24h"] == 1
        assert len(m.hourly_invocations) == 24 == len(m.hourly_errors)

        hour["now"] += 1                        # 24 h: the first bucket leaves
        stats = m.get_24h_stats()
        assert stats["invocation_count_24h"] == 1 and stats["error_count_24h"] == 0

        hour["now"] += 10_000                   # long idle: everything expired, bounded work
        stats = m.get_24h_stats()
        assert stats["invocation_count_24h"] == 0 and stats["error_rate_24h"] == 0.0
        assert len(m.hourly_invocations) == 24

        hour["now"] -= 5                        # clock stepped back: nothing lost, no crash
        sm.mark_invocation("flag.a")
        assert m.get_24h_stats()["invocation_count_24h"] == 1
        assert sm.compute_digest().flags_enabled[0]["invocation_count_24h"] == 1
    finally:
        sm.reset_metrics()


def test_deprecated_call_records_a_relative_caller_file(monkeypatch):
    from core.telemetry import deprecated_api_calls as dac

    rel = dac._relative_caller_file(__file__)
    assert rel == "tests/tenants/test_review_2026_11_19_id_validation.py"
    assert dac._relative_caller_file("/home/someone/secret/place/mod.py") == "mod.py"
    assert dac._relative_caller_file("") in ("", ".")

    captured = {}
    import core.learning.event_persistence as ep
    monkeypatch.setattr(dac, "_register_audit_allowlist", lambda: None)
    monkeypatch.setattr(ep, "core_audit_event",
                        lambda name, *, tenant_id, details: captured.update(details))
    event = dac.log_deprecated_call("old_api", "core.old", tenant_id="_default")
    assert captured["caller_file"] == rel == event.caller_file
    assert not captured["caller_file"].startswith("/")


def test_telemetry_server_binds_loopback_and_ignores_the_query_tenant():
    pytest.importorskip("flask")
    pytest.importorskip("flask_cors")
    import inspect
    from core.telemetry.api_server import TelemetryAPIServer

    assert inspect.signature(TelemetryAPIServer.__init__).parameters["host"].default == "127.0.0.1"

    class Agg:
        instances: dict = {}
        def __init__(self): self.asked = []
        def get_cluster_stats(self, tenant_id):
            self.asked.append(tenant_id)
            class S:
                def to_dict(self_inner): return {"tenant": tenant_id}
            return S()
        def get_active_instances(self, tenant_id):
            self.asked.append(tenant_id); return []
        def get_active_subscribers(self): return 0
        def register_instance(self, **kw): self.asked.append(("register", kw["tenant_id"]))
        def submit_telemetry(self, **kw): self.asked.append(("submit", kw["tenant_id"]))

    agg = Agg()
    server = TelemetryAPIServer(agg, tenant_id="tenant-a")
    assert server.host == "127.0.0.1"
    http = server.app.test_client()

    assert http.get("/api/metrics/stats?tenant_id=tenant-b").get_json() == {"tenant": "tenant-a"}
    assert http.get("/api/telemetry/instances?tenant_id=tenant-b").status_code == 200
    assert agg.asked == ["tenant-a", "tenant-a"]

    body = {"instance_id": "i1", "tenant_id": "tenant-b", "turn_count": 1, "total_tokens": 2}
    assert http.post("/api/telemetry/submit", json=body).status_code == 403
    assert http.post("/api/telemetry/submit", data="[]", content_type="application/json").status_code == 400
    body["tenant_id"] = "tenant-a"
    assert http.post("/api/telemetry/submit", json=body).status_code == 200
    assert ("submit", "tenant-a") in agg.asked and ("submit", "tenant-b") not in agg.asked

    with pytest.raises(ValueError):
        TelemetryAPIServer(agg, tenant_id="../escape")
