"""A learning loop a plugin declares reaches the Learning Loops panel, measured (ADR-0906/0907, E8).

The chain under test, every link through its real entry point:

    CorvinTV ``plugin.yaml`` (``learning_loops:``)                       [Corvin-Marketplace]
      -> ``POST /v1/console/api/v1/marketplace/plugins/{id}/install``    (``marketplace_resolve.record_from_manifest``)
      -> ``PluginRecord.learning_loops`` in the tenant ``registry.yaml``
      -> ``capabilities._get_learning_loops`` -> loop index (``learning_analytics._sync_index_from_manifest``)
      -> ``GET /v1/console/learning-loops/list``, measured from the owner skill's own events.

Until 2026-10-26 the middle of that chain did not exist: ``_get_learning_loops`` imported a ``PluginRegistry``
that ``plugin_manager_v2`` does not define and swallowed the ``ImportError``, so no declared loop could ever
be listed. The positive control (the row is absent before the install) and the mutations recorded at the end
of this docstring are what make the green result mean something.

Needs the Corvin-Marketplace checkout next to this repo (skipped where it is absent, e.g. in CI).

Mutation record (2026-10-26): reverting ``_get_learning_loops`` to read a missing class, or dropping
``learning_loops`` from ``record_from_manifest``, turns ``test_declared_loop_is_listed_after_install`` red.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.learning.console_client import console_client

REPO = Path(__file__).resolve().parents[2]
# A worktree has no sibling checkout: CORVIN_MARKETPLACE_REPO points the test at the real one.
MARKETPLACE = Path(os.environ.get("CORVIN_MARKETPLACE_REPO") or REPO.parent / "Corvin-Marketplace")
BUILDIN = MARKETPLACE / "plugins" / "buildin"
INDEX = MARKETPLACE / "index" / "plugins.json"
INDEX_ID = "plugin:buildin-media-corvintv"
LOOP_ID = "video_producer:scene_feedback"
INSTALL = f"/v1/console/api/v1/marketplace/plugins/{INDEX_ID}/install"
LIST = "/v1/console/learning-loops/list"

pytestmark = pytest.mark.skipif(
    not (BUILDIN / "media" / "corvintv" / "plugin.yaml").is_file() or not INDEX.is_file(),
    reason="Corvin-Marketplace checkout not found next to this repo",
)


@pytest.fixture
def sb(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_MARKETPLACE_ROOT", str(BUILDIN))
    monkeypatch.setenv("CORVIN_MARKETPLACE_INDEX", str(INDEX))
    with console_client(tmp_path) as sandbox:
        yield sandbox


def _plugin_rows(sb, **params):
    r = sb.client.get(LIST, params=params)
    assert r.status_code == 200, r.text
    return {row["loop_id"]: row for row in r.json()["loops"] if row["origin"] == "plugin"}


def _install(sb):
    r = sb.client.post(INSTALL, json={"wait": True, "enable_after_install": True, "consent_granted": True},
                       headers=sb.csrf_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") in ("completed", "installed", "success"), body
    return body


def _record_feedback(sb, n: int) -> None:
    """``n`` real operator-feedback events of the owner skill, through the audit-first helper
    the video routes use."""
    import asyncio
    from types import SimpleNamespace

    from corvin_console.routes.feedback_emitter_helper import emit_feedback_event
    from corvin_console.routes.video_producer_api import _write_execution_event

    for i in range(n):
        # ADR-0534 reality check: a rating is accepted only for a skill that really ran. The host writes
        # this event when a production ends; here the same function records that a production ran.
        job = SimpleNamespace(id=f"job-{i}", status="complete", started_at=None, completed_at=None)
        assert _write_execution_event(sb.tenant_id, job, {"scenes": 3}), "the execution event was not recorded"
        ref = asyncio.run(emit_feedback_event(
            skill_id="os.video_producer", task_id=f"job-{i}", tenant_id=sb.tenant_id,
            outcome_feedback="yes", quality_rating=5, confidence=0.8, source="user",
            lom="tests/learning/test_adr_0906_plugin_loop_declaration_e2e.py", scene_id="s01"))
        assert ref, "the feedback event was not recorded"


def test_no_plugin_loop_before_install_positive_control(sb):
    assert _plugin_rows(sb) == {}
    manifest = sb.client.get("/v1/console/capabilities/manifest")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["learning_loops"] == []


def test_declared_loop_is_listed_after_install(sb):
    assert LOOP_ID not in _plugin_rows(sb)
    _install(sb)

    rows = _plugin_rows(sb, limit=51)  # a different limit is a different cache key
    assert LOOP_ID in rows, sorted(rows)
    row = rows[LOOP_ID]
    assert row["plugin_id"] == "video_producer"
    assert row["skill_id"] == "os.video_producer"
    # nothing has produced an event yet: not measured, neither healthy nor failing
    assert row["status"] == "unknown"
    assert row["health"]["score"] is None
    assert row["event_count_7d"] == 0

    manifest = sb.client.get("/v1/console/capabilities/manifest").json()
    declared = {d["loop_id"]: d for d in manifest["learning_loops"]}
    assert declared[LOOP_ID]["owner_skill"] == "os.video_producer"
    assert declared[LOOP_ID]["event_source"] == "FeedbackEvent.quality_rating"
    assert "health_score" not in declared[LOOP_ID], "a declaration carries no invented metrics"


def test_declared_loop_is_measured_from_its_owner_skills_events(sb):
    _install(sb)
    _plugin_rows(sb, limit=52)  # first listing syncs the index
    _record_feedback(sb, 3)

    row = _plugin_rows(sb, limit=53)[LOOP_ID]
    # every learning event of the owner skill counts, as for any OS-skill loop: 3 executions + 3 ratings
    assert row["event_count_7d"] == 6, row
    assert row["event_count_total"] == 6
    assert row["last_event"] is not None
    assert row["status"] == "active"
    # approvals are feedback, not task outcomes: still no health score, and it says why
    assert row["health"]["score"] is None
    assert "os.video_producer" in row["event_source"]


def test_disabled_plugin_declares_nothing(sb):
    _install(sb)
    assert any(d["loop_id"] == LOOP_ID for d in sb.client.get("/v1/console/capabilities/manifest").json()["learning_loops"])

    from corvin_console.routes.capabilities import _get_learning_loops
    from corvin_console.routes.plugins import _load

    reg = _load(sb.tenant_id)
    reg.records["video_producer"] = reg.records["video_producer"].with_enabled(False)
    reg.save()
    assert [d for d in _get_learning_loops(sb.tenant_id) if d["plugin_id"] == "video_producer"] == []


def test_registry_without_loops_round_trips_unchanged():
    """A record that declares nothing serialises without the key, so an older reader that rejects
    unknown keys keeps working."""
    from core.plugins.corvin_plugins.manifest import PluginRecord

    rec = PluginRecord(plugin_id="no-loops", version="1.0.0", display_name="No loops", plugin_type="web_surface")
    assert "learning_loops" not in rec.to_dict()
    with_loops = PluginRecord(plugin_id="with-loops", version="1.0.0", display_name="With", plugin_type="web_surface",
                              learning_loops=[{"id": "x", "description": "a declared loop"}])
    assert PluginRecord.from_dict(with_loops.to_dict()).learning_loops == with_loops.learning_loops


def test_an_installed_older_record_gets_the_declaration_through_the_update_route(sb):
    """The live situation: a record installed BEFORE the plugin declared its loop. Update refreshes it from
    the Marketplace manifest, keeps the enabled state, and the loop appears in the panel."""
    import dataclasses

    from corvin_console.routes.plugins import _load

    _install(sb)
    reg = _load(sb.tenant_id)
    current = reg.records["video_producer"]
    assert current.learning_loops, "the install should already carry the declaration"
    reg.records["video_producer"] = dataclasses.replace(current, version="1.7.0", learning_loops=[])
    reg.save()

    assert LOOP_ID not in _plugin_rows(sb, limit=61), "positive control: the old record lists no loop"
    r = sb.client.post(f"/v1/console/api/v1/marketplace/plugins/{INDEX_ID}/update", json={}, headers=sb.csrf_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["from_version"], body["enabled"]) == ("1.7.0", True), body

    row = _plugin_rows(sb, limit=62).get(LOOP_ID)
    assert row is not None, "the loop must appear after the update"
    assert row["skill_id"] == "os.video_producer"
    assert _load(sb.tenant_id).records["video_producer"].learning_loops[0]["id"] == "scene_feedback"
