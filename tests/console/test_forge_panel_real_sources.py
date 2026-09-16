"""The Forge panel over the real console boundary (ADR-0855).

Every assertion here failed before that change: the Skills tab read a
`skill-forge/registry.json` that exists nowhere on disk and reported 0; the
OS-Skills tab was a literal `os_skills = []`; and five mutation routes
returned 200 plus an audit record for state changes that never happened.

Uses the real router, a real session cookie and real CSRF — the defect was
invisible to any test that called the handlers directly, because it lived in
which FILES the handler opened.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from learning.console_client import console_client  # noqa: E402

FORGE = "/v1/console/forge"


def _make_skill(sb, name: str, *, grades=(), scope: str = "project") -> Path:
    """Create a skill the way SkillForge stores one: a DIRECTORY."""
    from core.paths.tenant import corvin_home  # noqa: PLC0415

    d = Path(corvin_home()) / "tenants" / sb.tenant_id / "skill-forge" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {name}\n")
    (d / "meta.json").write_text(json.dumps({
        "name": name, "type": "domain", "description": f"desc of {name}",
        "scope": scope, "created_at": 1.0, "sha256": "deadbeef",
        "grades": [{"run_id": f"r{i}", "score": s, "ts": 1.0, "notes": ""}
                   for i, s in enumerate(grades)],
    }))
    return d


class TestSkillsTab:
    def test_lists_skills_from_directories_not_a_registry_json(self, tmp_path):
        with console_client(tmp_path) as sb:
            _make_skill(sb, "alpha")
            _make_skill(sb, "beta")
            r = sb.client.get(f"{FORGE}/skills")
            assert r.status_code == 200, r.text
            body = r.json()
            names = {s["name"] for s in body["skills"]}
            assert {"alpha", "beta"} <= names
            assert body["count"] == len(body["skills"])

    def test_agrees_with_the_canonical_skills_endpoint(self, tmp_path):
        """Two endpoints, one source — 0 vs 203 is what this prevents."""
        with console_client(tmp_path) as sb:
            for n in ("alpha", "beta", "gamma"):
                _make_skill(sb, n)
            forge = {s["name"] for s in sb.client.get(f"{FORGE}/skills").json()["skills"]}
            canon = {s["name"] for s in sb.client.get("/v1/console/skills").json()["skills"]}
            assert forge == canon

    def test_learning_state_comes_from_real_grade_dicts(self, tmp_path):
        """sum(meta["grades"]) over dicts used to raise; a grade is a dict."""
        with console_client(tmp_path) as sb:
            _make_skill(sb, "graded", grades=(0.5, 1.0))
            skill = next(s for s in sb.client.get(f"{FORGE}/skills").json()["skills"]
                         if s["name"] == "graded")
            assert skill["grade_count"] == 2
            assert skill["learning_state"]["feedback_count"] == 2
            assert skill["learning_state"]["confidence"] == pytest.approx(0.75)

    def test_ungraded_skill_reports_no_learning_state(self, tmp_path):
        with console_client(tmp_path) as sb:
            _make_skill(sb, "plain")
            skill = next(s for s in sb.client.get(f"{FORGE}/skills").json()["skills"]
                         if s["name"] == "plain")
            assert skill["learning_state"] is None
            assert skill["mean_score"] is None

    def test_untracked_attributes_are_null_not_invented(self, tmp_path):
        """ADR-0763: no meta.json carries a version; "1.0.0" was fabricated."""
        with console_client(tmp_path) as sb:
            _make_skill(sb, "plain")
            skill = next(s for s in sb.client.get(f"{FORGE}/skills").json()["skills"]
                         if s["name"] == "plain")
            assert skill["version"] is None
            assert skill["versions"] == []

    def test_capabilities_say_the_controls_do_nothing(self, tmp_path):
        with console_client(tmp_path) as sb:
            caps = sb.client.get(f"{FORGE}/skills").json()["capabilities"]
            assert caps["enable_disable"] is False
            assert caps["rollback"] is False

    def test_graph_contains_skill_nodes(self, tmp_path):
        with console_client(tmp_path) as sb:
            _make_skill(sb, "alpha")
            nodes = sb.client.get(f"{FORGE}/graph").json()["nodes"]
            assert any(n["id"] == "skill:alpha" for n in nodes)

    def test_search_finds_skills(self, tmp_path):
        with console_client(tmp_path) as sb:
            _make_skill(sb, "findme")
            hits = sb.client.get(f"{FORGE}/search", params={"q": "findme"}).json()
            rows = hits["results"] if isinstance(hits, dict) and "results" in hits else hits
            assert any(r.get("id") == "findme" for r in rows)


class TestOSSkillsTab:
    def test_lists_the_live_registry_not_an_empty_literal(self, tmp_path):
        with console_client(tmp_path) as sb:
            body = sb.client.get(f"{FORGE}/os-skills").json()
            ids = {s["id"] for s in body["os_skills"]}
            assert "os.delegation_router" in ids
            assert "os.context_adapter" in ids
            assert body["count"] == len(body["os_skills"]) > 0

    def test_compliance_tier_is_marked_undisableable(self, tmp_path):
        with console_client(tmp_path) as sb:
            rows = sb.client.get(f"{FORGE}/os-skills").json()["os_skills"]
            compliance = [s for s in rows if s["tier"] == "compliance"]
            assert compliance, "expected at least one compliance-tier OS-Skill"
            assert all(s["disableable"] is False for s in compliance)


class TestMutationsAreRealOrHonest:
    def test_disable_then_enable_actually_changes_the_listed_state(self, tmp_path):
        with console_client(tmp_path) as sb:
            sid = "os.vibe_engineering"

            def listed() -> bool:
                rows = sb.client.get(f"{FORGE}/os-skills").json()["os_skills"]
                return next(s["enabled"] for s in rows if s["id"] == sid)

            assert listed() is True
            r = sb.client.post(f"{FORGE}/os-skills/{sid}/disable", headers=sb.csrf_headers)
            assert r.status_code == 200, r.text
            assert r.json()["enabled"] is False
            assert listed() is False, "route reported success but state did not change"

            r = sb.client.post(f"{FORGE}/os-skills/{sid}/enable", headers=sb.csrf_headers)
            assert r.status_code == 200, r.text
            assert listed() is True

    def test_compliance_skill_refuses_disable(self, tmp_path):
        with console_client(tmp_path) as sb:
            rows = sb.client.get(f"{FORGE}/os-skills").json()["os_skills"]
            sid = next(s["id"] for s in rows if s["tier"] == "compliance")
            r = sb.client.post(f"{FORGE}/os-skills/{sid}/disable", headers=sb.csrf_headers)
            assert r.status_code == 403

    def test_unknown_os_skill_is_404_not_a_cheerful_200(self, tmp_path):
        with console_client(tmp_path) as sb:
            r = sb.client.post(f"{FORGE}/os-skills/os.nope/disable", headers=sb.csrf_headers)
            assert r.status_code == 404

    @pytest.mark.parametrize("path", [
        "/skills/anything/enable",
        "/skills/anything/disable",
        "/tools/anything/enable",
        "/tools/anything/disable",
    ])
    def test_unimplemented_mutations_answer_501(self, tmp_path, path):
        """They used to return 200 AND write an audit record for nothing."""
        with console_client(tmp_path) as sb:
            r = sb.client.post(f"{FORGE}{path}", headers=sb.csrf_headers)
            assert r.status_code == 501, r.text
            assert "not implemented" in r.json()["detail"].lower()

    def test_a_501_mutation_writes_no_audit_record(self, tmp_path):
        """The real damage: a permanent chain entry for an act that never
        happened. The chain must be untouched by a refused mutation."""
        with console_client(tmp_path) as sb:
            before = len(sb.chain_records())
            sb.client.post(f"{FORGE}/skills/anything/disable", headers=sb.csrf_headers)
            sb.client.post(f"{FORGE}/tools/anything/disable", headers=sb.csrf_headers)
            after = sb.chain_records()
            assert len(after) == before
            assert not [r for r in after
                        if "forge_skill_disabled" in json.dumps(r)
                        or "forge_tool_disabled" in json.dumps(r)]
