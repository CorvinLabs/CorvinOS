"""run-all-tests.sh protected-chain tripwire: whose instance id is "live".

The tripwire snapshots every reachable live chain and fails the run when a
record written meanwhile carries an instance id other than the install's own.
It took "own" from the chain TAIL — so when a foreign writer (an earlier
leaking run) appended last, its id became "live": further leaks with that id
passed and the real service's records were flagged. The own id is now read
from ``<home>/global/instance_id.json`` (as the root conftest does); the tail
is only the fallback when that file is missing.

These tests execute the real embedded Python extracted from the script.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "corvin_operator" / "bridges" / "run-all-tests.sh"


def _checker() -> str:
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", SCRIPT.read_text(), re.S)
    assert m, "protected-chain checker not found in run-all-tests.sh"
    return m.group(1)


def _home(tmp_path: Path, *, own: str | None, tail: str) -> tuple[Path, Path]:
    home = tmp_path / "home"
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    chain.write_text(json.dumps({"event_type": "x", "instance_id": tail}) + "\n")
    if own:
        (home / "global").mkdir(parents=True)
        (home / "global" / "instance_id.json").write_text(json.dumps({"instance_id": own}))
    return home, chain


def _run(mode: str, snap: Path, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-", mode, str(snap), str(home)],
                          input=_checker(), capture_output=True, text=True)


def _append(chain: Path, iid: str) -> None:
    with open(chain, "a") as fh:
        fh.write(json.dumps({"event_type": "y", "instance_id": iid}) + "\n")


def test_foreign_tail_does_not_become_live(tmp_path):
    home, chain = _home(tmp_path, own="OWN", tail="FOREIGN")
    snap = tmp_path / "snap.json"
    assert _run("before", snap, home).returncode == 0
    assert json.loads(snap.read_text())[str(home.resolve())]["live"] == "OWN"
    _append(chain, "FOREIGN")
    r = _run("after", snap, home)
    assert r.returncode == 1 and "FOREIGN" in r.stderr, r


def test_own_service_append_passes(tmp_path):
    home, chain = _home(tmp_path, own="OWN", tail="FOREIGN")
    snap = tmp_path / "snap.json"
    _run("before", snap, home)
    _append(chain, "OWN")
    r = _run("after", snap, home)
    assert r.returncode == 0, r


def test_tail_is_the_fallback_without_instance_file(tmp_path):
    home, chain = _home(tmp_path, own=None, tail="TAILID")
    snap = tmp_path / "snap.json"
    _run("before", snap, home)
    assert json.loads(snap.read_text())[str(home.resolve())]["live"] == "TAILID"
