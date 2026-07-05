"""Tests for hermes_healing module (ACO L5 Tier LOCAL self-repair)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from hermes_healing import (
    get_available_models,
    get_health_status,
    has_hermes_model,
    is_hermes_reachable,
    repair_hermes,
    diagnose_hermes,
)


class TestHermesReachability:
    """Test Ollama API connectivity checks."""

    def test_is_hermes_reachable_success(self):
        """Hermes is reachable when Ollama API returns 200."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp
            assert is_hermes_reachable() is True

    def test_is_hermes_reachable_timeout(self):
        """Hermes is not reachable when connection times out."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = TimeoutError()
            assert is_hermes_reachable() is False

    def test_is_hermes_reachable_connection_refused(self):
        """Hermes is not reachable when connection is refused."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = ConnectionRefusedError()
            assert is_hermes_reachable() is False


class TestModelAvailability:
    """Test Ollama model detection."""

    def test_get_available_models_success(self):
        """Parse model list from Ollama API."""
        mock_response = {
            "models": [
                {"name": "qwen3:8b"},
                {"name": "qwen3:1.7b"},
                {"name": "llama2:7b"},
            ]
        }
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_open.return_value = mock_resp

            models = get_available_models()
            assert len(models) == 3
            assert "qwen3:8b" in models

    def test_get_available_models_empty(self):
        """Return empty list when Ollama is unreachable."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = ConnectionRefusedError()
            models = get_available_models()
            assert models == []

    def test_has_hermes_model_found(self):
        """Detect when qwen3 model is present."""
        models = ["qwen3:8b", "llama2:7b"]
        assert has_hermes_model(models) is True

    def test_has_hermes_model_not_found(self):
        """Return False when qwen3 is not present."""
        models = ["llama2:7b", "mistral:7b"]
        assert has_hermes_model(models) is False

    def test_has_hermes_model_empty_list(self):
        """Return False for empty model list (mock get_available_models so live Ollama can't contaminate)."""
        with patch("hermes_healing.get_available_models", return_value=[]):
            assert has_hermes_model([]) is False


class TestHealthStatus:
    """Test health status reporting."""

    def test_health_status_healthy(self):
        """Report healthy when Ollama is reachable with models."""
        with patch("hermes_healing.is_hermes_reachable") as mock_reach, \
             patch("hermes_healing.get_available_models") as mock_models, \
             patch("hermes_healing.has_hermes_model") as mock_has:
            mock_reach.return_value = True
            mock_models.return_value = ["qwen3:8b"]
            mock_has.return_value = True

            status = get_health_status()
            assert status["reachable"] is True
            assert status["has_model"] is True

    def test_health_status_server_down(self):
        """Report unhealthy when server is unreachable."""
        with patch("hermes_healing.is_hermes_reachable") as mock_reach, \
             patch("hermes_healing.get_available_models") as mock_models:
            mock_reach.return_value = False
            mock_models.return_value = []

            status = get_health_status()
            assert status["reachable"] is False
            assert status["has_model"] is False


class TestRepairHermes:
    """Test automated repair logic."""

    def test_repair_already_healthy(self):
        """No repair needed when system is healthy."""
        with patch("hermes_healing.is_hermes_reachable") as mock_reach, \
             patch("hermes_healing.has_hermes_model") as mock_has:
            mock_reach.return_value = True
            mock_has.return_value = True

            result = repair_hermes()
            assert result["reachable"] is True
            assert result["error"] is None

    def test_repair_failed_import(self):
        """Graceful fallback when hermes_bootstrap is not available."""
        with patch("builtins.__import__", side_effect=ImportError):
            result = repair_hermes()
            assert result["error"] is not None
            assert "Repair failed" in result["error"]


class TestDiagnosis:
    """Test human-readable diagnostic output."""

    def test_diagnose_healthy(self):
        """Healthy system shows checkmark."""
        with patch("hermes_healing.get_health_status") as mock_status:
            mock_status.return_value = {
                "reachable": True,
                "has_model": True,
                "model_count": 2,
            }
            diag = diagnose_hermes()
            assert "✓" in diag
            assert "healthy" in diag.lower()

    def test_diagnose_server_down(self):
        """Server down shows cross and action."""
        with patch("hermes_healing.get_health_status") as mock_status:
            mock_status.return_value = {
                "reachable": False,
                "has_model": False,
                "model_count": 0,
            }
            diag = diagnose_hermes()
            assert "✗" in diag
            assert "unreachable" in diag.lower()

    def test_diagnose_model_missing(self):
        """Missing model shows warning."""
        with patch("hermes_healing.get_health_status") as mock_status:
            mock_status.return_value = {
                "reachable": True,
                "has_model": False,
                "model_count": 1,
            }
            diag = diagnose_hermes()
            assert "⚠" in diag
            assert "no hermes model" in diag.lower()
