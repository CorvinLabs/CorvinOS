"""ADR-0701 — the console manual skill editor (``POST/PUT /v1/console/skills/manual``).

Driven over HTTP through the real console router (see ``_license_console_sandbox``).

What the product does today, and what ADR-0701 says it should do
-----------------------------------------------------------------
ADR-0701's G3 row lists ``POST/PUT /skills/manual`` among the routes that carry
``require_forge_capability`` and answer **HTTP 402** ``license_required``. They do
not: ``routes/skills_manual.py`` has no licence dependency. A free-tier write is
still refused — one layer down, by the G2 gate inside
``SkillRegistry.create`` (``skill_forge/registry.py::_require_forge_create_licence``)
— but that gate raises ``ValueError("license_required: …")``, which the route maps
to **HTTP 400**. So the refusal is real (nothing is written) and the status code
is wrong. The tests below pin the 400 and say why; when the route gains
``Depends(require_forge_capability)`` they must change to ``assert_402_forge``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import console, member_tier  # noqa: E402

URL = "/v1/console/skills/manual"
NAME = "assistant.real_flow"          # inside the console persona's namespace
BODY = "# Real flow\n\nA manually authored skill body."


@pytest.fixture
def con(tmp_path):
    with console(tmp_path) as c:
        yield c


def _skill_dir(con) -> Path:
    return con.home / "tenants" / "_default" / "skill-forge" / "skills" / NAME


def _assert_licence_refusal(resp) -> None:
    # 400, not 402 — see the module docstring (G3 dependency missing on this route).
    assert resp.status_code == 400, (resp.status_code, resp.text)
    detail = resp.json()["detail"]
    assert detail.startswith("license_required:"), detail
    assert "forge.create" in detail and "tier=free" in detail


def test_free_tier_create_is_refused_and_nothing_is_written(con):
    _assert_licence_refusal(con.client.post(URL, json={"name": NAME, "body": BODY}, headers=con.h))
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


def test_downgrade_to_free_blocks_edits_but_keeps_the_skill(con):
    with member_tier():
        assert con.client.post(URL, json={"name": NAME, "body": BODY}, headers=con.h).status_code == 200
    before = (_skill_dir(con) / "SKILL.md").read_text()
    _assert_licence_refusal(con.client.put(
        f"{URL}/{NAME}", json={"body": "# changed\n\nnew body"}, headers=con.h))
    assert (_skill_dir(con) / "SKILL.md").read_text() == before
    assert [s["name"] for s in con.client.get(URL).json()["skills"]] == [NAME]


def test_post_without_csrf_is_403(con):
    resp = con.client.post(URL, json={"name": NAME, "body": BODY})
    assert resp.status_code == 403, resp.text


def test_post_without_session_is_401(con):
    resp = con.anonymous().post(URL, json={"name": NAME, "body": BODY}, headers=con.h)
    assert resp.status_code == 401, resp.text
