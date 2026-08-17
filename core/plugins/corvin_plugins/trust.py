"""Third-party plugin provenance and operator consent (ADR-0249).

A plugin is in-process Python with a lifecycle. Unlike a persona (a prompt) or a
Forge tool (bwrap-sandboxed, operator-reviewed), a loaded plugin runs with the
privileges of the process that holds the audit chain writer, the consent gate and
the tenant keys. `CLAUDE.md` already forbids running Forge-tool Python in-process
without operator review; nothing equivalent existed for plugins.

**Threat model, stated plainly.** In scope: a malicious or compromised
third-party plugin obtained through the marketplace or a direct download and
loaded by an operator who intended to run something benign. Out of scope:
defending against a plugin the operator knowingly installed and trusted — once
loaded in-process it can call arbitrary Python, and no manifest field changes
that.

    A plugin manifest is a DECLARATION, not a sandbox. `network_egress: none`
    records what the author says the plugin does. It is not enforced by the
    interpreter. Any dashboard or install prompt presenting a declared field as a
    guarantee is lying to the operator.

So this module buys **attribution and consent, not containment**. Real
containment needs the subprocess isolation described in ADR-0241, which is a
separate decision with an IPC contract and a latency budget attached.

Signing reuses the `awpkg` construction (`core/awpkg/awpkg/manifest.py`): Ed25519
over the SHA-256 digest of the canonical JSON with the signature field removed,
verified fail-closed. It is deliberately NOT ADR-0141's Layer Integrity Protocol,
which signs RS256 against the `a2a_network_pubkey.pem` anchor — right for layer
manifests, wrong for third-party artifacts signed by a maintainer key.

**With one correction awpkg needs and this module makes.** `awpkg` verifies the
signature against the public key carried INSIDE the manifest. That proves the
artifact is internally consistent and proves nothing about who produced it —
anyone can generate a keypair and self-sign. For `origin=vetted` to mean "signed
by Corvin Labs" the key must additionally be pinned to a trust anchor. A signature
that verifies against an unpinned key is `community`, not `vetted`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger("corvin.plugins.trust")

#: Feature flag gating ENFORCEMENT. Ships dark: an existing install with
#: community plugins must keep booting exactly as before until the operator turns
#: this on deliberately (CLAUDE.md § Feature Flags). Flag off is a quiet path —
#: the checks still compute a verdict for display, nothing is refused.
TRUST_ENFORCEMENT_FLAG = "plugin_trust_enforcement"


class Verdict(str, Enum):
    """What the trust layer concluded about one plugin."""

    #: Signed and the key is pinned to a maintainer anchor.
    VETTED = "vetted"
    #: Ships with CorvinOS — trusted because it is part of the wheel.
    BUILTIN = "builtin"
    #: Unreviewed. Loadable only with an explicit per-plugin operator decision.
    COMMUNITY = "community"
    #: Claimed `vetted` and the claim did not hold. Never downgraded — refused.
    FORGED = "forged"


@dataclass(frozen=True)
class TrustDecision:
    verdict: Verdict
    allowed: bool
    reason: str

    @property
    def refused(self) -> bool:
        return not self.allowed


# ── Digest + signature ───────────────────────────────────────────────────────

def manifest_signing_digest(raw: dict) -> bytes:
    """SHA-256 over canonical JSON with ``signature`` removed.

    Identical construction to ``awpkg.manifest.manifest_signing_digest`` so one
    signing tool serves both, and so the signature never covers itself.
    """
    body = {k: v for k, v in raw.items() if k != "signature"}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_signature(raw: dict, *, trust_anchors: Iterable[str] = ()) -> bool:
    """True iff ``raw`` carries a valid Ed25519 signature from a PINNED key.

    Fail-closed in every direction: a missing field, unsupported algorithm, bad
    encoding, absent `cryptography` backend, verification failure, or a key that
    verifies but is not in ``trust_anchors`` all return False. An unverifiable
    plugin is treated as unsigned, never as valid.

    ``trust_anchors`` holds base64url-encoded DER public keys. An EMPTY anchor set
    means nothing can be vetted — deliberately, because the alternative (accept
    any self-signed key) is what makes `vetted` meaningless.
    """
    sig = raw.get("signature")
    if not isinstance(sig, dict):
        return False
    if sig.get("algorithm") != "ed25519":
        return False
    pub_b64 = sig.get("public_key")
    val_b64 = sig.get("value")
    if not isinstance(pub_b64, str) or not isinstance(val_b64, str):
        return False

    anchors = {a.strip() for a in trust_anchors if a and a.strip()}
    if not anchors:
        log.debug("no trust anchors configured — nothing can be vetted")
        return False
    if pub_b64.strip() not in anchors:
        # Verifies-but-unpinned is the self-signing hole. Refuse before spending
        # a verification on it, and say why at debug level.
        log.debug("plugin signing key is not a pinned trust anchor")
        return False

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_der_public_key
    except ImportError:
        log.warning("cryptography backend unavailable — treating plugin as unsigned")
        return False

    try:
        pub = load_der_public_key(_b64url_decode(pub_b64))
        if not isinstance(pub, Ed25519PublicKey):
            return False
        pub.verify(_b64url_decode(val_b64), manifest_signing_digest(raw))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def load_trust_anchors(corvin_home: Path) -> tuple[str, ...]:
    """Read pinned maintainer keys.

    Order: ``CORVIN_PLUGIN_TRUST_ANCHORS`` (comma-separated, for CI and tests),
    then ``<corvin_home>/global/plugin_trust_anchors.txt`` (one key per line,
    ``#`` comments allowed).

    Ships EMPTY. No maintainer key is baked in here, because a key committed to a
    public repo is not a trust anchor — it is a published secret's public half
    with no revocation story. Until the maintainer deposits one, nothing can reach
    `vetted`, which is the honest state rather than a permissive default.
    """
    env = os.environ.get("CORVIN_PLUGIN_TRUST_ANCHORS", "")
    if env.strip():
        return tuple(p.strip() for p in env.split(",") if p.strip())

    path = corvin_home / "global" / "plugin_trust_anchors.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ()
    return tuple(
        ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")
    )


# ── Operator consent ─────────────────────────────────────────────────────────

def _consent_path(corvin_home: Path, tenant_id: str) -> Path:
    return corvin_home / "tenants" / tenant_id / "global" / "plugin_consent.json"


def consent_granted(
    plugin_id: str, *, corvin_home: Path, tenant_id: str = "_default"
) -> bool:
    """Has the operator explicitly approved THIS plugin?

    Per-plugin on purpose. A blanket "allow community plugins" switch turns a
    per-artifact trust decision into a one-time one, and the operator who flips it
    in month one is not the one who inherits the tenth plugin in month nine.
    """
    try:
        data = json.loads(
            _consent_path(corvin_home, tenant_id).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    entry = data.get("approved", {}).get(plugin_id)
    return isinstance(entry, dict) and entry.get("approved") is True


def grant_consent(
    plugin_id: str,
    *,
    corvin_home: Path,
    tenant_id: str = "_default",
    operator: str = "unknown",
    digest: str = "",
    audit_emit: Any = None,
) -> None:
    """Record an operator's decision to run a specific unreviewed plugin.

    This IS an Art. 30 event — a named operator deciding to run unreviewed
    in-process code on a live system — so unlike the build-time validator
    (ADR-0247, which must never touch the chain) it emits an audit record.
    """
    path = _consent_path(corvin_home, tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError, UnicodeDecodeError):
        data = {}
    data.setdefault("approved", {})[plugin_id] = {
        "approved": True,
        "operator": operator,
        "digest": digest,
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    if callable(audit_emit):
        try:
            audit_emit(
                "plugin.consent_granted",
                {
                    "plugin_id": plugin_id,
                    "tenant_id": tenant_id,
                    "operator": operator,
                    "digest": digest,
                },
            )
        except Exception:  # never let auditing break the grant
            log.exception("consent audit emit failed")


# ── The decision ─────────────────────────────────────────────────────────────

def evaluate(
    record_dict: dict,
    *,
    corvin_home: Path,
    tenant_id: str = "_default",
    enforcement: bool = False,
    trust_anchors: Optional[Iterable[str]] = None,
) -> TrustDecision:
    """Decide whether this plugin may be loaded.

    ``enforcement`` mirrors the feature flag. When it is False the verdict is
    still computed — the Console and `corvin plugin check` want to show it — but
    nothing is refused, so an existing install behaves exactly as before.
    """
    origin = str(record_dict.get("origin") or "community").lower()
    plugin_id = str(record_dict.get("plugin_id") or "?")

    if origin == "builtin":
        return TrustDecision(Verdict.BUILTIN, True, "ships with CorvinOS")

    anchors = (
        tuple(trust_anchors)
        if trust_anchors is not None
        else load_trust_anchors(corvin_home)
    )

    if origin == "vetted":
        if verify_signature(record_dict, trust_anchors=anchors):
            return TrustDecision(
                Verdict.VETTED, True, "signature verified against a pinned anchor"
            )
        # Refused, never downgraded to community. Downgrading would let a stripped
        # signature turn a hard failure into a quiet one — the plugin would still
        # load, just under a weaker label.
        return TrustDecision(
            Verdict.FORGED,
            not enforcement,
            "claims origin=vetted but carries no valid signature from a pinned "
            "trust anchor",
        )

    if consent_granted(plugin_id, corvin_home=corvin_home, tenant_id=tenant_id):
        return TrustDecision(
            Verdict.COMMUNITY, True, "unreviewed, but explicitly approved by an operator"
        )
    return TrustDecision(
        Verdict.COMMUNITY,
        not enforcement,
        "unreviewed third-party code with no operator approval on record",
    )


def enforcement_enabled(tenant_id: str = "_default") -> bool:
    """Resolve the ship-dark flag. Never raises; unknown flag → False."""
    try:
        from corvin_core import feature_flags

        return bool(feature_flags.is_enabled(TRUST_ENFORCEMENT_FLAG, tenant_id))
    except Exception:
        return False


__all__ = [
    "TRUST_ENFORCEMENT_FLAG",
    "TrustDecision",
    "Verdict",
    "consent_granted",
    "enforcement_enabled",
    "evaluate",
    "grant_consent",
    "load_trust_anchors",
    "manifest_signing_digest",
    "verify_signature",
]
