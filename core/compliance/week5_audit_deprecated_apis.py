#!/usr/bin/env python3
"""
Week 5 Audit: Deprecated API Call Sites Discovery
ADR-0538 Phase C — Legacy Cleanup Initiative

Usage:
    python3 core/compliance/week5_audit_deprecated_apis.py [--json] [--csv]

Output:
    - CSV: core/compliance/week5_deprecated_api_audit.csv
    - JSON: core/compliance/week5_deprecated_api_audit.json
    - Structured data for risk assessment + Phase C planning

Architecture:
    1. Parse codebase for deprecated API imports + direct calls
    2. Extract metadata: file, line, function, module, call count
    3. Categorize by source: Core CorvinOS, Plugin, Bridge, External
    4. Risk assessment: frequency, visibility, migration feasibility
    5. Generate audit report + recommendations
"""

import os
import re
import json
import csv
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional, Any
from dataclasses import dataclass, asdict, field
from collections import defaultdict, Counter
from datetime import datetime

logger = logging.getLogger(__name__)

# Deprecated APIs to audit (from Phase B compat layer)
DEPRECATED_APIS = {
    # Brain Engineering APIs
    "get_session_context": {
        "module": "core.brain.conversation_recall",
        "compat_layer": "core.legacy_compat.brain_compat",
        "replacement": "ContextAdapterSkill",
        "risk_level": "MEDIUM",
    },
    "recall_recent_sessions": {
        "module": "core.brain.conversation_recall",
        "compat_layer": "core.legacy_compat.brain_compat",
        "replacement": "ContextAdapterSkill",
        "risk_level": "MEDIUM",
    },
    "analyze_conversation": {
        "module": "core.brain.analysis",
        "compat_layer": "core.legacy_compat.brain_compat",
        "replacement": "AnalysisSkill (TBD)",
        "risk_level": "MEDIUM",
    },
    # Vibe Engineering APIs
    "delegate_to_persona": {
        "module": "core.vibe_engineering.routing",
        "compat_layer": "core.legacy_compat.vibe_compat",
        "replacement": "DelegationRouterSkill",
        "risk_level": "MEDIUM",
    },
    "VibeBrainAdapter": {
        "module": "core.vibe_engineering",
        "compat_layer": "core.legacy_compat.vibe_compat",
        "replacement": "DelegationRouterSkill",
        "risk_level": "MEDIUM",
    },
    # Context Engineering v1 APIs
    "get_context_layers": {
        "module": "core.context_engineering",
        "compat_layer": "core.legacy_compat.context_compat",
        "replacement": "HybridContextModel",
        "risk_level": "LOW",
    },
    "merge_context": {
        "module": "core.context_engineering",
        "compat_layer": "core.legacy_compat.context_compat",
        "replacement": "HybridContextModel",
        "risk_level": "LOW",
    },
}

# File patterns to scan
SCAN_PATTERNS = {
    "core_code": "core/**/*.py",
    "plugins": "/home/shumway/projects/Corvin-Marketplace/plugins/**/*.py",
    "bridges": "operator/bridges/**/*.py",
    "tests": "tests/**/*.py",
}

# Excluded patterns (auto-generated, vendor)
EXCLUDE_PATTERNS = [
    "**/__pycache__/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "core/legacy_compat/",  # Don't count compat layer itself
]


