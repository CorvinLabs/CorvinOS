"""Gap closing: Forge / Workflows / Compute / Skill Packages (release-readiness 2026-10-24).

Every test drives the REAL console over HTTP (a ``uvicorn`` subprocess started by
``tests/sandbox/harness.py`` with a sandboxed CORVIN_HOME); none imports a handler. Red on
origin/main 925406772, green on branch ``gap/forge-wf-compute``:

* G1 ``GET /forge/plugins`` had no handler (404) -> the Plugins tab was dead.
* G2 ``POST /forge/skills/{id}/rollback`` and ``POST /layer-forge/gate-thresholds/apply`` wrote a
  fictional audit record -> 501, nothing in the chain.
* G3 ``GET /forge/audit`` kept only ``console.action_performed`` with ``details.action`` starting
  ``forge_`` and read the last 256 KiB -> 0 events on a chain with thousands of Forge records.
* G4 Templates tab: ids unknown to the server -> ``GET /workflow-templates`` + ``POST /workflows
  {template}``.
* G5 ``POST /compute/jobs`` queued forever and charged the compute quota -> 501, nothing charged.
* G6 The Skill Manager upload is the supported path of the skill store the Skills tab reads.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402

# The process tenant: ``security_events.write_event`` drops (AuditTenantMismatch) any record whose
# tenant is not the process tenant, so a sandbox tenant such as ``sbxa`` would never reach its chain.
TENANT = "_default"


@pytest.fixture(scope="module")
def sess_a(session_for):
    """Overrides the conftest fixture of the same name: a session of the process tenant."""
    return session_for(TENANT)

# Product writers, run in a child of the sandbox (same CORVIN_HOME / anchor key as the server).
EMIT_FORGE_EVENTS = r"""
import json, sys
from corvin_console import audit as a
t = sys.argv[1]
a.forge_run_started(tenant_id=t, run_id="gaprun01", run_kind="tool")
a.forge_run_finished(tenant_id=t, run_id="gaprun01", run_kind="tool", status="success",
                     artifact_kind="tool", artifact_id="gap_probe_tool", event_count=3)
a.action_performed(tenant_id=t, sid_fingerprint="sbxfp", action="skill.generated_created",
                   target_kind="generated_skill", target_id="assistant.gap_probe_skill")
a.action_performed(tenant_id=t, sid_fingerprint="sbxfp", action="workflow.created",
                   target_kind="workflow", target_id="not_a_forge_event")
from forge import security_events as se, paths as p
se.write_event(p.tenant_audit_chain(t), "forge.tool_executed", tool="gap_probe_tool",
               details={"tool_name": "gap_probe_tool", "status": "ok", "exit_code": 0,
                        "duration_ms": 12, "run_id": "gaprun01", "decision": "allow"})
# the shape the SkillForge MCP (skill_create through a chat turn) writes: name in the `tool` field
se.write_event(p.tenant_audit_chain(t), "skill.create", tool="assistant.gap_mcp_skill",
               details={"sha": "694456452841acda", "scope": "session", "type": "domain",
                        "persona": "assistant"})
# OS-skill telemetry written on every turn must NOT show up in the Forge tab
se.write_event(p.tenant_audit_chain(t), "skill.model_selector.classified", tool="",
               details={"tenant_id": t})
print("ok")
"""

PLANT_PLUGIN = r"""
import json, sys
from corvin_console import audit  # noqa: F401  (puts the forge package on sys.path)
from forge import paths as p
dest =p.tenant_home(sys.argv[1]) / "plugin-builder" / "community_gap_probe_plugin"
dest.mkdir(parents=True, exist_ok=True)
(dest / "FORGE_PROVENANCE.json").write_text(json.dumps({
    "surface": "plugin_forge", "plugin_id": "community_gap_probe_plugin",
    "display_name": "Gap Probe", "kind": "tool", "tier": "A", "quality": "reviewed",
    "created_at": 1.0}), encoding="utf-8")
