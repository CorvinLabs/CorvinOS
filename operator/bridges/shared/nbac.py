"""Network-Bound Audit Chains (NBAC) — ADR-0117.

Every legitimate CorvinOS audit chain begins with a ``chain.genesis`` event
whose payload is RSA-signed by the Network Root Private Key.  The genesis
block's hash permeates the entire chain (via the standard hash-chain link),
making the chain structurally incompatible with chains from forks or foreign
networks.

Module responsibilities:
    - Genesis block creation and verification (M1)
    - Epoch Certificate creation and verification (M2, offline mode)
    - Network Root Public Key loading
    - Helper utilities (get_genesis_block, get_genesis_hash)

This module MUST NOT import anthropic (CI AST lint enforces).
This module MUST NOT make network calls (CA interaction is in a separate
higher-level module to keep this one testable offline).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

# Canonical public network identifier. Enterprise operators override via
# CORVIN_NBAC_NETWORK_ID env var or nbac_network_id in tenant config.
DEFAULT_NETWORK_ID = "corvinlabs-public"

# Path to the embedded network root public key.  The file is part of the
# repository and committed; the private key is NEVER shipped.
_PUBKEY_PATH = Path(__file__).parent.parent.parent / "license" / "nbac_network_pubkey.pem"

# genesis event type (must match EVENT_SEVERITY in security_events.py)
GENESIS_EVENT_TYPE = "chain.genesis"

# Maximum age of an Epoch Certificate before a WARNING is raised (seconds).
EPOCH_STALE_AFTER_S: int = int(os.environ.get("CORVIN_NBAC_EPOCH_STALE_S", str(86_400 * 3)))

# Hard deadline for offline operation without a valid Epoch Certificate (s).
EPOCH_HARD_DEADLINE_S: int = int(os.environ.get("CORVIN_NBAC_EPOCH_HARD_DEADLINE_S", str(86_400 * 30)))


# ── Public Key loading ─────────────────────────────────────────────────────────

def load_network_pubkey() -> Any:
    """Return the cryptography RSA public key object, or None if unavailable."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key  # type: ignore
        pem = _find_pubkey_pem()
        if pem is None:
            return None
        return load_pem_public_key(pem)
    except Exception:
        return None


def _find_pubkey_pem() -> bytes | None:
    """Locate nbac_network_pubkey.pem relative to this file's repo layout."""
    candidates = [
        _PUBKEY_PATH,
        # Fallback: operator/bridges/shared → operator/ (one level up from bridges)
        Path(__file__).parent.parent / "license" / "nbac_network_pubkey.pem",
    ]
    for p in candidates:
        if p.exists():
            return p.read_bytes()
    return None


def pubkey_fingerprint() -> str | None:
    """SHA-256 of the DER-encoded public key, hex-encoded. Returns None if key unavailable."""
    try:
        from cryptography.hazmat.primitives.serialization import (  # type: ignore
            Encoding, PublicFormat,
        )
        key = load_network_pubkey()
        if key is None:
            return None
        der = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        return hashlib.sha256(der).hexdigest()
    except Exception:
        return None


def network_id() -> str:
    """Return the active network_id for this instance."""
    return os.environ.get("CORVIN_NBAC_NETWORK_ID", DEFAULT_NETWORK_ID)


# ── Signing helpers ─────────────────────────────────────────────────────────────

