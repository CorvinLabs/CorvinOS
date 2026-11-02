"""Route path ids must stay ONE path component on Windows too.

compute/workflow routes refused only ``/`` before ``<base> / <id>``. On Windows a
``%5C`` in the URL arrives as ``\\`` (a separator there) and ``C:\\...`` is
absolute, so ``/compute/runs/x%5C..%5C..%5C<other tenant>`` or
``/compute/runs/C:%5CWindows`` read outside the tenant's tree. POSIX keeps the
old rule (a backslash or colon is an ordinary character there).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))

from corvin_console.routes import compute, workflows  # noqa: E402


@pytest.mark.parametrize("mod", [compute, workflows])
@pytest.mark.parametrize("bad", ["x\\..\\..\\t2", "C:\\Windows", "C:x", "run:stream", "a/b"])
def test_windows_rejects_separator_drive_and_stream(mod, bad):
    with mock.patch.object(mod.os, "name", "nt"):
        assert mod._has_path_sep(bad)


@pytest.mark.parametrize("mod", [compute, workflows])
def test_plain_ids_pass(mod):
    for name in ("nt", "posix"):
        with mock.patch.object(mod.os, "name", name):
            assert not mod._has_path_sep("run-2026_11.02")


@pytest.mark.parametrize("mod", [compute, workflows])
def test_posix_rule_unchanged(mod):
    with mock.patch.object(mod.os, "name", "posix"):
        assert mod._has_path_sep("a/b")
        assert not mod._has_path_sep("a\\b")
        assert not mod._has_path_sep("a:b")
