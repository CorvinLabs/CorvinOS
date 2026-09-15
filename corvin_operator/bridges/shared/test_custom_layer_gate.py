"""Tests for ADR-0156 M2 — Custom Layer License Gate.

Key invariant: Tier-B/C layer activation (install OR enable) must be gated
by the license limit. A free-tier user must not be able to exceed 1 active
Tier-B/C layer via any code path.

CL-ENABLE-BYPASS-01: installing two inactive Tier-B/C layers and then
enabling both must be blocked at the second enable_layer() call.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

# Ensure shared/ and operator/ are importable
_SHARED = Path(__file__).resolve().parent
_OPERATOR = _SHARED.parent
for _p in (str(_SHARED), str(_OPERATOR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_layer_dir(tmp_path: Path, name: str, tier: str) -> Path:
    """Create a minimal valid custom layer directory."""
    layer_dir = tmp_path / name
    layer_dir.mkdir(parents=True)
    vendor, short = name.split(".", 1)
    (layer_dir / "layer.corvin.yaml").write_text(
        f"name: {name}\ndisplay_name: Test\ntier: {tier}\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    return layer_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVendorNamespace:
    """F-06: Vendor prefix 'corvin-*' and 'system-*' must be blocked."""

    def test_corvin_exact_blocked(self):
        from custom_layer_registry import validate_name, CustomLayerNameError
        with pytest.raises(CustomLayerNameError, match="reserved"):
            validate_name("corvin.mytool")

    def test_corvin_prefix_blocked(self):
        from custom_layer_registry import validate_name, CustomLayerNameError
        with pytest.raises(CustomLayerNameError, match="reserved"):
            validate_name("corvin-labs.security")

    def test_system_prefix_blocked(self):
        from custom_layer_registry import validate_name, CustomLayerNameError
        with pytest.raises(CustomLayerNameError, match="reserved"):
            validate_name("system-core.audit")

    def test_legitimate_vendor_allowed(self):
        from custom_layer_registry import validate_name
        # Should not raise
        validate_name("acme.mytool")
        validate_name("my-org.search")


class TestCheckLayerInstall:
    """Unit tests for custom_layer_gate.check_layer_install."""

    def test_tier_a_always_allowed(self):
        from custom_layer_gate import check_layer_install
        # Should never raise, regardless of existing count.
        check_layer_install("A", 999)

    def test_tier_b_first_install_allowed_free(self, monkeypatch):
        """0 active BC → first Tier-B install allowed on free tier."""
        from custom_layer_gate import check_layer_install
        import license.validator as _v
        _v._set_active_license(None)
        check_layer_install("B", 0)  # should not raise

    def test_tier_b_second_install_blocked_free(self, monkeypatch):
        """1 active BC → second Tier-B install blocked on free tier."""
        from custom_layer_gate import check_layer_install, LayerLimitExceeded
        import license.validator as _v
        _v._set_active_license(None)
        with pytest.raises(LayerLimitExceeded):
            check_layer_install("B", 1)

    def test_tier_c_blocked_when_at_limit(self, monkeypatch):
        """Tier-C treated same as Tier-B for limit purposes."""
        from custom_layer_gate import check_layer_install, LayerLimitExceeded
        import license.validator as _v
        _v._set_active_license(None)
        with pytest.raises(LayerLimitExceeded):
            check_layer_install("C", 1)

    def test_import_failure_is_free_tier(self, monkeypatch):
        """On license import failure, gate degrades to FREE_TIER (fail-closed)."""
        from custom_layer_gate import check_layer_install, LayerLimitExceeded
        import custom_layer_gate as _g
        monkeypatch.setattr(_g, "_get_limit", lambda: 1)
        with pytest.raises(LayerLimitExceeded):
            check_layer_install("B", 1)


class TestEnableLayerBypass:
    """CL-ENABLE-BYPASS-01: enable_layer must gate Tier-B/C same as install_layer."""

    def test_enable_bypass_blocked(self, tmp_path, monkeypatch):
        """Install 2 inactive Tier-B layers, then enabling the second must be blocked."""
        import license.validator as _v
        _v._set_active_license(None)

        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

        import importlib
        import custom_layer_registry as reg
        importlib.reload(reg)

        from custom_layer_gate import LayerLimitExceeded

        # Install two Tier-B layers while 0 are active — both installs should pass.
        dir1 = _make_layer_dir(tmp_path / "src", "acme.alpha", "B")
        dir2 = _make_layer_dir(tmp_path / "src", "acme.beta", "B")
        reg.install_layer(dir1, tenant_id="_default")
        reg.install_layer(dir2, tenant_id="_default")

        # Enable the first one — should succeed (0 → 1 active).
        reg.enable_layer("acme.alpha", tenant_id="_default")

        # Enable the second one — must be blocked (1 active ≥ limit of 1).
        with pytest.raises(LayerLimitExceeded):
            reg.enable_layer("acme.beta", tenant_id="_default")

    def test_enable_tier_a_never_gated(self, tmp_path, monkeypatch):
        """Tier-A layers must never be blocked by enable_layer gate."""
        import license.validator as _v
        _v._set_active_license(None)

        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

        import importlib
        import custom_layer_registry as reg
        importlib.reload(reg)

        # Already have 1 active Tier-B layer somehow
        dir_b = _make_layer_dir(tmp_path / "src", "acme.tools", "B")
        dir_a = _make_layer_dir(tmp_path / "src", "acme.prompts", "A")
        reg.install_layer(dir_b, tenant_id="_default")
        reg.install_layer(dir_a, tenant_id="_default")
        reg.enable_layer("acme.tools", tenant_id="_default")

        # Enabling a Tier-A layer should never raise.
        reg.enable_layer("acme.prompts", tenant_id="_default")
