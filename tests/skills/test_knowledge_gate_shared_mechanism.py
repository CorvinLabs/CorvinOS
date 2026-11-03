"""ADR Gate and Concept Gate write through ONE mechanism (kb.py new) and are judged by ONE
rule source (kb check) — the block that says so is byte-identical in both skills.

The skills are read through `skill_inject._load_core_quality_skills`, the loader that puts
them into every turn (not by opening the files), so a test that passes here means the
injected text carries the mechanism.

Before 2026-10-26 both skills told an agent to pick the next number with
`ls decisions/ | grep -E '^[0-9]{4}' | tail -1` (canonical names are `ADR-NNNN-...`, so the
scan saw only legacy files: it returned 0468 while the corpus was at ADR-2301) and to commit
into the frozen Corvin-ADR repository; quality-layer-control imported `operator.bridges...`,
which is the standard library, so every one of its commands failed.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from corvin_operator.bridges.shared import skill_inject

BEGIN = "<!-- knowledge-gate-mechanism:begin -->"
END = "<!-- knowledge-gate-mechanism:end -->"
REPO = Path(__file__).resolve().parents[2]


def _injected() -> dict[str, str]:
    return {spec.name: spec.body for _scope, spec in skill_inject._load_core_quality_skills()}


def _block(body: str) -> str:
    assert body.count(BEGIN) == 1 and body.count(END) == 1, "exactly one shared block"
    return body[body.index(BEGIN):body.index(END) + len(END)]


def test_both_gates_are_injected_and_carry_the_identical_shared_mechanism():
    bodies = _injected()
    assert {"adr_gate", "concept_gate"} <= set(bodies), sorted(bodies)   # positive control
    adr, concept = _block(bodies["adr_gate"]), _block(bodies["concept_gate"])
    assert adr == concept, "the shared knowledge-gate block drifted between the two skills"
    for needle in ("kb.py new decision", "new concept", "kb.py check", "blocking: 0",
                   "/console/app/quality", "kb.py new task", "kb.py task T-NNNN"):
        assert needle in adr, needle


def test_the_injected_gates_name_this_tenants_kb_not_the_maintainers_checkout(tmp_path, monkeypatch):
    """ADR-2265: every install has its own KB at <CORVIN_HOME>/tenants/<tenant>/knowledge. The gate
    skills used to hard-code /home/shumway/projects/Corvin-Knowledge, which exists on one machine."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    monkeypatch.delenv("CORVIN_KB_REPO", raising=False)
    want = (tmp_path / "home" / "tenants" / "acme" / "knowledge" / "scripts" / "kb.py").as_posix()
    for name in ("adr_gate", "concept_gate"):
        body = _injected()[name]
        assert "/home/shumway" not in body, name
        assert "{{" not in _block(body), name               # every placeholder rendered
        assert f" {want} new decision" in body, name        # the exact path the path gate pins
    monkeypatch.setenv("CORVIN_KB_REPO", str(tmp_path / "operator-kb"))   # explicit override, kb tenant only
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    assert (tmp_path / "operator-kb" / "scripts" / "kb.py").as_posix() in _injected()["adr_gate"]


def test_no_gate_skill_hand_numbers_or_commits_to_the_frozen_repo():
    for name, body in _injected().items():
        if name not in ("adr_gate", "concept_gate"):
            continue
        outside = body.replace(_block(body), "")   # the block itself names the anti-patterns
        assert not re.search(r"grep -E '\^\[0-9\]\{4\}'", outside), name
        assert "cd ../Corvin-ADR" not in outside and "Commit in the Corvin-ADR" not in outside, name
        assert "find the next number" not in outside, name


def test_quality_layer_control_commands_import_a_module_that_exists():
    body = (REPO / "corvin_operator/bundle/skills/quality-layer-control/SKILL.md").read_text(encoding="utf-8")
    modules = set(re.findall(r"from ([\w.]+) import", body))
    assert modules, "positive control: the skill documents import commands"
    assert not any(m == "operator" or m.startswith("operator.") for m in modules), modules
    for m in modules:
        assert importlib.util.find_spec(m) is not None, f"{m} does not resolve"
