"""ADR-0701 — the console manual skill editor (``POST/PUT /v1/console/skills/manual``).

Driven over HTTP through the real console router (see ``_license_console_sandbox``).

G3 on this route
----------------
ADR-0701's G3 row lists ``POST/PUT /skills/manual`` among the routes that carry
``require_forge_capability`` and answer **HTTP 402** ``license_required``. Until
2026-09-27 they did not: the free-tier write was refused one layer down by the
G2 gate inside ``SkillRegistry.create`` (``ValueError("license_required: …")``),
which the route mapped to **HTTP 400**. The route now carries the G3 dependency,
and a G2 refusal that still reaches the route (the tier changed between the two
checks) is mapped to 402 as well.

ADR-2095 (1c1f7aa64) amended this for SKILLS: the free tier authors skills
within a daily quota (``skill_forge_per_day``); the route's G3 dependency is
``require_skill_forge_quota`` and a free write past the quota answers 402
``limit_reached``. The free-tier tests below follow that contract (they used to
assert a blanket 402 ``license_required``).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import (  # noqa: E402
    assert_402_limit_reached, console, exhaust_free_skill_quota, member_tier,
)

URL = "/v1/console/skills/manual"
NAME = "assistant.real_flow"          # inside the console persona's namespace
BODY = "# Real flow\n\nA manually authored skill body."


@pytest.fixture
def con(tmp_path):
    with console(tmp_path) as c:
        yield c


def _skill_dir(con) -> Path:
    return con.home / "tenants" / "_default" / "skill-forge" / "skills" / NAME


def test_free_tier_create_within_its_quota_is_written(con):
    resp = con.client.post(URL, json={"name": NAME, "body": BODY}, headers=con.h)
    assert resp.status_code == 200, resp.text
    assert (_skill_dir(con) / "SKILL.md").is_file()


def test_free_tier_create_past_its_quota_is_refused_and_nothing_is_written(con):
    exhaust_free_skill_quota(con.home)
    assert_402_limit_reached(con.client.post(URL, json={"name": NAME, "body": BODY}, headers=con.h))
    assert not _skill_dir(con).exists()
    assert con.client.get(URL).json()["count"] == 0


def test_member_can_create_a_manual_skill(con):
    with member_tier():
        resp = con.client.post(URL, json={"name": NAME, "body": BODY}, headers=con.h)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True and data["name"] == NAME
    assert (_skill_dir(con) / "SKILL.md").is_file()
    listed = con.client.get(URL).json()
    assert [s["name"] for s in listed["skills"]] == [NAME]


def test_member_create_is_audited_in_the_tenant_chain(con):
    with member_tier():
        resp = con.client.post(URL, json={"name": NAME, "body": BODY}, headers=con.h)
    assert resp.status_code == 200, resp.text
    events = con.audit_events()
    types = [e.get("event_type") for e in events]
    assert "skill.create" in types
    assert "console.action_performed" in types
    performed = [e for e in events if e.get("event_type") == "console.action_performed"]
    assert any(e.get("details", {}).get("action") == "skill.manual_created" for e in performed)
    # every record is hash-linked to its predecessor
    assert len(events) >= 2 and all(e.get("hash") for e in events)
    for prev, cur in zip(events, events[1:]):
        assert cur.get("prev_hash") == prev.get("hash")


def test_downgrade_to_free_blocks_edits_past_the_quota_but_keeps_the_skill(con):
    with member_tier():
        assert con.client.post(URL, json={"name": NAME, "body": BODY}, headers=con.h).status_code == 200
    before = (_skill_dir(con) / "SKILL.md").read_text(encoding="utf-8")
    exhaust_free_skill_quota(con.home)
    assert_402_limit_reached(con.client.put(
        f"{URL}/{NAME}", json={"body": "# changed\n\nnew body"}, headers=con.h))
    assert (_skill_dir(con) / "SKILL.md").read_text(encoding="utf-8") == before
    assert [s["name"] for s in con.client.get(URL).json()["skills"]] == [NAME]


def test_post_without_csrf_is_403(con):
    resp = con.client.post(URL, json={"name": NAME, "body": BODY})
    assert resp.status_code == 403, resp.text


def test_post_without_session_is_401(con):
    resp = con.anonymous().post(URL, json={"name": NAME, "body": BODY}, headers=con.h)
    assert resp.status_code == 401, resp.text


def test_g2_refusal_behind_a_passed_g3_is_still_402(con):
    """If the G3 pre-check passed but the G2 registry charge refuses (the quota
    ran out in between), the route answers 402 limit_reached — never a 400."""
    from corvin_console.routes.skill_creator_api import require_skill_forge_quota
    from corvin_console.deps import require_session

    exhaust_free_skill_quota(con.home)
    app = con.client.app
    app.dependency_overrides[require_skill_forge_quota] = require_session
    try:
        resp = con.client.post(URL, json={"name": NAME, "body": BODY}, headers=con.h)
    finally:
        app.dependency_overrides.pop(require_skill_forge_quota, None)
    assert_402_limit_reached(resp)
    assert not _skill_dir(con).exists()
