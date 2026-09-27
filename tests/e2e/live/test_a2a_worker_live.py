"""LIVE: an inbound A2A task runs on the real ``claude`` CLI and is priced.

Opt-in: ``CLAUDE_LIVE_E2E=1`` and the ``claude`` CLI on PATH (one short turn).

Entry point: ``a2a_worker.spawn_a2a_worker`` — the single call the L38
``RemoteTriggerReceiver`` makes after it has verified an envelope
(``remote_trigger_receiver.py`` ``spawn_a2a_worker(...)``), with the receiver's
own defaults: no ``engine_factory`` (→ the real ``ClaudeCodeEngine``), the
sender's default ``CHAT_RESULT_SCHEMA`` and the subagent tools denied. Envelope
signing/pairing is NOT exercised here (that needs two paired instances; the
ten-peer E2E covers it with a scripted model) — this test covers the half the
ten-peer E2E replaces: the real model.

Asserted (52028827a "chat replies no longer empty"; ADR-0759 A2A span):

1. status ``ok`` and the reply is delivered as ``{"output": "<text>"}`` with the
   requested token — not the empty ``{}`` a free-text answer used to become;
2. the worker's ``engine.span.end`` (role ``worker``) in the tenant chain names
   the model the ENGINE reported and carries a real token split;
3. ``model_usage`` counts it under that model with ``roles.worker``;
4. no instruction text in the chain.

Run: CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -o addopts="" \
     -p no:cacheprovider tests/e2e/live/test_a2a_worker_live.py -s
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
        reason="live A2A worker E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
    ),
]

HAIKU = "claude-haiku-4-5-20251001"


def test_inbound_a2a_task_answers_on_real_claude_and_is_priced(tmp_path: Path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    # A2A passes no model=; the CLI's own default applies. Pin that default to
    # haiku through the CLI's env so the run is cheap — the span must still
    # report what the engine ATTESTED, never this config value by assumption.
    monkeypatch.setenv("ANTHROPIC_MODEL", HAIKU)
    for p in (str(SHARED), str(REPO)):
        if p not in sys.path:
            sys.path.insert(0, p)

    from core.paths import tenant_audit_chain

    chain = Path(tenant_audit_chain("_default"))
    assert home in chain.parents, chain
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))

    import a2a_worker  # noqa: PLC0415
    from remote_trigger_sender import CHAT_RESULT_SCHEMA  # noqa: PLC0415

    task_id = f"t-live-{uuid.uuid4().hex[:10]}"
    instruction = "Reply with exactly the single word PONG and nothing else."
    res = a2a_worker.spawn_a2a_worker(
        instruction=instruction,
        origin_id="origin-live-e2e",
        task_id=task_id,
        persona="assistant",
        ttl_s=180,
        result_schema=CHAT_RESULT_SCHEMA,
        disallowed_tools=["Task", "Agent", "TodoWrite", "TodoRead"],
    )
    print(f"\n[live] a2a status={res.status} engine={res.engine_name} "
          f"parsed={res.parsed_output!r} err={res.error!r} {res.duration_ms}ms")
    assert res.status == "ok", res.error
    assert "PONG" in str(res.parsed_output.get("output", "")).upper(), res.parsed_output

    events = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    print(f"[live] tenant chain: {[e.get('event_type') for e in events]}")
    ends = [e.get("details") or {} for e in events if e.get("event_type") == "engine.span.end"
            and (e.get("details") or {}).get("role") == "worker"]
    assert ends, "no worker engine.span.end in the tenant chain for the A2A run"
    end = ends[-1]
    print(f"[live] worker span end: {end}")
    assert end.get("model_id"), f"A2A worker span has no model_id (unpriceable): {end}"
    assert end.get("model_id", "").startswith("claude-haiku"), end
    assert int(end.get("output_tokens") or 0) > 0, f"A2A worker span has no token split: {end}"

    from corvin_console.model_usage import model_usage  # noqa: PLC0415

    rows = {m["model_id"]: m for m in model_usage("_default")["models"]}
    row = rows.get(end["model_id"])
    assert row and row["roles"].get("worker", 0) >= 1 and row["output_tokens"] > 0, rows
    assert "single word PONG" not in chain.read_text(), "instruction text leaked into the chain"
