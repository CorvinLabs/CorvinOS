"""Weight persistence: load/save learned weights to disk."""

import json
from pathlib import Path
from typing import Dict, Optional


class WeightPersistence:
    """Load/save task-type-specific weights."""

    def __init__(self, storage_path: Path = None):
        """Initialize persistence layer."""
        if storage_path is None:
            storage_path = (
                Path.home() / ".corvin" / "tenants" / "_default" /
                "global" / "forge" / "dod_weights.json"
            )
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def load_weights(self) -> Dict[str, Dict[str, float]]:
        """
        Load persisted weights from disk.

        Returns: {task_type: {weight_name: value, ...}, ...}
        Fallback: return empty dict if file doesn't exist
        """
        try:
            if self.storage_path.exists():
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                    return data.get("weights", {})
            else:
                return {}
        except Exception as e:
            print(f"[WARN] Failed to load weights: {e}")
            return {}

    def save_weights(self, weights: Dict[str, Dict[str, float]], confidence: Dict[str, Dict[str, float]]) -> bool:
        """
        Persist weights to disk.

        Args:
            weights: {task_type: {weight_name: value, ...}, ...}
            confidence: {task_type: {weight_name: confidence, ...}, ...}

        Returns: bool (success/failure)
        """
        try:
            data = {
                "version": "0.1.0",
                "weights": weights,
                "confidence": confidence,
            }
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save weights: {e}")
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
