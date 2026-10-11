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


def test_os_skills_is_a_regular_package():
    """Review ACP-1: without core/skills/os_skills/__init__.py pytest's import mode itself put the
    directory on sys.path for every os_skills/<pkg>/tests package - the regex above cannot see that."""
    assert (ROOT / "core" / "skills" / "os_skills" / "__init__.py").is_file()


def test_collecting_an_os_skills_test_package_does_not_put_os_skills_on_sys_path():
    """The runtime check behind the static ones: collecting os_skills test packages must not leave
    the os_skills directory on sys.path (where its ``audit`` package shadows the core writer)."""
    import subprocess
    import sys

    code = (
        "import sys, pytest, os; "
        "pytest.main(['-q', '--co', '-p', 'no:cacheprovider', '-o', 'addopts=', "
        "'core/skills/os_skills/daemon/tests', 'core/skills/os_skills/audit/tests']); "
        "target = os.path.realpath('core/skills/os_skills'); "
        "print('HITS=' + str(sum(1 for p in sys.path if p and os.path.realpath(p) == target)))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                         timeout=120)
    line = [l for l in out.stdout.splitlines() if l.startswith("HITS=")]
    assert line, out.stdout[-2000:] + out.stderr[-2000:]
    assert line[0] == "HITS=0", line[0]
