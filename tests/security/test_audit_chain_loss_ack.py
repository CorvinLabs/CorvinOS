"""ADR-2058 — acknowledging a LOST audit chain.

Reproduces the 2026-09-24 incident end to end in an isolated key dir: a chain is
anchored, the file is destroyed (``git clean -fxd``), writers keep appending
(every record stamped ``_chain_replaced_from``) and the tripwire's verifier
refuses. The acknowledgement must make the canonical chain verify again WITHOUT
deleting or rewriting anything, and must refuse when nothing was lost.

Runs each scenario in a fresh interpreter: ``security_events`` caches the anchor
key process-wide, so an in-process test would share it with every other test.
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

SCENARIO = textwrap.dedent("""
    import hashlib, json, sys
    from pathlib import Path
    from forge import security_events as se
    from forge import chain_loss as cl

    root = Path(sys.argv[1])
    chain = root / "audit" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    mode = sys.argv[2]
    out = {}

    for i in range(5):
        se.write_event(chain, "tool.created", details={"tool": f"t{i}"})
    anchored = se._read_chain_path_record(chain)
    out["anchored_genesis"] = anchored["genesis"]

    if mode == "healthy":
        try:
            cl.acknowledge_chain_loss(chain, cause="test")
            out["refused"] = False
        except cl.ChainLossRefused as exc:
            out["refused"] = True
            out["reason"] = str(exc)
        out["identity_after"] = se._read_chain_path_record(chain)["genesis"]
        print(json.dumps(out)); sys.exit(0)

    chain.unlink()                                   # the loss
    for i in range(3):
        se.write_event(chain, "tool.created", details={"tool": f"after{i}"})
    ok, problems = se.verify_chain(chain)[:2]
    out["before_ok"] = ok
    out["before_issues"] = sorted({p.get("issue") for p in problems})
    postloss_bytes = hashlib.sha256(chain.read_bytes()).hexdigest()

    backup = root / "backup.jsonl"
    se.write_event(backup, "tool.created", details={"tool": "old"})

    res = cl.acknowledge_chain_loss(chain, cause="git clean -fxd in repo checkout",
                                    backups=[backup])
    out["res"] = res
    frozen = Path(res["frozen_postloss"])
    out["frozen_unchanged"] = hashlib.sha256(frozen.read_bytes()).hexdigest() == postloss_bytes
    out["retired_identity_exists"] = Path(res["retired_identity"]).is_file()

    se.write_event(chain, "tool.created", details={"tool": "next"})
    ok, problems = se.verify_chain(chain)[:2]
    out["after_ok"] = ok
    out["after_issues"] = sorted({str(p.get("issue")) for p in problems})
    recs = [json.loads(l) for l in chain.read_text().splitlines()]
    out["first_event"] = recs[0]["event_type"]
    out["first_details"] = recs[0]["details"]
    out["events"] = [r["event_type"] for r in recs]
    out["stamped_after"] = any("_chain_replaced_from" in json.dumps(r) for r in recs)
    print(json.dumps(out))
""")


def _run(tmp_path: Path, mode: str) -> dict:
    env = dict(os.environ)
    env["CORVIN_AUDIT_ANCHOR_KEY"] = str(tmp_path / "keys" / "audit_anchor.key")
    env["PYTHONPATH"] = f"{FORGE}{os.pathsep}{env.get('PYTHONPATH', '')}"
    proc = subprocess.run(
        [sys.executable, "-c", SCENARIO, str(tmp_path), mode],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_lost_chain_is_detected_then_acknowledged(tmp_path):
    out = _run(tmp_path, "loss")
    # positive control: the loss IS detected before the acknowledgement
    assert out["before_ok"] is False
    assert "chain_replaced" in out["before_issues"]
    # after: the canonical chain verifies, first record is the acknowledgement
    assert out["after_ok"] is True, out["after_issues"]
    assert out["first_event"] == "audit.chain_loss_acknowledged"
    d = out["first_details"]
    assert d["lost_genesis"] == out["anchored_genesis"][:32]
    assert d["cause"] == "git clean -fxd in repo checkout"
    assert d["postloss_frozen"] is True and d["backups_linked"] == 1
    # both surviving fragments are linked in-chain; nothing rewritten
    assert out["events"].count("audit.chain_supersedes") == 2
    assert out["frozen_unchanged"] is True
    assert out["retired_identity_exists"] is True
    assert out["stamped_after"] is False


def test_healthy_chain_is_refused_and_untouched(tmp_path):
    out = _run(tmp_path, "healthy")
    assert out["refused"] is True
    assert "still the live chain" in out["reason"]
    assert out["identity_after"] == out["anchored_genesis"]
