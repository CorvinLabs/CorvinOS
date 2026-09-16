"""Licensing Module - A2A Delegation Access Control

ADR-0704: Licensing Phase 2 - A2A RSA Gate
- Member credentials with RSA keypairs
- Signed task verification (fail-closed)
- Revocation list management
- Audit-integrated credential lifecycle

License: Apache-2.0
"""

from core.licensing.member_credential import (
    MemberCredential,
    SignedTask,
    RSAKeyPair,
    LicenseTier,
    CredentialStore,
)
from core.licensing.a2a_verifier import (
    A2ADelegationVerifier,
    VerificationResult,
    RevocationList,
)
from core.licensing.authority_server import (
    AuthorityServer,
    IssuanceResult,
)

__all__ = [
    "MemberCredential",
    "SignedTask",
    "RSAKeyPair",
    "LicenseTier",
    "CredentialStore",
    "A2ADelegationVerifier",
    "VerificationResult",
    "RevocationList",
    "AuthorityServer",
    "IssuanceResult",
]
