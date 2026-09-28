"""A2A (App-to-App) adversarial tests against the PRODUCTION A2A modules.

Every test drives a module the live L38 path actually imports
(``corvin_operator/bridges/shared/``):

- ``a2a_friendship`` — the ADR-0070 pairing token (``corvin-a2a:ft1:``);
  HMAC tampering and expiry must be refused.
- ``a2a_nonce_store`` — replay detection, per-origin keying, expiry, and
  refusal of a nonce without an authenticated origin.
- ``a2a_audit`` — the fail-closed allow-list: tenant_id is required.
- ``a2a_attachments`` — the total-size cap that stops a large-payload DoS.

History (adversarial review, 2026-09-28): this file used to import
``a2a_token`` (``core/bridges/shared/a2a_token.py``), a codec with no
production caller, via a sys.path that did not contain it — so the token
tests could not even import. The nonce tests pre-dated the 2026-09-25 rule
that a nonce is keyed by its authenticated origin and refused without one.
Two placeholder tests (a "pairing state machine" that assigned and asserted
a local string, and a "concurrent pairing" test that only printed a comment
about a ``_pair_lock`` that no longer exists) asserted nothing and were
removed; the tenant_id check swallowed its own ``AssertionError`` in a bare
``except Exception`` and could never fail.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parent.parent / "corvin_operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # a2a_friendship may resolve a binding key under CORVIN_HOME.
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))


class TestFriendshipTokenSecurity:
    """ADR-0070 friendship token: signature and expiry are enforced."""

    def test_tampered_payload_is_rejected(self):
        import a2a_friendship as ft

        _tok, token_str = ft.create_friendship_token(url="http://10.0.0.1:8765", label="issuer")
        # Sanity: the untampered token verifies.
        assert ft.parse_and_verify(token_str).url == "http://10.0.0.1:8765"

        rest = token_str[len(ft.TOKEN_PREFIX):]
        payload_b64, sig_b64 = rest.rsplit(".", 1)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        payload["url"] = "http://attacker.example:8765"
        forged_b64 = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        forged = f"{ft.TOKEN_PREFIX}{forged_b64}.{sig_b64}"

        with pytest.raises(ft.FriendshipError, match="HMAC"):
            ft.parse_and_verify(forged)

    def test_expired_token_is_rejected(self):
        import a2a_friendship as ft

        # Expired one hour ago — well past the 30 s clock-skew tolerance.
        _tok, token_str = ft.create_friendship_token(url=None, ttl_seconds=-3600)
        with pytest.raises(ft.FriendshipError, match="expired"):
            ft.parse_and_verify(token_str)


class TestA2ANonceStore:
    """Replay detection in the in-memory nonce store (same rules as SQLite)."""

    def test_nonce_replay_detection(self):
        from a2a_nonce_store import REPLAY, _InMemoryNonceStore

        store = _InMemoryNonceStore(ttl_s=700)
        assert store.check_and_add("abc123def456", origin_id="peer-a") is True
        assert store.check_and_add_ex("abc123def456", origin_id="peer-a") == REPLAY
        # Same nonce from a DIFFERENT authenticated origin is not a replay.
        assert store.check_and_add("abc123def456", origin_id="peer-b") is True

    def test_nonce_without_origin_is_refused(self):
        from a2a_nonce_store import INVALID, _InMemoryNonceStore

        store = _InMemoryNonceStore(ttl_s=700)
        assert store.check_and_add_ex("n1", origin_id="") == INVALID
        assert store.check_and_add_ex("n1", origin_id="   ") == INVALID

    def test_nonce_expiry(self):
        from a2a_nonce_store import _InMemoryNonceStore

        store = _InMemoryNonceStore(ttl_s=0.2)
        assert store.check_and_add("expires_test_123", origin_id="peer-a") is True
        assert store.check_and_add("expires_test_123", origin_id="peer-a") is False
        time.sleep(0.3)
        assert store.check_and_add("expires_test_123", origin_id="peer-a") is True


class TestA2ATenantIsolation:
    """a2a audit allow-list is fail-closed on a missing tenant_id."""

    def test_event_with_tenant_id_is_accepted(self):
        from a2a_audit import _check_allow_list

        _check_allow_list("a2a.genesis_block_created", {
            "tenant_id": "tenant_a",
            "instance_id": "inst_1",
            "network_id": "net_1",
            "nonce_prefix": "abc12345",
            "epoch": 1,
        })

    def test_event_without_tenant_id_is_rejected(self):
        from a2a_audit import AuditFieldNotAllowed, _check_allow_list

        with pytest.raises(AuditFieldNotAllowed, match="tenant_id"):
            _check_allow_list("a2a.genesis_block_created", {"instance_id": "inst_1"})

    def test_unknown_field_is_rejected(self):
        from a2a_audit import AuditFieldNotAllowed, _check_allow_list

        with pytest.raises(AuditFieldNotAllowed, match="forbidden"):
            _check_allow_list("a2a.genesis_block_created", {
                "tenant_id": "tenant_a", "prompt": "free text must never reach audit",
            })


class TestA2AAttackVectors:
    def test_oversized_attachments_are_rejected(self):
        from a2a_attachments import (
            MAX_ATTACHMENTS_TOTAL_BYTES,
            Attachment,
            AttachmentError,
            validate_attachments,
        )

        big = Attachment.from_bytes(
            name="big.bin", mime="application/octet-stream",
            content=b"\0" * (MAX_ATTACHMENTS_TOTAL_BYTES + 1),
        )
        with pytest.raises(AttachmentError, match="attachments_total_too_large"):
            validate_attachments([big.to_dict()])

        ok = Attachment.from_bytes(name="small.txt", mime="text/plain", content=b"hi")
        assert [a.name for a in validate_attachments([ok.to_dict()])] == ["small.txt"]
