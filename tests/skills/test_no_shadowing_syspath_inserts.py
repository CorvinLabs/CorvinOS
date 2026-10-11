"""No test may put ``core/skills/os_skills`` on ``sys.path``.

That directory holds a package named ``audit`` (and ``monitoring``, ``flow_guard`` …). Once it is
first on ``sys.path`` for the rest of the process, ``import audit`` — the CORE audit writer — resolves
to that package instead and every later fail-closed audit write reports "core audit writer
unavailable" (observed: 3 infinite-session tests failed only when one of four DoD/Data-Hub test
files ran before them). Import through the package path ``core.skills.os_skills.<name>`` instead.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSERT = re.compile(r"sys\.path\.(insert|append)\([^\n]*os_skills")


def test_no_test_file_shadows_the_core_audit_module():
    offenders = []
    files = 0
    for base in (ROOT / "tests", ROOT / "core", ROOT / "corvin_operator"):
        for path in base.rglob("test*.py"):
            if "node_modules" in path.parts or path == Path(__file__).resolve():
                continue
            files += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            # A temporary insert that is taken out again in the same file is fine.
            if INSERT.search(text) and "sys.path.remove(" not in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert files > 100, f"positive control: the scan only reached {files} files"
    assert not offenders, f"sys.path insert of core/skills/os_skills (shadows `audit`): {offenders}"
