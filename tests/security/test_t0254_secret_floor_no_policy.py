"""T-0254 — a tenant with NO ``tenant.corvin.yaml`` still refuses a SECRET prompt.

Before: ``load_guard_for_tenant`` returned ``None`` for such a tenant and every spawn-site
gate read that as "no enforcement", so a prompt carrying a literal credential reached a
cloud engine with no classification record. Now the built-in default guard judges SECRET
(and only SECRET) for a policy-less tenant: refused, and ``data_flow.blocked`` lands in the
tenant's own audit chain without the prompt text.

Every test goes through a real entry point (bridge gate, console gate, delegation gate, A2A
worker) with a real sandboxed ``CORVIN_HOME`` and a real chain file; nothing under test is
mocked except the engine factory of the A2A worker, which exists only to prove that no
engine was started.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (
    _REPO / "corvin_operator" / "forge",
    _REPO / "core" / "delegate",
    _REPO / "core" / "console",
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO,
):
    sys.path.insert(0, str(_p))

import data_classification as dc  # noqa: E402
import spawn_gates  # noqa: E402

# Built from parts: this file must not itself contain a credential-shaped literal.
SECRET_PROMPT = "please push with token ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
TOKEN_FRAGMENT = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "corvin"
    h.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    spawn_gates._l34_cache.clear()
    # L44 runs BEFORE L34 on the console and A2A paths and may spawn a cloud classifier whose
    # verdict on a credential-shaped prompt varies from run to run. Pin it to "allow" (the idiom
    # of test_acs_quota_fallback.py) so these tests judge L34 only and never make a model call.
    import house_rules  # noqa: PLC0415
    monkeypatch.setattr(house_rules, "_house_rules_classifier",
                        lambda task, rules, auth, **kw: ("", 0.0, "test clear"))
    return h


def _chain(home: Path) -> Path:
    return home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"


def _records(home: Path) -> list[dict]:
    p = _chain(home)
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text("utf-8").splitlines() if line.strip()]


def _write_policy(home: Path, body: str = "spec: {}\n") -> None:
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body, "utf-8")
    cfg.chmod(0o600)
    spawn_gates._l34_cache.clear()


# ── the bridge gate (check_l34) ───────────────────────────────────────────────


def test_secret_prompt_without_policy_is_refused_and_audited(home):
    out = spawn_gates.check_l34("claude_code", "_default", prompt=SECRET_PROMPT,
                                persona="assistant", corvin_home=home)
    assert out is not None and "[data-flow] Spawn rejected" in out and "SECRET" in out

    blocked = [r for r in _records(home) if r.get("event_type") == "data_flow.blocked"]
    assert len(blocked) == 1, _records(home)
    details = blocked[0].get("details", blocked[0])
    assert details.get("classification") == "SECRET"
    assert details.get("engine_id") == "claude_code"
    assert TOKEN_FRAGMENT not in _chain(home).read_text("utf-8"), "the prompt must never be audited"


@pytest.mark.parametrize("prompt", [
    "summarize this article about rust",
    "please email me at anna.example@example.com about the invoice",   # CONFIDENTIAL stays opt-in
    "pay to DE89 3704 0044 0532 0130 00",                              # IBAN: CONFIDENTIAL, not SECRET
])
def test_everything_below_secret_is_untouched_and_unaudited(home, prompt):
    assert spawn_gates.check_l34("claude_code", "_default", prompt=prompt,
                                 persona="assistant", corvin_home=home) is None
    assert _records(home) == [], "a policy-less tenant gets no per-turn audit record below SECRET"


def test_a_public_marker_cannot_down_classify_a_secret(home):
    out = spawn_gates.check_l34("claude_code", "_default", prompt="[class:public] " + SECRET_PROMPT,
                                persona="assistant", corvin_home=home)
    assert out is not None and "SECRET" in out


def test_declared_classification_is_honoured(home):
    assert spawn_gates.check_l34("claude_code", "_default", classification="SECRET",
                                 corvin_home=home) is not None
    assert spawn_gates.check_l34("claude_code", "_default", classification="internal",
                                 corvin_home=home) is None


def test_an_unregistered_engine_is_refused_for_secret(home):
    out = spawn_gates.check_l34("some_new_engine", "_default", prompt=SECRET_PROMPT, corvin_home=home)
    assert out is not None, "SECRET to an engine the registry does not know must not pass"


def test_a_classifier_error_without_policy_is_not_a_refusal(home, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("classifier down")
    monkeypatch.setattr(dc, "classify_task", boom)
    assert spawn_gates.check_l34("claude_code", "_default", prompt=SECRET_PROMPT,
                                 corvin_home=home) is None


def test_a_tenant_with_a_policy_is_still_judged_by_that_policy(home):
    _write_policy(home)
    out = spawn_gates.check_l34("claude_code", "_default", prompt=SECRET_PROMPT,
                                persona="assistant", corvin_home=home)
    assert out is not None
    assert "Classification SECRET is not allowed" in out, "the policy path, not the floor message"
    assert "even without a tenant.corvin.yaml" not in out


# ── the console gate (what chat_runtime calls) ────────────────────────────────


def test_console_gate_refuses_a_secret_without_policy(home):
    from corvin_console import _spawn_gates as console_gates
    out = console_gates.check_console_spawn_or_refusal(
        SECRET_PROMPT, tenant_id="_default", persona="assistant", channel="web", chat_key="t0254")
    assert out is not None and "SECRET" in out
    assert any(r.get("event_type") == "data_flow.blocked" for r in _records(home))


def test_console_gate_lets_an_ordinary_prompt_through(home):
    from corvin_console import _spawn_gates as console_gates
    assert console_gates.check_console_spawn_or_refusal(
        "summarize this article about rust", tenant_id="_default", persona="assistant",
        channel="web", chat_key="t0254") is None


def test_console_os_turn_path_refuses_a_secret_without_policy(home):
    """``chat_runtime`` starts the OS turn through ``resolve_console_spawn`` (switchable), not
    through ``check_console_spawn_or_refusal``; the floor must hold on that road too."""
    from corvin_console import _spawn_gates as console_gates
    route = console_gates.resolve_console_spawn(
        SECRET_PROMPT, tenant_id="_default", persona="assistant", channel="web",
        chat_key="t0254-os", switchable=True)
    assert route.refusal is not None and "SECRET" in route.refusal
    ok = console_gates.resolve_console_spawn(
        "summarize this article about rust", tenant_id="_default", persona="assistant",
        channel="web", chat_key="t0254-os", switchable=True)
    assert ok.refusal is None


# ── delegation ────────────────────────────────────────────────────────────────


def test_delegation_gate_refuses_a_secret_without_policy(home):
    from corvin_delegate import delegation
    res = delegation._check_data_classification(
        engine="claude_code", prompt=SECRET_PROMPT, model=None, persona_tag="assistant")
    assert res is not None and res.ok is False and "data-flow-denied" in res.error
    assert delegation._check_data_classification(
        engine="claude_code", prompt="summarize rust", model=None, persona_tag="assistant") is None


# ── inbound A2A worker ────────────────────────────────────────────────────────


def test_a2a_worker_refuses_a_secret_without_policy_and_starts_no_engine(home):
    import a2a_worker
    started: list[str] = []

    def factory(*_a, **_k):
        started.append("engine")
        raise AssertionError("no engine may be created for a refused task")

    res = a2a_worker.spawn_a2a_worker(
        instruction=SECRET_PROMPT, origin_id="peer-t0254", task_id="t-0254",
        persona="assistant", ttl_s=30, engine_factory=factory)
    assert res.status == "rejected" and res.reason_code == "data_flow", res
    assert started == []
    assert any(r.get("event_type") == "data_flow.blocked" for r in _records(home))
