"""Every PYTHONPATH directory the bridge scripts compose must exist.

``bridge.sh console`` and ``run-all-tests.sh`` (GW_PYTHONPATH) still named
``<repo>/operator/{forge,skill-forge,bridges/shared}`` after the directory
became ``corvin_operator/``: the console started with no ``spawn_gates`` /
forge on its path (``ModuleNotFoundError: No module named 'spawn_gates'``),
and the gateway suites ran against a PYTHONPATH that did not match the unit's.
Same bug class as tests/test_unit_template_paths.py, for shell scripts.
"""
from __future__ import annotations

import re
from pathlib import Path

BRIDGES = Path(__file__).resolve().parents[1]
REPO = BRIDGES.parents[1]

# (script, assignment regex, placeholder → value)
_CASES = [
    (BRIDGES / "bridge.sh", re.compile(r'^\s*local pypath="([^"]+)"', re.M), {"$repo_root": str(REPO)}),
    (BRIDGES / "run-all-tests.sh", re.compile(r'^GW_PYTHONPATH="([^"]+)"', re.M), {"${_REPO_ABS}": str(REPO)}),
]


def _entries() -> list[tuple[str, str]]:
    out = []
    for script, rx, subs in _CASES:
        m = rx.search(script.read_text(encoding="utf-8"))
        assert m, f"{script.name}: PYTHONPATH assignment not found (update this test)"
        for raw in m.group(1).split(":"):
            path = raw
            for ph, val in subs.items():
                path = path.replace(ph, val)
            out.append((f"{script.name}: {raw}", path))
    return out


def test_positive_control_parses_both_scripts():
    entries = _entries()
    assert len(entries) >= 10, entries
    assert any(p.endswith("corvin_operator/bridges/shared") for _, p in entries)


def test_every_pythonpath_dir_exists():
    missing = [label for label, path in _entries() if not Path(path).is_dir()]
    assert not missing, "PYTHONPATH entries that do not exist:\n" + "\n".join(missing)
