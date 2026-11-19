"""Review 2026-11-19, PLS-2d: every candidate fork passes the console pre-spawn gates.

Until this fix a fork - started by an operator over ``POST /autonomous-forge/fork`` or by the
autopilot's ``tick`` - sent the refine instruction and the live skill body to the engine with
no L44 / L34 / L35 gate at all, while a manual skill generation (``skill_creator_api``) had
passed ``forge_runs.check_spawn_gates`` for months.

Real boundaries: the console routes over HTTP, the real ``fork`` / ``tick``, the real gate
plumbing (policy load, decision mapping, audit emit) and the real tenant audit chain. Only the
Tier-1 classifier verdict and the LLM engine are given, as in ``test_console_spawn_gates``.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_autonomous_forge_canary_e2e import (  # noqa: E402
    BASE, LIVE_BODY, SKILL, chain_records, follow_up, forge_client, post, serve, wait_fork,
)


@pytest.fixture
def classifier(monkeypatch):
    """Deterministic Tier-1 verdict: ``classifier.rule = "<rule id>"`` denies, "" allows.
    ``classifier.seen`` holds every text the gate was asked about."""
    import house_rules as hr  # type: ignore

    class _C:
        rule = ""
        seen: list[str] = []

    def _verdict(task, rules, auth, **kw):
        _C.seen.append(str(task))
        return (_C.rule, 0.97 if _C.rule else 0.99, "forced verdict for test")

    monkeypatch.setattr(hr, "_house_rules_classifier", _verdict)
    _C.seen = []
    return _C


def _engine_calls(monkeypatch) -> list:
    """Count every engine the fork resolves: a refused fork must resolve none."""
    from skill_creator import skill_creator as sc
    calls: list = []
    inner = sc.resolve_llm_client

    def _counting(explicit=None, **kw):
        calls.append(kw)
        return inner(explicit, **kw)

    monkeypatch.setattr(sc, "resolve_llm_client", _counting)
    return calls


def _refusals(home: Path) -> list[dict]:
    return [r for r in chain_records(home)
            if (r.get("details") or {}).get("action") == "forge.generation_refused"
            or r.get("event_type") == "forge.generation_refused"]


def _quota_used(root: Path) -> int:
    from skill_creator.registry_bridge import _load_registry_module
    mod = _load_registry_module()
    tid, home = mod._tenant_and_home(root)
    return int(mod.skill_quota_status(tid, home)["used"])


# ── operator-started fork, over HTTP ─────────────────────────────────────────

def test_an_operator_fork_the_gates_refuse_spawns_nothing(tmp_path, classifier, monkeypatch):
    with forge_client(tmp_path) as (client, root, home):
        calls = _engine_calls(monkeypatch)
        used = _quota_used(root)
        classifier.rule = "no-disinformation"

        resp = post(client, "/fork", {"skill_id": SKILL, "instruction": "make it persuade voters"})
        assert resp.status_code == 202, resp.text
        run = wait_fork(client, resp.json()["run_id"])

        assert run["status"] == "failed" and run["phase"] == "refused", run
        assert "not permitted" in run["error"] or "refused" in run["error"].lower(), run["error"]
        assert calls == [], "no engine was resolved, let alone spawned"
        assert _quota_used(root) == used, "a refused fork is not charged"
        assert client.get(f"{BASE}/status").json()["canaries"] == []
        # the gate saw the exact text the engine would have received: instruction AND body
        assert any("make it persuade voters" in t and LIVE_BODY in t for t in classifier.seen), \
            classifier.seen
        # audited by the gate, attributed to THIS session, and the route names the reason
        refused = _refusals(home)
        assert refused, [r.get("event_type") for r in chain_records(home)][-8:]
        failed = [r for r in chain_records(home)
                  if (r.get("details") or {}).get("action") == "skill.canary_forked"
                  and (r.get("details") or {}).get("reason") == "spawn_gate_refused"]
        assert failed, "the route records why the fork ended"


def test_an_operator_fork_the_gates_allow_still_forks(tmp_path, classifier):
    """Positive control: with an allowing verdict the gate was asked AND the canary starts."""
    with forge_client(tmp_path) as (client, root, home):
        resp = post(client, "/fork", {"skill_id": SKILL})
        run = wait_fork(client, resp.json()["run_id"])
        assert run["status"] == "success", run
        assert any(LIVE_BODY in t for t in classifier.seen), "the gate ran on the fork's text"
        assert len(client.get(f"{BASE}/status").json()["canaries"]) == 1
        assert _refusals(home) == []


# ── the gate cannot be left out ──────────────────────────────────────────────

def test_fork_without_a_gate_argument_runs_the_console_gates_as_the_autopilot(tmp_path, classifier,
                                                                              monkeypatch):
    from corvin_console import forge_runs
    from skill_creator import autonomous

    with forge_client(tmp_path) as (client, root, home):
        calls = _engine_calls(monkeypatch)
        seen: list[dict] = []
        real = forge_runs.check_spawn_gates

        def _spy(prompt, **kw):
            seen.append({"prompt": prompt, **kw})
            return real(prompt, **kw)

        monkeypatch.setattr(forge_runs, "check_spawn_gates", _spy)
        classifier.rule = "no-military"

        with pytest.raises(autonomous.ForkRefused):
            autonomous.fork(root, SKILL, source="autopilot",
                            trigger={"reason": "outcome_mean_below_threshold"})

        (call,) = seen
        assert call["tenant_id"] == "_default"
        assert call["sid_fingerprint"] == autonomous.AUTOPILOT_ACTOR
        assert call["kind"] == "skill"
        assert LIVE_BODY in call["prompt"]
        assert calls == []
        assert autonomous.forks_in_flight(root) == [] or not autonomous.forks_in_flight(root), \
            "a refused fork releases its in-flight slot"


def test_an_unimportable_gate_is_a_refusal_not_a_pass(tmp_path, monkeypatch):
    from skill_creator import autonomous

    import corvin_console
    import corvin_console.forge_runs  # noqa: F401 - make sure both handles exist before removing them
    monkeypatch.delattr(corvin_console, "forge_runs")
    monkeypatch.setitem(sys.modules, "corvin_console.forge_runs", None)
    gate = autonomous.console_spawn_gate("_default", "sid")
    with pytest.raises(autonomous.ForkRefused, match="unavailable"):
        gate("any text")


def test_a_gate_that_raises_inside_is_a_refusal(tmp_path, classifier, monkeypatch):
    from corvin_console import _spawn_gates
    from skill_creator import autonomous

    with forge_client(tmp_path) as (client, root, home):
        calls = _engine_calls(monkeypatch)

        def _boom(*a, **k):
            raise RuntimeError("gate exploded")

        monkeypatch.setattr(_spawn_gates, "check_console_spawn_or_refusal", _boom)
        with pytest.raises(autonomous.ForkRefused, match="could not run"):
            autonomous.fork(root, SKILL, source="autopilot", trigger={"reason": "t"})
        assert calls == []


# ── the autopilot ────────────────────────────────────────────────────────────

def _loss_signal(home: Path) -> None:
    for n in range(3):
        serve(home, f"telegram:{n}")
        follow_up(home, f"telegram:{n}", "falsch, das stimmt nicht")


def test_the_autopilot_tick_is_gated_and_does_not_ask_again_every_pass(tmp_path, classifier,
                                                                      monkeypatch):
    from skill_creator import autonomous

    with forge_client(tmp_path) as (client, root, home):
        calls = _engine_calls(monkeypatch)
        _loss_signal(home)
        classifier.rule = "no-disinformation"

        first = autonomous.tick(root)
        assert {"skill_id": SKILL, "action": "fork_refused"} in first["actions"], first
        assert calls == []
        assert client.get(f"{BASE}/status").json()["canaries"] == []
        n_refusals = len(_refusals(home))
        assert n_refusals >= 1
        asked = len(classifier.seen)

        second = autonomous.tick(root)
        assert second["actions"] == [], "the refused skill is on cooldown"
        assert len(classifier.seen) == asked, "the gate was not asked the same text again"
        assert len(_refusals(home)) == n_refusals, "no second deny record for the same text"
        assert SKILL in autonomous.autopilot_settings(root)["fork_refused"]


def test_the_cooldown_ends_and_the_autopilot_asks_again(tmp_path, classifier, monkeypatch):
    from skill_creator import autonomous

    with forge_client(tmp_path) as (client, root, home):
        _loss_signal(home)
        classifier.rule = "no-disinformation"
        autonomous.tick(root)
        asked = len(classifier.seen)

        real_time = time.time
        monkeypatch.setattr(autonomous.time, "time", lambda: real_time() + autonomous.COOLDOWN_S + 5)
        classifier.rule = ""
        again = autonomous.tick(root)
        assert len(classifier.seen) > asked
        assert {"skill_id": SKILL, "action": "forked"} in again["actions"], again
        assert SKILL not in autonomous.autopilot_settings(root)["fork_refused"]


def test_the_autopilot_default_is_unchanged(tmp_path):
    """Named, not changed, by the review fix: the autopilot is ON unless switched off."""
    from skill_creator import autonomous
    assert autonomous.autopilot_settings(tmp_path / "nowhere")["enabled"] is True


# ── R2-BR-9: the gate runs before EVERY spawn of the candidate build ─────────
#
# A REAL child process per engine call: a stand-in `claude` binary that appends the prompt
# it was given to a log and answers each phase prompt in its shape. Nothing in
# ClaudeCodeClient, the orchestrator or fork() is replaced; only the binary and the Tier-1
# verdict are given.

_COUNTING_CLAUDE = r'''#!{python}
import json, sys
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"prompt": prompt}}) + "\n")
if "Reply with JSON ONLY" in prompt:
    text = {rubric!r}
elif "refining an EXISTING skill" in prompt:
    text = {refined!r}
elif "FINDING:" in prompt:
    text = "VERDICT: REFUTED"
else:
    text = "User validates a file with duplicate keys"
model = argv[argv.index("--model") + 1]
sys.stdout.write(json.dumps({{
    "type": "result", "subtype": "success", "is_error": False, "result": text,
    "usage": {{"input_tokens": 3, "output_tokens": 2}},
    "modelUsage": {{model: {{"outputTokens": 2}}}}}}))
'''


@pytest.fixture
def counting_claude(tmp_path, monkeypatch):
    """Route the fork's engine to a real ClaudeCodeClient on a counting stand-in binary."""
    import stat as _stat
    import json as _json
    from test_skill_creator_e2e import CLEAN_RUBRIC, REFINED_METHOD
    from skill_creator import llm_client as lc
    from skill_creator import skill_creator as sc

    log = tmp_path / "claude-spawns.jsonl"
    refined = _json.dumps({
        "name": SKILL, "scope": "assistant",
        "purpose": "Validates JSON files and reports duplicate keys as warnings too.",
        "method": REFINED_METHOD, "dependencies": ["python3"], "keywords": ["json"]})
    binary = tmp_path / "bin" / "claude"
    binary.parent.mkdir()
    binary.write_text(_COUNTING_CLAUDE.format(python=sys.executable, log=str(log),
                                              rubric=CLEAN_RUBRIC, refined=refined), encoding="utf-8")
    binary.chmod(binary.stat().st_mode | _stat.S_IXUSR)
    import engine_span
    monkeypatch.setattr(engine_span, "send_live_model_usage", lambda details: None)

    class _H:
        @staticmethod
        def spawns() -> list[str]:
            if not log.exists():
                return []
            return [_json.loads(x)["prompt"] for x in log.read_text(encoding="utf-8").splitlines() if x]

        @staticmethod
        def install() -> None:
            # forge_client() replaces sc.resolve_llm_client on entry; this goes on top of it.
            # Like the real resolver, an injected client is returned unchanged (the phase
            # objects re-resolve the orchestrator's client that way).
            monkeypatch.setattr(
                sc, "resolve_llm_client",
                lambda explicit=None, **kw: explicit if explicit is not None else
                lc.ClaudeCodeClient(binary=str(binary), tenant_id=kw.get("tenant_id") or "_default"))
    return _H


