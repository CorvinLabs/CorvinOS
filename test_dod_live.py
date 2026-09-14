#!/usr/bin/env python3
"""Live DoD Test: Testing this conversation task."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "core" / "skills" / "os_skills"))

from definition_of_done_verifier.skill import DoD_VerifierSkill

# Test parameters for THIS conversation
task_id = "complete-all-gaps-2026-09-14"
task_type = "cli_command"  # This is a CLI task
symbol_name = "DoD_VerifierSkill"  # Main implementation
commit_msg = "feat(dod-verifier): Phase 3 Complete + All Integrations + Live Test"

print("=" * 80)
print("🔬 LIVE DoD VERIFICATION TEST")
print("=" * 80)
print(f"Task ID: {task_id}")
print(f"Task Type: {task_type}")
print(f"Symbol: {symbol_name}")
print()

# Create and execute Skill
skill = DoD_VerifierSkill(
    cwd=Path(__file__).parent / "core" / "skills" / "os_skills"
)

try:
    result = skill.execute(
        task_id=task_id,
        task_type=task_type,
        symbol_name=symbol_name,
        commit_msg=commit_msg,
        test_path=Path(__file__).parent / "tests" / "skills" / "test_dod_verifier_phase1.py",
        test_output_file=Path(__file__).parent / ".test_output.txt",
    )

    print("✅ VERIFICATION COMPLETE")
    print("-" * 80)
    print(f"Score: {result.score:.1%}")
    print(f"Passed: {'✅ YES' if result.passed else '❌ NO'}")
    print()
    print("Check Results:")
    for check_name, check_data in result.checks.items():
        status = "✅" if check_data["passed"] else "❌"
        print(f"  {status} {check_name}: {check_data['evidence'][:50]}...")
    print()
    print(f"Reason: {result.reason}")
    print()
    print("Weights Applied:")
    for w_name, w_val in result.weights.items():
        print(f"  {w_name}: {w_val:.2f}")
    print()
    print("Audit Event ID:", result.audit_event_id)
    print("=" * 80)

    # Exit code based on pass/fail
    sys.exit(0 if result.passed else 1)

except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(2)
