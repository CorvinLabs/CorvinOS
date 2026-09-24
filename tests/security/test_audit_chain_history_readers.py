"""ADR-2058 — console READERS traverse a chain's seam-linked history.

After a chain loss the canonical file restarts; its past lives in history files
it links to by ``audit.chain_supersedes`` seams (``record_chain_supersession``).
``chain_history_files`` / ``iter_chain_records`` resolve those seams for readers
that aggregate; verifiers keep reading the canonical chain alone.

Every scenario runs in a fresh interpreter with its own
``CORVIN_AUDIT_ANCHOR_KEY``: ``security_events`` caches the anchor key
process-wide, so an in-process test would share it with every other test.
Seams are real — written by ``record_chain_supersession`` — never hand-crafted.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FORGE = REPO / "corvin_operator" / "forge"
CONSOLE = REPO / "core" / "console"

HELPERS = textwrap.dedent("""
    import json, sys, time
    from pathlib import Path
    from forge import security_events as se

    def mk(path, marker, n=2):
        path.parent.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            se.write_event(path, "tool.created", details={"tool": f"{marker}{i}"})
        time.sleep(0.02)

    def markers(recs):
        return [r["details"]["tool"] for r in recs
                if r.get("event_type") == "tool.created"]
""")

UNIT = HELPERS + textwrap.dedent("""
    root = Path(sys.argv[1])
    forge = root / "forge"
    rec_dir = forge / "recovered"
    chain = forge / "audit.jsonl"
    carved = rec_dir / "audit.carved-20260924T190000Z.jsonl"
    backup = rec_dir / "audit.backup-2026-08-31.jsonl"
    postloss = forge / "audit.jsonl.postloss-20260924T180640Z"
    corrupt = rec_dir / "audit.corrupt-20260924.jsonl"
    unrelated = rec_dir / "audit.unrelated.jsonl"
    standalone = root / "solo" / "audit.jsonl"

    # Written oldest-first: carved < backup < postloss < corrupt < unrelated < canonical.
    mk(carved, "carved")
    mk(backup, "backup")
    mk(postloss, "postloss")
    mk(corrupt, "corrupt")
    mk(unrelated, "unrelated")
    mk(chain, "canon")
    mk(standalone, "solo")
    out = {}
    out["before_seams"] = [p.name for p in se.chain_history_files(chain)]

    # Seams in NON-chronological order; carved is reachable only transitively
    # (its seam lives inside the postloss file, not in the canonical chain).
    assert se.record_chain_supersession(postloss, carved, reason="carve") is not None
    assert se.record_chain_supersession(chain, postloss, reason="chain_loss") is not None
    assert se.record_chain_supersession(chain, backup, reason="chain_loss") is not None
    assert se.record_chain_supersession(chain, corrupt, reason="chain_loss") is not None

    out["history"] = [p.name for p in se.chain_history_files(chain)]
    out["history_again"] = [p.name for p in se.chain_history_files(chain)]
    out["all_markers"] = markers(se.iter_chain_records(chain))
    out["canon_only"] = markers(se.iter_chain_records(chain, include_history=False))
    out["needle_markers"] = markers(se.iter_chain_records(chain, needles=('"backup',)))
    out["solo_history"] = [p.name for p in se.chain_history_files(standalone)]
    out["solo_markers"] = markers(se.iter_chain_records(standalone))
    out["missing_history"] = se.chain_history_files(root / "nope" / "audit.jsonl")
    out["missing_records"] = list(se.iter_chain_records(root / "nope" / "audit.jsonl"))

    # A torn line in a history file is skipped, not fatal.
    with backup.open("a") as fh:
        fh.write('{"torn": ')
    out["after_torn"] = markers(se.iter_chain_records(chain))

    # The canonical chain still verifies; nothing was merged into it.
    ok = se.verify_chain(chain)[0]
    out["canon_verifies"] = bool(ok)
    print(json.dumps(out))
