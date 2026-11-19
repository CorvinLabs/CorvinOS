"""Regression: the L44 house-rules Tier-1 classifier must resolve the claude
CLI via the hardened resolver (CORVIN_CLAUDE_BIN → PATH → known fallbacks),
NOT the bare name ``"claude"``.

Root cause this guards against: the adapter runs under systemd with a stripped
PATH that lacks ``~/.local/bin`` (where Claude Code installs the CLI). The
WorkerEngine path already survives this via
``agents.claude_code._resolve_claude_bin``, but the house-rules classifier
spawned the bare name and relied on PATH. ``FileNotFoundError`` → classifier
error → the FAIL-CLOSED L44 gate escalated EVERY request to operator approval
(observed live in Discord: "[house-rules] This request needs operator
approval … (rule 'acceptable-use')"). See adapter._resolve_helper_claude_bin.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ADR-0158 M3: classifier internals moved to house_rules.py.
# _resolve_helper_claude_bin is now in house_rules; patches must target
# house_rules (not adapter) so the callers in house_rules see the mock.
import house_rules as _house_rules_mod  # type: ignore  # noqa: E402


def _fresh_adapter():
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    return adapter


def test_resolve_helper_claude_bin_honours_pin(monkeypatch) -> None:
    """An absolute CORVIN_CLAUDE_BIN pin is returned as-is, even when PATH is
    stripped of the directory that holds the binary."""
    adapter = _fresh_adapter()
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", "/opt/custom/claude")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no claude here
    assert adapter._resolve_helper_claude_bin() == "/opt/custom/claude"


def test_resolve_helper_claude_bin_uses_fallback_under_stripped_path(monkeypatch, tmp_path) -> None:
    """With no pin and a stripped PATH, the engine fallback list still finds a
    real binary (the production systemd failure mode)."""
    adapter = _fresh_adapter()
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.delenv("CORVIN_CLAUDE_BIN", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setenv("CORVIN_CLAUDE_BIN_FALLBACKS", str(fake))
    assert adapter._resolve_helper_claude_bin() == str(fake)


def test_classifier_chunk_spawns_resolved_binary(monkeypatch) -> None:
    """``_house_rules_classify_chunk`` must put the RESOLVED binary at argv[0],
    never the bare literal ``"claude"`` (which dies on a stripped PATH)."""
    adapter = _fresh_adapter()
    monkeypatch.setattr(_house_rules_mod, "_resolve_helper_claude_bin", lambda: "/resolved/claude")

    captured: dict[str, object] = {}

    class _Proc:
        stdout = '{"violated_rule_id": "", "confidence": 1.0, "reason": "ok"}'
        stderr = ""
        returncode = 0

    def fake_run(argv, *a, **kw):  # noqa: ANN001
        captured["argv"] = argv
        return _Proc()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    rid, conf, _ = adapter._house_rules_classify_chunk("hello", "(no rules)", "none")

    argv = captured["argv"]
    assert argv[0] == "/resolved/claude", f"argv[0] was {argv[0]!r}, expected resolved binary"
    assert argv[0] != "claude", "classifier must not spawn the bare 'claude' literal"
    assert rid == "" and conf == 1.0


def test_classifier_retries_once_on_transient_then_succeeds(monkeypatch) -> None:
    """A transient spawn blip (CLI timeout) must trigger exactly ONE retry; a clean
    reply on the second attempt yields the clear — so a benign request is no longer
    fail-closed escalated by a momentary classifier hiccup (the dominant live cause:
    6 of 9 Discord escalations were transient classifier_error)."""
    adapter = _fresh_adapter()
    monkeypatch.setattr(_house_rules_mod, "_resolve_helper_claude_bin", lambda: "/resolved/claude")
    monkeypatch.setattr(adapter.time, "sleep", lambda *_a, **_k: None)  # no real backoff

    calls = {"n": 0}

    class _Proc:
        stdout = '{"violated_rule_id": "", "confidence": 0.98, "reason": "clean"}'
        stderr = ""
        returncode = 0

    def fake_run(argv, *a, **kw):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise adapter.subprocess.TimeoutExpired(cmd=argv, timeout=20)
        return _Proc()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    rid, conf, _ = adapter._house_rules_classify_chunk("hello", "(no rules)", "none")
    assert calls["n"] == 2, "must retry exactly once after a transient timeout"
    assert rid == "" and conf == 0.98


def test_classifier_no_retry_when_cli_missing(monkeypatch) -> None:
    """A missing CLI (FileNotFoundError) is not transient — retrying is pointless.
    Exactly one spawn attempt, no backoff sleep, and a ``spawn_missing``-tagged error
    propagates so the gate still fails CLOSED (escalate)."""
    adapter = _fresh_adapter()
    monkeypatch.setattr(_house_rules_mod, "_resolve_helper_claude_bin", lambda: "/nope/claude")
    sleeps = {"n": 0}
    monkeypatch.setattr(adapter.time, "sleep",
                        lambda *_a, **_k: sleeps.__setitem__("n", sleeps["n"] + 1))

    calls = {"n": 0}

    def fake_run(argv, *a, **kw):  # noqa: ANN001
        calls["n"] += 1
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    import pytest

    with pytest.raises(adapter._HouseRulesClassifierError) as ei:
        adapter._house_rules_classify_chunk("hello", "(no rules)", "none")
    assert ei.value.cause == "spawn_missing"
    assert calls["n"] == 1, "missing CLI must not be retried"
    assert sleeps["n"] == 0, "no backoff sleep on a non-transient fault"


def test_classifier_retry_exhausted_raises_tagged(monkeypatch) -> None:
    """Two transient failures exhaust the retry budget → the last tagged error
    propagates so the fail-closed gate escalates (never a silent allow)."""
    adapter = _fresh_adapter()
    monkeypatch.setattr(_house_rules_mod, "_resolve_helper_claude_bin", lambda: "/resolved/claude")
    monkeypatch.setattr(adapter.time, "sleep", lambda *_a, **_k: None)

    class _Empty:
        stdout = ""
        stderr = ""
        returncode = 0

    monkeypatch.setattr(adapter.subprocess, "run", lambda *a, **k: _Empty())
    import pytest

    with pytest.raises(adapter._HouseRulesClassifierError) as ei:
        adapter._house_rules_classify_chunk("hi", "(no rules)", "none")
    assert ei.value.cause == "empty_output"


def _run_escalate_message(adapter, monkeypatch, reason: str, rule_id: str):
    """Drive _check_house_rules_or_fail with a stubbed gate that returns a fixed
    escalate decision, and return the user-facing message."""
    import egress_gate  # type: ignore
    import house_rules as hr  # type: ignore

    dec = hr.HouseRulesDecision("escalate", rule_id, reason, 0.1)

    class _Gate:
        @classmethod
        def from_repo(cls, **kw):  # noqa: ANN001
            return cls()

        def classify(self, *a, **kw):  # noqa: ANN001
            return dec

    monkeypatch.setattr(hr, "HouseRulesGate", _Gate)
    monkeypatch.setattr(hr, "load_tenant_overlay", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(egress_gate, "make_forge_audit_writer",
                        lambda *_a, **_k: (lambda *a, **k: None), raising=False)
    return adapter._check_house_rules_or_fail(
        prompt="please do a thing", persona="assistant",
        channel="discord", chat_key="123", engine_id="claude_code",
    )


def test_escalate_message_soft_for_transient_and_clean(monkeypatch) -> None:
    """classifier_error must NOT tell the user their request "touches a restricted
    area" — it's a technical failure, not a finding against their content. It gets
    the neutral try-again message and the request is STILL blocked.

    R3-SEC-7 (2026-11-19): the adapter no longer evaluates the decision itself — it
    delegates to ``spawn_gates.check_l44`` like the console. An ESCALATE therefore
    blocks whatever its reason code: the legacy ``clear_low_confidence`` escalate used
    to be waved through here (``house_rules.allowed_after_lowconf``) while the console
    blocked it. An unsure clear that Tier-0 does not contradict never reaches this
    function as an escalate — the gate returns it as an ALLOW
    (``clear_low_confidence_no_tier0_hit``, see test_lowconf_without_tier0_runs)."""
    adapter = _fresh_adapter()

    # classifier_error: technical failure → still blocked, neutral wording
    msg = _run_escalate_message(adapter, monkeypatch, "classifier_error", "")
    assert msg is not None, "fail-closed: classifier_error must still be blocked"
    assert "couldn't be safety-checked" in msg, f"classifier_error → {msg!r}"
    assert "needs operator approval" not in msg, "classifier_error leaked firm wording"

    for reason in ("clear_low_confidence", "clear_unsure_tier0_flagged"):
        msg = _run_escalate_message(adapter, monkeypatch, reason, "")
        assert msg is not None, f"an escalate ({reason}) must block on the bridge too"
        assert "couldn't be safety-checked" in msg, f"{reason} → {msg!r}"
        assert "needs operator approval" not in msg

    msg = _run_escalate_message(adapter, monkeypatch, "prompt_over_classifier_cap", "")
    assert msg is not None and "too long to be safety-checked" in msg
    assert "needs operator approval" not in msg

    msg = _run_escalate_message(adapter, monkeypatch, "floor_only_policy_unreadable", "")
    assert msg is not None and "tenant.corvin.yaml" in msg


def test_adapter_and_console_give_the_same_l44_answer(monkeypatch) -> None:
    """One decision, one wording: the adapter's answer IS ``spawn_gates.check_l44``'s
    (what ``corvin_console._spawn_gates._check_house_rules_or_fail`` returns) for every
    reason code, allow included."""
    import egress_gate  # type: ignore
    import house_rules as hr  # type: ignore
    import spawn_gates  # type: ignore

    adapter = _fresh_adapter()
    cases = [
        ("allow", "", "clear", 0.99),
        ("allow", "", "clear_low_confidence_no_tier0_hit", 0.4),
        ("warn", "r", "warn_rule", 0.9),
        ("escalate", "", "classifier_error", 0.1),
        ("escalate", "", "clear_low_confidence", 0.1),
        ("escalate", "", "clear_unsure_tier0_flagged", 0.4),
        ("escalate", "", "prompt_over_classifier_cap", 0.0),
        ("escalate", "", "floor_only_policy_unreadable", 0.0),
        ("escalate", "no-offensive-cyber", "classifier_violation_low_confidence", 0.5),
        ("deny", "no-military", "classifier_violation", 0.95),
    ]
    monkeypatch.setattr(hr, "_house_rules_track_degradation", lambda **_kw: None)
    monkeypatch.setattr(hr, "load_tenant_overlay", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(egress_gate, "make_forge_audit_writer",
                        lambda *_a, **_k: (lambda *a, **k: None), raising=False)
    for action, rid, reason, conf in cases:
        dec = hr.HouseRulesDecision(action, rid, reason, conf)

        class _Gate:
            @classmethod
            def from_repo(cls, **kw):  # noqa: ANN001
                return cls()

            def classify(self, *a, **kw):  # noqa: ANN001
                return dec

        monkeypatch.setattr(hr, "HouseRulesGate", _Gate)
        bridge = adapter._check_house_rules_or_fail(
            prompt="please do a thing", persona="assistant",
            channel="discord", chat_key="123", engine_id="claude_code")
        console = spawn_gates.check_l44(
            "please do a thing", "_default", persona="assistant",
            channel="discord", chat_key="123", engine_id="claude_code")
        assert bridge == console, (action, reason, bridge, console)
        assert (bridge is None) == dec.allowed, (action, reason, bridge)


def test_lowconf_without_tier0_runs_and_is_recorded_allowed(monkeypatch, tmp_path) -> None:
    """The REAL gate behind the adapter: an unsure clear with no Tier-0 keyword hit runs,
    recorded once as ``house_rules.allowed`` — never as an escalate that is overridden."""
    import egress_gate  # type: ignore

    adapter = _fresh_adapter()
    writes: list[tuple] = []
    monkeypatch.setattr(egress_gate, "make_forge_audit_writer",
                        lambda *_a, **_k: (lambda *a, **k: writes.append(a)), raising=False)
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 0.4, "unsure")
    msg = adapter._check_house_rules_or_fail(
        prompt="what is the capital of France", persona="assistant",
        channel="discord", chat_key="123", engine_id="claude_code")
    assert msg is None, msg
    types = [w[0] for w in writes]
    assert "house_rules.allowed" in types, types
    assert "house_rules.escalated" not in types, types
    assert "house_rules.allowed_after_lowconf" not in types, types
    import house_rules as hr  # type: ignore
    assert hr._house_rules_classifier is not adapter._house_rules_classifier, \
        "the adapter's classifier binding must not stay installed in house_rules"


def test_escalate_message_firm_for_genuine_borderline(monkeypatch) -> None:
    """A genuine borderline verdict (classifier flagged a rule at low confidence)
    keeps the firm operator-approval wording — the soft message is reserved for
    non-findings only."""
    adapter = _fresh_adapter()
    msg = _run_escalate_message(
        adapter, monkeypatch, "classifier_violation_low_confidence", "no-offensive-cyber")
    assert msg is not None
    assert "needs operator approval" in msg
    assert "couldn't be safety-checked" not in msg


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))


def _run_escalate_capturing_audit(adapter, monkeypatch, reason: str, rule_id: str):
    """Like _run_escalate_message but records every audit write the gate makes."""
    import egress_gate  # type: ignore
    import house_rules as hr  # type: ignore

    dec = hr.HouseRulesDecision("escalate", rule_id, reason, 0.42)
    writes: list[tuple] = []

    class _Gate:
        @classmethod
        def from_repo(cls, **kw):  # noqa: ANN001
            return cls()

        def classify(self, *a, **kw):  # noqa: ANN001
            return dec

    monkeypatch.setattr(hr, "HouseRulesGate", _Gate)
    monkeypatch.setattr(hr, "load_tenant_overlay", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(egress_gate, "make_forge_audit_writer",
                        lambda *_a, **_k: (lambda *a, **k: writes.append((a, k))), raising=False)
    msg = adapter._check_house_rules_or_fail(
        prompt="please do a thing", persona="assistant",
        channel="discord", chat_key="123", engine_id="claude_code",
    )
    return msg, writes


def test_escalate_is_never_overridden_into_an_allow(monkeypatch) -> None:
    """F-B4 (2026-09-07) recorded the adapter's override of a ``clear_low_confidence``
    escalate as ``house_rules.allowed_after_lowconf``. R3-SEC-7 removes the override
    itself: an escalate the gate recorded blocks, so no overriding record exists."""
    adapter = _fresh_adapter()
    monkeypatch.setattr(_house_rules_mod, "_house_rules_track_degradation", lambda **_kw: None)
    for reason in ("clear_low_confidence", "classifier_error", "clear_unsure_tier0_flagged"):
        msg, writes = _run_escalate_capturing_audit(adapter, monkeypatch, reason, "")
        assert msg is not None, reason
        assert "please do a thing" not in repr(writes)
        assert not [w for w in writes if w[0] and w[0][0] == "house_rules.allowed_after_lowconf"]
