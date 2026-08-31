"""layer_integrity.py — ADR-0141 Tier 1 + Tier 2: Layer Integrity Protocol core.

This module owns the cryptographic substrate shared by two tiers of the Layer
Integrity Protocol:

  * **Tier 1 (boot check)** — at adapter start, hash every mandatory security
    layer file and compare against a Corvin Labs–signed manifest
    (``operator/security/layer-manifest.json``). A tampered or missing layer is
    detected before the adapter accepts traffic.
  * **Tier 2 (network attestation)** — the same per-file hashes are folded into
    a single ``layer_integrity_hash`` that travels inside the A2A
    ``network_attestation`` block (Protocol v7). A peer recomputes the expected
    value from the official manifest and rejects an envelope whose hash differs.

Severity model (the load-bearing rollout synthesis — see ADR-0141 deployment
order). The manifest's *private* signing key lives only at Corvin Labs and is
deliberately absent from the repo, so a valid manifest cannot exist until a
release ships one. To avoid bricking every pre-manifest install while still
detecting tampering:

  * manifest ABSENT          -> WARNING  (genuine pre-rollout state)
  * manifest present, BAD sig -> CRITICAL (forged / tampered manifest)
  * manifest valid, layer hash MISMATCH -> CRITICAL (tampered layer)
  * everything matches       -> INFO

A *present* manifest is therefore fully fail-closed; only the not-yet-shipped
state is advisory. Once a release commits a signed manifest, the absent case can
no longer occur on a genuine install.

CI lint contract: MUST NOT ``import anthropic``.
Audit contract: emit only metadata — NEVER file paths, file bytes, or mtimes.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ── Canonical mandatory layer set ───────────────────────────────────────────
#
# capability-name -> repo-relative path. The capability names mirror
# security_capabilities.MANDATORY_CAPABILITIES exactly (Tier 1 / Tier 3 lockstep)
# plus the integrity-substrate files that the residual-risk table in ADR-0141
# requires to be pinned (security_capabilities.py and this module).

MANDATORY_LAYER_FILES: dict[str, str] = {
    "path_gate": "operator/voice/hooks/path_gate.py",
    "audit": "operator/bridges/shared/audit.py",
    "consent_gate": "operator/bridges/shared/consent.py",
    "data_classification": "operator/bridges/shared/data_classification.py",
    "egress_gate": "operator/bridges/shared/egress_gate.py",
    "erasure_orchestrator": "operator/bridges/shared/erasure_orchestrator.py",
    "self_test": "operator/bridges/shared/self_test.py",
    "remote_trigger_receiver": "operator/bridges/shared/remote_trigger_receiver.py",
    # L44 acceptable-use gate (EU AI Act Art. 5) — a mandatory Tier-3 capability
    # (CAP_HOUSE_RULES) that was MISSING here, breaking the documented Tier-1/Tier-3
    # lockstep, so neither the signed manifest nor the attestation hash covered it
    # (security-audit 2026-06-25 #4).
    "house_rules": "operator/bridges/shared/house_rules.py",
    # L34/L35/L44 spawn-gate orchestrator (SSOT invoked at every spawn). Pinned so
    # a fork cannot neuter check_l34/l35/l44 into no-ops without tripping the
    # manifest (security-audit 2026-06-25 #5).
    "spawn_gates": "operator/bridges/shared/spawn_gates.py",
    # Integrity substrate — pinned so a fork cannot silently patch the Tier-3
    # registry or this verifier (ADR-0141 residual-risk table).
    "security_capabilities": "operator/bridges/shared/security_capabilities.py",
    "layer_integrity": "operator/bridges/shared/layer_integrity.py",
}

MANIFEST_REL_PATH = "operator/security/layer-manifest.json"
PUBKEY_REL_PATH = "operator/license/a2a_network_pubkey.pem"
MANIFEST_SCHEMA_VERSION = 1


def _repo_root() -> Path:
    # operator/bridges/shared/layer_integrity.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def manifest_path(root: "Path | None" = None) -> Path:
    return (root or _repo_root()) / MANIFEST_REL_PATH


def pubkey_path(root: "Path | None" = None) -> Path:
    return (root or _repo_root()) / PUBKEY_REL_PATH


# ── Hashing ─────────────────────────────────────────────────────────────────


def _hash_file(path: Path) -> str:
    """Return ``sha256:<hex>`` of a file's bytes, or ``""`` when unreadable."""
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def compute_layer_hashes(root: "Path | None" = None) -> dict[str, str]:
    """Hash every mandatory layer file on disk.

    A missing/unreadable file maps to ``""`` — a sentinel that can never equal a
    real ``sha256:`` digest, so it always trips a mismatch against a manifest.
    """
    base = root or _repo_root()
    return {
        name: _hash_file(base / rel)
        for name, rel in sorted(MANDATORY_LAYER_FILES.items())
    }


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def aggregate_hash(file_hashes: dict[str, str]) -> str:
    """Fold a ``{name: sha256:...}`` map into a single ``sha256:<hex>`` digest.

    Deterministic: the canonical JSON sorts keys, so two honest nodes running
    identical code produce an identical aggregate.
    """
    return "sha256:" + hashlib.sha256(_canonical(file_hashes)).hexdigest()


