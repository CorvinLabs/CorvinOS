"""
Integration Tests for Phase 4 Consolidation — ADR-0421

Tests the full integration of dead-code detection and module analysis with audit trail.
GDPR Art. 30 compliance: All findings are audit-logged.
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.consolidation import (
    DeadCodeDetector,
    DeadCodeReport,
    ModuleAnalyzer,
    ModuleDependencyReport,
)
from core.audit.chain import AuditChain, AuditEntry
from core.audit.integration import AuditChainWithCorruptionDetection


class TestConsolidationAuditIntegration:
    """Test consolidation tools with audit trail integration."""

    @pytest.fixture
    def audit_chain(self):
        """Create a temporary audit chain."""
        with TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            chain = AuditChain(audit_file)
            yield chain

    @pytest.fixture
    def codebase_with_issues(self):
        """Create a codebase with both dead code and circular dependencies."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Dead code in service_a
            (tmpdir_path / "service_a.py").write_text(
                """
import os  # Unused
from service_b import fetch_b

def public_api():
    return fetch_b()

def unused_helper():
    return 42

unused_var = 99
"""
            )

            # Service B imports A (circular)
            (tmpdir_path / "service_b.py").write_text(
                """
from service_a import public_api

def fetch_b():
    return "result"

def internal_func():
    return public_api()
"""
            )

            yield tmpdir_path

    def test_dead_code_detection_audit_event(self, codebase_with_issues, audit_chain):
        """Test that dead code detection creates audit events."""
        detector = DeadCodeDetector(codebase_with_issues, tenant_id="test_tenant")
        report = detector.scan()

        # Get audit event
        audit_event = detector.get_audit_event_dict(report)

        # Create audit entry and record
        entry = AuditEntry(
            event_type=audit_event["event_type"],
            actor=audit_event["actor"],
            action=audit_event["action"],
            resource=audit_event["resource"],
            result=audit_event["result"],
            timestamp=audit_event["timestamp"],
            tenant_id="test_tenant",
            details=audit_event.get("details"),
        )

        audit_chain.record(entry)

        # Verify audit chain
        assert audit_chain.verify_chain() is True
        assert audit_chain.entry_count() == 1

        # Verify audit event content
        entries = audit_chain.get_entries()
        assert entries[0].event_type == "consolidation_dead_code_scan"
        assert entries[0].actor == "consolidation_system"
        assert entries[0].details["tenant_id"] == "test_tenant"

    def test_module_analysis_audit_event(self, codebase_with_issues, audit_chain):
        """Test that module analysis creates audit events."""
        analyzer = ModuleAnalyzer(codebase_with_issues, tenant_id="test_tenant")
        report = analyzer.scan()

        # Get audit event
        audit_event = analyzer.get_audit_event_dict(report)

        # Create audit entry and record
        entry = AuditEntry(
            event_type=audit_event["event_type"],
            actor=audit_event["actor"],
            action=audit_event["action"],
            resource=audit_event["resource"],
            result=audit_event["result"],
            timestamp=audit_event["timestamp"],
            tenant_id="test_tenant",
            details=audit_event.get("details"),
        )

        audit_chain.record(entry)

        # Verify audit chain
        assert audit_chain.verify_chain() is True
        assert audit_chain.entry_count() == 1

        # Verify audit event content
        entries = audit_chain.get_entries()
        assert entries[0].event_type == "consolidation_module_analysis"
        assert "circular_dependencies_found" in entries[0].result or "success" in entries[0].result

    def test_sequential_audits(self, codebase_with_issues, audit_chain):
        """Test multiple audits in sequence with hash chain."""
        # Scan 1: Dead code
        detector = DeadCodeDetector(codebase_with_issues, tenant_id="test_tenant")
        report1 = detector.scan()

        entry1 = AuditEntry(
            event_type="consolidation_dead_code_scan",
            actor="consolidation_system",
            action="detect_dead_code",
            resource="codebase",
            result="success",
            timestamp=__import__("datetime").datetime.utcnow().isoformat(),
            tenant_id="test_tenant",
            details=report1.to_dict(),
        )
        audit_chain.record(entry1)

        hash_after_first = audit_chain.last_hash()

        # Scan 2: Module dependencies
        analyzer = ModuleAnalyzer(codebase_with_issues, tenant_id="test_tenant")
        report2 = analyzer.scan()

        entry2 = AuditEntry(
            event_type="consolidation_module_analysis",
            actor="consolidation_system",
            action="analyze_dependencies",
            resource="codebase",
            result="success",
            timestamp=__import__("datetime").datetime.utcnow().isoformat(),
            tenant_id="test_tenant",
            details=report2.to_dict(),
        )
        audit_chain.record(entry2)

        # Verify chain integrity
        assert audit_chain.verify_chain() is True
        assert audit_chain.entry_count() == 2

        # Verify hash chain linking
        entries = audit_chain.get_entries()
        assert entries[0].self_hash != entries[1].self_hash
        assert entries[1].prior_hash == hash_after_first

    def test_tenant_isolation_in_audit(self, codebase_with_issues, audit_chain):
        """Test that tenant_id is preserved in audit trail."""
        detectors = [
            DeadCodeDetector(codebase_with_issues, tenant_id="tenant_1"),
            DeadCodeDetector(codebase_with_issues, tenant_id="tenant_2"),
        ]

        for detector in detectors:
            report = detector.scan()
            audit_event = detector.get_audit_event_dict(report)

            entry = AuditEntry(
                event_type=audit_event["event_type"],
                actor=audit_event["actor"],
                action=audit_event["action"],
                resource=audit_event["resource"],
                result=audit_event["result"],
                timestamp=audit_event["timestamp"],
                tenant_id=detector.tenant_id,
                details=audit_event.get("details"),
            )
            audit_chain.record(entry)

        # Verify both tenants logged
        entries = audit_chain.get_entries()
        tenant_ids = set(e.tenant_id for e in entries)
        assert "tenant_1" in tenant_ids
        assert "tenant_2" in tenant_ids

    def test_circular_dependency_severity_in_audit(self, audit_chain):
        """Test that circular dependency severity is captured in audit."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create 2-module cycle
            (tmpdir_path / "x.py").write_text("from y import func")
            (tmpdir_path / "y.py").write_text("from x import func")

            analyzer = ModuleAnalyzer(tmpdir_path, tenant_id="audit_test")
            report = analyzer.scan()

            audit_event = analyzer.get_audit_event_dict(report)

            # Verify cycle severity in audit details
            assert "findings_count" in audit_event["details"]
            details = audit_event["details"]
            assert details.get("total_dependencies") > 0

            entry = AuditEntry(
                event_type=audit_event["event_type"],
                actor=audit_event["actor"],
                action=audit_event["action"],
                resource=audit_event["resource"],
                result=audit_event["result"],
                timestamp=audit_event["timestamp"],
                tenant_id="audit_test",
                details=details,
            )
            audit_chain.record(entry)

            # Verify audit preserved the severity info
            entries = audit_chain.get_entries()
            audit_details = entries[0].details
            assert "circular_dependencies" in audit_details

    def test_large_scan_performance_and_audit(self, audit_chain):
        """Test that large scans complete and audit properly."""
        import time

        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create 50 modules
            for i in range(50):
                (tmpdir_path / f"module_{i}.py").write_text(
                    f"def func_{i}(): return {i}"
                )

            # Scan
            start = time.time()
            detector = DeadCodeDetector(tmpdir_path, tenant_id="perf_test")
            report = detector.scan()
            duration = time.time() - start

            # Should complete in <1 second
            assert duration < 1.0

            # Audit
            audit_event = detector.get_audit_event_dict(report)
            entry = AuditEntry(
                event_type=audit_event["event_type"],
                actor=audit_event["actor"],
                action=audit_event["action"],
                resource=audit_event["resource"],
                result=audit_event["result"],
                timestamp=audit_event["timestamp"],
                tenant_id="perf_test",
                details=audit_event.get("details"),
            )
            audit_chain.record(entry)

            # Verify audit
            assert audit_chain.verify_chain() is True
            entries = audit_chain.get_entries()
            assert entries[0].details["total_files_scanned"] >= 50


class TestConsolidationEndToEnd:
    """End-to-end tests for consolidation workflow."""

    def test_consolidation_workflow(self):
        """Full consolidation workflow: scan, detect issues, audit."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create example codebase
            (tmpdir_path / "main.py").write_text(
                """
import unused_lib
from helper import helper_func

def main():
    return helper_func()
"""
            )
            (tmpdir_path / "helper.py").write_text(
                """
from main import main  # Circular

def helper_func():
    return "help"
"""
            )

            # Step 1: Dead code detection
            detector = DeadCodeDetector(tmpdir_path, tenant_id="e2e_test")
            dead_report = detector.scan()

            assert dead_report.total_files_scanned >= 2
            assert len(dead_report.findings) > 0  # Should find unused_lib
            print(f"Dead code findings: {len(dead_report.findings)}")

            # Step 2: Module analysis
            analyzer = ModuleAnalyzer(tmpdir_path, tenant_id="e2e_test")
            dep_report = analyzer.scan()

            assert dep_report.modules_analyzed >= 2
            assert len(dep_report.circular_dependencies) > 0  # Should find main<->helper cycle
            print(f"Circular dependencies: {len(dep_report.circular_dependencies)}")

            # Step 3: Audit
            with TemporaryDirectory() as audit_tmpdir:
                audit_file = Path(audit_tmpdir) / "audit.jsonl"
                chain = AuditChain(audit_file)

                # Record both scans
                for report_obj in [dead_report, dep_report]:
                    if isinstance(report_obj, DeadCodeReport):
                        event = detector.get_audit_event_dict(report_obj)
                    else:
                        event = analyzer.get_audit_event_dict(report_obj)

                    entry = AuditEntry(
                        event_type=event["event_type"],
                        actor=event["actor"],
                        action=event["action"],
                        resource=event["resource"],
                        result=event["result"],
                        timestamp=event["timestamp"],
                        tenant_id="e2e_test",
                        details=event.get("details"),
                    )
                    chain.record(entry)

                # Verify audit chain
                assert chain.verify_chain() is True
                assert chain.entry_count() == 2

                print("✓ E2E consolidation workflow complete")
