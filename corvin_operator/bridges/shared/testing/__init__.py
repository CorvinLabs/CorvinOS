"""
ADR-0087 M9–M10: Universal Testing Framework for WorkerEngines.

Provides unified test runner for all 4 engines (Claude Code, Codex, OpenCode, Copilot).
"""

from .universal_test_framework import (
    TestTier,
    TestCase,
    TestResult,
    EngineTestRunner,
    TierValidator,
    TestMatrix,
    CoverageReporter,
)

__all__ = [
    "TestTier",
    "TestCase",
    "TestResult",
    "EngineTestRunner",
    "TierValidator",
    "TestMatrix",
    "CoverageReporter",
]
