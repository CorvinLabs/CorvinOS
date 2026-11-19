"""R3-SEC-5: removing the chain, its identity record and its witness together.

Round 2 made the witness a second path-keyed proof that a chain existed. A
witness is only written by the first verification, and an attacker who can
delete two files beside the anchor key can delete three: what was left were
genesis-keyed markers, which the tripwire can only warn about, so the next boot
started a fresh chain.

Every chained append now stores ``path_sha`` (sha256 of the chain file's
resolved path) in its ``chain_tails/`` record, and the tripwire reads a record
naming the absent path as ``chain_missing``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
FORGE = REPO / "corvin_operator" / "forge"


def _run(code: str, tmp_path: Path) -> dict:
    home = tmp_path / "home"
    keys = tmp_path / "keys"
    keys.mkdir(exist_ok=True)
    key = keys / "audit_anchor.key"
    if not key.exists():
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)
    env = {k: v for k, v in os.environ.items()
           if k not in ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_TENANT_ID")}
    env.update({
        "CORVIN_HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(key),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "corvin_operator"), str(FORGE),
                                       str(SHARED), str(REPO / "core" / "compliance")]),
    })
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(_PRELUDE) + textwrap.dedent(code)],
                          env=env, capture_output=True, text=True, timeout=180, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


_PRELUDE = """
    import json, os
    from pathlib import Path
    from forge import security_events as se
    from forge import paths as fp
    from corvin_compliance_reports import tripwire as tw
    KD = Path(os.environ["CORVIN_AUDIT_ANCHOR_KEY"]).parent
    chain = Path(fp.tenant_audit_chain("_default"))
    chain.parent.mkdir(parents=True, exist_ok=True)

    def wipe(sub):
        d = KD / sub
        if d.exists():
            for p in d.iterdir():
                p.unlink()

    def verdict():
        r = tw.audit_chain_intact()
        return [bool(r.ok), str(r.detail)]
"""


def test_tail_record_names_its_chain_path(tmp_path):
    out = _run("""
        se.write_event(chain, "tool.created", details={"sha": "x" * 8})
        docs = [json.loads(p.read_text()) for p in (KD / "chain_tails").iterdir()]
        print(json.dumps({"docs": docs, "want": se._chain_tail_path_sha(chain),
                          "n": se.chain_tail_records_for_path(chain),
                          "other": se.chain_tail_records_for_path(chain.with_name("other.jsonl"))}))
    """, tmp_path)
    assert len(out["docs"]) == 1
    assert out["docs"][0]["path_sha"] == out["want"]
    assert len(out["want"]) == 64
    assert out["n"] == 1
    assert out["other"] == 0


def test_chain_identity_and_witness_removed_still_refuses_boot(tmp_path):
    """Scenarios A-D of the reviewer's repro."""
    out = _run("""
        for _ in range(3):
            se.write_event(chain, "tool.created", details={"sha": "x" * 8})
        res = {"start": verdict()}
        chain.unlink()
        res["A"] = verdict()
        wipe("chain_ids")
        res["B"] = verdict()
        wipe("chain_witness")
        res["C"] = verdict()
        res["left"] = sorted(p.parent.name for p in KD.rglob("*") if p.is_file())
        print(json.dumps(res))
    """, tmp_path)
    assert out["start"][0] is True
    for step in ("A", "B", "C"):
        assert out[step][0] is False, (step, out[step])
        assert "chain_missing" in out[step][1], (step, out[step])
    assert "chain_tails" in out["left"]


def test_no_witness_ever_written_is_still_caught(tmp_path):
    """A chain that was appended to but never verified has no witness; the
    tail record alone must carry the proof."""
    out = _run("""
        se.write_event(chain, "tool.created", details={"sha": "x" * 8})
        had_witness = (KD / "chain_witness").exists() and any((KD / "chain_witness").iterdir())
        chain.unlink()
        wipe("chain_ids")
        print(json.dumps({"had_witness": had_witness, "v": verdict()}))
    """, tmp_path)
    assert out["had_witness"] is False
    assert out["v"][0] is False and "chain_missing" in out["v"][1]


def test_legacy_tail_record_without_path_sha_only_warns(tmp_path):
    out = _run("""
        se.write_event(chain, "tool.created", details={"sha": "x" * 8})
        for p in (KD / "chain_tails").iterdir():
            doc = json.loads(p.read_text())
            doc.pop("path_sha")
            p.write_text(json.dumps(doc))
        chain.unlink()
        wipe("chain_ids")
        wipe("chain_witness")
        print(json.dumps({"v": verdict()}))
    """, tmp_path)
    assert out["v"][0] is True
    assert "marker(s) of other chains" in out["v"][1]


def test_tail_record_of_another_chain_does_not_refuse_a_new_tenant(tmp_path):
    """One key directory serves every chain of the host: tenant B's first boot
    must not be refused because tenant A has a tail record."""
    out = _run("""
        other = Path(fp.tenant_audit_chain("acme"))
        other.parent.mkdir(parents=True, exist_ok=True)
        se.write_event(other, "tool.created", details={"sha": "x" * 8})
        print(json.dumps({"exists": chain.exists(), "v": verdict()}))
    """, tmp_path)
    assert out["exists"] is False
    assert out["v"][0] is True, out["v"]


def test_fresh_install_still_boots(tmp_path):
    out = _run("""
        print(json.dumps({"v": verdict()}))
    """, tmp_path)
    assert out["v"] == [True, "no chain yet (fresh install)"]
