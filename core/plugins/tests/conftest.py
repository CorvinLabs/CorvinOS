"""Audit-chain isolation for the plugin suite.

Why this file exists, and why it is the FOURTH place in this repo that needed
it: `tests/conftest.py` at the repo root redirects `VOICE_AUDIT_PATH` to tmp for
everything under `tests/`. The plugin suite does not live there — it lives next
to the package it tests — so it was never covered.

That matters here more than almost anywhere else, because this suite exercises
BOOT paths. `bootstrap_declared()` / `bootstrap_tenant()` / `bootstrap_global()`
build a real `PluginContext` whose `audit_emit` resolves the real hash-chained
writer. A test that loads a plugin therefore appends `plugin.loaded` to the live
GDPR Art. 30 chain — and because the chain is hash-linked and append-only, those
records can never be removed afterwards without corrupting it. One such run left
28 permanent test records in `.corvin/global/forge/audit.jsonl`.

They are content-free lifecycle records, so this was noise rather than a data
leak. It is still the wrong default: on a host with a pinned service directory
the same code writes into the production chain, and "unremovable" is the part
that makes it worth a structural fix instead of a per-test patch.

Individual tests that patch `audit_emit` directly still do so — this is a floor,
not a replacement for isolating a specific call path.

Feature-flag isolation (added 2026-07-28)
-----------------------------------------
The same "next to the package, so no conftest covered it" gap applied to the
operator's feature-flag overlay, and it bit harder. `feature_flags.is_enabled()`
resolves the tenant home from `CORVIN_HOME`, so on a machine whose
`.corvin/tenants/_default/global/features.json` has flags switched on, this
suite reads them. On the maintainer's own box — 15 flags on, including
`bridge_supervisor_plugins` — 17 tests failed: `bootstrap_declared()` injected
the seven bundled bridge supervisors into assertions that expected exactly the
one plugin the test had declared.

Measured the same day: 1073 passed with a clean `CORVIN_HOME`, 17 failed with
the live one. That is the worst possible split, because it points the wrong
way — a clean CI runner is green, so the gate this suite became on 2026-07-27
reports success while the developer who actually runs it locally sees red and
has no reason to trust either result.

Pinning `CORVIN_HOME` to tmp gives every test the shipped defaults (absent
overlay = every flag off, which is what they already assert). Tests that need a
flag ON build their own home and pass it explicitly, so they are unaffected.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# ``import audit`` collision isolation (added 2026-08-29)
# ------------------------------------------------------
# Nine different ``audit.py`` modules are bare-importable in this repo (bridges,
# learning, console, compute, awpkg, …). Seven files in THIS suite do a runtime
# ``import audit`` and expect the ``operator/bridges/shared/audit.py`` one — the
# module that carries ``_se`` / ``audit_event`` / ``verify_audit``. But
# ``sys.modules['audit']`` is a process-global cache: whichever file imports
# ``audit`` first wins, so when the whole suite runs together a different
# ``audit.py`` (e.g. ``core/learning/audit.py``, which has no ``_se``) could
# already be cached, and every later test read the wrong module —
# ``AttributeError: module 'audit' has no attribute '_se'``. Each file passed in
# isolation and failed in the aggregate: classic cross-file ``sys.modules``
# pollution, invisible on a per-file CI shard.
#
# conftest is imported before any test module in this directory, so pre-binding
# the correct ``audit`` here — and re-pinning it before every test, in case a
# test popped or replaced it — makes the bare name resolve deterministically.
_SHARED = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"


def _bind_shared_audit():
    if str(_SHARED) not in sys.path:
        sys.path.insert(0, str(_SHARED))
    cached = sys.modules.get("audit")
    want = (_SHARED / "audit.py").resolve()
    if cached is None or Path(getattr(cached, "__file__", "")).resolve() != want:
        sys.modules.pop("audit", None)
        return importlib.import_module("audit")
    return cached


_SHARED_AUDIT = _bind_shared_audit()


@pytest.fixture(autouse=True)
def _pin_shared_audit_module():
    """Every ``import audit`` in this suite resolves to bridges/shared/audit.py."""
    sys.modules["audit"] = _SHARED_AUDIT
    yield


@pytest.fixture(autouse=True)
def _isolated_audit_chain_for_plugin_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))


@pytest.fixture(autouse=True)
def _shipped_defaults_not_the_operators_flags(monkeypatch, tmp_path):
    """No test in this suite may read the developer's own feature flags."""
    home = tmp_path / "corvin_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
