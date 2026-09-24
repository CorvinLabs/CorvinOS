"""Every repo path a tracked systemd unit template names must exist.

Nine bridge templates still pointed at ``__REPO_ROOT__/operator/bridges/…`` long
after the directory became ``corvin_operator/``. Installed units had been fixed
by hand, so nothing failed until ``bridge.sh install-units`` re-rendered them
(2026-09-24): every bridge crash-looped on ``status=200/CHDIR``.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLACEHOLDERS = {
    "__REPO_ROOT__": str(REPO),
    "__BRIDGES_DIR__": str(REPO / "corvin_operator" / "bridges"),
    "__PLUGIN_ROOT__": str(REPO / "corvin_operator" / "voice"),
}
_PATH_RE = re.compile(r"(?:%s)[^\s'\":;]*" % "|".join(map(re.escape, PLACEHOLDERS)))


def _templates() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.service"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout.split()
    return [REPO / p for p in out if not p.startswith(".claude/")]


def test_templates_found_positive_control():
    names = {p.name for p in _templates()}
    assert "corvin-voice-bridge-discord.service" in names
    assert "corvin-webui.service" in names


def test_every_repo_path_in_a_template_exists():
    missing = []
    for tpl in _templates():
        for line in tpl.read_text().splitlines():
            if line.lstrip().startswith("#"):
                continue
            for raw in _PATH_RE.findall(line):
                path = raw
                for ph, val in PLACEHOLDERS.items():
                    path = path.replace(ph, val)
                if not Path(path).exists():
                    missing.append(f"{tpl.relative_to(REPO)}: {raw}")
    assert not missing, "unit templates name paths that do not exist:\n" + "\n".join(missing)
