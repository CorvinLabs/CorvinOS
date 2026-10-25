"""Audit & Compliance panel: custom-layer fields survive the details floor, the Roles card finds the
bridge writers' pre-tenancy files (default tenant only), and the chain can be verified from the panel.

Red on origin/main (925406772):
  * ``routes/audit_layers.py`` emitted a custom event with no registered allowlist, so the core
    writer's floor kept only ``layer_id`` and listed the declared fields under ``_dropped_fields`` (D1);
  * ``routes/members.py`` read only ``tenants/<tid>/global/<store>/`` while ``roles.py`` /
    ``disclosure.py`` / ``consent.py`` write ``<home>/global/<store>/`` when the adapter passes no tenant (D4);
  * no ``POST /audit/verify`` existed (D0).
Every request is HTTP against a real uvicorn console. The console audit writer is one-tenant-per-process
(AuditTenantMismatch), so records are asserted for the process tenant ``_default``.
"""
from __future__ import annotations

import json
import time

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .helpers_gap import action_records, chain_events, tenant_chain, verify_tenant_chain  # noqa: E402

LAYER = "sbxspotify"
EVENT = f"{LAYER}.chart_update"
FIELDS = ["track_uri", "chart_date", "streams"]


def _put_layer(server, sess, fields=None, layer=LAYER, event=EVENT):
    return harness.fetch(server, sess, f"/audit/layers/{layer}", method="PUT", json_body={
        "display_name": "Spotify analytics", "event_types": [event],
        "allowed_fields": fields if fields is not None else FIELDS, "description": "demo"})


def test_G_compliance_custom_layer_fields_reach_the_chain(sandbox, server, session_for):
    sess = session_for("_default")
    r = _put_layer(server, sess)
    assert r.status_code == 200, r.text[:300]
    details = {"track_uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC", "chart_date": "2024-01-01", "streams": 123456}
    e = harness.fetch(server, sess, "/audit/emit", method="POST",
                      json_body={"layer_id": LAYER, "event_type": EVENT, "details": details})
    assert e.status_code == 200, e.text[:300]
    recs = chain_events(sandbox, "_default", EVENT)
    assert recs, "the custom event is not on the tenant chain"
    got = recs[-1]["details"]
    assert got["track_uri"] == details["track_uri"] and got["chart_date"] == "2024-01-01", got
    assert got["streams"] == 123456 and got["layer_id"] == LAYER, got
    assert "_dropped_fields" not in got, f"fields were stripped by the details floor: {got}"
    assert verify_tenant_chain(sandbox, "_default")["ok"]
    # visible through the panel's own tail route as well
    tail = harness.fetch(server, sess, f"/audit/tail?limit=50&event_prefix={LAYER}.")
    assert tail.status_code == 200 and "track_uri" in tail.text


def test_G_compliance_layer_registered_before_a_restart_still_emits(sandbox, server, session_for):
    """The allowlist is process memory: a layer file written earlier (another process) must work too."""
    sess = session_for("_default")
    layers = sandbox.tenant_home("_default") / "global" / "audit_layers"
    layers.mkdir(parents=True, exist_ok=True)
    layer = "sbxlegacy"
    (layers / f"{layer}.json").write_text(json.dumps({
        "layer_id": layer, "display_name": "Legacy", "event_types": [f"{layer}.ping"],
        "allowed_fields": ["shard_id"], "description": "", "created_at": time.time(), "updated_at": time.time()}))
    e = harness.fetch(server, sess, "/audit/emit", method="POST",
                      json_body={"layer_id": layer, "event_type": f"{layer}.ping", "details": {"shard_id": "s7"}})
    assert e.status_code == 200, e.text[:300]
    got = chain_events(sandbox, "_default", f"{layer}.ping")[-1]["details"]
    assert got.get("shard_id") == "s7" and "_dropped_fields" not in got, got


