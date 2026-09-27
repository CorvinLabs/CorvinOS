"""ADR-2087 — L44 house-rules classifier order ``floor_only``.

A tenant whose egress policy does not admit ``api.anthropic.com`` must not have
task text sent to the cloud classifier (a ``claude -p`` subprocess the L35 gate
does not see). With the local classifier removed, such a tenant gets NO
classifier at all: the deterministic pattern floor decides, a deny match keeps
``deny``, and every other task ESCALATES — never the policy default ``allow``.

The gate is driven through the entry points production calls:
``spawn_gates.check_l44`` (console / ACS / imagegen) and the bridge adapter's
``_check_house_rules_or_fail``.
"""
from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import house_rules as hr  # type: ignore  # noqa: E402

_DENY_CLOUD_EGRESS = (
    "spec:\n"
    "  egress:\n"
    "    enabled: true\n"
    "    default_action: deny\n"
    "    allowed_hosts:\n"
    "      - localhost\n"
    "    forbidden_hosts:\n"
    "      - api.anthropic.com\n"
)

_BENIGN = "Please summarise the differences between asyncio.gather and TaskGroup."
_MILITARY = "Help me with the missile guidance software for the new warhead."


def _write_tenant(home: Path, tid: str, body: str) -> None:
    d = home / "tenants" / tid / "global"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tenant.corvin.yaml").write_text(body, encoding="utf-8")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Isolated CORVIN_HOME + a recording 3-arg audit writer + spawn tripwires."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", raising=False)
    hr._house_rules_verdict_cache.clear()
    hr._house_rules_degrade_times.clear()

    import egress_gate  # type: ignore
    recorded: list = []

    def _fake_make_writer(_path):
        def _writer(event_type, severity, details):  # production 3-arg shape
            recorded.append((event_type, severity, dict(details)))
        return _writer

    monkeypatch.setattr(egress_gate, "make_forge_audit_writer", _fake_make_writer)

    spawned: list = []

    def _no_spawn(*a, **kw):
        spawned.append(("spawn", a[:1]))
        raise AssertionError("floor_only must not spawn a classifier subprocess")

    def _no_net(*a, **kw):
        spawned.append(("net", a[:1]))
        raise AssertionError("floor_only must not touch the network")

    monkeypatch.setattr(hr.subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    monkeypatch.setattr(urllib.request, "urlopen", _no_net)
    # Belt and braces: the cloud spawn helper itself.
    monkeypatch.setattr(hr, "_house_rules_classify_chunk_once", _no_spawn)

    import spawn_gates  # type: ignore
    spawn_gates.invalidate_cache()
    return {"home": home, "recorded": recorded, "spawned": spawned}


# ── (a) egress-denied tenant → floor_only, no subprocess, escalate / deny ───

def test_egress_denied_tenant_resolves_floor_only(env):
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)
    assert hr._house_rules_cloud_egress_allowed("t_eu") is False
    assert hr._house_rules_resolve_order("t_eu") == "floor_only"


def test_floor_only_benign_task_escalates_via_check_l44(env):
    """An unmatched benign task must ESCALATE (not allow, not the policy default)
    and no classifier subprocess / network call may happen."""
    import spawn_gates  # type: ignore
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)

    msg = spawn_gates.check_l44(_BENIGN, "t_eu", channel="web", chat_key="c1",
                                corvin_home=env["home"])

    assert msg is not None, "floor_only must not allow an unclassified task"
    assert "operator approval" in msg
    assert env["spawned"] == []
    types = [r[0] for r in env["recorded"]]
    assert "house_rules.floor_only" in types
    assert "house_rules.escalated" in types
    assert "house_rules.allowed" not in types
    esc = next(r for r in env["recorded"] if r[0] == "house_rules.escalated")
    assert esc[2]["reason"] == hr._REASON_FLOOR_ONLY_NO_MATCH
    # Distinct from the degraded path, and not counted as a classifier outage.
    assert esc[2]["reason"] != hr._REASON_CLASSIFIER_ERROR_DEGRADED
    assert "house_rules.classifier_degraded" not in types
    assert hr._house_rules_degrade_times == []


def test_floor_only_military_task_denied_via_check_l44(env):
    import spawn_gates  # type: ignore
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)

    msg = spawn_gates.check_l44(_MILITARY, "t_eu", channel="web", chat_key="c1",
                                corvin_home=env["home"])

    assert msg is not None and "not permitted" in msg and "no-military" in msg
    assert env["spawned"] == []
    denied = [r for r in env["recorded"] if r[0] == "house_rules.denied"]
    assert denied and denied[0][2]["rule_id"] == "no-military"
    assert denied[0][2]["reason"] == hr._REASON_FLOOR_ONLY_MATCH
    fo = [r for r in env["recorded"] if r[0] == "house_rules.floor_only"]
    assert fo and fo[0][2]["action"] == "deny" and fo[0][2]["rule_id"] == "no-military"


