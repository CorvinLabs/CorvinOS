"""A bridge test must never reach the live audit chain.

Live finding 2026-09-19: the canonical chain of the running install
(``CORVIN_HOME=<repo>/.corvin``) holds 40 ``engine.span.end`` records with
``role=worker`` from 2026-09-18 13:46 — span ids ``spn-awp-fetch``,
``spn-a2a-t1``, ``spn-a2a-dup-task-1``, engine ids ``fake``, ``x``,
``bidirectional-fake`` — written by test_awp_walker.py, test_a2a_worker.py and
test_a2a_bidirectional.py. ``paths.corvin_home()`` falls back to the REPO-LOCAL
``.corvin`` when ``CORVIN_HOME`` is unset, which on this host IS the service's
root, so a test process without a redirect appends to the production GDPR
Art. 30 record (append-only: the records are permanent). The Models console
then counts them as delegated worker runs.

``tests/conftest.py`` has redirected every test under ``tests/`` since
2026-07-24; this directory had no such fixture. Now it does
(``conftest.py::_isolated_audit_chain`` — chain only, see its docstring for
why not the whole home), and this test pins it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_shared = Path(__file__).resolve().parent
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))

import paths  # type: ignore  # noqa: E402

_REPO_CORVIN = Path(__file__).resolve().parents[3] / ".corvin"


def test_the_chain_this_process_appends_to_is_not_the_live_one():
    import audit  # type: ignore

    redirect = os.environ.get("VOICE_AUDIT_PATH", "")
    assert redirect, "VOICE_AUDIT_PATH is unset — audit_event() would append to the live chain"
    chain = Path(audit.audit_path()).resolve()
    assert chain == Path(redirect).resolve()
    assert not str(chain).startswith(str(_REPO_CORVIN.resolve())), chain
    assert not str(chain).startswith(str(Path(paths.corvin_home()).resolve())), chain


def test_the_install_itself_stays_readable():
    """Chain only — the fixture must not point CORVIN_HOME at an empty dir
    (70 tests of this directory read the install's config; see conftest)."""
    assert paths.corvin_home().exists()
