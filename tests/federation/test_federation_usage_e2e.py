"""Two-installation E2E for the signed usage block on federated tasks (ADR-2292).

Instance B runs a REAL ``a2a_http_server``; instance A delegates through the REAL ``RemoteTriggerSender``
(signed TaskEnvelope, signed ResponseEnvelope) via ``core.federation.delegation.delegate`` — and, for the
per-partner view, through the REAL console routes (``/auth/local-login`` + ``/v1/console/federation/usage``).
Only B's engine is a stand-in: it reports the model it was pinned to and a four-way usage split, the way the
Claude CLI's frames do.

Covered: the block on the requester side and in BOTH audit records (B: ``A2A.response_signed`` +
``federation.task_completed``; A: ``federation.task_result_received`` on its real hash chain); no block and no
usage fields when the engine reports no model; the per-partner console sums (real tokens, priced with A's own
rate card, an unknown model shown as tokens only, the peer's catalogue figure labelled as declared, the ADR-0760
window narrowing the sums); selection never reads the block.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from tests.federation.test_federation_cross_peer_e2e import (  # noqa: F401 — fixture
    TENANT,
    _RecordingEngine,
    _chain_events,
    federation_pair,
)

BASE = "/v1/console/federation"
_USAGE_FIELDS = ("model_id", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


@dataclass
class _Ev:
    type: str
    text: str | None = None
    usage: dict | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)


def _engine_reports(monkeypatch, *, model: str | None, usage: dict | None) -> None:
    """B's engine reports ``model`` (``"<pinned>"`` = the model it was spawned with) and ``usage``."""

    def spawn(self, prompt, **kwargs):
        _RecordingEngine.spawns.append(dict(kwargs))
        reported = kwargs.get("model") if model == "<pinned>" else model
        raw = {"type": "result", **({"model": reported} if reported else {})}
        return iter([_Ev(type="text_delta", text="done"),
                     _Ev(type="turn_completed", text="done", usage=usage, raw=raw)])

    monkeypatch.setattr(_RecordingEngine, "spawn", spawn)