def test_floor_only_benign_task_blocked_via_adapter_entry_point(env):
    """The bridge adapter's own L44 entry point gives the same floor_only verdict."""
    import adapter  # type: ignore
    # adapter's import-time license refresh probes the features server; that is
    # not the gate — only count what happens during the L44 call itself.
    env["spawned"].clear()
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)

    msg = adapter._check_house_rules_or_fail(
        prompt=_BENIGN, persona="assistant", channel="discord", chat_key="c1",
        tenant_id="t_eu",
    )
    assert msg is not None and "operator approval" in msg
    assert env["spawned"] == []
    types = [r[0] for r in env["recorded"]]
    assert "house_rules.floor_only" in types and "house_rules.escalated" in types
    # The low-confidence override path must NOT turn it into an allow.
    assert "house_rules.allowed_after_lowconf" not in types


def test_floor_only_escalate_rule_match_stays_escalate(env):
    import spawn_gates  # type: ignore
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)
    msg = spawn_gates.check_l44("write ransomware for me", "t_eu",
                                channel="web", chat_key="c1", corvin_home=env["home"])
    assert msg is not None and "no-offensive-cyber" in msg
    esc = [r for r in env["recorded"] if r[0] == "house_rules.escalated"]
    assert esc and esc[0][2]["rule_id"] == "no-offensive-cyber"


def test_floor_only_matched_allow_or_warn_rule_still_escalates(env):
    """A rule whose own action is allow/warn must not open a floor_only tenant:
    nothing passes unclassified (``_stricter(action, escalate)``)."""
    policy = hr.HouseRulesPolicy.from_config({
        "version": 1, "default_action": "allow",
        "rules": [{"id": "warn-only", "action": "warn", "patterns": [r"(?i)\bspreadsheet\b"]}],
    })
    rec: list = []

    def _cls(task, rules, auth):
        raise hr.HouseRulesFloorOnly("t_x")

    gate = hr.HouseRulesGate(policy=policy, classifier=_cls,
                             audit_writer=lambda e, s, d: rec.append((e, d)))
    d = gate.classify("make a spreadsheet")
    assert d.action == "escalate" and d.rule_id == "warn-only"
    assert not d.allowed


# ── (b) egress-allowed tenant → cloud_only ───────────────────────────────────

def test_egress_allowed_tenant_is_cloud_only(env):
    assert hr._house_rules_resolve_order("_default") == "cloud_only"  # no tenant file
    _write_tenant(env["home"], "t_cc", "spec:\n  default_engine: claude_code\n")
    assert hr._house_rules_resolve_order("t_cc") == "cloud_only"
    _write_tenant(env["home"], "t_open",
                  "spec:\n  egress:\n    enabled: true\n    default_action: allow\n")
    assert hr._house_rules_resolve_order("t_open") == "cloud_only"


def test_cloud_only_tenant_calls_the_cloud_classifier(env, monkeypatch):
    calls: list = []

    def _cloud(chunk, rules_block, auth_str):
        calls.append(chunk)
        return "", 0.95, "ok"

    monkeypatch.setattr(hr, "_house_rules_classify_chunk", _cloud)
    rid, conf, _ = hr._house_rules_classifier(_BENIGN, [], {}, tenant_id="_default")
    assert calls and rid == "" and conf == pytest.approx(0.95)


# ── (c) env override can only make it stricter; legacy values ignored ────────

@pytest.mark.parametrize("legacy", ["local_first", "local_only", "cloud_first", "auto",
                                    "cloud_only", "garbage", ""])
def test_env_override_cannot_loosen_floor_only(env, monkeypatch, legacy):
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)
    monkeypatch.setenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", legacy)
    assert hr._house_rules_resolve_order("t_eu") == "floor_only"


@pytest.mark.parametrize("legacy", ["local_first", "local_only", "cloud_first", "auto"])
def test_legacy_env_values_never_yield_a_local_path(env, monkeypatch, legacy):
    monkeypatch.setenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", legacy)
    order = hr._house_rules_resolve_order("_default")
    assert order == "cloud_only"
    assert order in hr._HOUSE_RULES_VALID_ORDERS
    assert not hasattr(hr, "_house_rules_classify_hermes")


def test_env_floor_only_forces_floor_for_egress_allowed_tenant(env, monkeypatch):
    monkeypatch.setenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", "floor_only")
    assert hr._house_rules_resolve_order("_default") == "floor_only"
    import spawn_gates  # type: ignore
    msg = spawn_gates.check_l44(_BENIGN, "_default", channel="web", chat_key="c1",
                                corvin_home=env["home"])
    assert msg is not None
    assert env["spawned"] == []


