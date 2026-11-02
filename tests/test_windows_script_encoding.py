"""Windows script encoding guards.

1. Windows PowerShell 5.1 decodes a BOM-less .ps1 as ANSI (cp1252). The UTF-8
   bytes of characters like U+2713 or U+2014 then contain cp1252 smart quotes,
   which PowerShell treats as string delimiters -- the script dies with a parse
   error before line 1 runs (validate-install.ps1 and install_repair.ps1 did).
   Every checked-in .ps1 is therefore either pure ASCII or carries a UTF-8 BOM.

2. The hidden-launch .vbs / generated .ps1 wrappers embed absolute profile
   paths. Written with -Encoding ASCII, a non-ASCII profile folder (an umlaut
   in the user name) became '?', and wscript.exe reads a BOM-less file as
   ANSI anyway -- the autostart task silently started nothing. They must be
   written as UTF-16 LE with BOM (-Encoding Unicode).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SKIP_PARTS = {".venv", "node_modules", "corvin_decisions", ".git"}


def _repo_ps1_files() -> list[Path]:
    return sorted(
        p for p in _REPO.rglob("*.ps1")
        if not (_SKIP_PARTS & set(p.relative_to(_REPO).parts))
    )


def test_non_ascii_ps1_files_carry_a_utf8_bom() -> None:
    offenders = []
    for p in _repo_ps1_files():
        raw = p.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or raw.startswith(b"\xff\xfe"):
            continue
        try:
            raw.decode("ascii")
        except UnicodeDecodeError:
            offenders.append(str(p.relative_to(_REPO)))
    assert not offenders, (
        "These .ps1 files contain non-ASCII characters but no UTF-8 BOM; "
        f"Windows PowerShell 5.1 fails to parse them: {offenders}"
    )


_WRAPPER_WRITERS = [
    _REPO / "install.ps1",
    _REPO / "update.ps1",
    _REPO / "corvin_operator" / "bridges" / "bridge.ps1",
]


@pytest.mark.parametrize("path", _WRAPPER_WRITERS, ids=lambda p: p.name)
def test_generated_wrappers_are_not_written_as_ascii(path: Path) -> None:
    src = path.read_text(encoding="utf-8-sig")
    bad = [
        line.strip() for line in src.splitlines()
        if re.search(r"Set-Content\b.*(\$vbs|\$ps1|\$VbsPath)", line, re.I)
        and re.search(r"-Encoding\s+ASCII\b", line, re.I)
    ]
    assert not bad, f"{path.name}: wrapper written as ASCII: {bad}"


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("wscript.exe"),
                    reason="needs Windows Script Host")
def test_unicode_vbs_runs_a_target_under_a_non_ascii_path(tmp_path: Path) -> None:
    d = tmp_path / "Jürgen Müller"
    d.mkdir()
    marker = d / "ran.txt"
    vbs = d / "t.vbs"
    cmd = f'cmd.exe /c echo ok > "{marker}"'
    body = 'CreateObject("WScript.Shell").Run "' + cmd.replace('"', '""') + '", 0, True'
    vbs.write_text(body, encoding="utf-16")
    subprocess.run(["wscript.exe", "//B", str(vbs)], check=False, timeout=60)
    assert marker.exists()
