"""scripts/recovery/rebuild_from_carve.py — driven through its real CLI (ADR-2058).

Regression: the script read the instance id from ``<home>/instance_id``, a file
``instance_identity`` never writes (it keeps ``<home>/global/instance_id.json``),
so ``--apply`` on an ordinary install died with FileNotFoundError before
admitting a single record; and it composed the tenant chain path by hand.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "recovery" / "rebuild_from_carve.py"

_WRITE = r'''
import sys
sys.path.insert(0, sys.argv[1])
from forge import paths, security_events as se
p = paths.tenant_audit_chain("_default")
p.parent.mkdir(parents=True, exist_ok=True)
for i in range(4):
    se.write_event(p, "audit.chain_gap_detected", details={"expected_prev": str(i)})
print(p)
'''


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({
        "CORVIN_HOME": str(home),
        "CORVIN_TENANT_ID": "_default",
        "HOME": str(home / "h"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(home / "keys" / "audit_anchor.key"),
        "VOICE_CONFIG_DIR": str(home / "voicecfg"),
    })
    env.pop("CORVIN_INSTANCE_ID_PATH", None)
    return env


def test_apply_without_instance_id_flag_uses_instance_identity(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    out = subprocess.run(
        [sys.executable, "-c", _WRITE, str(_REPO / "corvin_operator" / "forge")],
        env=env, capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr[-2000:]
    chain = Path(out.stdout.strip().splitlines()[-1])
    lines = chain.read_text().splitlines()
    assert len(lines) == 4
    assert (home / "global" / "instance_id.json").is_file()
    assert not (home / "instance_id").exists()

    carve = tmp_path / "raw.jsonl"
    carve.write_text("\n".join(lines) + "\n")
    run = subprocess.run(
        [sys.executable, str(_SCRIPT), "--carve", str(carve), "--apply"],
        env=env, capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    assert run.returncode == 0, (run.stdout + run.stderr)[-3000:]
    assert "kept=1 (4 records)" in run.stdout, run.stdout

    carved = sorted((chain.parent / "recovered").glob("audit.carved-*.jsonl"))
    assert len(carved) == 1
    assert len(carved[0].read_text().splitlines()) == 4
    seams = [json.loads(l) for l in chain.read_text().splitlines()
             if '"audit.chain_supersedes"' in l]
    assert seams and seams[-1]["details"].get("seam_reason") == "chain_loss_carve"
    # The carved history is reachable by the seam-traversing console readers.
    sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))
    from forge import security_events as se  # noqa: PLC0415
    assert carved[0] in se.chain_history_files(chain)
