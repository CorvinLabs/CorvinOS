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


def _is_ignored(rel: str) -> bool:
    # Trailing slash: a directory pattern such as ``.venv/`` only matches a
    # path git knows to be a directory, and a runtime path may not exist yet.
    return subprocess.run(["git", "check-ignore", "-q", rel.rstrip("/") + "/"],
                          cwd=REPO, capture_output=True).returncode == 0


def _resolves(path: Path) -> bool:
    """A named path is fine when it exists, or when it is runtime state the
    install creates — a git-ignored path such as the console ``.venv`` (which
    the unit's own ExecStartPre bootstraps) or ``.corvin`` — whose parent,
    the directory holding the FIRST ignored component, exists and holds
    tracked content.

    Requiring the runtime path itself to exist made this test pass only on a
    machine that had already booted the console once: a fresh clone, CI and
    every review worktree failed on ``core/console/.venv/bin/python``. The
    parent rule keeps the regression this file exists for — a renamed repo
    directory (``operator/`` -> ``corvin_operator/``) — failing, because the
    old directory is neither present nor tracked.
    """
    if path.exists():
        return True
    try:
        parts = path.relative_to(REPO).parts
    except ValueError:
        return False
    for i in range(len(parts)):
        if _is_ignored("/".join(parts[: i + 1])):
            parent = REPO.joinpath(*parts[:i])
            if not parent.is_dir():
                return False
            rel = "/".join(parts[:i]) or "."
            tracked = subprocess.run(["git", "ls-files", "--", rel], cwd=REPO,
                                     capture_output=True, text=True, check=True).stdout
            return bool(tracked.strip())
    return False


def _missing_in(text: str) -> list[str]:
    missing = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for raw in _PATH_RE.findall(line):
            path = raw
            for ph, val in PLACEHOLDERS.items():
                path = path.replace(ph, val)
            if not _resolves(Path(path)):
                missing.append(raw)
    return missing


def test_resolver_positive_controls():
    # The 2026-09-24 regression shape: a pre-rename directory, tracked or not.
    assert _missing_in("WorkingDirectory=__REPO_ROOT__/operator/bridges/discord")
    assert _missing_in("ExecStart=__REPO_ROOT__/operator/bridges/.venv/bin/python x")
    # Runtime state under a real, tracked directory is accepted.
    assert not _missing_in("ExecStart=__REPO_ROOT__/core/console/.venv/bin/python x")
    assert not _missing_in("WorkingDirectory=__BRIDGES_DIR__/discord")


def test_every_repo_path_in_a_template_exists():
    missing = []
    for tpl in _templates():
        for raw in _missing_in(tpl.read_text()):
            missing.append(f"{tpl.relative_to(REPO)}: {raw}")
    assert not missing, "unit templates name paths that do not exist:\n" + "\n".join(missing)