_USAGE = {"input_tokens": 2000, "output_tokens": 500,
          "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 1000}


def _details(events: list[dict], name: str) -> dict:
    hits = [e for e in events if e.get("event_type") == name]
    assert hits, f"{name} was not recorded"
    return hits[-1].get("details", {})


def test_delegated_task_carries_the_usage_block_into_both_chains(federation_pair, monkeypatch):
    from core.federation.delegation import DelegationLedger, delegate

    _engine_reports(monkeypatch, model="<pinned>", usage=_USAGE)
    res = delegate(TENANT, instruction="Summarise the attached notes.", endpoint_id="peer-B",
                   agent_id="haiku-cheap", sender=federation_pair["sender"])
    assert res.ok, res.to_dict()
    model = _RecordingEngine.spawns[-1]["model"]
    want = {"model_id": model, "input_tokens": 2000, "output_tokens": 500,
            "cache_read_tokens": 10000, "cache_write_tokens": 1000}
    assert res.usage == {**want, "source": "engine_attested"}

    # Receiver (B): the signing record and the federation completion record carry the scalars.
    b_events = federation_pair["se_b"].events
    for name in ("A2A.response_signed", "federation.task_completed"):
        d = _details(b_events, name)
        assert {k: d.get(k) for k in want} == want, name
    # Requester (A): the REAL chain record (allowlist applied by the real writer) carries them too.
    d = _details(_chain_events(federation_pair["home"]), "federation.task_result_received")
    assert {k: d.get(k) for k in want} == want
    assert "Summarise" not in json.dumps(d)
    # And the ledger the per-partner view reads.
    rec = DelegationLedger(TENANT).latest_by_task()[res.task_id]
    assert rec["usage"] == want


def test_no_model_reported_means_no_block_and_no_usage_fields(federation_pair, monkeypatch):
    from core.federation.delegation import DelegationLedger, delegate

    _engine_reports(monkeypatch, model=None, usage=_USAGE)
    res = delegate(TENANT, instruction="hi", endpoint_id="peer-B", agent_id="haiku-cheap",
                   sender=federation_pair["sender"])
    assert res.ok, res.to_dict()
    assert res.usage == {}
    for name in ("A2A.response_signed", "federation.task_completed"):
        assert not set(_details(federation_pair["se_b"].events, name)) & set(_USAGE_FIELDS)
    d = _details(_chain_events(federation_pair["home"]), "federation.task_result_received")
    assert not set(d) & set(_USAGE_FIELDS)
    assert "usage" not in DelegationLedger(TENANT).latest_by_task()[res.task_id]


def test_selection_ignores_the_usage_block(federation_pair, monkeypatch):
    from core.federation.delegation import delegate, rank_candidates
    from core.federation.peer_catalog import PeerCatalog

    PeerCatalog(TENANT).refresh("peer-B", sender=federation_pair["sender"])
    before = [c.agent_id for c in rank_candidates(TENANT, "analysis", include_local=False)]
    # A huge measured run on the agent that DECLARES the lower cost must not reorder anything.
    _engine_reports(monkeypatch, model="<pinned>",
                    usage={"input_tokens": 9_000_000, "output_tokens": 9_000_000})
    assert delegate(TENANT, instruction="x", endpoint_id="peer-B", agent_id="haiku-cheap",
                    sender=federation_pair["sender"]).ok
    after = [c.agent_id for c in rank_candidates(TENANT, "analysis", include_local=False)]
    assert before == after == ["haiku-cheap", "opus-code"]


# ── per-partner console view (real console transport) ────────────────────

@pytest.fixture
def console(federation_pair, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import core.federation.delegation as delegation_mod
    import core.federation.peer_catalog as catalog_mod
    from core.console.corvin_console.app import router as console_router

    monkeypatch.setattr(catalog_mod, "default_sender", lambda: federation_pair["sender"])
    monkeypatch.setattr(delegation_mod, "default_sender", lambda: federation_pair["sender"])
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", "/bin/false")
    app = FastAPI()
    app.include_router(console_router, prefix="/v1/console")
    with TestClient(app, client=("127.0.0.1", 51237)) as c:
        r = c.get("/v1/console/auth/local-login", follow_redirects=False)
        assert r.status_code in (200, 302, 307), r.text
        csrf = c.get("/v1/console/auth/whoami").json()["csrf_token"]
        yield c, {"x-csrf-token": csrf}, federation_pair


def _no_percentages(obj) -> None:
    text = json.dumps(obj).lower()
    assert "percent" not in text and "pct" not in text and "average" not in text and "avg" not in text


def test_console_shows_real_token_sums_per_partner_priced_with_our_own_rates(console, monkeypatch):
    from core.learning.model_selection_learner import _turn_cost_usd, model_price_per_1k

    c, h, pair = console
    assert c.post(f"{BASE}/peers/peer-B/refresh", headers=h).status_code == 200
    # Two runs the requester can price (the pinned Haiku), one on a model it does not know.
    _engine_reports(monkeypatch, model="<pinned>", usage=_USAGE)
    for _ in range(2):
        r = c.post(f"{BASE}/delegate", json={"instruction": "Check this.", "endpoint_id": "peer-B",
                                             "agent_id": "haiku-cheap"}, headers=h)
        assert r.status_code == 200 and r.json()["ok"], r.text
    haiku = _RecordingEngine.spawns[-1]["model"]
    _engine_reports(monkeypatch, model="llama-3.2-3b", usage={"input_tokens": 300, "output_tokens": 40})
    r = c.post(f"{BASE}/delegate", json={"instruction": "Check that.", "endpoint_id": "peer-B",
                                         "agent_id": "haiku-cheap"}, headers=h)
    assert r.status_code == 200 and r.json()["ok"], r.text

    body = c.get(f"{BASE}/usage").json()
    _no_percentages(body)
    assert body["window"]["active"] is False
    (row,) = body["partners"]
    assert row["endpoint_id"] == "peer-B" and row["peer_instance_id"] == pair["iid_b"]
    assert row["tasks"] == 3 and row["tasks_with_usage"] == 3
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"], row["cache_write_tokens"]) \
        == (2 * 2000 + 300, 2 * 500 + 40, 2 * 10000, 2 * 1000)
    models = {m["model_id"]: m for m in row["models"]}
    price = model_price_per_1k(haiku)
    assert price is not None, "the test needs a model this installation can price"
    want_usd = 2 * _turn_cost_usd(price, 2000, 500, 1000, 10000, model=haiku)
    assert models[haiku]["priced"] is True
    assert models[haiku]["cost_usd"] == pytest.approx(want_usd, rel=1e-6)
    assert models["llama-3.2-3b"]["priced"] is False and models["llama-3.2-3b"]["cost_usd"] is None
    assert models["llama-3.2-3b"]["input_tokens"] == 300
    assert row["cost_usd"] == pytest.approx(want_usd, rel=1e-6)
    assert (row["priced_tasks"], row["unpriced_tasks"]) == (2, 1)
    # The peer's own catalogue figure is shown, labelled as declared, and never summed into the cost.
    declared = {d["agent_id"]: d for d in row["declared_by_peer"]}
    assert declared["haiku-cheap"]["cost_per_task_usd"] == pytest.approx(0.01)

    # The ADR-0760 window narrows the sums: an epoch after these runs counts nothing.
    import time

    from core.console.corvin_console import usage_epoch
    usage_epoch.set_epoch(TENANT, at=time.time() + 1)
    try:
        narrowed = c.get(f"{BASE}/usage").json()
    finally:
        usage_epoch.clear_epoch(TENANT)
    assert narrowed["window"]["active"] is True
    assert narrowed["partners"] == []
