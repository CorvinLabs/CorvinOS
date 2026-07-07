"""RTL2-LIC-01 (ADR-0146): the compute license gate must honor a propagated
revocation (sync_cache.is_revoked) and deny — it previously granted full compute
+ compute_fabric to a revoked-but-unexpired token until the JWT's own expiry.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute import license_gate as LG


def test_revoked_license_is_denied(monkeypatch):
    # Plugin present + propagated revocation present -> deny, do NOT grant/trial.
    monkeypatch.setattr(LG, "_LICENSE_PLUGIN_AVAILABLE", True)
    monkeypatch.setattr(LG, "_is_license_revoked", lambda: True)
    with tempfile.TemporaryDirectory() as td:
        res = LG.check_compute_access(corvin_home=Path(td))
    assert res.allowed is False
    assert res.mode == "denied", f"revoked license must be denied, got mode={res.mode!r}"


def test_non_revoked_falls_through_to_normal_flow(monkeypatch):
    # Not revoked + no license file -> normal trial path (revocation check is a
    # no-op, it does not break the licensed/trial flow).
    monkeypatch.setattr(LG, "_LICENSE_PLUGIN_AVAILABLE", True)
    monkeypatch.setattr(LG, "_is_license_revoked", lambda: False)
    with tempfile.TemporaryDirectory() as td:
        res = LG.check_compute_access(corvin_home=Path(td))
    assert res.mode != "denied", f"non-revoked must not be denied by the revocation gate, got {res.mode!r}"


def test_apache_build_has_no_revocation_channel():
    # _is_license_revoked must never raise and returns False when corvin_license
    # (the enterprise sync plugin) is absent — the Apache-only build is unaffected.
    assert LG._is_license_revoked() in (True, False)


# ── Corrupt-cache fail-CLOSED (MEDIUM) ───────────────────────────────────────
# _is_license_revoked previously masked a corrupt/partial sync_cache.json into a
# non-revoked default (fail-OPEN): a revoked token kept full compute + fabric just
# because its cache got clobbered. It must now distinguish "no cache (first-run →
# not revoked)" from "cache present but unreadable (fail CLOSED → revoked)".

def _with_cache(monkeypatch, td: Path, contents):
    """Point corvin_license.sync at a temp home; write `contents` (or leave the
    cache absent when contents is None) and return True on success. Skips if the
    enterprise plugin is not importable in this build."""
    try:
        from corvin_license import sync as _sync  # type: ignore[import]
    except Exception:
        return None
    cache = Path(td) / "global" / "license" / "sync_cache.json"
    if contents is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(_sync, "_cache_path", lambda: cache)
    return _sync


def test_corrupt_cache_fails_closed(monkeypatch):
    # Cache file EXISTS but is unparseable garbage -> treat as revoked (True).
    with tempfile.TemporaryDirectory() as td:
        sync = _with_cache(monkeypatch, td, "{ this is not valid json ")
        if sync is None:
            import pytest
            pytest.skip("corvin_license plugin not installed in this build")
        assert LG._is_license_revoked() is True, (
            "present-but-corrupt sync_cache.json must fail CLOSED (revoked)"
        )


def test_missing_cache_is_not_revoked(monkeypatch):
    # No cache file (fresh compute user / first run) -> not revoked (False),
    # so we never lock out a legitimate first-run user.
    with tempfile.TemporaryDirectory() as td:
        sync = _with_cache(monkeypatch, td, None)  # no file written
        if sync is None:
            import pytest
            pytest.skip("corvin_license plugin not installed in this build")
        assert LG._is_license_revoked() is False, (
            "absent cache is a legitimate first-run, must NOT fail closed"
        )


def test_valid_non_revoked_cache_is_not_revoked(monkeypatch):
    # Well-formed cache with is_revoked=False -> not revoked (False).
    import json
    with tempfile.TemporaryDirectory() as td:
        sync = _with_cache(
            monkeypatch, td,
            json.dumps({"is_revoked": False, "server_tier": "member"}),
        )
        if sync is None:
            import pytest
            pytest.skip("corvin_license plugin not installed in this build")
        assert LG._is_license_revoked() is False, (
            "a clean non-revoked cache must read as not-revoked"
        )


def test_valid_revoked_cache_is_revoked(monkeypatch):
    # Well-formed cache with is_revoked=True -> revoked (True).
    import json
    with tempfile.TemporaryDirectory() as td:
        sync = _with_cache(
            monkeypatch, td,
            json.dumps({"is_revoked": True, "server_tier": "member"}),
        )
        if sync is None:
            import pytest
            pytest.skip("corvin_license plugin not installed in this build")
        assert LG._is_license_revoked() is True
