"""Adversarial review 2026-11-05 (area DX-4) — a delegated run is priceable on EVERY surface.

``run_delegate`` wrote its ``engine.span.*`` pair only from the WDAT run-dir writer, and
that writer returns at once without ``CORVIN_SESSION_DIR`` — which only the web console
injects. Every delegation from a bridge OS turn (Discord/Telegram/..., the ``delegate``
MCP tool) or from the ACS engine adapter therefore left NO span on the chain: the run
was invisible to the model-usage roll-up and priced at $0.00 (ADR-0759: "don't add a
worker spawn path without a priceable span").

Drives the real ``run_delegate`` with the real ``ClaudeCodeEngine`` spawning a real
subprocess (a recording ``claude`` shim that prints the CLI stream frames) and reads the
real tenant chain.
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "delegate", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shim script")

REPORTED = "claude-sonnet-5"

_SHIM = r'''#!__PY__
import json, sys
sys.stdin.read() if not sys.stdin.isatty() else None
print(json.dumps({"type": "system", "subtype": "init", "model": "__MODEL__", "session_id": "s1"}))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                  "usage": {"input_tokens": 5, "output_tokens": 9,
                            "cache_read_input_tokens": 400, "cache_creation_input_tokens": 60}}))
'''


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "corvin"
    h.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("CORVIN_SESSION_DIR", raising=False)  # a bridge / CLI caller
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    return h


def _shim(tmp_path: Path) -> str:
    p = tmp_path / "claude"
    p.write_text(_SHIM.replace("__PY__", sys.executable).replace("__MODEL__", REPORTED), "utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


def _worker_ends(home: Path) -> list[dict]:
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not chain.is_file():
        return []
    out = []
    for line in chain.read_text("utf-8").splitlines():
        rec = json.loads(line) if line.strip() else {}
        if rec.get("event_type") == "engine.span.end" and rec["details"].get("role") == "worker":
            out.append(rec["details"])
    return out


def test_dx4_bridge_delegation_writes_a_priceable_span(home, tmp_path):
    from agents.claude_code import ClaudeCodeEngine
    from corvin_delegate import delegation

    binary = _shim(tmp_path)
    res = delegation.run_delegate(
        engine="claude_code", prompt="summarise the rust changelog", budget_s=30,
        engine_factory=lambda _eid: ClaudeCodeEngine(binary=binary))
    assert res.ok, f"positive control: the delegated engine never ran ({res.error})"
    ends = _worker_ends(home)
    assert len(ends) == 1, f"a bridge delegation left {len(ends)} worker spans on the chain"
    end = ends[0]
    assert end["model_id"] == REPORTED
    assert (end["input_tokens"], end["output_tokens"],
            end["cache_read_tokens"], end["cache_write_tokens"]) == (5, 9, 400, 60), end


def test_refute_dx4_console_delegation_writes_exactly_one_span_pair(home, tmp_path, monkeypatch):
    """Console surface (session dir set): still the WDAT run + ONE start/end pair, not two."""
    from agents.claude_code import ClaudeCodeEngine
    from corvin_delegate import delegation

    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setenv("CORVIN_SESSION_DIR", str(session))
    binary = _shim(tmp_path)
    res = delegation.run_delegate(
        engine="claude_code", prompt="summarise the rust changelog", budget_s=30,
        engine_factory=lambda _eid: ClaudeCodeEngine(binary=binary))
    assert res.ok, res.error
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    recs = [json.loads(x) for x in chain.read_text("utf-8").splitlines() if x.strip()]
    starts = [r for r in recs if r.get("event_type") == "engine.span.start"
              and r["details"].get("role") == "worker"]
    assert len(starts) == 1 and len(_worker_ends(home)) == 1, (len(starts), len(_worker_ends(home)))
    assert any(r.get("event_type") == "acs.worker_spawned" for r in recs), "WDAT records lost"
    assert list((session / "acs" / "runs").iterdir()), "WDAT run directory not written"


# ── DLG-21: the span's four-way split comes from engine_span.usage_split() ──────────


_SHIM_NORMALISED = r'''#!__PY__
import json, sys
sys.stdin.read() if not sys.stdin.isatty() else None
print(json.dumps({"type": "system", "subtype": "init", "model": "__MODEL__", "session_id": "s1"}))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                  "usage": {"input_tokens": 5, "output_tokens": 9, "tokens_in": 999,
                            "cache_read_tokens": 400, "cache_write_tokens": 60}}))
'''


def test_dlg21_run_delegate_span_uses_the_shared_usage_normaliser(home, tmp_path):
    """An engine reporting the NORMALISED cache names is priced with its cache here exactly
    as on every other path; a non-alias (`tokens_in`) is never read. Before: cache 0/0."""
    from agents.claude_code import ClaudeCodeEngine
    from corvin_delegate import delegation
    import engine_span

    p = tmp_path / "claude"
    p.write_text(_SHIM_NORMALISED.replace("__PY__", sys.executable)
                 .replace("__MODEL__", REPORTED), "utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    res = delegation.run_delegate(
        engine="claude_code", prompt="summarise the rust changelog", budget_s=30,
        engine_factory=lambda _eid: ClaudeCodeEngine(binary=str(p)))
    assert res.ok, f"positive control: the delegated engine never ran ({res.error})"
    ends = _worker_ends(home)
    assert len(ends) == 1, ends
    end = ends[0]
    got = {k: end[k] for k in ("input_tokens", "output_tokens",
                                "cache_read_tokens", "cache_write_tokens")}
    assert got == engine_span.usage_split(res.usage) == {
        "input_tokens": 5, "output_tokens": 9,
        "cache_read_tokens": 400, "cache_write_tokens": 60}, (got, res.usage)
