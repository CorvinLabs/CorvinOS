"""Feature-flag registry + worker-engine selection (CLAUDE.md § Feature Flags).

Pins the two load-bearing properties:

  * every registered flag is OFF until somebody turns it on — on a fresh
    install AND after an upgrade (absent key never means "on");
  * compliance/security mechanisms cannot enter the registry at all.

Run: python3 -m pytest core/console/tests/test_feature_flags.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from corvin_console import feature_flags as ff  # noqa: E402


@pytest.fixture()
def tenant_home(tmp_path, monkeypatch):
    """Point forge paths at a throwaway CORVIN_HOME (never the live one)."""
    home = tmp_path / "corvin"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    ff._spec_cache.clear()
    yield home
    ff._spec_cache.clear()


def _write_yaml(home: Path, body: str) -> None:
    p = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    p.write_text(body, encoding="utf-8")
    ff._spec_cache.clear()


# ── Registry invariants ───────────────────────────────────────────────────

def test_every_registered_flag_defaults_off():
    assert ff.REGISTRY, "registry should not be empty"
    for entry in ff.REGISTRY:
        assert entry.default is False, f"{entry.id} must ship dark"


def test_every_flag_has_owner_and_target_release():
    for entry in ff.REGISTRY:
        assert entry.owner
        assert entry.target_release


def test_registry_rejects_a_default_on_flag():
    bad = ff.FeatureFlag(id="something_new", label="x", description="x",
                         owner="me", target_release="0.11", default=True)
    with pytest.raises(ValueError, match="must default to False"):
        ff._validate_registry((bad,))


@pytest.mark.parametrize("flag_id", [
    "audit_chain_light",
    "house_rules_bypass",
    "path_gate_relaxed",
    "consent_autoadmit",
    "disclosure_optional",
    "flow_guard_off",
    "license_free_mode",
    "compliance_lite",
    "gdpr_erasure_skip",
])
def test_registry_refuses_compliance_mechanisms(flag_id):
    """A compliance mechanism must never become an operator toggle."""
    entry = ff.FeatureFlag(id=flag_id, label="x", description="x",
                           owner="me", target_release="0.11")
    with pytest.raises(ff.ProtectedMechanismError):
        ff._validate_registry((entry,))


def test_registry_rejects_duplicate_ids():
    a = ff.FeatureFlag(id="dup_flag", label="a", description="a",
                       owner="me", target_release="0.11")
    with pytest.raises(ValueError, match="duplicate"):
        ff._validate_registry((a, a))


# ── Resolution ────────────────────────────────────────────────────────────

def test_fresh_install_has_everything_off(tenant_home):
    for entry in ff.REGISTRY:
        assert ff.is_enabled(entry.id) is False


def test_unknown_flag_is_false_not_an_exception(tenant_home):
    assert ff.is_enabled("no_such_flag") is False


def test_absent_key_never_means_on(tenant_home):
    _write_yaml(tenant_home, "spec:\n  features: {}\n")
    for entry in ff.REGISTRY:
        assert ff.is_enabled(entry.id) is False


def test_tenant_yaml_can_enable(tenant_home):
    fid = ff.REGISTRY[0].id
    _write_yaml(tenant_home, f"spec:\n  features:\n    {fid}: true\n")
    assert ff.is_enabled(fid) is True
    states = {f["id"]: f for f in ff.describe_all()}
    assert states[fid]["source"] == "tenant_yaml"


def test_console_overlay_beats_tenant_yaml(tenant_home):
    fid = ff.REGISTRY[0].id
    _write_yaml(tenant_home, f"spec:\n  features:\n    {fid}: true\n")
    ff.set_enabled(fid, False)
    assert ff.is_enabled(fid) is False
    states = {f["id"]: f for f in ff.describe_all()}
    assert states[fid]["source"] == "console"


def test_set_enabled_roundtrip_and_persistence(tenant_home):
    fid = ff.REGISTRY[0].id
    ff.set_enabled(fid, True)
    assert ff.is_enabled(fid) is True
    overlay = json.loads(
        (tenant_home / "tenants" / "_default" / "global" / "features.json")
        .read_text(encoding="utf-8"))
    assert overlay["flags"][fid] is True
    ff.set_enabled(fid, False)
    assert ff.is_enabled(fid) is False


def test_set_enabled_rejects_unregistered_id(tenant_home):
    with pytest.raises(ff.UnknownFlagError):
        ff.set_enabled("not_a_registered_flag", True)


def test_corrupt_overlay_degrades_to_off(tenant_home):
    (tenant_home / "tenants" / "_default" / "global" / "features.json").write_text(
        "{not json", encoding="utf-8")
    for entry in ff.REGISTRY:
        assert ff.is_enabled(entry.id) is False


# ── Worker engine ─────────────────────────────────────────────────────────

def test_worker_engine_defaults_to_native(tenant_home):
    assert ff.worker_engine_mode() == "native"
    assert ff.WORKER_ENGINE_DEFAULT == "native"


def test_worker_engine_from_tenant_yaml(tenant_home):
    _write_yaml(tenant_home, "spec:\n  web_chat:\n    worker_engine: acs\n")
    assert ff.worker_engine_mode() == "acs"


def test_worker_engine_overlay_beats_yaml(tenant_home):
    _write_yaml(tenant_home, "spec:\n  web_chat:\n    worker_engine: acs\n")
    ff.set_worker_engine_mode("tde")
    assert ff.worker_engine_mode() == "tde"


def test_unknown_worker_engine_in_config_degrades_to_native(tenant_home):
    _write_yaml(tenant_home, "spec:\n  web_chat:\n    worker_engine: quantum\n")
    assert ff.worker_engine_mode() == "native"


def test_set_worker_engine_rejects_unknown_mode(tenant_home):
    with pytest.raises(ValueError):
        ff.set_worker_engine_mode("quantum")
    assert ff.worker_engine_mode() == "native"


def test_worker_engine_is_per_tenant(tenant_home):
    (tenant_home / "tenants" / "other" / "global").mkdir(parents=True)
    ff.set_worker_engine_mode("acs", "_default")
    assert ff.worker_engine_mode("_default") == "acs"
    assert ff.worker_engine_mode("other") == "native"



# ── Wiring: each flag must actually gate its feature, in BOTH states ───────
#
# A flag that is only ever tested in one state rots (CLAUDE.md § Feature
# Flags). These assert the call site reads the flag at all — the behavioural
# on/off pairs live next to each feature's own tests.

def test_registry_covers_the_shipped_dark_features():
    ids = {f.id for f in ff.REGISTRY}
    for expected in ("ccc_command_routing", "acs_context_sync",
                     "browser_automation", "execution_context_badge"):
        assert expected in ids, f"{expected} missing from the registry"


def test_ccc_is_gated_by_its_flag():
    src = (_REPO / "core" / "console" / "corvin_console" / "chat_runtime.py").read_text(
        encoding="utf-8")
    assert 'is_enabled("ccc_command_routing"' in src, (
        "CCC entity extraction must read its feature flag")
    # The legacy env kill switch may still force OFF, but must not be the
    # only gate — otherwise the feature is on by default again.
    ccc_line = [ln for ln in src.splitlines()
                if 'is_enabled("ccc_command_routing"' in ln]
    assert ccc_line, "flag check not found on its own line"


def test_context_sync_is_gated_by_its_flag():
    src = (_REPO / "core" / "console" / "corvin_console" / "chat_runtime.py").read_text(
        encoding="utf-8")
    assert src.count('is_enabled("acs_context_sync"') >= 2, (
        "both delegated branches (TDE + ACS) must gate the ADR-0213 sync")


def test_browser_is_gated_at_session_creation():
    src = (_REPO / "core" / "console" / "corvin_console" / "routes" / "browser.py").read_text(
        encoding="utf-8")
    assert 'is_enabled("browser_automation"' in src, (
        "browser session creation must read its feature flag")
    # Gate must sit in create_session — every other route needs a session id.
    head = src.split("async def create_session", 1)[1][:1200]
    assert 'is_enabled("browser_automation"' in head, (
        "the gate must be inside create_session, not somewhere later")


def test_flag_off_is_the_default_for_all_new_flags(tenant_home):
    """The whole point: a fresh install runs none of these."""
    for fid in ("ccc_command_routing", "acs_context_sync", "browser_automation"):
        assert ff.is_enabled(fid) is False


def test_flag_on_is_reachable_for_all_new_flags(tenant_home):
    """…and each one can actually be switched on again (no dead flag)."""
    for fid in ("ccc_command_routing", "acs_context_sync", "browser_automation"):
        ff.set_enabled(fid, True)
        assert ff.is_enabled(fid) is True
        ff.set_enabled(fid, False)
        assert ff.is_enabled(fid) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