print("ok")
"""


def _chain(sb) -> list[dict]:
    path = sb.tenant_home(TENANT) / "global" / "forge" / "audit.jsonl"
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def _events(sb, name: str) -> list[dict]:
    return [r for r in _chain(sb)
            if r.get("event_type") == name or (r.get("details") or {}).get("action") == name]


# ── G1: Forge Plugins tab ────────────────────────────────────────────────────

def test_G1_forge_plugins_route_exists_and_lists_staged_plugins(server, sandbox, sess_a):
    sandbox.run_child(PLANT_PLUGIN, TENANT)
    r = harness.fetch(server, sess_a, "/forge/plugins")
    assert r.status_code == 200, f"GET /forge/plugins -> {r.status_code} {r.text[:200]}"
    body = r.json()
    ids = [p["id"] for p in body["plugins"]]
    assert "community_gap_probe_plugin" in ids, body
    row = next(p for p in body["plugins"] if p["id"] == "community_gap_probe_plugin")
    assert row["name"] == "Gap Probe" and row["installed"] is False and row["enabled"] is False
    # the Marketplace "Forged" list and this tab read the same staging directory
    other = harness.fetch(server, sess_a, "/forge-creator/plugins").json()
    assert {p["dirname"] for p in other["plugins"]} == set(ids)


# ── G2: stub routes write no fictional audit record ──────────────────────────

def test_G2_skill_rollback_is_501_and_writes_nothing(server, sandbox, sess_a):
    before = len(_events(sandbox, "forge_skill_rollback"))
    r = harness.fetch(server, sess_a, "/forge/skills/assistant.x/rollback?version=1.0.0", method="POST")
    assert r.status_code == 501, f"{r.status_code} {r.text[:200]}"
    assert len(_events(sandbox, "forge_skill_rollback")) == before == 0


def test_G2_gate_threshold_apply_is_501_and_writes_nothing(server, sandbox, sess_a):
    r = harness.fetch(server, sess_a, "/layer-forge/gate-thresholds/apply", method="POST",
                      json_body={"gate_id": "schema_check", "new_threshold": 0.8, "reason": "probe"})
    assert r.status_code == 501, f"{r.status_code} {r.text[:200]}"
    assert not _events(sandbox, "layer_forge.gate_threshold_applied")


# ── G3: Forge Audit tab shows the real Forge events ──────────────────────────

def test_G3_forge_audit_lists_real_forge_events_beyond_the_tail(server, sandbox, sess_a):
    out = sandbox.run_child(EMIT_FORGE_EVENTS, TENANT)
    assert out.strip().endswith("ok")
    # 700 KiB of unrelated records AFTER the Forge events: the old reader looked at the last 256 KiB.
    chain = sandbox.tenant_home(TENANT) / "global" / "forge" / "audit.jsonl"
    noise = json.dumps({"ts": 9e9, "event_type": "console.page_viewed",
                        "details": {"tenant_id": TENANT, "pad": "x" * 200}}) + "\n"
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(noise * 3000)
    assert chain.stat().st_size > 600 * 1024

    r = harness.fetch(server, sess_a, "/forge/audit")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    types = [e["type"] for e in body["events"]]
    for expected in ("forge_run.started", "forge_run.finished", "skill.generated_created",
                     "forge.tool_executed"):
        assert expected in types, f"{expected} missing from {types}"
    assert "skill.create" in types, types
    mcp_skill = next(e for e in body["events"] if e["type"] == "skill.create")
    assert mcp_skill["resource_type"] == "skill" and mcp_skill["resource_id"] == "assistant.gap_mcp_skill"
    assert "workflow.created" not in types and "console.page_viewed" not in types
    assert "skill.model_selector.classified" not in types
    tool = next(e for e in body["events"] if e["type"] == "forge.tool_executed")
    assert tool["resource_type"] == "tool" and tool["resource_id"] == "gap_probe_tool"
    assert len(tool["hash"]) == 8 and len(tool["prev_hash"]) == 8
    skill = next(e for e in body["events"] if e["type"] == "skill.generated_created")
    assert skill["resource_type"] == "skill" and skill["resource_id"] == "assistant.gap_probe_skill"
    assert body["complete"] is True

    only_runs = harness.fetch(server, sess_a, "/forge/audit?event_type=forge_run.").json()
    assert {e["type"] for e in only_runs["events"]} == {"forge_run.started", "forge_run.finished"}


# ── G4: Workflows templates are real ─────────────────────────────────────────

def test_G4_templates_are_served_and_create_a_workflow(server, sandbox, sess_a):
    r = harness.fetch(server, sess_a, "/workflow-templates")
    assert r.status_code == 200, r.text[:200]
    tpls = {t["id"]: t for t in r.json()["templates"]}
    assert {"daily-digest", "inbox-monitor", "data-pipeline", "approval-loop"} <= set(tpls)
    assert tpls["daily-digest"]["node_count"] == 4 and tpls["daily-digest"]["description"]

    c = harness.fetch(server, sess_a, "/workflows", method="POST",
                      json_body={"id": "gap_digest", "title": "Gap digest", "template": "daily-digest"})
    assert c.status_code == 200, c.text[:300]
    d = harness.fetch(server, sess_a, "/workflows/gap_digest").json()
    assert "name: gap_digest" in d["yaml"]
    node_ids = [n["id"] for n in d["graph"]["nodes"]] if isinstance(d["graph"], dict) else [n["id"] for n in d["graph"]]
    assert node_ids == ["cron_trigger", "fetch_content", "summarise", "deliver"], node_ids
    created = _events(sandbox, "workflow.created")
    assert any((r.get("details") or {}).get("target_id") == "gap_digest"
               and (r.get("details") or {}).get("trigger") == "template=daily-digest" for r in created)

    assert harness.fetch(server, sess_a, "/workflows", method="POST",
                         json_body={"id": "gap_x", "template": "nope"}).status_code == 404
    assert harness.fetch(server, sess_a, "/workflows", method="POST",
                         json_body={"id": "gap_y", "template": "daily-digest", "yaml": "awp: x"}).status_code == 400
    harness.fetch(server, sess_a, "/workflows/gap_digest", method="DELETE")


# ── G5: compute jobs do not queue forever or charge ──────────────────────────

def test_G5_compute_job_submit_is_501_and_charges_nothing(server, sandbox, sess_a):
    before = harness.fetch(server, sess_a, "/compute/license").json()
    r = harness.fetch(server, sess_a, "/compute/jobs", method="POST",
                      json_body={"name": "gap job", "job_type": "grid", "strategy": "grid", "max_trials": 3})
    assert r.status_code == 501, f"{r.status_code} {r.text[:200]}"
    assert harness.fetch(server, sess_a, "/compute/jobs").json()["count"] == 0
    assert harness.fetch(server, sess_a, "/compute/license").json() == before
    assert not _events(sandbox, "compute.job_submitted")


def test_G5_legacy_job_records_can_still_be_listed_and_removed(server, sandbox, sess_a):
    jid = "4227b5b7-1111-4222-8333-444455556666"
    d = sandbox.tenant_home(TENANT) / "global" / "compute" / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{jid}.json").write_text(json.dumps({"job_id": jid, "name": "old", "job_type": "grid",
                                               "status": "queued", "created_at": 1.0}), encoding="utf-8")
    listed = harness.fetch(server, sess_a, "/compute/jobs").json()
    assert [j["job_id"] for j in listed["jobs"]] == [jid]
    assert harness.fetch(server, sess_a, f"/compute/jobs/{jid}", method="DELETE").status_code == 200
    assert harness.fetch(server, sess_a, "/compute/jobs").json()["count"] == 0


# ── G6: the Skill Manager upload is the supported path ───────────────────────

def _skill_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "# gap probe skill\n")
    return buf.getvalue()


def test_G6_skill_manager_upload_feeds_the_skills_catalogue(server, sandbox, sess_a):
    import httpx

    with server.client() as c:
        r = c.post("/skills-manager/skills/install", cookies=sess_a.cookies(),
                   headers={"x-csrf-token": sess_a.csrf},
                   data={"skill_id": "gap_probe_skill", "version": "1.0.0"},
                   files={"file": ("gap.zip", _skill_zip(), "application/zip")})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    installed = harness.fetch(server, sess_a, "/skills-manager/skills/installed").json()
    assert "gap_probe_skill" in [s["skill_id"] for s in installed["skills"]]
    found = harness.fetch(server, sess_a, "/marketplace/skills/search?q=gap_probe").json()
    assert "gap_probe_skill" in json.dumps(found), found
    assert (sandbox.corvin_home / "skills_installed" / "gap_probe_skill" / "1.0.0" / "SKILL.md").is_file()
    # tidy: the supported removal path
    d = harness.fetch(server, sess_a, "/skills-manager/skills/uninstall/gap_probe_skill/1.0.0", method="DELETE")
    assert d.status_code == 200
    assert isinstance(httpx.__version__, str)


def test_G6_packages_tab_store_is_not_the_skills_catalogue(server, sandbox, sess_a):
    """The Marketplace Packages store (routes/packages.py) is separate: it must not be what the
    Skills tab reads. Pinned by source: marketplace_routes resolves its registry through the
    Skill Manager's ``installed_skills_root`` and never imports ``packages``."""
    from pathlib import Path

    src = (harness.REPO_ROOT / "core/console/corvin_console/routes/marketplace_routes.py").read_text()
    assert "installed_skills_root" in src and "skills_registry.json" in src
    assert "routes.packages" not in src and "from .packages" not in src and "import packages" not in src
    assert Path(harness.REPO_ROOT / "core/console/corvin_console/routes/skill_manager.py").is_file()
