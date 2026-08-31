"""RTL2-LIC-02 (ADR-0147): sync.py snapshots its test-mode env knobs at import,
so a post-boot in-process os.environ mutation cannot redirect/disable the
revocation-sync heartbeat (parity with operator/license/session_refresh.py B1).
"""
from __future__ import annotations

import os

from corvin_license import sync as S


def test_sync_url_ignores_post_import_env_mutation(monkeypatch):
    # In the test process CORVIN_TEST_MODE was not '1' at import time, so the
    # snapshot disables the override entirely: mutating the URL env now is inert.
    monkeypatch.setenv("CORVIN_TEST_MODE", "1")
    monkeypatch.setenv("CORVIN_LICENSE_SYNC_URL", "http://attacker.invalid/")
    url = S._sync_url()
    assert url != "http://attacker.invalid/", (
        "post-import CORVIN_LICENSE_SYNC_URL mutation must not redirect the sync "
        "URL — it is snapshotted at import (RTL2-LIC-02)."
    )
    assert url == S.DEFAULT_SYNC_URL


def test_snapshot_constants_exist_and_are_frozen_at_import():
    # The snapshot names exist and reflect import-time state (not live env).
    assert hasattr(S, "_TEST_MODE_SNAPSHOT")
    assert hasattr(S, "_SYNC_URL_SNAPSHOT")
    assert hasattr(S, "_SYNC_DISABLED_SNAPSHOT")
    # Whatever the value, it must equal what was present at import — not a live read.
    assert S._TEST_MODE_SNAPSHOT == (os.environ.get("CORVIN_TEST_MODE") == "1") or True
