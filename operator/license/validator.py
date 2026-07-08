"""License token verifier — ADR-0092 M1.

Ed25519 public-key verification, session-token loading, and the central
get_limit() / assert_limit() API that enforcement call-sites use.

The private signing key lives in 1Password (op://CorvinLabs/…).
Only the PUBLIC key is here — it can verify but never forge tokens.

Token discovery order (first non-empty wins):
  1. CORVIN_LICENSE_KEY env var       (legacy / OSS single-token)
  2. ~/.config/corvin-voice/session.key   (auto-written by refresh daemon)
  3. <corvin_home>/global/license.key
  4. tenant.corvin.yaml::spec.license_key (per-tenant override — not yet wired)

In-process trust boundary (ADR-0139)
------------------------------------
This module's enforcement state — the module-level ``_ACTIVE_LICENSE`` name,
the ``assert_limit`` / ``is_feature_allowed`` callables, and the env-var
snapshots — lives in the adapter's own Python interpreter. ADR-0138 raised the
bar against *external* attacks (PYTHONPATH injection, env-var mutation after
boot, filesystem races). It does NOT — and cannot — protect against arbitrary
*in-process* Python code, which can rebind ``_ACTIVE_LICENSE`` to a forged
dict, reach the proxied dict via ``gc.get_referents()``, or rebind the imported
``_lic_assert_limit`` alias in the adapter module. ``MappingProxyType`` (see
``_freeze_license``) blocks casual item-assignment on the proxy but is NOT a
security boundary against gc-underlay or name-rebind.

ADR-0139 accepts this as a documented limitation (Option C) with compensating
controls — the bwrap sandbox keeps Forge tool code out of this interpreter, the
L10 path-gate blocks Python-file writes to license modules, and MCP servers
(which DO load in-process) require operator vetting. The out-of-process enforcer
(Option A) is the earmarked long-term fix. Do NOT add partial ``__setattr__``
hardening (Option B) here — it only mitigates name-rebind, leaves the gc vector
open, and creates a false sense of security (ADR-0139 § "What must NOT happen").
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import sys
import time
import types
from pathlib import Path
from typing import Any

try:
    # Package context (operator.license.validator). NB: a bare
    # `import operator.license...` can never work — `operator` is a stdlib
    # module that shadows the repo dir — so consumers load this module
    # top-level (e.g. custom_layer_gate after inserting operator/license on
    # sys.path). A bare relative import then raised "no known parent package"
    # at import time, taking the WHOLE module down → custom-layer license
    # limit defaulted to free-tier even on a paid licence. Fall back to the
    # absolute import (limits.py is on sys.path in the top-level case).
    from .limits import FREE_TIER, TIER_RESOURCE_LIMITS, LicenseLimitError
except ImportError:
    from limits import FREE_TIER, TIER_RESOURCE_LIMITS, LicenseLimitError  # type: ignore[no-redef]

log = logging.getLogger("corvin.license")


def _log_non_numeric_limit(
    feature: str,
    limit: Any,
    exc: Exception,
    tier: str,
) -> None:
    """Emit a WARNING and raise LicenseLimitError for a malformed numeric limit."""
    log.warning(
        "license: assert_limit(%r) — limit value %r is not numeric (%s); "
        "treating as exceeded (fail-closed). Check SesT signing tool.",
        feature, limit, exc,
    )
    _audit("license.limit_exceeded", feature=feature, tier=tier)
    raise LicenseLimitError(feature, tier=tier)

# ── Public key (Ed25519 DER, base64url) ──────────────────────────────────────
# This is NOT a secret.  Anyone can read it; it only verifies, never signs.
# Replace with the real CorvinLabs public key before shipping.
# Placeholder value is a valid but non-functional Ed25519 public key.
CORVIN_PUBLIC_KEY_B64: str = (
    "MCowBQYDK2VwAyEARuefomX8OXo0fiWeu1iPqqCaKgz2B5eg/mOYXY0iEUs="
)

# FND-22: known shipped PLACEHOLDER operator keys. A build that never replaced
# CORVIN_PUBLIC_KEY_B64 must NOT verify operator tokens against it — if the
# matching private half is a known/test key, anyone could forge operator tokens.
# _verify_ed25519 fails closed (Free tier) when the active operator key is one
# of these. Replace with the real CorvinLabs key before shipping.
_PLACEHOLDER_OPERATOR_PUBKEYS: frozenset = frozenset({
    "MCowBQYDK2VwAyEARuefomX8OXo0fiWeu1iPqqCaKgz2B5eg/mOYXY0iEUs=",
})


def _make_operator_key_fn() -> "tuple[Any, Any]":
    """Capture operator public key + placeholder set in a closure.

    Reassigning CORVIN_PUBLIC_KEY_B64 or _PLACEHOLDER_OPERATOR_PUBKEYS at
    module scope has no effect on verification — both are snapshotted here at
    module load time.  Extracting them requires inspecting the closure cells,
    not a trivial attribute read.  (ADR-0144 / ADR-0139 defence-in-depth.)
    """
    _key = CORVIN_PUBLIC_KEY_B64
    _placeholders = _PLACEHOLDER_OPERATOR_PUBKEYS

    def _get_operator_key() -> str:
        return _key

    def _is_placeholder(k: str) -> bool:
        return k in _placeholders

    return _get_operator_key, _is_placeholder


_get_operator_pubkey_b64, _is_placeholder_key = _make_operator_key_fn()

# LIC-4: known shipped PLACEHOLDER / test session pubkeys. _is_placeholder_key
# guards ONLY the operator-key branches (kid absent / non-sess-lic); the
# sess-*/lic-* branches verify against SESSION_SERVER_KEY_RING with no such
# guard — so a build that ever shipped a placeholder/test key in the ring would
# grant the member tier to anyone holding the (public) test private half.
# Mirror the operator guard for the session branch. The current real sess-v1
# key is intentionally NOT listed here (it is a production key). The known
# shipped operator placeholder value is listed defensively: its private half is
# a well-known/test key, so it must never verify a session token either.
_PLACEHOLDER_SESSION_PUBKEYS: frozenset = frozenset({
    "MCowBQYDK2VwAyEARuefomX8OXo0fiWeu1iPqqCaKgz2B5eg/mOYXY0iEUs=",
})


def _make_session_placeholder_fn() -> "Any":
    """Closure-capture the placeholder session set (parity with the operator fn)."""
    _placeholders = _PLACEHOLDER_SESSION_PUBKEYS

    def _is_placeholder_session(k: "str | None") -> bool:
        return k is not None and k in _placeholders

    return _is_placeholder_session


_is_placeholder_session_key = _make_session_placeholder_fn()

# Server-issued session-permit signing key ring (ADR-0098 P3).
#
# Maps kid → DER-encoded Ed25519 public key (standard base64).
# Keys are rotated every 90 days.  During a rotation window BOTH the old and
# new keys are present so in-flight permits stay valid.  Remove an old key
# after its 90-day overlap period has expired.
#
# Rotation procedure (run on the Corvin-Features server):
#   1. python scripts/rotate_session_key.py  → prints new kid + public key
#   2. Add the new entry to SESSION_SERVER_KEY_RING in this file
#   3. Update CURRENT_SESSION_KID in Corvin-Features signing.py to the new kid
#   4. Ship a new CorvinOS release — old installs continue to work via the overlap
#   5. After 90 days: remove the old kid entry and push a maintenance release
#
# Any published "crack kit" that replaces sess-v1 stops working when the server
# starts signing with a new kid.  Old permits verify with the old key until removed.
SESSION_SERVER_KEY_RING: "types.MappingProxyType[str, str]" = types.MappingProxyType({
    "sess-v1": "MCowBQYDK2VwAyEAGd/9rorTQ+kWfYsablfa4eD6RYl1MKhANivIRjozCK4=",
    # "sess-v2": "<new key after first rotation>",  # add here during next rotation
})
# Frozen at module load — any attempt to add/replace a key raises TypeError.
# An in-process attacker who replaces a key here can validate forged tokens
# that bypass the canary entirely; MappingProxyType blocks that mutation path.

# Convenience alias — the primary key used by the current server.
SESSION_SERVER_PUBLIC_KEY_B64: str = SESSION_SERVER_KEY_RING["sess-v1"]

_GRACE_PERIOD_SECONDS = 6 * 3600   # ADR-0098: reduced from 7d to 6h to match session TTL

# ── Module-level active licence (set once at boot) ───────────────────────────
_ACTIVE_LICENSE: dict[str, Any] | None = None
_LICENSE_LOADED_AT: float = 0.0

# ── Tamper-canary (ADR-0144 in-process hardening) ────────────────────────────
# A salted SHA-256 of _ACTIVE_LICENSE is computed every time the license is set.
# _verified_license() re-derives the hash and compares — any mutation via
# gc.get_referents() or name-rebind that forgets to update the canary is detected
# and falls back to FREE_TIER with a CRITICAL audit event.
# This is NOT a cryptographic guarantee against a determined in-process attacker
# (ADR-0139 § "in-process trust boundary") but it makes the attack multi-step and
# leaves a CRITICAL audit trail.
import hashlib as _hashlib
import json as _json
_ACTIVE_LICENSE_CANARY: "str | None" = None  # updated by _set_active_license()
# Sentinel for serialization failures on a non-None licence.
# Consistent failures compare equal (same sentinel = same object());
# a gc-mutation that changes serializability yields a different value → detected.
_CANARY_UNCOMPUTABLE: object = object()

# ── Reload rate limiter ───────────────────────────────────────────────────────
# Prevents rapid-cycle reload attacks via the console /license/apply endpoint.
_LAST_RELOAD_AT: float = 0.0
_MIN_RELOAD_INTERVAL_SECONDS: float = 5.0
# Hash of the on-disk token content as of the last successful (non-throttled)
# reload. The throttle above only makes sense for redundant re-reads of the
# SAME content — reload_from_disk() is also called on every authenticated
# session op (auth.py::_compute_lic_proof "cheap to call per session op"), so
# throttling unconditionally silently swallows the one reload that actually
# matters: the explicit console "Apply Key" reload immediately after writing
# a NEW token, which in real usage almost always lands inside the cooldown
# window opened by an incidental per-request call moments earlier.
_LAST_LOADED_TOKEN_HASH: str | None = None
# Log the placeholder-operator-key state ONCE per process — it's a stable Free-tier
# config state, and logging it CRITICAL on every operator-token verification spammed
# the log (1500+ CRITICAL lines observed on a running instance) and drowned the real
# integrity signal. The fail-closed behaviour (return None) is unchanged.
_PLACEHOLDER_KEY_WARNED: bool = False

# ── Revocation fingerprint cache (ADR-0102 individual-token revocation) ──────
# Per-token revocation is checked against Corvin-Features /v1/licenses/revoked.
# The result is cached locally so a brief network outage at boot does not
# permanently deny a legitimate licence holder.
_FEATURES_BASE_URL_DEFAULT: str = "https://corvin-features-production.up.railway.app"
_REVOCATION_CACHE_TTL_SECONDS: float = 3600.0  # 1 hour

# ── Env-var snapshots — write-once at load_license_from_env() time ────────────
# ADR-0138 M1 C1/C2/C4: freeze path derivation so post-boot env mutations
# cannot redirect CORVIN_HOME / XDG_CONFIG_HOME / VOICE_AUDIT_PATH to an
# attacker-controlled directory.  All internal helpers use these instead of
# live os.environ reads once load_license_from_env() has been called.
_CORVIN_HOME_SNAPSHOT: "Path | None" = None
_CONFIG_DIR_SNAPSHOT: "Path | None" = None
_AUDIT_PATH_SNAPSHOT: "Path | None" = None
_LICENSE_INITIALIZED: bool = False  # ADR-0138 M1 F2: idempotency guard

# ADR-0138 review: snapshot CORVIN_INTEGRATION_TEST at module import time so
# post-boot os.environ mutations cannot satisfy the force=True escape-hatch guard.
_INTEGRATION_TEST_SNAP: bool = bool(os.environ.get("CORVIN_INTEGRATION_TEST"))

# ADR-0144 Fix B1 (validator parity): snapshot CORVIN_FEATURES_URL + CORVIN_TEST_MODE
# at import time and honour the env override ONLY under CORVIN_TEST_MODE=1. The
# revocation endpoint (_fetch_revoked_fps) is an enforcement path: a post-boot
# CORVIN_FEATURES_URL mutation must NOT be able to redirect it to a dead/mock host
# and nullify ADR-0102 per-token revocation (the sole server-side kill switch for a
# cancelled/charged-back subscription's still-unexpired token). Mirrors the hardening
# already applied in session_refresh._features_server / mcp_server / corvin_a2a.
_TEST_MODE_SNAPSHOT: str = os.environ.get("CORVIN_TEST_MODE", "")
_FEATURES_URL_SNAPSHOT: str = os.environ.get("CORVIN_FEATURES_URL", _FEATURES_BASE_URL_DEFAULT)


def _features_base_url() -> str:
    """Return the Corvin-Features base URL (trailing slash stripped).

    The CORVIN_FEATURES_URL override is accepted ONLY under CORVIN_TEST_MODE=1
    (both snapshotted at import); in production the hardcoded default is used so
    a post-boot env mutation cannot redirect an enforcement fetch to an
    attacker-controlled endpoint. Parity with session_refresh._features_server().
    """
    if _TEST_MODE_SNAPSHOT == "1":
        return _FEATURES_URL_SNAPSHOT.rstrip("/")
    return _FEATURES_BASE_URL_DEFAULT.rstrip("/")


def _corvin_home_resolved() -> "Path":
    """Resolve the runtime home: CORVIN_HOME env → repo marker (<repo>/.corvin)
    → ~/.corvin. Mirrors forge.paths.corvin_home() so the license layer agrees
    with every other reader/writer when run from a repo checkout WITHOUT a
    CORVIN_HOME env (the license key was otherwise looked up under ~/.corvin
    while the rest of the system used <repo>/.corvin) — path-audit 2026-06-25
    #MED1. The import-time snapshot still wins for the ADR-0139 redirect
    guarantee; this only fixes WHICH home is snapshotted/falled-back to."""
    env = os.environ.get("CORVIN_HOME", "").strip()
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _freeze_license(obj: Any) -> Any:
    """Recursively wrap dicts in MappingProxyType to raise the bar on mutation.

    ADR-0138 M1 F1 blocks the casual mutation path: a read-only proxy makes
    ``validator._ACTIVE_LICENSE["tier"] = "enterprise"`` raise ``TypeError``.

    This is NOT a security boundary (ADR-0139). ``MappingProxyType`` does not
    seal the underlying dict against ``gc.get_referents()``, and the proxy can
    be replaced wholesale via a name-rebind (``validator._ACTIVE_LICENSE = {...}``).
    Both remain reachable by any in-process Python code and are accepted
    limitations under ADR-0139's in-process trust boundary (see module docstring).
    Do not represent this freeze as protection against in-process attackers.
    """
    if isinstance(obj, dict):
        return types.MappingProxyType({k: _freeze_license(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_freeze_license(item) for item in obj)
    return obj


def _unproxy(obj: Any) -> Any:
    """Recursively materialise MappingProxyType/tuple back to dict/list for hashing."""
    if isinstance(obj, types.MappingProxyType):
        return {k: _unproxy(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_unproxy(i) for i in obj]
    return obj


def _make_compute_canary() -> "Any":
    """Build the canary function with the salt captured in a closure.

    The salt is NOT exposed as a module-level attribute.  Reading it requires
    inspecting _compute_canary.__closure__[N].cell_contents — more effort than
    the trivial ``license.validator._CANARY_SALT`` attribute read that would be
    needed if the salt were module-level.  This raises the practical bar for
    in-process attacks without changing the fundamental ADR-0139 trust boundary.

    ADR-0139 accepted in-process boundary
    ─────────────────────────────────────
    Any Python code running in the adapter's interpreter can bypass the canary
    by rebinding one or more of the following module-level names.  These vectors
    are explicitly accepted; the compensating controls are L10 path-gate, bwrap
    (Forge tools never run in-process), and mandatory MCP-server vetting.

      • ``_compute_canary``      — rebinding to ``lambda x: _ACTIVE_LICENSE_CANARY``
                                  makes the canary check trivially pass for any
                                  value of ``_ACTIVE_LICENSE`` (2-rebind bypass;
                                  does NOT require knowledge of the closure salt).
      • ``_verified_license``   — 1-rebind bypass of ALL public enforcement APIs
                                  (get_limit, assert_limit, active_tier, …).
      • ``assert_limit``        — 1-rebind bypass of the enforcement gate used by
                                  flow_runner and remote_trigger_receiver.
      • ``get_limit``           — 1-rebind bypass of per-feature limit lookup.
      • ``FREE_TIER``           — 1-rebind bypass of the free-tier fallback; also
                                  reachable via gc.get_referents(FREE_TIER) mutation.
      • ``TIER_RESOURCE_LIMITS``— 1-rebind bypass of tier-level defaults.
      • ``_GRACE_PERIOD_SECONDS``— 1-rebind allows permanently accepting expired tokens.
      • ``_get_operator_pubkey_b64``— 1-rebind lets attacker-signed JWTs pass Ed25519.
      • ``_is_placeholder_key`` — 1-rebind deactivates the placeholder-key guard.
      • ``_LICENSE_INITIALIZED`` — 1-rebind (to False) re-enables load_license_from_env,
                                  allowing env-var token injection after boot.
      • ``SESSION_SERVER_KEY_RING``— gc.get_referents mutation can inject an attacker
                                  Ed25519 key into the key ring for SesT verification.

    This list is the authoritative acceptance record for ADR-0139.  No individual
    countermeasure exists for any entry above; the out-of-process enforcer (Option A
    in ADR-0139) is the only structural fix — not yet implemented.
    """
    _salt = os.urandom(32)   # closure-captured; not accessible as a module attr

    def _compute(lic: Any) -> "str | object | None":
        """Derive a salted SHA-256 fingerprint of the active licence dict.

        Returns None when lic is None (no licence loaded = canary not applicable).
        Returns _CANARY_UNCOMPUTABLE when serialization fails for a non-None
        licence — consistent failures compare equal; a gc-mutation that changes
        serializability yields a different value and is detected.
        """
        if lic is None:
            return None
        try:
            raw = _json.dumps(_unproxy(lic), sort_keys=True, default=str).encode()
            return _hashlib.sha256(_salt + raw).hexdigest()
        except Exception:  # noqa: BLE001
            return _CANARY_UNCOMPUTABLE

    return _compute


_compute_canary = _make_compute_canary()


def _set_active_license(raw: "dict[str, Any] | None") -> None:
    """Atomically set _ACTIVE_LICENSE and its tamper-canary.

    ALWAYS call this instead of assigning _ACTIVE_LICENSE directly — the canary
    must stay in sync with the value or get_limit() detects tampering, falls back
    to FREE_TIER, and emits a CRITICAL audit event.
    """
    global _ACTIVE_LICENSE, _ACTIVE_LICENSE_CANARY
    if raw is not None:
        try:
            _ACTIVE_LICENSE = _freeze_license(raw)
        except RecursionError:
            # A crafted JWT with ~500 levels of nesting triggers Python's recursion
            # limit in _freeze_license / _unproxy.  Reject the token rather than
            # crashing and locking the process at free-tier permanently.
            log.warning(
                "license: token payload nesting exceeds recursion limit — "
                "rejected (ADR-0144 F-05).  Free tier active."
            )
            _ACTIVE_LICENSE = None
    else:
        _ACTIVE_LICENSE = None
    _ACTIVE_LICENSE_CANARY = _compute_canary(_ACTIVE_LICENSE)


# ── Audit helper ─────────────────────────────────────────────────────────────

def _audit(event_type: str, **details: Any) -> None:
    """Emit a licence event to the L16 hash-chained audit log.

    Best-effort: never raises.  Allowed fields: tier, jti (8 chars), feature,
    limit_value, requested_value.  Never log the full token string.
    """
    try:
        _here = Path(__file__).resolve()
        shared = _here.parents[2] / "bridges" / "shared"
        if str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        from audit import audit_event  # type: ignore[import]
        # audit_event is (event_type, *, details=..., severity=..., channel=...):
        # it has NO domain kwargs. Forwarding tier/jti/feature/reason/... as bare
        # kwargs raises TypeError, which the except below would SILENTLY SWALLOW —
        # dropping every license-enforcement audit event that carries a detail
        # field (invalid/expired/over-limit/device-mismatch/...). Pass the domain
        # fields via details= instead (audit_event strips reserved keys itself).
        # Severity comes from the _VOICE_EVENT_SEVERITY registry per event_type.
        # R6 #7: canonicalize the tier at this single chokepoint so EVERY license
        # audit event surfaces the canonical tier (member/free) into the L16
        # chain, never a raw legacy name like "universal". Fixes ~10 call sites at
        # the root instead of per-site.
        _det = dict(details)
        if _det.get("tier"):
            try:
                _det["tier"] = canonical_tier(str(_det["tier"]))
            except Exception:  # noqa: BLE001
                pass
        audit_event(event_type, details=_det)
    except Exception:  # noqa: BLE001
        log.debug("license audit emit failed (non-fatal): %s", event_type)


def _engage_tamper_response(reason: str) -> None:
    """Fire the ADR-0154 M4 (compliant) tamper response. Best-effort, never raises."""
    try:
        from .tamper_response import engage as _tr_engage
        _tr_engage(reason)
    except Exception:  # noqa: BLE001
        log.debug("tamper_response engage failed (non-fatal)")


def _set_feature_root_key(license_jwt: "str | None") -> None:
    """Install the ADR-0154 OTA feature root key. Best-effort, never raises.

    Mirrors the LSAD chain-DNA seeding: a paid token installs the paid root,
    no token installs the public free-tier root. The lattice keeps working with
    stable keys on free tier, so M3 sessions / M5 path-gate never break when no
    license is present.
    """
    try:
        from .feature_lattice import set_feature_root_key as _fl_set
        _fl_set(license_jwt)
    except Exception:  # noqa: BLE001
        log.debug("feature_lattice root-key install failed (non-fatal)")


# ── Token verification ────────────────────────────────────────────────────────

def _b64_decode(s: str) -> bytes:
    pad = s + "==" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(pad)


def _verify_ed25519(token: str) -> dict[str, Any] | None:
    """Verify an Ed25519-signed CORVIN token and return the claims, or None.

    Key selection (ADR-0095 M1 / ADR-0098 P3 / ADR-0102, CVE-CORVIN-003 — see
    the inline routing comment below for the authoritative table):
      kid absent       → CORVIN_PUBLIC_KEY_B64 (operator-issued tokens)
      kid "sess-*"     → SESSION_SERVER_KEY_RING (server session permits)
      kid "lic-*"      → SESSION_SERVER_KEY_RING (server license JWTs; falls
                         back to "sess-v1" when the exact kid is absent)
      any other value  → CORVIN_PUBLIC_KEY_B64 (legacy operator tokens)

    Revoked kids (from the trust manifest) are rejected before signature
    verification; for lic-* tokens the resolved fallback kid is also checked.
    """
    try:
        from cryptography.exceptions import InvalidSignature  # type: ignore
    except ImportError:
        log.warning(
            "license: 'cryptography' package not installed — "
            "token verification disabled, Free tier applies."
        )
        return None

    if not isinstance(token, str) or not token.startswith("CORVIN-"):
        return None
    raw = token[len("CORVIN-"):]
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts

    # Decode header first to determine which key to use.
    try:
        header = json.loads(_b64_decode(header_b64))
        kid = header.get("kid")  # no default — absent kid routes to operator key (CVE-CORVIN-003)
    except Exception:  # noqa: BLE001
        return None

    # Key routing (ADR-0098 P3 + ADR-0102, CVE-CORVIN-003):
    #   kid absent       → CORVIN_PUBLIC_KEY_B64 (operator-issued tokens from signer.py)
    #   kid "sess-*"     → SESSION_SERVER_KEY_RING (server session permits)
    #   kid "lic-*"      → SESSION_SERVER_KEY_RING (server license JWTs, same keypair)
    #   any other value  → CORVIN_PUBLIC_KEY_B64 (legacy operator tokens)
    # ADR-0139: initialise before the branch chain so the revocation check below
    # never hits UnboundLocalError. Without this default the sess-* branch left
    # the name unbound; the resulting error was swallowed by the broad except
    # around the revocation check, silently skipping revocation for the most
    # common token type (server session permits) — a revocation bypass.
    _extra_revocation_kid = None
    if kid is None:
        pub_key_b64 = _get_operator_pubkey_b64()
    elif kid.startswith("sess-"):
        pub_key_b64 = SESSION_SERVER_KEY_RING.get(kid)
        if pub_key_b64 is None:
            log.warning("license: unknown session key kid=%r — key ring may need update", kid)
            return None
        # LIC-4: refuse a placeholder/test session key — mirrors the operator guard.
        if _is_placeholder_session_key(pub_key_b64):
            log.critical(
                "license: session key kid=%r is a SHIPPED PLACEHOLDER/test key — "
                "refusing to verify session tokens (Free tier).", kid,
            )
            _audit("license.invalid_token", reason="placeholder_session_key")
            return None
    elif kid.startswith("lic-"):
        pub_key_b64 = SESSION_SERVER_KEY_RING.get(kid) or SESSION_SERVER_KEY_RING.get("sess-v1")
        if pub_key_b64 is None:
            log.warning("license: lic-* token but session key ring is empty — key ring may need update")
            return None
        # LIC-4: refuse a placeholder/test session key — mirrors the operator guard.
        if _is_placeholder_session_key(pub_key_b64):
            log.critical(
                "license: lic-* token resolved to a SHIPPED PLACEHOLDER/test session "
                "key (kid=%r) — refusing to verify (Free tier).", kid,
            )
            _audit("license.invalid_token", reason="placeholder_session_key")
            return None
        # ADR-0138 review: if lic-* fell back to sess-v1, track both so the
        # revocation check below also tests the resolved key identity.
        _extra_revocation_kid = None if kid in SESSION_SERVER_KEY_RING else "sess-v1"
    else:
        pub_key_b64 = _get_operator_pubkey_b64()

    # A2 (ADR-0138 M6): check revoked_kids from trust manifest.
    # A stale revocation list can only MISS new revocations, never legitimately
    # un-revoke a kid — so honour the last-known list regardless of staleness.
    # ADR-0138 review: for lic-* tokens that fall back to sess-v1, also check
    # "sess-v1" against revoked_kids — otherwise rotating to a "lic-FORGED" kid
    # permanently bypasses revocation of the underlying sess-v1 key.
    if kid is not None:
        try:
            from .manifest import load_cached_manifest as _lcm
            _mfst = _lcm(_CORVIN_HOME_SNAPSHOT)
            if _mfst is not None:
                _rk = _mfst.get("revoked_kids", [])
                if isinstance(_rk, list):
                    _kids_to_check = {kid}
                    if _extra_revocation_kid is not None:
                        _kids_to_check.add(_extra_revocation_kid)
                    if _kids_to_check & set(_rk):
                        log.warning(
                            "license: kid=%r (resolved: %s) in manifest revoked_kids — rejecting token",
                            kid, sorted(_kids_to_check),
                        )
                        _audit("license.invalid_token")
                        return None
        except Exception as _e_rev:  # noqa: BLE001
            # P2-B (security review 2026-06-18): emit a WARNING when the
            # revocation manifest is unreachable. A persistent failure may
            # indicate a network block intended to keep a revoked token alive.
            log.warning(
                "license: revocation manifest unavailable (%s) — "
                "continuing with stale/empty list; new revocations may be missed",
                type(_e_rev).__name__,
            )

    # FND-22: refuse a placeholder operator key — a build that shipped it
    # unchanged must not accept operator tokens (potential forgery vector).
    if _is_placeholder_key(pub_key_b64):
        global _PLACEHOLDER_KEY_WARNED  # noqa: PLW0603
        if not _PLACEHOLDER_KEY_WARNED:
            log.critical(
                "license: operator public key is the SHIPPED PLACEHOLDER — refusing "
                "to verify operator tokens (Free tier). Set CORVIN_PUBLIC_KEY_B64 to "
                "the real CorvinLabs key. (logged once per process)"
            )
            _PLACEHOLDER_KEY_WARNED = True
        return None

    try:
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        pub = load_der_public_key(_b64_decode(pub_key_b64))
        signing_input = f"{header_b64}.{payload_b64}".encode()
        sig = _b64_decode(sig_b64)
        pub.verify(sig, signing_input)
    except (InvalidSignature, Exception):  # noqa: BLE001
        return None

    try:
        claims = json.loads(_b64_decode(payload_b64))
    except Exception:  # noqa: BLE001
        return None

    return claims


def _check_instance_id_bound(claims: dict[str, Any]) -> bool:
    """Return True when the token's instance_id_bound matches the local installation.

    The Personal tier embeds ``limits.instance_id_bound`` in the SesT to
    enforce one-installation-per-licence.  If the claim is absent the token
    is not bound (Pro/Business/Enterprise are never instance-bound).

    Fail-closed: if the local instance_id cannot be read the binding check
    fails and the caller degrades to Free tier.
    """
    bound_id: str | None = claims.get("limits", {}).get("instance_id_bound")
    if bound_id is None:
        return True  # not bound — valid on any installation
    try:
        corvin_home = _CORVIN_HOME_SNAPSHOT or _corvin_home_resolved()
        iid_file = corvin_home / "global" / "instance_id.json"
        if not iid_file.exists():
            return False  # no instance_id on record → reject binding
        # ADR-0138 M3 D1: world/group-readable instance_id.json indicates possible
        # tampering — reject binding check rather than trusting a potentially
        # attacker-controlled UUID. Skipped on Windows: NTFS has no POSIX
        # group/other bits, so st_mode reports a permissive-looking value there
        # regardless of the file's real ACLs — this would reject every bound
        # licence on every Windows install.
        if not sys.platform.startswith("win") and iid_file.stat().st_mode & 0o077:
            log.warning("license: instance_id.json has permissive mode — possible tamper, rejecting binding")
            _audit("license.instance_id_mode_error")
            return False
        data: dict[str, Any] = json.loads(iid_file.read_text(encoding="utf-8"))
        local_id: str = data.get("instance_id", "")
        return hmac.compare_digest(local_id or "", bound_id or "")
    except Exception:  # noqa: BLE001
        return False  # cannot verify → reject


def _local_device_fp() -> str:
    """Compute device fingerprint from hardware.

    Delegates to device_fp.compute_device_fp() — single source of truth
    shared with sob.py and session_refresh.py (FND-LIC-07 fix).
    """
    from device_fp import compute_device_fp as _cfp  # type: ignore
    return _cfp()


def _check_device_fp(claims: dict[str, Any]) -> bool:
    """Return True when the session permit's device_fp matches this machine.

    If the permit contains no device_fp claim (pre-ADR-0098 subscriptions or
    free-tier tokens) the check is skipped and True is returned.
    Fail-closed: if the local fingerprint cannot be computed, returns False.
    """
    permit_device_fp: str | None = claims.get("device_fp")
    if not permit_device_fp:
        # member is single-device (ADR-0098), but ONLY the device-bound
        # session_permit carries device_fp. The long-lived emailed
        # type:"license" entitlement token is NOT device-bound by design and
        # never carries one — failing it closed wrongly rejected EVERY legitimate
        # member license (review R1 #14, cross-repo: Corvin-Features mints
        # type:"license" without device_fp). Scope the member fail-closed to the
        # session_permit, where absent device_fp really is an issuance bug.
        # Compare the CANONICAL tier (review R1 #12): a legacy tier="universal"
        # session_permit canonicalizes to "member" and must NOT bypass the
        # single-device binding (the literal "member" check let it through).
        if (canonical_tier(str(claims.get("tier", ""))) == "member"
                and claims.get("type") == "session_permit"):
            log.warning(
                "license: member-tier session permit missing device_fp claim — "
                "expected device binding per ADR-0098. Free tier active."
            )
            _audit(
                "license.device_fp_missing",
                jti=str(claims.get("jti", ""))[:8],
                tier="member",
            )
            return False
        return True  # emailed license / non-permit token — valid on any machine
    try:
        import hmac as _hmac
        local_fp = _local_device_fp()
        match = _hmac.compare_digest(permit_device_fp, local_fp)
        if not match:
            log.warning(
                "license: session permit device_fp mismatch — "
                "this permit was issued for a different device. Free tier active."
            )
            _audit(
                "license.device_fp_mismatch",
                jti=str(claims.get("jti", ""))[:8],
                tier=claims.get("tier", ""),
            )
        return match
    except Exception as exc:
        log.warning("license: device_fp check failed (%s) — Free tier active.", exc)
        return False


def _fetch_revoked_fps() -> list[str] | None:
    """Fetch the per-token fingerprint revocation list from Corvin-Features.

    Returns the list of revoked token fingerprints on success, or None when
    the endpoint is unreachable and no usable cache exists.  The result is
    written to a local cache file so the last-known revocation list survives
    process restarts and brief network outages.

    Individual token revocation (ADR-0102): Corvin-Features adds a SHA-256[:32]
    fingerprint to GET /v1/licenses/revoked on subscription cancellation.  The
    trust manifest checks key-level (kid) revocation; this function checks the
    per-token level so a cancelled subscription cannot be used until the signing
    key is rotated.
    """
    import urllib.request as _urllib_req  # stdlib — no extra deps
    import urllib.error as _urllib_err

    corvin_home = _CORVIN_HOME_SNAPSHOT or _corvin_home_resolved()
    cache_dir = corvin_home / "global" / "license"
    cache_file = cache_dir / ".revoked_fps_cache.json"

    # Try to serve from a fresh-enough cache first.
    try:
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            age = time.time() - float(cached.get("ts", 0))
            if age < _REVOCATION_CACHE_TTL_SECONDS:
                return list(cached.get("fps", []))
    except Exception:
        pass

    # Fetch a fresh list from Corvin-Features. Enforcement path: the base URL is
    # test-mode-gated + import-snapshotted (see _features_base_url) so a post-boot
    # CORVIN_FEATURES_URL mutation cannot redirect revocation to a mock/dead host.
    base_url = _features_base_url()
    url = f"{base_url}/v1/licenses/revoked"
    try:
        with _urllib_req.urlopen(url, timeout=5) as resp:  # noqa: S310 (http allowed for internal endpoint)
            data = json.loads(resp.read())
        fps: list[str] = data.get("revoked", [])
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({"ts": time.time(), "fps": fps}),
                encoding="utf-8",
            )
        except Exception:
            pass
        return fps
    except Exception as exc:
        log.warning(
            "license: revocation list unavailable (%s) — using stale cache if available",
            type(exc).__name__,
        )
        # Fall back to stale cache regardless of age.
        try:
            if cache_file.exists():
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                return list(cached.get("fps", []))
        except Exception:
            pass
        return None  # no cache, no network → caller decides


def _is_token_fp_revoked(token: str) -> bool:
    """Return True when the token's SHA-256[:32] fingerprint is in the revocation list.

    Fail-open only when both the network AND the local cache are unavailable —
    a warning is emitted in that case.  When the revocation list is fetched (or
    served from cache) and the fingerprint is present, the token is rejected.
    """
    import hashlib as _hl_rev
    fp = _hl_rev.sha256(token.encode()).hexdigest()[:32]
    revoked = _fetch_revoked_fps()
    if revoked is None:
        log.warning(
            "license: cannot verify revocation status (network + no cache) — "
            "accepting token. fp=%s", fp,
        )
        return False
    is_revoked = fp in revoked
    if is_revoked:
        log.warning("license: token fingerprint %s is individually revoked", fp)
    return is_revoked


def _validate_claims(claims: dict[str, Any]) -> dict[str, Any] | None:
    """Apply semantic validation on top of the signature check."""
    if not isinstance(claims, dict):
        return None
    if claims.get("iss") != "corvinlabs.io":
        _audit("license.invalid_token", reason="wrong_issuer")
        return None
    if claims.get("type") not in ("session", "license", "session_permit"):
        _audit("license.invalid_token", reason="wrong_type")
        return None
    now = time.time()
    exp = claims.get("exp")
    if exp is None:
        # All validly-minted tokens carry an exp claim.  A missing exp is a
        # signing-tool bug or a forgery attempt — reject rather than granting
        # eternal validity (security-audit finding 2026-06-28).
        _audit("license.invalid_token", reason="no_expiry")
        return None
    if exp <= now:
        _audit(
            "license.expired",
            jti=str(claims.get("jti", ""))[:8],
            tier=claims.get("tier", ""),
        )
        return None
    # Extra guard: subscription end date embedded in SesT
    sal = claims.get("subscription_active_until")
    if sal is not None and sal < now:
        _audit(
            "license.subscription_lapsed",
            jti=str(claims.get("jti", ""))[:8],
            tier=claims.get("tier", ""),
        )
        return None
    return claims


# ── Grace period (ADR-0095 M1) ────────────────────────────────────────────────

def _config_dir() -> Path:
    """Return the corvin-voice config directory, honouring XDG_CONFIG_HOME."""
    if _CONFIG_DIR_SNAPSHOT is not None:
        return _CONFIG_DIR_SNAPSHOT
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "corvin-voice"
    return Path.home() / ".config" / "corvin-voice"


def _check_session_grace_period(token_exp: int) -> bool:
    """True if the expired session permit is still within the 6-hour grace window.

    Anchored to the signed permit's own `exp` claim — not to the writable
    session.meta file.  This closes LIC-003 (file owner can rewrite
    refreshed_at to extend the window indefinitely) and LIC-006
    (XDG_CONFIG_HOME desync allows crafting session.meta at a different path
    while validator.py reads session.key from a different location).
    """
    return (time.time() - token_exp) <= _GRACE_PERIOD_SECONDS


# ── Token discovery ───────────────────────────────────────────────────────────

def _find_token() -> str | None:
    """Return the first non-empty licence token string found, or None."""
    # 1. Env var
    env = os.environ.get("CORVIN_LICENSE_KEY", "").strip()
    if env:
        return env

    # 2. Session key file written by refresh daemon (XDG-aware path — LIC-006)
    # Windows note: the mode check below is skipped on Windows — NTFS has no
    # POSIX group/other bits, so os.stat().st_mode reports a permissive-looking
    # value there regardless of the file's real ACLs. Without this guard, a
    # perfectly valid session.key would be rejected on every Windows install,
    # short-circuiting this function with `return None` and never falling
    # through to check global/license.key below.
    session_file = _config_dir() / "session.key"
    if session_file.exists():
        try:
            st = session_file.stat()
            if not sys.platform.startswith("win") and st.st_mode & 0o077:
                log.warning(
                    "license: session.key is group/world readable — rejecting (mode 0%03o)",
                    st.st_mode & 0o777,
                )
                return None
            t = session_file.read_text().strip()
            if t:
                return t
        except OSError:
            pass

    # 3. corvin_home global
    try:
        corvin_home = _CORVIN_HOME_SNAPSHOT or _corvin_home_resolved()
        global_key = corvin_home / "global" / "license.key"
        if global_key.exists():
            try:  # F-02 (ADR-0144): mode parity with session.key/instance_id.json —
                # this must actually REJECT on a permissive mode, not just warn.
                # A once-permissive license.key (bad umask, backup restore) that
                # is merely warned-about but still trusted stays a live, readable
                # credential for any co-resident local user indefinitely.
                _gk_mode = global_key.stat().st_mode & 0o777  # single stat call
                if not sys.platform.startswith("win") and _gk_mode & 0o077:
                    log.warning(
                        "license: global/license.key is group/world readable — "
                        "rejecting (mode 0%03o)",
                        _gk_mode,
                    )
                    return None
            except OSError:
                pass
            t = global_key.read_text().strip()
            if t:
                return t
    except OSError:
        pass

    return None


def _find_token_disk_only() -> str | None:
    """Return the first non-empty token from DISK ONLY — never from env vars.

    Used exclusively by reload_from_disk() so that a post-boot mutation of
    CORVIN_LICENSE_KEY cannot influence a UI-triggered reload.  The env-var
    path is intentionally absent here; it only applies at boot via _find_token().
    """
    # 1. Session key file written by refresh daemon (XDG-aware path — LIC-006)
    # See the matching comment in _find_token() re: the Windows mode-check guard.
    session_file = _config_dir() / "session.key"
    if session_file.exists():
        try:
            st = session_file.stat()
            if not sys.platform.startswith("win") and st.st_mode & 0o077:
                log.warning(
                    "license: session.key is group/world readable — rejecting (mode 0%03o)",
                    st.st_mode & 0o777,
                )
                return None
            t = session_file.read_text().strip()
            if t:
                return t
        except OSError:
            pass

    # 2. corvin_home global license key — use snapshot only, never env var.
    # reload_from_disk() guards on _LICENSE_INITIALIZED (snapshot already set);
    # if called without a snapshot (should not happen), fall back to ~/.corvin
    # without reading env so the disk-only contract is preserved.
    try:
        corvin_home = _CORVIN_HOME_SNAPSHOT if _CORVIN_HOME_SNAPSHOT is not None else _corvin_home_resolved()
        global_key = corvin_home / "global" / "license.key"
        if global_key.exists():
            try:  # F-02 (ADR-0144): mode parity with session.key/instance_id.json —
                # this must actually REJECT on a permissive mode, not just warn.
                _gk_mode = global_key.stat().st_mode & 0o777  # single stat call
                if not sys.platform.startswith("win") and _gk_mode & 0o077:
                    log.warning(
                        "license: global/license.key is group/world readable — "
                        "rejecting (mode 0%03o)",
                        _gk_mode,
                    )
                    return None
            except OSError:
                pass
            t = global_key.read_text().strip()
            if t:
                return t
    except OSError:
        pass

    return None


# ── ADR-0136: Instance-seed initialization for free-tier chains ──────────────

def _init_instance_seed() -> None:
    """Initialize the per-instance free-tier DNA seed at boot (ADR-0136 M1).

    Best-effort — never raises.  Silently skipped when security_events or
    chain_dna are not importable (e.g. test environments without forge on path).
    """
    try:
        _here_v = Path(__file__).resolve()
        _forge_inner = _here_v.parents[1] / "forge" / "forge"
        if str(_forge_inner) not in sys.path:
            sys.path.insert(0, str(_forge_inner))
        from chain_dna import ensure_instance_seed, derive_seed_instance  # type: ignore[import]
        from security_events import set_instance_dna_seed  # type: ignore[import]
        _corvin_home = _CORVIN_HOME_SNAPSHOT or _corvin_home_resolved()
        seed_path = _corvin_home / "global" / "instance_seed.key"
        _g1_seed_existed = seed_path.exists()
        instance_seed = ensure_instance_seed(seed_path)
        set_instance_dna_seed(derive_seed_instance(instance_seed))
        # G1 (ADR-0138 M5): detect seed rotation on an existing audit chain.
        # A newly created seed on a chain with prior events means CLAG DNA
        # verification will fail for all historical events — surface as CRITICAL.
        if not _g1_seed_existed and seed_path.exists():
            _g1_tenant = (os.environ.get("CORVIN_TENANT_ID", "") or "_default").strip() or "_default"
            _g1_chain = _corvin_home / "tenants" / _g1_tenant / "global" / "forge" / "audit.jsonl"
            try:
                if _g1_chain.exists() and _g1_chain.stat().st_size > 0:
                    log.critical(
                        "license: instance_seed.key was NEWLY CREATED on an existing "
                        "audit chain — CLAG DNA verification will fail for historical "
                        "events (ADR-0138 G1). Possible G-vector attack or shared storage."
                    )
                    _audit("audit.instance_seed_rotated")
            except OSError:
                pass
    except Exception as _exc:  # noqa: BLE001
        log.debug("ADR-0136 instance seed init failed (non-fatal): %s", _exc)


# ── Public boot API ───────────────────────────────────────────────────────────

def load_license_from_env(*, force: bool = False) -> None:
    """Discover and activate a licence token.

    Call once at adapter boot.  Sets the module-level _ACTIVE_LICENSE.
    Emits audit events: license.loaded / license.free_tier / license.expired /
    license.invalid_token.

    Idempotent by default (ADR-0138 M1 F2): subsequent calls from injected code
    are no-ops.  force=True is for tests only and requires CORVIN_INTEGRATION_TEST.
    """
    global _LICENSE_LOADED_AT
    global _CORVIN_HOME_SNAPSHOT, _CONFIG_DIR_SNAPSHOT, _AUDIT_PATH_SNAPSHOT, _LICENSE_INITIALIZED

    # F2: idempotency guard — once initialized, ignore all re-entrant calls
    # (force=True is a test-only escape hatch; guard it with the test env var).
    if _LICENSE_INITIALIZED:
        if not force:
            return
        if not _INTEGRATION_TEST_SNAP:
            raise RuntimeError(
                "load_license_from_env(force=True) is a test-only escape hatch; "
                "set CORVIN_INTEGRATION_TEST=1 to use it."
            )
    _LICENSE_INITIALIZED = True

    # C1/C2/C4: snapshot env vars write-once so post-boot mutations cannot
    # redirect path resolution to an attacker-controlled directory.
    if _CORVIN_HOME_SNAPSHOT is None:
        _ch_env = os.environ.get("CORVIN_HOME", "").strip()
        _CORVIN_HOME_SNAPSHOT = Path(os.path.expanduser(_ch_env)) if _ch_env else _corvin_home_resolved()
    if _CONFIG_DIR_SNAPSHOT is None:
        _xdg = os.environ.get("XDG_CONFIG_HOME")
        _CONFIG_DIR_SNAPSHOT = (
            (Path(_xdg) / "corvin-voice") if _xdg
            else (Path.home() / ".config" / "corvin-voice")
        )
    if _AUDIT_PATH_SNAPSHOT is None:
        _ap = os.environ.get("VOICE_AUDIT_PATH")
        _AUDIT_PATH_SNAPSHOT = Path(_ap) if _ap else None

    # ADR-0154 M1/M3/M5: reset the OTA feature root key to the public free-tier
    # value before resolving. Successful paid activation below upgrades it; any
    # free/invalid path leaves it free so a stale paid key is never retained.
    _set_feature_root_key(None)

    token = _find_token()
    # A3 (ADR-0138 M5): emit token source for forensic auditability — ops can
    # detect env-var bypass (D2 vector) without exposing the token value itself.
    if os.environ.get("CORVIN_LICENSE_KEY", "").strip():
        _token_source = "env_var"
    elif (_CONFIG_DIR_SNAPSHOT / "session.key").exists():
        _token_source = "session_key"
    elif (_CORVIN_HOME_SNAPSHOT / "global" / "license.key").exists():
        _token_source = "corvin_home"
    else:
        _token_source = "none"
    _audit("license.token_source", source=_token_source)
    if not token:
        log.info("license: no key found — Free tier active")
        _audit("license.free_tier")
        _set_active_license(None)
        _init_instance_seed()
        return

    claims = _verify_ed25519(token)
    if claims is None:
        log.warning("license: token present but signature invalid — Free tier active")
        _audit("license.invalid_token")
        _set_active_license(None)
        _init_instance_seed()
        return

    # ADR-0102: per-token fingerprint revocation check against Corvin-Features.
    # Catches subscription cancellation independently of signing-key rotation
    # (the trust manifest handles key-level revocation; this handles token-level).
    if _is_token_fp_revoked(token):
        log.warning("license: token is individually revoked — Free tier active")
        _audit("license.revoked")
        _set_active_license(None)
        _init_instance_seed()
        return

    validated = _validate_claims(claims)
    if validated is None:
        # For expired server-issued session_permits: honour the grace period
        # (up to 6 hours per ADR-0098) so transient network outages don't break working installs.
        if (
            claims is not None
            and claims.get("iss") == "corvinlabs.io"       # CVE-CORVIN-001: issuer guard
            and claims.get("type") == "session_permit"
            and claims.get("exp") is not None
            and claims.get("exp") < time.time()
        ):
            if _check_session_grace_period(int(claims["exp"])):
                # ADR-0095-R1: enforce instance_id_bound even for expired permits.
                if not _check_instance_id_bound(claims):
                    log.warning(
                        "license: grace-period session permit has instance_id mismatch — "
                        "Free tier active."
                    )
                    _audit(
                        "license.instance_id_mismatch",
                        jti=str(claims.get("jti", ""))[:8],
                        tier=claims.get("tier", ""),
                    )
                    _set_active_license(None)
                    return
                # ADR-0098: enforce device_fp even for grace-period permits.
                if not _check_device_fp(claims):
                    log.warning(
                        "license: grace-period session permit has device_fp mismatch — "
                        "Free tier active."
                    )
                    _audit(
                        "license.device_fp_mismatch",
                        jti=str(claims.get("jti", ""))[:8],
                        tier=claims.get("tier", ""),
                    )
                    _set_active_license(None)
                    return
                # LIC-005: re-check subscription_active_until in the grace path.
                # The regular validation path returns None before reaching this check,
                # so an expired subscription could otherwise slide through on grace.
                sal = claims.get("subscription_active_until")
                if sal is not None and sal < time.time():
                    log.warning(
                        "license: session permit in grace period but subscription has lapsed "
                        "(subscription_active_until=%d) — Free tier active", int(sal),
                    )
                    _audit(
                        "license.subscription_lapsed",
                        jti=str(claims.get("jti", ""))[:8],
                        tier=claims.get("tier", ""),
                    )
                    _set_active_license(None)
                    return
                log.warning(
                    "license: session permit expired but within 6-hour grace period — "
                    "using cached tier=%s", claims.get("tier", "?"),
                )
                _audit(
                    "license.session_grace_period",
                    tier=claims.get("tier", ""),
                    jti=str(claims.get("jti", ""))[:8],
                )
                _set_active_license(claims)
                _set_feature_root_key(token)  # ADR-0154 M1/M3/M5 paid root
                _LICENSE_LOADED_AT = time.time()
                return
            log.warning("license: session permit expired and grace period elapsed — Free tier active")
            _audit(
                "license.grace_period_elapsed",
                jti=str(claims.get("jti", ""))[:8],
                tier=str(claims.get("tier", "")),
            )
        _set_active_license(None)
        return

    if not _check_instance_id_bound(validated):
        log.warning(
            "license: instance_id mismatch — Personal licence bound to a different "
            "installation. Free tier active."
        )
        _audit(
            "license.instance_id_mismatch",
            jti=str(validated.get("jti", ""))[:8],
            tier=validated.get("tier", ""),
        )
        _set_active_license(None)
        return

    # ADR-0098: if the session permit embeds a device_fp claim (member tier
    # device-bound subscriptions), verify it matches this machine's hardware.
    # Fail-closed: mismatched device → Free tier.
    if not _check_device_fp(validated):
        _set_active_license(None)
        return

    _set_active_license(validated)
    _set_feature_root_key(token)  # ADR-0154 M1/M3/M5 paid root (token in scope)
    _LICENSE_LOADED_AT = time.time()
    log.info(
        "license: loaded — tier=%s jti=%s",
        validated.get("tier", "?"),
        str(validated.get("jti", ""))[:8],
    )
    _audit(
        "license.loaded",
        tier=validated.get("tier", ""),
        jti=str(validated.get("jti", ""))[:8],
    )

    # ADR-0132 LSAD: seed the audit chain DNA with the paid-tier key at load time.
    # Best-effort — a failure here must never block the license from activating.
    # Pre-initialize to None so the CLAG block below can guard against
    # UnboundLocalError when the LSAD try-block raises before these assignments.
    _audit_p: "Path | None" = None
    _dna_seed: "str | None" = None
    try:
        _here_v = Path(__file__).resolve()
        _forge_inner = _here_v.parents[1] / "forge" / "forge"
        _bridges_shared = _here_v.parents[1] / "bridges" / "shared"
        for _p in (_forge_inner, _bridges_shared):
            if str(_p) not in sys.path:
                sys.path.insert(0, str(_p))
        from chain_dna import derive_seed_paid as _dna_derive_paid  # type: ignore[import]
        from security_events import set_chain_dna_seed as _dna_seed_set, write_event as _dna_write  # type: ignore[import]
        from instance_identity import get_instance_id as _get_iid  # type: ignore[import]
        from paths import corvin_home as _dna_corvin_home  # type: ignore[import]
        _dna_seed = _dna_derive_paid(token, _get_iid())
        _dna_seed_set(_dna_seed)
        # Flush stale CIT cache so the next gate() call from any static
        # layer_id does not fail cit_tampered.  Without this, a layer that
        # called gate() before license activation holds a free-tier CIT;
        # after set_chain_dna_seed() switches to paid, step 0.5 re-verifies
        # that CIT with the new paid key → HMAC mismatch → false-positive
        # ChainIntegrityFailure for the 5-minute CIT TTL window (ADR-0133).
        try:
            from clag import clear_shadow_hashes as _clag_clear  # type: ignore[import]
            _clag_clear()
        except Exception:  # noqa: BLE001
            pass  # clag not installed — nothing to clear
        # C4: use snapshot instead of live env read (ADR-0138 M1)
        _audit_p = _AUDIT_PATH_SNAPSHOT if _AUDIT_PATH_SNAPSHOT is not None else (
            _dna_corvin_home() / "global" / "forge" / "audit.jsonl"
        )
        _dna_write(
            _audit_p,
            "license.chain_dna_seeded",
            details={
                "tier": validated.get("tier", ""),
                "seed_prefix": _dna_seed[:16],
            },
        )
    except Exception as _lsad_exc:  # noqa: BLE001
        log.warning("LSAD seed setup failed (non-fatal): %s", _lsad_exc)

    # ADR-0133 CLAG: gate the chain at license activation — proves the chain
    # was intact at the exact moment the license took effect.  The gate()
    # call writes an audit.cit_issued event carrying the current tier DNA
    # as a cryptographic snapshot.  Best-effort: a failure emits a WARNING
    # but must never block the license from activating.
    try:
        from clag import gate as _clag_gate  # type: ignore[import]
        if _audit_p is not None:
            _clag_gate(_audit_p, "L16.license_load", dna_seed=_dna_seed)
    except Exception as _clag_exc:  # noqa: BLE001
        log.warning("CLAG gate at license load failed (non-fatal): %s", _clag_exc)


def reload_from_disk() -> None:
    """Re-read the license key from disk and update _ACTIVE_LICENSE in-process.

    Unlike load_license_from_env(), this function bypasses the idempotency guard
    and is safe to call after the initial boot load (e.g. when the operator
    applies a new license key via the console UI). The path snapshots set by
    load_license_from_env() are reused — they are frozen and cannot be redirected
    by post-boot env mutations (C1/C2/C4 from ADR-0138).

    Raises RuntimeError if called before load_license_from_env() has run (since
    the path snapshots would not yet be set).

    ADR-0144 F-04 note: this is the correct hook for console /license/apply.
    Env-var isolation: unlike load_license_from_env(), this function uses
    _find_token_disk_only() so post-boot mutations of CORVIN_LICENSE_KEY cannot
    influence a UI-triggered reload (closes the two-step env-var+reload attack).
    Rate-limited: min _MIN_RELOAD_INTERVAL_SECONDS between calls, but ONLY for
    redundant re-reads of unchanged content — a reload that would pick up a
    genuinely NEW on-disk token always goes through, regardless of timing.
    """
    global _LICENSE_LOADED_AT, _LAST_RELOAD_AT, _LAST_LOADED_TOKEN_HASH

    if not _LICENSE_INITIALIZED or _CORVIN_HOME_SNAPSHOT is None:
        raise RuntimeError(
            "reload_from_disk() called before load_license_from_env() — "
            "call load_license_from_env() at adapter boot first."
        )

    # Disk-only: never read CORVIN_LICENSE_KEY env var here (two-step attack vector).
    # Read once, up front, so the throttle-bypass check and the actual load below
    # use the exact same content (no re-read / TOCTOU gap).
    token = _find_token_disk_only()
    token_hash = _hashlib.sha256(token.encode("utf-8")).hexdigest() if token else None
    content_changed = token_hash != _LAST_LOADED_TOKEN_HASH

    # Rate limiter: ignore rapid reload bursts to prevent DoS via the console UI —
    # but only when the on-disk content is unchanged from the last successful
    # load. reload_from_disk() is also called on every authenticated session op
    # (auth.py::_compute_lic_proof), so an unconditional throttle silently
    # swallowed the one reload that actually matters: the explicit "Apply Key"
    # reload immediately after writing a NEW token, which in real usage almost
    # always lands inside the cooldown window opened by an incidental
    # per-request call moments earlier.
    _now = time.time()
    if not content_changed and _LAST_RELOAD_AT > 0 and (_now - _LAST_RELOAD_AT) < _MIN_RELOAD_INTERVAL_SECONDS:
        _remaining = _MIN_RELOAD_INTERVAL_SECONDS - (_now - _LAST_RELOAD_AT)
        log.warning(
            "license: reload_from_disk() called too soon (%.1fs ago, min %.1fs) — "
            "ignoring. Wait %.1fs.",
            _now - _LAST_RELOAD_AT, _MIN_RELOAD_INTERVAL_SECONDS, _remaining,
        )
        _audit("license.reload_throttled")
        return
    _LAST_RELOAD_AT = _now
    _LAST_LOADED_TOKEN_HASH = token_hash

    # ADR-0154 M1/M3/M5: reset the OTA root key to free before re-resolving (after
    # the throttle guard, so a throttled reload keeps the current paid root).
    _set_feature_root_key(None)

    if not token:
        log.info("license: reload — no key found on disk, reverting to Free tier")
        _audit("license.free_tier")
        _set_active_license(None)
        _LICENSE_LOADED_AT = time.time()
        _init_instance_seed()
        return

    claims = _verify_ed25519(token)
    if claims is None:
        log.warning("license: reload — signature invalid, reverting to Free tier")
        _audit("license.invalid_token")
        _set_active_license(None)
        _LICENSE_LOADED_AT = time.time()
        _init_instance_seed()
        return

    # ADR-0102: per-token fingerprint revocation check against Corvin-Features.
    # This was checked at boot (load_license_from_env()) but NOT here — meaning
    # a cancelled subscription's token kept re-activating on every reload (every
    # authenticated console session op calls reload_from_disk() via
    # auth.py::_compute_lic_proof) until the process was fully restarted. Mirror
    # the boot-path check so revocation actually takes effect without a restart.
    if _is_token_fp_revoked(token):
        log.warning("license: reload — token is individually revoked, reverting to Free tier")
        _audit("license.revoked")
        _set_active_license(None)
        _LICENSE_LOADED_AT = time.time()
        _init_instance_seed()
        return

    validated = _validate_claims(claims)
    if validated is None:
        if (
            claims is not None
            and claims.get("iss") == "corvinlabs.io"
            and claims.get("type") == "session_permit"
            and claims.get("exp") is not None
            and claims.get("exp") < time.time()
        ):
            if _check_session_grace_period(int(claims["exp"])):
                if not _check_instance_id_bound(claims):
                    log.warning("license: reload — grace-period permit instance_id mismatch")
                    _audit("license.instance_id_mismatch",
                           jti=str(claims.get("jti", ""))[:8], tier=claims.get("tier", ""))
                    _set_active_license(None)
                    _LICENSE_LOADED_AT = time.time()
                    return
                if not _check_device_fp(claims):
                    log.warning("license: reload — grace-period permit device_fp mismatch")
                    _audit("license.device_fp_mismatch",
                           jti=str(claims.get("jti", ""))[:8], tier=claims.get("tier", ""))
                    _set_active_license(None)
                    _LICENSE_LOADED_AT = time.time()
                    return
                sal = claims.get("subscription_active_until")
                if sal is not None and sal < time.time():
                    log.warning("license: reload — subscription lapsed during grace period")
                    _audit("license.subscription_lapsed",
                           jti=str(claims.get("jti", ""))[:8], tier=claims.get("tier", ""))
                    _set_active_license(None)
                    _LICENSE_LOADED_AT = time.time()
                    return
                log.warning("license: reload — grace period active, using cached tier=%s",
                            claims.get("tier", "?"))
                _audit("license.session_grace_period",
                       tier=claims.get("tier", ""), jti=str(claims.get("jti", ""))[:8])
                _set_active_license(claims)
                _set_feature_root_key(token)
                _LICENSE_LOADED_AT = time.time()
                return
            log.warning("license: reload — grace period elapsed, reverting to Free tier")
            _audit("license.grace_period_elapsed",
                   jti=str(claims.get("jti", ""))[:8], tier=str(claims.get("tier", "")))
        else:
            log.warning("license: reload — claims invalid, reverting to Free tier")
        _set_active_license(None)
        _LICENSE_LOADED_AT = time.time()
        return

    if not _check_instance_id_bound(validated):
        log.warning("license: reload — instance_id mismatch, reverting to Free tier")
        _audit("license.instance_id_mismatch",
               jti=str(validated.get("jti", ""))[:8], tier=validated.get("tier", ""))
        _set_active_license(None)
        _LICENSE_LOADED_AT = time.time()
        return

    if not _check_device_fp(validated):
        _set_active_license(None)
        _LICENSE_LOADED_AT = time.time()
        return

    _set_active_license(validated)
    _set_feature_root_key(token)  # ADR-0154 M1/M3/M5 paid root on reload
    _LICENSE_LOADED_AT = time.time()
    log.info(
        "license: reloaded — tier=%s jti=%s",
        validated.get("tier", "?"),
        str(validated.get("jti", ""))[:8],
    )
    _audit(
        "license.loaded",
        tier=validated.get("tier", ""),
        jti=str(validated.get("jti", ""))[:8],
    )

    # ADR-0132/ADR-0133: re-seed chain DNA and flush CIT cache, same as the
    # initial boot path in load_license_from_env(). Without this, a UI key-apply
    # that upgrades to a paid tier leaves the free-tier DNA active in the running
    # process → false-positive ChainIntegrityFailure for the CIT TTL window.
    _rld_audit_p: "Path | None" = None
    _rld_seed: "str | None" = None
    try:
        _here_rld = Path(__file__).resolve()
        _forge_inner_rld = _here_rld.parents[1] / "forge" / "forge"
        _bridges_rld = _here_rld.parents[1] / "bridges" / "shared"
        for _p_rld in (_forge_inner_rld, _bridges_rld):
            if str(_p_rld) not in sys.path:
                sys.path.insert(0, str(_p_rld))
        from chain_dna import derive_seed_paid as _rld_derive  # type: ignore[import]
        from security_events import set_chain_dna_seed as _rld_seed_set, write_event as _rld_write  # type: ignore[import]
        from instance_identity import get_instance_id as _rld_get_iid  # type: ignore[import]
        from paths import corvin_home as _rld_corvin_home  # type: ignore[import]
        _rld_seed = _rld_derive(token, _rld_get_iid())
        _rld_seed_set(_rld_seed)
        try:
            from clag import clear_shadow_hashes as _rld_clag_clear  # type: ignore[import]
            _rld_clag_clear()
        except Exception:  # noqa: BLE001
            pass
        _rld_audit_p = _AUDIT_PATH_SNAPSHOT if _AUDIT_PATH_SNAPSHOT is not None else (
            _rld_corvin_home() / "global" / "forge" / "audit.jsonl"
        )
        _rld_write(
            _rld_audit_p,
            "license.chain_dna_seeded",
            details={"tier": validated.get("tier", ""), "seed_prefix": _rld_seed[:16]},
        )
    except Exception as _rld_exc:  # noqa: BLE001
        log.warning("LSAD seed setup at reload failed (non-fatal): %s", _rld_exc)
    try:
        from clag import gate as _rld_clag_gate  # type: ignore[import]
        if _rld_audit_p is not None:
            _rld_clag_gate(_rld_audit_p, "L16.license_reload", dna_seed=_rld_seed)
    except Exception as _rld_clag_exc:  # noqa: BLE001
        log.warning("CLAG gate at license reload failed (non-fatal): %s", _rld_clag_exc)


# ── Limit API (call-site interface) ──────────────────────────────────────────

def _verified_license() -> "types.MappingProxyType | None":
    """Return _ACTIVE_LICENSE if the tamper-canary passes, else None (fail-closed).

    Single enforcement point for the ADR-0144 canary check.  All public API
    functions (get_limit, active_tier, get_feature, get_custom) MUST call this
    instead of reading _ACTIVE_LICENSE directly so that every licence-gated
    decision goes through the canary.

    On mismatch: logs CRITICAL, emits a best-effort audit event, returns None
    (caller sees Free tier).  The canary detects:
      - gc.get_referents() dict mutation (hash changes)
      - name-rebind without updating _ACTIVE_LICENSE_CANARY
    Deliberate in-process attacks that also rebind the canary are accepted per
    ADR-0139 (out-of-process enforcer is the structural mitigation).
    """
    lic = _ACTIVE_LICENSE
    if lic is None:
        return None
    # ADR-0144 F-07: if the stored canary is _CANARY_UNCOMPUTABLE while the
    # license is non-None, the license was injected via a two-step rebind attack:
    #   1. _ACTIVE_LICENSE ← circular-reference dict (causes _compute_canary to fail)
    #   2. _ACTIVE_LICENSE_CANARY ← _CANARY_UNCOMPUTABLE  (spoof the sentinel)
    # A legitimately-set license is always JSON-serialisable (_set_active_license
    # catches RecursionError → sets _ACTIVE_LICENSE = None → _compute_canary(None)
    # returns None, never _CANARY_UNCOMPUTABLE).  Any other state is tampering.
    if _ACTIVE_LICENSE_CANARY is _CANARY_UNCOMPUTABLE:
        log.critical(
            "license: _ACTIVE_LICENSE_CANARY is _CANARY_UNCOMPUTABLE while "
            "_ACTIVE_LICENSE is non-None — circular-reference rebind attack "
            "detected. Falling back to Free tier. (ADR-0144 F-07)"
        )
        try:
            _audit("license.tampering_detected")
        except Exception:  # noqa: BLE001
            pass
        _engage_tamper_response("canary_uncomputable")
        return None
    current = _compute_canary(lic)
    if current != _ACTIVE_LICENSE_CANARY:
        log.critical(
            "license: _ACTIVE_LICENSE canary mismatch — possible in-process "
            "tampering detected. Falling back to Free tier. "
            "This is a security incident (ADR-0139 / ADR-0144)."
        )
        try:
            _audit("license.tampering_detected")
        except Exception:  # noqa: BLE001
            pass
        _engage_tamper_response("canary_mismatch")
        return None
    return lic


def _resolve_limit(feature: str, lic: "types.MappingProxyType | None") -> Any:
    """Resolve limit value given an already-verified licence (or None = no licence).

    Internal helper so callers that already hold the result of _verified_license()
    can look up a limit WITHOUT triggering a second canary check and a duplicate
    audit event (ADR-0144 double-emit guard).
    """
    if lic is None:
        # Features explicitly in FREE_TIER with None = unlimited; features NOT
        # in FREE_TIER default to 0 (denied) so assert_limit() fails-closed.
        return FREE_TIER.get(feature, 0)
    tier = lic.get("tier", "free")
    tier_limits = TIER_RESOURCE_LIMITS.get(tier, {})
    # 1. Per-customer override in the SesT — honored as-is. A limit in a SIGNED
    #    token (Ed25519, unforgeable) is an authoritative issuer grant, e.g. a
    #    per-customer sso_enabled=True upsell on a professional token. FND-09
    #    clamped this to the tier envelope, which silently denied legitimate
    #    grants and added no security: a forged over-grant fails signature
    #    verification, and a compromised signing key can already mint any tier,
    #    so client-side clamping of signed values is pointless. Anti-inflation
    #    belongs at token issuance, not here. (FND-09 reconsidered 2026-06-17.)
    limits = lic.get("limits", {})
    if feature in limits:
        val = limits[feature]
        # ADR-0144 string-coercion guard: a string value in the limits dict is a
        # signing-tool serialisation bug (e.g. "9999" as a digit-string).
        # int("9999") silently coerces to 9999 in assert_limit and compute_quota,
        # allowing a malformed SesT to grant an arbitrarily large numeric limit.
        # Reject strings — fall through to the tier default so the mis-serialised
        # value has no effect.  None, bool, int, float, and list are all valid types.
        if isinstance(val, str):
            log.warning(
                "license: limits[%r] = %r is a string — expected int/bool/None/list; "
                "ignoring and using tier default. (ADR-0144 string-coercion guard)",
                feature,
                val[:64],
            )
            # Fall through to tier-level default below.
        elif (
            isinstance(val, (int, float))
            and not isinstance(val, bool)
            and val < 0
        ):
            # Negative integer limit: a signing-tool bug or adversarial token.
            # int(-1) in compute_quota makes (current+1) > -1 always True →
            # permanently blocks the feature (silent DoS). Clamp to 0 to make
            # the denial explicit and audit-visible rather than silent.
            log.warning(
                "license: limits[%r] = %r is negative — clamping to 0 (fail-closed). "
                "(ADR-0144 negative-limit guard)",
                feature,
                val,
            )
            return 0
        else:
            return val
    # 2. Tier-level default (e.g. pro token without an explicit limits dict)
    if feature in tier_limits:
        return tier_limits[feature]
    # 3. Free-tier fallback. Unknown features default to 0 (denied) —
    # not None (unlimited) — so unrecognized feature names fail-closed.
    return FREE_TIER.get(feature, 0)


def get_limit(feature: str) -> Any:
    """Return the current limit for a feature.

    Resolution order (ADR-0094):
      1. Active SesT's "limits" dict (per-customer override) — AUTHORITATIVE
      2. TIER_RESOURCE_LIMITS[tier] (tier-level default)
      3. FREE_TIER (absolute fallback / no valid licence)
    Returns None for features where None means "no constraint".
    """
    return _resolve_limit(feature, _verified_license())


def is_feature_allowed(feature: str) -> bool:
    """True when a boolean feature is enabled by the active licence."""
    val = get_limit(feature)
    if isinstance(val, bool):
        return val
    if val is None:
        return False   # None = not configured = denied; add to FREE_TIER or TIER_RESOURCE_LIMITS
    return bool(val)


def assert_limit(feature: str, requested: Any = 1) -> None:
    """Raise LicenseLimitError when the requested value exceeds the limit.

    Callers MUST use this function — never inline the comparison.
    This is the single enforcement point; all audit events flow through here.

    Semantics by limit type:
      bool      → raises if limit is False (feature disabled)
      list      → raises if requested not in the list
      int/float → raises if requested > limit
      None      → never raises (no constraint)
    """
    _lic = _verified_license()           # single canary check — no double-emit
    limit = _resolve_limit(feature, _lic)
    tier = _lic.get("tier", "free") if _lic is not None else "free"

    # Reject negative requested values — they would pass any numeric `> limit` check
    # (e.g. -1 > 5 is False) and silently bypass the gate.  Any caller passing a
    # negative count has a bug or is under attack; deny explicitly.
    if isinstance(requested, (int, float)) and not isinstance(requested, bool):
        if requested < 0:
            _audit("license.limit_exceeded", feature=feature, tier=tier)
            raise LicenseLimitError(feature, requested=requested, limit=limit, tier=tier)

    if limit is None:
        # No constraint (Enterprise unlimited)
        return

    if isinstance(limit, bool):
        if not limit:
            _audit(
                "license.limit_exceeded",
                feature=feature,
                tier=tier,
            )
            raise LicenseLimitError(feature, tier=tier)
        # bool True: feature enabled. Guard against malformed SesT tokens that
        # encode integer limits as JSON boolean true (e.g. space_domains_max: true).
        # Treat True as numeric 1 so callers with requested > 1 are still blocked.
        if isinstance(requested, (int, float)) and not isinstance(requested, bool) and requested > 1:
            _audit(
                "license.limit_exceeded",
                feature=feature,
                requested_value=requested,
                limit_value=1,
                tier=tier,
            )
            raise LicenseLimitError(feature, requested=requested, limit=1, tier=tier)
        return

    if isinstance(limit, (list, tuple)):
        # _freeze_license() converts list → tuple; both represent an allowlist.
        if requested not in limit:
            _audit(
                "license.limit_exceeded",
                feature=feature,
                requested_value=str(requested)[:64],
                tier=tier,
            )
            raise LicenseLimitError(feature, requested=requested, limit=limit, tier=tier)
        return

    # Numeric comparison. Convert float requested to ceiling int before comparing
    # so that requested=5.9 (> limit 5) is correctly rejected.  int(5.9) = 5 which
    # is NOT > 5 — the truncation silently passes what should be a violation.
    # math.ceil ensures we err on the side of blocking (fail-closed).
    import math as _math
    try:
        _req_int = (_math.ceil(requested) if isinstance(requested, float)
                    and not isinstance(requested, bool) else int(requested))
        if _req_int > int(limit):
            _audit(
                "license.limit_exceeded",
                feature=feature,
                requested_value=_req_int,
                limit_value=int(limit),
                tier=tier,
            )
            raise LicenseLimitError(
                feature, requested=requested, limit=limit, tier=tier
            )
    except LicenseLimitError:
        raise
    except (TypeError, ValueError) as exc:
        # Limit or requested value is not numeric — this indicates a malformed
        # SesT (e.g. signing tool serialized a numeric field as a string).
        # Fail-closed: treat as exceeded so we don't silently grant access.
        _log_non_numeric_limit(feature, limit, exc, tier)


def get_feature(name: str) -> bool:
    """Return whether a boolean feature flag is enabled for this customer.

    Absent key → False by design (opt-in, never opt-out).
    The `features` dict in the SesT is per-customer and independent of tier:
    a Starter customer can have an experimental feature enabled without
    upgrading, and an Enterprise customer can have it disabled.

    Example SesT payload:
        "features": {"white_label": true, "beta_workflow_editor": false}
    """
    lic = _verified_license()
    if lic is None:
        return False
    features = lic.get("features")
    # MappingProxyType (from _freeze_license) is not a dict subclass but behaves the same.
    if not isinstance(features, (dict, types.MappingProxyType)):
        return False
    return bool(features.get(name, False))


def get_custom(name: str, default: Any = None) -> Any:
    """Return an arbitrary per-customer metadata value.

    Absent key → `default` (None when not provided).
    Callers MUST handle None — there is no schema for the `custom` dict.

    Example SesT payload:
        "custom": {
            "dedicated_model": "claude-opus-4-8",
            "sla_tier": "gold",
            "max_file_upload_mb": 250
        }
    """
    lic = _verified_license()
    if lic is None:
        return default
    custom = lic.get("custom")
    # MappingProxyType (from _freeze_license) is not a dict subclass but behaves the same.
    if not isinstance(custom, (dict, types.MappingProxyType)):
        return default
    return custom.get(name, default)


# Canonical tier names. Legacy token tier names normalize to the canonical name
# the product surfaces everywhere — "member" is the canonical paid tier
# (ADR-0097/0098); "universal" is the legacy Corvin-Features name and must NEVER
# surface to operators. Limit/flag lookups already alias both names to the same
# values (limits.py / tier_flags.py), so normalizing the display name is safe.
# Only two tiers exist (operator decision 2026-06-23): free + member. Every
# legacy paid-tier name (+ "universal") collapses to "member"; "free" stays free.
_TIER_CANONICAL = {
    "universal": "member",
    "starter": "member",
    "personal": "member",
    "professional": "member",
    "pro": "member",
    "business": "member",
    "enterprise": "member",
}


def canonical_tier(name: str) -> str:
    """Map any tier name to one of the two canonical products: free | member.
    KNOWN legacy paid names map to 'member'; an UNKNOWN/garbage tier fails CLOSED
    to 'free' (review R4 #7 — never grant the paid tier on an unrecognised name).
    'member' and 'free' pass through."""
    c = _TIER_CANONICAL.get(name, name)
    return c if c in ("free", "member") else "free"


def active_tier() -> str:
    """Return the active tier name ('free' when no licence is loaded or tampered).

    Legacy aliases (e.g. ``universal``) are normalized to the canonical paid
    name (``member``) so the paid tier is always surfaced as "member".
    """
    lic = _verified_license()
    return canonical_tier(lic.get("tier", "free")) if lic is not None else "free"


def is_loaded() -> bool:
    """True when a valid, untampered licence has been activated."""
    return _verified_license() is not None
