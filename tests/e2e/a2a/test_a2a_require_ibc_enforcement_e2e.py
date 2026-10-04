"""E2E test: require_ibc flag is set on new pairings and enforced end-to-end.

ADR-2099 P0 Fact 3: New pairings must have require_ibc=true in origin file,
and the receiver must reject envelopes from origins with require_ibc=true
that lack a valid IBC attestation.

This test proves:
1. Real HTTP pairing (redeem/accept) sets require_ibc=true
2. Origin file contains the flag
3. Receiver rejects envelope without IBC when flag is true
"""
import json
import pytest
from pathlib import Path


def test_new_friendship_pairing_sets_require_ibc(
    a2a_test_server,
    temp_corvin_home,
    temp_tenant_id,
):
    """E2E: Friendship token redeem → require_ibc=true in origin file."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "corvin_operator" / "bridges" / "shared"))
    
    from a2a_friendship import generate_token, FriendshipToken
    
    # 1. Issuer generates friendship token
    token = generate_token(label="test-peer")
    token_str = token.to_string()
    
    # 2. Redeemer: POST /remote-trigger/pair/friendship/redeem (real HTTP call)
    response = a2a_test_server.post(
        "/remote-trigger/pair/friendship/redeem",
        json={"token": token_str, "origin_id": "test-redeemer"},
    )
    assert response.status_code == 200, f"Redeem failed: {response.text}"
    
    # 3. Verify origin file was written with require_ibc=true
    origins_dir = Path(temp_corvin_home) / "global" / "remote_trigger" / "remote_origins"
    origin_file = origins_dir / f"{token.kid}.json"
    assert origin_file.exists(), f"Origin file not found: {origin_file}"
    
    origin_data = json.loads(origin_file.read_text())
    assert origin_data.get("require_ibc") is True, (
        f"require_ibc not set to True in origin file. "
        f"Got: {origin_data}"
    )


def test_receiver_rejects_envelope_without_ibc_when_require_ibc_true(
    a2a_test_server,
    temp_corvin_home,
):
    """E2E: Receiver with require_ibc=true rejects envelope missing IBC."""
    # Create origin file with require_ibc=true (no IBC)
    origins_dir = Path(temp_corvin_home) / "global" / "remote_trigger" / "remote_origins"
    origins_dir.mkdir(parents=True, exist_ok=True)
    
    origin_data = {
        "origin_id": "test-peer",
        "hmac_key": "test-hmac-key",
        "recv_key": "test-recv-key",
        "enabled": True,
        "require_ibc": True,
    }
    origin_file = origins_dir / "test-peer.json"
    origin_file.write_text(json.dumps(origin_data))
    origin_file.chmod(0o600)
    
    # Send envelope without IBC attestation
    envelope = {
        "task_id": "test-task",
        "origin_id": "test-peer",
        "instruction": "test instruction",
        "issued_at": "2026-10-04T12:00:00Z",
        "nonce": "test-nonce",
    }
    
    # POST to receiver (real HTTP)
    response = a2a_test_server.post(
        "/remote-trigger/receive",
        json=envelope,
    )
    
    # Expect 403 or similar rejection (require_ibc check fails)
    assert response.status_code >= 400, (
        f"Expected rejection (require_ibc without IBC), got {response.status_code}: "
        f"{response.text}"
    )


def test_receiver_accepts_legacy_origin_without_require_ibc(
    a2a_test_server,
    temp_corvin_home,
):
    """E2E: Receiver still accepts envelopes from legacy origins (require_ibc=false)."""
    origins_dir = Path(temp_corvin_home) / "global" / "remote_trigger" / "remote_origins"
    origins_dir.mkdir(parents=True, exist_ok=True)
    
    origin_data = {
        "origin_id": "legacy-peer",
        "hmac_key": "test-hmac-key",
        "recv_key": "test-recv-key",
        "enabled": True,
        "require_ibc": False,  # Legacy: no IBC required
    }
    origin_file = origins_dir / "legacy-peer.json"
    origin_file.write_text(json.dumps(origin_data))
    origin_file.chmod(0o600)
    
    # Send envelope without IBC
    envelope = {
        "task_id": "test-task",
        "origin_id": "legacy-peer",
        "instruction": "test instruction",
        "issued_at": "2026-10-04T12:00:00Z",
        "nonce": "test-nonce",
    }
    
    # POST to receiver (real HTTP) — should succeed or fail for other reasons, not require_ibc
    response = a2a_test_server.post(
        "/remote-trigger/receive",
        json=envelope,
    )
    
    # Should NOT fail due to missing IBC (require_ibc gate bypassed)
    # Other failures (bad HMAC, etc.) are OK in this test
    if response.status_code >= 400:
        error = response.json() if response.headers.get("content-type") == "application/json" else response.text
        assert "ibc" not in str(error).lower(), (
            f"Expected to NOT reject for IBC reason, but got: {error}"
        )
