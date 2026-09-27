"""RBAC Framework for Task Tracking (Phase C k=5).

Pluggable identity-provider abstraction enables RBAC to ship without OIDC (ADR-0007).
LocalDevIdentityProvider works offline; OIDCIdentityProvider plugs in later.

Modules:
  - identity.py: IdentityProvider abstract interface (ADR-0007 conforms to this)
  - local_identity.py: LocalDevIdentityProvider (localhost login, no OIDC)
  - policy.py: RBACPolicy (permission enforcement)
  - middleware.py: FastAPI middleware integration
  - models.py: Role/Permission dataclasses

Compliance:
  - GDPR Art. 5: Every permission check audited
  - GDPR Art. 32: Token validation fail-closed
  - ADR-0007: Wiring points frozen for OIDC team
"""

from .identity import IdentityProvider, IdentityToken, User
from .local_identity import LocalDevIdentityProvider
from .policy import RBACPolicy, Permission
from .models import Role, RoleAssignment

__all__ = [
    "IdentityProvider",
    "IdentityToken",
    "User",
    "LocalDevIdentityProvider",
    "RBACPolicy",
    "Permission",
    "Role",
    "RoleAssignment",
]
