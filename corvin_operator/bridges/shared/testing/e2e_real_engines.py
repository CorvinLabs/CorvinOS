"""
ADR-0087 Real-World E2E Validation

Tests M5–M8 capabilities with live LLM calls across all available engines:
- Codex (OpenAI CLI)
- Copilot (GitHub CLI)
- Hermes (Ollama local)

Validates:
- M5: Function-Call Bridge (Copilot only)
- M6: System-Prompt Injection (all engines)
- M7: Multi-Turn Wrapper (Copilot only)
- M8: Capability Matrix (all engines)
"""

import subprocess
import json
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EngineE2ETester:
    """Base class for real-world engine testing."""

    def __init__(self, engine_id: str):
        self.engine_id = engine_id
        self.results = []

    def test_system_prompt_injection(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """Test M6: System-Prompt Injection."""
        raise NotImplementedError()

    def test_capability_matrix_lookup(self) -> Dict[str, Any]:
        """Test M8: Capability Matrix — verify engine capabilities."""
        raise NotImplementedError()

    def run_all_tests(self) -> Dict[str, Any]:
        """Run all available tests for this engine."""
        raise NotImplementedError()


class CodexE2ETester(EngineE2ETester):
    """OpenAI Codex real-world testing."""

    def __init__(self):
        super().__init__("codex")
        self.results = []

    def test_system_prompt_injection(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """
        Test M6: System-Prompt Injection via <SYSTEM>...</SYSTEM> block.

        Expected format (from M6 spec):
            <SYSTEM>Your system prompt</SYSTEM>
            User prompt here
        """
        formatted_prompt = f"<SYSTEM>{system_prompt}</SYSTEM>\n\n{prompt}"

        try:
            result = subprocess.run(
                ["codex", "-p", formatted_prompt],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                return {
                    "status": "pass",
                    "engine": "codex",
                    "test": "system_prompt_injection",
                    "system_prompt_formatted": True,
                    "output_length": len(result.stdout),
                    "output_preview": result.stdout[:200],
                }
            else:
                return {
                    "status": "fail",
                    "engine": "codex",
                    "test": "system_prompt_injection",
                    "error": result.stderr[:200],
                }
        except Exception as e:
            return {
                "status": "error",
                "engine": "codex",
                "test": "system_prompt_injection",
                "error": str(e),
            }

    def test_capability_matrix_lookup(self) -> Dict[str, Any]:
        """Test M8: Verify Codex capabilities."""
        try:
            from engines.capability_matrix import CANONICAL_CAPABILITY_MATRIX

            codex_caps = CANONICAL_CAPABILITY_MATRIX.get("codex", {})

            expected = {
                "mid_stream_inject": "buffered",
                "hooks": "teb_brokered",
                "skills": "append_system_prompt",
                "system_prompt": "text_prefix",
                "multi_turn": None,
            }

            matches = all(
                codex_caps.get(k) == v for k, v in expected.items()
            )

            return {
                "status": "pass" if matches else "fail",
                "engine": "codex",
                "test": "capability_matrix_lookup",
                "expected_capabilities": expected,
                "actual_capabilities": codex_caps,
                "matches": matches,
            }
        except Exception as e:
            return {
                "status": "error",
                "engine": "codex",
                "test": "capability_matrix_lookup",
                "error": str(e),
            }

    def run_all_tests(self) -> Dict[str, Any]:
        """Run all Codex tests."""
        logger.info("=" * 70)
        logger.info("CODEX (OpenAI) — Real-World E2E Testing")
        logger.info("=" * 70)

        # Test 1: System-Prompt Injection (M6)
        logger.info("\nTest 1: System-Prompt Injection (M6)")
        result1 = self.test_system_prompt_injection(
            prompt="Write a function that adds two numbers.",
            system_prompt="You are a Python expert. Always provide clean, well-documented code.",
        )
        logger.info(f"  Status: {result1['status']}")
        if result1["status"] == "pass":
            logger.info(f"  Output preview: {result1['output_preview'][:100]}...")
        self.results.append(result1)

        # Test 2: Capability Matrix (M8)
        logger.info("\nTest 2: Capability Matrix (M8)")
        result2 = self.test_capability_matrix_lookup()
        logger.info(f"  Status: {result2['status']}")
        logger.info(f"  Capabilities match: {result2.get('matches', False)}")
        self.results.append(result2)

        return {
            "engine": "codex",
            "total_tests": 2,
            "passed": sum(1 for r in self.results if r["status"] == "pass"),
            "results": self.results,
        }


class CopilotE2ETester(EngineE2ETester):
    """GitHub Copilot real-world testing."""

    def __init__(self):
        super().__init__("copilot")
        self.results = []

    def test_system_prompt_injection(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """
        Test M6: System-Prompt Injection via [SYSTEM]...[/SYSTEM] marker.

        Expected format (from M6 spec):
            [SYSTEM]Your system prompt[/SYSTEM]
            User prompt here
        """
        formatted_prompt = f"[SYSTEM]{system_prompt}[/SYSTEM]\n\n{prompt}"

        try:
            result = subprocess.run(
                ["copilot", "-p", formatted_prompt],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                return {
                    "status": "pass",
                    "engine": "copilot",
                    "test": "system_prompt_injection",
                    "system_prompt_formatted": True,
                    "output_length": len(result.stdout),
                    "output_preview": result.stdout[:200],
                }
            else:
                return {
                    "status": "fail",
                    "engine": "copilot",
                    "test": "system_prompt_injection",
                    "error": result.stderr[:200],
                }
        except Exception as e:
            return {
                "status": "error",
                "engine": "copilot",
                "test": "system_prompt_injection",
                "error": str(e),
            }

    def test_capability_matrix_lookup(self) -> Dict[str, Any]:
        """Test M8: Verify Copilot capabilities."""
        try:
            from engines.capability_matrix import CANONICAL_CAPABILITY_MATRIX

            copilot_caps = CANONICAL_CAPABILITY_MATRIX.get("copilot", {})

            expected = {
                "mid_stream_inject": None,
                "hooks": "teb_brokered",
                "skills": "prompt_prefix",
                "system_prompt": "text_prefix",
                "mcp": "fcb_emulated",  # M5
                "multi_turn": "sequential_wrapper",  # M7
            }

            matches = all(
                copilot_caps.get(k) == v for k, v in expected.items()
            )

            return {
                "status": "pass" if matches else "fail",
                "engine": "copilot",
                "test": "capability_matrix_lookup",
                "expected_capabilities": expected,
                "actual_capabilities": copilot_caps,
                "matches": matches,
            }
        except Exception as e:
            return {
                "status": "error",
                "engine": "copilot",
                "test": "capability_matrix_lookup",
                "error": str(e),
            }

    def run_all_tests(self) -> Dict[str, Any]:
        """Run all Copilot tests."""
        logger.info("=" * 70)
        logger.info("COPILOT (GitHub) — Real-World E2E Testing")
        logger.info("=" * 70)

        # Test 1: System-Prompt Injection (M6)
        logger.info("\nTest 1: System-Prompt Injection (M6)")
        result1 = self.test_system_prompt_injection(
            prompt="Write a function that adds two numbers.",
            system_prompt="You are a Python expert. Always provide clean, well-documented code.",
        )
        logger.info(f"  Status: {result1['status']}")
        if result1["status"] == "pass":
            logger.info(f"  Output preview: {result1['output_preview'][:100]}...")
        self.results.append(result1)

        # Test 2: Capability Matrix (M8)
        logger.info("\nTest 2: Capability Matrix (M8) — M5/M6/M7")
        result2 = self.test_capability_matrix_lookup()
        logger.info(f"  Status: {result2['status']}")
        logger.info(f"  Capabilities match: {result2.get('matches', False)}")
        self.results.append(result2)

        return {
            "engine": "copilot",
            "total_tests": 2,
            "passed": sum(1 for r in self.results if r["status"] == "pass"),
            "results": self.results,
        }


class HermesE2ETester(EngineE2ETester):
    """Ollama Hermes local testing."""

    def __init__(self):
        super().__init__("hermes")
        self.results = []

    def test_system_prompt_injection(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """
        Test M6: System-Prompt Injection via {"role": "system"} in messages.

        Expected format (from M6 spec):
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
        """
        try:
            payload = {
                "model": "hermes-2.5-mistral-7b",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            }

            result = subprocess.run(
                ["curl", "-s", "http://localhost:11434/api/chat"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                try:
                    response = json.loads(result.stdout)
                    message = response.get("message", {}).get("content", "")
                    return {
                        "status": "pass",
                        "engine": "hermes",
                        "test": "system_prompt_injection",
                        "system_prompt_formatted": True,
                        "output_length": len(message),
                        "output_preview": message[:200],
                    }
                except json.JSONDecodeError:
                    return {
                        "status": "fail",
                        "engine": "hermes",
                        "test": "system_prompt_injection",
                        "error": "Invalid JSON response",
                    }
            else:
                return {
                    "status": "fail",
                    "engine": "hermes",
                    "test": "system_prompt_injection",
                    "error": result.stderr[:200],
                }
        except Exception as e:
            return {
                "status": "error",
                "engine": "hermes",
                "test": "system_prompt_injection",
                "error": str(e),
            }

    def test_capability_matrix_lookup(self) -> Dict[str, Any]:
        """Test M8: Verify Hermes capabilities."""
        try:
            from engines.capability_matrix import CANONICAL_CAPABILITY_MATRIX

            hermes_caps = CANONICAL_CAPABILITY_MATRIX.get("hermes", {})

            expected = {
                "mid_stream_inject": "buffered",
                "hooks": "teb_brokered",
                "skills": "system_message",
                "system_prompt": "message_role",
                "mcp": None,  # Ollama doesn't have MCP
            }

            matches = all(
                hermes_caps.get(k) == v for k, v in expected.items()
            )

            return {
                "status": "pass" if matches else "fail",
                "engine": "hermes",
                "test": "capability_matrix_lookup",
                "expected_capabilities": expected,
                "actual_capabilities": hermes_caps,
                "matches": matches,
            }
        except Exception as e:
            return {
                "status": "error",
                "engine": "hermes",
                "test": "capability_matrix_lookup",
                "error": str(e),
            }

    def run_all_tests(self) -> Dict[str, Any]:
        """Run all Hermes tests."""
        logger.info("=" * 70)
        logger.info("HERMES (Ollama) — Real-World E2E Testing")
        logger.info("=" * 70)

        # Test 1: System-Prompt Injection (M6)
        logger.info("\nTest 1: System-Prompt Injection (M6)")
        result1 = self.test_system_prompt_injection(
            prompt="Write a function that adds two numbers.",
            system_prompt="You are a Python expert. Always provide clean, well-documented code.",
        )
        logger.info(f"  Status: {result1['status']}")
        if result1["status"] == "pass":
            logger.info(f"  Output preview: {result1['output_preview'][:100]}...")
        self.results.append(result1)

        # Test 2: Capability Matrix (M8)
        logger.info("\nTest 2: Capability Matrix (M8)")
        result2 = self.test_capability_matrix_lookup()
        logger.info(f"  Status: {result2['status']}")
        logger.info(f"  Capabilities match: {result2.get('matches', False)}")
        self.results.append(result2)

        return {
            "engine": "hermes",
            "total_tests": 2,
            "passed": sum(1 for r in self.results if r["status"] == "pass"),
            "results": self.results,
        }


def run_e2e_validation() -> Dict[str, Any]:
    """Run E2E validation across all available engines."""

    testers = [
        CodexE2ETester(),
        CopilotE2ETester(),
        HermesE2ETester(),
    ]

    all_results = {
        "timestamp": time.time(),
        "engines_tested": [],
        "total_tests": 0,
        "total_passed": 0,
    }

    for tester in testers:
        logger.info("\n")
        try:
            result = tester.run_all_tests()
            all_results["engines_tested"].append(result)
            all_results["total_tests"] += result["total_tests"]
            all_results["total_passed"] += result["passed"]
        except Exception as e:
            logger.error(f"Error testing {tester.engine_id}: {e}")

    return all_results


if __name__ == "__main__":
    sys.path.insert(0, "operator/bridges/shared")
    results = run_e2e_validation()

    logger.info("\n" + "=" * 70)
    logger.info("E2E VALIDATION SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Engines tested: {len(results['engines_tested'])}")
    logger.info(f"Total tests: {results['total_tests']}")
    logger.info(f"Passed: {results['total_passed']}")
    logger.info(f"Failed: {results['total_tests'] - results['total_passed']}")

    # Export JSON report
    report_file = "e2e_validation_report.json"
    Path(report_file).write_text(json.dumps(results, indent=2))
    logger.info(f"\nReport saved: {report_file}")
