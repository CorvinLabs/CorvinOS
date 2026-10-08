"""E2E: current Claude models are offered, and a retired one jumps to its successor.

Operator request, 2026-09-27: *"bei den modelle die zur auswahl stehen sollen
auch die aktuellsten modelle mit versions nummer dargestellt werden. wenn zb.
opus 5 nicht mehr verfügbar ist weil es durch opus5.5 ersetzt wurde soll
automatisch auf opus 5.5 gesprungen werden"* — and, asked, "automatic routing
always uses the newest version; a manual pin stays until its model retires".

Transport: the retirement round-trip drives the REAL ``ClaudeCodeEngine``
against a real subprocess that speaks the CLI's stream-json, so the error text
the engine parses and the ``--model`` argv it builds are the production ones.
The routing/picker assertions go through the real resolver and the real
registry YAML against an isolated ``CORVIN_HOME``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "corvin_operator" / "forge",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import engine_models as EM  # noqa: E402
import model_lineage as ML  # noqa: E402
import model_selector as MS  # noqa: E402
from agents.claude_code import ClaudeCodeEngine  # noqa: E402

import core.skills.os_skills.model_selector  # noqa: E402,F401 — Tier 2.9 warm

RETIRED = "claude-opus-5"
SUCCESSOR = "claude-opus-5-5"


@pytest.fixture
def home(tmp_path):
    """Isolated tenant home with NO os_model pin; restored afterwards."""
    h = tmp_path / ".corvin"
    cfg = h / "tenants" / "_default" / "global"
    cfg.mkdir(parents=True)
    y = cfg / "tenant.corvin.yaml"
    y.write_text("spec:\n  engine_models:\n    claude_code:\n"
                 "      worker_model: claude-opus-5\n")
    y.chmod(0o600)
    previous = os.environ.get("CORVIN_HOME")
    os.environ["CORVIN_HOME"] = str(h)
    EM.load_providers(force_reload=True)
    try:
        yield h
    finally:
        if previous is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = previous
        EM.load_providers(force_reload=True)


def _resolve(prompt: str, profile: dict | None = None) -> str | None:
    return MS.resolve_os_model(profile, task_input=prompt, payload_chars=len(prompt),
                               tenant_id="_default")


# ── lineage parsing ──────────────────────────────────────────────────


@pytest.mark.parametrize("mid, family, version, label", [
    ("claude-opus-5", "opus", (5, 0), "Opus 5"),
    ("claude-opus-5-5", "opus", (5, 5), "Opus 5.5"),
    ("claude-haiku-4-5-20251001", "haiku", (4, 5), "Haiku 4.5"),
    ("anthropic/claude-fable-5-1", "fable", (5, 1), "Fable 5.1"),
])
def test_parse_reads_family_and_version(mid, family, version, label):
    assert ML.parse(mid) == (family, version)
    assert ML.version_label(mid) == label


@pytest.mark.parametrize("mid", ["sonnet", "opusplan", "gpt-4", "claude-3-5-sonnet", ""])
def test_non_lineage_ids_are_left_alone(mid, home):
    assert ML.parse(mid) is None
    assert ML.current(mid) == mid
    assert ML.mark_retired(mid) is False


# ── pickers show the newest versions ─────────────────────────────────


def test_the_picker_offers_the_newest_version_of_each_family(home):
    spec = EM.load_registry(force_reload=True)["claude_code"]
    ids = {m.id for m in spec.worker_models}
    assert {SUCCESSOR, "claude-fable-5-1", "claude-sonnet-5",
            "claude-haiku-4-5-20251001"} <= ids
    labels = {m.id: m.label for m in spec.worker_models}
    assert labels[SUCCESSOR].startswith("Opus 5.5")


# ── automatic routing = newest version ───────────────────────────────


def test_complex_routes_to_the_newest_opus(home):
    assert ML.latest("opus") == SUCCESSOR
    assert _resolve("schreib eine ADR für X") == SUCCESSOR


def test_the_floor_and_top_tier_use_the_newest_opus(home):
    assert MS.top_model() == SUCCESSOR
    assert MS.apply_floor("claude-sonnet-5", "opus") == SUCCESSOR


def test_a_newer_version_ranks_with_its_family(home):
    assert MS._rank(SUCCESSOR) == MS._rank(RETIRED) > MS._rank("claude-sonnet-5")


def test_a_saved_tier_is_a_pin_not_upgraded(home):
    cfg = home / "tenants" / "_default" / "global" / "model_selection_config.json"
    cfg.write_text(json.dumps({"models": {"COMPLEX": {"selected_model": RETIRED}}}))
    assert _resolve("schreib eine ADR für X") == RETIRED


# ── pins stay until retirement ───────────────────────────────────────


def test_an_available_pin_is_kept(home):
    assert _resolve("x", {"model": RETIRED}) == RETIRED


def test_an_unlisted_but_unretired_pin_is_not_upgraded(home):
    """Absent from the curated list is NOT retired — a valid older model."""
    assert _resolve("x", {"model": "claude-opus-4-8"}) == "claude-opus-4-8"


def test_a_retired_pin_jumps_to_its_successor(home):
    assert ML.mark_retired(RETIRED)
    assert _resolve("x", {"model": RETIRED}) == SUCCESSOR
    assert _resolve("x", {"model": f"anthropic/{RETIRED}"}) == SUCCESSOR


def test_a_retired_model_leaves_the_picker_and_hands_on_its_default(home):
    ML.mark_retired("claude-haiku-4-5-20251001")  # nothing newer in the family
    spec = EM.load_registry()["claude_code"]
    assert "claude-haiku-4-5-20251001" not in {m.id for m in spec.worker_models}
    ML.mark_retired(RETIRED)
    assert RETIRED not in {m.id for m in EM.load_registry()["claude_code"].os_models}


def test_a_retired_model_without_successor_is_not_invented(home):
    # The NEWEST Haiku has no successor by definition (it was 4.5 until the
    # registry gained Haiku 5.5 — a hard-coded id here goes stale every launch).
    newest = ML.latest("haiku")
    ML.mark_retired(newest)
    assert ML.current(newest) == newest


def test_a_runtime_mark_expires(home, monkeypatch):
    ML.mark_retired(RETIRED)
    monkeypatch.setattr(ML, "RUNTIME_RETIRE_TTL_S", 0)
    assert not ML.is_retired(RETIRED)


# ── the real engine round-trip ───────────────────────────────────────


FAKE_CLI = r'''
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["LINEAGE_ARGV_LOG"], "a") as f:
    f.write(json.dumps(argv) + "\n")
model = argv[argv.index("--model") + 1] if "--model" in argv else ""
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "model": model}))
if model == "claude-opus-5":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
        "result": "There's an issue with the selected model (claude-opus-5). "
                  "It may not exist or you may not have access to it."}))
else:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
        "result": "ok", "usage": {}}))
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI)
    shim = tmp_path / "claude"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    shim.chmod(0o755)
    log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("LINEAGE_ARGV_LOG", str(log))
    return shim, log


def _models_launched(log: Path) -> list[str]:
    out = []
    for line in log.read_text().splitlines():
        argv = json.loads(line)
        out.append(argv[argv.index("--model") + 1])
    return out


def test_the_cli_error_retires_the_model_and_the_next_spawn_uses_the_successor(home, fake_cli):
    shim, log = fake_cli
    eng = ClaudeCodeEngine(binary=str(shim))
    first = list(eng.spawn("hi", model=RETIRED, streaming=True))
    assert any(e.type == "error" and "selected model" in (e.error or "") for e in first)
    assert ML.is_retired(RETIRED), "the CLI's error was not recorded as evidence"

    second = list(ClaudeCodeEngine(binary=str(shim)).spawn("hi", model=RETIRED, streaming=True))
    assert any(e.type == "turn_completed" for e in second)
    assert _models_launched(log) == [RETIRED, SUCCESSOR]


# ── adversarial review 2026-09-27 (round 4) ──────────────────────────


def test_a_pin_absent_from_the_registry_is_not_upgraded_to_a_minor_version(home, monkeypatch):
    """The dated-snapshot rule matched ANY ``<pin>-…`` registry id, so once
    the curated YAML stopped listing ``claude-opus-5`` (not retired — no CLI
    evidence) a pin to it resolved to ``claude-opus-5-5``: a silent upgrade of
    the operator's explicit choice. Only an 8-digit date extends an id."""
    import dataclasses
    real = EM.load_registry()["claude_code"]
    strip = [m for m in real.os_models if m.id != RETIRED]
    strip_w = [m for m in real.worker_models if m.id != RETIRED]
    spec = dataclasses.replace(real, os_models=strip, worker_models=strip_w)
    monkeypatch.setattr(EM, "load_registry", lambda force_reload=False: {"claude_code": spec})
    assert not ML.is_retired(RETIRED)
    assert MS.resolve_registry_id(RETIRED, "claude_code") is None
    assert _resolve("x", {"model": RETIRED}) == RETIRED
    # the snapshot rule it exists for still works
    assert MS.resolve_registry_id("claude-haiku-4-5", "claude_code") == "claude-haiku-4-5-20251001"


def test_a_provider_qualified_model_never_reaches_the_cli(home, fake_cli):
    """``delegate_task(model="anthropic/…")`` hands the id straight to
    ``spawn()``; ``model_lineage.current`` keeps the prefix on a successor.
    Both reached ``--model`` verbatim (404 model_not_found)."""
    shim, log = fake_cli
    list(ClaudeCodeEngine(binary=str(shim)).spawn("hi", model=f"anthropic/{SUCCESSOR}",
                                                   streaming=True))
    assert ML.mark_retired(RETIRED)
    list(ClaudeCodeEngine(binary=str(shim)).spawn("hi", model=f"anthropic/{RETIRED}",
                                                   streaming=True))
    assert _models_launched(log) == [SUCCESSOR, SUCCESSOR]


def test_a_bedrock_arn_is_passed_through_untouched():
    from agents.claude_code import _current_model
    arn = "arn:aws:bedrock:eu-central-1:123456789012:inference-profile/eu.anthropic.claude-opus-5"
    assert _current_model(arn) == arn
    assert _current_model("ollama/qwen3:8b") == "ollama/qwen3:8b"
