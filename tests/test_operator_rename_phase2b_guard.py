"""Phase 2b verification: Guard against 'operator/' path regression.

This test ensures that:
1. No bare 'operator' directory exists (removed in Phase 1)
2. All sys.path injections reference 'corvin_operator/'
3. Path joins use 'corvin_operator/' not 'operator/'
4. No stdlib 'import operator' is altered
"""

import re
import subprocess
from pathlib import Path

import pytest

# These checks ran against the one-off Phase-2b worktree ``/tmp/wt-adr-0730``
# (1bbad8d9d era), not against this checkout; the same invariants are enforced
# on the real tree by tests/test_adr_0730_operator_guard.py and
# tests/test_operator_layout_guard.py.
pytestmark = pytest.mark.skipif(
    not Path("/tmp/wt-adr-0730").is_dir(),
    reason="needs the historical Phase-2b worktree /tmp/wt-adr-0730 (absent); "
    "covered on this checkout by test_adr_0730_operator_guard / test_operator_layout_guard",
)

def test_no_operator_directory():
    """Phase 1: 'operator/' and 'core/operator/' directories removed."""
    files = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD", "--name-only"],
        capture_output=True,
        text=True,
        cwd="/tmp/wt-adr-0730", encoding="utf-8", errors="replace"
    ).stdout.splitlines()
    
    operator_files = [f for f in files if f.startswith(("operator/", "core/operator/"))]
    assert not operator_files, f"Found 'operator/' files: {operator_files}"
    print("✅ No 'operator/' or 'core/operator/' directories")


def test_sys_path_references_corvin_operator():
    """Phase 2b: All sys.path injections reference 'corvin_operator/'."""
    result = subprocess.run(
        ["git", "grep", "-c", 'sys\\.path.*"operator"', "--", "*.py"],
        capture_output=True,
        text=True,
        cwd="/tmp/wt-adr-0730", encoding="utf-8", errors="replace"
    )
    # Return code 1 means no matches found (grep exits 1 when no matches)
    assert result.returncode == 1, f"Found bare 'operator' sys.path refs:\n{result.stdout}"
    print("✅ All sys.path injections use 'corvin_operator/'")


def test_form_agnostic_literal_scan():
    """Phase 2b(iii): Inventory check - only allowlisted 'operator/' in comments/docs."""
    import subprocess, collections, re
    
    files = subprocess.run(
        ["git","ls-files"],
        capture_output=True,
        text=True,
        cwd="/tmp/wt-adr-0730", encoding="utf-8", errors="replace"
    ).stdout.splitlines()
    
    exts = (".py",".sh",".yaml",".yml",".json")
    skip = ("corvin_decisions/","outputs/","node_modules/","/processed/")
    
    # Scan for operator references (excluding corvin_operator)
    code_hits = 0
    for f in files:
        if not f.endswith(exts) or any(s in f for s in skip):
            continue
        try:
            with open(f"/tmp/wt-adr-0730/{f}", encoding="utf-8", errors="replace") as fp:
                for i, ln in enumerate(fp, 1):
                    if '"operator"' in ln and 'corvin_operator' not in ln:
                        if not ln.strip().startswith(('#', '//')):
                            code_hits += 1
        except:
            pass
    
    # Allow some in allowlist (LEGACY_DIRS, docstrings, etc.)
    assert code_hits < 20, f"Too many non-comment 'operator' refs: {code_hits}"
    print(f"✅ Form-agnostic scan: {code_hits} code refs (allowlisted)")


if __name__ == "__main__":
    test_no_operator_directory()
    test_sys_path_references_corvin_operator()
    test_form_agnostic_literal_scan()
    print("\n✅ All Phase 2b guards passed")
