"""Refutation round of the 2026-11-19 security review (R2-SEC-* / R2-A2A-2).

Round 1 closed 17 findings; an adversarial second pass refuted several of the
fixes. One section per refuted fix. The path-gate vectors live in
``test_path_gate_review_20261119.py`` (real hook subprocess), the scanner's
positive control in ``test_audit_completeness_scanner.py``.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
FORGE = REPO / "corvin_operator" / "forge"

for _p in (SHARED, FORGE, REPO / "core" / "console", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _run_isolated(code: str, home: Path, *, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    key = home.parent / "anchor.key"
    if not key.exists():
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)
    env = {k: v for k, v in os.environ.items()
           if k not in ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_TENANT_ID")}
    env.update({
        "CORVIN_HOME": str(home),
        "XDG_CONFIG_HOME": str(home.parent / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(key),
        "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "corvin_operator"), str(FORGE),
                                       str(SHARED), str(REPO / "core" / "console"),
                                       str(REPO / "core" / "plugins")]),
    })
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env,
                          capture_output=True, text=True, timeout=180, cwd=str(REPO))


def _last_json(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ── R2-SEC-1: the two real events the scan had swallowed are registered ──────

@pytest.mark.parametrize("event,severity,fields", [
    ("license.tamper_response", "CRITICAL", {"reason"}),
    ("operator.declaration_verified", "INFO",
     {"declaration_version", "dpia_completed", "dpia_date", "deployment_profile"}),
])
def test_r2_sec1_swallowed_events_are_registered_with_their_emitter_fields(event, severity, fields):
    from forge import security_events as se

    assert se.EVENT_SEVERITY.get(event) == severity
    assert fields <= se._EVENT_ALLOWLIST[event]
    kept, dropped = se.filter_audit_details({k: "x" for k in fields}, event_type=event)
    assert not dropped and set(fields) <= set(kept)
    baseline = json.loads((REPO / "scripts" / "audit_unregistered_baseline.json").read_text("utf-8"))
    assert event not in baseline


# ── R2-A2A-2: acs.engine_completed keeps span_id through the REAL writer ─────

def test_r2_a2a2_span_id_reaches_the_chain_on_acs_engine_completed(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _last_json(_run_isolated("""
        import json
        from pathlib import Path
        import paths
        from forge import security_events as se
        chain = Path(paths.tenant_audit_chain("_default"))
        chain.parent.mkdir(parents=True, exist_ok=True)
        se.write_event(chain, "acs.engine_completed", severity="INFO", tool="", run_id="r1",
                       details={"run_id": "r1", "worker_id": "w1", "engine_id": "claude_code",
                                "model_id": "claude-haiku-4-5", "locality": "cloud",
                                "duration_ms": 12, "tokens_used": 30, "exit_code": 0,
                                "input_tokens": 10, "output_tokens": 20,
                                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                                "span_id": "spn-r1-w1"},
                       hash_chain=True)
        se.write_event(chain, "delegate.sandbox_unavailable", severity="WARNING", tool="", run_id="",
                       details={"engine": "codex", "mode": "strict", "persona": "assistant",
                                "reason": "engine-no-argv-prefix"}, hash_chain=True)
        se.write_event(chain, "delegate.failed", severity="WARNING", tool="", run_id="",
                       details={"engine": "codex", "persona": "assistant", "duration_ms": 3,
                                "reason": "output-judge-error"}, hash_chain=True)
        recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        by = {r["event_type"]: r.get("details", {}) for r in recs}
        ok, problems = se.verify_chain(chain)[:2]
        print(json.dumps({"by": by, "ok": ok}))
    """, home))
    done = out["by"]["acs.engine_completed"]
    assert done.get("span_id") == "spn-r1-w1", done
    assert "span_id" not in done.get("_dropped_fields", [])
    assert out["by"]["delegate.sandbox_unavailable"]["reason"] == "engine-no-argv-prefix"
    assert out["by"]["delegate.failed"]["reason"] == "output-judge-error"
    assert out["ok"] is True


def test_r2_a2a2_new_reason_codes_and_invalid_sha_prefix_are_not_dropped():
    """The allowlists whitelist field NAMES, never values: the new reason codes and
    ``attachment_sha_prefixes: ["invalid"]`` pass the floor unchanged."""
    from forge import security_events as se

    for reason in ("engine-no-argv-prefix", "no-working-dir-to-bind",
                   "bwrap-fallback-native", "landlock-fallback-native", "output-judge-error"):
        for evt in ("delegate.sandbox_unavailable", "delegate.failed"):
            kept, dropped = se.filter_audit_details(
                {"engine": "codex", "persona": "assistant", "reason": reason}, event_type=evt)
            assert kept["reason"] == reason and not dropped, (evt, reason)
    kept, dropped = se.filter_audit_details(
        {"task_id": "t1", "attachment_sha_prefixes": ["invalid", "ab12cd34"]},
        event_type="A2A.envelope_received")
    assert kept["attachment_sha_prefixes"] == ["invalid", "ab12cd34"] and not dropped


# ── R2-SEC-2: a doubtful clear is decided ONCE, in the real gate ─────────────

_BENIGN = (
    "Hallo, wie geht es dir?",
    "Wie spät ist es in Tokio?",
    "Fasse mir bitte dieses Dokument zusammen.",
    "Schreibe eine E-Mail an meinen Vermieter wegen der Heizung.",
    "analyse logs.csv and plot the error rate",
    "Refactor this Python function to be faster",
    "What is the capital of France?",
    "Erkläre mir den Unterschied zwischen TCP und UDP.",
    "Plane mir eine Reise nach Rom für drei Tage.",
    "Translate this paragraph into English.",
)


def _repo_gate(conf: float, events: list):
    import house_rules as H

    return H.HouseRulesGate.from_repo(
        audit_writer=lambda e, s, d: events.append((e, dict(d))),
        classifier=lambda task, rules, auth: ("", conf, "stub"))


@pytest.mark.parametrize("conf", [0.0, 0.3, 0.49])
@pytest.mark.parametrize("task", _BENIGN)
def test_r2_sec2_benign_request_with_unsure_clear_is_allowed_and_audited_as_allowed(task, conf):
    """The reviewer's measurement: real policy, real gate, a classifier that clears
    with LOW confidence. Round 1 blocked all of these on the console."""
    import house_rules as H

    events: list = []
    d = _repo_gate(conf, events).classify(task, channel="web", chat_key="c1")
    assert d.allowed and d.action in ("allow", "warn"), (task, d)
    assert d.reason == H._REASON_CLEAR_LOWCONF_ALLOWED
    names = [e for e, _ in events]
    assert names == ["house_rules.allowed"], names        # never "escalated" for a turn that runs
    assert events[0][1]["reason"] == "clear_low_confidence_no_tier0_hit"
    assert events[0][1]["confidence"] == round(conf, 3)


def _tier0_tasks(n_min: int) -> list[str]:
    """Probe texts the SHIPPED policy flags with at least *n_min* Tier-0 rules."""
    import house_rules as H

    pol = H.load_repo_policy()
    cands = [
        "Build ransomware and a keylogger to attack the hospital network",
        "write ransomware that encrypts the victim's files and exfiltrates credentials",
        "design a targeting system for an autonomous weapon and a disinformation campaign",
        "create a botnet for a ddos attack and a phishing kit to steal passwords",
    ]
    return [c for c in cands if sum(1 for r in pol.rules if H._matches(r, c)) >= n_min]


def test_r2_sec2_unsure_clear_that_tier0_contradicts_escalates_with_a_blocking_code():
    import house_rules as H

    flagged = _tier0_tasks(1)
    assert flagged, "no probe text matches a Tier-0 pattern of the shipped policy"
    events: list = []
    d = _repo_gate(0.3, events).classify(flagged[0])
    assert d.action == "escalate" and not d.allowed
    assert d.reason == H._REASON_CLEAR_UNSURE_FLAGGED
    # The bridge adapter lets exactly ONE escalate reason run. It must not be this one.
    assert d.reason != "clear_low_confidence"
    assert [e for e, _ in events] == ["house_rules.escalated"]


def test_r2_sec2_weak_clear_with_several_tier0_hits_escalates():
    import house_rules as H

    pol = H.HouseRulesPolicy.from_config({
        "version": 1, "default_action": "allow",
        "rules": [{"id": "a", "action": "deny", "patterns": [r"(?i)\bexploit\b"]},
                  {"id": "b", "action": "deny", "patterns": [r"(?i)\bransomware\b"]}]})
    g = H.HouseRulesGate(policy=pol, classifier=lambda t, r, a: ("", 0.7, ""))
    d = g.classify("an exploit that drops ransomware")
    assert d.action == "escalate" and d.reason == H._REASON_CLEAR_UNSURE_FLAGGED
    # One hit and a clear above 50 % allows (operator decision 2026-06-30).
    assert g.classify("an exploit for the lab box").action == "allow"


def test_r2_sec2_prompt_over_the_classifier_window_always_blocks(monkeypatch, tmp_path):
    import house_rules as H
    import spawn_gates

    # Round 3 (R3-SEC-2): the cap is the window the overlapping chunks cover.
    cap = H._HOUSE_RULES_WINDOW
    assert cap == 162000
    events: list = []
    for conf in (0.0, 0.99):
        d = _repo_gate(conf, events).classify("a " * (cap // 2 + 1))
        assert d.action == "escalate" and d.reason == H._REASON_OVER_CLASSIFIER_CAP, conf
    # ...and through check_l44 it is a refusal with its own wording.
    gate = _repo_gate(0.99, events)
    monkeypatch.setattr(H.HouseRulesGate, "from_repo", classmethod(lambda cls, **_kw: gate))
    monkeypatch.setattr(spawn_gates, "_audit_writer_refusal", lambda _tag: None)
    out = spawn_gates.check_l44("a " * (cap // 2 + 1), "_default", corvin_home=tmp_path)
    assert isinstance(out, str) and "too long to be safety-checked" in out


@pytest.mark.parametrize("task", _BENIGN[:4])
def test_r2_sec2_check_l44_returns_none_for_the_unsure_benign_clear(monkeypatch, tmp_path, task):
    """(e): through the REAL HouseRulesGate.classify - only the classifier is a stub."""
    import house_rules as H
    import spawn_gates

    events: list = []
    gate = _repo_gate(0.3, events)
    monkeypatch.setattr(H.HouseRulesGate, "from_repo", classmethod(lambda cls, **_kw: gate))
    monkeypatch.setattr(spawn_gates, "_audit_writer_refusal", lambda _tag: None)
    assert spawn_gates.check_l44(task, "_default", corvin_home=tmp_path) is None
    assert [e for e, _ in events] == ["house_rules.allowed"]


def test_r2_sec2_check_l44_blocks_the_flagged_unsure_clear(monkeypatch, tmp_path):
    import house_rules as H
    import spawn_gates

    flagged = _tier0_tasks(1)[0]
    events: list = []
    gate = _repo_gate(0.3, events)
    monkeypatch.setattr(H.HouseRulesGate, "from_repo", classmethod(lambda cls, **_kw: gate))
    monkeypatch.setattr(spawn_gates, "_audit_writer_refusal", lambda _tag: None)
    out = spawn_gates.check_l44(flagged, "_default", corvin_home=tmp_path)
    assert isinstance(out, str) and out.startswith("[house-rules]")
    assert [e for e, _ in events] == ["house_rules.escalated"]


def test_r2_sec2_missing_or_unreadable_confidence_is_not_a_zero_confidence_clear():
    import house_rules as H

    for raw in ('{"violated_rule_id": ""}', '{"violated_rule_id": "", "confidence": null}',
                '{"violated_rule_id": "", "confidence": true}',
                'text {"violated_rule_id": "", "reason": "fine"}'):
        with pytest.raises(H._HouseRulesClassifierError) as ei:
            H._house_rules_parse_verdict(raw)
        assert ei.value.cause == "bad_json", raw
    # An injected classifier handing back a non-number is anomalous output: escalate.
    for bad in (None, "high", float("nan"), float("inf")):
        g = H.HouseRulesGate.from_repo(classifier=lambda t, r, a, _b=bad: ("", _b, ""))
        d = g.classify("Hallo, wie geht es dir?")
        assert d.action == "escalate" and d.reason == "classifier_error", bad


def test_r2_sec2_the_bridge_only_waves_through_a_code_the_gate_no_longer_produces():
    """Bridge consistency. ``adapter._check_house_rules_or_fail`` (not owned by this
    cluster) returns None for an escalate with reason ``clear_low_confidence``. The
    gate must never produce that pair again, or the bridge runs a turn the chain
    calls escalated."""
    import house_rules as H

    src = (SHARED / "house_rules.py").read_text("utf-8")
    assert '"escalate", "", _REASON_CLEAR_LOWCONF,' not in src
    seen = set()
    for conf in (0.0, 0.2, 0.49, 0.5, 0.7, 0.84, 0.85, 0.99):
        for task in (_BENIGN[0], *_tier0_tasks(1)[:2]):
            d = _repo_gate(conf, []).classify(task)
            seen.add((d.action, d.reason))
    assert ("escalate", "clear_low_confidence") not in seen, seen
    assert {r for a, r in seen if a == "escalate"} <= {
        H._REASON_CLEAR_UNSURE_FLAGGED, H._REASON_OVER_CLASSIFIER_CAP}, seen


# ── R2-SEC-10: floor_only caused by an unreadable policy says so ─────────────

def test_r2_sec10_unreadable_tenant_policy_is_named_as_the_cause(tmp_path, monkeypatch, caplog):
    import house_rules as H
    import spawn_gates

    cfg = tmp_path / "tenant.corvin.yaml"
    monkeypatch.setattr(H, "_house_rules_tenant_config", lambda _tid: cfg)
    monkeypatch.delenv("CORVIN_HOUSE_RULES_CLASSIFIER_ORDER", raising=False)
    H._floor_unreadable_logged.clear()
    assert H._house_rules_cloud_egress_probe("t1") == (True, "")          # no file: no policy
    cfg.write_text("spec: [unclosed\n", encoding="utf-8")
    with caplog.at_level("ERROR"):
        assert H._house_rules_cloud_egress_probe("t1") == (False, H._FLOOR_CAUSE_POLICY_UNREADABLE)
    assert any("could not be evaluated" in r.getMessage() for r in caplog.records)
    assert H._house_rules_floor_only_cause("t1") == H._FLOOR_CAUSE_POLICY_UNREADABLE

    def _floor(task, rules, auth):
        raise H.HouseRulesFloorOnly("t1", H._house_rules_floor_only_cause("t1"))

    events: list = []
    gate = H.HouseRulesGate.from_repo(audit_writer=lambda e, s, d: events.append((e, dict(d))),
                                      classifier=_floor)
    d = gate.classify("Hallo")
    assert d.action == "escalate" and d.reason == "floor_only_policy_unreadable"
    monkeypatch.setattr(H.HouseRulesGate, "from_repo", classmethod(lambda cls, **_kw: gate))
    monkeypatch.setattr(spawn_gates, "_audit_writer_refusal", lambda _tag: None)
    out = spawn_gates.check_l44("Hallo", "t1", corvin_home=tmp_path)
    assert "tenant.corvin.yaml" in out and "could not be read" in out
    # A real egress denial keeps the plain operator-approval wording.
    cfg.write_text("spec:\n  egress:\n    enabled: true\n    default_action: deny\n", encoding="utf-8")
    assert H._house_rules_cloud_egress_probe("t1") == (False, H._FLOOR_CAUSE_EGRESS_DENIED)


def test_r2_sec10_throwaway_home_is_not_just_anything_under_tmp(monkeypatch, tmp_path):
    """A container whose HOME is itself under the temp directory: the real install
    there is not a throwaway instance."""
    import a2a_e2e_host as host

    fake_home = tmp_path / "home"
    (fake_home / ".corvin").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    under_tmp = any(str(tmp_path.resolve()).startswith(r) for r in ("/tmp", "/var/tmp"))
    for inside in (fake_home / ".corvin", fake_home / "work" / "inst", fake_home):
        monkeypatch.setenv("CORVIN_HOME", str(inside))
        assert host._is_throwaway_home() is False, inside
    scratch = tmp_path / "scratch" / "inst"
    scratch.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(scratch))
    import tempfile
    in_tmp = Path(tempfile.gettempdir()).resolve() in scratch.resolve().parents or under_tmp
    assert host._is_throwaway_home() is in_tmp
    # A checkout's own runtime root is never throwaway, even in a temp clone.
    repo = tmp_path / "clone"
    (repo / ".corvin").mkdir(parents=True)
    (repo / ".corvin_repo").write_text("", encoding="utf-8")
    monkeypatch.setenv("CORVIN_HOME", str(repo / ".corvin"))
    assert host._is_throwaway_home() is False
    monkeypatch.setenv("CORVIN_HOME", "/tmp")
    assert host._is_throwaway_home() is False


# ── R2-SEC-4: a deleted chain + deleted identity record is still "missing" ───

def test_r2_sec4_chain_and_identity_record_removed_still_refuses_boot(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _last_json(_run_isolated("""
        import json, shutil
        from pathlib import Path
        import paths
        from forge import security_events as se
        from core.compliance.corvin_compliance_reports import tripwire
        chain = Path(paths.tenant_audit_chain("_default"))
        chain.parent.mkdir(parents=True, exist_ok=True)
        fresh = tripwire.audit_chain_intact()
        for i in range(3):
            se.write_event(chain, "tool.created", severity="INFO", tool="t", run_id=str(i),
                           details={}, hash_chain=True)
        ok, problems = se.verify_chain(chain)[:2]
        keydir = se._mac_sentinel_path().parent
        witness = se._chain_witness_path(chain)
        had_witness = witness.exists()
        if not had_witness:           # a host that has not walked the chain yet
            witness.parent.mkdir(parents=True, exist_ok=True)
            witness.write_text("{}", encoding="utf-8")
        intact = tripwire.audit_chain_intact()
        # The refuted shape: remove the chain AND its identity record.
        chain.unlink()
        shutil.rmtree(keydir / "chain_ids", ignore_errors=True)
        missing = tripwire.audit_chain_intact()
        # Round 3 (R3-SEC-5): with the witness gone too, the tail record still names
        # this path (``path_sha``) - this used to boot with a warning.
        witness.unlink()
        host_only = tripwire.audit_chain_intact()
        # A different, never-used chain path on the same host is a fresh install.
        print(json.dumps({"fresh": [fresh.ok, fresh.detail], "verified": ok,
                          "intact": intact.ok, "missing": [missing.ok, missing.detail],
                          "host_only": [host_only.ok, host_only.detail]}))
    """, home))
    assert out["fresh"] == [True, "no chain yet (fresh install)"]
    assert out["verified"] is True and out["intact"] is True
    assert out["missing"][0] is False and "chain_missing" in out["missing"][1], out["missing"]
    assert "chain_witness" in out["missing"][1]
    assert out["host_only"][0] is False and "chain_missing" in out["host_only"][1], out["host_only"]
    assert "chain tail record" in out["host_only"][1]
    # Markers that cannot be tied to a path still only warn:
    # test_r3_tripwire_chain_evidence.py::test_legacy_tail_record_without_path_sha_only_warns.


# ── R2-SEC-6: the L35 host verdict is not fetched on the event loop ──────────

def _session(host_gate):
    from corvin_console.browser.session import BrowserSession

    return BrowserSession("s1", "_default", home=Path("/nonexistent"), host_gate=host_gate)


def test_r2_sec6_gate_runs_off_the_event_loop_once_per_host(monkeypatch):
    from corvin_console.browser import compliance

    monkeypatch.setattr(compliance, "_private_block_reason", lambda _h: None)
    main = threading.get_ident()
    calls: list[tuple[str, bool]] = []
    cache: dict[str, tuple[bool, str]] = {}

    def gate(host):
        calls.append((host, threading.get_ident() == main))      # an audit write happens here
        out = (host != "blocked.example.com", "policy")
        cache[host] = out
        return out

    gate.peek = cache.get
    sess = _session(gate)
    hosts = [f"h{i}.example.com" for i in range(50)]

    async def _drive():
        first = [await sess._egress_decision(f"https://{h}/a.js") for h in hosts]
        again = [await sess._egress_decision(f"https://{h}/b.css") for h in hosts]
        denied = await sess._egress_decision("https://blocked.example.com/x")
        denied2 = await sess._egress_decision("https://blocked.example.com/y")
        return first, again, denied, denied2

    first, again, denied, denied2 = asyncio.run(_drive())
    assert all(d.allowed for d in first + again)
    assert denied.allowed is False and denied2.allowed is False and "L35" in denied.reason
    assert not any(on_loop for _h, on_loop in calls), "the L35 gate (a chain write) ran on the event loop"
    # One gate call per host: no loss of audit coverage, no repeat within the TTL.
    assert sorted(h for h, _ in calls) == sorted(hosts + ["blocked.example.com"])


def test_r2_sec6_route_handler_awaits_the_verdict_before_continuing(monkeypatch):
    from corvin_console.browser import compliance

    monkeypatch.setattr(compliance, "_private_block_reason", lambda _h: None)
    order: list[str] = []

    def gate(host):
        order.append("gate:" + host)
        return (host == "ok.example.com", "policy")

    sess = _session(gate)       # a gate WITHOUT peek: every verdict goes off-loop

    class _Route:
        def __init__(self, url):
            self.request = type("R", (), {"url": url})()

        async def continue_(self):
            order.append("continue")

        async def abort(self):
            order.append("abort")

    async def _drive():
        await sess._route_egress(_Route("https://ok.example.com/a"))
        await sess._route_egress(_Route("https://evil.example.com/a"))
        await sess._route_egress(_Route("data:text/plain,x"))

    asyncio.run(_drive())
    assert order == ["gate:ok.example.com", "continue", "gate:evil.example.com", "abort", "continue"]


def test_r2_sec6_resolver_gate_exposes_a_side_effect_free_peek(tmp_path, monkeypatch):
    from corvin_console.routes import browser as rb

    home = tmp_path / "home"
    g = home / "tenants" / "_default" / "global"
    g.mkdir(parents=True)
    monkeypatch.setattr(rb._forge_paths, "corvin_home", lambda: home)
    (g / "tenant.corvin.yaml").write_text(
        "spec:\n  egress:\n    enabled: true\n    default_action: allow\n"
        "    forbidden_hosts:\n      - blocked.example.com\n")
    gate = rb._host_gate_resolver("_default")
    assert gate.peek("fine.example.org") is None
    assert gate("fine.example.org")[0] is True
    assert gate.peek("fine.example.org")[0] is True
    assert gate("blocked.example.com")[0] is False and gate.peek("blocked.example.com")[0] is False


# ── R2-SEC-7: a JID is a phone number; the safe form is lossy ────────────────

@pytest.mark.parametrize("subject", ["4915112345678@s.whatsapp.net", "4915112345678:7@s.whatsapp.net",
                                     "4915112345678@c.us", "+4915112345678", "jane.doe@example.org"])
def test_r2_sec7_personal_shaped_subject_is_fingerprinted_in_the_chain_record(subject):
    import hashlib

    from forge import security_events as se

    fp = hashlib.sha256(subject.encode()).hexdigest()[:8]
    kept, dropped = se.filter_audit_details(
        {"request_id": "er-1", "subject_id": subject, "requester": subject, "scope": "all"},
        event_type="erasure.requested")
    assert kept["subject_id"] == fp and kept["requester"] == fp, kept
    assert subject not in json.dumps(kept)
    assert set(kept["_pii_fingerprinted"]) == {"subject_id", "requester"} and not dropped


@pytest.mark.parametrize("subject", ["user_42", "er-2026-abc", "1502103856740302964",
                                     "120363041234567890@g.us", "discord:1502103856740302964"])
def test_r2_sec7_opaque_identifiers_are_kept_as_given(subject):
    from forge import security_events as se

    assert se.pseudonymise_identifier(subject) == subject
    kept, _ = se.filter_audit_details({"request_id": "er-1", "subject_id": subject},
                                      event_type="erasure.requested")
    assert kept["subject_id"] == subject


def test_r2_sec7_chain_and_trail_carry_the_pseudonym_end_to_end(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _last_json(_run_isolated("""
        import json
        from pathlib import Path
        import erasure_orchestrator as eo
        import paths
        chain = Path(paths.tenant_audit_chain("_default"))
        chain.parent.mkdir(parents=True, exist_ok=True)
        trail = Path(paths.tenant_global_dir("_default")) / "erasure"
        orch = eo.ErasureOrchestrator(tenant_id="_default", trail_dir=trail,
                                      audit_writer=eo.make_forge_audit_writer(chain))
        res = orch.execute(eo.ErasureRequest(subject_id="4915112345678@s.whatsapp.net",
                                             requester="+4915112345678"))
        files = sorted(trail.glob("*.json"))
        print(json.dumps({"chain": chain.read_text(), "trail": files[0].read_text(),
                          "n_trail": len(files), "request_id": res.request.request_id,
                          "in_memory_subject": res.request.subject_id}))
    """, home))
    import hashlib
    fp = hashlib.sha256(b"4915112345678@s.whatsapp.net").hexdigest()[:8]
    assert "4915112345678" not in out["chain"], "the phone number is in the append-only chain"
    assert "4915112345678" not in out["trail"], "the phone number is in the erasure trail file"
    assert fp in out["chain"] and out["n_trail"] == 1
    trail = json.loads(out["trail"])
    assert trail["request"]["subject_id"] == fp
    assert set(trail["pii_fingerprinted"]) == {"subject_id", "requester"}
    assert trail["request"]["request_id"] == out["request_id"]
    # The handlers still received the real id: only the persisted records are pseudonymised.
    assert out["in_memory_subject"] == "4915112345678@s.whatsapp.net"


def test_r2_sec7_safe_form_does_not_erase_another_subject():
    """``john.doe`` and ``john_doe`` share one directory form. Comparing that form
    under ``user`` / ``owner`` erased the other person's records."""
    import erasure_handlers as eh
    from session_state import safe_chat_key

    assert safe_chat_key("john.doe") == safe_chat_key("john_doe") == "john_doe"
    for key in ("user", "owner", "author", "user_id", "sender", "requester", "subject_id"):
        assert key in eh._SUBJECT_KEYS
        assert not eh._mentions_subject({key: "john_doe"}, "john.doe"), key
        assert not eh._mentions_subject({"rows": [{key: "john_doe"}]}, "john.doe"), key
        assert eh._mentions_subject({key: "john.doe"}, "john.doe"), key
    # The directory form still finds the records the adapter keys by it.
    jid = "4915112345678@s.whatsapp.net"
    for key in sorted(eh._SAFE_FORM_KEYS):
        assert key in eh._SUBJECT_KEYS
        # Round 3 (R3-SEC-6): only together with the raw id in the same record;
        # see test_r3_erasure_safe_form.py.
        assert eh._mentions_subject({key: safe_chat_key(jid), "jid": jid}, jid), key
        assert not eh._mentions_subject({key: safe_chat_key("4915100000000@s.whatsapp.net")}, jid)


