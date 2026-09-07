"""A superseded chain stays REACHABLE and VERIFIABLE from the canonical one.

The six chain files R4 found are append-only and hash-chained. Merging,
reordering or deleting them would destroy the exact integrity property the trail
exists for, so convergence is forward-only: every writer now resolves one path,
and a chained ``audit.chain_supersedes`` seam in the canonical chain names each
historical file's path key, genesis and FINAL TAIL HASH.

These tests drive the real ``record_chain_supersession`` /
``chain_seam_links`` / ``verify_chain`` and the real boot tripwire, in an
isolated ``CORVIN_HOME``, and assert an auditor can walk canonical → sibling,
verify the sibling, and walk back.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "operator" / "forge"))


@pytest.fixture()
def se(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    import forge.security_events as mod
    return mod


def _seed(se, path: Path, n: int, prefix: str = "e") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        se.write_event(path, "tool.created", tool=f"{prefix}{i}", details={"count": i})


def test_seam_links_canonical_to_sibling_and_back(se, tmp_path):
    home = tmp_path / "home"
    canonical = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    sibling = home / "global" / "forge" / "audit.jsonl"      # the pre-ADR-0007 location
    _seed(se, sibling, 5, "legacy")
    _seed(se, canonical, 2, "new")

    sibling_tail = se._read_chain_tail(sibling)
    rec = se.record_chain_supersession(canonical, sibling)
    assert rec is not None and rec["event_type"] == se.CHAIN_SEAM_EVENT

    # FORWARD: an auditor reading the canonical chain finds the sibling.
    fwd = [l for l in se.chain_seam_links(canonical) if l["direction"] == "supersedes"]
    assert len(fwd) == 1, fwd
    link = fwd[0]
    assert link["key"] == se.chain_path_key(sibling)
    assert link["records"] == 5
    assert sibling_tail.startswith(link["tail"]) or link["tail"].startswith(sibling_tail[:32])

    # ...and can VERIFY it: the recorded tail is the sibling's real tail, and
    # the sibling's own chain still holds end to end.
    ok, problems = se.verify_chain(sibling)
    assert ok, problems

    # BACKWARD: an auditor who starts from the sibling is led to the canonical.
    back = [l for l in se.chain_seam_links(sibling) if l["direction"] == "superseded_by"]
    assert len(back) == 1, back
    assert back[0]["key"] == se.chain_path_key(canonical)
    assert Path(back[0]["path"]) == canonical.resolve()


def test_seam_never_mutates_either_chain(se, tmp_path):
    """The sibling is frozen, not merged: byte-identical after the seam."""
    home = tmp_path / "home"
    canonical = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    sibling = home / "tenants" / "_default" / "audit.jsonl"
    _seed(se, sibling, 4)
    _seed(se, canonical, 1)
    before = sibling.read_bytes()
    can_lines_before = len(canonical.read_text().splitlines())

    se.record_chain_supersession(canonical, sibling)

    assert sibling.read_bytes() == before, "a seam must never append to the superseded chain"
    assert len(canonical.read_text().splitlines()) == can_lines_before + 1
    ok, problems = se.verify_chain(canonical)
    assert ok, problems


def test_seam_is_idempotent(se, tmp_path):
    home = tmp_path / "home"
    canonical = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    sibling = home / "forge" / "audit.jsonl"
    _seed(se, sibling, 3)
    _seed(se, canonical, 1)
    assert se.record_chain_supersession(canonical, sibling) is not None
    assert se.record_chain_supersession(canonical, sibling) is None, "re-seamed unchanged sibling"
    # A sibling that MOVED (a writer we have not converged is still appending)
    # is worth recording again.
    _seed(se, sibling, 1, "more")
    assert se.record_chain_supersession(canonical, sibling) is not None


def test_seam_skips_a_compat_symlink_and_an_empty_file(se, tmp_path):
    home = tmp_path / "home"
    canonical = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    _seed(se, canonical, 2)
    # one file, two names — the ADR-0007 backward-compat symlink shape
    alias_dir = home / "global"
    alias_dir.parent.mkdir(parents=True, exist_ok=True)
    if alias_dir.exists():  # a prior seed created it as a real dir
        import shutil; shutil.rmtree(alias_dir)
    alias_dir.symlink_to(home / "tenants" / "_default" / "global", target_is_directory=True)
    assert se.record_chain_supersession(canonical, home / "global" / "forge" / "audit.jsonl") is None
    empty = home / "forge" / "audit.jsonl"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.touch()
    assert se.record_chain_supersession(canonical, empty) is None


def test_boot_tripwire_names_the_split_and_writes_the_seams(se, tmp_path, monkeypatch):
    """Through the REAL tripwire check, not a hand-rolled reimplementation."""
    sys.path.insert(0, str(REPO))
    home = tmp_path / "home"
    canonical = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    _seed(se, canonical, 2)
    for legacy in (home / "global" / "forge" / "audit.jsonl",
                   home / "tenants" / "_default" / "audit.jsonl"):
        _seed(se, legacy, 3)

    import importlib
    from core.compliance.corvin_compliance_reports import tripwire as tw
    importlib.reload(tw)
    res = tw._check_audit_unification()

    assert "audit_chain_split" in res.detail, res.detail
    assert "2 seam record(s)" in res.detail, res.detail
    links = {l["key"] for l in se.chain_seam_links(canonical) if l["direction"] == "supersedes"}
    assert links == {se.chain_path_key(home / "global" / "forge" / "audit.jsonl"),
                     se.chain_path_key(home / "tenants" / "_default" / "audit.jsonl")}
    ok, problems = se.verify_chain(canonical)
    assert ok, problems


def test_unified_install_reports_no_split(se, tmp_path):
    sys.path.insert(0, str(REPO))
    home = tmp_path / "home"
    _seed(se, home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl", 2)
    import importlib
    from core.compliance.corvin_compliance_reports import tripwire as tw
    importlib.reload(tw)
    res = tw._check_audit_unification()
    assert res.ok and "unified" in res.detail, res.detail
