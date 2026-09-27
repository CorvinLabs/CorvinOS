"""Regression: voice_lib.sh python resolution (0bf511288 follow-up).

``voice_resolve_python`` was always called as ``$(voice_resolve_python)`` — a
subshell — so its ``VOICE_PY_BIN`` cache never reached the caller and every
probe re-ran the whole chain, including ``core/console/bootstrap.sh`` (a pip
upgrade + install from PyPI). One ``speak`` therefore ran the bootstrap up to
three times when no venv carried ``openai``, and it ran even under
``CORVIN_TTS_LOCAL_ONLY=1``, the deployment mode that forbids cloud egress.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "voice_lib.sh"


@pytest.fixture
def fake_repo(tmp_path):
    scripts = tmp_path / "repo" / "corvin_operator" / "voice" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(_LIB, scripts / "voice_lib.sh")
    console = tmp_path / "repo" / "core" / "console"
    console.mkdir(parents=True)
    calls = tmp_path / "bootstrap_calls.log"
    # A bootstrap that always fails (offline host) and counts its runs.
    (console / "bootstrap.sh").write_text(f"echo run >> '{calls}'\nexit 1\n")
    return scripts / "voice_lib.sh", calls, tmp_path


def _run(lib: Path, tmp: Path, body: str, **env) -> subprocess.CompletedProcess:
    script = f"set -u\nsource '{lib}'\n{body}\n"
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp), "XDG_CONFIG_HOME": str(tmp / "xdg")}
    base.update(env)
    return subprocess.run(["bash", "-c", script], env=base, capture_output=True,
                          text=True, timeout=60)


def _count(calls: Path) -> int:
    return len(calls.read_text().splitlines()) if calls.exists() else 0


def test_bootstrap_runs_at_most_once_across_probes(fake_repo):
    lib, calls, tmp = fake_repo
    r = _run(lib, tmp, 'voice_python_init; a="$VOICE_PY_BIN"\n'
                       'b="$(voice_resolve_python)"\n'
                       'voice_detect_engine >/dev/null 2>&1 || true\n'
                       'echo "$a|$b"')
    assert r.returncode == 0, r.stderr
    assert _count(calls) == 1
    a, b = r.stdout.strip().split("|")
    assert a and a == b


def test_bootstrap_never_runs_when_local_only(fake_repo):
    lib, calls, tmp = fake_repo
    r = _run(lib, tmp, 'voice_python_init; echo "$VOICE_PY_BIN"',
             CORVIN_TTS_LOCAL_ONLY="1")
    assert r.returncode == 0, r.stderr
    assert _count(calls) == 0
    assert r.stdout.strip()  # still resolves a fallback interpreter


def test_no_script_resolves_python_in_a_subshell():
    """``$(voice_resolve_python)`` drops the cache (see module docstring); every
    in-repo caller must use ``voice_python_init; ... "$VOICE_PY_BIN"``."""
    scripts = _LIB.parent
    offenders = [
        f"{p.name}:{i}"
        for p in sorted(scripts.glob("*.sh"))
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if "$(voice_resolve_python)" in line and not line.lstrip().startswith("#")
    ]
    assert offenders == []
