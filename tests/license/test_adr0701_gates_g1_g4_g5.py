"""ADR-0701 forge.create gates G1, G4, G5 — behaviour, not import checks.

The previous version asserted ``hasattr``/``callable`` and built ``LicenseDenied``
with the pre-ADR-0703 one-argument signature (``LicenseDenied(capability, tier,
reason)`` since). These tests run the real ``capability_api.require_capability``;
free tier = scratch CORVIN_HOME with no licence key, member tier = the licence
resolver (``capability_api.active_tier``) reporting ``member``.

G1 (``corvin_operator/forge/forge/registry.py``) is FAIL-OPEN today, for two
independent reasons, both pinned below so a fix has to touch these tests:

1. It calls ``require_capability(..., tenant_id="")``. An empty tenant fails
   ``validate_tenant_id`` and ``require_capability`` RETURNS an
   ``ENFORCEMENT_UNAVAILABLE`` decision instead of raising; G1 only catches
   ``LicenseDenied``, so the create proceeds on the free tier.
2. Its module-level ``from corvin_operator.license.capability_api import …`` is
   inside an import cycle (capability_api → forge.forge.paths → forge.forge
   package ``__init__`` → registry → capability_api, half-initialised). When
   capability_api is imported first, the ImportError fallback binds a no-op
   ``require_capability`` for the life of the process.
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
    from corvin_operator.forge.forge import registry as registry_mod

    # registry_mod.LicenseDenied, not capability_api's: when the import cycle
    # (reason 2) hit, registry holds its own fallback class and its ``except``
    # only matches that one. Both accept this argument shape.
    denied = registry_mod.LicenseDenied("forge.create", capability_api.Tier.FREE,
                                        "not_available_in_tier")
    reg = _registry(tmp_path)
    with mock.patch.object(registry_mod, "require_capability", side_effect=denied):
        with pytest.raises(PermissionError, match="forge.create denied"):
            reg.create("g1_tool", "d", {"type": "object"}, IMPL)
        with pytest.raises(PermissionError, match="forge.create denied"):
            reg.promote("g1_tool")
    assert reg.get("g1_tool") is None


def test_g1_create_on_free_tier_is_fail_open_KNOWN_BUG(tmp_path):
    """KNOWN BUG (reason 1 in the module docstring): with no licence at all,
    ``Registry.create`` still forges the tool. Flip to ``pytest.raises(PermissionError)``
    once G1 passes a real tenant and treats a non-ALLOW verdict as a denial."""
    assert capability_api.active_tier() == "free"
    spec = _registry(tmp_path).create("free_tool", "d", {"type": "object"}, IMPL)
    assert spec.name == "free_tool"


def test_g1_binding_depends_on_import_order_KNOWN_BUG():
    """KNOWN BUG (reason 2): importing capability_api before the forge package
    leaves G1 bound to the no-op fallback. Run in a fresh interpreter so this
    process's import history cannot mask either order."""
    probe = (
        "import corvin_operator.license.capability_api as ca\n"
        "from corvin_operator.forge.forge import registry as r\n"
        "print(r.require_capability is ca.require_capability)\n"
    )
    reverse = (
        "from corvin_operator.forge.forge import registry as r\n"
        "import corvin_operator.license.capability_api as ca\n"
        "print(r.require_capability is ca.require_capability)\n"
    )
    run = lambda src: subprocess.run(  # noqa: E731
        [sys.executable, "-c", src], cwd=REPO, capture_output=True, text=True, timeout=120,
    ).stdout.strip().splitlines()[-1]
    assert run(reverse) == "True"     # forge first → real gate bound
    assert run(probe) == "False"      # capability_api first → no-op bound


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


def test_g5_invalid_tenant_is_fail_open_KNOWN_BUG():
    """KNOWN BUG: an invalid tenant yields ENFORCEMENT_UNAVAILABLE (returned, not
    raised) and check_forge_capability only re-raises LicenseDenied — so it
    returns as if allowed. Same root cause as G1 reason 1."""
    from core.orchestration.quota_gate import check_forge_capability

    assert check_forge_capability("", entry_point="test") is None
    decision = capability_api.require_capability("forge.create", tenant_id="", entry_point="test")
    assert decision.decision is capability_api.Decision.ENFORCEMENT_UNAVAILABLE
