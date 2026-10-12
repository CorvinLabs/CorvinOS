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
OS_SKILLS = (ROOT / "core" / "skills" / "os_skills").resolve()
INSERT = re.compile(r"sys\.path\.(?:insert|append)\(([^\n]*)")
# ``Path(__file__)[.resolve()]`` followed by ``.parent`` x n or ``.parents[n]`` - resolved per file,
# because an insert of ``parents[2]`` names os_skills without the literal (review ACP2-3).
FILE_REL = re.compile(r"Path\(__file__\)(?:\.resolve\(\))?((?:\.parent\b)+|\.parents\[(\d+)\])")


def _targets(path: Path, arg: str) -> list[Path]:
    out = []
    if "os_skills" in arg and "core.skills.os_skills" not in arg:
        out.append(OS_SKILLS)
    for m in FILE_REL.finditer(arg):
        n = int(m.group(2)) + 1 if m.group(2) is not None else m.group(1).count(".parent")
        tgt = path.resolve()
        for _ in range(n):
            tgt = tgt.parent
        sub = arg[m.end():]
        # ``... / "x"`` appended after the parent chain
        for part in re.findall(r'^\s*/\s*["\']([^"\']+)["\']', sub):
            tgt = tgt / part
        out.append(tgt)
    return out


def test_no_test_file_shadows_the_core_audit_module():
    offenders = []
    files = 0
    for base in (ROOT / "tests", ROOT / "core", ROOT / "corvin_operator"):
        for path in list(base.rglob("test*.py")) + list(base.rglob("conftest.py")):
            if "node_modules" in path.parts or path == Path(__file__).resolve():
                continue
            files += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in INSERT.finditer(text):
                # Narrow exemption (no longer "any sys.path.remove( anywhere"): the SAME variable
                # is inserted and removed again, i.e. a transient insert around one import.
                var = re.match(r"\s*(?:0\s*,\s*)?([A-Za-z_]\w*)\s*\)", m.group(1))
                if var and f"sys.path.remove({var.group(1)})" in text:
                    continue
                if any(t == OS_SKILLS for t in _targets(path, m.group(1))):
                    offenders.append(str(path.relative_to(ROOT)))
                    break
    assert files > 100, f"positive control: the scan only reached {files} files"
    assert not offenders, f"sys.path insert of core/skills/os_skills (shadows `audit`): {offenders}"


def test_the_scanner_sees_a_parents_n_insert():
    """Positive control for the resolution above (the evasion shape from review ACP2-3)."""
    demo = OS_SKILLS / "data_hub" / "tests" / "test_demo.py"
    assert OS_SKILLS in _targets(demo, "0, str(Path(__file__).resolve().parents[2]))")
    assert OS_SKILLS in _targets(demo, "0, str(Path(__file__).parent.parent.parent))")
    assert OS_SKILLS not in _targets(demo, "0, str(Path(__file__).resolve().parents[4]))")


def test_os_skills_is_a_regular_package():
    """Review ACP-1: without core/skills/os_skills/__init__.py pytest's import mode itself put the
    directory on sys.path for every os_skills/<pkg>/tests package - the regex above cannot see that."""
    assert (ROOT / "core" / "skills" / "os_skills" / "__init__.py").is_file()


def test_collecting_every_os_skills_test_package_leaves_audit_resolving_to_the_core_writer():
    """The runtime check behind the static ones: after collecting EVERY os_skills test package plus
    tests/skills, os_skills is not on sys.path and ``audit`` resolves outside it (review ACP2-3: only
    two packages were collected)."""
    import subprocess
    import sys

    code = (
        "import sys, pytest, os, importlib.util; "
        "pytest.main(['-q', '--co', '-p', 'no:cacheprovider', '-o', 'addopts=', "
        "'core/skills/os_skills', 'tests/skills']); "
        "target = os.path.realpath('core/skills/os_skills'); "
        "print('HITS=' + str(sum(1 for p in sys.path if p and os.path.realpath(p) == target))); "
        "m = sys.modules.get('audit'); "
        "o = getattr(m, '__file__', None) or getattr(importlib.util.find_spec('audit'), 'origin', ''); "
        "print('AUDIT=' + os.path.realpath(o or ''))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                         timeout=600)
    hits = [l for l in out.stdout.splitlines() if l.startswith("HITS=")]
    aud = [l for l in out.stdout.splitlines() if l.startswith("AUDIT=")]
    assert hits and aud, out.stdout[-2000:] + out.stderr[-2000:]
    assert hits[0] == "HITS=0", hits[0]
    assert str(OS_SKILLS) not in aud[0], aud[0]
