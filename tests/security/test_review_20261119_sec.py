"""Regression tests for the security review of 2026-11-19 (SEC-1 .. SEC-17).

One section per finding. Every test names the failure it pins; each of them
fails on the code before the review round.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
FORGE = REPO / "corvin_operator" / "forge"

for _p in (SHARED, FORGE, REPO / "core" / "console", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _run_isolated(code: str, home: Path, *, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run *code* in a fresh interpreter with its own CORVIN_HOME + anchor key."""
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


# ── SEC-1: a missing L10 hook is a CRITICAL, correctly worded finding ────────

def test_sec1_l10_finding_names_the_real_marketplace_and_stays_non_fatal(monkeypatch, tmp_path):
    from core.compliance.corvin_compliance_reports import tripwire

    monkeypatch.setattr(tripwire, "_settings_candidates", lambda: [tmp_path / "none.json"])
    res = tripwire.l10_hook_registered()
    assert res.ok is False
    assert "claudeos-local" not in res.detail, "the remedy named a marketplace that does not exist"
    assert "voice@corvin-voice-local" in res.detail
    marketplace = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    assert marketplace["name"] == "corvin-voice-local"
    # Reporting-only (the boot is not refused) but recorded at CRITICAL.
    assert "l10_hook_registered" in tripwire.REPORTING_ONLY
    assert "l10_hook_registered" in tripwire.CRITICAL_FINDINGS


def test_sec1_l10_finding_is_recorded_at_critical_severity(monkeypatch):
    from core.compliance.corvin_compliance_reports import tripwire

    calls = []
    fake = SimpleNamespace(audit_event=lambda event, **kw: calls.append((event, kw)))
    monkeypatch.setattr(tripwire, "_audit_module", lambda: fake)
    tripwire._record_finding(tripwire.TripwireResult("l10_hook_registered", False, "x"))
    assert calls and calls[0][0] == "compliance.tripwire_finding"
    assert calls[0][1].get("severity") == "CRITICAL"


# ── SEC-8: an anchored chain that is gone is chain_missing, not a fresh install