""")

READERS = HELPERS + textwrap.dedent("""
    import os
    home = Path(os.environ["CORVIN_HOME"])
    tenant = "_default"
    from core.paths import tenant_audit_chain
    chain = Path(tenant_audit_chain(tenant))
    hist = chain.parent / "recovered" / "audit.backup-2026-08-31.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)

    # History-only work: one OS turn, one classified turn, one learning record,
    # all dated 2026-08-30 (before the loss).
    se.write_event(hist, "os_turn.started", details={
        "turn_id": "hist-turn-1", "model": "claude-history-only-1", "persona": "assistant"})
    se.write_event(hist, "os_turn.completed", details={
        "turn_id": "hist-turn-1", "model": "claude-history-only-1", "exit_code": 0,
        "input_tokens": 11, "output_tokens": 7, "tools_called": 1})
    se.write_event(hist, "skill.model_selector.classified", details={
        "complexity": "simple", "confidence": 0.85, "turn_id": "hist-turn-1"})
    se.write_event(hist, "learning.outcome_recorded", details={
        "loop_id": "loop-hist", "skill_id": "os.delegation_router",
        "tenant_id": tenant, "outcome": "success"})
    # The writer refuses a foreign tenant_id outright, so a foreign record can
    # only reach a history file by other means (a restored file from another
    # tenant, a carve). Appended raw: the READER must still drop it.
    with hist.open("a") as fh:
        fh.write(json.dumps({"event_type": "learning.outcome_recorded", "ts": time.time(),
                             "details": {"loop_id": "loop-hist", "skill_id": "foreign",
                                         "tenant_id": "other-tenant",
                                         "outcome": "success"}}) + "\\n")
    time.sleep(0.02)
    # The post-loss canonical chain: one turn of its own + the seam.
    se.write_event(chain, "os_turn.completed", details={
        "turn_id": "canon-turn-1", "model": "claude-canonical-1", "exit_code": 0,
        "input_tokens": 3, "output_tokens": 2, "tools_called": 0})
    assert se.record_chain_supersession(chain, hist, reason="chain_loss") is not None

    out = {}
    from corvin_console import model_usage as mu
    res = mu.model_usage(tenant)
    out["usage_models"] = sorted(m["model_id"] for m in res["models"])
    out["usage_turns"] = res["totals"]["turns"]

    from corvin_console.routes import maturity_live as ml
    events, total = ml.read_audit_events(tenant, None)
    kinds = [k for _ts, k in events]
    out["maturity_has_history"] = "skill.model_selector.classified" in kinds
    out["maturity_total"] = total
    out["canonical_lines"] = sum(1 for l in chain.read_text().splitlines() if l.strip())

    from core.learning import model_selection_learner as msl
    turns = msl._read_completed_turns(chain, msl._MAX_SCAN_BYTES)
    out["learner_turns"] = sorted(t.get("model") or "" for t in turns)

    from corvin_console.routes import engine_api as ea
    out["classified_fwd"] = len(list(ea._iter_classified(tenant)))
    out["classified_rev"] = len(list(ea._iter_classified(tenant, newest_first=True)))

    from corvin_console.routes import learning_analytics as la
    rows = la._read_loop_audit_events(tenant, "loop-hist", 10)
    out["loop_rows"] = [(r["event_type"], r["skill_id"], r["outcome"]) for r in rows]
    print(json.dumps(out))
""")


def _run(tmp_path: Path, script: str) -> dict:
    env = dict(os.environ)
    env["CORVIN_AUDIT_ANCHOR_KEY"] = str(tmp_path / "keys" / "audit_anchor.key")
    env["CORVIN_HOME"] = str(tmp_path / "home")
    env["CORVIN_TENANT_ID"] = "_default"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(FORGE), str(REPO), str(CONSOLE), env.get("PYTHONPATH", "")]
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env, capture_output=True, text=True, timeout=180, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_history_resolution_ordering_and_exclusions(tmp_path):
    out = _run(tmp_path, UNIT)
    # no seams yet → nothing, even though recovered/ is full of files
    assert out["before_seams"] == []
    # oldest-first by first record ts, NOT seam order; carved found transitively;
    # .corrupt- and the unrelated file never picked up; the cache re-resolved
    # after the canonical chain grew its seams.
    assert out["history"] == [
        "audit.carved-20260924T190000Z.jsonl",
        "audit.backup-2026-08-31.jsonl",
        "audit.jsonl.postloss-20260924T180640Z",
    ]
    assert out["history_again"] == out["history"]
    assert out["all_markers"] == [
        "carved0", "carved1", "backup0", "backup1",
        "postloss0", "postloss1", "canon0", "canon1",
    ]
    assert out["canon_only"] == ["canon0", "canon1"]
    assert out["needle_markers"] == ["backup0", "backup1"]
    assert out["solo_history"] == [] and out["solo_markers"] == ["solo0", "solo1"]
    assert out["missing_history"] == [] and out["missing_records"] == []
    assert out["after_torn"] == out["all_markers"]
    assert out["canon_verifies"] is True


def test_console_readers_count_history_only_records(tmp_path):
    out = _run(tmp_path, READERS)
    # model_usage(): a model that exists ONLY in the history file is counted.
    assert "claude-history-only-1" in out["usage_models"], out
    assert "claude-canonical-1" in out["usage_models"], out
    assert out["usage_turns"] == 2
    # maturity: history events are read and the chain length spans the seam
    assert out["maturity_has_history"] is True
    assert out["maturity_total"] > out["canonical_lines"]
    # learner cost/threshold reader joins the history turn
    assert "claude-history-only-1" in out["learner_turns"]
    # engine config classified tally, both directions
    assert out["classified_fwd"] == 1 and out["classified_rev"] == 1
    # learning-loop audit rows: the history record is found; the record naming
    # another tenant is dropped (fail-closed isolation)
    assert out["loop_rows"] == [
        ["learning.outcome_recorded", "os.delegation_router", "success"]
    ], out["loop_rows"]
