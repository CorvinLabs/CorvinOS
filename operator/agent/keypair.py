"""RSA-2048 keypair lifecycle for the Instance Agent.

The keypair is generated once at first boot and stored on disk under the
tenant's global agent directory with mode 0600 (private key) / 0644 (public).
The private key NEVER leaves the container.

Paths:
  <tenant_home>/global/agent/byok_privkey.pem  (mode 0600)
  <tenant_home>/global/agent/byok_pubkey.pem   (mode 0644)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding


def _agent_dir(tenant_id: str | None = None) -> Path:
    """Resolve the agent state directory for the current tenant."""
    here = Path(__file__).resolve()
    # Walk up to find repo root (contains .corvin_repo marker or operator/)
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "operator").is_dir():
            repo = parent
            break
    else:
        repo = Path.home()

    # Prefer forge's tenant resolver; fall back to simple env-var lookup.
    forge_path = repo / "operator" / "forge"
    if str(forge_path) not in sys.path:
        sys.path.insert(0, str(forge_path))
    try:
        from forge.paths import tenant_home as _tenant_home  # type: ignore
        base = _tenant_home(tenant_id)
    except Exception:
        corvin = (
            os.environ.get("CORVIN_HOME")
            or str(Path.home() / ".corvin")
        )
        tid = tenant_id or os.environ.get("CORVIN_TENANT_ID", "_default")
        base = Path(corvin) / "tenants" / tid

    return base / "global" / "agent"


def generate_or_load_keypair(
    agent_dir: Path | None = None,
    *,
    tenant_id: str | None = None,
) -> tuple[bytes, bytes]:
    """Return (private_pem, public_pem).  Generate and persist on first call."""
    d = agent_dir if agent_dir is not None else _agent_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass

    priv_path = d / "byok_privkey.pem"
    pub_path = d / "byok_pubkey.pem"

    if priv_path.exists() and pub_path.exists():
        priv_pem = priv_path.read_bytes()
        pub_pem = pub_path.read_bytes()
        return priv_pem, pub_pem

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # Write private key first (mode 0600), then public key.
    _atomic_write(priv_path, priv_pem, mode=0o600)
    _atomic_write(pub_path, pub_pem, mode=0o644)
    return priv_pem, pub_pem


def get_public_key_pem(
    agent_dir: Path | None = None,
    *,
    tenant_id: str | None = None,
) -> bytes:
    """Return the RSA public key in PEM format, generating it if absent."""
    _, pub_pem = generate_or_load_keypair(agent_dir, tenant_id=tenant_id)
    return pub_pem


def decrypt_oaep(
    ciphertext_b64: str,
    agent_dir: Path | None = None,
    *,
    tenant_id: str | None = None,
) -> str:
    """Decrypt an RSA-OAEP-SHA256 ciphertext.  Returns plaintext string.

    Raises ValueError on bad ciphertext, FileNotFoundError if key absent.
    """
    import base64
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    from cryptography.exceptions import InvalidKey

    d = agent_dir if agent_dir is not None else _agent_dir(tenant_id)
    priv_path = d / "byok_privkey.pem"
    if not priv_path.exists():
        raise FileNotFoundError(f"BYOK private key not found at {priv_path}")

    priv_pem = priv_path.read_bytes()
    private_key = serialization.load_pem_private_key(priv_pem, password=None)
    if not isinstance(private_key, RSAPrivateKey):
        raise ValueError("loaded key is not an RSA private key")

    try:
        ciphertext = base64.b64decode(ciphertext_b64)
    except Exception as exc:
        raise ValueError(f"ciphertext is not valid base64: {exc}") from exc

    try:
        plaintext = private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:
        raise ValueError(f"RSA-OAEP decryption failed: {exc}") from exc

    return plaintext.decode("utf-8")


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_suffix(".pem.tmp")
    tmp.write_bytes(data)
    try:
        tmp.chmod(mode)
    except OSError:
        pass
    tmp.replace(path)