def compute_layer_integrity_hash(root: "Path | None" = None) -> str:
    """The Tier-2 value computed from LOCAL files (sender side + self-check)."""
    return aggregate_hash(compute_layer_hashes(root))


def manifest_layer_integrity_hash(manifest: dict) -> str:
    """The Tier-2 *expected* value derived from a manifest's pinned hashes
    (receiver side). For an honest sender this equals
    :func:`compute_layer_integrity_hash` on the sender's box."""
    layers = manifest.get("mandatory_layers")
    if not isinstance(layers, dict):
        return ""
    # Only fold the canonical set of names, in sorted order, mirroring
    # compute_layer_hashes() so the two aggregates are comparable.
    folded = {name: str(layers.get(name, "")) for name in sorted(MANDATORY_LAYER_FILES)}
    return aggregate_hash(folded)


# ── Manifest load + signature ───────────────────────────────────────────────


def load_manifest(root: "Path | None" = None) -> "dict | None":
    """Read + parse ``layer-manifest.json``. Returns None when absent/unreadable."""
    p = manifest_path(root)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_pubkey(root: "Path | None" = None) -> Any:
    try:
        from cryptography.hazmat.primitives.serialization import (  # type: ignore
            load_pem_public_key,
        )
        return load_pem_public_key(pubkey_path(root).read_bytes())
    except Exception:
        return None


def _manifest_signing_payload(manifest: dict) -> bytes:
    """Canonical bytes signed/verified — every field except ``manifest_sig``."""
    payload = {k: v for k, v in manifest.items() if k != "manifest_sig"}
    return _canonical(payload)


def verify_manifest_signature(manifest: dict, root: "Path | None" = None) -> bool:
    """Return True iff the RS256 ``manifest_sig`` verifies against
    ``a2a_network_pubkey.pem``. Returns False (never raises) on any failure.

    Mirrors a2a_manifest._verify_manifest_signature exactly (PKCS1v15 +
    Prehashed SHA256, base64url signature) so the trust anchor and signing
    convention are identical across the A2A network and the LIP manifest.
    """
    sig_b64 = manifest.get("manifest_sig")
    if not sig_b64 or not isinstance(sig_b64, str):
        return False
    msg_hash = hashlib.sha256(_manifest_signing_payload(manifest)).digest()
    try:
        sig_bytes = base64.urlsafe_b64decode(sig_b64 + "==")
    except Exception:
        return False
    pubkey = _load_pubkey(root)
    if pubkey is None:
        return False
    try:
        from cryptography.exceptions import InvalidSignature  # type: ignore  # noqa: F401
        from cryptography.hazmat.primitives import hashes  # type: ignore
        from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore
        from cryptography.hazmat.primitives.asymmetric.utils import (  # type: ignore
            Prehashed,
        )

        pubkey.verify(
            sig_bytes, msg_hash, padding.PKCS1v15(), Prehashed(hashes.SHA256())
        )
        return True
    except Exception:
        return False