def _gate_spy(monkeypatch, spawns):
    """Every text the console gates were asked about, with the spawn count at that moment."""
    from corvin_console import forge_runs
    asked: list[tuple[int, str]] = []
    real = forge_runs.check_spawn_gates

    def _spy(prompt, **kw):
        asked.append((len(spawns()), str(prompt)))
        return real(prompt, **kw)

    monkeypatch.setattr(forge_runs, "check_spawn_gates", _spy)
    return asked


def test_every_spawn_of_a_candidate_build_is_gated(tmp_path, classifier, counting_claude, monkeypatch):
    from skill_creator import autonomous

    with forge_client(tmp_path) as (client, root, home):
        counting_claude.install()
        asked = _gate_spy(monkeypatch, counting_claude.spawns)
        used = _quota_used(root)

        state = autonomous.fork(root, SKILL, source="operator", trigger={"reason": "t"})
        assert state["status"] == "canary", state

        spawns = counting_claude.spawns()
        assert len(spawns) >= 4, f"positive control: the build must spawn repeatedly, got {len(spawns)}"
        # One gate pass for the request before the build, then one per spawn.
        assert len(asked) == len(spawns) + 1, (len(asked), len(spawns))
        # Each spawn was handed a text the gate had been asked about before that spawn existed.
        for i, prompt in enumerate(spawns):
            earlier = [t for n, t in asked[1:] if n <= i]
            assert any(t and t in prompt for t in earlier), f"spawn {i} was not gated on its text"
        assert _quota_used(root) == used + 1 or _quota_used(root) == used   # member: no-op
        assert _refusals(home) == []


