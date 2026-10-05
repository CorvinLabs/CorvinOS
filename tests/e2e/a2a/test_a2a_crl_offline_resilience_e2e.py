"""E2E test: CRL receive path never blocks (cache-only, no 15s timeout).

ADR-2099 P0 Fact 4: ``peer_ibc_revoked`` reads the cache only during receive,
so offline hosts do not pay a 15s timeout on every envelope. The cache is
refreshed out-of-band by ``corvin-id maintain`` (corvin-ibc-maintain.timer).
"""
import json
import sys
import time
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared"


@pytest.fixture
def temp_corvin_home(monkeypatch, tmp_path):
    """A private CORVIN_HOME for the CRL cache. The tests referenced this
    fixture without it existing anywhere, so all three errored at setup and
    the cache-only receive guarantee (ADR-2099 P0 fact 4) was never run."""
    home = tmp_path / "corvin-home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    if str(_SHARED) not in sys.path:
        sys.path.insert(0, str(_SHARED))
    return home


def test_receive_crl_check_uses_cache_only_not_network(
    temp_corvin_home,
    monkeypatch,
):
    """E2E: receive() calls peer_ibc_revoked which reads cache only (no network)."""
    
    from instance_identity import peer_ibc_revoked, _crl_cache_path
    import instance_identity
    
    # 1. Populate cache with a revoked JTI
    cache_path = _crl_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    cache_data = {
        "fetched_at": now,
        "revoked_jti": ["revoked-jti-123", "revoked-jti-456"],
    }
    cache_path.write_text(json.dumps(cache_data))
    cache_path.chmod(0o600)
    
    # 2. Block network calls (simulate offline host)
    original_urlopen = None
    def blocked_urlopen(*args, **kw):
        raise OSError("Network unreachable — this should NOT be called on receive path")
    
    import urllib.request
    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = blocked_urlopen
    
    try:
        # 3. Call peer_ibc_revoked (critical receive path, no force_refresh)
        # Should NOT block, should NOT call network
        start = time.time()
        result = peer_ibc_revoked("revoked-jti-123", force_refresh=False)
        elapsed = time.time() - start
        
        # 4. Verify result and timing
        assert result is True, f"Expected revoked, got {result}"
        assert elapsed < 0.5, f"Expected <0.5s, got {elapsed}s (possible network timeout)"
        
    finally:
        urllib.request.urlopen = original_urlopen


def test_offline_grace_period_serves_stale_cache(
    temp_corvin_home,
):
    """E2E: CRL cache older than 24h but <7d is still served (offline tolerance)."""
    
    from instance_identity import fetch_revocation_list, _crl_cache_path
    import time
    
    # 1. Write cache from 3 days ago
    cache_path = _crl_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    three_days_ago = now - (3 * 24 * 3600)
    cache_data = {
        "fetched_at": three_days_ago,
        "revoked_jti": ["old-revoked-jti"],
    }
    cache_path.write_text(json.dumps(cache_data))
    cache_path.chmod(0o600)
    
    # 2. Block network (offline)
    import urllib.request
    original_urlopen = urllib.request.urlopen
    call_count = [0]
    
    def counting_blocked(*args, **kw):
        call_count[0] += 1
        raise OSError("Network unreachable")
    
    urllib.request.urlopen = counting_blocked
    
    try:
        # 3. Past the 24 h TTL the non-receive fetch (operator / maintain
        #    path) tries ONE refresh and, offline, serves the stale cache.
        #    The receive path never fetches at all — that is test 1
        #    (peer_ibc_revoked is cache-only).
        result = fetch_revocation_list(force_refresh=False)

        assert "old-revoked-jti" in result, f"Expected stale cache data, got {result}"
        assert call_count[0] == 1, (
            f"Expected exactly one refresh attempt past the TTL, got {call_count[0]}"
        )
    finally:
        urllib.request.urlopen = original_urlopen


def test_crl_cache_beyond_grace_period_returns_empty_safely(
    temp_corvin_home,
):
    """E2E: CRL cache older than 7d returns empty (fail-open, not stale revocation)."""
    
    from instance_identity import peer_ibc_revoked, _crl_cache_path
    import time
    
    # 1. Write cache from 8 days ago (beyond 7d grace)
    cache_path = _crl_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    eight_days_ago = now - (8 * 24 * 3600)
    cache_data = {
        "fetched_at": eight_days_ago,
        "revoked_jti": ["some-jti"],
    }
    cache_path.write_text(json.dumps(cache_data))
    cache_path.chmod(0o600)
    
    # 2. Check revocation without network (offline)
    result = peer_ibc_revoked("some-jti", force_refresh=False)
    
    # Should return False (fail-open: no cache → assume not revoked, never "revoked"
    # based on expired data)
    assert result is False, (
        f"Expected False for expired cache (fail-open), got {result}"
    )
