"""Unit tests for hermes_bootstrap.py — ADR-0125.

Tests RAM detection, model selection, and the full bootstrap flow
with mocked subprocess calls. No real ollama invocations occur.

Run: python3 -m pytest core/console/tests/test_hermes_bootstrap_adr0125.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from hermes_bootstrap import (
    bootstrap_hermes,
    get_available_ram_gb,
    install_ollama,
    is_ollama_installed,
    pull_model,
    select_model_for_ram,
)


# ---------------------------------------------------------------------------
# get_available_ram_gb
# ---------------------------------------------------------------------------

class TestGetAvailableRamGb(unittest.TestCase):

    def test_reads_meminfo(self):
        meminfo = "MemTotal:       16384000 kB\nMemFree:        8192000 kB\n"
        mock_path = MagicMock()
        mock_path.return_value.read_text.return_value = meminfo
        with patch("hermes_bootstrap.Path", mock_path):
            ram = get_available_ram_gb()
        # 16384000 kB → 16384000 / 1024 / 1024 ≈ 15.625 GB
        self.assertAlmostEqual(ram, 16384000 / 1024 / 1024, places=2)

    def test_fallback_on_missing_file(self):
        mock_path = MagicMock()
        mock_path.return_value.read_text.side_effect = FileNotFoundError
        with patch("hermes_bootstrap.Path", mock_path):
            ram = get_available_ram_gb()
        self.assertEqual(ram, 4.0)

    def test_fallback_on_malformed_meminfo(self):
        """If MemTotal line is missing, fall back to 4.0."""
        meminfo = "MemFree: 8192000 kB\n"
        mock_path = MagicMock()
        mock_path.return_value.read_text.return_value = meminfo
        with patch("hermes_bootstrap.Path", mock_path):
            ram = get_available_ram_gb()
        self.assertEqual(ram, 4.0)


# ---------------------------------------------------------------------------
# select_model_for_ram
# ---------------------------------------------------------------------------

class TestSelectModelForRam(unittest.TestCase):
    # 2-tier model aligned with HermesEngine.HERMES_MODEL_ALIASES (canonical
    # default qwen3:8b, qwen3:1.7b for small RAM). Updated from the legacy
    # 3-tier qwen2.5 mapping (security review 2026-06-27 — test-vs-code drift).

    def test_low_ram_selects_fast(self):
        self.assertEqual(select_model_for_ram(2.5), "qwen3:1.7b")

    def test_boundary_below_6_selects_fast(self):
        self.assertEqual(select_model_for_ram(5.9), "qwen3:1.7b")

    def test_exactly_6_selects_balanced(self):
        # 6 GB is the lower bound of the balanced tier.
        self.assertEqual(select_model_for_ram(6.0), "qwen3:8b")

    def test_mid_range_selects_balanced(self):
        self.assertEqual(select_model_for_ram(8.0), "qwen3:8b")

    def test_high_ram_selects_balanced(self):
        self.assertEqual(select_model_for_ram(32.0), "qwen3:8b")


# ---------------------------------------------------------------------------
# is_ollama_installed + install_ollama
# ---------------------------------------------------------------------------

class TestIsOllamaInstalled(unittest.TestCase):

    def test_true_when_binary_found(self):
        with patch("hermes_bootstrap.shutil.which", return_value="/usr/bin/ollama"):
            self.assertTrue(is_ollama_installed())

    def test_false_when_binary_missing(self):
        # is_ollama_installed() delegates to _ollama_bin(), which probes PATH
        # AND known per-platform install paths (so a fresh-install binary not yet
        # on the process PATH is still found). Patching shutil.which alone is
        # insufficient when ollama is actually installed on the test host — mock
        # the resolver (security review 2026-06-27).
        with patch("hermes_bootstrap._ollama_bin", return_value=None):
            self.assertFalse(is_ollama_installed())


class TestInstallOllama(unittest.TestCase):

    def test_success(self):
        with patch("hermes_bootstrap.subprocess.run",
                   return_value=MagicMock(returncode=0)):
            self.assertTrue(install_ollama())

    def test_failure_on_nonzero_rc(self):
        with patch("hermes_bootstrap.subprocess.run",
                   return_value=MagicMock(returncode=1)):
            self.assertFalse(install_ollama())

    def test_failure_on_exception(self):
        with patch("hermes_bootstrap.subprocess.run",
                   side_effect=FileNotFoundError("curl not found")):
            self.assertFalse(install_ollama())


# ---------------------------------------------------------------------------
# pull_model
# ---------------------------------------------------------------------------

class TestPullModel(unittest.TestCase):

    def test_success(self):
        with patch("hermes_bootstrap.subprocess.run",
                   return_value=MagicMock(returncode=0)):
            self.assertTrue(pull_model("qwen2.5:7b"))

    def test_failure_on_nonzero_rc(self):
        with patch("hermes_bootstrap.subprocess.run",
                   return_value=MagicMock(returncode=1)):
            self.assertFalse(pull_model("qwen2.5:7b"))

    def test_failure_on_timeout(self):
        import subprocess
        with patch("hermes_bootstrap.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["ollama", "pull"], 600)):
            self.assertFalse(pull_model("qwen2.5:7b"))


# ---------------------------------------------------------------------------
# bootstrap_hermes — full flow
# ---------------------------------------------------------------------------

class TestBootstrapHermes(unittest.TestCase):

    def _patch_ram(self, gb: float):
        return patch("hermes_bootstrap.get_available_ram_gb", return_value=gb)

    def test_already_installed_pull_success(self):
        with (
            self._patch_ram(8.0),
            patch("hermes_bootstrap.is_ollama_installed", return_value=True),
            patch("hermes_bootstrap.pull_model", return_value=True),
        ):
            result = bootstrap_hermes()
        self.assertIsNone(result["error"])
        self.assertTrue(result["model_pulled"])
        self.assertTrue(result["ollama_installed"])
        self.assertEqual(result["model_selected"], "qwen3:8b")

    def test_already_installed_pull_fails(self):
        with (
            self._patch_ram(8.0),
            patch("hermes_bootstrap.is_ollama_installed", return_value=True),
            patch("hermes_bootstrap.pull_model", return_value=False),
        ):
            result = bootstrap_hermes()
        self.assertFalse(result["model_pulled"])
        self.assertIsNotNone(result["error"])

    def test_not_installed_install_then_pull(self):
        with (
            self._patch_ram(16.0),
            patch("hermes_bootstrap.is_ollama_installed", return_value=False),
            patch("hermes_bootstrap.install_ollama", return_value=True),
            patch("hermes_bootstrap.pull_model", return_value=True),
        ):
            result = bootstrap_hermes()
        self.assertTrue(result["ollama_installed"])
        self.assertTrue(result["model_pulled"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["model_selected"], "qwen3:8b")

    def test_not_installed_install_fails(self):
        with (
            self._patch_ram(8.0),
            patch("hermes_bootstrap.is_ollama_installed", return_value=False),
            patch("hermes_bootstrap.install_ollama", return_value=False),
        ):
            result = bootstrap_hermes()
        self.assertFalse(result["ollama_installed"])
        self.assertFalse(result["model_pulled"])
        self.assertIsNotNone(result["error"])

    def test_force_model_overrides_ram_selection(self):
        with (
            self._patch_ram(2.0),
            patch("hermes_bootstrap.is_ollama_installed", return_value=True),
            patch("hermes_bootstrap.pull_model", return_value=True),
        ):
            result = bootstrap_hermes(force_model="qwen2.5:14b")
        # RAM would select 3b but force_model wins
        self.assertEqual(result["model_selected"], "qwen2.5:14b")

    def test_result_contains_ram_gb(self):
        with (
            self._patch_ram(12.5),
            patch("hermes_bootstrap.is_ollama_installed", return_value=True),
            patch("hermes_bootstrap.pull_model", return_value=True),
        ):
            result = bootstrap_hermes()
        self.assertAlmostEqual(result["ram_gb"], 12.5)

    def test_low_ram_selects_fast(self):
        with (
            self._patch_ram(4.0),
            patch("hermes_bootstrap.is_ollama_installed", return_value=True),
            patch("hermes_bootstrap.pull_model", return_value=True),
        ):
            result = bootstrap_hermes()
        self.assertEqual(result["model_selected"], "qwen3:1.7b")

    def test_no_exception_propagation(self):
        """bootstrap_hermes must never raise — all errors go into result['error']."""
        with (
            self._patch_ram(8.0),
            patch("hermes_bootstrap.is_ollama_installed",
                  side_effect=RuntimeError("unexpected failure")),
        ):
            try:
                result = bootstrap_hermes()
                # Should return error dict, not raise
                self.assertIsNotNone(result.get("error"))
            except Exception as e:
                self.fail(f"bootstrap_hermes raised unexpectedly: {e}")


if __name__ == "__main__":
    unittest.main()
