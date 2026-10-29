"""One sandboxed console per test module for the adversarial DAG suite (F1 tiers).

Importing the console app dominates the time of a single test (about 4 s), and CI bounds the whole
``core/console/tests/`` directory to 20 minutes, so a module shares ONE real console (uvicorn over a
socket, a real session, a sandboxed CORVIN_HOME) and each test starts from a wiped workflow directory.
A test that counts audit-chain events takes a before/after delta; a test that needs a second tenant or a
changed client session asks for ``fresh_sandbox`` (its own console) instead.

A test module opts in with:

    from dag_adv_console import fresh_sandbox, live_sandbox, module_console  # noqa: F401
"""
from __future__ import annotations

import shutil
from contextlib import contextmanager

import pytest

from test_workflow_resume_hardening import _live_sandbox as fresh_sandbox  # noqa: F401  (re-exported)

_SHARED: dict = {}


@pytest.fixture(scope="module", autouse=True)
def module_console(tmp_path_factory):
    root = tmp_path_factory.mktemp("console")
    with fresh_sandbox(root) as ctx:
        _SHARED["ctx"] = ctx
        _SHARED["root"] = root
        yield
    _SHARED.clear()


@contextmanager
def live_sandbox(_tmp_path=None):
    client, home, tid = _SHARED["ctx"]
    from forge import paths as fp
    shutil.rmtree(fp.tenant_home(tid) / "workflows", ignore_errors=True)
    # The Free tier meters 10 compute units a day (ADR-0094). A console of its own per test started every
    # test at zero; a shared one must too, or the 11th run of the module is refused with 402 (a real
    # licence answer, not a defect). The counter lives in THIS sandbox's home, never in the live install.
    assert str(home).startswith(str(_SHARED["root"])), "refusing to reset a counter outside the sandbox"
    shutil.rmtree(home / "global" / "license", ignore_errors=True)
    yield client, home, tid
