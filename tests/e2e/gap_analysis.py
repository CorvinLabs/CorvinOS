"""
E2E Test Coverage Gap Analysis — Identify untested critical paths

Analyzes:
1. Feature-based coverage: which features have E2E tests?
2. Code-based coverage: which entry points are E2E-tested?
3. Critical path mapping: entry point → endpoint → database
4. Risk assessment: impact of missing tests

Output: Gap report + prioritized fix list
"""

import os
import re
import json
from pathlib import Path
from typing import List, Dict, Set, Tuple
from dataclasses import dataclass, field, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class CriticalPath:
    """A critical entry point that must be E2E-tested."""

    name: str
    system: str  # plugin-system, console-ui, api, session-mgmt, learning, marketplace
    entry_point: str  # Function name, HTTP endpoint, CLI command
    file_path: str  # Where the entry point is defined
    line_number: int
    has_e2e_test: bool = False
    test_file: str = ""  # Which E2E test covers this
    risk_level: str = "medium"  # low, medium, high, critical
    dependencies: List[str] = field(default_factory=list)  # Other entry points this depends on


@dataclass
class GapAnalysisReport:
    """Complete gap analysis."""

    timestamp: str
    total_critical_paths: int
    tested_paths: int
    coverage_percent: float
    untested_critical_paths: List[CriticalPath] = field(default_factory=list)
    gaps_by_system: Dict[str, Dict] = field(default_factory=dict)
    risk_matrix: Dict[str, int] = field(default_factory=dict)  # risk_level -> count


class CriticalPathMapper:
    """Map all critical entry points in CorvinOS."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        self.critical_paths: List[CriticalPath] = []

    def map_all_critical_paths(self) -> List[CriticalPath]:
        """Identify all critical entry points that need E2E testing."""

        # Plugin System (ADR-0030/0233/0243)
        self.critical_paths.extend(
            [
                CriticalPath(
                    name="plugin_boot",
                    system="plugin-system",
                    entry_point="bootstrap_global()",
                    file_path="core/plugins/bootstrap.py",
                    line_number=50,
                    risk_level="critical",
                ),
                CriticalPath(
                    name="plugin_install_verify",
                    system="plugin-system",
                    entry_point="PluginInstaller.install_with_verify()",
                    file_path="core/plugins/installer.py",
                    line_number=100,
                    risk_level="high",
                ),
                CriticalPath(
                    name="audit_backend_provider",
                    system="plugin-system",
                    entry_point="AuditBackendProvider.write_event()",
                    file_path="core/plugins/providers/audit_backend.py",
                    line_number=30,
                    risk_level="critical",
                ),
            ]
        )

        # Console UI (Web-Next)
        self.critical_paths.extend(
            [
                CriticalPath(
                    name="console_spa_mount",
                    system="console-ui",
                    entry_point="mount_static()",
                    file_path="core/console/app.py",
                    line_number=444,
                    risk_level="high",
                ),
                CriticalPath(
                    name="console_marketplace_panel",
                    system="console-ui",
                    entry_point="MarketplacePanel.render()",
                    file_path="core/console/web-next/src/panels/marketplace.tsx",
                    line_number=20,
                    risk_level="medium",
                ),
            ]
        )

        # API Boundaries (HTTP endpoints)
        self.critical_paths.extend(
            [
                CriticalPath(
                    name="auth_login",
                    system="api",
                    entry_point="POST /auth/login",
                    file_path="core/console/routes/auth.py",
                    line_number=50,
                    risk_level="critical",
                ),
                CriticalPath(
                    name="audit_write",
                    system="api",
                    entry_point="POST /audit/write",
                    file_path="core/compliance/audit_api.py",
                    line_number=30,
                    risk_level="critical",
                ),
                CriticalPath(
                    name="consent_gate",
                    system="api",
                    entry_point="ConsentGate.verify()",
                    file_path="core/compliance/consent_gate.py",
                    line_number=40,
                    risk_level="critical",
                ),
            ]
        )

        # Session Management
        self.critical_paths.extend(
            [
                CriticalPath(
                    name="session_recovery",
                    system="session-mgmt",
                    entry_point="SessionManager.recover()",
                    file_path="core/session_manager/session_manager.py",
                    line_number=100,
                    risk_level="high",
                ),
                CriticalPath(
                    name="context_inheritance",
                    system="session-mgmt",
                    entry_point="ExecutionContext.inherit_from()",
                    file_path="core/concurrency/context.py",
                    line_number=80,
                    risk_level="high",
                ),
            ]
        )

        # Learning Infrastructure (ADR-0314+)
        self.critical_paths.extend(
            [
                CriticalPath(
                    name="learning_event_emit",
                    system="learning",
                    entry_point="EventEmitter.emit()",
                    file_path="core/learning/events.py",
                    line_number=50,
                    risk_level="medium",
                ),
                CriticalPath(
                    name="skill_injection",
                    system="learning",
                    entry_point="SkillSystemIntegration.inject_skill()",
                    file_path="operator/skill-forge/skill_injection.py",
                    line_number=30,
                    risk_level="medium",
                ),
            ]
        )

        # Marketplace
        self.critical_paths.extend(
            [
                CriticalPath(
                    name="marketplace_discover",
                    system="marketplace",
                    entry_point="MarketplaceIndex.discover()",
                    file_path="core/marketplace/index.py",
                    line_number=50,
                    risk_level="medium",
                ),
            ]
        )

        return self.critical_paths


class E2ETestDetector:
    """Detect which critical paths have E2E tests."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        self.e2e_tests: Dict[str, str] = {}  # entry_point -> test_file

    def scan_e2e_tests(self) -> Dict[str, str]:
        """Scan all E2E test files and map to entry points."""

        e2e_dir = Path(self.repo_root) / "tests" / "e2e"

        for test_file in e2e_dir.glob("**/*.py"):
            if test_file.name.startswith("_") or test_file.name == "gap_analysis.py":
                continue

            try:
                with open(test_file) as f:
                    content = f.read()

                    # Extract test names
                    test_methods = re.findall(r"def (test_\w+)", content)

                    # Extract what they're testing (heuristic: look for comments or imports)
                    for method in test_methods:
                        # Simple heuristic: test name -> entry point mapping
                        if "plugin" in method:
                            self.e2e_tests[method] = str(test_file)
                        elif "auth" in method or "login" in method:
                            self.e2e_tests["auth_login"] = str(test_file)
                        elif "console" in method:
                            self.e2e_tests[method] = str(test_file)
                        elif "session" in method:
                            self.e2e_tests[method] = str(test_file)

            except Exception as e:
                logger.warning(f"Error scanning {test_file}: {e}")

        return self.e2e_tests


