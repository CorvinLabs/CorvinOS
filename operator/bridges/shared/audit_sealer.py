"""audit_sealer.py — Layer 37: Audit-at-rest Encryption + Retention.

ADR-0044 (companion to ADR-0041 / ADR-0042 / ADR-0043). Rotation +
encryption-at-rest + retention-floor for the L16 hash chain.

Lifecycle of an audit segment:

  1. **Live.** ``audit.jsonl`` accepts appends from
     :func:`forge.security_events.write_event`. Hash-chain links every
     entry to its predecessor.

  2. **Rotation.** Triggered by ``should_rotate()`` (size or age over
     the tenant policy). Live segment renamed to
     ``audit.YYYY-MM-DDTHHMMSS.jsonl``.

  3. **Chain-link continuation.** A fresh ``audit.jsonl`` is created
     with a single ``audit.rotation_link`` event whose ``prev_hash``
     equals the last hash of the rotated segment. From here, normal
     ``write_event`` appends resume — the chain crosses the segment
     boundary because verifiers can walk
     ``rotated[-1].hash == live[0].prev_hash``.

  4. **Sealing.** Rotated segment is piped through the configured
     sealer (``age`` by default, ``gpg`` supported) into
     ``audit.YYYY-MM-DDTHHMMSS.jsonl.age`` (or ``.gpg``). The
     plaintext rotated file is securely overwritten and removed.
     Sealed segment chmod-444; chmod-444 enforced after the rename
     to prevent silent edits.

  5. **Retention.** Sealed segments older than ``retention_years``
     are removed by :func:`enforce_retention`. Default 7 years; the
     audit chain's ``audit.segment_retired`` event records the
     deletion intent before the file leaves disk.

Verification spans segments:

  * Each rotated segment self-verifies via
    :func:`forge.security_events.verify_chain` after unseal.
  * Cross-segment verification: walk sealed segments in
    chronological order, unseal each into ``/tmp`` (mode 0600),
    verify, then confirm
    ``sealed[i].last_hash == sealed[i+1].first_prev_hash``.

Operator unseal path:

  * ``voice-audit unseal <segment>`` (DPO / legal hold) — emits
    ``audit.unseal_requested`` (WARNING) into the live chain *before*
    decrypting, so a request for a specific past day's audit is
    itself audited.

Out of claude scope (documented as a non-goal):

  * The encryption key itself — the operator picks ``age`` recipient
    or ``gpg`` key id and provides them; this module never touches
    private key material.
  * SIEM forwarding of sealed segments; that is the operator's
    operational concern.
  * HSM integration; on-prem hardware key store is operator's choice.

Tenant configuration::

    spec:
      audit:
        retention_years: 7
        encryption_at_rest:
          enabled: true
          recipient: "age1xyz..."   # or a gpg key id
          sealer_cmd: age           # age | gpg | <custom binary>
          # RFC 3161 external timestamping (opt-in):
          tsa_enabled: false
          tsa_url: ""               # e.g. "http://tsa.example.com/tsr"
          tsa_hash_algo: sha256     # currently only sha256 supported
        rotation:
          max_size_mb: 100
          max_age_days: 30

Audit events (L16 hash chain):

  * ``audit.rotation_started``      (INFO)
  * ``audit.segment_sealed``        (INFO)
  * ``audit.segment_timestamped``   (INFO)   — RFC 3161 TSA; opt-in
  * ``audit.tsa_request_failed``    (WARNING) — non-fatal TSA failure
  * ``audit.rotation_failed``       (CRITICAL)
  * ``audit.segment_retired``       (INFO)
  * ``audit.unseal_requested``      (WARNING)

CI lint: module MUST NOT ``import anthropic``. Audit details
allow-list enforced at emission time.
"""
from __future__ import annotations

from _compat_fcntl import fcntl as _fcntl  # POSIX real / Windows no-op shim
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

# ----- config dataclasses --------------------------------------------

SealerKind = Literal["age", "gpg"]


@dataclass(frozen=True)
class RotationPolicy:
    """When does the live chain segment rotate?"""
    max_size_mb: float = 100.0
    max_age_days: int = 30

    def __post_init__(self) -> None:
        if self.max_size_mb < 0:
            raise ValueError(f"max_size_mb must be >= 0, got {self.max_size_mb}")
        if self.max_age_days < 0:
            raise ValueError(f"max_age_days must be >= 0, got {self.max_age_days}")


@dataclass(frozen=True)
class EncryptionConfig:
    """How is a rotated segment sealed at rest?"""
    enabled: bool = False
    recipient: str = ""
    sealer_cmd: SealerKind = "age"
    # RFC 3161 external timestamping (opt-in, non-fatal on failure)
    tsa_enabled: bool = False
    tsa_url: str = ""
    tsa_hash_algo: str = "sha256"

    def __post_init__(self) -> None:
        if self.enabled and not self.recipient:
            raise ValueError(
                "encryption_at_rest.enabled=true requires a non-empty recipient"
            )
        if self.sealer_cmd not in ("age", "gpg"):
            raise ValueError(
                f"sealer_cmd must be 'age' or 'gpg', got {self.sealer_cmd!r}"
            )
        if self.tsa_enabled and not self.tsa_url:
            raise ValueError(
                "tsa_enabled=true requires a non-empty tsa_url"
            )
        if self.tsa_hash_algo not in ("sha256",):
            raise ValueError(
                f"tsa_hash_algo must be 'sha256', got {self.tsa_hash_algo!r}"
            )


