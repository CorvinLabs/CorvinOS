"""LIVE: a real bridge turn through ``adapter.process_one`` and the real ``claude`` CLI.

Opt-in: ``CLAUDE_LIVE_E2E=1`` and the ``claude`` CLI on PATH (costs credits;
each test is one short turn).

What is real and what is not:

* the entry point is the adapter's own ``process_one`` — the function the main
  loop calls for every inbox envelope — fed a genuine envelope FILE;
* the engine is the real ``ClaudeCodeEngine`` spawning the real ``claude``
  binary. ``CORVIN_CLAUDE_BIN`` points at a thin pass-through shim that, at the
  moment the CLI is spawned, snapshots the task records on disk and then
  ``exec``s the real binary with the same argv/stdin — the LLM answer is real,
  the shim only observes;
* the audit chain is the tenant chain resolved by ``tenant_audit_chain()`` under
  a throw-away ``CORVIN_HOME`` — the file the console's readers use. (A
  sandboxed adapter — ``ADAPTER_INBOX`` set — would otherwise park its chain at
  ``<inbox>/../audit.jsonl``; ``VOICE_AUDIT_PATH`` is pinned to the resolver's
  answer so the turn lands where a production turn lands.)
* bridge settings live at the canonical ``<corvin_home>/bridges/discord/
  settings.json`` (no ``ADAPTER_BRIDGES_DIR`` override), so the c2bdc105a
  resolver is on the path too.

Tests:

1. ``test_turn_task_opened_at_pickup_and_priced_span`` (ADR-2081 P2 + ADR-0759):
   the turn's task record already exists and is RUNNING with the
   ``preparing`` stage and the adapter's owner pid when the CLI is spawned;
   afterwards there is exactly one record, ``completed``, whose summary holds
   the real reply, with a ``task.engine_started`` carrying the engine pid; the
   chain carries ``engine.span.start/end`` with a non-empty ``model_id`` equal
   to the pinned registry id; and the console's ``model_usage`` reader counts
   that turn under that model with real token counts (priced, not $0).
2. ``test_unpinned_turn_is_routed_by_the_tier29_classifier`` (ADR-0952): with
   no pin anywhere, the OS model resolver's Tier 2.9 complexity classifier
   decides the turn — an ``os_model.classified`` record lands in the chain —
   and the model the span closes on is the one the resolver served.

Run: CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -o addopts="" \
     -p no:cacheprovider tests/e2e/live/test_bridge_turn_live.py -s
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
        reason="live bridge E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
    ),
]

HAIKU = "claude-haiku-4-5-20251001"
OPERATOR = "u-live-bridge-op"
CHAT = "chat-live-bridge"


def _write_shim(tmp: Path, real_claude: str) -> Path:
    """A pass-through ``claude``: snapshot the task records, then exec the real CLI."""
    shim = tmp / "shim-bin" / "claude"
    shim.parent.mkdir(parents=True)
    probe = tmp / "probe.jsonl"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import glob, json, os, sys\n"
        "home = os.environ.get('CORVIN_HOME', '')\n"
        "seen = []\n"
        "for p in glob.glob(home + '/tenants/*/sessions/**/tasks/*.json', recursive=True):\n"
        "    try:\n"
        "        d = json.load(open(p))\n"
        "        ev = open(p[:-5] + '.events.jsonl').read()\n"
        "    except Exception:\n"
        "        continue\n"
        "    seen.append({'task_id': d.get('task_id') or d.get('id'), 'status': d.get('status'),\n"
        "                 'preparing': '\"preparing\"' in ev, 'events': ev})\n"
        "flags = [a for a in sys.argv[1:] if a.startswith('--')]\n"
        "model = sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else ''\n"
        f"with open({str(probe)!r}, 'a') as fh:\n"
        "    fh.write(json.dumps({'flags': flags, 'model': model, 'tasks': seen}) + '\\n')\n"
        f"os.execv({real_claude!r}, [{real_claude!r}] + sys.argv[1:])\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


@pytest.fixture
def bridge(tmp_path: Path, monkeypatch):
    real_claude = shutil.which("claude")
    assert real_claude
    home = tmp_path / "corvin_home"
    for d in ("inbox", "outbox", "processed"):
        (tmp_path / d).mkdir()
    settings_dir = home / "bridges" / "discord"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(json.dumps({"whitelist": [OPERATOR]}))
    shared_settings = tmp_path / "shared-settings.json"
    shared_settings.write_text(json.dumps({"always_voice": False, "voice_summary_mode": "never"}))
    shim = _write_shim(tmp_path, real_claude)

    for k in ("VOICE_AUDIT_PATH", "FORGE_ROOT", "ADAPTER_BRIDGES_DIR", "ADAPTER_FAKE_CLAUDE",
              "CORVIN_OS_MODEL", "CORVIN_OS_MODEL_OVERRIDE", "ADAPTER_MODEL",
              "CORVIN_OS_MODEL_AUTOSELECT", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {
        "CORVIN_HOME": str(home),
        "CORVIN_TENANT_ID": "_default",
        "ADAPTER_INBOX": str(tmp_path / "inbox"),
        "ADAPTER_OUTBOX": str(tmp_path / "outbox"),
        "ADAPTER_PROCESSED": str(tmp_path / "processed"),
        "ADAPTER_SETTINGS": str(shared_settings),
        "ADAPTER_ROUTING_MODE": "off",
        "ADAPTER_DISABLE_VOICE": "1",
        "BRIDGE_PROGRESS_UPDATES": "0",
        "CORVIN_VOICE_PREWARM": "0",
        "ADAPTER_HEARTBEAT_INTERVAL": "0",
        "CORVIN_CLAUDE_BIN": str(shim),
    }.items():
        monkeypatch.setenv(k, v)
    for p in (str(SHARED), str(REPO)):
        if p not in sys.path:
            sys.path.insert(0, p)

    from core.paths import tenant_audit_chain

    chain = Path(tenant_audit_chain("_default"))
    assert home in chain.parents, f"tenant chain {chain} escaped the sandbox {home}"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))

    for m in ("adapter", "model_selector"):
        sys.modules.pop(m, None)
    adapter = importlib.import_module("adapter")

    def turn(text: str, msg_id: str) -> dict:
        env = {"id": msg_id, "channel": "discord", "from": OPERATOR, "chat_id": CHAT,
               "text": text, "ts": time.time()}
        f = tmp_path / "inbox" / f"{msg_id}.json"
        f.write_text(json.dumps(env), encoding="utf-8")
        t0 = time.monotonic()
        adapter.process_one(f, settings=json.loads(shared_settings.read_text()))
        elapsed = time.monotonic() - t0
        outs = [json.loads(p.read_text()) for p in sorted((tmp_path / "outbox").glob("*.json"))]
        replies = [o for o in outs if str(o.get("chat_id")) == CHAT and not o.get("_progress")
                   and not o.get("_heartbeat") and (o.get("text") or "").strip()]
        events = ([json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
                  if chain.exists() else [])
        probes = ([json.loads(l) for l in (tmp_path / "probe.jsonl").read_text().splitlines()]
                  if (tmp_path / "probe.jsonl").exists() else [])
        tasks = []
        for p in home.rglob("tasks/*.json"):
            rec = json.loads(p.read_text())
            evf = Path(str(p)[:-5] + ".events.jsonl")
            rec["_events"] = ([json.loads(l) for l in evf.read_text().splitlines() if l.strip()]
                              if evf.exists() else [])
            tasks.append(rec)
        print(f"\n[live] turn {msg_id}: {elapsed:.1f}s, replies={[r.get('text') for r in replies]!r}")
        print(f"[live] chain event types: {[e.get('event_type') for e in events]}")
        return {"replies": replies, "events": events, "probes": probes, "tasks": tasks,
                "home": home, "chain": chain, "adapter": adapter}

    try:
        yield turn
    finally:
        for m in ("adapter", "model_selector"):
            sys.modules.pop(m, None)


def _engine_probe(probes: list[dict]) -> dict:
    """The spawn of the OS turn itself (stream-json engine), not a helper call
    such as the L44 house-rules classifier which also resolves CORVIN_CLAUDE_BIN."""
    turn_spawns = [p for p in probes if "--input-format" in p["flags"]
                   or "--append-system-prompt-file" in p["flags"]]
    assert turn_spawns, f"the OS turn never spawned the claude CLI; spawns={probes!r}"
    return turn_spawns[-1]


def _span(events: list[dict], kind: str) -> list[dict]:
    return [e.get("details") or {} for e in events if e.get("event_type") == f"engine.span.{kind}"]


def test_turn_task_opened_at_pickup_and_priced_span(bridge, monkeypatch):
    monkeypatch.setenv("CORVIN_OS_MODEL_OVERRIDE", HAIKU)  # Tier 1 pin — cheap turn
    r = bridge("Reply with exactly the single word PONG and nothing else.", "live-bridge-1")

    # the real reply reached the outbox
    assert r["replies"], "no reply envelope in the outbox"
    assert any("PONG" in (x.get("text") or "").upper() for x in r["replies"]), r["replies"]

    # ── turn task opened at pickup (observed by the shim at spawn time) ─────
    spawn = _engine_probe(r["probes"])
    assert spawn["model"] == HAIKU, f"engine spawned with --model {spawn['model']!r}"
    at_spawn = spawn["tasks"]
    assert len(at_spawn) == 1, f"expected exactly one task record at engine spawn, got {at_spawn!r}"
    assert at_spawn[0]["status"] == "running", at_spawn
    assert at_spawn[0]["preparing"], "task was not opened at pickup (no 'preparing' stage)"
    pickup = [json.loads(l) for l in at_spawn[0]["events"].splitlines() if l.strip()]
    started = [e for e in pickup if e.get("event") == "task.started"]
    assert started and started[0].get("stage") == "preparing", pickup
    assert started[0].get("owner_pid") == os.getpid(), (
        f"pickup record carries no owner pid of the adapter process: {started[0]}")

    # ── closed exactly once, completed, reply preview as summary ────────────
    tasks = r["tasks"]
    assert len(tasks) == 1, f"one turn must be one task record, got {len(tasks)}"
    t = tasks[0]
    assert t["status"] == "completed", t
    assert "PONG" in (t.get("result_summary") or "").upper(), t.get("result_summary")
    kinds = [e.get("event") for e in t["_events"]]
    assert kinds.count("task.started") == 1, kinds
    assert "task.engine_started" in kinds, kinds
    eng = next(e for e in t["_events"] if e.get("event") == "task.engine_started")
    assert isinstance(eng.get("pid"), int) and eng["pid"] != os.getpid(), eng
    assert kinds[-1] == "task.completed", kinds

    # ── engine span with a real model_id, in the ONE tenant chain ───────────
    starts, ends = _span(r["events"], "start"), _span(r["events"], "end")
    assert starts and ends, "no engine.span.* pair in the tenant chain"
    os_end = [e for e in ends if e.get("role") == "os"]
    assert os_end, ends
    assert os_end[-1].get("model_id") == HAIKU, os_end[-1]
    assert os_end[-1].get("status") == "ok", os_end[-1]
    done = [e.get("details") or {} for e in r["events"] if e.get("event_type") == "os_turn.completed"]
    assert done and done[-1].get("model") == HAIKU, done
    assert int(done[-1].get("output_tokens") or 0) > 0, f"no real token usage recorded: {done[-1]}"

    # ── the console reader prices it under that model ───────────────────────
    from corvin_console.model_usage import model_usage  # noqa: PLC0415

    usage = model_usage("_default")
    rows = {m["model_id"]: m for m in usage["models"]}
    print(f"[live] model_usage rows: {json.dumps(usage['models'])[:600]}")
    assert HAIKU in rows, f"turn not counted under {HAIKU}: {list(rows)}"
    assert rows[HAIKU]["ok"] >= 1, rows[HAIKU]
    assert rows[HAIKU]["output_tokens"] > 0, f"turn counted but unpriced (0 tokens): {rows[HAIKU]}"

    # metadata-only chain
    raw = r["chain"].read_text()
    assert "exactly the single word PONG" not in raw, "prompt text leaked into the audit chain"
    assert OPERATOR not in raw, "raw platform uid leaked into the audit chain"


def test_unpinned_turn_is_routed_by_the_tier29_classifier(bridge):
    # ADR-0952: no pin at any tier → Tier 2.9 (complexity classifier) decides.
    # The classifier import is warmed in a daemon thread when model_selector is
    # first imported, and the tier ABSTAINS until that finished (documented:
    # "a first turn falls through to Tier 3"). Import the same module the
    # adapter imports and wait for the warm-up, so an abstention below is not
    # merely a cold start.
    ms = importlib.import_module("model_selector")
    for _ in range(100):
        if "core.skills.os_skills.model_selector" in sys.modules:
            break
        time.sleep(0.1)
    assert "core.skills.os_skills.model_selector" in sys.modules, "classifier warm-up never ran"
    assert ms.autoselect_enabled()
    r = bridge("What is 2+2? Answer with just the number.", "live-bridge-2")

    assert r["replies"], "no reply envelope in the outbox"
    assert any("4" in (x.get("text") or "") for x in r["replies"]), r["replies"]
    spawn = _engine_probe(r["probes"])
    classified = [e.get("details") or {} for e in r["events"]
                  if e.get("event_type") == "os_model.classified"]
    print(f"[live] os_model.classified: {classified}; served --model {spawn['model']!r}")
    assert classified, (
        "Tier 2.9 never ran on a real unpinned bridge turn — no os_model.classified record. "
        "classify_os_model() needs core.skills.os_skills.model_selector.ModelSelector "
        f"(present: {hasattr(sys.modules.get('core.skills.os_skills.model_selector'), 'ModelSelector')})"
    )
    decision = classified[-1]
    if decision.get("outcome") == "applied":
        assert spawn["model"] == decision["selected_model"], (spawn["model"], decision)
    ends = [e for e in _span(r["events"], "end") if e.get("role") == "os"]
    assert ends and ends[-1].get("model_id") == spawn["model"], (ends, spawn["model"])
