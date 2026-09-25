"""
E2E (integration) test for Discovery Relay.

Tests the full FastAPI endpoint stack with real HTTP, encryption, and HMAC.
Verifies that a client can:
1. Register an instance with encrypted payload + HMAC auth
2. Query the catalog
3. Send heartbeat
4. Observe stale retirement and cleanup
"""

import pytest
import os
import json
import asyncio
from datetime import datetime, timezone
from httpx import AsyncClient

from corvin_operator.discovery_relay.relay import create_relay_app, DiscoveryRelay
from corvin_operator.discovery_relay.security import (
    compute_hmac, encrypt_payload, decrypt_payload
)


@pytest.fixture
async def relay_app_fixture():
    """Create a relay app with a test org configured."""
    app, relay = create_relay_app()

    # Configure test org
    test_org_key = os.urandom(32)
    relay.set_org_key("test_org", test_org_key)

    # Don't start housekeeping for this test (we'll test it separately)
    yield app, relay, test_org_key

    # Cleanup
    await relay.stop_housekeeping()


@pytest.mark.asyncio
async def test_register_instance(relay_app_fixture):
    """Test instance registration with encryption and HMAC."""
    app, relay, org_key = relay_app_fixture

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Prepare payload
        instance_id = "test_app_001"
        endpoint = "https://test-app.example.com:443"

        # Encrypt tier + kid
        tier_payload = {"tier": "pro", "kid": "kid_v1"}
        tier_enc, tier_nonce = encrypt_payload(tier_payload, org_key)

        # Prepare registration request
        register_req = {
            "instance_id": instance_id,
            "endpoint": endpoint,
            "tier_enc": tier_enc,
            "tier_enc_nonce": tier_nonce,
            "kid": "kid_v1",
            "latency_ms": 42,
        }

        # Compute HMAC for auth
        payload_for_sig = b'{"instance_id":"' + instance_id.encode() + b'"}'
        auth_sig = compute_hmac(payload_for_sig, org_key)

        # POST /discovery/{org_id}/register
        response = await client.post(
            "/discovery/test_org/register",
            json=register_req,
            headers={"Authorization": f"Bearer {auth_sig}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "registered"
        assert data["instance_id"] == instance_id


@pytest.mark.asyncio
async def test_query_catalog(relay_app_fixture):
    """Test querying the catalog."""
    app, relay, org_key = relay_app_fixture

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Register an instance first
        instance_id = "test_app_001"
        endpoint = "https://test-app.example.com:443"
        tier_payload = {"tier": "pro", "kid": "kid_v1"}
        tier_enc, tier_nonce = encrypt_payload(tier_payload, org_key)

        register_req = {
            "instance_id": instance_id,
            "endpoint": endpoint,
            "tier_enc": tier_enc,
            "tier_enc_nonce": tier_nonce,
            "kid": "kid_v1",
            "latency_ms": 42,
        }

        payload_for_sig = b'{"instance_id":"' + instance_id.encode() + b'"}'
        auth_sig = compute_hmac(payload_for_sig, org_key)

        await client.post(
            "/discovery/test_org/register",
            json=register_req,
            headers={"Authorization": f"Bearer {auth_sig}"},
        )

        # Query catalog
        response = await client.get("/discovery/test_org/catalog")
        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == "test_org"
        assert data["total"] == 1
        assert len(data["instances"]) == 1
        assert data["instances"][0]["instance_id"] == instance_id
        assert data["instances"][0]["endpoint"] == endpoint
        assert data["instances"][0]["state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_heartbeat(relay_app_fixture):
    """Test instance heartbeat."""
    app, relay, org_key = relay_app_fixture

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Register an instance
        instance_id = "test_app_001"
        tier_payload = {"tier": "pro", "kid": "kid_v1"}
        tier_enc, tier_nonce = encrypt_payload(tier_payload, org_key)

        register_req = {
            "instance_id": instance_id,
            "endpoint": "https://test-app.example.com:443",
            "tier_enc": tier_enc,
            "tier_enc_nonce": tier_nonce,
            "kid": "kid_v1",
            "latency_ms": 42,
        }

        payload_for_sig = b'{"instance_id":"' + instance_id.encode() + b'"}'
        auth_sig = compute_hmac(payload_for_sig, org_key)

        await client.post(
            "/discovery/test_org/register",
            json=register_req,
            headers={"Authorization": f"Bearer {auth_sig}"},
        )

        # Get current heartbeat time
        catalog = await client.get("/discovery/test_org/catalog")
        old_time = catalog.json()["instances"][0]["last_heartbeat"]

        # Send heartbeat
        await asyncio.sleep(0.1)  # Ensure time passes
        hb_req = {"instance_id": instance_id, "latency_ms": 50}
        response = await client.post(
            "/discovery/test_org/heartbeat",
            json=hb_req,
            headers={"Authorization": f"Bearer {auth_sig}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "heartbeat_received"

        # Verify heartbeat was updated
        catalog = await client.get("/discovery/test_org/catalog")
        new_time = catalog.json()["instances"][0]["last_heartbeat"]
        assert new_time > old_time


@pytest.mark.asyncio
async def test_invalid_hmac_rejected(relay_app_fixture):
    """Test that invalid HMAC signature is rejected."""
    app, relay, org_key = relay_app_fixture

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Try to register with invalid HMAC
        instance_id = "test_app_001"
        tier_payload = {"tier": "pro", "kid": "kid_v1"}
        tier_enc, tier_nonce = encrypt_payload(tier_payload, org_key)

        register_req = {
            "instance_id": instance_id,
            "endpoint": "https://test-app.example.com:443",
            "tier_enc": tier_enc,
            "tier_enc_nonce": tier_nonce,
            "kid": "kid_v1",
            "latency_ms": 42,
        }

        # Use wrong signature
        wrong_sig = "invalid_signature_here"

        response = await client.post(
            "/discovery/test_org/register",
            json=register_req,
            headers={"Authorization": f"Bearer {wrong_sig}"},
        )

        assert response.status_code == 401
        assert "Invalid signature" in response.json()["detail"]


@pytest.mark.asyncio
async def test_decryption_failure_rejected(relay_app_fixture):
    """Test that decryption failure is rejected."""
    app, relay, org_key = relay_app_fixture

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Try to register with corrupted encryption
        instance_id = "test_app_001"

        register_req = {
            "instance_id": instance_id,
            "endpoint": "https://test-app.example.com:443",
            "tier_enc": "corrupted_ciphertext",
            "tier_enc_nonce": "corrupted_nonce",
            "kid": "kid_v1",
            "latency_ms": 42,
        }

        payload_for_sig = b'{"instance_id":"' + instance_id.encode() + b'"}'
        auth_sig = compute_hmac(payload_for_sig, org_key)

        response = await client.post(
            "/discovery/test_org/register",
            json=register_req,
            headers={"Authorization": f"Bearer {auth_sig}"},
        )

        assert response.status_code == 400
        assert "Decryption failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unknown_org_rejected(relay_app_fixture):
    """Test that unknown org is rejected."""
    app, relay, org_key = relay_app_fixture

    async with AsyncClient(app=app, base_url="http://test") as client:
        register_req = {
            "instance_id": "test_app_001",
            "endpoint": "https://test-app.example.com:443",
            "tier_enc": "enc",
            "tier_enc_nonce": "nonce",
            "kid": "kid_v1",
            "latency_ms": 42,
        }

        # Try unknown org
        response = await client.post(
            "/discovery/unknown_org/register",
            json=register_req,
            headers={"Authorization": "Bearer sig"},
        )

        assert response.status_code == 403


@pytest.mark.asyncio
async def test_health_check(relay_app_fixture):
    """Test health check endpoint."""
    app, relay, org_key = relay_app_fixture

    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "active_instances" in data
        assert "retired_instances" in data
        assert "orgs_count" in data


@pytest.mark.asyncio
async def test_multiple_instances_per_org(relay_app_fixture):
    """Test multiple instances registered under one org."""
    app, relay, org_key = relay_app_fixture

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Register 3 instances
        for i in range(3):
            instance_id = f"test_app_{i:03d}"
            tier_payload = {"tier": "pro", "kid": "kid_v1"}
            tier_enc, tier_nonce = encrypt_payload(tier_payload, org_key)

            register_req = {
                "instance_id": instance_id,
                "endpoint": f"https://app{i}.example.com:443",
                "tier_enc": tier_enc,
                "tier_enc_nonce": tier_nonce,
                "kid": "kid_v1",
                "latency_ms": 10 * i,  # Different latencies
            }

            payload_for_sig = b'{"instance_id":"' + instance_id.encode() + b'"}'
            auth_sig = compute_hmac(payload_for_sig, org_key)

            response = await client.post(
                "/discovery/test_org/register",
                json=register_req,
                headers={"Authorization": f"Bearer {auth_sig}"},
            )
            assert response.status_code == 200

        # Query and verify all 3 are present and sorted by latency
        response = await client.get("/discovery/test_org/catalog")
        data = response.json()
        assert data["total"] == 3
        latencies = [inst["latency_ms"] for inst in data["instances"]]
        assert latencies == [0, 10, 20]  # Sorted ascending


@pytest.mark.asyncio
async def test_multiple_orgs_isolation(relay_app_fixture):
    """Test that different orgs are isolated in the catalog."""
    app, relay, org_key = relay_app_fixture

    # Configure second org
    org2_key = os.urandom(32)
    relay.set_org_key("org2", org2_key)

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Register instance in org1
        tier_payload = {"tier": "pro", "kid": "kid_v1"}
        tier_enc, tier_nonce = encrypt_payload(tier_payload, org_key)

        register_req = {
            "instance_id": "app_org1",
            "endpoint": "https://app1.example.com:443",
            "tier_enc": tier_enc,
            "tier_enc_nonce": tier_nonce,
            "kid": "kid_v1",
            "latency_ms": 42,
        }

        payload_for_sig = b'{"instance_id":"app_org1"}'
        auth_sig = compute_hmac(payload_for_sig, org_key)

        await client.post(
            "/discovery/test_org/register",
            json=register_req,
            headers={"Authorization": f"Bearer {auth_sig}"},
        )

        # Register instance in org2
        tier_enc2, tier_nonce2 = encrypt_payload(tier_payload, org2_key)
        register_req2 = {
            "instance_id": "app_org2",
            "endpoint": "https://app2.example.com:443",
            "tier_enc": tier_enc2,
            "tier_enc_nonce": tier_nonce2,
            "kid": "kid_v1",
            "latency_ms": 42,
        }

        payload_for_sig2 = b'{"instance_id":"app_org2"}'
        auth_sig2 = compute_hmac(payload_for_sig2, org2_key)

        await client.post(
            "/discovery/org2/register",
            json=register_req2,
            headers={"Authorization": f"Bearer {auth_sig2}"},
        )

        # Query org1 should only see org1's instance
        response1 = await client.get("/discovery/test_org/catalog")
        data1 = response1.json()
        assert data1["total"] == 1
        assert data1["instances"][0]["instance_id"] == "app_org1"

        # Query org2 should only see org2's instance
        response2 = await client.get("/discovery/org2/catalog")
        data2 = response2.json()
        assert data2["total"] == 1
        assert data2["instances"][0]["instance_id"] == "app_org2"
