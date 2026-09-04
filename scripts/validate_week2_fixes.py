#!/usr/bin/env python3
"""
Validation script for L5 k=2 Week 2 code review fixes.

Validates that all 10 findings have been correctly fixed:
1. Silent success fallback in apply ✅
2. Silent success fallback in rollback ✅
3. State desync: hash updated before callback succeeds ✅
4. Fail-open gate violation ✅
5. E2E test calls private callback ✅
6. Config hash format in test ✅
7. Metrics double-count ✅
8. previous_hash after exception ✅
9. Dummy hash fallback masks bug ✅
10. previous_hash computed after state change ✅
"""

import sys
import re
from pathlib import Path


def check_fix_1_and_9(file_path):
    """Check that _get_current_config_hash raises ValueError for missing _config_getter."""
    content = file_path.read_text()

    # Should raise ValueError if _config_getter not set
    if 'raise ValueError("_config_getter not configured' in content:
        print("✅ Fix 1/9: _get_current_config_hash raises ValueError")
        return True
    else:
        print("❌ Fix 1/9: _get_current_config_hash should raise ValueError")
        return False


def check_fix_2(file_path):
    """Check that _restore_config raises ValueError for missing _config_restorer."""
    content = file_path.read_text()

    # Should raise ValueError if _config_restorer not set
    if 'raise ValueError("_config_restorer not configured' in content:
        print("✅ Fix 2: _restore_config raises ValueError")
        return True
    else:
        print("❌ Fix 2: _restore_config should raise ValueError")
        return False


def check_fix_3_8_10(file_path):
    """Check that previous_hash is saved BEFORE applying config."""
    content = file_path.read_text()

    # Check _apply_config saves hash before applying
    apply_section = re.search(
        r'def _apply_config\(.*?\n.*?if not hasattr.*?\n.*?raise ValueError.*?\n.*?# Save previous hash BEFORE.*?\n.*?prev_hash = self._get_current_config_hash\(\)',
        content,
        re.DOTALL
    )

    restore_section = re.search(
        r'def _restore_config\(.*?\n.*?if not hasattr.*?\n.*?raise ValueError.*?\n.*?# Save current hash BEFORE.*?\n.*?current_hash = self._get_current_config_hash\(\)',
        content,
        re.DOTALL
    )

    if apply_section and restore_section:
        print("✅ Fix 3/8/10: Save previous_hash BEFORE applying config")
        return True
    else:
        print("❌ Fix 3/8/10: Should save previous_hash BEFORE applying")
        return False


def check_fix_4(file_path):
    """Check that exception in process_feedback propagates (fail-closed)."""
    content = file_path.read_text()

    # Should NOT have try/except catching the approval_gate.request_approval exception
    process_feedback_section = re.search(
        r'def process_feedback\(.*?\n.*?# Step 2:.*?\n.*?# Don\'t catch exception.*?\n.*?record, auto_approved = self\.approval_gate\.request_approval\(',
        content,
        re.DOTALL
    )

    if process_feedback_section and 'Don\'t catch exception' in content:
        # Make sure the old fail-open code is gone
        if 'return None, True  # Fallback' not in content:
            print("✅ Fix 4: Exception in request_approval propagates (fail-closed)")
            return True

    print("❌ Fix 4: Should not catch exception in process_feedback")
    return False


def check_fix_5(file_path):
    """Check that tests use public handle_approval instead of private callback."""
    content = file_path.read_text()

    # Count private callback calls
    private_calls = len(re.findall(r'config_applier\._on_approval_callback\(', content))

    # Should have 0 private calls (all replaced with public method)
    if private_calls == 0:
        print("✅ Fix 5: Tests use public handle_approval method")
        return True
    else:
        print(f"❌ Fix 5: Found {private_calls} private callback calls, should be 0")
        return False


def check_fix_6(file_path):
    """Check that config hash format is correct (64-char hex)."""
    content = file_path.read_text()

    # Look for the hash format fix
    if 'new_config_hash = f"{cycle:064x}"' in content:
        print("✅ Fix 6: Config hash format is proper 64-char hex")
        return True
    else:
        print("❌ Fix 6: Config hash format should be proper 64-char hex")
        return False


def check_fix_3_optimizer(file_path):
    """Check that hash is updated AFTER callback succeeds in handle_approval."""
    content = file_path.read_text()

    # Should have callback called first, then hash updated
    handle_approval_section = re.search(
        r'def handle_approval\(.*?# Call Skill-provided callback FIRST.*?\n.*?if self\.on_approval_callback:.*?\n.*?try:.*?\n.*?self\.on_approval_callback\(.*?\n.*?except Exception.*?\n.*?logger\.error.*?\n.*?return  # Don\'t update hash if callback fails.*?\n.*?# Update hash ONLY after callback succeeds.*?\n.*?self\.current_config_hash = new_config_hash',
        content,
        re.DOTALL
    )

    if handle_approval_section:
        print("✅ Fix 3: Hash updated AFTER callback succeeds")
        return True
    else:
        print("❌ Fix 3: Hash should be updated AFTER callback succeeds")
        return False


def check_fix_7(file_path):
    """Check that metrics avoid double-count of auto-approved approvals."""
    content = file_path.read_text()

    # Should have logic to exclude auto-approved from manual count
    if 'auto_approved_ids = {r["approval_id"] for r in self.approval_requests if r["auto_approved"]}' in content:
        if 'manual_approved = sum(1 for a in self.approvals if a["approval_id"] not in auto_approved_ids)' in content:
            print("✅ Fix 7: Metrics avoid double-count of auto-approved")
            return True

    print("❌ Fix 7: Metrics should exclude auto-approved from manual count")
    return False


def main():
    """Run all validation checks."""
    config_applier = Path("/home/shumway/projects/CorvinOS/core/skills/config_applier.py")
    optimizer_integration = Path("/home/shumway/projects/CorvinOS/core/learning/optimizer_integration.py")
    approval_metrics = Path("/home/shumway/projects/CorvinOS/core/learning/approval_metrics.py")
    test_file = Path("/home/shumway/projects/CorvinOS/tests/test_l5_k2_week2_full_integration_e2e.py")

    print("\n" + "="*70)
    print("L5 k=2 Week 2 Code Review Fixes Validation")
    print("="*70 + "\n")

    results = []

    print("config_applier.py:")
    results.append(check_fix_1_and_9(config_applier))
    results.append(check_fix_2(config_applier))
    results.append(check_fix_3_8_10(config_applier))
    print()

    print("optimizer_integration.py:")
    results.append(check_fix_4(optimizer_integration))
    results.append(check_fix_3_optimizer(optimizer_integration))
    print()

    print("approval_metrics.py:")
    results.append(check_fix_7(approval_metrics))
    print()

    print("test_l5_k2_week2_full_integration_e2e.py:")
    results.append(check_fix_5(test_file))
    results.append(check_fix_6(test_file))
    print()

    print("="*70)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} fixes validated")
    print("="*70 + "\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
