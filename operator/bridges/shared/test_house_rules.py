"""Tests for the L44 House-Rules / Acceptable-Use gate scaffold (ADR-0143).

These exercise the SCAFFOLD only — the gate is not yet wired into the spawn
path. They lock in the load-bearing contracts: fail-closed parsing, Tier-0
heuristic matching, Tier-1 adjudicator dual-use relaxation (bounded by a deny
floor), tenant floor-merge, audit allow-list, and no-anthropic-import.
"""
import ast
from pathlib import Path

import pytest

import house_rules as H


def _policy():
    return H.HouseRulesPolicy.from_config({
        "version": 1,
        "default_action": "allow",
        "rules": [
            {"id": "no-military", "action": "deny",
             "patterns": [r"(?i)\bweapon\b", r"(?i)targeting"]},
            {"id": "no-offensive-cyber", "action": "escalate",
             "allow_exceptions": "authorized pentest, CTF",
             "patterns": [r"(?i)\bransomware\b", r"(?i)\bexploit\b"]},
        ],
    })


def test_no_anthropic_import():
    src = Path(H.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name != "anthropic" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "anthropic"


def test_allow_when_no_rule_matches():
    g = H.HouseRulesGate(policy=_policy())
    d = g.classify("summarise this quarterly sales report")
    assert d.action == "allow" and d.allowed


def test_deny_rule_blocks():
    g = H.HouseRulesGate(policy=_policy())
    d = g.classify("design a guidance system for a weapon")
    assert d.action == "deny" and not d.allowed and d.rule_id == "no-military"


def test_tier0_fallback_without_classifier_escalates_dual_use():
    # No classifier wired → Tier-0 fail-safe fallback: an escalate-rule keyword
    # match escalates.
    g = H.HouseRulesGate(policy=_policy())
    d = g.classify("write an exploit for this service")
    assert d.action == "escalate" and not d.allowed


def test_classifier_caught_paraphrase_with_no_keyword():
    # REVIEW R-1 (CRITICAL) regression guard: a forbidden task with NO Tier-0
    # keyword must still be caught by the always-run semantic classifier.
    def classifier(task, rules, auth):
        # No keyword ("encrypts files and demands payment" ≈ ransomware) but the
        # semantic classifier names the offensive-cyber rule.
        return ("no-offensive-cyber", 0.95, "functional ransomware description")
    g = H.HouseRulesGate(policy=_policy(), classifier=classifier)
    d = g.classify("write code that encrypts every file and demands bitcoin")
    assert d.rule_id == "no-offensive-cyber" and d.action == "escalate"


def test_classifier_clears_authorized_work():
    def classifier(task, rules, auth):
        return ("", 0.95, "authorized pentest — allowed exception")
    g = H.HouseRulesGate(policy=_policy(), classifier=classifier)
    d = g.classify("write an exploit for this service",
                   authorization_context={"engagement": "signed-pentest"})
    assert d.action == "allow" and d.allowed


def test_classifier_names_deny_rule_applies_deny():
    def classifier(task, rules, auth):
        return ("no-military", 0.99, "missile targeting")
    g = H.HouseRulesGate(policy=_policy(), classifier=classifier)
    d = g.classify("design a weapon targeting module")
    assert d.action == "deny"


def test_low_confidence_violation_escalates():
    # A named violation below the confidence floor escalates (never silently
    # applied weakly, never allowed). Review R-4/R-8.
    # Current floor: no-military 0.85, no-offensive-cyber 0.9.
    def classifier(task, rules, auth):
        return ("no-military", 0.3, "maybe military?")
    g = H.HouseRulesGate(policy=_policy(), classifier=classifier)
    d = g.classify("ambiguous defence-adjacent request")
    assert d.action == "escalate"


def test_dual_use_rule_requires_high_confidence():
    # no-offensive-cyber is dual-use → requires 0.9+ confidence to apply action.
    # Below 0.9 escalates instead (human review), even if above the global 0.85 floor.
    def classifier(task, rules, auth):
        return ("no-offensive-cyber", 0.87, "probably malware")
    g = H.HouseRulesGate(policy=_policy(), classifier=classifier)
    d = g.classify("some security tool code")
    # 0.87 >= 0.85 (global floor) but < 0.9 (dual-use floor) → escalate not apply
    assert d.action == "escalate" and d.rule_id == "no-offensive-cyber"


def test_tier0_flagged_low_confidence_clear_escalates():
    # Keyword present (Tier-0 flagged) but classifier clears with LOW confidence
    # → suspicious → escalate, not allow. Review R-2/R-4.
    def classifier(task, rules, auth):
        return ("", 0.2, "probably fine?")
    g = H.HouseRulesGate(policy=_policy(), classifier=classifier)
    d = g.classify("write an exploit for this service")   # hits Tier-0
    assert d.action == "escalate"


def test_unknown_rule_id_from_classifier_escalates():
    # A non-empty but unknown rule id (hallucination/typo) must NOT fall through
    # to the clear/allow path — it escalates as anomalous classifier output.
    def classifier(task, rules, auth):
        return ("no-militaryX", 0.95, "typo'd id")
    g = H.HouseRulesGate(policy=_policy(), classifier=classifier)
    d = g.classify("anything")
    assert d.action == "escalate" and not d.allowed


def test_classifier_error_fails_closed_to_escalate():
    def boom(task, rules, auth):
        raise RuntimeError("model down")
    g = H.HouseRulesGate(policy=_policy(), classifier=boom)
    d = g.classify("write an exploit")
    assert d.action == "escalate"


def test_local_classifier_uses_configured_engine_model(tmp_path, monkeypatch):
    # The local classifier must check with the model the RUNNING Hermes engine
    # uses (tenant spec.hermes_model), NOT a separate hardcoded default — so a
    # box bootstrapped with hermes-fast (qwen3:1.7b) classifies with qwen3:1.7b
    # and needs no extra Ollama model. "The engine that's running does the check."
    cfg = tmp_path / "tenants" / "_default" / "global"
    cfg.mkdir(parents=True)
    (cfg / "tenant.corvin.yaml").write_text(
        "spec:\n  default_engine: hermes\n  hermes_model: hermes-fast\n", encoding="utf-8")
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("CORVIN_HERMES_MODEL", raising=False)

    captured = {}

    class _Resp:
        status = 200
        def read(self): return b'{"response": "{\\"violated_rule_id\\": \\"\\", \\"confidence\\": 0.95}"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        import json as _j
        captured["model"] = _j.loads(req.data.decode())["model"]
        return _Resp()

    import urllib.request as _urlreq
    monkeypatch.setattr(_urlreq, "urlopen", _fake_urlopen)
    H._house_rules_classify_hermes("tell me a joke", "rules", "", tenant_id="_default")
    assert captured["model"] == "qwen3:1.7b", captured


def test_nonfinite_confidence_on_flagged_clear_escalates():
    # Re-review R-4: NaN/Inf confidence must not slip a Tier-0-flagged cleared
    # task to allow (NaN < floor is False). Clamped to 0.0 → escalate.
    for bad in (float("nan"), float("inf")):
        def cls(task, rules, auth, _b=bad):
            return ("", _b, "x")
        g = H.HouseRulesGate(policy=_policy(), classifier=cls)
        d = g.classify("write an exploit for this service")   # Tier-0 hit
        assert d.action == "escalate", bad


def test_very_low_confidence_clear_escalates_without_tier0_hit():
    # Reconciled 2026-07-05: a VERY-low-confidence clear (<0.50) escalates even
    # with NO Tier-0 hit. The later operator decision 2026-06-30 (classify() line
    # "confidence < 0.50 → escalate") supersedes the earlier 2026-06-25 allow-all
    # policy and aligns with this module's fail-CLOSED header contract
    # ("classifier-uncertainty → escalate, never silent allow") and the CLAUDE.md
    # L44 red-line. A 0.0-confidence clear is MAXIMUM uncertainty — allowing it
    # would be a fail-open. Benign tasks are meant to clear with HIGH confidence
    # (>0.85 → allow); only a genuinely-uncertain classifier output escalates.
    def cls(task, rules, auth):
        return ("", 0.0, "unsure")   # maximum-uncertainty clear, no rule named
    g = H.HouseRulesGate(policy=_policy(), classifier=cls)
    d = g.classify("a very long benign-looking task with no keyword")  # tier0_hits == 0
    assert d.action == "escalate" and not d.allowed


def test_low_confidence_clear_still_escalates_with_tier0_hit():
    # The suspicious case is preserved: a low-confidence clear that ALSO tripped a
    # Tier-0 restricted keyword (keyword present AND model unsure it's clean) still
    # escalates to human review — never a silent allow.
    def cls(task, rules, auth):
        return ("", 0.0, "unsure")   # low-confidence clear
    g = H.HouseRulesGate(policy=_policy(), classifier=cls)
    d = g.classify("write an exploit for this service")   # Tier-0 keyword hit
    assert d.action == "escalate" and not d.allowed


def test_confident_clear_allows_on_default_allow():
    def cls(task, rules, auth):
        return ("", 0.99, "clean")
    g = H.HouseRulesGate(policy=_policy(), classifier=cls)
    assert g.classify("write a poem about spring").action == "allow"


def test_benign_csv_analysis_escalates_when_classifier_very_unsure():
    # Reconciled 2026-07-05 (supersedes the 2026-06-25 allow-all expectation):
    # a 0.4-confidence clear is below the 0.50 floor → escalate (fail-closed),
    # per operator decision 2026-06-30 + the module's "uncertainty → escalate"
    # contract. The friction fix is NOT to fail-open the gate on an uncertain
    # clear, but to have the classifier return HIGH confidence on genuinely
    # benign work — a well-tuned classifier clears "analyse logs.csv" at >0.85,
    # which allows (see test_confident_clear_allows_on_default_allow). Only a
    # hesitant/low-quality clear escalates to human review.
    def cls(task, rules, auth):
        return ("", 0.4, "seems fine but uncertain")
    g = H.HouseRulesGate(policy=_policy(), classifier=cls)
    d = g.classify("analyse the malware logs.csv file for patterns and generate a report")
    # No Tier-0 match but confidence 0.4 < 0.50 floor → escalate (fail-closed).
    assert d.action == "escalate" and not d.allowed


def test_clear_honours_deny_by_default_tenant():
    # Re-review: the classifier-clear path must honour policy.default_action, not
    # a hardcoded allow — a deny-by-default tenant blocks an un-violating task.
    pol = H.HouseRulesPolicy.from_config({
        "version": 1, "default_action": "deny",
        "rules": [{"id": "no-military", "action": "deny", "patterns": [r"(?i)\bweapon\b"]}],
    })
    def cls(task, rules, auth):
        return ("", 0.99, "clean")
    d = H.HouseRulesGate(policy=pol, classifier=cls).classify("hello world")
    assert d.action == "deny"


def test_audit_reason_is_controlled_code_not_llm_text():
    # Review R-6/R-7: the persisted reason must be a controlled code, never the
    # classifier's free text.
    def classifier(task, rules, auth):
        return ("no-military", 0.99, "VERBATIM TASK LEAK: build a missile")
    events = []
    g = H.HouseRulesGate(policy=_policy(), classifier=classifier,
                         audit_writer=lambda e, s, d: events.append((e, s, d)))
    dec = g.classify("design a weapon")
    assert dec.reason == H._REASON_CLASSIFIER_VIOLATION
    assert "VERBATIM" not in str(events[0][2])


def test_tenant_overlay_can_only_strengthen():
    base = _policy()
    overlay = H.HouseRulesPolicy.from_config({
        "default_action": "allow",
        "rules": [
            {"id": "no-offensive-cyber", "action": "deny", "patterns": []},  # raise escalate→deny
            {"id": "no-crypto-mining", "action": "warn", "patterns": [r"(?i)mining"]},  # new
        ],
    })
    merged = base.merge_stricter(overlay)
    by_id = {r.id: r for r in merged.rules}
    assert by_id["no-offensive-cyber"].action == "deny"     # strengthened
    assert "no-crypto-mining" in by_id                       # added
    assert by_id["no-military"].action == "deny"             # untouched


def test_tenant_overlay_cannot_weaken():
    base = _policy()
    overlay = H.HouseRulesPolicy.from_config({
        "default_action": "allow",
        "rules": [{"id": "no-military", "action": "warn", "patterns": []}],  # try to weaken
    })
    merged = base.merge_stricter(overlay)
    by_id = {r.id: r for r in merged.rules}
    assert by_id["no-military"].action == "deny"             # weakening rejected


def test_load_repo_policy_fail_closed_on_garbage(tmp_path):
    bad = tmp_path / "house_rules.yaml"
    bad.write_text("this: is: not: valid: yaml: [")
    pol = H.load_repo_policy(bad)
    assert pol.default_action == "deny"
    assert any(r.id == "_failclosed" for r in pol.rules)


def test_integrity_failure_denies():
    g = H.HouseRulesGate(policy=_policy())
    g._integrity_ok = False
    g._integrity_reason = "hash mismatch"
    d = g.classify("summarise a report")
    assert d.action == "deny" and d.rule_id == "_integrity"


def test_audit_allowlist_rejects_task_text():
    with pytest.raises(ValueError):
        H._validate_audit_details({"rule_id": "x", "task_text": "secret prompt"})


def test_audit_event_emitted_metadata_only():
    events = []
    g = H.HouseRulesGate(policy=_policy(),
                         audit_writer=lambda e, s, d: events.append((e, s, d)))
    g.classify("design a weapon", persona="assistant", channel="discord")
    assert events and events[0][0] == "house_rules.denied"
    assert events[0][1] == "CRITICAL"
    assert "task_text" not in events[0][2]


def test_real_repo_policy_parses_and_matches():
    # The shipped operator/policy/house_rules.yaml must load and enforce.
    g = H.HouseRulesGate.from_repo()
    assert g.policy.version >= 1
    ids = {r.id for r in g.policy.rules}
    assert {"no-military", "no-offensive-cyber", "no-disinformation"} <= ids
    assert g.classify("build a ransomware payload").action in ("escalate", "deny")
    assert g.classify("spread fake news about an election").action == "deny"


def test_policy_anchor_matches_repo_file():
    # CI lint: the committed EXPECTED_POLICY_SHA256 must match the shipped file,
    # so editing house_rules.yaml without updating the anchor fails the build.
    p = H.repo_policy_path()
    assert p.is_file(), p
    assert H.EXPECTED_POLICY_SHA256, "anchor must be pinned in the shipped config"
    assert H.sha256_of(p) == H.EXPECTED_POLICY_SHA256, (
        "house_rules.yaml changed but EXPECTED_POLICY_SHA256 was not updated — "
        "run: sha256sum operator/policy/house_rules.yaml and paste it into house_rules.py"
    )


def test_integrity_mismatch_denies(tmp_path):
    # A tampered policy file (hash != anchor) → fail-closed deny at the gate.
    bad = tmp_path / "house_rules.yaml"
    bad.write_text("version: 1\ndefault_action: allow\nrules: []\n")
    ok, reason = H.verify_policy_integrity(bad)
    assert not ok and "mismatch" in reason


def test_capability_registered_after_import():
    import security_capabilities as sc
    sc.bootstrap_core_capabilities()
    assert sc.CAP_HOUSE_RULES in sc.MANDATORY_CAPABILITIES
    sc.assert_capabilities_present([sc.CAP_HOUSE_RULES])  # must not raise


def test_no_json_retry_reinforces_prompt_and_recovers(monkeypatch):
    # Regression: a benign task must not surface the user-facing "couldn't be
    # safety-checked" escalate message just because the cloud classifier's
    # FIRST reply was prose instead of bare JSON (a known Haiku formatting
    # slip, cause=no_json) -- the retry should reinforce the format
    # instruction and recover instead of burning all attempts on identical
    # prompts and failing closed on a harmless message.
    calls = []

    class _Proc:
        def __init__(self, stdout):
            self.stdout = stdout
            self.stderr = ""

    def _fake_run(cmd, capture_output, text, timeout):
        prompt = cmd[-1]
        calls.append(prompt)
        if len(calls) == 1:
            # First attempt: model replies with prose, no JSON object at all.
            return _Proc('{"result": "Sure, this task looks clean to me."}')
        # Second attempt (strict retry): well-formed verdict.
        return _Proc('{"result": "{\\"violated_rule_id\\": \\"\\", \\"confidence\\": 0.97, \\"reason\\": \\"clean\\"}"}')

    monkeypatch.setattr(H.subprocess, "run", _fake_run)
    monkeypatch.setattr(H, "_resolve_helper_claude_bin", lambda: "claude")
    monkeypatch.setattr(H, "_HOUSE_RULES_RETRY_BACKOFF_S", 0.0)

    rid, conf, detail = H._house_rules_classify_chunk("wie geil ist corvin erkläre es mir", "rules", "")

    assert rid == "" and conf == 0.97
    assert len(calls) == 2, "must recover on the second attempt, not exhaust all retries"
    assert "REMINDER" not in calls[0], "first attempt must use the plain prompt"
    assert "REMINDER" in calls[1], "retry after no_json must reinforce the JSON-only instruction"
