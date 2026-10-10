"""Boot E2E: every builtin OS Skill is reachable through the booted registry and audited.

``boot_skills`` -> ``registry.execute`` -> audit record, for each id in
``BUILTIN_SKILL_IDS``. The registry is the single audit point for Skill runs (a Skill
need not emit by itself), so the invariant under test is: whatever a Skill does with
the input -- success, its own error, a refusal -- the run leaves a record carrying its
skill id and the tenant. A Skill that registers but is never audited fails here.

Also pins MANIFEST.json against the boot set: a skill declared in the manifest must
either boot (``BUILTIN_SKILL_IDS``) or be listed in ``OPTIONAL_SKILLS``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.skills.boot import boot_skills
from core.skills.os_skills_phase1 import BUILTIN_SKILL_IDS, OPTIONAL_SKILLS
from core.skills.skill_registry_phase1 import get_registry

REPO = Path(__file__).resolve().parents[2]
TENANT = "_default"
LOM = "tests/skills/test_builtin_skills_boot_audit_e2e.py:test_every_builtin_skill_run_is_audited"


@pytest.fixture()
def booted(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path / "forge"))
    records: list[tuple[str, dict]] = []

    def _spy(event_type: str, details: dict) -> bool:
        records.append((event_type, dict(details)))
        return True

    ids = boot_skills(tenant_id=TENANT, audit_emit=_spy, wire_learning=False)
    return ids, records


def test_all_builtin_ids_boot(booted):
    ids, _ = booted
    assert set(BUILTIN_SKILL_IDS) <= set(ids)


@pytest.mark.parametrize("skill_id", BUILTIN_SKILL_IDS)
def test_every_builtin_skill_run_is_audited(booted, skill_id):
    _, records = booted
    before = len(records)
    # An empty input is deliberately not valid for most Skills: the point is that the
    # registry records the run whatever the Skill does with it.
    get_registry().execute(skill_id, {}, timeout_ms=3000, lom=LOM, tenant_id=TENANT)
    new = records[before:]
    mine = [(t, d) for t, d in new if d.get("skill_id") == skill_id]
    assert mine, f"{skill_id}: execute() left no audit record (saw {[t for t, _ in new]})"
    assert all(d.get("tenant_id") == TENANT for _, d in mine), f"{skill_id}: tenant missing"


def test_manifest_skills_are_booted_or_declared_optional():
    manifest = json.loads((REPO / "core/skills/os_skills/MANIFEST.json").read_text())
    declared = {s["id"] for s in manifest["skills"]}
    unaccounted = declared - set(BUILTIN_SKILL_IDS) - set(OPTIONAL_SKILLS)
    assert not unaccounted, f"in MANIFEST but neither booted nor optional: {sorted(unaccounted)}"
    assert not (set(BUILTIN_SKILL_IDS) & set(OPTIONAL_SKILLS)), "an id is both booted and optional"
