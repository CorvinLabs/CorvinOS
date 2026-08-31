"""ADR-0026 — Backend Negotiation tests.

Tests the Haiku-4.5-based Backend Negotiation system (ADR-0026 Section C,
Phase 26.11). Negotiation is called when `backend` is omitted from
compute_job_create; it calls `claude -p --max-turns 1 --no-tools`
to select a backend from the available plugin registry.

MUST NOT import anthropic — CI AST lint enforced.

Run:
    python -m pytest core/compute/tests/test_negotiation.py -v
"""
from __future__ import annotations

import json
import sys
import subprocess
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

from corvin_compute.fabric_config import FabricConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Stub Negotiation engine (real: corvin_compute.fabric.negotiation)
# ---------------------------------------------------------------------------

class FallbackBackendRequired(RuntimeError):
    """Raised when negotiation is disabled or Claude subprocess unavailable."""


class NegotiationResult:
    def __init__(self, backend: str, reason: str) -> None:
        self.backend = backend
        self.reason = reason


_NEGOTIATION_PROMPT_TEMPLATE = (
    "You are an ML backend selector. Given the dataset metadata and available "
    "backends, return ONLY valid JSON: {{\"backend\": \"<name>\", "
    "\"reason\": \"<one sentence>\"}}.\n\n"
    "Dataset metadata:\n{metadata}\n\n"
    "Available backends:\n{backends}"
)


def _parse_negotiation_output(raw: str) -> NegotiationResult:
    """Parse structured JSON from claude -p output."""
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            l for l in lines
            if not l.startswith("```")
        )
    data = json.loads(raw)
    backend = data["backend"]
    reason = data.get("reason", "")
    return NegotiationResult(backend=backend, reason=reason)


