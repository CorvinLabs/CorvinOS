"""Unit tests for keyring and CRL modules.

ADR-0703 §2.2–2.3: Ring embedding and CRL delta merge tests.
"""
from __future__ import annotations

import pytest
import tempfile
import json
import time
from pathlib import Path

from core.operator.license.keyring import RING, RingKey, Ring
from core.operator.license.crl import CRLPage, CRLState, load_crl_state, merge_crl_delta, is_revoked, crl_age_seconds


class TestRingEmbedding:
    """RING constant is properly embedded."""

    def test_ring_has_all_phase0_keys(self):
        """Ring contains root-v1, lic-v2, ibc-v2, mkt-v1."""
        required_kids = {"root-v1", "lic-v2", "ibc-v2", "mkt-v1"}
        assert required_kids.issubset(RING.kids.keys()), \
            f"Ring missing keys: {required_kids - set(RING.kids.keys())}"

    def test_ring_keys_are_frozen(self):
        """Ring and RingKey are frozen dataclasses."""
        with pytest.raises(AttributeError):
            RING.kids["root-v1"].public_key = "modified"

    def test_ring_generation_is_positive(self):
        """Ring generation is monotonic."""
        assert RING.generation >= 0

    def test_ring_revoked_kids_is_frozenset(self):
        """Revoked KIDs cannot be modified after construction."""
        assert isinstance(RING.revoked_kids, frozenset)
        with pytest.raises(AttributeError):
            RING.revoked_kids.add("test")

    def test_ring_keys_have_public_key_pem(self):
        """All keys have non-empty PEM-encoded public keys."""
        for kid, key in RING.kids.items():
            assert key.public_key.startswith("-----BEGIN PUBLIC KEY-----")
            assert key.public_key.endswith("-----END PUBLIC KEY-----")


class TestCRLDeltaMerge:
    """CRL delta merge with monotonic enforcement."""

    def test_merge_empty_delta(self):
        """Merging an empty delta preserves the state."""
        state = CRLState(pages={}, last_fetched_at=None)
        delta = {"pages": []}
        result = merge_crl_delta(state, delta)
        assert result.pages == {}

    def test_merge_single_page(self):
        """Merge a single CRL page."""
        state = CRLState(pages={}, last_fetched_at=None)
        now = int(time.time())
        delta = {
            "pages": [
                {
                    "issued_at": now,
                    "revoked_serials": ["serial-1", "serial-2"],
                    "serial": "root-sig-1"
                }
            ]
        }
        result = merge_crl_delta(state, delta)
        assert now in result.pages
        assert result.pages[now].revoked_serials == frozenset(["serial-1", "serial-2"])

    def test_merge_enforces_monotonic_issued_at(self):
        """Merging with non-monotonic issued_at is rejected (fail-closed)."""
        now = int(time.time())
        state = CRLState(
            pages={now: CRLPage(issued_at=now, revoked_serials=frozenset(), serial=None)},
            last_fetched_at=None
        )

        # Try to merge a page with an older issued_at (violates monotonicity)
        delta = {
            "pages": [
                {
                    "issued_at": now - 1000,
                    "revoked_serials": ["serial-3"],
                    "serial": "root-sig-2"
                }
            ]
        }
        result = merge_crl_delta(state, delta)
        # Should reject and return unchanged state
        assert result.pages == state.pages

    def test_merge_multiple_pages_in_order(self):
        """Merging multiple pages in increasing order succeeds."""
        now = int(time.time())
        state = CRLState(pages={}, last_fetched_at=None)

        delta = {
            "pages": [
                {
                    "issued_at": now,
                    "revoked_serials": ["serial-1"],
                    "serial": "sig-1"
                },
                {
                    "issued_at": now + 1000,
                    "revoked_serials": ["serial-2"],
                    "serial": "sig-2"
                }
            ]
        }
        result = merge_crl_delta(state, delta)
        assert len(result.pages) == 2
        assert now in result.pages
        assert now + 1000 in result.pages

    def test_merge_updates_last_fetched_at(self):
        """After merge, last_fetched_at is updated to now."""
        state = CRLState(pages={}, last_fetched_at=None)
        now_before = int(time.time())
        delta = {
            "pages": [
                {
                    "issued_at": now_before,
                    "revoked_serials": [],
                    "serial": None
                }
            ]
        }
        result = merge_crl_delta(state, delta)
        now_after = int(time.time())
        assert now_before <= result.last_fetched_at <= now_after