class GapAnalyzer:
    """Perform complete gap analysis."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        self.mapper = CriticalPathMapper(repo_root)
        self.detector = E2ETestDetector(repo_root)

    def analyze(self) -> GapAnalysisReport:
        """Run complete gap analysis."""

        # Map all critical paths
        critical_paths = self.mapper.map_all_critical_paths()
        logger.info(f"Found {len(critical_paths)} critical paths to test")

        # Detect existing E2E tests
        e2e_tests = self.detector.scan_e2e_tests()
        logger.info(f"Found E2E tests for: {list(e2e_tests.keys())}")

        # Mark which paths have tests
        tested_count = 0
        for path in critical_paths:
            # Simple matching: if entry_point mentions something tested
            if any(test_key.replace("test_", "") in path.entry_point.lower() for test_key in e2e_tests):
                path.has_e2e_test = True
                tested_count += 1

        # Generate report
        untested = [p for p in critical_paths if not p.has_e2e_test]
        gaps_by_system = self._group_gaps_by_system(untested)
        risk_matrix = self._build_risk_matrix(untested)

        from datetime import datetime

        report = GapAnalysisReport(
            timestamp=datetime.utcnow().isoformat(),
            total_critical_paths=len(critical_paths),
            tested_paths=tested_count,
            coverage_percent=(tested_count / len(critical_paths) * 100) if critical_paths else 0,
            untested_critical_paths=untested,
            gaps_by_system=gaps_by_system,
            risk_matrix=risk_matrix,
        )

        return report

    def _group_gaps_by_system(self, untested: List[CriticalPath]) -> Dict[str, Dict]:
        """Group untested paths by system."""
        grouped = {}
        for path in untested:
            if path.system not in grouped:
                grouped[path.system] = {"count": 0, "paths": [], "high_risk": 0}

            grouped[path.system]["count"] += 1
            grouped[path.system]["paths"].append(path.name)

            if path.risk_level in ["high", "critical"]:
                grouped[path.system]["high_risk"] += 1

        return grouped

    def _build_risk_matrix(self, untested: List[CriticalPath]) -> Dict[str, int]:
        """Count untested paths by risk level."""
        matrix = {"critical": 0, "high": 0, "medium": 0, "low": 0}

        for path in untested:
            matrix[path.risk_level] += 1

        return matrix


def print_gap_report(report: GapAnalysisReport):
    """Print human-readable gap analysis report."""

    print("\n" + "=" * 80)
    print("E2E TEST COVERAGE GAP ANALYSIS REPORT")
    print("=" * 80)
    print(f"Timestamp: {report.timestamp}")
    print(f"Total Critical Paths: {report.total_critical_paths}")
    print(f"Tested Paths: {report.tested_paths}")
    print(f"Coverage: {report.coverage_percent:.1f}%")
    print()

    print("UNTESTED CRITICAL PATHS (by system):")
    print("-" * 80)
    for system, gaps in report.gaps_by_system.items():
        print(f"\n{system.upper()}:")
        print(f"  - Total gaps: {gaps['count']}")
        print(f"  - High/Critical: {gaps['high_risk']}")
        print(f"  - Paths: {', '.join(gaps['paths'][:3])}{'...' if len(gaps['paths']) > 3 else ''}")

    print("\nRISK MATRIX (untested paths by severity):")
    print("-" * 80)
    for level, count in report.risk_matrix.items():
        icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}[level]
        print(f"{icon} {level.upper():10s}: {count} untested paths")

    print("\n" + "=" * 80)
    print(f"RECOMMENDATION: Implement {sum(report.risk_matrix.get(l, 0) for l in ['critical', 'high'])} "
          f"high-priority tests")
    print("=" * 80)