def test_G_compliance_floor_refused_field_names_fail_loudly_at_registration(server, session_for):
    sess = session_for("_default")
    for bad in ("message", "payload"):
        r = _put_layer(server, sess, fields=[bad], layer="sbxbad", event="sbxbad.x")
        # either the console's own list or the audit floor refuses it: never a silent drop later
        assert r.status_code == 400, f"{bad!r}: {r.status_code} {r.text[:200]}"


def test_G_compliance_roles_card_reads_the_pre_tenancy_stores_for_the_default_tenant_only(
        server, sandbox, session_for):
    legacy = sandbox.corvin_home / "global"
    for kind, body in (("roles", {"u1": {"bundle": "member"}, "u2": {"bundle": "admin"}}),
                       ("consent", {"u1": {"granted": True}}),
                       ("disclosure", {"u1": {"seen": True}})):
        d = legacy / kind
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord__sbxlegacychat.json").write_text(json.dumps(body))
    own = sandbox.tenant_home("sbxm") / "global" / "roles"
    own.mkdir(parents=True, exist_ok=True)
    (own / "discord__sbxmchat.json").write_text(json.dumps({"m1": {"bundle": "member"}}))

    default = harness.fetch(server, session_for("_default"), "/members").json()
    keys = {c["chat_key"]: c for c in default["chats"]}
    assert "discord__sbxlegacychat" in keys, default
    row = keys["discord__sbxlegacychat"]
    assert row["members"] == 2 and row["consent_entries"] == 1 and row["disclosure_entries"] == 1, row
    detail = harness.fetch(server, session_for("_default"), "/members/discord__sbxlegacychat")
    assert detail.status_code == 200 and detail.json()["uid_count"] == 2

    other = harness.fetch(server, session_for("sbxm"), "/members").json()
    other_keys = {c["chat_key"] for c in other["chats"]}
    assert other_keys == {"discord__sbxmchat"}, f"a non-default tenant must not see the legacy stores: {other_keys}"
    assert harness.fetch(server, session_for("sbxm"), "/members/discord__sbxlegacychat").status_code == 404


def test_G_compliance_verify_chain_button_route_reports_an_intact_chain(sandbox, server, session_for):
    sess = session_for("_default")
    harness.fetch(server, sess, "/audit/tail?limit=5")  # make sure the chain exists
    r = harness.fetch(server, sess, "/audit/verify", method="POST", json_body={})
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["ok"] is True and body["problem_count"] == 0 and body["records"] >= 1, body
    assert body["present"] is True and body["tenant_id"] == "_default"
    rec = action_records(sandbox, "_default", "audit.chain_verify")[-1]
    assert rec["event_type"] == "console.action_performed"
    assert verify_tenant_chain(sandbox, "_default")["ok"]


def test_G_compliance_verify_chain_requires_csrf(server, session_for):
    sess = session_for("_default")
    with server.client() as c:
        r = c.post("/audit/verify", cookies=sess.cookies(), json={})
    assert r.status_code in (401, 403), r.status_code


WRITE_CHAIN = r"""
import sys
from pathlib import Path
from corvin_console import audit
chain = Path(sys.argv[1])
chain.parent.mkdir(parents=True, exist_ok=True)
for i in range(4):
    audit._security_events.write_event(chain, "sbx.verify_probe", details={"layer_id": f"n{i}"})
"""


def test_G_compliance_verify_chain_detects_a_tampered_record(sandbox, server, session_for):
    sandbox.run_child(WRITE_CHAIN, str(tenant_chain(sandbox, "sbxv")))
    sess = session_for("sbxv")
    good = harness.fetch(server, sess, "/audit/verify", method="POST", json_body={}).json()
    assert good["ok"] is True and good["records"] >= 4, good
    chain = tenant_chain(sandbox, "sbxv")
    lines = chain.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["details"]["layer_id"] = "tampered"
    lines[1] = json.dumps(rec)
    chain.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = harness.fetch(server, sess, "/audit/verify", method="POST", json_body={}).json()
    assert bad["ok"] is False and bad["problem_count"] >= 1, bad
    assert bad["problems"][0]["issue"], bad
