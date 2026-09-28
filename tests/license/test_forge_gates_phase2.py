"""ADR-0701 G2 — the SkillForge write path, and its fail-closed contract.

G2 is ``skill_forge/registry.py::SkillRegistry.create`` (via
``_require_forge_create_licence``): the single generation write path that the
L7 MCP ``skill_create``/``skill_promote`` tools, ``MultiSkillRegistry.promote``,
``update_body`` (the console manual editor) and the Skill-Creator all funnel
into. Its HTTP face is covered in test_forge_create_real_flow.py; G1/G4/G5 in
test_adr0701_gates_g1_g4_g5.py; G3 in test_g3_gates_e2e.py.

The earlier version of this file was 27 empty ``pytest.skip`` bodies plus four
requests to ``/v1/skill-creator/...``-style paths that exist nowhere (404).
Stubs whose subject does not exist in the product were dropped rather than kept
as skips — they are listed in the review report: ``SkillRegistry.create(files=…)``
(multi-file skills — not implemented), forge provenance signing
(``forge.artifact_provenance_signed`` — not emitted anywhere), the forge MCP stdio
transport, and grep-based "no bypass" guards.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import member_tier  # noqa: E402

from skill_forge.multi_registry import MultiSkillRegistry  # noqa: E402
from skill_forge.registry import SkillRegistry  # noqa: E402

BODY = "# G2 skill\n\nDoes one small thing."


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "corvin_home"
    h.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    return h


def _reg(home: Path) -> SkillRegistry:
    root = home / "skill-forge-root"
    root.mkdir(parents=True, exist_ok=True)
    return SkillRegistry(root, audit_path=home / "audit.jsonl")


def _create(reg: SkillRegistry, name: str = "g2_skill"):
    return reg.create(name=name, type="learned-experience", body_md=BODY,
                      description="g2", scope="user")


def test_free_tier_cannot_create_a_skill(home):
    reg = _reg(home)
    with pytest.raises(ValueError, match=r"^license_required: .*forge\.create.*tier=free"):
        _create(reg)
    assert reg.get("g2_skill") is None


def test_member_can_create_a_skill(home):
    reg = _reg(home)
    with member_tier():
        spec = _create(reg)
    assert spec.name == "g2_skill"
    assert reg.get("g2_skill") is not None
    assert "Does one small thing." in (reg.get_body("g2_skill") or "")


def test_licensing_module_unavailable_fails_closed(home):
    """An ImportError of the licensing API must refuse, never allow."""
    reg = _reg(home)
    with mock.patch.dict(sys.modules, {"corvin_operator.license.capability_api": None}):
        with pytest.raises(ValueError, match="licensing module unavailable"):
            _create(reg)
    assert reg.get("g2_skill") is None


def test_invalid_tenant_fails_closed(home, monkeypatch):
    """An invalid tenant is a LicenseDenied(reason="invalid_tenant"); G2 turns it
    into a refusal — even for a member."""
    monkeypatch.setenv("CORVIN_TENANT_ID", "../escape")
    reg = _reg(home)
    with member_tier():
        with pytest.raises(ValueError, match="license_required: .*invalid_tenant"):
            _create(reg)
    assert reg.get("g2_skill") is None


def test_multi_registry_promote_after_downgrade_is_refused(home, tmp_path):
    """Member creates in session scope; after the licence lapses, promotion
    (which re-creates in the target scope through G2) is refused and the
    original copy is untouched."""
    multi = MultiSkillRegistry(tenant_id="_default", channel_id="chan", task_id="task",
                               project_root=tmp_path / "project")
    with member_tier():
        multi.create(scope="session", name="promo_skill", type="learned-experience",
                     body_md=BODY, description="p")
    assert multi.find_scope("promo_skill") == "session"

    with pytest.raises(ValueError, match="^license_required:"):
        multi.promote("promo_skill", to="user", force=True)
    assert multi.find_scope("promo_skill") == "session"


def test_multi_registry_promote_as_member_moves_the_skill(home, tmp_path):
    multi = MultiSkillRegistry(tenant_id="_default", channel_id="chan", task_id="task",
                               project_root=tmp_path / "project")
    with member_tier():
        multi.create(scope="session", name="promo_ok", type="learned-experience",
                     body_md=BODY, description="p")
        multi.promote("promo_ok", to="user", force=True)
    assert multi.find_scope("promo_ok") == "user"