def test_sec8_missing_chain_with_anchored_identity_refuses_boot(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    code = """
        import json, os, sys
        from pathlib import Path
        import audit
        from core.compliance.corvin_compliance_reports import tripwire
        fresh = tripwire.audit_chain_intact()
        audit.audit_event("consent.granted", details={"mode": "durable"})
        chain = Path(audit.audit_path())
        assert chain.is_file() and chain.stat().st_size > 0, chain
        present = tripwire.audit_chain_intact()
        os.unlink(chain)
        tripwire._verify_cache.clear()
        gone = tripwire.audit_chain_intact()
        chain.write_bytes(b"")
        empty = tripwire.audit_chain_intact()
        print(json.dumps({"fresh": [fresh.ok, fresh.detail], "present": [present.ok, present.detail],
                          "gone": [gone.ok, gone.detail], "empty": [empty.ok, empty.detail]}))
    """
    r = _run_isolated(code, home)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["fresh"][0] is True and "fresh install" in out["fresh"][1]
    assert out["present"][0] is True, out["present"]
    assert out["gone"][0] is False and "chain_missing" in out["gone"][1], out["gone"]
    assert out["empty"][0] is False and "chain_missing" in out["empty"][1], out["empty"]


# ── SEC-5: channel ids with "@" / "+" are erasable; directory form matches ───

def test_sec5_subject_id_admits_at_and_plus_but_not_paths():
    import erasure_orchestrator as eo

    for sid in ("4915112345678@s.whatsapp.net", "+4915112345678", "user@example.com"):
        assert eo.validate_subject_id(sid) == sid
    for bad in ("", "a b", "../x", "a/b", "a\\b", "x\n", "a" * 129):
        with pytest.raises(ValueError):
            eo.validate_subject_id(bad)


def test_sec5_mentions_subject_matches_the_adapter_directory_form():
    import erasure_handlers as eh
    from session_state import safe_chat_key

    jid = "4915112345678@s.whatsapp.net"
    # Round 2 (R2-SEC-7): the directory form is compared only under a key that
    # demonstrably stores it; see test_review_20261119_round2.py for the
    # over-erasure case this used to allow under EVERY identity key.
    key = "chat_key"
    assert safe_chat_key(jid) != jid
    assert eh._mentions_subject({key: jid}, jid)
    # Round 3 (R3-SEC-6): the directory form counts only when the record also
    # carries the raw id; see test_r3_erasure_safe_form.py.
    assert eh._mentions_subject({"nested": [{key: safe_chat_key(jid), "jid": jid}]}, jid), \
        "a record keyed by the directory form survived the erasure"
    assert not eh._mentions_subject({key: "4915100000000_s_whatsapp_net"}, jid)
    assert not eh._mentions_subject({key: "someone_else"}, jid)
    # An already-alphanumeric id has exactly one form: no widening.
    assert eh._subject_value_forms("user42") == frozenset({"user42"})


# ── SEC-7: current credential shapes are caught by the value scan ────────────

@pytest.mark.parametrize("value", [
    "sk-ant-" "api03-AbC_def-12345678901234567890",
    "sk-proj" "-abcDEF_123-45678901234567890",
    "sk-abcdefghijklmnopqrstuvwx",
    "xai-abcdefghijklmnopqrstuvwxyz",
    "AIzaSyA" "1234567890abcdefghijklmnopqrstuv",
    "hf_abcdefghijklmnopqrstuvwxyz",
    "glpat-a" "bcdefghij_klmnopqrst",
    "https://deploy:hunter2@git.example.com/repo.git",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
])
def test_sec7_secret_shapes_are_dropped_from_audit_details(value):
    from forge import security_events as se

    assert se._AUDIT_SECRET_VALUE_RE.search(value), value
    assert se._audit_value_leaks(value)
    kept = se.filter_audit_details({"reason": f"failed with {value}"}, event_type="bridge.error")
    assert value not in json.dumps(kept)


@pytest.mark.parametrize("value", [
    "task-manager-plugin-builder-thing", "risk-assessment-of-something-long",
    "https://user@host.example/path", "4915112345678@s.whatsapp.net",
    "http://127.0.0.1:8765/console", "bearer token missing", "shf_notatoken",
])
def test_sec7_ordinary_identifiers_are_not_mistaken_for_secrets(value):
    from forge import security_events as se

    assert not se._AUDIT_SECRET_VALUE_RE.search(value), value


# ── SEC-9: a console record lost to a tenant mismatch is an ERROR line ───────

def test_sec9_console_audit_loss_is_logged_loudly(monkeypatch, caplog):
    from corvin_console import audit as console_audit

    def _refuse(*_a, **_kw):
        raise console_audit._security_events.AuditTenantMismatch("tenant differs")

    monkeypatch.setattr(console_audit._security_events, "write_event", _refuse)
    with caplog.at_level(logging.ERROR, logger="corvin.console.audit"):
        console_audit.system_event(tenant_id="tenant_b", event="models.catalog_refreshed",
                                   details={"count": 1})
    lost = [r for r in caplog.records if "LOST" in r.getMessage()]
    assert lost, "a refused console record left no trace"
    msg = lost[0].getMessage()
    assert lost[0].levelno >= logging.ERROR
    assert "models.catalog_refreshed" in msg and "tenant_b" in msg and "AuditTenantMismatch" in msg
    with pytest.raises(console_audit.AuditWriteFailed):
        console_audit._emit("models.catalog_refreshed", tenant_id="tenant_b",
                            details={"count": 1}, strict=True)


# ── SEC-10: an escalate verdict blocks, whatever its reason code ─────────────

# Round 2 (R2-SEC-2): the invariant that survives is "an ESCALATE verdict blocks,
# whatever its reason code" - check_l44 never turns a decision the gate recorded
# as ``house_rules.escalated`` into a turn that runs. What changed is which
# verdict a doubtful clear GETS, and that is decided once, in
# ``HouseRulesGate.classify`` (an unsure clear with no Tier-0 hit is an ALLOW
# with its own reason code). Those cases run through the real gate in
# test_review_20261119_round2.py.
@pytest.mark.parametrize("reason", ["clear_low_confidence", "clear_unsure_tier0_flagged",
                                    "prompt_over_classifier_cap", "classifier_error",
                                    "floor_only_policy_unreadable",
                                    "classifier_violation_low_confidence"])
def test_sec10_escalate_always_blocks(monkeypatch, tmp_path, reason):
    import house_rules
    import spawn_gates

    class _Gate:
        def classify(self, task, **_kw):
            return house_rules.HouseRulesDecision("escalate", "", reason, 0.4)

    monkeypatch.setattr(house_rules.HouseRulesGate, "from_repo",
                        classmethod(lambda cls, **_kw: _Gate()))
    monkeypatch.setattr(house_rules, "_house_rules_track_degradation", lambda **_kw: None)
    monkeypatch.setattr(spawn_gates, "_audit_writer_refusal", lambda _tag: None)
    out = spawn_gates.check_l44("please analyse logs.csv", "_default", corvin_home=tmp_path)
    assert isinstance(out, str) and out.startswith("[house-rules]"), \
        f"escalate/{reason} let the turn run"


def test_sec10_allow_still_passes(monkeypatch, tmp_path):
    import house_rules
    import spawn_gates

    class _Gate:
        def classify(self, task, **_kw):
            return house_rules.HouseRulesDecision("allow", "", "classifier_cleared", 0.97)

    monkeypatch.setattr(house_rules.HouseRulesGate, "from_repo",
                        classmethod(lambda cls, **_kw: _Gate()))
    monkeypatch.setattr(spawn_gates, "_audit_writer_refusal", lambda _tag: None)
    assert spawn_gates.check_l44("hello", "_default", corvin_home=tmp_path) is None


# ── SEC-11: a class marker only raises; policy callers fail closed ───────────

def _pii_text() -> str:
    import data_classification as dc

    for cand in ("Patient Max Mustermann, born 1980-02-01, email max.mustermann@example.com, "
                 "phone +49 151 12345678, IBAN DE89370400440532013000",
                 "Kundin Erika Musterfrau, erika@example.com, Telefon 0151 2345678"):
        if dc.classify_task(cand) is dc.DataClassification.CONFIDENTIAL:
            return cand
    pytest.skip("zone classifier does not flag the probe texts as personal_data")


def test_sec11_marker_cannot_lower_the_class():
    import data_classification as dc

    C = dc.DataClassification
    pii = _pii_text()
    assert dc.classify_task("[class:public] " + pii) is C.CONFIDENTIAL, \
        "[class:public] lowered personal data to PUBLIC"
    assert dc.classify_task("[class:internal] " + pii) is C.CONFIDENTIAL
    assert dc.classify_task("[class:secret] " + pii) is C.SECRET
    assert dc.classify_task("[class:internal] list the open tickets") is C.INTERNAL
    assert dc.classify_task("list the open tickets") is C.PUBLIC


def test_sec11_honor_marker_false_ignores_a_foreign_label():
    import data_classification as dc

    C = dc.DataClassification
    assert dc.classify_task("[class:confidential] hello", honor_marker=False) is C.PUBLIC
    assert dc.classify_task("[class:public] " + _pii_text(), honor_marker=False) is C.CONFIDENTIAL


def test_sec11_classifier_failure_is_fail_closed_for_policy_callers(monkeypatch):
    import compliance_zone_classifier as czc
    import data_classification as dc

    def _boom(*_a, **_kw):
        raise RuntimeError("zone classifier down")

    monkeypatch.setattr(czc, "classify_zone", _boom)
    assert dc.classify_task("hello there") is dc.DataClassification.PUBLIC  # legacy: no policy
    with pytest.raises(dc.ClassifierError):
        dc.classify_task("hello there", fail_closed=True)


def test_sec11_check_l34_passes_fail_closed_to_the_classifier():
    src = (SHARED / "spawn_gates.py").read_text("utf-8")
    assert src.count("fail_closed=True") >= 2, "the policy-enforcing call sites lost fail_closed"


# ── SEC-12: no erasure.requested record, no purge ────────────────────────────

class _Handler:
    layer_id = "L-test"

    def __init__(self):
        self.calls = 0

    def purge(self, subject_id, request_id):
        import erasure_orchestrator as eo
        self.calls += 1
        return eo.ErasureLayerResult(layer_id=self.layer_id, status=eo.LayerStatus.SKIPPED,
                                     count=0, reason="nothing", code=eo.ReasonCode.STORE_EMPTY.value)


def test_sec12_failed_request_record_refuses_the_erasure(tmp_path):
    import erasure_orchestrator as eo

    def _writer(event_type, severity, details):
        if event_type == "erasure.requested":
            raise OSError("disk full")

    h = _Handler()
    orch = eo.ErasureOrchestrator(tenant_id="_default", trail_dir=tmp_path / "trail",
                                  audit_writer=_writer)
    orch.register_handler(h)
    with pytest.raises(eo.ErasureAuditError):
        orch.execute(eo.ErasureRequest(subject_id="user42", requester="dpo"))
    assert h.calls == 0, "a handler purged data although erasure.requested did not commit"


def test_sec12_strict_path_of_the_production_writer_is_used(tmp_path):
    import erasure_orchestrator as eo

    seen = []

    def _writer(event_type, severity, details):
        seen.append(("plain", event_type))

    def _strict(event_type, severity, details):
        seen.append(("strict", event_type))
        raise RuntimeError("chain refused")

    _writer.strict = _strict
    h = _Handler()
    orch = eo.ErasureOrchestrator(tenant_id="_default", trail_dir=tmp_path / "trail",
                                  audit_writer=_writer)
    orch.register_handler(h)
    with pytest.raises(eo.ErasureAuditError):
        orch.execute(eo.ErasureRequest(subject_id="user42", requester="dpo"))
    assert ("strict", "erasure.requested") in seen and h.calls == 0


def test_sec12_production_writer_commits_the_request_first(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    code = """
        import json
        from pathlib import Path
        import erasure_orchestrator as eo
        import paths
        chain = Path(paths.tenant_audit_chain("_default"))
        writer = eo.make_forge_audit_writer(chain)
        assert callable(getattr(writer, "strict", None))
        order = []
        class H:
            layer_id = "L-test"
            def purge(self, subject_id, request_id):
                order.append([json.loads(l)["event_type"] for l in chain.read_text().splitlines() if l.strip()])
                return eo.ErasureLayerResult(layer_id="L-test", status=eo.LayerStatus.SKIPPED, count=0,
                                             reason="nothing", code=eo.ReasonCode.STORE_EMPTY.value)
        orch = eo.ErasureOrchestrator(tenant_id="_default", trail_dir=Path(%r), audit_writer=writer)
        orch.register_handler(H())
        orch.execute(eo.ErasureRequest(subject_id="alice@example.com", requester="dpo"))
        recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        req = [r for r in recs if r["event_type"] == "erasure.requested"][0]
        print(json.dumps({"at_purge": order[0], "subject": req["details"].get("subject_id")}))
    """ % str(tmp_path / "trail")
    r = _run_isolated(code, home)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "erasure.requested" in out["at_purge"], "the handler ran before the request was on the chain"
    # An e-mail-shaped subject reaches the chain as its fingerprint (pseudonym key).
    assert out["subject"] and "@" not in out["subject"], out["subject"]


# ── SEC-15: consent records land in the chain of the tenant they belong to ───

def test_sec15_consent_audit_follows_the_explicit_tenant(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    code = """
        import json
        from pathlib import Path
        import consent, paths
        consent.grant("telegram", "555", "alice", ttl_s=None, tenant_id="tenant_b")
        consent.revoke("telegram", "555", "alice", tenant_id="tenant_b")
        consent.admit_observer_drop("telegram", "555", "bob", text_len=3, tenant_id="tenant_b")
        def evs(tid):
            p = Path(paths.tenant_audit_chain(tid))
            if not p.is_file():
                return []
            return [json.loads(l)["event_type"] for l in p.read_text().splitlines() if l.strip()]
        print(json.dumps({"b": evs("tenant_b"), "d": evs("_default")}))
    """
    r = _run_isolated(code, home)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for ev in ("consent.granted", "consent.revoked", "consent.observer_dropped"):
        assert ev in out["b"], (ev, out)
        assert ev not in out["d"], f"{ev} of tenant_b was written into the process tenant's chain"


# ── SEC-16: only 0 <= age < max trusts the epoch ─────────────────────────────

def _bridge_consent():
    # ``import consent`` can resolve to core/compliance/consent.py depending on
    # sys.path order; load the bridge module by file.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_bridge_consent_under_test", SHARED / "consent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sec16_future_or_garbage_epoch_is_not_trusted(monkeypatch):
    consent = _bridge_consent()

    monkeypatch.setattr(consent, "is_granted", lambda *a, **kw: (False, "no-entry"))
    monkeypatch.setattr(consent, "_audit", lambda *a, **kw: None)
    now = time.time()
    assert consent.is_granted_with_epoch("t", "c", "u", consent_epoch=now - 1) == (True, "epoch_ok")
    for bad in (now + 3600, now + 10 ** 9, float("nan"), float("inf"), -float("inf"),
                "soon", True, now - 31):
        granted, reason = consent.is_granted_with_epoch("t", "c", "u", consent_epoch=bad)
        assert granted is False and reason == "no-entry", (bad, granted, reason)


# ── SEC-17: no vacuous boot check, no process-wide L44 replacement ───────────

def test_sec17_bootstrap_check_verifies_the_tenant_chain(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    code = """
        import json
        from pathlib import Path
        import audit, paths
        from core.pipeline import bootstrap as bs
        fresh = bs.verify_audit_durability("_default")
        audit.audit_event("consent.granted", details={"mode": "durable"})
        chain = Path(paths.tenant_audit_chain("_default"))
        assert Path(audit.audit_path()) == chain
        ok = bs.verify_audit_durability("_default")
        lines = chain.read_text().splitlines()
        rec = json.loads(lines[0]); rec["event_type"] = "consent.revoked"
        chain.write_text(json.dumps(rec) + "\\n" + "\\n".join(lines[1:]) + ("\\n" if lines[1:] else ""))
        try:
            tampered = list(bs.verify_audit_durability("_default"))
        except SystemExit as e:
            tampered = ["exit", e.code]
        print(json.dumps({"fresh": list(fresh), "ok": list(ok), "tampered": tampered}))
    """
    r = _run_isolated(code, home)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["fresh"] == [True, "new_audit_chain"]
    assert out["ok"] == [True, "verified (0 problems)"], out["ok"]
    # A tampered record is no longer "verified": either the boot is refused or
    # the message says, in words, that the chain did not verify clean.
    assert out["tampered"][0] == "exit" or "NOT verified" in str(out["tampered"][1]), out["tampered"]


def test_sec17_e2e_host_has_no_unconditional_l44_off_switch(monkeypatch, tmp_path):
    src = (SHARED / "a2a_e2e_host.py").read_text("utf-8")
    assert "spawn_gates.check_l44 = lambda" not in src, "process-wide L44 no-op is back"
    assert "check_l44_floor" in src
    import a2a_e2e_host as host

    monkeypatch.delenv("CORVIN_HOME", raising=False)
    assert host._is_throwaway_home() is False
    monkeypatch.setenv("CORVIN_HOME", str(Path.home() / ".corvin"))
    assert host._is_throwaway_home() is False
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "inst"))
    import tempfile
    inside_tmp = Path(tempfile.gettempdir()).resolve() in (tmp_path / "inst").resolve().parents
    home_in_tmp = Path.home().resolve() in (tmp_path / "inst").resolve().parents
    assert host._is_throwaway_home() is (inside_tmp and not home_in_tmp)


# ── SEC-6 / SEC-13: browser agent gates, empty allowlist, L35 per host ───────

def test_sec6_empty_allowed_hosts_stays_deny_all(monkeypatch, tmp_path):
    from corvin_console.browser import compliance
    from corvin_console.routes import browser as rb

    cfg_dir = tmp_path / "global"
    cfg_dir.mkdir()
    monkeypatch.setattr(rb._forge_paths, "tenant_global_dir", lambda _tid: cfg_dir)
    assert rb._allowlist_resolver("_default") == (None, None)          # no file: no policy
    (cfg_dir / "tenant.corvin.yaml").write_text("spec:\n  browser:\n    allowed_hosts: []\n")
    allow, forbid = rb._allowlist_resolver("_default")
    assert allow == [] and forbid is None, "allowed_hosts: [] became 'no policy' (allow all)"
    assert compliance.check_egress("https://example.com/", allowlist=allow, forbidden=forbid).allowed is False
    (cfg_dir / "tenant.corvin.yaml").write_text("spec:\n  browser: {}\n")
    assert rb._allowlist_resolver("_default") == (None, None)
    (cfg_dir / "tenant.corvin.yaml").write_text("spec:\n  browser:\n    allowed_hosts: example.com\n")
    with pytest.raises(ValueError):
        rb._allowlist_resolver("_default")


def test_sec13_l35_host_gate_can_only_narrow(monkeypatch):
    from corvin_console.browser import compliance

    monkeypatch.setattr(compliance, "_private_block_reason", lambda _h: None)
    url = "https://blocked.example.com/x"
    assert compliance.check_egress(url, allowlist=None, forbidden=None).allowed is True
    deny = compliance.check_egress(url, allowlist=None, forbidden=None,
                                   host_gate=lambda h: (False, "forbidden_hosts"))
    assert deny.allowed is False and "L35" in deny.reason
    # A browser allowlist entry does not override an L35 deny.
    assert compliance.check_egress(url, allowlist=["example.com"], forbidden=None,
                                   host_gate=lambda h: (False, "default_deny")).allowed is False

    def _boom(_h):
        raise RuntimeError("gate down")

    assert compliance.check_egress(url, allowlist=None, forbidden=None, host_gate=_boom).allowed is False
    assert compliance.check_egress(url, allowlist=None, forbidden=None,
                                   host_gate=lambda h: (True, "allowed_hosts")).allowed is True


def test_sec13_session_passes_the_gate_to_every_egress_check():
    """Every egress decision of the session goes through the two helpers that
    consult the tenant L35 gate (round 2: ``_egress_decision`` for async callers,
    ``_egress_decision_cached`` + the gate for the one synchronous callback)."""
    import ast

    src = (REPO / "core/console/corvin_console/browser/session.py").read_text("utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BrowserSession")
    direct = {}
    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Attribute) and node.attr == "check_egress"
                    and isinstance(node.value, ast.Name) and node.value.id == "_cmp"):
                direct.setdefault(fn.name, 0)
                direct[fn.name] += 1
    assert set(direct) == {"_egress_decision_cached", "_egress_decision", "_route_web_socket"}, direct
    assert "host_gate=self._host_gate" in src and "_egress_decision(" in src


