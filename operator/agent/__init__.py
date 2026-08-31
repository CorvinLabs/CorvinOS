"""operator/agent — Instance Agent for ADR-0047 (Hosted-Mode Tenant Console + BYOK).

The Instance Agent runs inside each tenant's container and bridges the
Management API (control plane) with the Corvin core (data plane).

Responsibilities:
  - Generate and persist RSA-2048 keypair for BYOK encryption
  - Register with Management API at boot (mTLS provisioning flow)
  - Expose /health, /pubkey, /secrets/<key_name> over HTTP
  - Receive encrypted BYOK blobs, decrypt, write to L16 vault
  - Emit structured audit events for all security-relevant actions

Must NOT:
  - import anthropic (CI AST lint enforces this)
  - forward decrypted values outside the instance
  - share the RSA private key with any external system
"""

from .keypair import generate_or_load_keypair, get_public_key_pem
from .byok import apply_byok_secret, validate_key_name

__all__ = [
    "generate_or_load_keypair",
    "get_public_key_pem",
    "apply_byok_secret",
    "validate_key_name",
]
