"""E2E test suite for the Instance Agent (ADR-0047 M1 + M2).

Tests the full BYOK pipeline without any Management API or real vault:
  1. Keypair generation + persistence
  2. RSA-OAEP encryption (simulates browser)
  3. POST /secrets/<key_name> → decryption + vault write
  4. Config-push event dispatch
  5. Key-name validation rules

Run:
    pytest operator/agent/tests/test_agent_e2e.py -v
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make operator.agent importable.
_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


# ── helpers ──────────────────────────────────────────────────────────────

def _encrypt_for_agent(plaintext: str, pub_pem: bytes) -> str:
    """Simulate browser RSA-OAEP-SHA256 encryption using cryptography lib."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_pem_public_key(pub_pem)
    ciphertext = public_key.encrypt(
        plaintext.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("ascii")


# ── M1: Keypair ───────────────────────────────────────────────────────────

class TestKeypair:
    def test_generate_creates_files(self, tmp_path):
        from agent.keypair import generate_or_load_keypair
        priv, pub = generate_or_load_keypair(tmp_path)
        assert (tmp_path / "byok_privkey.pem").exists()
        assert (tmp_path / "byok_pubkey.pem").exists()
        assert priv.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert pub.startswith(b"-----BEGIN PUBLIC KEY-----")

    def test_idempotent_on_second_call(self, tmp_path):
        from agent.keypair import generate_or_load_keypair
        priv1, pub1 = generate_or_load_keypair(tmp_path)
        priv2, pub2 = generate_or_load_keypair(tmp_path)
        assert priv1 == priv2
        assert pub1 == pub2

    def test_private_key_mode_0600(self, tmp_path):
        from agent.keypair import generate_or_load_keypair
        generate_or_load_keypair(tmp_path)
        st = (tmp_path / "byok_privkey.pem").stat()
        import stat
        assert stat.S_IMODE(st.st_mode) == 0o600

    def test_get_public_key_pem(self, tmp_path):
        from agent.keypair import get_public_key_pem
        pub = get_public_key_pem(tmp_path)
        assert b"PUBLIC KEY" in pub

    def test_decrypt_oaep_roundtrip(self, tmp_path):
        from agent.keypair import generate_or_load_keypair, decrypt_oaep
        _, pub_pem = generate_or_load_keypair(tmp_path)
        plaintext = "sk-ant-test-1234567890abcdef"
        ciphertext_b64 = _encrypt_for_agent(plaintext, pub_pem)
        result = decrypt_oaep(ciphertext_b64, tmp_path)
        assert result == plaintext

    def test_decrypt_wrong_ciphertext_raises(self, tmp_path):
        from agent.keypair import generate_or_load_keypair, decrypt_oaep
        generate_or_load_keypair(tmp_path)
        with pytest.raises(ValueError, match="decryption failed|base64"):
            decrypt_oaep("not-valid-base64!!!", tmp_path)


# ── M1: Key-name validation ───────────────────────────────────────────────

class TestKeyNameValidation:
    @pytest.mark.parametrize("name", [
        "anthropic_api_key",
        "openai_api_key",
        "stt_openai_api_key",
        "stt_local_whisper_api_key",
        "custom_my-tool",
        "custom_test123",
        "custom_abc-xyz",
    ])
    def test_valid_names(self, name):
        from agent.byok import validate_key_name
        validate_key_name(name)  # must not raise

    @pytest.mark.parametrize("name", [
        "audit_key",
        "my_vault_token",
        "path_gate_bypass",
        "policy_override",
        "license_key",
        "custom_audit_thing",
        "",
        "ANTHROPIC_API_KEY",  # uppercase not in known set
        "custom_" + "x" * 33,  # slug too long
        "unknown_key_not_in_list",
    ])
    def test_invalid_names(self, name):
        from agent.byok import validate_key_name
        with pytest.raises(ValueError):
            validate_key_name(name)


# ── M2: Full BYOK pipeline ────────────────────────────────────────────────

class TestBYOKPipeline:
    def test_apply_byok_secret_roundtrip(self, tmp_path):
        """Encrypt → POST /secrets → verify vault file written."""
        from agent.keypair import generate_or_load_keypair
        from agent.byok import apply_byok_secret

        agent_dir = tmp_path / "agent"
        vault_dir = tmp_path / "vaultroot"

        _, pub_pem = generate_or_load_keypair(agent_dir)
        plaintext = "sk-ant-api03-abcdef1234567890"
        ciphertext_b64 = _encrypt_for_agent(plaintext, pub_pem)

        result = apply_byok_secret(
            "anthropic_api_key",
            ciphertext_b64,
            agent_dir=agent_dir,
            vault_dir=vault_dir,
        )

        assert result["key_name"] == "anthropic_api_key"
        assert result["last4"] == plaintext[-4:]
        assert result["rotated_at"] > 0

        # Verify vault file was written.
        vault_file = vault_dir / "vault" / "anthropic_api_key.json"
        assert vault_file.exists(), f"Expected vault file at {vault_file}"
        stored = json.loads(vault_file.read_text())
        assert stored["value"] == plaintext

    def test_apply_byok_custom_key(self, tmp_path):
        from agent.keypair import generate_or_load_keypair
        from agent.byok import apply_byok_secret

        agent_dir = tmp_path / "agent"
        vault_dir = tmp_path / "vaultroot"
        _, pub_pem = generate_or_load_keypair(agent_dir)
        ct = _encrypt_for_agent("my-custom-secret-value", pub_pem)
        result = apply_byok_secret("custom_mytool", ct, agent_dir=agent_dir, vault_dir=vault_dir)
        assert result["ok"] if "ok" in result else True
        assert result["key_name"] == "custom_mytool"

    def test_invalid_ciphertext_raises_value_error(self, tmp_path):
        from agent.keypair import generate_or_load_keypair
        from agent.byok import apply_byok_secret

        agent_dir = tmp_path / "agent"
        generate_or_load_keypair(agent_dir)
        with pytest.raises(ValueError):
            apply_byok_secret("anthropic_api_key", "BAD_CIPHERTEXT===", agent_dir=agent_dir)

    def test_forbidden_key_name_raises(self, tmp_path):
        from agent.byok import apply_byok_secret
        with pytest.raises(ValueError, match="reserved substring"):
            apply_byok_secret("audit_bypass", "AAAA==", agent_dir=tmp_path)


# ── WA-21: key-shape validation ────────────────────────────────────────────
#
# Nothing in the pipeline ever checked that a decrypted value looks like the
# provider's real key format — autoComplete="off" is well known to be
# ignored by Chromium/most password managers on type="password" inputs, so
# these "paste a new key" fields can get silently autofilled with an
# unrelated saved credential; without this check it would be accepted and
# stored as though it were a working key, with no feedback at all.

class TestKeyShapeValidation:
    @pytest.mark.parametrize("key_name,plaintext", [
        ("anthropic_api_key", "atlr 0e1feae0e6945d06b988c9902267f1d1"),
        ("openai_api_key", "atlr 0e1feae0e6945d06b988c9902267f1d1"),
        ("stt_openai_api_key", "not-a-real-key-at-all"),
    ])
    def test_wrong_shape_rejected(self, tmp_path, key_name, plaintext):
        from agent.keypair import generate_or_load_keypair
        from agent.byok import apply_byok_secret

        agent_dir = tmp_path / "agent"
        vault_dir = tmp_path / "vaultroot"
        _, pub_pem = generate_or_load_keypair(agent_dir)
        ct = _encrypt_for_agent(plaintext, pub_pem)

        with pytest.raises(ValueError, match="does not look like a valid key"):
            apply_byok_secret(key_name, ct, agent_dir=agent_dir, vault_dir=vault_dir)

        # Rejected — must NOT have been written to the vault.
        assert not (vault_dir / "vault" / f"{key_name}.json").exists()

    @pytest.mark.parametrize("key_name,plaintext", [
        ("anthropic_api_key", "sk-ant-api03-abcdef1234567890"),
        ("openai_api_key", "sk-abcdef1234567890"),
        ("stt_openai_api_key", "sk-proj-abcdef1234567890"),
    ])
    def test_correct_shape_accepted(self, tmp_path, key_name, plaintext):
        from agent.keypair import generate_or_load_keypair
        from agent.byok import apply_byok_secret

        agent_dir = tmp_path / "agent"
        vault_dir = tmp_path / "vaultroot"
        _, pub_pem = generate_or_load_keypair(agent_dir)
        ct = _encrypt_for_agent(plaintext, pub_pem)

        result = apply_byok_secret(key_name, ct, agent_dir=agent_dir, vault_dir=vault_dir)
        assert result["key_name"] == key_name

    def test_custom_key_has_no_shape_restriction(self, tmp_path):
        """custom_<slug> keys are explicitly free-form (MCP tokens, third-party
        services, ...) and must never be shape-checked."""
        from agent.keypair import generate_or_load_keypair
        from agent.byok import apply_byok_secret

        agent_dir = tmp_path / "agent"
        vault_dir = tmp_path / "vaultroot"
        _, pub_pem = generate_or_load_keypair(agent_dir)
        ct = _encrypt_for_agent("literally anything", pub_pem)

        result = apply_byok_secret("custom_mytool", ct, agent_dir=agent_dir, vault_dir=vault_dir)
        assert result["key_name"] == "custom_mytool"

    def test_local_whisper_key_has_no_shape_restriction(self, tmp_path):
        """Reserved slot, documented as having no fixed format yet."""
        from agent.keypair import generate_or_load_keypair
        from agent.byok import apply_byok_secret

        agent_dir = tmp_path / "agent"
        vault_dir = tmp_path / "vaultroot"
        _, pub_pem = generate_or_load_keypair(agent_dir)
        ct = _encrypt_for_agent("whatever-format-this-turns-out-to-be", pub_pem)

        result = apply_byok_secret(
            "stt_local_whisper_api_key", ct, agent_dir=agent_dir, vault_dir=vault_dir,
        )
        assert result["key_name"] == "stt_local_whisper_api_key"


# ── M1: Config-push handler ──────────────────────────────────────────────

class TestConfigPush:
    def test_secret_push_event(self, tmp_path):
        from agent.keypair import generate_or_load_keypair
        from agent.config_push import handle

        agent_dir = tmp_path / "agent"
        vault_dir = tmp_path / "vaultroot"
        _, pub_pem = generate_or_load_keypair(agent_dir)
        ct = _encrypt_for_agent("sk-openai-test", pub_pem)

        result = handle(
            {"event": "secret.push", "key_name": "openai_api_key", "ciphertext": ct},
            agent_dir=agent_dir,
            vault_dir=vault_dir,
        )
        assert result["ok"] is True
        assert result["key_name"] == "openai_api_key"

    def test_config_reload_event(self, tmp_path):
        from agent.config_push import handle
        result = handle({"event": "config.reload"}, agent_dir=tmp_path)
        assert result["ok"] is True

    def test_keypair_rotate_event(self, tmp_path):
        from agent.keypair import generate_or_load_keypair
        from agent.config_push import handle

        agent_dir = tmp_path / "agent"
        priv1, pub1 = generate_or_load_keypair(agent_dir)

        result = handle({"event": "keypair.rotate"}, agent_dir=agent_dir)
        assert result["ok"] is True
        assert "pubkey_pem" in result

        priv2, pub2 = generate_or_load_keypair(agent_dir)
        assert priv1 != priv2  # new keypair generated

    def test_unknown_event_returns_error(self, tmp_path):
        from agent.config_push import handle
        result = handle({"event": "unknown.thing"}, agent_dir=tmp_path)
        assert result["ok"] is False
        assert result["error"] == "unknown_event"


# ── M1: FastAPI endpoints (httpx TestClient) ─────────────────────────────

class TestAgentHTTP:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "test-tenant")
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.delenv("CORVIN_AGENT_PROVISION_TOKEN", raising=False)

        import agent.main as agent_main

        agent_main._AGENT_DIR = tmp_path / "agent"
        agent_main._VAULT_DIR = tmp_path / "vaultroot"
        (tmp_path / "agent").mkdir(parents=True, exist_ok=True)

        from agent.keypair import generate_or_load_keypair
        generate_or_load_keypair(tmp_path / "agent")

        from httpx import AsyncClient, ASGITransport
        from agent.main import app
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.anyio
    async def test_health_endpoint(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "uptime_s" in data
        assert "keypair_ready" in data

    @pytest.mark.anyio
    async def test_pubkey_endpoint(self, client):
        resp = await client.get("/pubkey")
        assert resp.status_code == 200
        assert "BEGIN PUBLIC KEY" in resp.text

    @pytest.mark.anyio
    async def test_post_secret_endpoint(self, client, tmp_path):
        pub_pem = (tmp_path / "agent" / "byok_pubkey.pem").read_bytes()
        ct = _encrypt_for_agent("sk-ant-e2e-test-0123456789ab", pub_pem)

        import agent.main as agent_main
        agent_main._VAULT_DIR = tmp_path / "vaultroot"

        resp = await client.post("/secrets/anthropic_api_key", json={
            "ciphertext": ct,
            "algorithm": "RSA-OAEP-SHA256",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["key_name"] == "anthropic_api_key"
        assert len(data["last4"]) == 4

    @pytest.mark.anyio
    async def test_post_secret_bad_algorithm(self, client):
        resp = await client.post("/secrets/openai_api_key", json={
            "ciphertext": "AAAA==",
            "algorithm": "AES-256-GCM",
        })
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_metrics_endpoint(self, client):
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "corvin_agent_uptime_seconds" in resp.text
