"""Template: custom UserBackend plugin (ADR-0233).

Authenticate against an external directory (LDAP, OIDC, SCIM, your own DB).

Copy this file, rename the class, fill in the TODOs, then install via:
    spec.plugins.installed:
      - id: "com.example.ldap-users"
        class_path: "my_package.my_module:MyLdapUserPlugin"
        config:
          server: "ldaps://dc1.corp.example:636"
          bind_dn_secret: "LDAP_BIND_DN"     # vault KEY NAME, never the value
          base_dn: "ou=people,dc=example,dc=com"

Rules you MUST honour:

* **Deny is the only safe failure.**  Return ``None`` when the credentials do not
  check out.  Raise only for infrastructure faults — the registry converts a raise
  and a timeout into a deny as well, but never into an admit.
* NEVER return a guest/anonymous principal as a fallback.  No auto-admit, no
  trusted-observer allowlist (CLAUDE.md § Compliance Baseline).
* NEVER return credential material in the principal dict (no password hashes, no
  tokens).  The registry strips known secret keys, but do not rely on that.
* NEVER log the submitted username, the bind DN, or ``str(exc)`` from your
  directory client — those routinely carry both PII and infrastructure detail.
  Log the exception CLASS only.
* ``enforce_quota`` raises to deny.  If you cannot evaluate the quota, raise —
  an unreachable quota service must not read as "unlimited".
"""
from __future__ import annotations

import logging

from corvin_plugins.protocol import HealthStatus, PluginContext

_log = logging.getLogger("corvin.auth.example")


class QuotaExceeded(RuntimeError):
    """Raised by enforce_quota to deny a resource."""


class MyLdapUserPlugin:
    """Replace with your actual class name."""

    plugin_id    = "com.example.ldap-users"   # globally unique reverse-domain
    plugin_type  = "user_backend"
    version      = "1.0.0"
    display_name = "My LDAP User Backend"

    def __init__(self) -> None:
        self._config: dict = {}
        self._pool = None

    # ── CorvinPlugin lifecycle ───────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        self._config = ctx.config
        # TODO: build a CONNECTION POOL here, not one connection per auth call.
        # TODO: resolve secrets by vault key name (self._config["bind_dn_secret"]).
        if ctx.user_registry is not None:
            ctx.user_registry.set_active(self)

    def on_unload(self) -> None:
        # TODO: close the pool.  After this returns, get_active() must no longer
        # reach you — the registry clears the slot on unload.
        self._pool = None

    def health_check(self) -> HealthStatus:
        # TODO: cheap bind against the directory.  Must return within 2 s.
        return HealthStatus(ok=True, message="ok")

    # ── UserBackend capability ───────────────────────────────────────────────

    async def authenticate(self, credentials: dict) -> dict | None:
        """Return a principal on success, None on ANY authentication failure."""
        username = credentials.get("username") or ""
        password = credentials.get("password") or ""
        if not username or not password:
            return None  # deny; never treat "no credentials" as anonymous access

        try:
            # TODO: bind against your directory here.
            authenticated = False
            groups: list[str] = []
        except Exception as exc:  # noqa: BLE001
            # Class name only — no username, no server URL, no bind DN.
            _log.error("directory bind failed (%s) — denying", type(exc).__name__)
            return None

        if not authenticated:
            return None

        return {
            # A STABLE, opaque id. Prefer the directory's immutable id (objectGUID,
            # `sub`) over the login name so a rename does not fork the identity.
            "user_id": f"ldap:{username}",
            "roles": self._map_groups_to_roles(groups),
            # No password, no hash, no token in here.
        }

    async def get_user(self, user_id: str) -> dict | None:
        # TODO: look the user up; return None when absent.
        return None

    async def list_users(self) -> list[dict]:
        # TODO: for the admin UI. Page your directory query — do not pull 50k
        # entries into memory.
        return []

    async def enforce_quota(self, user_id: str, resource: str) -> None:
        """Raise to deny; return to allow.

        resource is one of "tokens" | "compute_minutes" | "api_calls".
        If the quota cannot be evaluated, RAISE — fail-closed.
        """
        # TODO: check the user's allowance for `resource`.
        return None

    # ── internals ────────────────────────────────────────────────────────────

    @staticmethod
    def _map_groups_to_roles(groups: list[str]) -> list[str]:
        """Map directory groups to Corvin roles.

        Keep this explicit and allowlist-shaped: an unmapped group must produce NO
        role, never a default-admin or a default-operator.
        """
        mapping = {
            # "cn=corvin-admins,ou=groups,dc=example,dc=com": "admin",
        }
        return sorted({mapping[g] for g in groups if g in mapping})
