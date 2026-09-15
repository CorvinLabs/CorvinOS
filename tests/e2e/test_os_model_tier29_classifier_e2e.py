"""E2E wiring proof for Tier 2.9 — the complexity classifier as a REAL routing input.

Before this tier existed, ``resolve_os_model`` fell through to Tier 3
(``autoselect_os_model``), which returns Sonnet unconditionally unless
``CORVIN_OS_MODEL_ALLOW_HAIKU=1``. The real ``ModelSelector`` ran in shadow mode:
it classified every turn, recommended Haiku, and the recommendation was
discarded. Consequence for the Model Cost Optimizer panel: ``cost_model_mix``
had exactly one key, so the "savings" it displayed was Sonnet's fixed price
ratio against the Opus baseline — a pricing constant, not a routing outcome.

What this file proves, in the order the E2E-wiring-proof gate asks for it:

Phase 1 (reachability) — ``TestReachability`` asserts the console's real turn
    path passes ``task_input=`` into ``resolve_os_model``. Tier 2.9 is opt-in per
    surface: without that keyword the tier is skipped entirely, so a silent
    removal of the argument would revert routing to single-model while every
    unit test stayed green.

Phase 2 (real transport) — ``TestTier29OverWebSocket`` drives the actual console
    WebSocket route ``/v1/console/chat/sessions/{sid}/stream`` after a real
    ``/auth/local-login`` + CSRF handshake, and asserts on the argv the engine
    subprocess was really launched with. The only thing replaced is the paid
    Claude CLI binary (via the existing ``CORVIN_CLAUDE_BIN`` seam) — the
    console app, session auth, chat_runtime, model_selector and the hash-chained
    audit writer are all the production code paths.

Live confirmation on the running install (2026-09-15, port 8765, uvicorn
serving this working tree) recorded these two chained audit records for one
short web-chat turn — the first non-Sonnet OS turn this install has ever
served:

    os_model.classified  complexity=simple confidence=0.85
                         selected_model=claude-haiku-4-5-20251001
                         tier=2.9_classifier payload_chars=12218
                         outcome=applied            hash=77c199d297a65433
    os_turn.completed    model=claude-haiku-4-5-20251001
                                                    hash=7b3ba2d34ca13970

and ``/v1/console/learning/model-cost-optimizer/status`` then reported
``cost_model_mix = {"claude-sonnet-5": 3, "claude-haiku-4-5-20251001": 1}``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: What Tier 2.9 must route a short, simple prompt to. This is the operator's
#: own SIMPLE mapping from ``model_selection_config.py::_DEFAULTS``, not a value
#: invented by the test.
EXPECTED_SIMPLE_MODEL = "claude-haiku-4-5-20251001"


# ── Phase 1 — reachability ───────────────────────────────────────────────────


class TestReachability:
    """Tier 2.9 has a production call site, and it is fed the prompt."""

    def test_console_turn_passes_task_input_to_resolver(self) -> None:
        src = (
            REPO_ROOT / "core" / "console" / "corvin_console" / "chat_runtime.py"
        ).read_text(encoding="utf-8")
        idx = src.find("resolve_os_model(")
        assert idx != -1, "console web-chat no longer calls resolve_os_model"
        call = src[idx : idx + 900]
        assert "task_input=" in call, (
            "the console turn stopped passing task_input=, which silently skips "
            "Tier 2.9 and reverts every turn to the Sonnet-only Tier 3 default"
        )

    def test_resolver_consults_the_classifier(self) -> None:
        src = (
            REPO_ROOT / "corvin_operator" / "bridges" / "shared" / "model_selector.py"
        ).read_text(encoding="utf-8")
        assert "def classify_os_model(" in src
        assert "classify_os_model(" in src.split("def resolve_os_model(", 1)[1], (
            "resolve_os_model no longer calls classify_os_model — Tier 2.9 is dead"
        )

    def test_classifier_abstains_below_the_confidence_floor(self) -> None:
        """An uncertain verdict must not route. ModelSelector scores an
        uncertain/"medium" classification 0.60 and a keyword-only hint 0.70;
        only its own high-confidence verdicts (simple 0.85, complex 0.90) may
        move a turn off the default tier."""
        sys.path.insert(0, str(REPO_ROOT / "corvin_operator" / "bridges" / "shared"))
        import model_selector  # noqa: PLC0415

        assert model_selector._CLASSIFY_MIN_CONFIDENCE > 0.60


# ── Phase 2 — real transport ─────────────────────────────────────────────────


FAKE_CLI = r'''
"""Stand-in for the Claude Code CLI: records its argv, then emits the
stream-json events chat_runtime parses. Replaces ONLY the paid external
binary — every line of console/routing code under test stays real."""
import json, os, sys

with open(os.environ["CORVIN_FAKE_CLI_ARGV"], "w", encoding="utf-8") as fh:
    json.dump(sys.argv[1:], fh)

try:
    sys.stdin.read()
except Exception:
    pass

argv = sys.argv[1:]
model = ""
if "--model" in argv:
    i = argv.index("--model")
    if i + 1 < len(argv):
        model = argv[i + 1]

out = sys.stdout
for evt in (
    {"type": "system", "subtype": "init", "model": model},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
    {
        "type": "result",
        "result": "ok",
        "usage": {
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    },
):
    out.write(json.dumps(evt) + "\n")
out.flush()
'''


@pytest.fixture(scope="module")
def fake_cli(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """A real subprocess that speaks stream-json, reachable as a CLI binary.

    ``chat_runtime`` spawns ``CORVIN_CLAUDE_BIN`` and writes the prompt to its
    stdin, so a shim forwarding ``%*`` is safe: argv carries only flags.
    """
    d = tmp_path_factory.mktemp("fake_claude")
    script = d / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    argv_log = d / "argv.json"

    if sys.platform == "win32":
        shim = d / "claude.cmd"
        shim.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        shim = d / "claude.sh"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        shim.chmod(0o755)

    return {"bin": shim, "argv_log": argv_log}


@pytest.fixture(scope="module")
def client(fake_cli: dict[str, Path]):
    """The real console app over the ASGI/HTTP boundary.

    ``client=("127.0.0.1", …)`` is what makes ``/auth/local-login``'s
    loopback-only gate pass — the default TestClient peer is ``testclient``
    and is correctly rejected.
    """
    os.environ["CORVIN_CLAUDE_BIN"] = str(fake_cli["bin"])
    os.environ["CORVIN_FAKE_CLI_ARGV"] = str(fake_cli["argv_log"])
    os.environ.pop("CORVIN_OS_MODEL_ALLOW_HAIKU", None)

    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from core.console.corvin_console.app import router as console_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(console_router, prefix="/v1/console")
    with TestClient(app, client=("127.0.0.1", 51234)) as c:
        r = c.get("/v1/console/auth/local-login", follow_redirects=False)
        assert r.status_code in (200, 302, 307), r.text
        yield c


@pytest.fixture(scope="module")
def csrf(client) -> str:
    r = client.get("/v1/console/auth/whoami")
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


@pytest.fixture(scope="module")
def turn(client, csrf: str, fake_cli: dict[str, Path]) -> dict:
    """Drive ONE real web-chat turn over the real WebSocket route."""
    from core.paths import tenant_audit_chain  # noqa: PLC0415

    # The audit chain is append-only and shared across runs on this install, so
    # "some os_model.classified record exists" would pass on records this turn
    # never wrote — the vacuous-assertion trap. Mark the tail BEFORE the turn
    # and let every audit assertion below read only what this turn appended.
    chain = Path(tenant_audit_chain("_default"))
    chain_offset = (
        len(chain.read_text(encoding="utf-8", errors="replace").splitlines())
        if chain.exists()
        else 0
    )

    # Same reason: the mix is cumulative over the whole chain, so the panel
    # claim worth asserting is the DELTA this turn caused, not the presence of a
    # model some earlier run may have contributed.
    status_before = client.get("/v1/console/learning/model-cost-optimizer/status")

    r = client.post(
        "/v1/console/chat/sessions",
        json={"title": "tier29-e2e"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["sid"]

    events: list[dict] = []
    with client.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user", "text": "Say hi."})
        for _ in range(200):
            ev = ws.receive_json()
            events.append(ev)
            if ev.get("type") == "done":
                break

    # Resolve the audit chain HERE, while the turn's own CORVIN_HOME is still in
    # effect: the repo conftest rebinds CORVIN_HOME per test function, so
    # calling tenant_audit_chain() from inside a test would point at a fresh,
    # empty tmp home instead of the one this turn wrote to.
    # Same reason the console session store lives under CORVIN_HOME: the login
    # cookie minted above is only loadable while this home is current, so the
    # panel's own endpoint has to be called here too, not from a later test.
    status = client.get("/v1/console/learning/model-cost-optimizer/status")

    new_lines = chain.read_text(encoding="utf-8", errors="replace").splitlines()[
        chain_offset:
    ]

    argv_log = fake_cli["argv_log"]
    return {
        "sid": sid,
        "events": events,
        "status": status,
        "status_before": status_before,
        "chain": chain,
        "new_records": [json.loads(ln) for ln in new_lines if ln.strip()],
        "argv": json.loads(argv_log.read_text(encoding="utf-8"))
        if argv_log.exists()
        else None,
    }


class TestTier29OverWebSocket:
    def test_turn_completed_without_error(self, turn: dict) -> None:
        errors = [e for e in turn["events"] if e.get("type") == "error"]
        assert not errors, errors
        assert any(e.get("type") == "done" for e in turn["events"])

    def test_engine_was_launched_with_the_classified_model(self, turn: dict) -> None:
        """The load-bearing assertion: the routing decision reached the engine.

        A ``--model`` of Sonnet here means Tier 2.9 abstained or was skipped and
        the turn fell through to Tier 3 — exactly the single-model behaviour
        this tier exists to end.
        """
        argv = turn["argv"]
        assert argv is not None, "the engine subprocess never launched"
        assert "--model" in argv, f"no --model in engine argv: {argv}"
        assert argv[argv.index("--model") + 1] == EXPECTED_SIMPLE_MODEL, argv

    def test_classifier_decision_is_in_the_audit_chain(self, turn: dict) -> None:
        """ADR-0129 M2 is default-DENY for unregistered event types: without the
        ``os_model.classified`` allowlist every detail field would land in
        ``_dropped_fields`` and the decision would be unauditable."""
        assert turn["chain"].exists(), turn["chain"]

        applied = [
            r
            for r in turn["new_records"]
            if r.get("event_type") == "os_model.classified"
        ]
        assert applied, "this turn wrote no os_model.classified record"

        latest = applied[-1]["details"]
        assert latest.get("tier") == "2.9_classifier"
        assert latest.get("outcome") == "applied"
        assert latest.get("selected_model") == EXPECTED_SIMPLE_MODEL
        assert "_dropped_fields" not in latest, (
            f"audit allowlist rejected fields: {latest.get('_dropped_fields')}"
        )

    def test_served_model_is_recorded_for_the_cost_panel(self, turn: dict) -> None:
        """``os_turn.completed.model`` is the only input the Model Cost
        Optimizer's ``cost_model_mix`` is built from — if the served model is
        not recorded per turn, the panel cannot show a real mix."""
        mine = [
            r
            for r in turn["new_records"]
            if r.get("event_type") == "os_turn.completed"
            and turn["sid"] in (r.get("details", {}).get("chat_key") or "")
        ]
        assert mine, f"no os_turn.completed for sid {turn['sid']}"
        assert mine[-1]["details"]["model"] == EXPECTED_SIMPLE_MODEL


class TestCostPanelSeesTheMix:
    def test_status_endpoint_reports_the_haiku_turn(self, turn: dict) -> None:
        """The panel's own endpoint, over HTTP, must now report a multi-model
        mix — the condition under which its savings figure stops being a fixed
        price ratio and becomes a measured blend."""
        before, after = turn["status_before"], turn["status"]
        assert before.status_code == 200, before.text
        assert after.status_code == 200, after.text

        mix_before = before.json().get("cost_model_mix") or {}
        mix_after = after.json().get("cost_model_mix") or {}
        assert mix_after.get(EXPECTED_SIMPLE_MODEL, 0) > mix_before.get(
            EXPECTED_SIMPLE_MODEL, 0
        ), f"panel did not count this turn's Haiku: {mix_before} -> {mix_after}"