@dataclass(frozen=True)
class RetentionPolicy:
    """How long are sealed segments kept?"""
    retention_years: float = 7.0

    def __post_init__(self) -> None:
        if self.retention_years < 0:
            raise ValueError(
                f"retention_years must be >= 0, got {self.retention_years}"
            )

    @property
    def retention_seconds(self) -> float:
        # 365.25 days/year — matches civil-calendar averaging used by
        # most DPAs (DSGVO regulators don't quibble about leap-year
        # boundaries on 7-year retention).
        return self.retention_years * 365.25 * 86400.0


@dataclass(frozen=True)
class AuditPolicy:
    """Full L37 policy for a tenant."""
    rotation: RotationPolicy
    encryption: EncryptionConfig
    retention: RetentionPolicy


# ----- loader --------------------------------------------------------

def policy_from_tenant_config(
    tenant_config: dict[str, Any] | None,
) -> AuditPolicy:
    """Parse + validate ``spec.audit`` from a tenant.corvin.yaml dict.

    Missing fields use module defaults. Malformed entries raise
    ``ValueError``.
    """
    rotation = RotationPolicy()
    encryption = EncryptionConfig()
    retention = RetentionPolicy()

    if not tenant_config or not isinstance(tenant_config, dict):
        return AuditPolicy(rotation, encryption, retention)
    spec = tenant_config.get("spec")
    if not isinstance(spec, dict):
        return AuditPolicy(rotation, encryption, retention)
    raw = spec.get("audit")
    if not isinstance(raw, dict):
        return AuditPolicy(rotation, encryption, retention)

    # rotation
    rraw = raw.get("rotation")
    if isinstance(rraw, dict):
        max_size = rraw.get("max_size_mb", 100.0)
        max_age = rraw.get("max_age_days", 30)
        try:
            rotation = RotationPolicy(
                max_size_mb=float(max_size),
                max_age_days=int(max_age),
            )
        except (ValueError, TypeError) as e:
            raise ValueError(f"audit.rotation: {e}") from e

    # encryption
    eraw = raw.get("encryption_at_rest")
    if isinstance(eraw, dict):
        sealer = eraw.get("sealer_cmd", "age")
        if sealer not in ("age", "gpg"):
            raise ValueError(
                f"audit.encryption_at_rest.sealer_cmd must be 'age' or 'gpg', "
                f"got {sealer!r}"
            )
        try:
            encryption = EncryptionConfig(
                enabled=bool(eraw.get("enabled", False)),
                recipient=str(eraw.get("recipient") or ""),
                sealer_cmd=sealer,
                tsa_enabled=bool(eraw.get("tsa_enabled", False)),
                tsa_url=str(eraw.get("tsa_url") or ""),
                tsa_hash_algo=str(eraw.get("tsa_hash_algo", "sha256")),
            )
        except ValueError as e:
            raise ValueError(f"audit.encryption_at_rest: {e}") from e

    # retention
    retention_years = raw.get("retention_years", 7.0)
    try:
        retention = RetentionPolicy(retention_years=float(retention_years))
    except (ValueError, TypeError) as e:
        raise ValueError(f"audit.retention_years: {e}") from e

    return AuditPolicy(rotation, encryption, retention)


# ----- rotation gate -------------------------------------------------

@dataclass(frozen=True)
class RotationDecision:
    """Why (or why not) the live segment should rotate now."""
    should: bool
    reason: str
    size_mb: float
    age_days: float


def should_rotate(
    audit_path: Path,
    policy: RotationPolicy,
    *,
    now: float | None = None,
) -> RotationDecision:
    """Decide whether the live segment is due for rotation.

    Returns a decision describing the size and age and the reason. A
    missing file is not due (nothing to rotate).
    """
    if not audit_path.is_file():
        return RotationDecision(
            should=False, reason="audit file missing", size_mb=0.0, age_days=0.0,
        )
    stat = audit_path.stat()
    size_mb = stat.st_size / (1024 * 1024)
    t = now if now is not None else time.time()
    age_days = (t - stat.st_mtime) / 86400.0

    if policy.max_size_mb > 0 and size_mb >= policy.max_size_mb:
        return RotationDecision(
            should=True,
            reason=f"size {size_mb:.1f} MB ≥ max_size_mb={policy.max_size_mb}",
            size_mb=size_mb,
            age_days=age_days,
        )
    if policy.max_age_days > 0 and age_days >= policy.max_age_days:
        return RotationDecision(
            should=True,
            reason=f"age {age_days:.1f} d ≥ max_age_days={policy.max_age_days}",
            size_mb=size_mb,
            age_days=age_days,
        )
    return RotationDecision(
        should=False, reason="under thresholds",
        size_mb=size_mb, age_days=age_days,
    )


