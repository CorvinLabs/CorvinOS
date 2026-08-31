"""Regression: a permissive-mode sync_cache.json must not silently discard a
cached is_revoked=True record (adversarial review finding).

Before this fix, `load_sync_cache()` responded to a permissive file mode by
returning a brand-new empty `SyncCache()` — discarding whatever was cached,
including a legitimately-synced revocation. A local attacker with chmod
access to their own cache file could un-revoke a cancelled/revoked license
on every status check just by making the file world-readable. The correct
behaviour (mirrored from `operator/license/compute_quota.py::_load`) is to
log a warning but still honor the cached content; the mode is corrected on
the next write via `save_sync_cache()`.
"""
from __future__ import annotations

import os
import sys

import pytest

from corvin_license import sync as S


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX mode bits only")
def test_permissive_mode_still_honors_a_cached_revocation(sandbox_home):
    cache = S.SyncCache(is_revoked=True, server_tier="member")
    S.save_sync_cache(cache)

    path = S._cache_path()
    os.chmod(path, 0o644)  # simulate an attacker (or bad umask) loosening the mode

    loaded = S.load_sync_cache()
    assert loaded.is_revoked is True, (
        "a permissive file mode must not discard a cached is_revoked=True "
        "record — that is a revocation bypass"
    )
    assert loaded.server_tier == "member"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX mode bits only")
def test_permissive_mode_is_corrected_on_next_save(sandbox_home):
    cache = S.SyncCache(is_revoked=False)
    S.save_sync_cache(cache)
    path = S._cache_path()
    os.chmod(path, 0o644)

    S.load_sync_cache()  # honors content, does not itself fix the mode
    S.save_sync_cache(S.SyncCache(is_revoked=False))  # next write corrects it

    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
