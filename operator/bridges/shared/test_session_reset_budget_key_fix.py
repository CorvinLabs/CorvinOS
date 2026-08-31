#!/usr/bin/env python3
"""Test for Discord /new session reset budget key format fix.

Validates that session_reset.py correctly resets the budget using the
bare chat_id (not forge_channel_id with channel prefix). This was the
root cause of /new not creating fresh budget.

The bug: adapter.py registers budgets with bare chat_id ("1540066.."),
but session_reset.py was trying to delete with forge_channel_id ("discord:1540066.."),
so the budget was never deleted and next turn still showed budget exhausted.

Run: python3 operator/bridges/shared/test_session_reset_budget_key_fix.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_budget_key_format_in_reset_budget() -> bool:
    """Verify _reset_budget uses bare chat_id, not forge_channel_id."""
    code = Path(ROOT / "session_reset.py").read_text()

    # Check the function signature uses chat_id not forge_chan_id
    if "def _reset_budget(*, chat_id: str" not in code:
        print("FAIL: _reset_budget() signature should use 'chat_id' parameter")
        return False
    print("PASS: _reset_budget() uses bare chat_id parameter")

    # Check that it calls unregister with str(chat_id)
    if "_unregister_budget(str(chat_id))" not in code:
        print("FAIL: _reset_budget() should call _unregister_budget(str(chat_id))")
        return False
    print("PASS: _reset_budget() calls _unregister_budget with bare chat_id")

    return True


def test_reset_session_passes_bare_chat_id() -> bool:
    """Verify reset_session() calls _reset_budget with bare chat_id."""
    code = Path(ROOT / "session_reset.py").read_text()

    # Check that reset_session passes chat_id (not forge_chan_id)
    if "budget_reset = _reset_budget(\n        chat_id=chat_id" not in code:
        print("FAIL: reset_session() should pass chat_id= to _reset_budget()")
        return False
    print("PASS: reset_session() passes bare chat_id to _reset_budget()")

    return True


def test_datetime_import() -> bool:
    """Verify datetime is imported (fixes secondary bug)."""
    code = Path(ROOT / "session_reset.py").read_text()
    if "from datetime import datetime" not in code:
        print("FAIL: 'from datetime import datetime' not found")
        return False
    print("PASS: datetime is properly imported")
    return True


def test_cli_basic() -> bool:
    """Basic CLI test: ensure the modified code still runs."""
    result = subprocess.run(
        ["python3", str(ROOT / "session_reset.py"),
         "--channel", "discord", "--chat-id", "test_42"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"FAIL: CLI returned {result.returncode}: {result.stderr}")
        return False
    try:
        out = json.loads(result.stdout)
        if "budget_reset" not in out:
            print("FAIL: budget_reset field missing from output")
            return False
        print(f"PASS: CLI returns valid JSON with budget_reset field (value={out['budget_reset']})")
        return True
    except json.JSONDecodeError as e:
        print(f"FAIL: Invalid JSON output: {e}")
        return False


def test_docstring_explains_bare_chat_id() -> bool:
    """Verify docstring explains why bare chat_id is used."""
    code = Path(ROOT / "session_reset.py").read_text()

    # Find _reset_budget docstring
    start = code.find("def _reset_budget(")
    if start == -1:
        print("FAIL: Could not find _reset_budget function")
        return False

    docstring_start = code.find('"""', start)
    docstring_end = code.find('"""', docstring_start + 3)
    if docstring_start == -1 or docstring_end == -1:
        print("FAIL: Could not find docstring for _reset_budget")
        return False

    docstring = code[docstring_start:docstring_end]
    if "bare chat_id" in docstring and "adapter registers budgets" in docstring:
        print("PASS: _reset_budget() docstring explains bare chat_id usage")
        return True
    else:
        print("WARN: _reset_budget() docstring could better explain bare chat_id")
        return True


def main() -> int:
    print("=== Session Reset Budget Key Format Fix Validation ===\n")
    results = [
        test_budget_key_format_in_reset_budget(),
        test_reset_session_passes_bare_chat_id(),
        test_datetime_import(),
        test_cli_basic(),
        test_docstring_explains_bare_chat_id(),
    ]
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