# ----- chain-tail extraction (last hash of a segment) ----------------

def last_hash_of_segment(plaintext_path: Path) -> str:
    """Return the ``hash`` of the last chain entry in a plaintext
    segment, or ``""`` if no chain entries exist. Mirrors the logic
    in :func:`forge.security_events._last_hash` but takes any file
    path (live or rotated)."""
    if not plaintext_path.is_file():
        return ""
    last = ""
    with plaintext_path.open("r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            h = rec.get("hash")
            if isinstance(h, str) and h:
                last = h
    return last


def first_prev_hash_of_segment(plaintext_path: Path) -> str:
    """Return the ``prev_hash`` of the FIRST chain entry in a plaintext
    segment, or ``""``. Used to record cross-segment continuity in the
    signed manifest: segment[i].last_hash must equal segment[i+1]'s
    first ``prev_hash``."""
    if not plaintext_path.is_file():
        return ""
    with plaintext_path.open("r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ph = rec.get("prev_hash")
            return ph if isinstance(ph, str) else ""
    return ""


# ----- signed segment manifest (FND-14 / ADR-0137) -------------------
#
# The default `voice-audit verify` only checks the LIVE segment's internal
# hash chain. Cross-segment continuity (live[0].prev_hash == newest sealed
# tail, and sealed[i].tail == sealed[i+1].first_prev) was ONLY verified under
# --include-sealed, which decrypts every segment (emits unseal_requested,
# needs the key) — so it never runs on the daily timer. An attacker who
# deletes or swaps the newest sealed segment therefore went undetected by the
# default verify.
#
# The manifest closes this WITHOUT decryption: at each rotation we append one
# entry recording the rotated segment's first_prev_hash, last_hash, on-disk
# name and sha256, MAC'd with the out-of-tree anchor key (same key as the
# per-record MAC). Default verify then checks contiguity + that each segment
# file still exists with a matching sha256 + that the live chain links to the
# newest entry — all on plaintext metadata, no unseal. Rehashing the manifest
# after a deletion requires the anchor key, which a file-system attacker lacks.

SEGMENT_MANIFEST_NAME = "audit.segments.manifest.jsonl"


def segment_manifest_path(audit_dir: Path) -> Path:
    return audit_dir / SEGMENT_MANIFEST_NAME


def _manifest_mac_sentinel_base() -> Path:
    """Out-of-tree base dir for manifest-MAC markers (next to the anchor key, so
    a filesystem attacker who rewrites a manifest cannot also delete the proof
    that manifest macs were expected)."""
    env = os.environ.get("CORVIN_AUDIT_ANCHOR_KEY", "").strip()
    return (Path(env).expanduser().parent if env
            else Path(os.path.expanduser("~/.config/corvin-voice")))


def _manifest_mac_marker_path(audit_dir: "Path | None") -> Path:
    """R2 fix: PER-AUDIT-DIR manifest-MAC marker, keyed by sha256(abspath(dir)).

    The original host-global ``audit_manifest_mac_active`` sentinel false-
    positived across tenants/sessions: once ANY manifest on the host wrote a mac,
    a DIFFERENT audit dir with sealed segments but a never-mac'd manifest tripped
    the full-strip detector on an intact chain. Mirrors the chain's per-chain
    ``_mac_chain_marker_path``. ``audit_dir=None`` → legacy host-global marker."""
    base = _manifest_mac_sentinel_base()
    if audit_dir is None:
        return base / "audit_manifest_mac_active"
    key = hashlib.sha256(str(Path(audit_dir).resolve()).encode("utf-8")).hexdigest()[:32]
    return base / "manifest_mac_active_dirs" / key


def _mark_manifest_mac_active(audit_dir: "Path | None" = None) -> None:
    """Idempotently record that manifest-MAC writing is active for ``audit_dir``.
    Best-effort — a failure here must never break a manifest append."""
    try:
        sp = _manifest_mac_marker_path(audit_dir)
        if not sp.exists():
            sp.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(sp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
    except Exception:  # noqa: BLE001
        pass


def manifest_mac_active(audit_dir: "Path | None" = None) -> bool:
    """True if the manifest-MAC marker for ``audit_dir`` exists (this dir's
    manifest is expected to carry macs). Per-dir to avoid cross-tenant false
    positives; does NOT fall back to the host-global marker."""
    try:
        return _manifest_mac_marker_path(audit_dir).exists()
    except Exception:  # noqa: BLE001
        return False


def _manifest_anchor_key() -> bytes | None:
    """Load the shared audit anchor key (ADR-0137 M2). Reused from
    security_events so the manifest MAC and the per-record MAC share one
    out-of-tree secret. Returns None when unavailable (manifest still
    written, just unsigned — verify then falls back to existence + sha256
    + contiguity checks, which already detect deletion/swap)."""
    try:
        from forge.security_events import _anchor_key  # type: ignore[import]
    except Exception:  # noqa: BLE001
        try:
            from security_events import _anchor_key  # type: ignore[import]
        except Exception:  # noqa: BLE001
            return None
    try:
        return _anchor_key()
    except Exception:  # noqa: BLE001
        return None


def _manifest_canonical(rec: dict[str, Any]) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def append_segment_manifest(
    audit_dir: Path,
    *,
    segment_name: str,
    on_disk_name: str,
    first_prev_hash: str,
    last_hash: str,
    ts: float,
) -> None:
    """Append one MAC'd continuity record for a freshly rotated segment.

    ``segment_name`` is the plaintext segment name (stable across seal);
    ``on_disk_name`` is what actually persists (``<seg>.age`` when sealed,
    or the plaintext name when encryption is off). Best-effort: a manifest
    write failure must never break rotation (the chain itself is intact),
    so callers wrap this and downgrade failure to a WARNING."""
    rec: dict[str, Any] = {
        "ts": ts,
        "segment": segment_name,
        "on_disk": on_disk_name,
        "first_prev_hash": first_prev_hash,
        "last_hash": last_hash,
        "sha256": _sha256_file(audit_dir / on_disk_name),
    }
    ak = _manifest_anchor_key()
    if ak:
        import hmac as _hmac
        mac = _hmac.new(ak, _manifest_canonical(rec).encode("utf-8"),
                        hashlib.sha256).hexdigest()[:16]
        rec["mac"] = mac
        # R3-03: record (out-of-tree, PER audit_dir) that manifest MAC is active
        # so a later full-strip of every manifest `mac` field is detectable —
        # without false-positiving on other tenants'/sessions' audit dirs (R2).
        _mark_manifest_mac_active(audit_dir)
    mpath = segment_manifest_path(audit_dir)
    lock_fd = os.open(str(mpath.with_name(".segments.manifest.lock")),
                      os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
        with mpath.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(mpath, 0o600)
        except OSError:
            pass
    finally:
        _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
        os.close(lock_fd)


# ----- sealing -------------------------------------------------------

# Sealer is a callable: (plaintext_path, sealed_path, recipient) -> None
Sealer = Callable[[Path, Path, str], None]


def _seal_with_age(plaintext: Path, sealed: Path, recipient: str) -> None:
    """Pipe the plaintext file through ``age -r <recipient>``.

    Raises ``RuntimeError`` on non-zero exit. The sealed path is
    written atomically (write to .tmp then rename).
    """
    tmp = sealed.with_suffix(sealed.suffix + ".tmp")
    try:
        with plaintext.open("rb") as src, tmp.open("wb") as dst:
            proc = subprocess.run(
                ["age", "-r", recipient],
                stdin=src,
                stdout=dst,
                stderr=subprocess.PIPE,
                check=False,
            )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()[:500]
            raise RuntimeError(f"age sealer rc={proc.returncode}: {err}")
        os.replace(tmp, sealed)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _seal_with_gpg(plaintext: Path, sealed: Path, recipient: str) -> None:
    """Pipe through ``gpg --batch --yes --encrypt -r <recipient>``."""
    tmp = sealed.with_suffix(sealed.suffix + ".tmp")
    try:
        with plaintext.open("rb") as src, tmp.open("wb") as dst:
            proc = subprocess.run(
                ["gpg", "--batch", "--yes", "--encrypt", "-r", recipient],
                stdin=src,
                stdout=dst,
                stderr=subprocess.PIPE,
                check=False,
            )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()[:500]
            raise RuntimeError(f"gpg sealer rc={proc.returncode}: {err}")
        os.replace(tmp, sealed)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


_BUILTIN_SEALERS: dict[SealerKind, Sealer] = {
    "age": _seal_with_age,
    "gpg": _seal_with_gpg,
}


def sealer_for(kind: SealerKind) -> Sealer:
    """Resolve the sealer callable. Caller can monkeypatch for tests."""
    return _BUILTIN_SEALERS[kind]


def sealer_binary_available(kind: SealerKind) -> bool:
    """Best-effort which-lookup; useful for boot self-test."""
    return shutil.which(kind) is not None


# ----- RFC 3161 external timestamping (L37 TSA extension) ------------

# SHA-256 AlgorithmIdentifier DER bytes (OID 2.16.840.1.101.3.4.2.1 + NULL)
# 30 0d 06 09 60 86 48 01 65 03 04 02 01 05 00
_SHA256_ALG_ID_DER: bytes = bytes([
    0x30, 0x0d,
    0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01,
    0x05, 0x00,
])


def _der_len(n: int) -> bytes:
    """Minimal DER definite-length encoding."""
    if n < 0x80:
        return bytes([n])
    if n < 0x100:
        return bytes([0x81, n])
    if n < 0x10000:
        return bytes([0x82, n >> 8, n & 0xFF])
    raise ValueError(f"DER length too large for this helper: {n}")


def _build_tsa_request(file_hash_bytes: bytes) -> bytes:
    """Build a minimal RFC 3161 TimeStampReq (DER) for a SHA-256 hash.

    Structure::

        TimeStampReq SEQUENCE {
            version          INTEGER 1
            messageImprint   MessageImprint {
                hashAlgorithm  AlgorithmIdentifier (SHA-256)
                hashedMessage  OCTET STRING (32 bytes)
            }
            certReq          BOOLEAN TRUE
        }

    Total size for a 32-byte SHA-256 hash: 59 bytes.
    """
    hash_oct = bytes([0x04, len(file_hash_bytes)]) + file_hash_bytes
    msg_imp_inner = _SHA256_ALG_ID_DER + hash_oct
    msg_imp = bytes([0x30]) + _der_len(len(msg_imp_inner)) + msg_imp_inner
    version = bytes([0x02, 0x01, 0x01])
    cert_req = bytes([0x01, 0x01, 0xFF])
    req_inner = version + msg_imp + cert_req
    return bytes([0x30]) + _der_len(len(req_inner)) + req_inner


def _request_timestamp_token(
    sealed_path: Path,
    *,
    tsa_url: str,
    hash_algo: str = "sha256",
    timeout_s: int = 15,
) -> bytes:
    """Hash ``sealed_path`` with SHA-256, POST an RFC 3161 TimeStampReq
    to ``tsa_url``, return the raw TimeStampResp bytes.

    Raises ``RuntimeError`` on network failure, HTTP error, or timeout.
    The caller is responsible for persisting the response as a ``.tsr``
    file alongside the sealed segment.

    ``hash_algo`` is accepted for forward-compat but only ``sha256``
    is currently supported.
    """
    if hash_algo != "sha256":
        raise ValueError(f"unsupported tsa_hash_algo: {hash_algo!r}")

    import urllib.request as _urlreq  # stdlib, imported lazily

    h = hashlib.sha256()
    with sealed_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)

    req_der = _build_tsa_request(h.digest())
    http_req = _urlreq.Request(
        tsa_url,
        data=req_der,
        headers={"Content-Type": "application/timestamp-query"},
        method="POST",
    )
    try:
        with _urlreq.urlopen(http_req, timeout=timeout_s) as resp:
            status = resp.status
            if status != 200:
                raise RuntimeError(f"TSA returned HTTP {status}")
            return resp.read()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"TSA request to {tsa_url!r} failed: {exc}") from exc


# ----- rotation + seal -----------------------------------------------

# Segment filename pattern: audit.YYYY-MM-DDTHHMMSSZ.jsonl[.<ext>]
_SEGMENT_NAME_RE = re.compile(
    r"^audit\.(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}\d{2}\d{2}Z)\.jsonl(?:\.(age|gpg))?$"
)


def _segment_name(now: float) -> str:
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    return f"audit.{dt.strftime('%Y-%m-%dT%H%M%SZ')}.jsonl"


@dataclass(frozen=True)
class RotationResult:
    """Outcome of a rotation pass."""
    rotated: bool
    rotated_path: Path | None           # plaintext rotated segment (may be removed post-seal)
    sealed_path: Path | None            # sealed segment (if encryption enabled)
    new_live_path: Path                 # the fresh audit.jsonl after rotation
    last_hash: str                      # tail hash carried into the new segment
    reason: str
    timestamp_token_path: Path | None = None  # RFC 3161 .tsr file (if TSA enabled + succeeded)


def rotate_and_seal(
    audit_path: Path,
    policy: AuditPolicy,
    *,
    audit_writer: Callable[[str, str, dict[str, Any]], None] | None = None,
    now: float | None = None,
    sealer: Sealer | None = None,
) -> RotationResult:
    """Rotate the live segment and (if configured) seal the rotation.

    Steps:
      1. Pick a segment timestamp (``now`` or wall clock).
      2. Atomically rename ``audit.jsonl`` →
         ``audit.<stamp>.jsonl``. (If no live file exists, no-op.)
      3. Extract last hash of the rotated segment.
      4. Create a fresh ``audit.jsonl`` containing one
         ``audit.rotation_link`` event whose ``prev_hash`` equals the
         rotated tail hash. From here, ``write_event`` resumes.
      5. If encryption is enabled: seal the rotated plaintext through
         the configured sealer, chmod-444 the sealed file, then
         securely overwrite + unlink the plaintext.
      6. Emit ``audit.segment_sealed`` (or ``audit.rotation_failed``)
         into the *new* live chain via ``audit_writer``.

    Returns a :class:`RotationResult`. Raises ``RuntimeError`` on
    irrecoverable failure (e.g. sealer non-zero with no fallback).
    """
    # Invariant: when encryption is enabled, audit_writer MUST be provided so
    # that audit.rotation_failed (CRITICAL) can reach the L16 hash chain on
    # sealer failure.  Omitting it with encryption=on silently drops the
    # CRITICAL event — a compliance violation (L37 + L16 tamper-evidence).
    if policy.encryption.enabled and audit_writer is None:
        raise ValueError(
            "audit_writer is required when encryption is enabled; "
            "omitting it would silently drop audit.rotation_failed (CRITICAL) "
            "from the L16 hash chain."
        )
    t = now if now is not None else time.time()
    audit_dir = audit_path.parent

    # 1 + 2. Rename. If the live file doesn't exist, there's nothing to do.
    if not audit_path.is_file():
        return RotationResult(
            rotated=False, rotated_path=None, sealed_path=None,
            new_live_path=audit_path, last_hash="",
            reason="audit file missing — nothing to rotate",
        )

    # V-010: Guard the entire rotation body with an exclusive flock so that
    # concurrent callers (e.g. two timer firings in the same second) cannot
    # interleave the os.replace → rotation_link write sequence.
    rotation_lock_path = audit_path.with_name(".rotation.lock")
    lock_fd = os.open(str(rotation_lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            _fcntl.flock(lock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "rotation already in progress — concurrent rotation skipped"
            )

        rotated_name = _segment_name(t)
        rotated_path = audit_dir / rotated_name
        if rotated_path.exists():
            # Extremely rare — second rotation within the same UTC second.
            # Suffix with a counter to keep names unique.
            i = 1
            while (audit_dir / f"{rotated_name}.{i}").exists():
                i += 1
            rotated_path = audit_dir / f"{rotated_name}.{i}"

        os.replace(audit_path, rotated_path)

        # 3. Tail hash + first prev_hash (manifest continuity, captured BEFORE
        # sealing because the plaintext is shredded once the segment is sealed).
        tail = last_hash_of_segment(rotated_path)
        seg_first_prev = first_prev_hash_of_segment(rotated_path)

        # 4. Fresh live chain with one rotation_link entry.
        # V-009: This write is fail-closed — a broken rotation_link would
        # sever the hash chain.  Any failure raises RuntimeError immediately.
        link_rec = {
            "ts": t,
            "event_type": "audit.rotation_link",
            "severity": "INFO",
            "run_id": "",
            "tool": "audit_sealer",
            "details": {
                "rotated_segment": rotated_path.name,
            },
            "prev_hash": tail,
        }
        # Compute the hash for the rotation_link entry itself so the new
        # live chain starts already-linked.
        canonical = json.dumps(link_rec, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256()
        h.update(tail.encode("utf-8"))
        h.update(b"\n")
        h.update(canonical.encode("utf-8"))
        link_rec["hash"] = h.hexdigest()[:16]
        try:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_path.open("w") as fh:
                fh.write(json.dumps(link_rec) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception as exc:
            if audit_writer is not None:
                _emit(audit_writer, "audit.rotation_failed", "CRITICAL", {
                    "rotated_segment": rotated_path.name,
                    "reason": str(exc)[:500],
                })
            raise RuntimeError(
                f"audit chain broken at rotation — rotation_link write failed: {exc}"
            ) from exc

        # 5. Seal if configured.
        sealed_path: Path | None = None
        timestamp_token_path: Path | None = None
        if policy.encryption.enabled:
            ext = "age" if policy.encryption.sealer_cmd == "age" else "gpg"
            sealed_path = audit_dir / f"{rotated_path.name}.{ext}"
            try:
                seal_fn = sealer or sealer_for(policy.encryption.sealer_cmd)
                seal_fn(rotated_path, sealed_path, policy.encryption.recipient)
                # chmod-444 to prevent silent edits.
                os.chmod(sealed_path, 0o444)
                # Overwrite-then-unlink the plaintext rotation. Best-effort
                # zero-fill — a determined attacker with raw disk access
                # isn't stopped, but routine `cat` of the file is.
                try:
                    size = rotated_path.stat().st_size
                    with rotated_path.open("r+b") as fh:
                        remaining = size
                        _chunk = 65536
                        while remaining > 0:
                            fh.write(b"\x00" * min(_chunk, remaining))
                            remaining -= min(_chunk, remaining)
                        fh.flush()
                        os.fsync(fh.fileno())
                except OSError:
                    pass
                try:
                    rotated_path.unlink(missing_ok=True)
                except OSError as _unlink_exc:
                    # Seal succeeded and sealed_path exists; plaintext cleanup
                    # failed (e.g. chmod-444 from a prior partial failure).
                    # Log a WARNING but do NOT propagate — triggering
                    # audit.rotation_failed (CRITICAL) after a successful seal
                    # is a false positive.  The operator should manually remove
                    # the plaintext file.
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "audit_sealer: plaintext cleanup failed after successful "
                        "seal — sealed_path=%s plaintext=%s error=%s",
                        sealed_path, rotated_path, _unlink_exc,
                    )
                # audit.segment_sealed is best-effort: the segment is already
                # sealed at this point so a failure here is observable but not
                # chain-breaking.
                if audit_writer is not None:
                    _emit(audit_writer, "audit.segment_sealed", "INFO", {
                        "sealed_segment": sealed_path.name,
                        "sealer_cmd": policy.encryption.sealer_cmd,
                        "rotated_size_bytes": size if 'size' in locals() else 0,
                    })
                # RFC 3161 external timestamping — opt-in, non-fatal.
                if policy.encryption.tsa_enabled:
                    tsr_path = sealed_path.with_suffix(sealed_path.suffix + ".tsr")
                    try:
                        tsr_bytes = _request_timestamp_token(
                            sealed_path,
                            tsa_url=policy.encryption.tsa_url,
                            hash_algo=policy.encryption.tsa_hash_algo,
                        )
                        tsr_path.write_bytes(tsr_bytes)
                        os.chmod(tsr_path, 0o444)
                        timestamp_token_path = tsr_path
                        if audit_writer is not None:
                            _emit(audit_writer, "audit.segment_timestamped", "INFO", {
                                "sealed_segment": sealed_path.name,
                                "timestamp_token_name": tsr_path.name,
                                "tsa_success": True,
                            })
                    except Exception as tsa_exc:  # noqa: BLE001
                        if audit_writer is not None:
                            _emit(audit_writer, "audit.tsa_request_failed", "WARNING", {
                                "sealed_segment": sealed_path.name,
                                # Truncate like the sibling emits (l.623/707):
                                # an untruncated TSA error (ASN.1/HTTP, can embed
                                # a long URL) >2048 would be dropped wholesale by
                                # the ADR-0129 floor, losing the diagnostic.
                                "reason": str(tsa_exc)[:500],
                            })
            except Exception as e:  # noqa: BLE001
                if audit_writer is not None:
                    _emit(audit_writer, "audit.rotation_failed", "CRITICAL", {
                        "rotated_segment": rotated_path.name,
                        "reason": str(e)[:500],
                    })
                raise

        # 6b. Re-anchor AFTER all audit events (ADR-0135 M2 fix): stored_tail
        # must equal the current chain tail at the moment the anchor is written.
        # Placing the re-anchor before audit.segment_sealed / segment_timestamped
        # caused tail_mismatch CRITICAL on every encrypted rotation — the anchor
        # captured an intermediate tail, but boot saw the post-sealed tail.
        # Skipped automatically on sealing failure (exception from step 5 propagates
        # past this point). Best-effort — failure yields "absent" anchor at next
        # boot (WARNING), not CRITICAL.
        _anchor_path = audit_path.parent / "chain_anchor.json"
        try:
            try:
                from forge.clag import write_chain_anchor as _wca  # type: ignore[import]
            except ImportError:
                from clag import write_chain_anchor as _wca  # type: ignore[import]
            _wca(audit_path, _anchor_path)
        except Exception:  # noqa: BLE001
            pass  # best-effort; absent anchor at next boot is WARNING, not CRITICAL

        # 6c. FND-14: append the signed segment-continuity manifest entry.
        # on_disk_name is the sealed file when encryption is on (plaintext is
        # shredded), else the plaintext rotated segment. Best-effort: the
        # chain is already intact, so a manifest write failure is a WARNING,
        # never a rotation failure.
        try:
            _on_disk = sealed_path.name if sealed_path is not None else rotated_path.name
            append_segment_manifest(
                audit_dir,
                segment_name=rotated_path.name,
                on_disk_name=_on_disk,
                first_prev_hash=seg_first_prev,
                last_hash=tail,
                ts=t,
            )
        except Exception as _man_exc:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "audit_sealer: segment manifest append failed (continuity "
                "metadata not recorded for %s) — error=%s",
                rotated_path.name, _man_exc,
            )

    finally:
        _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
        os.close(lock_fd)
        try:
            rotation_lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    return RotationResult(
        rotated=True,
        rotated_path=rotated_path if not policy.encryption.enabled else None,
        sealed_path=sealed_path,
        new_live_path=audit_path,
        last_hash=tail,
        reason="size/age threshold reached" if policy.encryption.enabled else "rotation without sealing (encryption disabled)",
        timestamp_token_path=timestamp_token_path,
    )


# ----- retention -----------------------------------------------------

def list_sealed_segments(audit_dir: Path) -> list[Path]:
    """All audit segments in the directory, plaintext + sealed,
    sorted by timestamp ascending.

    Live ``audit.jsonl`` is NOT included.
    """
    if not audit_dir.is_dir():
        return []
    out = []
    for p in audit_dir.iterdir():
        m = _SEGMENT_NAME_RE.match(p.name)
        if m:
            out.append(p)
    out.sort(key=lambda p: p.name)
    return out


def enforce_retention(
    audit_dir: Path,
    policy: RetentionPolicy,
    *,
    audit_writer: Callable[[str, str, dict[str, Any]], None] | None = None,
    now: float | None = None,
) -> list[Path]:
    """Remove sealed segments older than ``retention_years``.

    Emits ``audit.segment_retired`` BEFORE removal so the retirement
    intent is itself audited. Returns the list of removed paths.
    """
    t = now if now is not None else time.time()
    cutoff = t - policy.retention_seconds
    removed: list[Path] = []
    for p in list_sealed_segments(audit_dir):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        if audit_writer is not None:
            _emit(audit_writer, "audit.segment_retired", "INFO", {
                "sealed_segment": p.name,
                "age_days": (t - mtime) / 86400.0,
            })
        try:
            os.chmod(p, 0o644)  # so we can unlink it (was chmod 444)
            p.unlink()
            removed.append(p)
        except OSError:
            continue
    return removed


# ----- unseal (DPO / legal hold) -------------------------------------

def unseal_to_temp(
    sealed_path: Path,
    *,
    tmpdir: Path | None = None,
    identity_file: Path | None = None,
    sealer_cmd: SealerKind | None = None,
    audit_writer: Callable[[str, str, dict[str, Any]], None] | None = None,
    requester: str = "",
) -> Path:
    """Decrypt a sealed segment into a tmpdir-bound plaintext file
    with mode 0600. The caller is responsible for removing the
    plaintext when inspection is done.

    Emits ``audit.unseal_requested`` (WARNING) into the live chain
    BEFORE decryption so the request is itself audited regardless of
    what happens to the plaintext afterwards.
    """
    if not sealed_path.is_file():
        raise FileNotFoundError(f"sealed segment not found: {sealed_path}")

    # Resolve sealer
    kind: SealerKind
    if sealer_cmd is not None:
        kind = sealer_cmd
    elif sealed_path.suffix == ".age":
        kind = "age"
    elif sealed_path.suffix == ".gpg":
        kind = "gpg"
    else:
        raise ValueError(
            f"cannot infer sealer from suffix {sealed_path.suffix!r}; "
            f"pass sealer_cmd= explicitly"
        )

    # Audit BEFORE decryption.
    if audit_writer is not None:
        _emit(audit_writer, "audit.unseal_requested", "WARNING", {
            "sealed_segment": sealed_path.name,
            "requester": requester or "unknown",
            "sealer_cmd": kind,
        })

    if tmpdir is None:
        import tempfile
        td = Path(tempfile.mkdtemp(prefix="corvin-unseal-"))
    else:
        td = tmpdir
        td.mkdir(parents=True, exist_ok=True)
    plaintext = td / sealed_path.with_suffix("").name  # strip .age/.gpg
    # touch with mode 0600 BEFORE writing content
    fd = os.open(plaintext, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)

    cmd = (
        ["age", "-d"] + (["-i", str(identity_file)] if identity_file else [])
        + [str(sealed_path)]
        if kind == "age"
        else ["gpg", "--batch", "--yes", "--decrypt", str(sealed_path)]
    )
    with plaintext.open("wb") as dst:
        proc = subprocess.run(
            cmd,
            stdout=dst,
            stderr=subprocess.PIPE,
            check=False,
        )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()[:500]
        # Wipe partial plaintext on failure
        try:
            plaintext.unlink()
        except OSError:
            pass
        raise RuntimeError(f"{kind} unseal rc={proc.returncode}: {err}")
    os.chmod(plaintext, 0o600)
    return plaintext


# ----- audit emission helper ----------------------------------------

_AUDIT_ALLOWED: frozenset[str] = frozenset({
    "rotated_segment",
    "sealed_segment",
    "sealer_cmd",
    "rotated_size_bytes",
    "age_days",
    "reason",
    "requester",
    # RFC 3161 TSA extension — basename and boolean only (no URL/path leakage)
    "tsa_success",
    "timestamp_token_name",
})


def _validate_audit_details(details: dict[str, Any]) -> None:
    for k in details:
        if k not in _AUDIT_ALLOWED:
            raise ValueError(
                f"audit_sealer detail '{k}' not in allow-list "
                f"{sorted(_AUDIT_ALLOWED)}"
            )


def _emit(
    audit_writer: Callable[[str, str, dict[str, Any]], None],
    event_type: str,
    severity: str,
    details: dict[str, Any],
) -> None:
    _validate_audit_details(details)
    try:
        audit_writer(event_type, severity, details)
    except Exception:  # noqa: BLE001
        # Best-effort, mirrors L34 + L35 pattern.
        pass


# ----- forge-backed audit writer (production wiring) -----------------

def make_forge_audit_writer(
    audit_path: Path,
    *,
    allowed_keys: frozenset[str] = _AUDIT_ALLOWED,
):
    """Same pattern as L34 / L35. Returns ``(event_type, severity,
    details) -> None`` that appends to the L16 chain via
    ``forge.security_events.write_event``.

    V-021: details are filtered to ``allowed_keys`` at the writer level
    so that callers cannot accidentally leak keys not in the allow-list,
    even if ``_validate_audit_details`` would have caught them later.
    Defaults to the module-level ``_AUDIT_ALLOWED`` frozenset.

    Best-effort: returns a no-op writer when forge isn't importable
    (standalone test environment).
    """
    try:
        import sys
        here = Path(__file__).resolve()
        repo = None
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is not None:
            forge_pkg = repo / "operator" / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))
        from forge.security_events import write_event  # type: ignore
    except Exception:  # noqa: BLE001
        def _noop(event_type: str, severity: str, details: dict[str, Any]) -> None:
            return
        return _noop

    def _writer(event_type: str, severity: str, details: dict[str, Any]) -> None:
        # V-021: filter details at writer level — strip keys not in allowed_keys
        # before they reach write_event, providing defence-in-depth against
        # accidental PII / content leakage.
        filtered = {k: v for k, v in details.items() if k in allowed_keys}
        try:
            write_event(
                audit_path, event_type,
                severity=severity, details=filtered,
            )
        except Exception:  # noqa: BLE001
            pass

    return _writer
