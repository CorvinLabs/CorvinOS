"""OIDC / JWT validation for the Corvin Gateway.

ADR-0007 Phase 3.4. Adds a second authentication class alongside the
Phase 2.1 ``atlr_`` bearer tokens: incoming requests may now also
carry an ``Authorization: Bearer <jwt>`` header where the value is a
signed JWT issued by an OpenID Connect provider the operator has
configured for the tenant.

OIDC trust contract
-------------------

Per-tenant ``oidc.yaml`` (mode ``0o600``) under
``<tenant_home>/global/auth/`` declares which issuers a tenant
trusts and how the tenant id is extracted from a presented JWT::

    apiVersion: corvin/v1
    kind: TenantOIDC
    metadata:
      id: acme
    spec:
      issuers:
        - issuer: https://idp.acme.com/realms/acme
          audience: corvin-acme
          # Either a static JWKS (HS / RS / EC keys) or a JWKS URI
          # the operator pre-fetches and pins. Phase 3.4 ships the
          # static-jwks shape; dynamic JWKS fetching from a live
          # issuer arrives in Phase 3.6 alongside the Keycloak smoke.
          jwks:
            keys:
              - {"kty": "oct", "kid": "k1", "k": "<base64url-secret>"}
          tenant_claim: "sub"           # default: "sub"
          allowed_algorithms: [HS256]   # default: [RS256, ES256]
          required_scopes: []           # optional

What this module does NOT do
----------------------------

* No JWKS-URI fetching. Phase 3.4 ships the static / pinned-key
  shape only. Operators who want dynamic JWKS rotation pre-fetch
  the JWKS and pin it; Phase 3.6's Keycloak smoke documents the
  cadence.
* No SCIM provisioning. Phase 3.5 owns that.
* No automatic tenant inference from issuer hostname. The tenant
  id is extracted from the JWT's ``tenant_claim`` field (default
  ``sub``); the auth path validates that the tenant exists on disk.
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
import jwt as _pyjwt
from jwt import PyJWKSet

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge.tenants import InvalidTenantID, validate_tenant_id  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402

from .auth import ResolvedToken, _audit as _auth_audit  # re-use chain helper


# ── Event registration ───────────────────────────────────────────────


_OIDC_EVENTS = {
    "gateway.oidc_resolved":         "INFO",
    "gateway.oidc_resolve_failed":   "WARNING",
    "gateway.oidc_trust_unconfigured": "WARNING",
}
for _evt, _sev in _OIDC_EVENTS.items():
    _security_events.EVENT_SEVERITY.setdefault(_evt, _sev)


# ── Constants ────────────────────────────────────────────────────────


OIDC_FILENAME = "oidc.yaml"
_REQUIRED_MODE = 0o600

# Curated default algorithm set. HS256 is allowed but operators must
# opt in via the YAML — shared-secret JWTs are weaker than asymmetric
# ones; we don't quietly accept them in the default list.
_DEFAULT_ALLOWED_ALGS = ("RS256", "ES256")

_TENANT_CLAIM_DEFAULT = "sub"


# ── Exceptions ───────────────────────────────────────────────────────


class OidcTrustMalformed(Exception):
    """Tenant has an oidc.yaml but it is unreadable / wrong shape /
    mode > 0o600."""


class OidcVerificationError(Exception):
    """A JWT was presented but failed verification — bad signature,
    expired, wrong audience, unknown issuer, etc."""


# ── Trust store ──────────────────────────────────────────────────────


@dataclass
class IssuerTrust:
    issuer:             str
    audience:           str | None
    jwks:               dict[str, Any]
    tenant_claim:       str = _TENANT_CLAIM_DEFAULT
    allowed_algorithms: tuple[str, ...] = _DEFAULT_ALLOWED_ALGS
    required_scopes:    tuple[str, ...] = ()
    # Phase 3.6: optional dynamic JWKS source. When set, the verifier
    # fetches the JWKS from this URL (HTTPS only in production), caches
    # it for ``jwks_cache_ttl_s``, and falls back to the inline ``jwks``
    # field on fetch failure (so a transient outage does not lock every
    # tenant out).
    jwks_uri:           str | None = None
    jwks_cache_ttl_s:   int = 300


@dataclass
class TenantOidcTrust:
    tenant_id: str
    issuers:   list[IssuerTrust] = field(default_factory=list)


def _oidc_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "auth" / OIDC_FILENAME


def _validate_jwks(jwks: Any) -> dict[str, Any]:
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise OidcTrustMalformed("jwks must be {'keys': [...]} object")
    if not jwks["keys"]:
        raise OidcTrustMalformed("jwks.keys must be a non-empty list")
    return jwks


def _validate_issuer(entry: Any) -> IssuerTrust:
    if not isinstance(entry, dict):
        raise OidcTrustMalformed("each issuer entry must be a mapping")
    issuer = entry.get("issuer")
    if not isinstance(issuer, str) or not issuer:
        raise OidcTrustMalformed("issuer must be a non-empty string")
    aud = entry.get("audience")
    if aud is not None and not isinstance(aud, str):
        raise OidcTrustMalformed("audience must be a string when set")
    # Phase 3.6: jwks is now optional when jwks_uri is set, but at
    # least one of the two MUST be present.
    jwks_raw = entry.get("jwks")
    jwks_uri = entry.get("jwks_uri")
    if jwks_uri is not None and not isinstance(jwks_uri, str):
        raise OidcTrustMalformed("jwks_uri must be a string when set")
    if jwks_uri and not (
        jwks_uri.startswith("http://") or jwks_uri.startswith("https://")
    ):
        raise OidcTrustMalformed("jwks_uri must be http(s)://...")
    if jwks_raw is None and not jwks_uri:
        raise OidcTrustMalformed("either jwks or jwks_uri must be set")
    if jwks_raw is None:
        jwks = {"keys": []}
    elif jwks_uri:
        # jwks_uri is the source of truth; jwks (if present) is just
        # a pinned fallback for outage scenarios. Allow an empty
        # keys list because save_trust round-trips it as such.
        if not isinstance(jwks_raw, dict) or not isinstance(
            jwks_raw.get("keys"), list
        ):
            raise OidcTrustMalformed("jwks must be {'keys': [...]} object")
        jwks = jwks_raw
    else:
        jwks = _validate_jwks(jwks_raw)
    tenant_claim = entry.get("tenant_claim", _TENANT_CLAIM_DEFAULT)
    if not isinstance(tenant_claim, str) or not tenant_claim:
        raise OidcTrustMalformed("tenant_claim must be a non-empty string")
    algs = entry.get("allowed_algorithms")
    if algs is None:
        algs_t = _DEFAULT_ALLOWED_ALGS
    else:
        if not isinstance(algs, list) or not algs:
            raise OidcTrustMalformed(
                "allowed_algorithms must be a non-empty list"
            )
        for a in algs:
            if not isinstance(a, str) or not a:
                raise OidcTrustMalformed(
                    "allowed_algorithms entries must be non-empty strings"
                )
        algs_t = tuple(algs)
    scopes = entry.get("required_scopes") or []
    if not isinstance(scopes, list):
        raise OidcTrustMalformed("required_scopes must be a list")
    ttl = entry.get("jwks_cache_ttl_s", 300)
    if not isinstance(ttl, int) or ttl < 30 or ttl > 86400:
        raise OidcTrustMalformed(
            "jwks_cache_ttl_s must be an int in [30, 86400]"
        )
    return IssuerTrust(
        issuer=issuer, audience=aud, jwks=jwks,
        tenant_claim=tenant_claim,
        allowed_algorithms=algs_t,
        required_scopes=tuple(scopes),
        jwks_uri=jwks_uri or None,
        jwks_cache_ttl_s=ttl,
    )


def load_trust(tenant_id: str) -> TenantOidcTrust | None:
    """Return the parsed trust config, or ``None`` if no
    ``oidc.yaml`` is present for this tenant."""
    validate_tenant_id(tenant_id)
    p = _oidc_path(tenant_id)
    if not p.exists():
        return None
    try:
        st = p.stat()
    except OSError as e:
        raise OidcTrustMalformed(f"stat failed for {p}: {e}") from e
    mode = st.st_mode & 0o777
    if mode != _REQUIRED_MODE:
        raise OidcTrustMalformed(
            f"{p}: mode 0o{mode:o}, want 0o{_REQUIRED_MODE:o}"
        )
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise OidcTrustMalformed(f"malformed YAML in {p}: {e}") from e
    if not isinstance(data, dict):
        raise OidcTrustMalformed(f"{p}: top-level must be a mapping")
    if data.get("apiVersion") != "corvin/v1":
        raise OidcTrustMalformed(f"{p}: apiVersion must be 'corvin/v1'")
    if data.get("kind") != "TenantOIDC":
        raise OidcTrustMalformed(f"{p}: kind must be 'TenantOIDC'")
    md = data.get("metadata") or {}
    if md.get("id") != tenant_id:
        raise OidcTrustMalformed(
            f"{p}: metadata.id != tenant directory {tenant_id!r}"
        )
    spec = data.get("spec") or {}
    issuer_entries = spec.get("issuers") or []
    if not isinstance(issuer_entries, list) or not issuer_entries:
        raise OidcTrustMalformed(
            f"{p}: spec.issuers must be a non-empty list"
        )
    issuers = [_validate_issuer(e) for e in issuer_entries]
    return TenantOidcTrust(tenant_id=tenant_id, issuers=issuers)


def save_trust(trust: TenantOidcTrust) -> Path:
    """Write the trust config atomically with mode ``0o600``."""
    tenant_id = trust.tenant_id
    validate_tenant_id(tenant_id)
    p = _oidc_path(tenant_id)
    if not p.parent.parent.exists():
        raise OidcTrustMalformed(
            f"tenant directory does not exist: {p.parent.parent}"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "apiVersion": "corvin/v1",
        "kind":       "TenantOIDC",
        "metadata":   {"id": tenant_id},
        "spec": {
            "issuers": [
                {
                    "issuer":             i.issuer,
                    "audience":           i.audience,
                    "jwks":               i.jwks,
                    "jwks_uri":           i.jwks_uri,
                    "jwks_cache_ttl_s":   i.jwks_cache_ttl_s,
                    "tenant_claim":       i.tenant_claim,
                    "allowed_algorithms": list(i.allowed_algorithms),
                    "required_scopes":    list(i.required_scopes),
                }
                for i in trust.issuers
            ],
        },
    }
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _REQUIRED_MODE)
    try:
        os.write(fd, body.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, p)
    os.chmod(p, _REQUIRED_MODE)
    return p


# ── JWT detection + verification ─────────────────────────────────────


_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def looks_like_jwt(presented: str) -> bool:
    """Cheap heuristic: three base64url segments separated by dots.

    Used by the FastAPI auth dispatcher to choose between the
    `atlr_` bearer path (Phase 2.1) and the OIDC path (Phase 3.4).
    A token that satisfies neither shape gets reported as
    `unknown-bearer`.
    """
    if not isinstance(presented, str) or len(presented) < 16:
        return False
    return bool(_JWT_RE.match(presented))


def _fingerprint(token: str) -> str:
    """Audit-safe fingerprint — first 8 chars of the sha256 of the
    presented JWT. Mirror of auth._fingerprint."""
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def _algorithms_for_jwks(jwks: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for k in jwks.get("keys", []):
        alg = k.get("alg")
        if isinstance(alg, str) and alg:
            out.add(alg)
    return out


# ── JWKS-URI dynamic fetch + cache ───────────────────────────────────


# Process-wide cache: (issuer-key, last_fetched, parsed_jwks).
# Keyed by jwks_uri because two tenants might pin the same Keycloak
# realm; sharing the fetched bytes is fine.
_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _fetch_jwks_uri(jwks_uri: str, *, timeout_s: float = 5.0) -> dict[str, Any]:
    """Fetch a JWKS document via HTTPS (or http for tests).

    Raises :class:`OidcVerificationError` on any HTTP / parse failure.
    """
    import httpx
    try:
        r = httpx.get(jwks_uri, timeout=timeout_s)
    except Exception as exc:
        raise OidcVerificationError(
            f"jwks_uri fetch failed: {type(exc).__name__}: {exc}"
        ) from exc
    if r.status_code != 200:
        raise OidcVerificationError(
            f"jwks_uri fetch returned HTTP {r.status_code}"
        )
    try:
        body = r.json()
    except Exception as exc:
        raise OidcVerificationError(
            f"jwks_uri body not JSON: {exc}"
        ) from exc
    if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
        raise OidcVerificationError(
            "jwks_uri body lacks 'keys' list"
        )
    return body


def _resolve_jwks_for_issuer(issuer: IssuerTrust) -> dict[str, Any]:
    """Return the JWKS to verify against — dynamic fetch + cache when
    ``jwks_uri`` is set, falling back to the inline JWKS on fetch
    failure so a transient outage does not lock the tenant out."""
    if not issuer.jwks_uri:
        return issuer.jwks
    cached = _JWKS_CACHE.get(issuer.jwks_uri)
    now = time.time()
    if cached is not None and (now - cached[0]) < issuer.jwks_cache_ttl_s:
        return cached[1]
    try:
        fetched = _fetch_jwks_uri(issuer.jwks_uri)
        _JWKS_CACHE[issuer.jwks_uri] = (now, fetched)
        return fetched
    except OidcVerificationError:
        # Stale-or-pinned fallback: prefer last-known good cache,
        # then the inline pinned JWKS (if any). A tenant with both
        # an absent jwks_uri origin AND no inline JWKS will fail
        # verification — that's the right outcome.
        if cached is not None:
            return cached[1]
        if issuer.jwks.get("keys"):
            return issuer.jwks
        raise


def _verify_against_issuer(
    token: str,
    issuer: IssuerTrust,
) -> dict[str, Any]:
    """Try to verify *token* against a single issuer trust. Returns
    the decoded claims dict on success, raises
    :class:`OidcVerificationError` on any failure."""
    jwks_dict = _resolve_jwks_for_issuer(issuer)
    try:
        jwks_obj = PyJWKSet.from_dict(jwks_dict)
    except Exception as exc:
        raise OidcVerificationError(f"bad jwks: {exc}") from exc

    try:
        unverified_header = _pyjwt.get_unverified_header(token)
    except Exception as exc:
        raise OidcVerificationError(f"malformed JWT header: {exc}") from exc

    alg = unverified_header.get("alg")
    if alg not in issuer.allowed_algorithms:
        raise OidcVerificationError(
            f"alg {alg!r} not in allowed_algorithms {issuer.allowed_algorithms}"
        )

    kid = unverified_header.get("kid")
    pyjwk = None
    if kid is not None:
        for k in jwks_obj.keys:
            if k.key_id == kid:
                pyjwk = k
                break
        if pyjwk is None:
            raise OidcVerificationError(f"kid {kid!r} not in trusted JWKS")
    else:
        # No kid in header — try every key. The first one whose alg
        # matches gets the chance; this is the OIDC convention for
        # small operator setups.
        if len(jwks_obj.keys) != 1:
            raise OidcVerificationError(
                "JWT missing 'kid' header and JWKS has multiple keys"
            )
        pyjwk = jwks_obj.keys[0]

    try:
        claims = _pyjwt.decode(
            token,
            key=pyjwk.key,
            algorithms=list(issuer.allowed_algorithms),
            audience=issuer.audience,
            issuer=issuer.issuer,
            options={"require": ["exp", "iss"]},
        )
    except _pyjwt.ExpiredSignatureError as exc:
        raise OidcVerificationError(f"token expired: {exc}") from exc
    except _pyjwt.InvalidAudienceError as exc:
        raise OidcVerificationError(f"bad audience: {exc}") from exc
    except _pyjwt.InvalidIssuerError as exc:
        raise OidcVerificationError(f"bad issuer: {exc}") from exc
    except _pyjwt.InvalidTokenError as exc:
        raise OidcVerificationError(f"invalid token: {exc}") from exc

    # Required-scope check
    if issuer.required_scopes:
        scope_str = claims.get("scope", "") if isinstance(claims, dict) else ""
        granted = set(scope_str.split())
        missing = [s for s in issuer.required_scopes if s not in granted]
        if missing:
            raise OidcVerificationError(
                f"missing required scopes: {missing}"
            )
    return claims


def resolve_jwt(presented: str) -> ResolvedToken | None:
    """Identify the tenant + label that owns a JWT, or ``None``.

    Walks every on-disk tenant looking for an ``oidc.yaml``; for
    each tenant, tries every configured issuer. Returns on first
    match. The tenant_id is taken from the configured
    ``tenant_claim`` of the matching issuer entry.
    """
    if not looks_like_jwt(presented):
        _auth_audit(
            "gateway.oidc_resolve_failed",
            tenant_id="_default",
            details={"reason": "not-a-jwt"},
            severity="WARNING",
        )
        return None

    tenants_root = _forge_paths.corvin_home() / "tenants"
    if not tenants_root.exists():
        _auth_audit(
            "gateway.oidc_resolve_failed",
            tenant_id="_default",
            details={"reason": "no-tenants-dir"},
            severity="WARNING",
        )
        return None

    last_error: str | None = None
    fp = _fingerprint(presented)
    for entry in sorted(tenants_root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            tid_on_disk = validate_tenant_id(entry.name)
        except InvalidTenantID:
            continue
        try:
            trust = load_trust(tid_on_disk)
        except OidcTrustMalformed as exc:
            last_error = f"malformed-trust:{tid_on_disk}"
            continue
        if trust is None:
            continue
        for issuer in trust.issuers:
            try:
                claims = _verify_against_issuer(presented, issuer)
            except OidcVerificationError as exc:
                last_error = str(exc)
                continue
            claimed = claims.get(issuer.tenant_claim)
            if not isinstance(claimed, str) or claimed != tid_on_disk:
                last_error = (
                    f"tenant-claim-mismatch:"
                    f"claim={claimed!r},dir={tid_on_disk!r}"
                )
                continue
            label = f"oidc:{issuer.issuer}"
            _auth_audit(
                "gateway.oidc_resolved",
                tenant_id=tid_on_disk,
                details={
                    "fingerprint": fp,
                    "issuer":      issuer.issuer,
                    "subject":     claims.get("sub", ""),
                },
            )
            return ResolvedToken(
                tenant_id=tid_on_disk, label=label, fingerprint=fp,
            )

    _auth_audit(
        "gateway.oidc_resolve_failed",
        tenant_id="_default",
        details={
            "fingerprint": fp,
            "reason":      last_error or "no-trusting-tenant",
        },
        severity="WARNING",
    )
    return None
