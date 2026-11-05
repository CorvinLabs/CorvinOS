"""E2E: the Forge console panel's tabs serve the tenant's real Forge data.

Every request goes over the real ASGI transport through the ``/v1/console/forge``
mount (``client`` fixture, tests/fixtures_console.py). The data is SEEDED into the
test's sandboxed ``CORVIN_HOME`` in the exact on-disk layouts the routes read:

* Tools: ``<tenant>/global/forge/registry.json`` (name → entry).
* Skills: one DIRECTORY per skill, ``<tenant>/global/skill-forge/skills/<name>/``
  with ``meta.json`` — never a skill ``registry.json`` (d3dbe9baa, ADR-0855: that
  file never existed, which is why the tab once showed 0 skills).

This file used to read the operator's LIVE ``~/.corvin`` registries and call the
handlers directly; since the test harness sandboxes ``CORVIN_HOME`` it saw nothing.
It also asserted behaviour d3dbe9baa removed on purpose: tool enable/disable
"succeeded" while persisting nothing (now an honest 501), and the OS-Skill guard
compared against fake ids such as ``audit-gate`` (now: unknown id → 404, real
compliance-tier Skills are refused by the registry itself).
"""
from __future__ import annotations

import json

import pytest

from core.console.corvin_console import _bootstrap

BASE = "/v1/console/forge"
TID = "_default"

TOOLS = {
    "browser_control": {"name": "browser_control", "description": "Drive a browser",
                        "version": "1.0.0", "runtime": "python",
                        "input_schema": {"properties": {"url": {}, "action": {}}}},
    "send_email": {"name": "send_email", "description": "Send an e-mail",
                   "version": "1.0.0", "runtime": "python"},
    "sql_query": {"name": "sql_query", "description": "Run a read-only SQL query",
                  "version": "0.2.0", "runtime": "python"},
}

SKILLS = {
    "os.delegation_router": {
        "description": "Routes a turn to the right engine", "type": "learned-experience",
        "grades": [{"run_id": f"r{i}", "score": s, "ts": "2026-10-01T00:00:00Z", "notes": ""}
                   for i, s in enumerate((0.92, 0.88, 0.95, 0.91))],
    },
    "assistant.code_reviewer": {"description": "Reviews code changes", "type": "workflow"},
    "assistant.e2e_wiring_proof": {"description": "Proves a new entry point is reachable",
                                   "type": "learned-experience"},
}


@pytest.fixture
def seeded(client):
    """Seed the sandboxed tenant with a Tools registry and three skill directories."""
    global_dir = _bootstrap.forge_paths.tenant_global_dir(TID)
    reg = global_dir / "forge" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps(TOOLS), encoding="utf-8")
    skills_dir = global_dir / "skill-forge" / "skills"
    for name, meta in SKILLS.items():
        d = skills_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"name": name, "scope": "user", **meta}),
                                     encoding="utf-8")
        (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return client


class TestForgeToolsTab:
    def test_list_tools_returns_the_seeded_registry(self, seeded):
        data = seeded.get(f"{BASE}/tools").json()
        by_name = {t["name"]: t for t in data["tools"]}
        assert set(TOOLS) <= set(by_name)
        assert by_name["browser_control"]["param_count"] == 2
        assert by_name["browser_control"]["scope_source"] == "user"
        for tool in data["tools"]:
            assert {"id", "name", "enabled", "version", "runtime", "description"} <= set(tool)

    @pytest.mark.parametrize("verb", ["enable", "disable"])
    def test_tool_toggle_is_an_honest_501(self, seeded, verb):
        response = seeded.post(f"{BASE}/tools/browser_control/{verb}")
        assert response.status_code == 501
        assert "not implemented" in response.json()["detail"]


class TestForgeSkillsTab:
    def test_list_skills_reads_the_skill_directories(self, seeded):
        data = seeded.get(f"{BASE}/skills").json()
        names = {s["name"] for s in data["skills"]}
        assert set(SKILLS) <= names
        assert data["capabilities"] == {"enable_disable": False, "rollback": False,
                                        "versioning": False}

    def test_learning_state_comes_from_real_grades(self, seeded):
        skills = {s["name"]: s for s in seeded.get(f"{BASE}/skills").json()["skills"]}
        state = skills["os.delegation_router"]["learning_state"]
        assert state["feedback_count"] == 4
        assert state["confidence"] == pytest.approx((0.92 + 0.88 + 0.95 + 0.91) / 4, abs=1e-3)
        assert skills["assistant.code_reviewer"]["learning_state"] is None


class TestForgeOSSkillsTab:
    def test_list_os_skills_structure(self, seeded):
        data = seeded.get(f"{BASE}/os-skills").json()
        assert {"os_skills", "count", "tenant_id", "timestamp"} <= set(data)
        assert isinstance(data["os_skills"], list)

    def test_disabling_an_unregistered_os_skill_is_404(self, seeded):
        assert seeded.post(f"{BASE}/os-skills/audit-gate/disable").status_code == 404


class TestForgeGraphTab:
    def test_graph_has_a_node_per_tool_and_skill(self, seeded):
        data = seeded.get(f"{BASE}/graph").json()
        ids = {n["id"] for n in data["nodes"]}
        assert {f"tool:{n}" for n in TOOLS} <= ids
        assert {f"skill:{n}" for n in SKILLS} <= ids
        assert {n["type"] for n in data["nodes"]} <= {"tool", "skill", "os-skill"}


class TestForgeAuditTab:
    def test_audit_endpoint_structure(self, seeded):
        response = seeded.get(f"{BASE}/audit")
        assert response.status_code == 200, response.text[:300]
        data = response.json()
        assert {"events", "count", "tenant_id", "timestamp", "complete"} <= set(data)
        for event in data["events"]:
            assert {"id", "timestamp", "type", "resource_type", "resource_id", "action"} <= set(event)


class TestForgeSearch:
    def test_search_finds_tools(self, seeded):
        results = seeded.get(f"{BASE}/search", params={"q": "browser"}).json()["results"]
        assert any(r["type"] == "tool" and r["id"] == "browser_control" for r in results)

    def test_search_finds_skills(self, seeded):
        results = seeded.get(f"{BASE}/search", params={"q": "router"}).json()["results"]
        assert any(r["type"] == "skill" and r["id"] == "os.delegation_router" for r in results)

    def test_search_deduplicates_results(self, seeded):
        results = seeded.get(f"{BASE}/search", params={"q": "re"}).json()["results"]
        keys = [(r["type"], r["id"]) for r in results]
        assert keys and len(keys) == len(set(keys))

    def test_one_character_query_is_400(self, seeded):
        assert seeded.get(f"{BASE}/search", params={"q": "e"}).status_code == 400