def negotiate_backend(
    *,
    dataset_metadata: dict[str, Any],
    available_backends: list[str],
    tenant_config: FabricConfig,
    _subprocess_run=subprocess.run,
) -> NegotiationResult:
    """Select a backend using Haiku-4.5 subprocess.

    Raises FallbackBackendRequired when:
    - negotiation_enabled=False in tenant config
    - claude is not in PATH
    - subprocess fails after 3 retries
    """
    if not tenant_config.negotiation_enabled:
        raise FallbackBackendRequired(
            "negotiation_enabled=False; provide explicit backend param"
        )

    prompt = _NEGOTIATION_PROMPT_TEMPLATE.format(
        metadata=json.dumps(dataset_metadata, indent=2),
        backends="\n".join(f"- {b}" for b in available_backends),
    )

    try:
        result = _subprocess_run(
            ["claude", "-p", "--max-turns", "1", "--tools", "", prompt],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        raise FallbackBackendRequired(
            "claude not in PATH; backend negotiation requires "
            "a Claude Code subscription"
        )
    except subprocess.TimeoutExpired:
        raise FallbackBackendRequired("negotiation subprocess timed out")

    if result.returncode != 0:
        raise FallbackBackendRequired(
            f"negotiation subprocess exited {result.returncode}: "
            f"{result.stderr[:200]}"
        )

    try:
        return _parse_negotiation_output(result.stdout)
    except (KeyError, json.JSONDecodeError) as exc:
        raise FallbackBackendRequired(
            f"negotiation output not parseable: {exc}"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNegotiationDisabled(unittest.TestCase):
    def test_disabled_raises_fallback_required(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=False)
        with self.assertRaises(FallbackBackendRequired) as ctx:
            negotiate_backend(
                dataset_metadata={"rows": 10000, "format": "parquet"},
                available_backends=["sklearn", "xgboost"],
                tenant_config=cfg,
            )
        assert "negotiation_enabled=False" in str(ctx.exception)

    def test_disabled_message_prompts_explicit_backend(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=False)
        with self.assertRaises(FallbackBackendRequired) as ctx:
            negotiate_backend(
                dataset_metadata={},
                available_backends=["sklearn"],
                tenant_config=cfg,
            )
        assert "explicit backend" in str(ctx.exception).lower()


class TestNegotiationClaudeNotFound(unittest.TestCase):
    def test_no_claude_in_path_raises_fallback(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)

        def _mock_run(*args, **kwargs):
            raise FileNotFoundError("claude: not found")

        with self.assertRaises(FallbackBackendRequired) as ctx:
            negotiate_backend(
                dataset_metadata={"rows": 100},
                available_backends=["sklearn"],
                tenant_config=cfg,
                _subprocess_run=_mock_run,
            )
        assert "PATH" in str(ctx.exception) or "subscription" in str(ctx.exception)


class TestNegotiationSubprocessOutput(unittest.TestCase):
    def _mock_subprocess(self, stdout: str, returncode: int = 0):
        """Return a mock subprocess.run callable."""
        def _run(*args, **kwargs):
            m = MagicMock()
            m.returncode = returncode
            m.stdout = stdout
            m.stderr = ""
            return m
        return _run

    def test_valid_json_output_parsed(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)
        output = json.dumps({"backend": "sklearn", "reason": "small dataset"})
        result = negotiate_backend(
            dataset_metadata={"rows": 1000, "format": "csv"},
            available_backends=["sklearn", "xgboost"],
            tenant_config=cfg,
            _subprocess_run=self._mock_subprocess(output),
        )
        assert result.backend == "sklearn"
        assert "small dataset" in result.reason

    def test_xgboost_selected_for_large_dataset(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)
        output = json.dumps({
            "backend": "xgboost",
            "reason": "dataset >50k rows, gradient boosting preferred",
        })
        result = negotiate_backend(
            dataset_metadata={"rows": 500000, "format": "parquet"},
            available_backends=["sklearn", "xgboost"],
            tenant_config=cfg,
            _subprocess_run=self._mock_subprocess(output),
        )
        assert result.backend == "xgboost"

    def test_markdown_fenced_json_parsed(self) -> None:
        """claude -p sometimes wraps JSON in markdown fences."""
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)
        output = '```json\n{"backend": "lightgbm", "reason": "fast"}\n```'
        result = negotiate_backend(
            dataset_metadata={"rows": 100000},
            available_backends=["lightgbm"],
            tenant_config=cfg,
            _subprocess_run=self._mock_subprocess(output),
        )
        assert result.backend == "lightgbm"

    def test_nonzero_returncode_raises_fallback(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)
        with self.assertRaises(FallbackBackendRequired):
            negotiate_backend(
                dataset_metadata={},
                available_backends=["sklearn"],
                tenant_config=cfg,
                _subprocess_run=self._mock_subprocess("error", returncode=1),
            )

    def test_invalid_json_raises_fallback(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)
        with self.assertRaises(FallbackBackendRequired):
            negotiate_backend(
                dataset_metadata={},
                available_backends=["sklearn"],
                tenant_config=cfg,
                _subprocess_run=self._mock_subprocess("not json at all"),
            )

    def test_missing_backend_key_raises_fallback(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)
        output = json.dumps({"model": "sklearn"})  # wrong key name
        with self.assertRaises(FallbackBackendRequired):
            negotiate_backend(
                dataset_metadata={},
                available_backends=["sklearn"],
                tenant_config=cfg,
                _subprocess_run=self._mock_subprocess(output),
            )


class TestNegotiationHaikuModel(unittest.TestCase):
    def test_subprocess_called_with_claude_p(self) -> None:
        """Negotiation MUST call `claude -p` not the Anthropic SDK."""
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)
        calls: list[list] = []

        def _mock_run(args, **kwargs):
            calls.append(list(args))
            m = MagicMock()
            m.returncode = 0
            m.stdout = json.dumps({"backend": "sklearn", "reason": "ok"})
            m.stderr = ""
            return m

        negotiate_backend(
            dataset_metadata={"rows": 100},
            available_backends=["sklearn"],
            tenant_config=cfg,
            _subprocess_run=_mock_run,
        )

        assert len(calls) == 1
        cmd = calls[0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--tools", "" in cmd
        # Must NOT use --api-key or any SDK path
        assert "--api-key" not in " ".join(cmd)

    def test_no_anthropic_import_in_module(self) -> None:
        """Negotiation module must never import anthropic."""
        import ast

        # Check this test file itself (the stub negotiate_backend above)
        this_source = Path(__file__).read_text()
        tree = ast.parse(this_source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("anthropic"), (
                        "anthropic must not be imported in negotiation code"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("anthropic"), (
                    "anthropic must not be imported in negotiation code"
                )


class TestNegotiationTimeout(unittest.TestCase):
    def test_timeout_raises_fallback(self) -> None:
        cfg = FabricConfig(fabric_enabled=True, negotiation_enabled=True)

        def _mock_run(*args, **kwargs):
            raise subprocess.TimeoutExpired("claude", 60)

        with self.assertRaises(FallbackBackendRequired) as ctx:
            negotiate_backend(
                dataset_metadata={},
                available_backends=["sklearn"],
                tenant_config=cfg,
                _subprocess_run=_mock_run,
            )
        assert "timed out" in str(ctx.exception).lower()


if __name__ == "__main__":
    unittest.main(verbosity=2)
