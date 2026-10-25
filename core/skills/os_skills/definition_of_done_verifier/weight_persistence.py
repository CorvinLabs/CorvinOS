"""Weight persistence: load/save learned DoD weights per tenant (ADR-2283 decision 3).

The file is ``<tenant_home>/global/forge/dod_weights.json`` where ``tenant_home`` is
``core.paths.tenant.tenant_home(tenant_id)`` — ``corvin_home()`` honours ``CORVIN_HOME``
(ADR-0007). Until 2026-10-25 the default was hard-wired to
``Path.home()/.corvin/tenants/_default/...``: every tenant shared one file, and it lay
outside the live install whenever ``CORVIN_HOME`` pointed elsewhere.

Weights AND their confidence are persisted and loaded back, so the ADR-0723 confidence
gate (a learned weight is used only at confidence > 0.5) survives a restart.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

FILE_NAME = "dod_weights.json"


def weights_path(tenant_id: str) -> Path:
    """``<tenant_home>/global/forge/dod_weights.json`` (tenant validated, CORVIN_HOME honoured)."""
    from core.paths.tenant import tenant_home  # noqa: PLC0415

    return tenant_home(tenant_id) / "global" / "forge" / FILE_NAME


class WeightPersistence:
    """Load/save task-type-specific weights and their confidence for ONE tenant."""

    def __init__(self, storage_path: Optional[Path] = None, *, tenant_id: str = "_default"):
        """``storage_path`` overrides the tenant file (tests); otherwise it is resolved
        under the tenant at construction time."""
        self.tenant_id = tenant_id
        self.storage_path = Path(storage_path) if storage_path is not None else weights_path(tenant_id)

    def load_state(self) -> Dict[str, Dict[str, Any]]:
        """``{"weights": {...}, "confidence": {...}, "sample_count": {...}}`` — each empty
        when the file is missing or unreadable (the defaults then apply)."""
        empty: Dict[str, Dict[str, Any]] = {"weights": {}, "confidence": {}, "sample_count": {}}
        try:
            if not self.storage_path.exists():
                return empty
            with open(self.storage_path, "r") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001 — a corrupt file falls back to the defaults
            print(f"[WARN] Failed to load weights: {e}")
            return empty
        if not isinstance(data, dict):
            return empty
        out = {}
        for key in empty:
            value = data.get(key)
            out[key] = value if isinstance(value, dict) else {}
        return out

    def load_weights(self) -> Dict[str, Dict[str, float]]:
        """``{task_type: {weight_name: value}}`` (empty if the file doesn't exist)."""
        return self.load_state()["weights"]

    def load_confidence(self) -> Dict[str, Dict[str, float]]:
        """``{task_type: {weight_name: confidence}}`` (empty if the file doesn't exist)."""
        return self.load_state()["confidence"]

    def save_weights(
        self,
        weights: Dict[str, Dict[str, float]],
        confidence: Dict[str, Dict[str, float]],
        sample_count: Optional[Dict[str, int]] = None,
    ) -> bool:
        """Persist atomically (temp file + replace, mode 0600). Returns success."""
        data = {
            "version": "0.2.0",
            "tenant_id": self.tenant_id,
            "weights": weights,
            "confidence": confidence,
            "sample_count": dict(sample_count or {}),
        }
        tmp = None
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".dod_weights.", suffix=".tmp",
                                       dir=str(self.storage_path.parent))
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.storage_path)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] Failed to save weights: {e}")
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return False

    def reset_to_defaults(self) -> bool:
        """Delete persisted weights (fallback to defaults)."""
        try:
            if self.storage_path.exists():
                self.storage_path.unlink()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to reset weights: {e}")
            return False


# Unit Tests
class TestWeightPersistence:
    """Test weight persistence."""

    def test_save_and_load(self, tmp_path):
        """Save weights to disk and load them back."""
        persist = WeightPersistence(storage_path=tmp_path / "weights.json")

        weights = {
            "api_endpoint": {
                "w_reach": 0.18,
                "w_audit": 0.30,
            }
        }
        confidence = {
            "api_endpoint": {
                "w_reach": 0.5,
                "w_audit": 0.8,
            }
        }

        # Save
        assert persist.save_weights(weights, confidence) == True

        # Load
        loaded = persist.load_weights()
        assert "api_endpoint" in loaded
        assert loaded["api_endpoint"]["w_audit"] == 0.30
        assert persist.load_confidence()["api_endpoint"]["w_audit"] == 0.8

    def test_load_missing_file(self):
        """Load from non-existent file returns empty dict."""
        persist = WeightPersistence(storage_path=Path("/tmp/nonexistent_weights_xyz.json"))
        loaded = persist.load_weights()
        assert loaded == {}

    def test_reset_to_defaults(self, tmp_path):
        """Reset removes persisted weights."""
        persist = WeightPersistence(storage_path=tmp_path / "weights.json")

        # Save something
        persist.save_weights({"api_endpoint": {"w_audit": 0.30}}, {})
        assert persist.storage_path.exists()

        # Reset
        assert persist.reset_to_defaults() == True
        assert not persist.storage_path.exists()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
