"""License JWT verification — RS256 against a pinned public key.

The Maintainer signs license tokens offline (outside any
Corvin deployment) with an RS256 private key. The matching
public key is **embedded in this plugin** at
``corvin_license/pubkey.pem`` — operators cannot swap it without
modifying source. This is intentional: it prevents a clever
operator from self-signing a license against an arbitrary key
they generated.

ADR-0093 M1.1: ``CORVIN_LICENSE_PUBKEY_PATH`` env-var override is
removed from the runtime code path. Tests use Python-level injection
(``monkeypatch.setattr(verifier, 'load_pubkey', lambda: pem)``).
Setting the env var at runtime is now a no-op and triggers a WARNING
in the audit log.

ADR-0093 M1.2: ``_PUBKEY_SHA256`` is a compile-time constant of the
embedded pubkey.  ``load_pubkey()`` verifies the file's sha256 on
every load and raises ``RuntimeError`` on mismatch — two coordinated
edits needed to bypass (constant + file), visually prominent in review.

License file location:
  ``<corvin_home>/global/license/license.jwt``   (mode 0o600)

A missing license file is the documented **free tier** — never an
error. Only an installed-but-invalid token raises.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make forge paths importable when running from any plugin venv.
_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parents[2]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402

try:
    import jwt as _pyjwt
except ImportError as exc:  # pragma: no cover — bootstrap should install it
    raise ImportError(
        "PyJWT not installed; run core/license/bootstrap.sh"
    ) from exc


# ── Constants ─────────────────────────────────────────────────────────

ALLOWED_ALGORITHMS = ("RS256",)  # No HS256 — shared-secret JWTs forbidden.
EXPECTED_ISSUER = "corvin-maintainer"
VALID_TIERS = frozenset({
    "free", "personal", "pro", "business", "enterprise",
    # ADR-0097/ADR-0098 flat-rate tier (canonical name + legacy aliases)
    "member", "universal",
    # Legacy tier aliases from earlier Corvin-Keys versions
    "starter", "professional",
})
# Legacy tier name → canonical product name. Accepted on the wire (VALID_TIERS)
# but normalized so only the canonical name surfaces. "member" is the canonical
# paid tier; "universal" is the legacy Corvin-Features name.
_CANONICAL_TIER = {
    "universal": "member",
    "starter": "personal",
    "professional": "pro",
}
VALID_FEATURE_FLAGS = frozenset({
    "compliance_reports_premium",
    "cross_tenant_search",
    "sso_wizard",
    "worm_archive",
    "sla_dashboard",
    "support_integration",
    "white_label_ui",
    "compute",          # ADR-0013/ADR-0017 — out-of-LLM compute worker
    "compute_fabric",   # ADR-0026 — Compute Fabric (parallel workers, sharding)
})
_CUSTOMER_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


class LicenseError(Exception):
    """Base class — never raised, always a subclass."""


class LicenseFileMissing(LicenseError):
    """No license.jwt installed — treated as free tier, not an error."""


class LicenseFileMalformed(LicenseError):
    """File exists but has wrong shape (mode, JSON, etc.)."""


class LicenseSignatureError(LicenseError):
    """Signature failed verification against the pinned key."""


class LicenseExpired(LicenseError):
    """Token's exp claim has passed.

    Carries the expired-at epoch + a customer-id fingerprint so the
    grace-period state machine can still anchor without re-verifying.
    """

    def __init__(
        self,
        message: str,
        *,
        expired_at: int | None = None,
        customer_fingerprint: str | None = None,
        tier: str | None = None,
    ) -> None:
        super().__init__(message)
        self.expired_at = expired_at
        self.customer_fingerprint = customer_fingerprint
        self.tier = tier


class LicenseClaimError(LicenseError):
    """Token verifies but a claim is missing / out-of-range."""


# ── Data model ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class License:
    """Validated license token.

    Field documentation:
    - customer_id: opaque operator-side identifier (hashed in audit details)
    - tier: free|pro|business|enterprise
    - employee_count_max: self-declared org size limit (advisory)
    - seats: number of named users allowed (advisory; not enforced here)
    - valid_until: unix epoch seconds; license expires at this point
    - issued_at: unix epoch seconds; token issuance time
    - feature_flags: list of enabled premium features
    - trial_type: "community" | "business" | None — set for trial tokens only
    - trial_expires_at: absolute epoch for trial end (signed, tamper-proof)
    - trial_id: opaque trial identifier for server-side activation anchoring
    - machine_fp: sha256 fingerprint of the installing machine (community only)
    """
    customer_id: str
    tier: str
    employee_count_max: int
    seats: int
    valid_until: int
    issued_at: int
    feature_flags: tuple[str, ...] = field(default_factory=tuple)
    # Trial-specific fields — None for paid licenses
    trial_type: str | None = None
    trial_expires_at: int | None = None
    trial_id: str | None = None
    machine_fp: str | None = None

    @property
    def is_trial(self) -> bool:
        return self.trial_type is not None

    def is_expired(self, *, now: int | None = None) -> bool:
        if now is None:
            now = int(time.time())
        return now >= self.valid_until

    def has_flag(self, flag: str) -> bool:
        return flag in self.feature_flags

    def to_public_dict(self) -> dict[str, Any]:
        """Render for /v1/license/status. customer_id is FINGERPRINTED."""
        d: dict[str, Any] = {
            "tier": self.tier,
            "customer_id_fingerprint": fingerprint_customer_id(self.customer_id),
            "employee_count_max": self.employee_count_max,
            "seats": self.seats,
            "valid_until": self.valid_until,
            "issued_at": self.issued_at,
            "feature_flags": list(self.feature_flags),
            "expired": self.is_expired(),
        }
        if self.is_trial:
            d["trial_type"] = self.trial_type
            d["trial_expires_at"] = self.trial_expires_at
            d["trial_id"] = self.trial_id
        return d


def fingerprint_customer_id(customer_id: str) -> str:
    """First 12 chars of sha256 — short, greppable, non-reversible."""
    import hashlib
    return hashlib.sha256(customer_id.encode("utf-8")).hexdigest()[:12]


# ── Public-key resolution (ADR-0093 M1.1 + M1.2) ─────────────────────

# sha256 of the embedded pubkey.pem committed to this repo.
# Two edits are required to swap the key: this constant AND the file.
# Never set to None — the check is unconditional so it cannot be
# disabled by a single edit to this file alone.
_PUBKEY_SHA256: str = (
    "ec18c9ada25604be1490fc581dd11f2d70c74a0bbbc89a87445800ab6533ab08"
)


def _pubkey_path() -> Path:
    """Canonical location of the pinned pubkey.  No env-var override."""
    return _THIS / "pubkey.pem"


def _warn_pubkey_env_override() -> None:
    """Log a WARNING when CORVIN_LICENSE_PUBKEY_PATH is set but ignored.

    The env-var override was removed in ADR-0093 M1.1. Operators who
    still have it set will see this in the audit log as an anomaly.
    """
    import logging
    logging.getLogger(__name__).warning(
        "CORVIN_LICENSE_PUBKEY_PATH is set but ignored since ADR-0093 M1.1. "
        "Remove this env var from your deployment. "
        "It has no effect and signals a misconfigured environment."
    )


def load_pubkey() -> bytes:
    """Read and integrity-check the pinned public-key PEM.

    ADR-0093 M1.1: CORVIN_LICENSE_PUBKEY_PATH is ignored at runtime.
    Raises RuntimeError if the embedded file does not match _PUBKEY_SHA256.
    Raises FileNotFoundError if pubkey.pem is absent (free-tier no-op path).
    """
    import hashlib
    if os.environ.get("CORVIN_LICENSE_PUBKEY_PATH"):
        _warn_pubkey_env_override()

    path = _pubkey_path()
    pem = path.read_bytes()

    actual = hashlib.sha256(pem).hexdigest()
    if actual != _PUBKEY_SHA256:
        raise RuntimeError(
            "license/pubkey.pem integrity check failed — "
            "the file has been modified since this build. "
            f"Expected sha256={_PUBKEY_SHA256!r}, got {actual!r}. "
            "Re-run: pip install --force-reinstall corvin-license"
        )
    return pem


# ── License file resolution ───────────────────────────────────────────

def license_file_path() -> Path:
    """Canonical on-disk location of license.jwt.

    The license is a per-Corvin-installation artefact (not
    per-tenant) — one license covers the whole deployment.
    """
    home = _forge_paths.corvin_home()
    return home / "global" / "license" / "license.jwt"


# ── JWT verification ──────────────────────────────────────────────────

def verify_token(
    token: str,
    *,
    pubkey_pem: bytes,
    now: int | None = None,
    leeway_seconds: int = 30,
) -> License:
    """Validate a license JWT against the pinned public key.

    Raises:
      LicenseSignatureError: bad signature OR wrong algorithm
      LicenseExpired: exp claim passed (beyond leeway)
      LicenseClaimError: required claim missing / wrong shape
    """
    now = now or int(time.time())

    # Parse header first to enforce alg whitelist (PyJWT honours the
    # passed `algorithms` list, but we double-check the header to
    # surface a precise error message).
    try:
        header = _pyjwt.get_unverified_header(token)
    except Exception as exc:
        raise LicenseSignatureError(f"malformed-token: {exc}") from exc

    alg = header.get("alg")
    if alg not in ALLOWED_ALGORITHMS:
        raise LicenseSignatureError(
            f"algorithm-not-allowed: {alg!r} (allowed: {ALLOWED_ALGORITHMS})"
        )

    try:
        claims = _pyjwt.decode(
            token,
            pubkey_pem,
            algorithms=list(ALLOWED_ALGORITHMS),
            issuer=EXPECTED_ISSUER,
            leeway=leeway_seconds,
            options={"require": ["exp", "iat", "iss"]},
        )
    except _pyjwt.ExpiredSignatureError as exc:
        # Re-extract exp + customer-id without verification so the
        # grace-period state machine has an anchor.
        try:
            unsafe = _pyjwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_iss": False,
                },
            )
            expired_at = int(unsafe.get("exp", 0)) or None
            cust = str(unsafe.get("customer_id", ""))
            fp = fingerprint_customer_id(cust) if cust else None
            tier_val = str(unsafe.get("tier", "")) or None
        except Exception:
            expired_at, fp, tier_val = None, None, None
        raise LicenseExpired(
            str(exc),
            expired_at=expired_at,
            customer_fingerprint=fp,
            tier=tier_val,
        ) from exc
    except _pyjwt.InvalidSignatureError as exc:
        raise LicenseSignatureError(f"signature-invalid: {exc}") from exc
    except _pyjwt.InvalidIssuerError as exc:
        raise LicenseClaimError(f"issuer-invalid: {exc}") from exc
    except _pyjwt.MissingRequiredClaimError as exc:
        raise LicenseClaimError(f"claim-missing: {exc}") from exc
    except _pyjwt.PyJWTError as exc:
        raise LicenseSignatureError(f"verify-failed: {exc}") from exc

    return _validate_claims(claims, now=now)


_VALID_TRIAL_TYPES = frozenset({"community", "business"})
_TRIAL_ID_RE = re.compile(r"^t_[a-zA-Z0-9]{6,64}$")
_MACHINE_FP_RE = re.compile(r"^[a-f0-9]{32}$")


def _validate_claims(claims: dict[str, Any], *, now: int) -> License:
    """Project + range-check the structural claims."""
    try:
        customer_id = str(claims["customer_id"])
    except KeyError as exc:
        raise LicenseClaimError("claim-missing: customer_id") from exc
    if not _CUSTOMER_ID_RE.match(customer_id):
        raise LicenseClaimError(f"customer-id-shape: {customer_id!r}")

    tier = str(claims.get("tier", "")).lower()
    if tier not in VALID_TIERS:
        raise LicenseClaimError(f"tier-invalid: {tier!r}")
    if tier == "free":
        raise LicenseClaimError(
            "tier-free-not-signable: a signed free-tier token is "
            "structurally meaningless; absence of license.jwt is "
            "the documented free-tier signal."
        )
    # Normalize legacy tier aliases to the canonical product name AFTER the
    # validity check (legacy names stay accepted on the wire, but never surface).
    # "member" is the canonical paid tier; "universal" is the legacy
    # Corvin-Features name and must never be shown to operators.
    tier = _CANONICAL_TIER.get(tier, tier)

    try:
        employee_count_max = int(claims.get("employee_count_max", 0))
    except (TypeError, ValueError) as exc:
        raise LicenseClaimError(f"employee_count_max-not-int: {exc}") from exc
    if not (1 <= employee_count_max <= 1_000_000):
        raise LicenseClaimError(
            f"employee_count_max-out-of-range: {employee_count_max}"
        )

    try:
        seats = int(claims.get("seats", 0))
    except (TypeError, ValueError) as exc:
        raise LicenseClaimError(f"seats-not-int: {exc}") from exc
    if not (1 <= seats <= 100_000):
        raise LicenseClaimError(f"seats-out-of-range: {seats}")

    issued_at = int(claims["iat"])
    valid_until = int(claims["exp"])
    if valid_until <= issued_at:
        raise LicenseClaimError("exp-not-after-iat")

    flags_raw = claims.get("feature_flags", []) or []
    if not isinstance(flags_raw, list):
        raise LicenseClaimError("feature_flags-not-list")
    flags: list[str] = []
    for f in flags_raw:
        if not isinstance(f, str):
            raise LicenseClaimError("feature_flags-non-string-entry")
        if f not in VALID_FEATURE_FLAGS:
            # Unknown flag = silently dropped (forward-compatible: a
            # plugin built for v1.2 ignores a v1.3-only flag rather
            # than crashing). Drop with no error.
            continue
        flags.append(f)

    # ADR-0019 §Security #6 — Tier→Flag defence-in-depth.
    # A signing-host compromise that produced a JWT with off-tier
    # flags is rejected here even if the signature is valid. The
    # tier_flags module is the canonical source of truth shared
    # between the issuer (cli/signer) and this verifier.
    from . import tier_flags as _tier_flags  # local: avoid import cycle
    try:
        _tier_flags.validate_flags_for_tier(tier, flags)
    except _tier_flags.TierFlagMismatch as exc:
        raise LicenseClaimError(f"tier-flag-drift: {exc}") from exc

    # Clock-rollback guard: if the local clock is more than 5 minutes
    # before the token's issuance time, something is wrong with the
    # system clock (or the token was forged with a future iat).
    # 300 s leeway absorbs NTP drift; a larger gap is a manipulation signal.
    if now < issued_at - 300:
        raise LicenseClaimError(
            f"clock-before-issuance: now={now} iat={issued_at} delta={issued_at - now}s"
        )

    # ── Optional trial claims ──────────────────────────────────────────
    trial_type: str | None = claims.get("trial_type") or None
    trial_expires_at: int | None = None
    trial_id: str | None = None
    machine_fp: str | None = None

    if trial_type is not None:
        trial_type = str(trial_type).lower()
        if trial_type not in _VALID_TRIAL_TYPES:
            raise LicenseClaimError(f"trial_type-invalid: {trial_type!r}")

        raw_texp = claims.get("trial_expires_at")
        if raw_texp is None:
            raise LicenseClaimError("trial_expires_at-missing for trial token")
        try:
            trial_expires_at = int(raw_texp)
        except (TypeError, ValueError) as exc:
            raise LicenseClaimError(f"trial_expires_at-not-int: {exc}") from exc
        if trial_expires_at <= issued_at:
            raise LicenseClaimError("trial_expires_at-not-after-iat")
        if trial_expires_at > valid_until:
            raise LicenseClaimError("trial_expires_at-exceeds-exp")

        raw_tid = claims.get("trial_id")
        if raw_tid is None:
            raise LicenseClaimError("trial_id-missing for trial token")
        trial_id = str(raw_tid)
        if not _TRIAL_ID_RE.match(trial_id):
            raise LicenseClaimError(f"trial_id-shape: {trial_id!r}")

        raw_fp = claims.get("machine_fp")
        if raw_fp is not None:
            machine_fp = str(raw_fp)
            if not _MACHINE_FP_RE.match(machine_fp):
                raise LicenseClaimError(f"machine_fp-shape: {machine_fp!r}")
            if trial_type == "community":
                # Community trials are single-machine — enforce binding.
                from . import trial as _trial
                current_fp = _trial.machine_fingerprint()
                if machine_fp != current_fp:
                    raise LicenseClaimError(
                        "machine_fp-mismatch: this token was issued for a "
                        "different installation"
                    )
            # Business trials are intentionally multi-machine (deployment-wide evaluation);
            # machine_fp is stored in the License dataclass for operator inspection but
            # is NOT enforced on install. The license.machine_fp docstring reflects this.

    return License(
        customer_id=customer_id,
        tier=tier,
        employee_count_max=employee_count_max,
        seats=seats,
        valid_until=valid_until,
        issued_at=issued_at,
        feature_flags=tuple(flags),
        trial_type=trial_type,
        trial_expires_at=trial_expires_at,
        trial_id=trial_id,
        machine_fp=machine_fp,
    )


# ── Load-from-disk convenience ────────────────────────────────────────

def load_license_from_disk(
    *,
    pubkey_pem: bytes | None = None,
    now: int | None = None,
) -> License:
    """Read + verify the on-disk license.jwt.

    Raises ``LicenseFileMissing`` when the file doesn't exist —
    callers MUST treat that as the free-tier signal, not an error.
    """
    path = license_file_path()
    if not path.exists():
        raise LicenseFileMissing(str(path))

    # Mode check — refuse a world-readable token (it's not strictly
    # secret because Apache-2.0 + structural lock-out — but the
    # operator-installed file is expected to be 0o600 and a wider
    # mode signals a mistaken `chmod`.)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise LicenseFileMalformed(
            f"file-mode-too-permissive: 0o{mode:o} (expected 0o600)"
        )

    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise LicenseFileMalformed("file-empty")
    if pubkey_pem is None:
        pubkey_pem = load_pubkey()
    return verify_token(token, pubkey_pem=pubkey_pem, now=now)


__all__ = [
    "License",
    "LicenseError",
    "LicenseFileMissing",
    "LicenseFileMalformed",
    "LicenseSignatureError",
    "LicenseExpired",
    "LicenseClaimError",
    "ALLOWED_ALGORITHMS",
    "EXPECTED_ISSUER",
    "VALID_TIERS",
    "VALID_FEATURE_FLAGS",
    "fingerprint_customer_id",
    "load_pubkey",
    "license_file_path",
    "verify_token",
    "load_license_from_disk",
]