class TestIsRevoked:
    """Serial revocation checking."""

    def test_empty_state_no_revocations(self):
        """Empty CRL state has no revocations."""
        state = CRLState(pages={}, last_fetched_at=None)
        assert not is_revoked("any-serial", state)

    def test_revoked_serial_found(self):
        """Revoked serial is found across all pages."""
        now = int(time.time())
        page1 = CRLPage(issued_at=now, revoked_serials=frozenset(["serial-1"]), serial=None)
        page2 = CRLPage(issued_at=now + 1000, revoked_serials=frozenset(["serial-2"]), serial=None)

        state = CRLState(pages={now: page1, now + 1000: page2}, last_fetched_at=now)

        assert is_revoked("serial-1", state)
        assert is_revoked("serial-2", state)
        assert not is_revoked("serial-3", state)


class TestCRLAge:
    """CRL age computation."""

    def test_crl_age_empty_state(self):
        """Empty CRL state has no age."""
        state = CRLState(pages={}, last_fetched_at=None)
        assert crl_age_seconds(state) is None

    def test_crl_age_seconds_computation(self):
        """CRL age is computed as now - newest page issued_at."""
        now = int(time.time())
        page = CRLPage(issued_at=now - 3600, revoked_serials=frozenset(), serial=None)
        state = CRLState(pages={now - 3600: page}, last_fetched_at=now)

        age = crl_age_seconds(state)
        # Allow 1-2 second drift due to test execution time
        assert 3598 <= age <= 3602

    def test_crl_age_uses_newest_page(self):
        """CRL age uses the newest (max) issued_at."""
        now = int(time.time())
        page1 = CRLPage(issued_at=now - 7200, revoked_serials=frozenset(), serial=None)
        page2 = CRLPage(issued_at=now - 3600, revoked_serials=frozenset(), serial=None)

        state = CRLState(pages={now - 7200: page1, now - 3600: page2}, last_fetched_at=now)
        age = crl_age_seconds(state)

        # Should be ~3600s, not ~7200s
        assert 3598 <= age <= 3602


class TestLoadCRLState:
    """Load CRL state from disk."""

    def test_load_nonexistent_file(self):
        """Loading nonexistent file returns empty state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            crl_path = Path(tmpdir) / "crl.json"
            state = load_crl_state(crl_path)
            assert state.pages == {}
            assert state.last_fetched_at is None

    def test_load_valid_crl_file(self):
        """Loading a valid CRL file restores state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            crl_path = Path(tmpdir) / "crl.json"
            now = int(time.time())

            crl_data = {
                "pages": {
                    str(now): {
                        "issued_at": now,
                        "revoked_serials": ["serial-1"],
                        "serial": "root-sig-1"
                    }
                },
                "last_fetched_at": now
            }
            crl_path.write_text(json.dumps(crl_data), encoding="utf-8")

            state = load_crl_state(crl_path)
            assert now in state.pages
            assert state.pages[now].revoked_serials == frozenset(["serial-1"])
            assert state.last_fetched_at == now

    def test_load_corrupt_file_returns_empty(self):
        """Loading corrupt JSON returns empty state (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            crl_path = Path(tmpdir) / "crl.json"
            crl_path.write_text("{invalid json}", encoding="utf-8")

            state = load_crl_state(crl_path)
            assert state.pages == {}
