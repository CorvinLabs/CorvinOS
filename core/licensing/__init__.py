"""Licensing Module - A2A Delegation Access Control & Model Billing

Consolidates two licensing subsystems:
1. A2A Delegation (ADR-0704)
   - Member credentials with RSA keypairs
   - Signed task verification (fail-closed)
   - Revocation list management

2. Model Billing (ADR-0700)
   - ModelTier access control (COMMUNITY / MEMBER)
   - BillingSchema with pricing and quotas
   - Per-model pricing configuration

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
from core.licensing.billing import (
    ModelTier,
    ModelPricing,
    ModelPricingModel,
    BillingSchema,
    create_default_billing_schema,
)

__all__ = [
    # A2A Delegation
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
    # Model Billing
    "ModelTier",
    "ModelPricing",
    "ModelPricingModel",
    "BillingSchema",
    "create_default_billing_schema",
]