def _canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _rsa_sign(privkey_pem: bytes, payload: dict) -> str:
    """Sign canonical JSON of payload with PKCS1v15/SHA-256. Returns base64url."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key  # type: ignore
    from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore
    from cryptography.hazmat.primitives import hashes  # type: ignore
    from cryptography.hazmat.primitives.asymmetric.utils import Prehashed  # type: ignore

    privkey = load_pem_private_key(privkey_pem, password=None)
    canonical = _canonical_json(payload)
    msg_hash = hashlib.sha256(canonical).digest()
    sig = privkey.sign(msg_hash, padding.PKCS1v15(), Prehashed(hashes.SHA256()))
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def _rsa_verify(payload: dict, sig_b64: str) -> bool:
    """Verify PKCS1v15/SHA-256 signature using the embedded network public key."""
    from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore
    from cryptography.hazmat.primitives import hashes  # type: ignore
    from cryptography.hazmat.primitives.asymmetric.utils import Prehashed  # type: ignore
    from cryptography.exceptions import InvalidSignature  # type: ignore

    pubkey = load_network_pubkey()
    if pubkey is None:
        return False
    try:
        # Add correct base64 padding: need (4 - len % 4) % 4 chars.
        pad = (4 - len(sig_b64) % 4) % 4
        sig_bytes = base64.urlsafe_b64decode(sig_b64 + "=" * pad)
    except Exception:
        return False
    canonical = _canonical_json(payload)
    msg_hash = hashlib.sha256(canonical).digest()
    try:
        pubkey.verify(sig_bytes, msg_hash, padding.PKCS1v15(), Prehashed(hashes.SHA256()))
        return True
    except (InvalidSignature, Exception):
        return False


# ── Genesis Block ──────────────────────────────────────────────────────────────

def build_genesis_payload(*, instance_id: str, software_commit: str | None = None) -> dict:
    """Build the genesis block payload (without signature). Deterministic except for issued_at."""
    fp = pubkey_fingerprint()
    return {
        "type":              GENESIS_EVENT_TYPE,
        "network_id":        network_id(),
        "instance_id":       instance_id,
        "software_commit":   software_commit or _software_commit(),
        "network_pubkey_fp": fp or "",
        "issued_at":         time.time(),
    }


def sign_genesis_block(privkey_pem: bytes, instance_id: str,
                       software_commit: str | None = None) -> dict:
    """Create and sign a genesis block.  Returns the full genesis dict (with genesis_sig)."""
    payload = build_genesis_payload(instance_id=instance_id, software_commit=software_commit)
    sig = _rsa_sign(privkey_pem, payload)
    return {**payload, "genesis_sig": sig, "prev_hash": "0" * 64}


def verify_genesis_block(block: dict) -> bool:
    """Return True iff the genesis block has a valid Network Root signature.

    Accepts both formats:
    - Raw genesis payload (genesis_sig at top level) — output of sign_genesis_block()
    - Chain record (event_type + details) — output of get_genesis_block() / write_event()
    """
    # Unwrap chain-record format: details holds the genesis payload.
    if "genesis_sig" not in block and "details" in block:
        inner = block["details"]
    else:
        inner = block
    sig = inner.get("genesis_sig")
    if not sig or not isinstance(sig, str):
        return False
    payload = {k: v for k, v in inner.items() if k not in ("genesis_sig", "prev_hash")}
    return _rsa_verify(payload, sig)


def verify_genesis_network(block: dict, expected_network_id: str | None = None) -> bool:
    """Return True iff the genesis block belongs to the expected network."""
    expected = expected_network_id or network_id()
    return block.get("network_id") == expected


def _software_commit() -> str:
    """Return a best-effort software version string (git hash or package version).

    Allow callers to skip the subprocess via CORVIN_NBAC_SOFTWARE_COMMIT env var
    (useful in sandboxes / bwrap / Docker without git access).
    """
    override = os.environ.get("CORVIN_NBAC_SOFTWARE_COMMIT", "").strip()
    if override:
        return override
    try:
        import subprocess
        repo_root = Path(__file__).parent.parent.parent
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=1,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ── Chain read helpers ─────────────────────────────────────────────────────────

def get_genesis_block(audit_jsonl: Path) -> dict | None:
    """Read the first chain.genesis event from audit_jsonl, or None."""
    if not audit_jsonl.exists():
        return None
    try:
        with audit_jsonl.open("r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event_type") == GENESIS_EVENT_TYPE:
                    return rec
    except OSError:
        pass
    return None


def get_genesis_hash(audit_jsonl: Path) -> str | None:
    """Return a stable 64-char SHA-256 fingerprint of the genesis block payload.

    Uses the canonical JSON of the genesis details dict (extracted from the
    chain record's 'details' field when written by write_event, or the block
    itself if genesis_sig is at the top level).  This is independent of the
    chain record's 16-char 'hash' prefix (which is too short for wire use)
    and provides a full 256-bit chain identity fingerprint.
    """
    block = get_genesis_block(audit_jsonl)
    if block is None:
        return None
    # Unwrap write_event wrapper when present
    details = block.get("details") if "details" in block else block
    if not details:
        return None
    return hashlib.sha256(_canonical_json(details)).hexdigest()


def get_genesis_network_id(audit_jsonl: Path) -> str | None:
    """Return the network_id from the genesis block, or None."""
    block = get_genesis_block(audit_jsonl)
    if block is None:
        return None
    # genesis_block details may be nested under "details" key (write_event wraps it)
    details = block.get("details") or block
    return details.get("network_id")


# ── Epoch Certificate (offline / test mode) ────────────────────────────────────

@dataclass
class EpochCertificate:
    """Serializable offline epoch certificate signed by the network root key."""
    instance_id: str
    network_id: str
    genesis_hash: str
    chain_tail: str
    epoch_number: int
    issued_at: float
    expires_at: float
    cert_sig: str = ""

    def payload(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "cert_sig"}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EpochCertificate":
        return cls(
            instance_id=str(d["instance_id"]),
            network_id=str(d["network_id"]),
            genesis_hash=str(d["genesis_hash"]),
            chain_tail=str(d["chain_tail"]),
            epoch_number=int(d["epoch_number"]),
            issued_at=float(d["issued_at"]),
            expires_at=float(d["expires_at"]),
            cert_sig=str(d.get("cert_sig", "")),
        )

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def is_stale(self) -> bool:
        return time.time() > self.issued_at + EPOCH_STALE_AFTER_S

    def verify(self) -> bool:
        return _rsa_verify(self.payload(), self.cert_sig)


def create_epoch_cert(
    privkey_pem: bytes,
    *,
    instance_id: str,
    genesis_hash: str,
    chain_tail: str,
    epoch_number: int,
    ttl_s: float = 86_400.0,
) -> EpochCertificate:
    """Create and sign an Epoch Certificate (offline mode — no CA required)."""
    now = time.time()
    cert = EpochCertificate(
        instance_id=instance_id,
        network_id=network_id(),
        genesis_hash=genesis_hash,
        chain_tail=chain_tail,
        epoch_number=epoch_number,
        issued_at=now,
        expires_at=now + ttl_s,
    )
    cert.cert_sig = _rsa_sign(privkey_pem, cert.payload())
    return cert


# ── Epoch Certificate storage ──────────────────────────────────────────────────

def epoch_cert_path(nbac_dir: Path, epoch_number: int) -> Path:
    return nbac_dir / f"epoch_{epoch_number}.json"


def save_epoch_cert(cert: EpochCertificate, nbac_dir: Path) -> Path:
    """Save cert to nbac_dir/epoch_N.json (mode 0600)."""
    nbac_dir.mkdir(parents=True, exist_ok=True)
    p = epoch_cert_path(nbac_dir, cert.epoch_number)
    p.write_text(json.dumps(cert.to_dict(), indent=2))
    p.chmod(0o600)
    return p


def load_latest_epoch_cert(nbac_dir: Path) -> EpochCertificate | None:
    """Load the highest-numbered epoch cert from nbac_dir, or None."""
    if not nbac_dir.exists():
        return None
    certs: list[tuple[int, Path]] = []
    for p in nbac_dir.glob("epoch_*.json"):
        try:
            n = int(p.stem.split("_", 1)[1])
            certs.append((n, p))
        except (IndexError, ValueError):
            pass
    if not certs:
        return None
    _, path = max(certs, key=lambda x: x[0])
    try:
        return EpochCertificate.from_dict(json.loads(path.read_text()))
    except Exception:
        return None


# ── Origin config helpers ──────────────────────────────────────────────────────

def origin_genesis_hash(origin_config: dict) -> str | None:
    """Return peer_genesis_hash from an origin config dict, or None."""
    v = origin_config.get("peer_genesis_hash")
    return str(v) if isinstance(v, str) and v else None


def genesis_hash_matches(env_genesis_hash: str | None, origin_config: dict) -> bool:
    """Return True if the envelope genesis hash matches the pairing record.

    A missing expected hash (no peer_genesis_hash in origin config) is treated
    as UNVERIFIABLE — returns True (grace period behaviour; fails in strict mode).
    A present but mismatched hash is a hard failure.
    """
    expected = origin_genesis_hash(origin_config)
    if expected is None:
        return True  # grace period: origin predates M4
    if env_genesis_hash is None:
        return True  # sender predates M4
    return expected == env_genesis_hash
