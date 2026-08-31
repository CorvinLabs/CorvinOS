"""Test isolation for the license suite.

``test_license_hardening.py`` re-imports ``license.*`` submodules with a clean
module state (its ``_fresh_validator`` helper deletes every ``license.*`` key
from ``sys.modules`` and re-imports). Without restoration this leaks: after such
a test runs, ``sys.modules['license.instance_epoch']`` (and siblings) are NEW
module objects. A later test that captured ``import license.instance_epoch as IE``
at collection time then monkeypatches the STALE object, while production code
resolves ``from .instance_epoch import read_instance_epoch`` against the fresh
one — so the patch silently misses.

This bit the anti-rollback regression tests in ``test_epoch_downgrade_floor.py``
(epoch-downgrade flooring): they passed in isolation but failed after the
hardening suite ran first, because the floor monkeypatch hit a dead module
object. A security control whose regression test is order-dependent can let a
real regression slip through CI — so we fix the isolation at the root.

The autouse fixture snapshots the ``license`` package and all ``license.*``
submodule objects before each test and restores that exact mapping afterwards.
Any submodule a test deletes/re-imports is reverted, so every test starts from
the same pristine, mutually-consistent set of module objects.
"""
from __future__ import annotations

import sys

import pytest


def _license_keys() -> list[str]:
    return [k for k in sys.modules if k == "license" or k.startswith("license.")]


@pytest.fixture(autouse=True)
def _restore_license_modules():
    # Snapshot the current license.* module objects (the pristine set, because
    # the previous test's teardown already restored them).
    saved = {k: sys.modules[k] for k in _license_keys()}
    try:
        yield
    finally:
        # Drop any module objects a test created/replaced, then reinstate the
        # originals so identity stays stable for the next test.
        for k in _license_keys():
            if k not in saved:
                del sys.modules[k]
        sys.modules.update(saved)
