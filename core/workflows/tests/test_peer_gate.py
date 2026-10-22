"""peer_gate (ADR-2276): the outbound gate of the peer_agent node and its audit helper.

Real L44 / classifier / registry code runs; only the endpoint registry is a stand-in (an in-memory
object with the registry's ``load``), and the failure cases monkeypatch the gate module itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_ROOT))
_REPO = _PKG_ROOT.parent.parent
for _p in (str(_REPO / "corvin_operator" / "bridges" / "shared"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_workflows import peer_gate  # noqa: E402
from corvin_workflows.node_types import PeerDelegationError  # noqa: E402

TENANT = "_default"
SECRET_TEXT = "token sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 please review"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "corvin_home"
    (h / "tenants" / TENANT / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    return h


class _Registry:
    def __init__(self, cfg=None, error=None):
        self.cfg, self.error = cfg, error

    def load(self, endpoint_id):
        if self.error:
            raise self.error
        return dict(self.cfg)


class _Sender:
    def __init__(self, **kw):
        self._registry = _Registry(**kw)


PAIRED = _Sender(cfg={"endpoint_id": "ep_acme", "instance_id": "iid-acme", "enabled": True})


def _policy(home, endpoints):
    import yaml

    (home / "tenants" / TENANT / "global" / "tenant.corvin.yaml").write_text(
        yaml.safe_dump({"spec": {"workflows": {"peer_policy": {"endpoints": endpoints}}}}))


def _evaluate(text, sender=PAIRED, endpoint="ep_acme"):
    return peer_gate.evaluate_peer_outbound(text, TENANT, endpoint, sender=sender)


def _chain(home):
    from core.paths import tenant_audit_chain

    p = tenant_audit_chain(TENANT)
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def test_public_text_to_a_paired_pinned_endpoint_is_allowed(home):
    d = _evaluate("Please review the attached public press release.")
    assert d.allowed and d.reason_code == "" and d.data_class == "PUBLIC"


def test_classified_text_is_refused_without_a_policy(home):
    d = _evaluate("[class:internal] quarterly numbers")
    assert (d.allowed, d.reason_code, d.data_class) == (False, "classified_above_public", "INTERNAL")


def test_policy_releases_exactly_up_to_its_grade_for_that_endpoint(home):
    _policy(home, {"ep_acme": {"max_classification": "internal"}})
    assert _evaluate("[class:internal] quarterly numbers").allowed
    assert not _evaluate("[class:confidential] customer list").allowed
    # another endpoint is not released by ep_acme's entry
    other = _Sender(cfg={"endpoint_id": "ep_other", "instance_id": "iid-o", "enabled": True})
    assert not _evaluate("[class:internal] x", sender=other, endpoint="ep_other").allowed


def test_secret_is_never_releasable_and_secret_is_not_a_valid_policy_value(home):
    _policy(home, {"ep_acme": {"max_classification": "confidential"}})
    d = _evaluate(SECRET_TEXT)
    assert (d.allowed, d.reason_code, d.data_class) == (False, "classified_above_public", "SECRET")
    _policy(home, {"ep_acme": {"max_classification": "secret"}})
    assert not _evaluate("[class:internal] x").allowed
    assert not _evaluate(SECRET_TEXT).allowed


@pytest.mark.parametrize("body", ["spec: [unclosed", "- not a mapping\n- at all", "spec: {workflows: 3}"])
def test_unreadable_or_malformed_policy_refuses_classified_text(home, body):
    (home / "tenants" / TENANT / "global" / "tenant.corvin.yaml").write_text(body)
    assert not _evaluate("[class:internal] x").allowed


def test_unreadable_policy_does_not_block_public_text(home):
    (home / "tenants" / TENANT / "global" / "tenant.corvin.yaml").write_text("spec: [unclosed")
    assert _evaluate("hello public world").allowed


def test_l44_refusal_refuses(home, monkeypatch):
    import spawn_gates

    monkeypatch.setattr(spawn_gates, "check_l44", lambda *a, **k: "[house-rules] refused")
    d = _evaluate("anything")
    assert (d.allowed, d.reason_code) == (False, "l44_refused")


def test_l44_error_and_unimportable_gate_refuse_fail_closed(home, monkeypatch):
    import spawn_gates

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(spawn_gates, "check_l44", boom)
    assert _evaluate("x").reason_code == "gate_unavailable"
    monkeypatch.undo()
    monkeypatch.setitem(sys.modules, "spawn_gates", None)  # `import spawn_gates` -> ImportError
    d = _evaluate("x")
    assert (d.allowed, d.reason_code) == (False, "gate_unavailable")


def test_classifier_error_refuses(home, monkeypatch):
    import data_classification

    def boom(*a, **k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(data_classification, "classify_task", boom)
    assert _evaluate("x").reason_code == "gate_unavailable"


@pytest.mark.parametrize("sender,reason", [
    (_Sender(error=RuntimeError("unknown_endpoint")), "endpoint_not_paired"),
    (_Sender(cfg={"endpoint_id": "ep_acme", "instance_id": "", "enabled": True}), "endpoint_not_pinned"),
    (_Sender(cfg={"endpoint_id": "ep_acme", "enabled": True}), "endpoint_not_pinned"),
])
def test_unpaired_or_unpinned_endpoint_refuses(home, sender, reason):
    d = _evaluate("public text", sender=sender)
    assert (d.allowed, d.reason_code) == (False, reason)


def test_audit_event_lands_in_the_tenant_chain_without_text(home):
    ok = peer_gate.audit_peer_event(
        "workflow.peer_refused", TENANT, strict=True, wid="wf", rid="r1", node_id="n1",
        endpoint_id="ep_acme", agent_id="rev", data_class="INTERNAL", reason_code="classified_above_public",
        instruction="LEAK-ME-NOT",  # not allow-listed: the writer drops it
    )
    assert ok
    rec = _chain(home)[-1]
    assert rec["event_type"] == "workflow.peer_refused" and rec["severity"] == "WARNING"
    d = rec["details"]
    assert d["reason_code"] == "classified_above_public" and d["node_id"] == "n1"
    assert "LEAK-ME-NOT" not in json.dumps(rec)


def test_strict_audit_failure_raises_and_lenient_one_reports_false(home, monkeypatch):
    fa = peer_gate._federation_module("audit")

    def boom():
        def w(*a, **k):
            raise OSError("disk full")
        return w

    monkeypatch.setattr(fa, "_core_write_event", boom)
    with pytest.raises(PeerDelegationError) as exc:
        peer_gate.audit_peer_event("workflow.peer_delegated", TENANT, strict=True, node_id="n")
    assert str(exc.value) == "audit_unavailable"
    assert peer_gate.audit_peer_event("workflow.peer_completed", TENANT, node_id="n") is False