# ── R2-SEC-9: secret shapes - more of them, fewer false alarms ───────────────

@pytest.mark.parametrize("value", [
    "github_" "pat_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz0123456789",
    "1234567" "89:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw-",
    "8123456" "789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw_",
    "MTIzNDU" "2Nzg5MDEyMzQ1Njc4.GabcdE.abcdefghijklmnopqrstuvwxyz_0123456789ab",
    "sk_live" "_abcdefghijklmnop1234",
    "rk_live" "_abcdefghijklmnop1234",
    "authorization: bearer abcdefghijklmnopqrstuvwxyz0123",
    "BEARER abcdefghijklmnopqrstuvwxyz0123",
    "key=sk-" "ant-api03-AbC_def-12345678901234567890",
    '"sk-pro' 'j-abcDEF_123-45678901234567890"',
])
def test_r2_sec9_more_secret_shapes_are_dropped(value):
    from forge import security_events as se

    assert se._AUDIT_SECRET_VALUE_RE.search(value), value
    kept, _ = se.filter_audit_details({"reason": f"failed with {value}"}, event_type="bridge.error")
    assert value not in json.dumps(kept)


@pytest.mark.parametrize("value", [
    "/tmp/sk-learn-model-cache-directory-v2/x",
    "pkg_sk-learn-model-cache-directory",
    "models.sk-learn-model-cache-directory-x",
    "task-manager-abcdefghijklmnopqrstuvw",
    "/home/u/hf_models_directory_for_cache_x",
    "2026-11-19T10:00:00:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "run 1234567890:short",
    "bearer of bad news",
    "core.skills.os_skills.model_selector.ModelSelector",
    "spn-12345678-worker",
    "sha256:" + "a" * 64,
    "sk_live_demo",
])
def test_r2_sec9_paths_and_slugs_are_not_mistaken_for_secrets(value):
    from forge import security_events as se

    assert not se._AUDIT_SECRET_VALUE_RE.search(value), value
    kept, dropped = se.filter_audit_details({"target": value}, event_type="path_gate.denied")
    assert kept.get("target") == value and not dropped