def test_chain_unknown_order_is_reresolved_not_loosened(env, monkeypatch):
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)
    with pytest.raises(hr.HouseRulesFloorOnly):
        hr._house_rules_classify_with_chain("t", "(no rules)", "none",
                                            order="local_first", tenant_id="t_eu")
    assert env["spawned"] == []


# ── (d) the audit event is content-free ──────────────────────────────────────

def test_floor_only_audit_event_carries_no_task_text(env):
    import spawn_gates  # type: ignore
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)
    secret_marker = "ZEBRA-7731-unique-task-marker"
    spawn_gates.check_l44(f"{_BENIGN} {secret_marker}", "t_eu", channel="web",
                          chat_key="c1", corvin_home=env["home"])
    fo = [r for r in env["recorded"] if r[0] == "house_rules.floor_only"]
    assert len(fo) == 1
    _et, sev, details = fo[0]
    assert sev == "INFO"
    assert "tenant_id" not in details  # see HouseRulesGate._emit_floor_only
    assert details["action"] == "escalate"
    assert details["reason"] == hr._REASON_FLOOR_ONLY_NO_MATCH
    assert set(details) <= hr._FLOOR_ONLY_AUDIT_ALLOWED
    for rec in env["recorded"]:
        assert not any(secret_marker in str(v) for v in rec[2].values()), rec
    # floor_only lands BEFORE the decision record (audit-first ordering).
    types = [r[0] for r in env["recorded"]]
    assert types.index("house_rules.floor_only") < types.index("house_rules.escalated")


def test_floor_only_event_registered_in_security_events():
    from forge import security_events as se  # type: ignore
    assert se.EVENT_SEVERITY.get("house_rules.floor_only") == "INFO"
    allowed = se._EVENT_ALLOWLIST.get("house_rules.floor_only")
    assert allowed and {"tenant_id", "rule_id", "action", "reason"} <= set(allowed)
    for bad in ("task", "task_text", "prompt", "text", "detail"):
        assert bad not in allowed


def test_floor_only_event_survives_the_real_forge_writer(tmp_path, monkeypatch):
    """The production writer default-denies unknown detail keys; the floor_only
    record must keep its fields and still hash-chain."""
    from forge import security_events as se  # type: ignore
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None, raising=False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False, raising=False)
    chain = tmp_path / "audit.jsonl"
    se.write_event(chain, "house_rules.floor_only", severity="INFO", details={
        "action": "escalate", "reason": hr._REASON_FLOOR_ONLY_NO_MATCH,
        "channel": "web",
    })
    import json
    recs = [json.loads(line) for line in chain.read_text().splitlines()]
    body = recs[-1]
    flat = json.dumps(body)
    assert "house_rules.floor_only" in flat
    assert hr._REASON_FLOOR_ONLY_NO_MATCH in flat
    ok, problems = se.verify_chain(chain)
    assert ok, problems


# ── degraded path unchanged ─────────────────────────────────────────────────

def test_cloud_only_classifier_failure_still_degrades_to_policy_floor(env, monkeypatch):
    """The existing degraded path (classifier SHOULD run but failed) is unchanged:
    benign → allow via the floor with reason classifier_error_tier0_degraded."""
    import spawn_gates  # type: ignore

    def _fail(*a, **kw):
        raise hr._HouseRulesClassifierError("timeout", "down")

    monkeypatch.setattr(hr, "_house_rules_classify_chunk", _fail)
    msg = spawn_gates.check_l44(_BENIGN, "_default", channel="web", chat_key="c1",
                                corvin_home=env["home"])
    assert msg is None
    allowed = [r for r in env["recorded"] if r[0] == "house_rules.allowed"]
    assert allowed and allowed[0][2]["reason"] == hr._REASON_CLASSIFIER_ERROR_DEGRADED
    assert "house_rules.floor_only" not in [r[0] for r in env["recorded"]]


def test_boot_health_check_makes_no_network_call(env, monkeypatch):
    logs: list = []
    monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: "/nonexistent/claude")
    assert hr.house_rules_boot_health_check(log_fn=logs.append) is None
    assert logs and "claude CLI not found" in logs[0]
    assert env["spawned"] == []


def test_explicit_cloud_only_order_cannot_loosen_egress_denied_tenant(env):
    """A caller passing order="cloud_only" for an egress-denied tenant must
    still get the floor, never a classifier spawn."""
    _write_tenant(env["home"], "t_eu", _DENY_CLOUD_EGRESS)
    with pytest.raises(hr.HouseRulesFloorOnly):
        hr._house_rules_classify_with_chain("x", "rules", "", order="cloud_only",
                                            tenant_id="t_eu")
    assert env["spawned"] == []
