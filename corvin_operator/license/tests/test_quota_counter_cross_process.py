"""Free-tier daily quota must hold across PROCESSES (Windows has no flock).

On Windows the ``fcntl`` shim is a no-op, so before the msvcrt byte-range lock
in ``quota_counter._do_increment_and_check`` two processes could both read
``count=N`` and write ``N+1`` -- exceeding the cap. N parallel children against
a limit of L must yield exactly L successes and N-L refusals on every platform.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

_CHILD = r'''
import sys
from pathlib import Path
import corvin_operator.license.quota_counter as qc
from corvin_operator.license.limits import LicenseLimitError
qc.get_limit = lambda feature: 5
try:
    qc.increment_and_check(Path(sys.argv[1]), "skill_forge_per_day", "_default")
    print("OK")
except LicenseLimitError:
    print("DENIED")
'''


def test_quota_not_exceeded_by_parallel_processes(tmp_path):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(_REPO), str(_REPO / "corvin_operator")])}
    home = tmp_path / "home"
    home.mkdir()
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD, str(home)], env=env,
                              cwd=str(tmp_path), stdout=subprocess.PIPE, text=True)
             for _ in range(12)]
    outs = [p.communicate(timeout=180)[0].strip().splitlines()[-1:] for p in procs]
    flat = [o[0] if o else "?" for o in outs]
    assert flat.count("OK") == 5, flat
    assert flat.count("DENIED") == 7, flat
    files = list((home / "quotas").glob("_default_skill_forge_per_day_*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["count"] == 5
