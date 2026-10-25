"""Call-site pins for three live learning systems (ADR-2281 §4).

Each pin drives the REAL entry point and observes the learning effect, so it
fails when the production call site is removed — not when a function returns a
wrong value. Nothing here imports a learning target and calls it.

* L06 skill grading + injection gate — inbox envelopes through
  ``adapter.process_one`` (``ADAPTER_FAKE_CLAUDE`` replaces only the Claude CLI
  subprocess). Turn 1 must inject the graded skill and NOT the ungraded one
  (``skill_inject.collect_active_skills`` gate, ``adapter.py`` injection call);
  the follow-up "danke, perfekt" must land an outcome grade in the registry
  (``adapter.py`` → ``skill_inject.grade_from_user_followup``).
* L10 user-model distillation — the same adapter turn path with
  ``user_model_enabled`` + ``user_model_distill_every_n_turns: 1`` in the chat
  profile: the turn must schedule ``_user_model_distill_async`` (the judge is a
  fake ``claude`` binary via ``CORVIN_CLAUDE_BIN``), the distilled model must be
  saved, and the next turn's system prompt must carry its ``<user_context>``.
* L14 Layer Forge learning — a single-layer ZIP POSTed to the console's
  ``/v1/console/forge-bundles/import`` (→ ``forge_bundle/item_import.py`` →
  ``LayerForgeOrchestrator``) with the real ACP registry booted for the tenant:
  the orchestrator's ``emit_gate_confidence`` / ``emit_enforcement_confidence``
  / ``emit_review_confidence`` must reach the tenant's learning event store.
  The adversarial review's Anthropic call is the one boundary replaced.

The adapter turns run in-process (no SIGTERM, so the adapter's unit sweep is
never reached); run-all-tests.sh still starts this file in its own
``systemd-run --user --scope`` when available.

Mutation record (2026-10-25, origin/main bff4bbe50): neutering ONE production call site
in a worktree and running this file turns exactly the matching pin red; restored afterwards.

* ``adapter.py`` ``skill_block = _skill_inject.collect_active_skills(`` → L06 pin red
* ``adapter.py`` ``outcome_graded = _skill_inject.grade_from_user_followup(`` → L06 pin red
* ``adapter.py`` ``target=_user_model_distill_async,`` → ``schedules_the_distill`` and
  ``yields_a_distilled_model`` red
* ``adapter.py`` ``um = _user_model.load(`` → ``injects_the_stored_user_model`` red
* ``orchestrator.py`` ``emit_gate_confidence(``, ``emit_enforcement_confidence(`` and
  ``emit_review_confidence(``, one at a time → the L14 pin red each time

The lines move when the files do; the register (``learning_systems.yaml``) holds the
current ``path:line`` of each call site.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import stat
import sys
import time
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
for p in (SHARED, REPO / "corvin_operator" / "forge", REPO / "corvin_operator" / "skill-forge",
          REPO / "corvin_operator", REPO / "core" / "console", REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TENANT = "_default"
SENDER = "u-pins"
GRADED = "assistant.pin_graded_skill"
UNGRADED = "assistant.pin_ungraded_skill"
STYLE_MARKER = "PINMARKER prefers terse answers"


# ── adapter fixture (shared by L06 and L10) ────────────────────────────────

@pytest.fixture
def bridge(tmp_path, monkeypatch):
    for name in ("inbox", "outbox", "processed", "bridges/discord"):
        (tmp_path / name).mkdir(parents=True)
    home = tmp_path / "home"
    judge = tmp_path / "fake-claude"
    judge.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        f"print(json.dumps({{'communication_style': {STYLE_MARKER!r}}}))\n",
        encoding="utf-8")
    judge.chmod(judge.stat().st_mode | stat.S_IXUSR)
    env = {
        "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": TENANT,
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(tmp_path / "anchor.key"),
        "ADAPTER_INBOX": str(tmp_path / "inbox"), "ADAPTER_OUTBOX": str(tmp_path / "outbox"),
        "ADAPTER_PROCESSED": str(tmp_path / "processed"),
        "ADAPTER_BRIDGES_DIR": str(tmp_path / "bridges"),
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0",
        "ADAPTER_FAKE_ARGS_DUMP": str(tmp_path / "args.jsonl"),
        "ADAPTER_ROUTING_MODE": "off", "ADAPTER_DISABLE_VOICE": "1",
        "VOICE_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "CORVIN_CLAUDE_BIN": str(judge),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FORGE_ROOT", raising=False)

    from license import validator as host_validator
    orig = host_validator._ACTIVE_LICENSE
    host_validator._set_active_license({"tier": "member"})
    for mod in ("adapter", "user_model", "conversation_recall"):
        sys.modules.pop(mod, None)
    adapter = importlib.import_module("adapter")
    yield adapter, tmp_path, home
    host_validator._set_active_license(orig)
    try:
        sys.modules["conversation_recall"]._close_all_connections()
    except Exception:  # noqa: BLE001
        pass


def _channel_settings(base: Path, profile: dict) -> None:
    (base / "bridges" / "discord" / "settings.json").write_text(json.dumps(
        {"whitelist": [SENDER], "chat_profiles": {"default": profile}}), encoding="utf-8")


def _send(adapter, base: Path, chat: str, text: str) -> None:
    msg_id = f"pin-{time.time_ns()}"
    envelope = {"id": msg_id, "channel": "discord", "from": SENDER, "chat_id": chat,
                "text": text, "ts": time.time()}
    f = base / "inbox" / f"{msg_id}.json"
    f.write_text(json.dumps(envelope))
    adapter.process_one(f, settings={"whitelist": [SENDER], "voice_summary_mode": "never"})


def _last_system_prompt(base: Path) -> str:
    lines = (base / "args.jsonl").read_text().splitlines()
    args = json.loads(lines[-1])["args"]
    return Path(args[args.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")


# ── L06 ─────────────────────────────────────────────────────────────────────

def test_l06_turn_path_injects_through_the_gate_and_grades_the_follow_up(bridge):
    adapter, base, home = bridge
    _channel_settings(base, {})
    from skill_creator.registry_bridge import promote_to_registry, registry_for
    root = home / "tenants" / TENANT / "skill-forge"
    promote_to_registry(root, name=GRADED, description="Checks JSON syntax strictly.",
                        body_md="GRADED-MARKER validate every JSON file and report errors")
    registry_for(root).create(name=UNGRADED, type="learned-experience", scope="user",
                              description="Checks JSON syntax strictly, too.",
                              body_md="UNGRADED-MARKER validate every YAML file", overwrite=True)

    # Neither skill is NAMED: an explicit request outranks the gate by design.
    _send(adapter, base, "chat-l06", "please check the JSON syntax of data.json")
    system = _last_system_prompt(base)
    assert f'<auto_skill name="{GRADED}"' in system, (
        "the graded skill never reached the engine: the adapter's injection call is gone")
    assert "UNGRADED-MARKER" not in system, (
        "an ungraded skill was injected: the injection gate (n_grades<1 or mean<=0) is gone")

    assert not registry_for(root).get(UNGRADED).grades, "an ungraded, never-injected skill got a grade"

    # Grading: a turn that USES the skill is auto-graded (usage detection,
    # capped) and snapshotted; the operator's follow-up then lands the
    # organic outcome grade (0.9 for an approval) through adapter.py.
    _send(adapter, base, "chat-l06b", f"please use {GRADED} on data.json")
    before = [g["score"] for g in registry_for(root).get(GRADED).grades]
    _send(adapter, base, "chat-l06b", "danke, perfekt")
    after = [g["score"] for g in registry_for(root).get(GRADED).grades]
    assert len(after) > len(before) and 0.9 in after and 0.9 not in before, (
        f"the follow-up approval was not graded through the turn path: {before} -> {after}")


# ── L10 ─────────────────────────────────────────────────────────────────────

def _distill_records(home: Path, chat: str) -> list[dict]:
    chain = home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
    if not chain.is_file():
        return []
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return [r for r in recs if str(r.get("event_type", "")).startswith("memory.user_model_")
            and (r.get("details") or {}).get("chat_key") == chat]


def _wait_for_distill(home: Path, chat: str, timeout: float = 15.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        recs = _distill_records(home, chat)
        if recs:
            return recs
        time.sleep(0.1)
    return []


def test_l10_turn_path_schedules_the_distill(bridge):
    """process_one -> scheduler -> _user_model_distill_async -> user_model.distill:
    every distill run ends in a chained memory.user_model_* record."""
    adapter, base, home = bridge
    _channel_settings(base, {"user_model_enabled": True, "user_model_distill_every_n_turns": 1})
    _send(adapter, base, "chat-l10", "I want short answers about the audit chain")
    assert _wait_for_distill(home, "chat-l10"), (
        "the turn never ran the user-model distill: the adapter's distill scheduler "
        "(process_one -> _user_model_distill_async) is gone")


def test_l10_turn_path_injects_the_stored_user_model(bridge):
    adapter, base, home = bridge
    _channel_settings(base, {"user_model_enabled": True,
                             "user_model_distill_every_n_turns": 1000})
    um = sys.modules["user_model"]
    model = um.UserModel.empty("discord", "chat-l10b")
    model.communication_style = STYLE_MARKER
    um.save(model)   # fixture data on disk, as a finished distill leaves it
    _send(adapter, base, "chat-l10b", "hello again")
    system = _last_system_prompt(base)
    assert "<user_context>" in system and STYLE_MARKER in system, (
        "the stored user model was not injected: the adapter's user-model read is gone")


def test_l10_a_real_turn_yields_a_distilled_model(bridge):
    # Was a strict xfail until 2026-10-25: recall('') went into FTS5 MATCH and matched no row,
    # so no user model was ever distilled. conversation_recall.recall now treats an empty query
    # as "this chat's newest turns" (chat-scoped only).
    adapter, base, home = bridge
    _channel_settings(base, {"user_model_enabled": True, "user_model_distill_every_n_turns": 1})
    _send(adapter, base, "chat-l10c", "I want short answers about the audit chain")
    recs = _wait_for_distill(home, "chat-l10c")
    assert recs, "no distill ran at all"
    reasons = [(r.get("details") or {}).get("reason") for r in recs]
    assert sys.modules["user_model"].load("discord", "chat-l10c") is not None, reasons


# ── L14 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def console(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    th = home / "tenants" / TENANT
    for sub in ("global/layer_forge/registry", "global/layer_forge/locks", "global/forge"):
        (th / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "audit.jsonl"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)

    # The review phase calls the Anthropic API — the one external boundary
    # replaced; every gate and enforcement rule runs for real.
    from core.orchestration.layer_forge import orchestrator
    from core.orchestration.layer_forge.review import ReviewVerdict
    monkeypatch.setattr(orchestrator, "review_layer_definition",
                        lambda manifest, enforcement, **_k: ReviewVerdict("PASS", flags=[]))

    # The host boot step that gives Layer Forge a learning emitter (gateway /
    # console boot_skills); the emitter writes the tenant's EventStore.
    from core.skills.boot import boot_skills
    boot_skills(TENANT)
    from core.skills import skill_registry_phase1 as reg
    emitter = reg._global_registry.learning_backend.emitter

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console import auth
    from corvin_console.app import router
    rec = auth.create_session(tenant_id=TENANT, token_fingerprint="pin-fp")
    csrf = auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    yield client, csrf, home, emitter
    emitter.stop()


def _layer_zip(entry_id: str, version: str) -> bytes:
    manifest = {
        "id": entry_id, "version": version,
        "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
        "quality_gates": [{"gate_id": "schema_check", "test_path": "tests/layer_forge/test_schema.py"}],
        "enforcement_rules": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{entry_id}/manifest.json", json.dumps(manifest))
    return buf.getvalue()


def test_l14_layer_import_route_emits_layer_forge_confidence(console):
    client, csrf, home, emitter = console
    r = client.post("/v1/console/forge-bundles/import", headers={"X-CSRF-Token": csrf},
                    files={"file": ("layer.zip", _layer_zip("acme.pin-layer", "1.0.0"),
                                    "application/zip")})
    assert r.status_code == 200, r.text
    emitter.stop()   # drains the queue into the store

    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType
    events = EventStore(home / "tenants" / TENANT, tenant_id=TENANT).query_events(
        TENANT, event_type=EventType.CONFIDENCE, skill_id="layer_forge")
    kinds = {e.signal.get("decision_type") for e in events
             if e.signal.get("entry_id") == "acme.pin-layer"}
    assert "quality_gate" in kinds, f"emit_gate_confidence not reached via item_import: {kinds}"
    assert "enforcement_rule" in kinds, f"emit_enforcement_confidence not reached: {kinds}"
    assert "adversarial_review" in kinds, f"emit_review_confidence not reached: {kinds}"
