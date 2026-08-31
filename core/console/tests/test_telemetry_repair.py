"""ADR-0179 — telemetry-driven repair: error signatures, PII scrubbing, the
reproduction proof gate (red→green→suite), the diagnosis synthesizer, and the
opt-in telemetry channel."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from corvin_console.aco import error_signature as ES  # type: ignore
from corvin_console.aco import diagnosis_synth as DS  # type: ignore
from corvin_console.aco import telemetry as TEL  # type: ignore
from corvin_console.aco import reproduction as RP  # type: ignore
from corvin_console.aco.maintenance_loop import Patch, PatchEdit  # type: ignore


# ── error_signature ─────────────────────────────────────────────────────────────

def test_scrub_removes_pii_shapes():
    s = ES.scrub("user alice@example.com id 123456789012345 at /home/bob/secret token=abcdef")
    assert "@example.com" not in s and "alice" not in s
    assert "/home/bob" not in s
    assert "<email>" in s and "<path>" in s
    assert "abcdef" not in s or "redacted" in s


def test_to_repo_path_maps_installed_and_intree():
    assert ES.to_repo_path("/home/u/.venv/lib/python3.12/site-packages/corvin_console/aco/x.py") \
        == "core/console/corvin_console/aco/x.py"
    assert ES.to_repo_path("/srv/app/core/gateway/corvin_gateway/app.py") \
        == "core/gateway/corvin_gateway/app.py"
    assert ES.to_repo_path("/usr/lib/python3/dist-packages/numpy/core.py") is None


def test_traceback_parse_is_localized_and_stable():
    tb = (
        'Traceback (most recent call last):\n'
        '  File "/x/site-packages/corvin_console/aco/foo.py", line 42, in do_it\n'
        '    raise ValueError("boom 17 for user dave@x.com")\n'
        'ValueError: boom 17 for user dave@x.com\n'
    )
    sigs = ES.parse_tracebacks(tb)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.localized and s.top_repo_file == "core/console/corvin_console/aco/foo.py"
    assert s.exc_type == "ValueError" and s.func == "do_it"
    assert "dave@x.com" not in s.message_template and "<email>" in s.message_template
    # stable: line number / values don't change the signature
    tb2 = tb.replace("line 42", "line 99").replace("17", "23").replace("dave@x.com", "eve@y.com")
    assert ES.parse_tracebacks(tb2)[0].signature == s.signature


def test_bare_error_line_not_localizable():
    sigs = ES.parse_error_lines("2026-01-01 ERROR something failed badly\nINFO ok\n")
    assert sigs and all(not s.localized for s in sigs)


# ── reproduction proof gate (REAL git + pytest) ─────────────────────────────────

def _repo_with_bug(tmp_path):
    repo = tmp_path / "repo"; (repo / "tests").mkdir(parents=True)
    def g(*a): subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    g("init", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    # buggy: add() subtracts. sub() works and an existing test guards it.
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n\n"
                                 "def sub(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_sub.py").write_text(
        "from app import sub\n\ndef test_sub():\n    assert sub(5, 2) == 3\n", encoding="utf-8")
    g("add", "-A"); g("commit", "-m", "init")
    return repo


_GOOD_TEST = ("from app import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
_FIX = "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n"


def _runner(repo):
    def run(cmd):
        p = subprocess.run([sys.executable, "-m", "pytest", *cmd, "-q"],
                           cwd=str(repo), capture_output=True, text=True, timeout=300)
        return p.returncode, (p.stdout + p.stderr)[-300:]   # raw pytest rc
    return (lambda targets: run(targets)), (lambda: run([]))


def test_repro_gate_proves_real_fix(tmp_path):
    repo = _repo_with_bug(tmp_path)
    patch = Patch(diagnosis_id="d1", summary="fix add", risk_class="engine_generated",
                  edits=[PatchEdit("tests/test_add.py", _GOOD_TEST),
                         PatchEdit("app.py", _FIX)])
    tr, fr = _runner(repo)
    res = RP.reproduction_gate(repo, patch, test_runner=tr, full_runner=fr)
    assert res.proven and res.stage_a_red and res.stage_b_green and res.stage_c_green
    # gate leaves the tree clean
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip() == ""


def test_repro_gate_rejects_test_that_does_not_reproduce(tmp_path):
    repo = _repo_with_bug(tmp_path)
    trivial = "def test_trivial():\n    assert True\n"
    patch = Patch("d2", "x", "engine_generated",
                  edits=[PatchEdit("tests/test_t.py", trivial), PatchEdit("app.py", _FIX)])
    tr, fr = _runner(repo)
    res = RP.reproduction_gate(repo, patch, test_runner=tr, full_runner=fr)
    assert not res.proven and not res.stage_a_red and "does not reproduce" in res.detail


def test_repro_gate_rejects_fix_that_does_not_work(tmp_path):
    repo = _repo_with_bug(tmp_path)
    bad_fix = "def add(a, b):\n    return a * b\n\ndef sub(a, b):\n    return a - b\n"
    patch = Patch("d3", "x", "engine_generated",
                  edits=[PatchEdit("tests/test_add.py", _GOOD_TEST), PatchEdit("app.py", bad_fix)])
    tr, fr = _runner(repo)
    res = RP.reproduction_gate(repo, patch, test_runner=tr, full_runner=fr)
    assert not res.proven and res.stage_a_red and not res.stage_b_green


def test_repro_gate_rejects_fix_that_breaks_suite(tmp_path):
    repo = _repo_with_bug(tmp_path)
    # fixes add() but drops sub() → the existing test_sub regression test breaks.
    breaking = "def add(a, b):\n    return a + b\n"
    patch = Patch("d4", "x", "engine_generated",
                  edits=[PatchEdit("tests/test_add.py", _GOOD_TEST), PatchEdit("app.py", breaking)])
    tr, fr = _runner(repo)
    res = RP.reproduction_gate(repo, patch, test_runner=tr, full_runner=fr)
    assert not res.proven and res.stage_a_red and res.stage_b_green and not res.stage_c_green


def test_repro_gate_rejects_new_feature_smuggled_as_fix(tmp_path):
    # Test imports a not-yet-existing symbol → pytest rc=2 (collection error) on
    # unpatched code. That is NOT a genuine reproduction; must be rejected even
    # though the "fix" adds the symbol and the test then passes. (review CRITICAL)
    repo = _repo_with_bug(tmp_path)
    feat_test = "from app import feature\n\ndef test_feature():\n    assert feature() == 1\n"
    fix = ("def add(a, b):\n    return a - b\n\ndef sub(a, b):\n    return a - b\n\n"
           "def feature():\n    return 1\n")
    patch = Patch("d6", "add feature", "engine_generated",
                  edits=[PatchEdit("tests/test_feat.py", feat_test), PatchEdit("app.py", fix)])
    tr, fr = _runner(repo)
    res = RP.reproduction_gate(repo, patch, test_runner=tr, full_runner=fr)
    assert not res.proven and not res.stage_a_red and "did not genuinely FAIL" in res.detail


def test_repro_gate_requires_a_test(tmp_path):
    repo = _repo_with_bug(tmp_path)
    patch = Patch("d5", "x", "engine_generated", edits=[PatchEdit("app.py", _FIX)])
    tr, fr = _runner(repo)
    res = RP.reproduction_gate(repo, patch, test_runner=tr, full_runner=fr)
    assert not res.proven and "no regression test" in res.detail


def test_repro_gate_rejects_absolute_path(tmp_path):
    # SH-3: an absolute edit path must be refused before any write; proven=False.
    repo = _repo_with_bug(tmp_path)
    evil = tmp_path / "pwned"
    patch = Patch("dabs", "x", "engine_generated",
                  edits=[PatchEdit("tests/test_add.py", _GOOD_TEST),
                         PatchEdit(str(evil), "PWNED\n")])
    tr, fr = _runner(repo)
    res = RP.reproduction_gate(repo, patch, test_runner=tr, full_runner=fr)
    assert not res.proven and "unsafe patch path" in res.detail
    assert not evil.exists()


def test_repro_gate_rejects_dotdot_traversal(tmp_path):
    # SH-3: a `..` traversal must be refused, even resolving under a temp parent.
    repo = _repo_with_bug(tmp_path)
    patch = Patch("ddd", "x", "engine_generated",
                  edits=[PatchEdit("tests/test_add.py", _GOOD_TEST),
                         PatchEdit("../escape.txt", "PWNED\n")])
    tr, fr = _runner(repo)
    res = RP.reproduction_gate(repo, patch, test_runner=tr, full_runner=fr)
    assert not res.proven and "unsafe patch path" in res.detail
    assert not (repo.parent / "escape.txt").exists()


def test_apply_helper_raises_on_absolute(tmp_path):
    # SH-3: the low-level _apply containment assert rejects an absolute path.
    repo = tmp_path / "repo"; repo.mkdir()
    with pytest.raises(ValueError):
        RP._apply(repo, [PatchEdit("/etc/evil", "x")])


# ── diagnosis synthesizer ───────────────────────────────────────────────────────

def _tb(file_pkg="corvin_console/aco/foo.py", exc="ValueError", func="do_it", val=1):
    return (f'Traceback (most recent call last):\n'
            f'  File "/x/site-packages/{file_pkg}", line {val}, in {func}\n'
            f'    raise {exc}("e{val}")\n{exc}: e{val}\n')


def test_synth_queues_recurring_localized_only(tmp_path):
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    # same signature 3×, plus a one-off different signature
    (home / "logs" / "corvin.log").write_text(
        _tb(val=1) + _tb(val=2) + _tb(val=3) + _tb(func="other", val=9), encoding="utf-8")
    res = DS.synthesize(home, min_occurrences=3)
    assert len(res["queued"]) == 1                  # the recurring one
    pend = list((home / "aco" / "diagnoses" / "pending").glob("*.json"))
    assert len(pend) == 1
    diag = json.loads(pend[0].read_text())
    assert diag["requires_repro_test"] is True
    assert diag["file"] == "core/console/corvin_console/aco/foo.py"
    assert diag["repro"]["occurrences"] == 3
    # the one-off went to reports, not pending
    assert len(res["reported"]) >= 1


def test_synth_dedups_known(tmp_path):
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    (home / "logs" / "corvin.log").write_text(_tb(val=1) + _tb(val=2) + _tb(val=3))
    DS.synthesize(home, min_occurrences=3)
    res2 = DS.synthesize(home, min_occurrences=3)   # second run: already known
    assert res2["queued"] == [] and res2["skipped_known"] >= 1


def test_synth_ingests_telemetry_counts(tmp_path):
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    (home / "logs" / "corvin.log").write_text(_tb(val=1))   # only 1 locally
    sig = ES.parse_tracebacks(_tb(val=1))[0]
    tsig = {**sig.to_dict(), "count": 5, "instance": "userX"}  # foreign users saw it 5×
    res = DS.synthesize(home, min_occurrences=3, telemetry_sigs=[tsig])
    assert len(res["queued"]) == 1                  # 1 local + 5 telemetry ≥ 3


def test_synth_rejects_malicious_telemetry_top_repo_file(tmp_path):
    # SH-8: an attacker-supplied top_repo_file that is absolute / has `..` / a colon
    # must NOT become a code-patch target — it is dropped to non-localizable, so the
    # signature can only ever be report-only, never queued for a patch.
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    for i, evil in enumerate(("/etc/passwd", "../../etc/shadow", "C:\\Windows\\system32")):
        sigid = f"sig_evil_{i}"
        tsig = {"signature": sigid, "exc_type": "ValueError",
                "message_template": "boom", "func": "f",
                "top_repo_file": evil, "count": 99, "instance": "attacker"}
        res = DS.synthesize(home, min_occurrences=3, telemetry_sigs=[tsig])
        assert sigid not in res["queued"]
    # no attacker path was written into a pending diagnosis
    pend = list((home / "aco" / "diagnoses" / "pending").glob("*.json"))
    assert pend == []


def test_synth_rejects_nonexistent_file_under_checkout(tmp_path):
    # SH-8: with a known repo_root, a top_repo_file that doesn't exist in the
    # checkout is dropped (can't localize a diagnosis onto a file that isn't there).
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    repo = tmp_path / "checkout"; repo.mkdir()
    tsig = {"signature": "sig::ghost", "exc_type": "ValueError",
            "message_template": "boom", "func": "f",
            "top_repo_file": "corvin_console/does_not_exist.py",
            "count": 99, "instance": "u"}
    res = DS.synthesize(home, min_occurrences=3, telemetry_sigs=[tsig], repo_root=repo)
    assert res["queued"] == []
    # but an existing repo file passes the check and is queued
    (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
    tsig2 = {**tsig, "signature": "sig::real", "top_repo_file": "real.py"}
    res2 = DS.synthesize(home, min_occurrences=3, telemetry_sigs=[tsig2], repo_root=repo)
    assert "sig::real" in res2["queued"]


# ── telemetry channel ────────────────────────────────────────────────────────────

def test_telemetry_default_on_opt_out(tmp_path, monkeypatch):
    home = tmp_path / "home"
    # Default-ON (opt-out, maintainer decision): granted unless explicitly disabled.
    monkeypatch.delenv("CORVIN_TELEMETRY_OPTIN", raising=False)
    assert TEL.consent_granted(home) is True
    # Explicit opt-out via env → off, and nothing is collected / submitted.
    monkeypatch.setenv("CORVIN_TELEMETRY_OPTIN", "false")
    assert TEL.consent_granted(home) is False
    assert TEL.collect_local(home) is None
    assert TEL.submit(home)["sent"] == 0


def test_telemetry_opt_in_collects_scrubbed(tmp_path, monkeypatch):
    monkeypatch.delenv("CORVIN_TELEMETRY_OPTIN", raising=False)
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    (home / "logs" / "corvin.log").write_text(
        _tb(val=1).replace('e1', 'user bob@x.com at /home/bob/x'), encoding="utf-8")
    TEL.grant_consent(home, pseudonym="tester")
    rep = TEL.collect_local(home)
    assert rep and rep["instance"] == "tester" and rep["signatures"]
    blob = json.dumps(rep)
    assert "@x.com" not in blob and "/home/bob" not in blob   # scrubbed
    assert rep["schema"] == "aco.telemetry/1"


def test_telemetry_submit_uses_injected_http_and_moves_sent(tmp_path):
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    (home / "logs" / "corvin.log").write_text(_tb(val=1))
    TEL.grant_consent(home, pseudonym="t")
    rep = TEL.collect_local(home)
    TEL.write_outbox(home, rep, stamp="0001")
    calls = []
    def http(url, payload):
        calls.append((url, payload)); return (True, "ok")
    res = TEL.submit(home, url="https://intake.example/telemetry", http=http)
    assert res["sent"] == 1 and len(calls) == 1
    assert calls[0][0] == "https://intake.example/telemetry"
    assert list((home / "aco" / "telemetry" / "sent").glob("*.json"))


def test_telemetry_assert_safe_blocks_leak(tmp_path):
    with pytest.raises(ValueError):
        TEL._assert_safe({"signatures": [{"message_template": "leak bob@evil.com"}]})


def test_scrub_hardened_secret_shapes():
    cases = {
        "auth eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dQw4w9WgXcQ done": "eyJzdWIi",
        "key sk_live_abcd1234efgh5678 used": "sk_live_abcd1234",
        "open \\\\fileserver\\share\\secret.txt": "fileserver",
        "stat ~/projects/CorvinOS/secret.key": "secret.key",
        "iface aa:bb:cc:dd:ee:ff up": "aa:bb:cc",
        "github ghp_AbCdEf0123456789xyz token": "ghp_AbCdEf",
    }
    for raw, must_be_gone in cases.items():
        out = ES.scrub(raw)
        assert must_be_gone not in out, f"{must_be_gone!r} survived scrub of {raw!r} -> {out!r}"


def test_telemetry_payload_is_content_free(tmp_path):
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    # a traceback whose MESSAGE carries a name + a bare ERROR line with a username
    (home / "logs" / "corvin.log").write_text(
        _tb(val=1).replace("e1", "could not greet 'Silvio Jurk'")
        + "ERROR adapter: user 'Hans' said something secret\n", encoding="utf-8")
    TEL.grant_consent(home, pseudonym="t")
    rep = TEL.collect_local(home)
    assert rep is not None
    blob = json.dumps(rep)
    assert "Silvio" not in blob and "Hans" not in blob          # no message text at all
    assert "message_template" not in blob                       # field not transmitted
    # only the localized traceback signature is present (bare ERROR line excluded)
    assert all(s.get("top_repo_file") for s in rep["signatures"])


def test_assert_safe_scans_dict_keys(tmp_path):
    with pytest.raises(ValueError):
        TEL._assert_safe({"counts": {"/home/victim/leak": 3}})   # leak hidden in a KEY


def test_assert_safe_rejects_nonhex_signature_field():
    with pytest.raises(ValueError):
        TEL._assert_safe({"signature": "leak bob@evil.com"})     # not a hash → scanned


def test_synth_dedups_mirror_and_telemetry_same_instance(tmp_path):
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    # instance 'hetzner' is rsync-mirrored: 2 occurrences in its mirrored log
    mlog = home / "aco" / "remote" / "hetzner" / "logs"; mlog.mkdir(parents=True)
    (mlog / "corvin.log").write_text(_tb(val=1) + _tb(val=2), encoding="utf-8")
    sig = ES.parse_tracebacks(_tb(val=1))[0]
    # the SAME instance also submits telemetry claiming count=5 for the same sig
    tsig = {**sig.to_dict(), "count": 5, "instance": "hetzner"}
    res = DS.synthesize(home, min_occurrences=3, telemetry_sigs=[tsig])
    # telemetry from the mirrored instance is dropped → only the 2 mirrored
    # occurrences count → below threshold 3 → NOT queued (no false positive)
    assert res["queued"] == []


def test_telemetry_ingest_inbox_roundtrip(tmp_path):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    rep = {"schema": "aco.telemetry/1", "instance": "u1",
           "signatures": [{"signature": "abc", "exc_type": "ValueError",
                           "top_repo_file": "core/x.py", "count": 4}]}
    (inbox / "r1.json").write_text(json.dumps(rep))
    (inbox / "bad.json").write_text(json.dumps({"schema": "other"}))
    out = TEL.ingest_inbox(inbox)
    assert len(out) == 1 and out[0]["signature"] == "abc" and out[0]["instance"] == "u1"


# ── loop integration: repro gate is fail-closed ─────────────────────────────────

def _cap_repo(tmp_path, monkeypatch):
    pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization as S
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from corvin_console.aco import maintainer_capability as MC  # type: ignore
    import base64
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw, S.NoEncryption())
    pub = sk.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
    monkeypatch.setenv("CORVIN_INSTANCE_ID", "rep1")
    monkeypatch.setenv("CORVIN_MAINTAINER_PUBKEY", base64.b64encode(pub).decode())
    monkeypatch.setenv("CORVIN_MAINTAINER_CAP", MC.issue(priv, instance_id="rep1", subject="shumway"))
    repo = tmp_path / "repo"; repo.mkdir()
    def g(*a): subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    g("init", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "app.py").write_text("x = 1\n"); g("add", "-A"); g("commit", "-m", "init")
    return repo


def _patch_src(_diag):
    return Patch("d", "fix", "engine_generated",
                 edits=[PatchEdit("tests/test_x.py", "def test_x():\n    assert True\n"),
                        PatchEdit("app.py", "x = 2\n")])


def test_loop_fail_closed_when_proof_required_but_no_runner(tmp_path, monkeypatch):
    from corvin_console.aco.maintenance_loop import run_maintenance_loop  # type: ignore
    repo = _cap_repo(tmp_path, monkeypatch)
    r = run_maintenance_loop(diagnosis={"id": "d", "requires_repro_test": True},
                             repo_dir=repo, patch_source=_patch_src,
                             gate_runner=lambda: (True, "ok"))
    assert r.status == "repro_failed" and "no repro_runner" in r.detail
    # main untouched
    head = subprocess.run(["git", "-C", str(repo), "show", "main:app.py"],
                          capture_output=True, text=True).stdout
    assert head == "x = 1\n"


def test_loop_blocks_when_repro_not_proven(tmp_path, monkeypatch):
    from corvin_console.aco.maintenance_loop import run_maintenance_loop  # type: ignore
    repo = _cap_repo(tmp_path, monkeypatch)
    class _NotProven:
        proven = False; detail = "stage A: did not reproduce"
        def to_dict(self): return {"proven": False}
    r = run_maintenance_loop(diagnosis={"id": "d", "requires_repro_test": True},
                             repo_dir=repo, patch_source=_patch_src,
                             gate_runner=lambda: (True, "ok"),
                             repro_runner=lambda patch: _NotProven())
    assert r.status == "repro_failed" and "did not reproduce" in r.detail


def test_loop_proceeds_when_repro_proven(tmp_path, monkeypatch):
    from corvin_console.aco.maintenance_loop import run_maintenance_loop  # type: ignore
    repo = _cap_repo(tmp_path, monkeypatch)
    class _Proven:
        proven = True; detail = "proven"
        def to_dict(self): return {"proven": True}
    r = run_maintenance_loop(diagnosis={"id": "d", "requires_repro_test": True},
                             repo_dir=repo, patch_source=_patch_src,
                             gate_runner=lambda: (True, "ok"),
                             repro_runner=lambda patch: _Proven())
    # engine_generated → ack required → pr_ready (never auto-merged)
    assert r.status == "pr_ready" and r.commit


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
