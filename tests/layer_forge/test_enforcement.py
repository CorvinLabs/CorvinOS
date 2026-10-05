"""Unit tests for EnforcementChecker — host-awareness validation."""
from core.orchestration.layer_forge.enforcement import EnforcementChecker


def test_no_host_awareness_declared_is_skipped(tmp_path):
    checker = EnforcementChecker(tmp_path)
    verdict = checker.check_host_awareness({})
    assert verdict.status == "SKIPPED"


def test_source_tree_path_exists_passes_when_no_cross_check(tmp_path):
    (tmp_path / "real_file.py").write_text("x = 1")
    manifest = {
        "host_awareness": {
            "source_tree": {"paths": ["real_file.py"]},
            "cross_check": "none",
        }
    }
    checker = EnforcementChecker(tmp_path)
    verdict = checker.check_host_awareness(manifest)
    assert verdict.status == "SKIPPED"  # no cross_check requested -> skipped, not silently PASS


def test_source_tree_path_missing_fails(tmp_path):
    manifest = {
        "host_awareness": {
            "source_tree": {"paths": ["does_not_exist.py"]},
            "cross_check": "none",
        }
    }
    checker = EnforcementChecker(tmp_path)
    verdict = checker.check_host_awareness(manifest)
    assert verdict.status == "FAIL"
    assert "does_not_exist.py" in verdict.detail


def test_cross_check_without_runtime_host_is_skipped_not_passed(tmp_path):
    """Load-bearing: on a repo-only checkout (no /opt/corvin), the runtime half
    of the cross-check must be SKIPPED, never silently treated as PASS."""
    (tmp_path / "real_file.py").write_text("x = 1")
    manifest = {
        "host_awareness": {
            "source_tree": {"paths": ["real_file.py"]},
            "runtime": {"paths": ["real_file.py"]},
            "cross_check": "sha256_match_or_fail",
        }
    }
    checker = EnforcementChecker(tmp_path)
    verdict = checker.check_host_awareness(manifest)
    assert verdict.status == "SKIPPED"
    assert verdict.detail == "skipped_no_runtime_host"


def test_gate_with_no_collected_tests_is_error_not_pass(tmp_path):
    from core.orchestration.layer_forge.gate_runner import QualityGateRunner

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_empty.py").write_text("x = 1\n")
    verdict = QualityGateRunner(tmp_path).run_gate("g", "tests/test_empty.py")
    assert verdict.status == "ERROR"
    assert verdict.detail == "pytest exit 5"