def test_sec13_host_gate_resolver_builds_from_the_tenant_egress_policy(tmp_path, monkeypatch):
    from corvin_console.routes import browser as rb

    home = tmp_path / "home"
    g = home / "tenants" / "_default" / "global"
    g.mkdir(parents=True)
    monkeypatch.setattr(rb._forge_paths, "corvin_home", lambda: home)
    assert rb._host_gate_resolver("_default") is None            # no tenant config: no policy
    (g / "tenant.corvin.yaml").write_text(
        "spec:\n  egress:\n    enabled: true\n    default_action: allow\n"
        "    forbidden_hosts:\n      - blocked.example.com\n")
    gate = rb._host_gate_resolver("_default")
    assert callable(gate)
    assert gate("blocked.example.com")[0] is False
    assert gate("fine.example.org")[0] is True
    (g / "tenant.corvin.yaml").write_text("spec: [unclosed\n")
    broken = rb._host_gate_resolver("_default")
    assert callable(broken) and broken("fine.example.org")[0] is False, \
        "an unparseable egress policy left the browser ungated"


class _FakeMgr:
    def __init__(self):
        self.started = []

    def session(self, tenant_id, sid, *, owner_fingerprint=""):
        if sid != "s1":
            raise KeyError("no such session")
        return object()

    def start_agent(self, tenant_id, sid, task, **kw):
        self.started.append((tenant_id, sid, task))
        return True


