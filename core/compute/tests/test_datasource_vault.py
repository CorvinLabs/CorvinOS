"""Tests for vault_env — secret key presence check + bwrap env injection (ADR-0026 D).

15 test cases:
- check_vault_keys_present: all keys present → OK
- missing key → MissingSecret
- vault file mode 0600 enforced
- values NOT returned by check_vault_keys_present
- get_vault_env_for_bwrap returns values correctly
- vault missing entirely → MissingSecret
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.fabric.datasources.vault_env import (
    MissingSecret,
    check_vault_keys_present,
    get_vault_env_for_bwrap,
)


def _write_vault(path: Path, data: dict, mode: int = 0o600) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(path, mode)


class TestCheckVaultKeysPresent(unittest.TestCase):
    def test_all_keys_present_passes(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"A": "val_a", "B": "val_b"})
            # Should not raise
            check_vault_keys_present(["A", "B"], vault)

    def test_missing_key_raises(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"A": "val_a"})
            with self.assertRaises(MissingSecret):
                check_vault_keys_present(["A", "MISSING"], vault)

    def test_empty_key_list_passes(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"A": "val_a"})
            check_vault_keys_present([], vault)

    def test_vault_not_found_raises(self):
        with self.assertRaises(MissingSecret):
            check_vault_keys_present(["A"], Path("/tmp/nonexistent_vault_xyz.json"))

    def test_vault_wrong_mode_raises_permission_error(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"A": "val_a"}, mode=0o644)
            with self.assertRaises(PermissionError):
                check_vault_keys_present(["A"], vault)

    def test_check_does_not_return_values(self):
        """check_vault_keys_present returns None — not the secret values."""
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"SECRET": "super_secret_value"})
            result = check_vault_keys_present(["SECRET"], vault)
            self.assertIsNone(result)

    def test_partial_keys_present(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"A": "a", "B": "b", "C": "c"})
            # These should pass
            check_vault_keys_present(["A", "B"], vault)
            # This should fail
            with self.assertRaises(MissingSecret):
                check_vault_keys_present(["A", "D"], vault)


class TestVaultFileMode(unittest.TestCase):
    def test_mode_0600_enforced_on_read(self):
        """Vault with 0644 mode should raise PermissionError."""
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"K": "v"}, mode=0o644)
            with self.assertRaises(PermissionError):
                check_vault_keys_present(["K"], vault)

    def test_mode_0600_passes(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"K": "v"}, mode=0o600)
            check_vault_keys_present(["K"], vault)

    def test_mode_0640_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"K": "v"}, mode=0o640)
            with self.assertRaises(PermissionError):
                check_vault_keys_present(["K"], vault)


class TestGetVaultEnvForBwrap(unittest.TestCase):
    def test_returns_correct_values(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"DB_USER": "admin", "DB_PASS": "s3cr3t"})
            result = get_vault_env_for_bwrap(["DB_USER", "DB_PASS"], vault)
            self.assertEqual(result["DB_USER"], "admin")
            self.assertEqual(result["DB_PASS"], "s3cr3t")

    def test_missing_key_raises(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"A": "a"})
            with self.assertRaises(MissingSecret):
                get_vault_env_for_bwrap(["A", "MISSING"], vault)

    def test_returns_only_requested_keys(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"A": "a", "B": "b", "C": "c"})
            result = get_vault_env_for_bwrap(["A"], vault)
            self.assertIn("A", result)
            self.assertNotIn("B", result)
            self.assertNotIn("C", result)

    def test_vault_missing_raises(self):
        with self.assertRaises(MissingSecret):
            get_vault_env_for_bwrap(["A"], Path("/tmp/no_such_vault_abc.json"))

    def test_wrong_mode_raises(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault.json"
            _write_vault(vault, {"A": "a"}, mode=0o644)
            with self.assertRaises(PermissionError):
                get_vault_env_for_bwrap(["A"], vault)


if __name__ == "__main__":
    unittest.main(verbosity=2)
