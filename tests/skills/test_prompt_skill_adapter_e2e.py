"""ADR-2175 / T-0103 — prompt skills are registry citizens, through the real boot path.

``boot_skills`` -> ``registry.execute("prompt.<name>")`` -> audit record. A SkillForge
skill is created through the real ``MultiSkillRegistry`` (the same one the injector
reads), not placed on disk by the test, so the adapter is proven against the real
source of truth. The audit spy is the ``audit_emit`` boot hands the registry — the
callable that reaches the core hash chain in production.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from core.skills.boot import boot_skills
from core.skills.prompt_skill_adapter import (
    collect_prompt_sources, prompt_skill_id, sync_prompt_skills)
from core.skills.skill_registry_phase1 import get_registry

REPO = Path(__file__).resolve().parents[2]
TENANT = "_default"
LOM = "tests/skills/test_prompt_skill_adapter_e2e.py:test_prompt_skill_run_is_audited"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path / "forge"))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    for d in (REPO / "corvin_operator" / "skill-forge", REPO / "corvin_operator" / "forge"):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
    records: list[tuple[str, dict]] = []

    def spy(event_type: str, details: dict) -> bool:
        records.append((event_type, dict(details)))
        return True

    return records, spy


def _forge_skill(name: str, body: str):
    from skill_forge.multi_registry import MultiSkillRegistry
    reg = MultiSkillRegistry(tenant_id=TENANT, caller_persona="assistant")
    # session scope lives under CORVIN_HOME (sandboxed); "project" resolves to the
    # repo-local LIVE .corvin and must never be written by a test.
    assert str(reg._root_for("session")).startswith(os.environ["CORVIN_HOME"])
    reg.create(scope="session", name=name, type="domain", body_md=body,
               description=f"{name} test skill", created_by="test")
    return reg


def _migrated(records):
    return [d for t, d in records if t == "skill.migrated"]


def test_boot_registers_the_bundle_prompt_skills(env):
    records, spy = env
    boot_skills(tenant_id=TENANT, audit_emit=spy, wire_learning=False)
    ids = {m.id for m in get_registry().list_skills()}
    bundle = [s for s in collect_prompt_sources(TENANT) if s.scope == "bundle"]
    assert len(bundle) >= 10, "the shipped bundle has 15 SKILL.md files"
    assert {prompt_skill_id(s.name) for s in bundle} <= ids
    assert {d["skill_id"] for d in _migrated(records)} >= {prompt_skill_id(s.name) for s in bundle}
    one = next(d for d in _migrated(records) if d["skill_id"] == prompt_skill_id("adr_gate"))
    assert one["action"] == "registered" and one["source"] == "bundle"
    assert one["tenant_id"] == TENANT and len(one["content_hash"]) == 16
    assert re.fullmatch(r"0\.0\.\d+\+[0-9a-f]{8}", one["skill_version"])


def test_prompt_skill_run_is_audited(env):
    records, spy = env
    boot_skills(tenant_id=TENANT, audit_emit=spy, wire_learning=False)
    before = len(records)
    out = get_registry().execute(prompt_skill_id("adr_gate"), {"task_text": "x"},
                                 timeout_ms=3000, lom=LOM, tenant_id=TENANT)
    assert out.status == "success" and out.output == {"decision": "inject", "mode": "bundle"}
    ran = [d for t, d in records[before:] if t == "skill.executed" and d.get("skill_id") == prompt_skill_id("adr_gate")]
    assert ran, [t for t, _ in records[before:]]
    assert ran[0]["decision"]["decision"] == "inject" and re.fullmatch(r"0\.0\.\d+\+[0-9a-f]{8}", ran[0]["skill_version"])


def test_sync_is_idempotent_and_a_changed_body_gets_the_next_version(env):
    import dataclasses
    from core.skills.prompt_skill_adapter import resolve_versions
    records, spy = env
    boot_skills(tenant_id=TENANT, audit_emit=spy, wire_learning=False)
    reg = get_registry()
    raw = [s for s in collect_prompt_sources(TENANT) if s.name == "adr_gate"]
    (src,), _, _ = resolve_versions(raw, TENANT)
    n = len(_migrated(records))
    again = sync_prompt_skills(reg, [src], tenant_id=TENANT)
    assert again.unchanged == 1 and not again.registered and len(_migrated(records)) == n
    edited = dataclasses.replace(raw[0], body=raw[0].body + "\nedited\n")
    (src2,), _, _ = resolve_versions([edited], TENANT)
    assert src2.version.startswith("0.0.2+") and src.version.startswith("0.0.1+")
    r = sync_prompt_skills(reg, [src2], tenant_id=TENANT)
    assert r.updated == [prompt_skill_id("adr_gate")]
    last = _migrated(records)[-1]
    assert last["action"] == "updated" and last["skill_version"] == src2.version
    assert reg.get(prompt_skill_id("adr_gate")).metadata.version == src2.version
    assert "edited" not in repr(records)  # the body never reaches the chain


def test_a_forged_skill_is_picked_up_from_skillforge(env):
    records, spy = env
    _forge_skill("assistant.adr2175_probe", "Probe body that must never reach the audit chain.")
    boot_skills(tenant_id=TENANT, audit_emit=spy, wire_learning=False)
    sid = prompt_skill_id("assistant.adr2175_probe")
    assert get_registry().get(sid) is not None
    rec = next(d for d in _migrated(records) if d["skill_id"] == sid)
    assert rec["source"] == "session"
    assert "must never reach" not in repr(records)


def test_a_failing_source_does_not_break_the_boot(env, monkeypatch):
    records, spy = env
    import core.skills.prompt_skill_adapter as m
    monkeypatch.setattr(m, "skillforge_sources", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    ids = boot_skills(tenant_id=TENANT, audit_emit=spy, wire_learning=False)
    assert "os.delegation_router" in ids  # builtins unaffected


def test_catalog_versions_order_by_sequence_not_text():
    from core.skills.version_manager import SemanticVersion, VersionResolver
    vs = ["0.0.9+aaaaaaaa", "0.0.10+bbbbbbbb", "0.0.2+cccccccc"]
    assert VersionResolver().resolve_version("prompt.x", vs) == "0.0.10+bbbbbbbb"
    assert str(SemanticVersion("0.0.3+abc12345")) == "0.0.3+abc12345"
