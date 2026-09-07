"""Live-LLM E2E: a REAL engine turn's audit span lands in THE tenant chain.

FIXER_RULES §6. The audit chain is written during a real engine invocation —
ADR-0171 emits one `engine.span.start` / `engine.span.end` pair per invocation
through `audit.audit_event`, which is exactly the resolver R4 moved from the
host-global chain to the tenant chain. A unit test proves the resolver returns
the right path; only a real turn proves that the path the running engine's audit
writer picks is the same one, in a real process, with a real subprocess in it.

Gated on `CLAUDE_LIVE_E2E=1`; runs `claude -p` (~10 s).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CLAUDE_LIVE_E2E") != "1",
        reason="live LLM E2E — set CLAUDE_LIVE_E2E=1",
    ),
]


def test_a_real_claude_turn_lands_its_engine_span_in_the_tenant_chain(tmp_path):
    home = tmp_path / "corvin-home"
    home.mkdir()
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(home)
    env["CORVIN_AUDIT_ANCHOR_KEY"] = str(home / "anchor.key")
    for k in ("VOICE_AUDIT_PATH", "FORGE_ROOT", "CORVIN_TENANT_ID"):
        env.pop(k, None)

    driver = tmp_path / "drive.py"
    driver.write_text(f'''
import json, subprocess, sys, time
from pathlib import Path
R = Path({str(REPO)!r})
sys.path.insert(0, str(R / "operator" / "forge"))
sys.path.append(str(R / "operator" / "bridges" / "shared"))
import audit as ba
from engine_span import new_span_id, emit_start, emit_end

span = new_span_id()
t0 = time.time()
emit_start(ba.audit_event, span_id=span, role="os",
           engine_id="claude-code", model_id="haiku", run_id="live-e2e")

# THE REAL LLM CALL
proc = subprocess.run(
    ["claude", "-p", "Reply with exactly the word: PONG", "--model", "haiku"],
    capture_output=True, text=True, timeout=180,
)
emit_end(ba.audit_event, span_id=span, role="os", engine_id="claude-code",
         model_id="haiku", run_id="live-e2e",
         status="ok" if proc.returncode == 0 else "error",
         duration_ms=int((time.time() - t0) * 1000))

print("@@" + json.dumps({{
    "rc": proc.returncode,
    "reply": (proc.stdout or "").strip()[:200],
    "chain": str(ba.audit_path()),
    "span_id": span,
}}))
''')
    proc = subprocess.run([str(REPO / ".venv" / "bin" / "python"), str(driver)],
                          capture_output=True, text=True, env=env,
                          cwd=str(REPO), timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads([l for l in proc.stdout.splitlines() if l.startswith("@@")][-1][2:])
    print("LIVE E2E:", json.dumps(out, indent=2))

    # 1. the LLM really answered
    assert out["rc"] == 0, out
    assert "PONG" in out["reply"].upper(), out

    # 2. the chain the running process wrote to is THE tenant chain
    canonical = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    assert Path(out["chain"]) == canonical, out
    assert canonical.is_file(), sorted(str(p) for p in home.rglob("audit.jsonl"))

    # 3. exactly one chain file exists under this home — no split
    assert [p for p in home.rglob("audit.jsonl")] == [canonical]

    # 4. both spans of the real turn are in it, hash-chained
    recs = [json.loads(l) for l in canonical.read_text().splitlines() if l.strip()]
    kinds = [r["event_type"] for r in recs]
    assert "engine.span.start" in kinds and "engine.span.end" in kinds, kinds
    span_recs = [r for r in recs if r["details"].get("span_id") == out["span_id"]]
    assert len(span_recs) == 2, span_recs
    assert span_recs[1]["details"]["status"] == "ok", span_recs[1]

    sys.path.insert(0, str(REPO / "operator" / "forge"))
    from forge.security_events import verify_chain
    os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(home / "anchor.key")
    ok, problems = verify_chain(canonical)
    assert ok, problems