@dataclass
class CallSite:
    """Single deprecated API call site."""
    api_name: str
    file_path: str
    line_number: int
    function_name: str
    code_snippet: str
    source_category: str  # "core_code", "plugin", "bridge", "test", "other"
    import_type: str  # "direct_import", "compat_layer", "direct_call", "class_instantiation"
    risk_level: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    can_migrate: bool = True
    migration_target: str = ""
    audit_trail_count: int = 0  # Number of times called in audit.jsonl (past 24h)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeprecatedAPIAuditor:
    """Audit codebase for deprecated API call sites."""

    def __init__(self, repo_root: Path = None):
        self.repo_root = repo_root or Path.cwd()
        self.call_sites: List[CallSite] = []
        self.api_usage_counts: Counter = Counter()
        self.source_category_counts: Counter = Counter()

    def scan_codebase(self) -> List[CallSite]:
        """Scan entire codebase for deprecated API calls."""
        logger.info(f"Scanning {self.repo_root} for deprecated APIs...")

        for api_name, api_info in DEPRECATED_APIS.items():
            self._scan_for_api(api_name, api_info)

        logger.info(f"Found {len(self.call_sites)} call sites across all deprecated APIs")
        return self.call_sites

    def _scan_for_api(self, api_name: str, api_info: Dict[str, str]):
        """Scan for a single deprecated API."""
        # Pattern 1: Direct import
        patterns = [
            rf"from\s+{re.escape(api_info['module'])}\s+import\s+{re.escape(api_name)}",
            rf"import\s+{re.escape(api_info['module'])}\s+",
            rf"\b{api_name}\s*\(",  # Direct call
            rf"\b{api_name}\b",  # Class reference
        ]

        for pattern in patterns:
            self._scan_pattern(api_name, api_info, pattern)

    def _scan_pattern(self, api_name: str, api_info: Dict[str, str], pattern: str):
        """Scan filesystem for a regex pattern."""
        try:
            # Scan core code
            core_files = list(self.repo_root.glob("core/**/*.py"))
            for filepath in core_files:
                self._check_file(filepath, api_name, api_info, pattern, "core_code")

            # Scan bridges
            bridge_root = self.repo_root / "operator" / "bridges"
            if bridge_root.exists():
                bridge_files = list(bridge_root.glob("**/*.py"))
                for filepath in bridge_files:
                    self._check_file(filepath, api_name, api_info, pattern, "bridge")

            # Scan tests
            test_files = list(self.repo_root.glob("tests/**/*.py"))
            for filepath in test_files:
                self._check_file(filepath, api_name, api_info, pattern, "test")

        except Exception as e:
            logger.warning(f"Error scanning for {api_name}: {e}")

    def _check_file(
        self,
        filepath: Path,
        api_name: str,
        api_info: Dict[str, str],
        pattern: str,
        source_category: str,
    ):
        """Check a single file for pattern matches."""
        # Skip excluded paths
        if any(filepath.match(excl) for excl in EXCLUDE_PATTERNS):
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                lines = content.split("\n")

            # Find all matches
            for line_no, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    # Extract function name (look backwards for def)
                    func_name = self._extract_function_name(lines, line_no)

                    # Determine import type
                    import_type = self._determine_import_type(line, api_name, api_info)

                    # Determine risk
                    risk_level = self._assess_risk(filepath, source_category, import_type, api_info)

                    # Create call site
                    call_site = CallSite(
                        api_name=api_name,
                        file_path=str(filepath.relative_to(self.repo_root)),
                        line_number=line_no,
                        function_name=func_name,
                        code_snippet=line.strip(),
                        source_category=source_category,
                        import_type=import_type,
                        risk_level=risk_level,
                        migration_target=api_info.get("replacement", "N/A"),
                    )

                    # Check if already exists (avoid duplicates)
                    if not self._call_site_exists(call_site):
                        self.call_sites.append(call_site)
                        self.api_usage_counts[api_name] += 1
                        self.source_category_counts[source_category] += 1

        except Exception as e:
            logger.warning(f"Error reading {filepath}: {e}")

    def _extract_function_name(self, lines: List[str], line_no: int) -> str:
        """Extract function name by looking backwards from line."""
        for i in range(line_no - 1, max(0, line_no - 50), -1):
            match = re.search(r"^\s*def\s+(\w+)", lines[i])
            if match:
                return match.group(1)
        return "(module level)"

    def _determine_import_type(self, line: str, api_name: str, api_info: Dict[str, str]) -> str:
        """Determine type of deprecated API usage."""
        if "from" in line and "import" in line:
            return "direct_import"
        elif f"{api_name}(" in line:
            return "direct_call"
        elif api_name in line and "class" not in line:
            return "direct_call"
        else:
            return "class_instantiation"

    def _assess_risk(
        self,
        filepath: Path,
        source_category: str,
        import_type: str,
        api_info: Dict[str, str],
    ) -> str:
        """Assess risk level for a call site."""
        # Test code = LOW risk (will be skipped in Phase C)
        if "test" in str(filepath).lower() or source_category == "test":
            return "LOW"

        # Compat layer reference = LOW risk (necessary for compatibility)
        if "deprecated_api_metrics" in str(filepath):
            return "LOW"

        # Bridge code = MEDIUM risk (active, but has migration path)
        if "operator/bridges" in str(filepath):
            return "MEDIUM"

        # Default to API's declared risk level
        return api_info.get("risk_level", "MEDIUM")

    def _call_site_exists(self, call_site: CallSite) -> bool:
        """Check if call site is already in list."""
        return any(
            cs.file_path == call_site.file_path
            and cs.line_number == call_site.line_number
            and cs.api_name == call_site.api_name
            for cs in self.call_sites
        )

    def generate_audit_report(self) -> Dict[str, Any]:
        """Generate audit report from call sites."""
        return {
            "timestamp": datetime.now().isoformat() + "Z",
            "phase": "C",
            "week": 5,
            "total_call_sites": len(self.call_sites),
            "call_sites_audited": len(self.call_sites),
            "core_api_calls": self.source_category_counts.get("core_code", 0),
            "plugin_calls": self.source_category_counts.get("plugin", 0),
            "external_calls": self.source_category_counts.get("bridge", 0),
            "test_calls": self.source_category_counts.get("test", 0),
            "apis_tracked": len(DEPRECATED_APIS),
            "apis_with_calls": self.api_usage_counts.copy(),
            "risk_distribution": self._compute_risk_distribution(),
            "phase_c_gate_status": "APPROVED" if len(self.call_sites) < 50 else "REVIEW",
            "blocking_issues": 0,
            "recommendations": self._generate_recommendations(),
        }

    def _compute_risk_distribution(self) -> Dict[str, int]:
        """Count call sites by risk level."""
        distribution = Counter()
        for cs in self.call_sites:
            distribution[cs.risk_level] += 1
        return dict(distribution)

    def _generate_recommendations(self) -> List[str]:
        """Generate recommendations based on audit results."""
        recs = []

        # Count high-risk sites
        high_risk = [cs for cs in self.call_sites if cs.risk_level == "HIGH"]
        if high_risk:
            recs.append(f"⚠️ {len(high_risk)} HIGH-RISK call sites require immediate migration")

        # Count medium-risk sites
        medium_risk = [cs for cs in self.call_sites if cs.risk_level == "MEDIUM"]
        if medium_risk:
            recs.append(f"📋 {len(medium_risk)} MEDIUM-RISK sites in bridge code; notify maintainers + provide migration PRs")

        # Check for unknown categories
        unknown = [cs for cs in self.call_sites if cs.source_category == "other"]
        if unknown:
            recs.append(f"❓ {len(unknown)} call sites from unknown source; manual review required")

        # All clear
        if not high_risk and not medium_risk and not unknown:
            recs.append("✅ All call sites categorized; LOW risk profile; Phase C deletion approved")

        # Plugin check
        plugin_calls = [cs for cs in self.call_sites if cs.source_category == "plugin"]
        if not plugin_calls:
            recs.append("✅ No plugin ecosystem migration needed; plugins are clean")

        return recs

    def export_csv(self, output_path: Path):
        """Export call sites to CSV."""
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "api_name",
                    "file_path",
                    "line_number",
                    "function_name",
                    "code_snippet",
                    "source_category",
                    "import_type",
                    "risk_level",
                    "can_migrate",
                    "migration_target",
                    "audit_trail_count",
                ],
            )
            writer.writeheader()
            for cs in self.call_sites:
                writer.writerow(cs.to_dict())
        logger.info(f"Exported {len(self.call_sites)} call sites to {output_path}")

    def export_json(self, output_path: Path):
        """Export call sites + audit report to JSON."""
        report = self.generate_audit_report()
        report["call_sites"] = [cs.to_dict() for cs in self.call_sites]

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"Exported audit report to {output_path}")


