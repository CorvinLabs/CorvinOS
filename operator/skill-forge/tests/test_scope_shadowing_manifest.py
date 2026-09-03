"""SkillForge registry contracts (adversarial review D-06, D-07a, D-11, D-18).

* D-06  higher scope SHADOWS lower: a task-scope copy never replaces the
        user-scope body returned by get/get_body/list_with_scope
* D-07a every scope's events land in the TENANT CORE CHAIN
        (<tenant_home>/global/forge/audit.jsonl) and the chain verifies
* D-11  SkillRegistry._save emits the ADR-0420 manifest.json that
        core.skills.corvin_skills.resolver reads — a skill created through
        the registry resolves via SkillDependencyResolver(base_path=<tenant_home>)
        and a later write is seen WITHOUT an explicit invalidate()
* D-18  promote() emits ONE skill.promote event and re-grades nothing
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SKILL_FORGE = _HERE.parents[1]
_REPO = _HERE.parents[3]
for _p in (str(_SKILL_FORGE), str(_REPO / "operator" / "forge"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from skill_forge.multi_registry import MultiSkillRegistry  # noqa: E402
from skill_forge.registry import SkillRegistry  # noqa: E402
from forge.security_events import verify_chain  # noqa: E402

_SLOT_TMP = tempfile.mkdtemp(prefix="sf-shadow-slot-")
os.environ["CORVIN_PLUGIN_SLOT_DIR"] = _SLOT_TMP

BODY_USER = (
    "# curated.skill\n\nThe curated user-scope body: five checks, a summary "
    "line, and an explicit stop condition so the reviewer knows when to end.\n"
)
BODY_TASK = (
    "# curated.skill\n\nThrowaway task-scope body written during one task; "
    "must never shadow the curated copy at injection time.\n"
)


@pytest.fixture
def env(tmp_path):
    prev = {k: os.environ.get(k) for k in (
        "CORVIN_HOME", "CORVIN_FORCE_SCOPE", "CORVIN_DEFAULT_SCOPE",
        "CORVIN_CHANNEL_ID", "CORVIN_TASK_ID", "CORVIN_PROJECT_ROOT", "CORVIN_TENANT_ID",
    )}
    for k in prev:
        os.environ.pop(k, None)
    os.environ["CORVIN_HOME"] = str(tmp_path)
    os.environ["CORVIN_PROJECT_ROOT"] = str(tmp_path)   # never the live repo registry
    tid = f"sf-shadow-{uuid.uuid4().hex[:8]}"
    yield tmp_path, tid
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    p = Path("/tmp/.corvin/tasks") / tid
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def _chain(home: Path) -> Path:
    return home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"


def _events(home: Path) -> list[dict]:
    p = _chain(home)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_higher_scope_shadows_lower(env):
    home, tid = env
    mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
    mr.create(scope="user", name="curated.skill", type="domain",
              body_md=BODY_USER, description="curated", claim={})
    mr.create(scope="task", name="curated.skill", type="domain",
              body_md=BODY_TASK, description="throwaway", claim={})

    assert mr.find_scope("curated.skill") == "user"
    assert mr.get("curated.skill").description == "curated"
    assert "curated user-scope body" in mr.get_body("curated.skill")
    assert "Throwaway" not in mr.get_body("curated.skill")
    scoped = {spec.name: scope for scope, spec in mr.list_with_scope()}
    assert scoped["curated.skill"] == "user"
    assert [s.name for s in mr.list()].count("curated.skill") == 1
    # purge without a scope pin removes the copy get() returns (the highest)
    assert mr.delete("curated.skill", reason="test") is True
    assert mr.find_scope("curated.skill") == "task"


def test_audit_lands_in_tenant_core_chain_for_every_scope(env):
    home, tid = env
    mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
    assert mr.audit_path() == _chain(home)
    for scope in ("task", "session", "project", "user"):
        mr.create(scope=scope, name=f"a.{scope}", type="domain",
                  body_md=BODY_USER, description="d", claim={})
        mr.grade(f"a.{scope}", "r1", 0.9)
    ev = _events(home)
    created = {e["tool"] for e in ev if e["event_type"] == "skill.create"}
    assert created == {"a.task", "a.session", "a.project", "a.user"}
    assert {e["details"]["scope"] for e in ev if e["event_type"] == "skill.create"} == \
        {"task", "session", "project", "user"}
    # nothing written to the old per-scope sibling files
    assert not (home / "tenants" / "_default" / "audit.jsonl").exists()
    assert not (home / "tenants" / "_default" / "sessions" / "ch" / "audit.jsonl").exists()
    ok, problems = verify_chain(_chain(home))
    assert ok, problems


def test_registry_emits_resolver_manifest_and_resolver_sees_writes(env):
    home, tid = env
    from core.skills.corvin_skills.resolver import SkillDependencyResolver

    mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
    tenant_home = home / "tenants" / "_default"
    resolver = SkillDependencyResolver(tenant_id="_default", base_path=tenant_home)
    assert resolver.resolve("res.skill") is None

    spec = mr.create(scope="user", name="res.skill", type="domain",
                     body_md=BODY_USER, description="resolvable", claim={})
    manifest = tenant_home / "skill-forge" / "manifest.json"
    assert manifest.exists(), "SkillRegistry._save must emit the ADR-0420 manifest"
    data = json.loads(manifest.read_text())
    assert [s["name"] for s in data["skills"]] == ["res.skill"]

    entry = resolver.resolve("res.skill")          # no explicit invalidate()
    assert entry is not None
    assert entry["metadata"]["description"] == "resolvable"
    assert entry["metadata"]["sha256"] == spec.sha256
    assert entry["metadata"]["n_grades"] == 0

    mr.grade("res.skill", "r1", 0.8)               # another write, same process
    entry2 = resolver.resolve("res.skill")
    assert entry2["metadata"]["n_grades"] == 1
    assert entry2["metadata"]["mean_score"] == 0.8

    mr.delete("res.skill", reason="test")
    assert resolver.resolve("res.skill") is None


def test_promote_emits_one_event_and_copies_grades(env):
    home, tid = env
    mr = MultiSkillRegistry(channel_id="ch", task_id=tid)
    mr.create(scope="task", name="pr.skill", type="domain",
              body_md=BODY_USER, description="d", claim={})
    mr.grade("pr.skill", "r1", 0.7)
    mr.grade("pr.skill", "r2", 0.9)
    before = _events(home)
    grades_before = sum(1 for e in before if e["event_type"] == "skill.grade")

    spec = mr.promote("pr.skill", to="session")
    assert mr.find_scope("pr.skill") == "session"
    assert spec.n_grades == 2
    assert [g["run_id"] for g in spec.grades] == ["r1", "r2"]

    after = _events(home)
    assert sum(1 for e in after if e["event_type"] == "skill.grade") == grades_before
    promotes = [e for e in after if e["event_type"] == "skill.promote"]
    assert len(promotes) == 1
    assert promotes[0]["tool"] == "pr.skill"
    assert promotes[0]["details"]["from_scope"] == "task"
    assert promotes[0]["details"]["to_scope"] == "session"
    assert promotes[0]["details"]["n_grades"] == 2
    ok, problems = verify_chain(_chain(home))
    assert ok, problems


def test_standalone_registry_keeps_sibling_audit_default(tmp_path):
    r = SkillRegistry(tmp_path / "skill-forge")
    assert r.audit_path() == tmp_path / "audit.jsonl"
    assert (tmp_path / "skill-forge" / "manifest.json").exists()