def test_a_refusal_in_the_middle_of_the_build_stops_every_further_spawn(tmp_path, classifier,
                                                                      counting_claude, monkeypatch):
    """The first gate pass (request + body) allows; a later call's text is refused. The phase
    code wraps or drops engine errors - the refusal must still end the build."""
    import house_rules as hr  # type: ignore
    from skill_creator import autonomous

    with forge_client(tmp_path) as (client, root, home):
        counting_claude.install()
        asked = _gate_spy(monkeypatch, counting_claude.spawns)
        used = _quota_used(root)
        n = {"calls": 0}

        def _verdict(task, rules, auth, **kw):
            n["calls"] += 1
            return ("no-disinformation", 0.97, "forced") if n["calls"] >= 3 else ("", 0.99, "ok")

        monkeypatch.setattr(hr, "_house_rules_classifier", _verdict)

        with pytest.raises(autonomous.ForkRefused):
            autonomous.fork(root, SKILL, source="autopilot", trigger={"reason": "t"})

        # Calls 1 (request) and 2 (first spawn) passed; call 3 was refused before its spawn.
        assert len(counting_claude.spawns()) == 1, counting_claude.spawns()
        assert len(asked) == 3, "the gate is not asked again after it refused"
        assert _quota_used(root) == used, "a refused build is not charged"
        assert client.get(f"{BASE}/status").json()["canaries"] == []
        assert _refusals(home), "the refusal is on the audit chain"
        assert not autonomous.forks_in_flight(root)
