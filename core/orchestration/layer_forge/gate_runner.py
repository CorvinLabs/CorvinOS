"""QualityGateRunner — executes the test suite named by each quality_gate
entry in a layer-definition (ADR-2222 D1). Runs pytest as a subprocess per
gate so a crashing test can never take down the orchestrator process.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QualityGateVerdict:
    gate_id: str
    status: str  # PASS | FAIL | ERROR
    test_path: str
    detail: str = ""


class QualityGateRunner:
    def __init__(self, repo_root: Path, timeout_s: float = 60.0):
        self.repo_root = Path(repo_root)
        self.timeout_s = timeout_s

    def run_gate(self, gate_id: str, test_path: str) -> QualityGateVerdict:
        if importlib.util.find_spec("pytest") is None:
            return QualityGateVerdict(gate_id, "ERROR", test_path, "pytest not installed for this interpreter")
        tests_root = (self.repo_root / "tests").resolve()
        full_path = (self.repo_root / test_path).resolve()
        if not full_path.is_relative_to(tests_root):
            return QualityGateVerdict(gate_id, "ERROR", test_path, "test_path outside tests/")
        if not full_path.exists():
            return QualityGateVerdict(gate_id, "ERROR", test_path, "test_path does not exist")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(full_path), "-q"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return QualityGateVerdict(gate_id, "ERROR", test_path, "timeout")

        tail = "\n".join(result.stdout.strip().splitlines()[-5:])
        if result.returncode == 0:
            return QualityGateVerdict(gate_id, "PASS", test_path, tail)
        if result.returncode == 1:
            return QualityGateVerdict(gate_id, "FAIL", test_path, tail)
        # pytest 2..5: interrupted, internal/usage error, no tests collected — the gate never ran
        return QualityGateVerdict(gate_id, "ERROR", test_path, f"pytest exit {result.returncode}")

    def run_all_gates(self, manifest: dict) -> list[QualityGateVerdict]:
        verdicts = []
        for gate in manifest.get("quality_gates", []):
            verdicts.append(self.run_gate(gate["gate_id"], gate["test_path"]))
        return verdicts
