"""ADR-0701 forge.create gates G1, G4, G5 — behaviour, not import checks.

The previous version asserted ``hasattr``/``callable`` and built ``LicenseDenied``
with the pre-ADR-0703 one-argument signature (``LicenseDenied(capability, tier,
reason)`` since). These tests run the real ``capability_api.require_capability``;
free tier = scratch CORVIN_HOME with no licence key, member tier = the licence
resolver (``capability_api.active_tier``) reporting ``member``.

G1 (``corvin_operator/forge/forge/registry.py``) was FAIL-OPEN until
2026-09-27, for two independent reasons, both now pinned as fixed below:

1. It called ``require_capability(..., tenant_id="")``. An empty tenant failed
   ``validate_tenant_id`` and ``require_capability`` RETURNED an
   ``ENFORCEMENT_UNAVAILABLE`` decision instead of raising; G1 only caught
   ``LicenseDenied``, so the create proceeded on the free tier. G1 now resolves
   the process tenant (``current_tenant()``) and ``require_capability`` raises
   ``LicenseDenied(reason="invalid_tenant")`` for an invalid one.
2. Its module-level ``from corvin_operator.license.capability_api import …`` sat
   inside an import cycle; importing capability_api first bound a no-op
   ``require_capability`` for the life of the process. The import is now lazy
   (call time) and an ImportError denies.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import REPO, member_tier  # noqa: E402

import corvin_operator.license.capability_api as capability_api  # noqa: E402

IMPL = "def main(**kwargs):\n    return {'ok': True}\n"


def _registry(tmp_path):
    from corvin_operator.forge.forge.registry import Registry

    return Registry(tmp_path / "forge_root")


# ── G1: forge Registry.create / promote ─────────────────────────────────────

def test_g1_translates_a_denial_into_permission_error_and_writes_nothing(tmp_path):
    """The G1 contract: LicenseDenied → PermissionError, before anything is written."""
    reg = _registry(tmp_path)
    with member_tier():
        reg.create("g1_tool", "d", {"type": "object"}, IMPL)
    # free tier (no licence) from here on
    with pytest.raises(PermissionError, match="forge.create denied"):
        reg.create("g1_other", "d", {"type": "object"}, IMPL)
    with pytest.raises(PermissionError, match="forge.create denied"):
        reg.promote("g1_tool")
    assert reg.get("g1_other") is None
    assert not (tmp_path / "forge_root" / "skills" / "g1_tool").exists()


def test_g1_create_on_free_tier_is_refused(tmp_path):
    """Was KNOWN BUG (reason 1): with no licence at all ``Registry.create`` forged."""
    assert capability_api.active_tier() == "free"
    with pytest.raises(PermissionError, match="not_available_in_tier"):
        _registry(tmp_path).create("free_tool", "d", {"type": "object"}, IMPL)


def test_g1_invalid_process_tenant_is_refused(tmp_path, monkeypatch):
    """An invalid ``CORVIN_TENANT_ID`` must deny, even for a member."""
    monkeypatch.setenv("CORVIN_TENANT_ID", "../escape")
    with member_tier():
        with pytest.raises(PermissionError, match="forge.create denied"):
            _registry(tmp_path).create("t", "d", {"type": "object"}, IMPL)


def test_g1_binding_no_longer_depends_on_import_order(tmp_path):
    """Was KNOWN BUG (reason 2): capability_api imported before the forge
    package left G1 bound to a no-op. Fresh interpreter, both orders, the free
    tier is refused either way."""
    probe = (
        "import sys, pathlib\n{first}\n{second}\n"
        "from corvin_operator.forge.forge.registry import Registry\n"
        "try:\n"
        "    Registry(pathlib.Path(sys.argv[1])).create('t', 'd', {{'type': 'object'}}, 'x=1')\n"
        "    print('ALLOWED')\n"
        "except PermissionError:\n"
        "    print('DENIED')\n"
    )
    ca = "import corvin_operator.license.capability_api"
    rg = "from corvin_operator.forge.forge import registry"
    for i, (first, second) in enumerate(((ca, rg), (rg, ca))):
        out = subprocess.run(
            [sys.executable, "-c", probe.format(first=first, second=second),
             str(tmp_path / f"r{i}")],
            cwd=REPO, capture_output=True, text=True, timeout=120,
        )
        assert out.stdout.strip().splitlines()[-1] == "DENIED", out.stderr[-2000:]


def test_g1_licensing_import_failure_denies(tmp_path):
    reg = _registry(tmp_path)
    with mock.patch.object(capability_api, "active_tier", lambda: "member"), \
            mock.patch.dict(sys.modules, {"corvin_operator.license.capability_api": None}):
        with pytest.raises(PermissionError, match="unavailable"):
            reg.create("t", "d", {"type": "object"}, IMPL)


# ── G4: /plugin-builder (console slash command and messenger bridges) ───────

def test_g4_free_tier_is_refused_and_no_interview_starts():
    from core.plugins.plugin_builder import session_store, turn

    reply = turn.command("", tenant_id="_default", session_key="g4-free")
    assert reply.startswith("Plugin-Builder requires a member seat")
    assert "forge.create" in reply and "tier=free" in reply
    assert session_store.get("_default", "g4-free") is None


def test_g4_member_passes_the_gate():
    from core.plugins.plugin_builder import turn

    with member_tier():
        reply = turn.command("status", tenant_id="_default", session_key="g4-member")
    assert not reply.startswith("Plugin-Builder requires a member seat")
    assert reply.startswith("No Plugin-Builder interview is active")


# ── G5: orchestration quota gate (Tool/SkillForge subsystems) ───────────────

def test_g5_free_tier_raises_license_denied():
    from core.orchestration.quota_gate import check_forge_capability

    with pytest.raises(capability_api.LicenseDenied) as exc:
        check_forge_capability("_default", entry_point="test")
    assert exc.value.capability == "forge.create"
    assert exc.value.tier is capability_api.Tier.FREE
    assert exc.value.reason == "not_available_in_tier"


def test_g5_member_passes():
    from core.orchestration.quota_gate import check_forge_capability

    with member_tier():
        assert check_forge_capability("_default", entry_point="test") is None


def test_g5_invalid_tenant_is_refused():
    """Was KNOWN BUG: an invalid tenant came back as a RETURNED
    ENFORCEMENT_UNAVAILABLE verdict that check_forge_capability ignored."""
    from core.orchestration.quota_gate import check_forge_capability

    with member_tier():
        with pytest.raises(capability_api.LicenseDenied) as exc:
            check_forge_capability("", entry_point="test")
    assert exc.value.reason == "invalid_tenant"