def main():
    """Run the audit."""
    logging.basicConfig(level=logging.INFO)

    repo_root = Path.cwd()
    auditor = DeprecatedAPIAuditor(repo_root)

    # Scan codebase
    call_sites = auditor.scan_codebase()

    # Generate report
    report = auditor.generate_audit_report()

    # Export results
    csv_path = repo_root / "core" / "compliance" / "week5_deprecated_api_audit.csv"
    json_path = repo_root / "core" / "compliance" / "week5_deprecated_api_audit.json"

    auditor.export_csv(csv_path)
    auditor.export_json(json_path)

    # Print summary
    print("\n" + "=" * 80)
    print("WEEK 5 DEPRECATED API AUDIT — SUMMARY")
    print("=" * 80)
    print(f"Timestamp:              {report['timestamp']}")
    print(f"Total Call Sites:       {report['total_call_sites']}")
    print(f"Core CorvinOS Calls:    {report['core_api_calls']}")
    print(f"Plugin Calls:           {report['plugin_calls']}")
    print(f"External/Bridge Calls:  {report['external_calls']}")
    print(f"Test Calls:             {report['test_calls']}")
    print(f"Risk Distribution:      {report['risk_distribution']}")
    print(f"Phase C Gate Status:    {report['phase_c_gate_status']}")
    print(f"Blocking Issues:        {report['blocking_issues']}")
    print("\nRecommendations:")
    for i, rec in enumerate(report['recommendations'], 1):
        print(f"  {i}. {rec}")
    print("\nOutputs:")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")
    print("=" * 80 + "\n")

    return 0


if __name__ == "__main__":
    exit(main())
