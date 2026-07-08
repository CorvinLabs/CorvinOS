"""ADR-0178 M4+M5 — the L6 self-improving maintenance loop.

Proves the trust boundary + gate chain: no capability → denied; deny-by-default
on red/absent tests; hard-blocked paths refused; sensitive surfaces + non-low-risk
classes require ack (never auto-merged); low-risk + green + opt-in → ff-merged.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from corvin_console.aco import maintenance_loop as ML  # type: ignore
from corvin_console.aco import maintainer_capability as MC  # type: ignore

pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")


@pytest.fixture
def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    return priv, pub


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def g(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True,
                       capture_output=True, text=True)
    g("init", "-b", "main")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "-A"); g("commit", "-m", "init")
    return repo


@pytest.fixture
def valid_cap(keypair, monkeypatch):
    priv, pub = keypair
    monkeypatch.setenv("CORVIN_INSTANCE_ID", "inst-test")
    tok = MC.issue(priv, instance_id="inst-test", subject="shumway")
    return tok, pub


def _green(): return (True, "ok")
def _red(): return (False, "1 failed")


class _ProvenRepro:
    """Stub reproduction result: the bug is proven and the fix works."""
    proven = True
    def to_dict(self): return {"proven": True}


def _repro_ok(_patch): return _ProvenRepro()


def test_denied_without_capability(git_repo):
    r = ML.run_maintenance_loop(diagnosis={"id": "d1"}, repo_dir=git_repo,
                                patch_source=lambda d: None, capability_token=None,
                                public_key_bytes=b"x" * 32, gate_runner=_green)
    assert r.status == "denied"


def test_no_patch_source(git_repo, valid_cap):
    tok, pub = valid_cap
    r = ML.run_maintenance_loop(diagnosis={"id": "d1"}, repo_dir=git_repo,
                                patch_source=None, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green)
    assert r.status == "no_patch"


def test_red_tests_block(git_repo, valid_cap):
    tok, pub = valid_cap
    patch = ML.Patch("d1", "fix", "platform_path",
                     [ML.PatchEdit("app.py", "x = 2\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d1"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_red)
    assert r.status == "gate_failed" and "tests red" in r.detail


def test_missing_gate_runner_is_deny_by_default(git_repo, valid_cap):
    tok, pub = valid_cap
    patch = ML.Patch("d1", "fix", "platform_path", [ML.PatchEdit("app.py", "x=2\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d1"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=None)
    assert r.status == "gate_failed"


def test_hard_blocked_path_refused(git_repo, valid_cap):
    tok, pub = valid_cap
    patch = ML.Patch("d1", "edit license", "platform_path",
                     [ML.PatchEdit("LICENSE", "haha\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d1"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green)
    assert r.status == "gate_failed" and "hard-blocked" in r.detail


def test_sensitive_surface_requires_ack_no_merge(git_repo, valid_cap):
    tok, pub = valid_cap
    (git_repo / "disclosure.py").write_text("a=1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "d"], check=True,
                   capture_output=True)
    patch = ML.Patch("d2", "touch disclosure", "platform_path",
                     [ML.PatchEdit("disclosure.py", "a=2\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d2"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green,
                                enable_direct_main=True)
    assert r.status == "pr_ready" and r.requires_ack is True


def test_non_low_risk_class_requires_ack(git_repo, valid_cap):
    tok, pub = valid_cap
    patch = ML.Patch("d3", "refactor", "refactor", [ML.PatchEdit("app.py", "x=3\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d3"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green,
                                enable_direct_main=True)
    assert r.status == "pr_ready" and r.requires_ack is True


def test_low_risk_green_direct_main_merges(git_repo, valid_cap):
    tok, pub = valid_cap
    patch = ML.Patch("d4", "platform path fix", "platform_path",
                     [ML.PatchEdit("app.py", "x = 99\n")])
    # SH-2: direct-main now requires a reproduction proof — supply a proven runner.
    r = ML.run_maintenance_loop(diagnosis={"id": "d4"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green,
                                repro_runner=_repro_ok,
                                enable_direct_main=True, enable_push=False)
    assert r.status == "merged", r.detail
    # main now has the change
    head = subprocess.run(["git", "-C", str(git_repo), "show", "main:app.py"],
                          capture_output=True, text=True).stdout
    assert "x = 99" in head


def test_direct_main_refused_without_repro_runner(git_repo, valid_cap):
    # SH-2 fail-closed: --direct-main WITHOUT a repro_runner must NOT reach main;
    # it is silently downgraded to the safe branch + PR default.
    tok, pub = valid_cap
    patch = ML.Patch("d4b", "platform path fix", "platform_path",
                     [ML.PatchEdit("app.py", "x = 77\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d4b"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green,
                                enable_direct_main=True, enable_push=True)
    assert r.status == "pr_ready", r.detail
    assert r.telemetry.get("direct_main_refused") == "no_repro_runner"
    # main must NOT carry the change (still the initial content)
    head = subprocess.run(["git", "-C", str(git_repo), "show", "main:app.py"],
                          capture_output=True, text=True).stdout
    assert "x = 77" not in head


def test_repro_runner_not_proven_blocks_commit(git_repo, valid_cap):
    # SH-2: a wired repro_runner that reports NOT proven must block the commit.
    tok, pub = valid_cap
    class _NotProven:
        proven = False
        detail = "stage A: not red"
        def to_dict(self): return {"proven": False}
    patch = ML.Patch("d4c", "fix", "platform_path", [ML.PatchEdit("app.py", "x = 8\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d4c"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green,
                                repro_runner=lambda p: _NotProven(),
                                enable_direct_main=True)
    assert r.status == "repro_failed", r.detail


def test_aco_self_improvement_path_requires_ack(git_repo, valid_cap):
    # SH-6: a patch that edits the ACO self-improvement code (its own gate) can
    # never auto-merge on tests-green alone — it forces a human ack.
    tok, pub = valid_cap
    target = "core/console/corvin_console/aco/maintenance_loop.py"
    p = git_repo / "core" / "console" / "corvin_console" / "aco"
    p.mkdir(parents=True)
    (p / "maintenance_loop.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "seed"], check=True,
                   capture_output=True)
    patch = ML.Patch("d7", "touch own gate", "platform_path",
                     [ML.PatchEdit(target, "x = 2\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d7"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green,
                                repro_runner=_repro_ok, enable_direct_main=True)
    assert r.status == "pr_ready" and r.requires_ack is True, r.detail


def test_low_risk_green_default_is_pr_not_merge(git_repo, valid_cap):
    # enable_direct_main defaults False → even a perfect low-risk fix stays PR-ready.
    tok, pub = valid_cap
    patch = ML.Patch("d5", "fix", "platform_path", [ML.PatchEdit("app.py", "x = 5\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d5"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green)
    assert r.status == "pr_ready"


def test_patch_path_escape_refused(git_repo, valid_cap):
    tok, pub = valid_cap
    patch = ML.Patch("d6", "escape", "platform_path",
                     [ML.PatchEdit("../../etc/evil", "pwned\n")])
    r = ML.run_maintenance_loop(diagnosis={"id": "d6"}, repo_dir=git_repo,
                                patch_source=lambda d: patch, capability_token=tok,
                                public_key_bytes=pub, gate_runner=_green)
    assert r.status == "gate_blocked"
    assert not (git_repo.parent.parent / "etc" / "evil").exists()


def test_convergence_tracker():
    t = ML.ConvergenceTracker(k_max=2)
    assert t.should_attempt("d") is True
    t.record("d"); t.record("d")
    assert t.should_attempt("d") is False and t.exhausted("d") is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