@pytest.fixture()
def browser_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console import _spawn_gates
    from corvin_console.browser.internal_auth import require_csrf_or_token
    from corvin_console.routes import browser as rb

    mgr = _FakeMgr()
    monkeypatch.setattr(rb, "_mgr", lambda: mgr)
    monkeypatch.setattr(rb.console_audit, "action_performed", lambda **_kw: None)
    monkeypatch.setattr(rb.console_audit, "action_denied", lambda **_kw: None)
    app = FastAPI()
    app.include_router(rb.router, prefix="/v1/console")
    rec = SimpleNamespace(tenant_id="_default", sid_fingerprint="fp1", is_internal_tool=False)
    app.dependency_overrides[require_csrf_or_token] = lambda: rec
    gate_calls = []
    return SimpleNamespace(client=TestClient(app), mgr=mgr, gates=_spawn_gates,
                           gate_calls=gate_calls, monkeypatch=monkeypatch)


def _agent_path(client) -> str:
    return "/v1/console/browser/s1/agent"


def test_sec6_browser_agent_runs_the_pre_spawn_gates_and_refuses(browser_client):
    bc = browser_client
    seen = []

    def _refuse(prompt, **kw):
        seen.append((prompt, kw))
        return "[house-rules] This request is not permitted."

    bc.monkeypatch.setattr(bc.gates, "check_console_spawn_or_refusal", _refuse)
    r = bc.client.post(_agent_path(bc.client), json={"task": "do the forbidden thing"})
    assert r.status_code == 403, r.text
    assert "[house-rules]" in r.json()["detail"]
    assert bc.mgr.started == [], "the agent started although a pre-spawn gate refused"
    assert seen and seen[0][0] == "do the forbidden thing"
    assert seen[0][1]["tenant_id"] == "_default" and seen[0][1]["channel"] == "web"


def test_sec6_browser_agent_gate_error_is_a_refusal(browser_client):
    bc = browser_client

    def _boom(prompt, **kw):
        raise RuntimeError("gate import failed")

    bc.monkeypatch.setattr(bc.gates, "check_console_spawn_or_refusal", _boom)
    r = bc.client.post(_agent_path(bc.client), json={"task": "anything"})
    assert r.status_code == 403, r.text
    assert bc.mgr.started == []


def test_sec6_browser_agent_starts_when_the_gates_allow(browser_client):
    bc = browser_client
    bc.monkeypatch.setattr(bc.gates, "check_console_spawn_or_refusal", lambda prompt, **kw: None)
    r = bc.client.post(_agent_path(bc.client), json={"task": "open the docs"})
    assert r.status_code == 200, r.text
    assert bc.mgr.started == [("_default", "s1", "open the docs")]