def sign_manifest(manifest_without_sig: dict, private_key_pem: bytes) -> str:
    """Return the base64url RS256 ``manifest_sig`` for a manifest body.

    Used by the offline signing tool (Corvin Labs holds the private key). Raises
    on cryptographic error — signing must never silently produce a bad value.
    """
    from cryptography.hazmat.primitives import hashes  # type: ignore
    from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore
    from cryptography.hazmat.primitives.asymmetric.utils import Prehashed  # type: ignore
    from cryptography.hazmat.primitives.serialization import (  # type: ignore
        load_pem_private_key,
    )

    key = load_pem_private_key(private_key_pem, password=None)
    msg_hash = hashlib.sha256(_manifest_signing_payload(manifest_without_sig)).digest()
    sig = key.sign(msg_hash, padding.PKCS1v15(), Prehashed(hashes.SHA256()))
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def build_manifest_body(*, issued_at: int, mandatory_after: "int | None" = None,
                        root: "Path | None" = None) -> dict:
    """Assemble an unsigned manifest body from the current on-disk layer files.

    The signing tool calls this, signs it, and writes the result. ``issued_at``
    and ``mandatory_after`` are passed in (this module never reads the clock, to
    stay deterministic / replay-safe)."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "issued_at": int(issued_at),
        "mandatory_after": (int(mandatory_after) if mandatory_after is not None else None),
        "mandatory_layers": compute_layer_hashes(root),
    }


# ── Verification result ─────────────────────────────────────────────────────


class IntegrityStatus(str, Enum):
    VERIFIED = "verified"
    MANIFEST_ABSENT = "manifest_absent"
    MANIFEST_INVALID = "manifest_invalid"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class IntegrityResult:
    status: IntegrityStatus
    detail: str = ""
    mismatched: list[str] = field(default_factory=list)

    @property
    def is_critical(self) -> bool:
        return self.status in (IntegrityStatus.MANIFEST_INVALID, IntegrityStatus.MISMATCH)

    @property
    def ok(self) -> bool:
        return self.status == IntegrityStatus.VERIFIED


def verify_integrity(root: "Path | None" = None) -> IntegrityResult:
    """The Tier-1 boot check. Pure (no audit / no I/O beyond reading files)."""
    manifest = load_manifest(root)
    if manifest is None:
        return IntegrityResult(
            IntegrityStatus.MANIFEST_ABSENT,
            detail="no signed layer-manifest.json present (pre-rollout state)",
        )
    if not verify_manifest_signature(manifest, root):
        return IntegrityResult(
            IntegrityStatus.MANIFEST_INVALID,
            detail="manifest signature failed RS256 verification",
        )
    pinned = manifest.get("mandatory_layers")
    if not isinstance(pinned, dict):
        return IntegrityResult(
            IntegrityStatus.MANIFEST_INVALID,
            detail="manifest missing 'mandatory_layers' map",
        )
    local = compute_layer_hashes(root)
    mismatched = [
        name for name in MANDATORY_LAYER_FILES
        if local.get(name, "") != str(pinned.get(name, ""))
    ]
    if mismatched:
        return IntegrityResult(
            IntegrityStatus.MISMATCH,
            detail=f"{len(mismatched)} layer file(s) differ from manifest",
            mismatched=sorted(mismatched),
        )
    return IntegrityResult(
        IntegrityStatus.VERIFIED,
        detail=f"{len(local)} layer files match signed manifest",
    )
