"""ADR-0178 — engine-backed patch generator (the fully-automatic patch_source).

Key invariant under test: an engine-generated patch is ALWAYS ack-gated — even
with a valid capability, green tests, and --direct-main, it can only ever become
a PR (requires_ack), never an auto-merge to main.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from corvin_console.aco import patch_generator as PG  # type: ignore
from corvin_console.aco import maintenance_loop as ML  # type: ignore
from corvin_console.aco import maintainer_capability as MC  # type: ignore


def test_parse_valid_json_forces_engine_risk_class():
    txt = '{"summary":"fix","risk_class":"platform_path",' \
          '"edits":[{"path":"a.py","new_content":"x=1\\n"}]}'
    p = PG.parse_patch(txt, {"id": "d1"})
    assert p is not None and p.risk_class == PG.ENGINE_RISK_CLASS  # LLM claim ignored
    assert p.edits[0].path == "a.py"


def test_parse_handles_fenced_and_prose():
    txt = "Sure!\n```json\n{\"summary\":\"s\",\"edits\":[{\"path\":\"b.py\",\"new_content\":\"y\\n\"}]}\n```\n"
    p = PG.parse_patch(txt, {"id": "d"})
    assert p is not None and p.edits[0].path == "b.py"


@pytest.mark.parametrize("txt", [
    "", "no json here", "{not json}", '{"summary":"s"}',          # no/empty edits
    '{"edits":[]}',
    '{"edits":[{"path":"/etc/passwd","new_content":"x"}]}',        # absolute
    '{"edits":[{"path":"../x","new_content":"x"}]}',               # traversal
    '{"edits":[{"path":"a","new_content":5}]}',                    # wrong type
])
def test_parse_rejects_bad(txt):
    assert PG.parse_patch(txt, {"id": "d"}) is None


def test_parse_rejects_too_many_edits():
    edits = [{"path": f"f{i}.py", "new_content": "x"} for i in range(50)]
    assert PG.parse_patch(json.dumps({"edits": edits}), {"id": "d"}, max_edits=8) is None


def test_engine_source_reads_target_and_builds_patch(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    captured = {}
    def stub_llm(prompt):
        captured["prompt"] = prompt
        return '{"summary":"fix","edits":[{"path":"app.py","new_content":"x = 2\\n"}]}'
    src = PG.engine_patch_source(repo_dir=tmp_path, llm=stub_llm)
    p = src({"id": "d1", "file": "app.py", "root_cause": "wrong value"})
    assert p is not None and p.edits[0].new_content == "x = 2\n"
    assert "x = 1" in captured["prompt"]          # current file content was given
    assert "wrong value" in captured["prompt"]     # diagnosis was given


def test_engine_source_no_llm_returns_none(tmp_path):
    src = PG.engine_patch_source(repo_dir=tmp_path, llm=None)
    assert src({"id": "d", "file": "app.py"}) is None


def test_engine_source_garbage_reply_returns_none(tmp_path):
    (tmp_path / "app.py").write_text("x=1\n", encoding="utf-8")
    src = PG.engine_patch_source(repo_dir=tmp_path, llm=lambda p: "I cannot help")
    assert src({"id": "d", "file": "app.py"}) is None


def test_engine_generated_patch_is_never_auto_merged(tmp_path, monkeypatch):
    """THE security invariant: engine patch + valid cap + green + --direct-main
    → PR-ready with requires_ack, NEVER merged to main."""
    from cryptography.hazmat.primitives import serialization as S
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw, S.NoEncryption())
    pub = sk.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
    monkeypatch.setenv("CORVIN_INSTANCE_ID", "inst-eng")

    repo = tmp_path / "repo"; repo.mkdir()
    def g(*a): subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    g("init", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "-A"); g("commit", "-m", "init")

    src = PG.engine_patch_source(
        repo_dir=repo,
        llm=lambda p: '{"summary":"fix","risk_class":"platform_path",'
                      '"edits":[{"path":"app.py","new_content":"x = 2\\n"}]}')
    tok = MC.issue(priv, instance_id="inst-eng", subject="shumway")
    r = ML.run_maintenance_loop(
        diagnosis={"id": "d1", "file": "app.py"}, repo_dir=repo,
        patch_source=src, capability_token=tok, public_key_bytes=pub,
        gate_runner=lambda: (True, "ok"),
        enable_direct_main=True, enable_push=True)   # both opt-ins ON
    assert r.status == "pr_ready" and r.requires_ack is True, r
    # main is UNCHANGED — the engine patch never reached it
    head = subprocess.run(["git", "-C", str(repo), "show", "main:app.py"],
                          capture_output=True, text=True).stdout
    assert head == "x = 1\n"


def test_default_llm_runs_engine_tool_less():
    # CRITICAL regression: the patch generator must spawn the engine with NO write
    # tools (mode="restricted" → --disallowedTools "*"), so it can never write/
    # commit/push as a side-effect before the gate. Source-introspection guard.
    import inspect
    src = inspect.getsource(PG.default_llm)
    assert 'mode": "restricted"' in src or "mode='restricted'" in src or \
           '"restricted"' in src, "patch generator engine must run tool-less"


def test_parse_rejects_colon_in_first_segment():
    assert PG.parse_patch(
        '{"edits":[{"path":"weird:name/x.py","new_content":"x"}]}', {"id": "d"}) is None
    assert PG.parse_patch(
        '{"edits":[{"path":"C:/x.py","new_content":"x"}]}', {"id": "d"}) is None


def test_engine_source_rejects_symlink_escape(tmp_path):
    if os.name == "nt":
        pytest.skip("symlink semantics differ on Windows")
    repo = tmp_path / "repo"; repo.mkdir()
    secret = tmp_path / "outside_secret"; secret.write_text("TOPSECRET")
    (repo / "link").symlink_to(secret)         # in-repo name → out-of-repo target
    src = PG.engine_patch_source(repo_dir=repo, llm=lambda p: pytest.fail("should not reach llm"))
    assert src({"id": "d", "file": "link"}) is None   # refused before prompting


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
