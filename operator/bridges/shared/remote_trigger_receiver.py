"""Layer 38 — RemoteTriggerReceiver + A2A TaskEnvelope Protocol (v3+IBC).

Inbound A2A (agent-to-agent) execution path: a signed TaskEnvelope from a
trusted remote origin is validated, anchored in the L16 audit chain (audit-
first invariant), and a signed ResponseEnvelope is returned.

Protocol v3 extends v2 with binary attachments and IBC (ADR-0145):

  * ``TaskEnvelope.sender_instance_id`` — caller's local UUID, HMAC-covered.
  * ``ResponseEnvelope.instance_id`` — receiver's UUID, signed into recv-key HMAC.
  * ``TaskEnvelope.instance_attestation`` — IBC-backed sender identity (M2 IBC).
  * Binary attachments via ``a2a_attachments`` (sha256-verified, capped at 1 MiB).

M1: protocol + validation + audit anchoring. No WorkerEngine spawn.
M2: WorkerEngine spawn + result filter + IBC attestation gate (ADR-0145 M2).

ADR-0048 compliance:
- OriginRegistry per-call reads (no cache); mode 0600 enforced.
- NonceStore in-memory, TTL-keyed, LRU-evicted at 10 000.
- HMAC-SHA256 with constant-time compare.
- Audit-first: L16 write MUST succeed before any response.
- Fail-silent: all errors return identical "rejected" ResponseEnvelope.
- MUST NOT import the anthropic SDK (CI AST lint enforces this).
"""
from __future__ import annotations

import collections
import hashlib
import hmac as _hmac
import json
import math
import os
import secrets
import stat
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ── ADR-0141 Tier 3 — self-register this security capability at import time ──
try:  # pragma: no cover - exercised at adapter boot / self-test
    from security_capabilities import (  # noqa: E402
        register_capability as _reg_cap,
        module_self_hash as _self_hash,
    )

    _reg_cap("remote_trigger_receiver", version="1.0", file_hash=_self_hash(__file__))
except Exception:  # pragma: no cover - fail-closed: absent capability blocks spawn
    pass

# ADR-0077 S-3: per-origin rate-limit bucket.
_RateBucket = collections.namedtuple("_RateBucket", ["tokens", "last_refill"])

# ── forge security_events — direct import for audit-first strict write ────
_forge_se: Any = None
try:
    _forge_parent = Path(__file__).resolve().parents[2] / "forge"
    if str(_forge_parent) not in sys.path:
        sys.path.insert(0, str(_forge_parent))
    from forge import security_events as _forge_se  # type: ignore[import-not-found]
except Exception:
    _forge_se = None

# ── audit_path from sibling audit.py ─────────────────────────────────────
try:
    from audit import audit_path  # type: ignore[import-not-found]
except ImportError:
    _shared = Path(__file__).resolve().parent
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from audit import audit_path  # type: ignore[import-not-found]

# ── instance_identity (local UUID) ────────────────────────────────────────
try:
    from instance_identity import get_instance_id  # type: ignore[import-not-found]
except ImportError:
    _shared = Path(__file__).resolve().parent
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from instance_identity import get_instance_id  # type: ignore[import-not-found]

# ── IBC verification helpers (ADR-0145 Protocol v7) ──────────────────────
# _verify_ibc_ed25519 verifies+decodes the IBC in one call: the IBC is now
# signed with Corvin-Features' Ed25519 session-signing keypair under kid
# "ibc-vN" (reused, not a separate RS256 trust anchor — see ADR-0145's
# "Known deviation" note), matching instance_identity.py's own client-side
# verification of the same certificate format.
try:
    from instance_identity import (  # type: ignore[import-not-found]
        verify_instance_sig as _verify_ibc_sig,
        build_canonical_payload as _build_ibc_canonical,
        _verify_ibc_signature as _verify_ibc_ed25519,
    )
    _IBC_VERIFY_AVAILABLE = True
except ImportError:
    _IBC_VERIFY_AVAILABLE = False

try:
    import jwt as _ibc_jwt  # type: ignore[import-not-found]
    _IBC_JWT_OK = True
except ImportError:
    _IBC_JWT_OK = False

# ── A2A network manifest (ADR-0103 M3) — lazy import, never blocks boot ──
try:
    from a2a_manifest import load_manifest as _load_a2a_manifest  # type: ignore[import-not-found]
except Exception:
    _load_a2a_manifest = None  # type: ignore[assignment]

# ── A2A network pubkey path (ADR-0103 M2) ────────────────────────────────
_A2A_NETWORK_PUBKEY_PATH = (
    Path(__file__).resolve().parents[2] / "license" / "a2a_network_pubkey.pem"
)

# ── Origin registry resolution ────────────────────────────────────────────
_REMOTE_ORIGINS_ENV = "REMOTE_ORIGINS_DIR"
_REMOTE_ORIGINS_DEFAULT = Path(__file__).resolve().parents[2] / "cowork" / "remote_origins"

# Validation time window: ±300 s
_TIME_WINDOW_S: float = 300.0

# Nonce store config
_NONCE_MAX: int = 10_000
# TTL must be > 2 × _TIME_WINDOW_S to prevent the boundary-race where a nonce
# expires exactly at the last moment an envelope with issued_at=(now-300) is
# still valid, then lazy eviction removes it before the next check_and_add call
# (ADR-0099 iter-4 finding LOW-IT4-01).  700 s = 2 × 300 + 100 s safety margin.
_NONCE_TTL_S: float = 700.0

# Default rate limit applied to origins that do not configure rate_limit_rpm.
# 60 RPM = 1 request/second — permissive enough for legitimate multi-step A2A
# flows, but prevents unbounded compute-flooding by a compromised or malicious
# peer. Operators who need higher throughput set rate_limit_rpm explicitly in
# their origin config; set rate_limit_rpm: 0 to disable rate limiting entirely.
# (ADR-0144 A2A-fix: rate_limit_rpm previously had no safe default.)
_DEFAULT_RATE_LIMIT_RPM: int = 60

# ── Protocol version ──────────────────────────────────────────────────────
# ADR-0153 M5: current protocol version supported by this receiver.
# Senders/receivers at v7 and earlier continue to work — v8 merely adds the
# optional corvin_id_jwt field (additive, backward-compatible).
PROTOCOL_VERSION: int = 8


# ── Exceptions ────────────────────────────────────────────────────────────

class A2AError(Exception):
    """Base for all A2A-internal errors. Never surfaced to the caller."""


class AuditWriteError(A2AError):
    """Raised when the L16 audit write fails; blocks the entire request."""


class ValidationError(A2AError):
    """Raised for any envelope validation failure. reason is audit-only.

    Post-HMAC failures (rate_limited, replay, purpose_not_allowed, etc.) carry
    recv_key so the caller can produce a signed rejection (ADR-0077 C-5).
    Pre-HMAC failures leave recv_key as None → unsigned rejection.
    """

    def __init__(self, reason: str, recv_key: bytes | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.recv_key: bytes | None = recv_key


class _InjectionRejected(A2AError):
    """Internal: injection defence tripped (M2 worker path). Mapped to
    A2A.request_rejected by the caller; identical external surface as a
    bad signature."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── Dataclasses ───────────────────────────────────────────────────────────

@dataclass
class TaskEnvelope:
    task_id: str
    nonce: str
    issued_at: float
    origin_id: str
    instruction: str
    result_schema: dict
    ttl_s: int
    sender_instance_id: str
    attachments: list           # v3: list of {name, mime, sha256, content_b64}
    signature: str
    # ADR-0077 C-2 — Protocol v4: optional purpose declaration.
    purpose_id: str | None = None
    # ADR-0078 Phase 1 — Protocol v5: optional sender attestation (IAC).
    # Included in the HMAC payload when present so it cannot be stripped
    # or replaced in transit. v4 envelopes without this field continue to
    # work (canonical_payload() omits None fields).
    sender_attestation: dict | None = None
    # ADR-0103 M2 — Protocol v6: optional A2A network membership attestation.
    # Contains {sest_fp, sest_sig, pairing_id, attested_at}. Distinct from
    # sender_attestation (IAC) — these two fields serve orthogonal purposes.
    # Included in HMAC payload when present. Older senders omit the field;
    # receivers allow absence during the grace period.
    network_attestation: dict | None = None
    # ADR-0116 M4 — Protocol v7: optional sender chain tail for cross-peer
    # anchoring. hash of sender's last audit record, included in HMAC payload
    # so it cannot be stripped in transit. Pre-M4 senders omit this field;
    # receivers that encounter it emit A2A.chain_anchor_received.
    sender_chain_tail: str | None = None
    # ADR-0117 M4 — Protocol v8: optional sender genesis hash for chain DNA
    # verification. sha256 of the sender's chain.genesis event, included in
    # HMAC so it cannot be replaced in transit. Receivers check it against
    # the peer_genesis_hash stored in the origin config (set at pairing time).
    # Pre-M4 senders omit the field; receivers apply grace-period behaviour.
    sender_genesis_hash: str | None = None
    # ADR-0145 M2 — IBC Protocol v9: optional per-message instance attestation.
    # Contains {ibc_jti, ed25519_sig, ibc_snapshot}. Included in HMAC payload
    # so it cannot be stripped or swapped in transit. Receivers that pre-date
    # ADR-0145 ignore the field (additive, backward-compatible).
    instance_attestation: dict | None = None
    # ADR-0153 M5 — Protocol v8 (CorvinID): optional CorvinID JWT from the
    # Corvin Labs identity service. Carries RS256-signed operator identity claims;
    # verified best-effort by the receiver (missing PyJWT → skip; invalid → WARNING
    # audit only, envelope NOT rejected). Included in HMAC payload so it cannot be
    # stripped or swapped in transit. Capped at 8 192 chars. Pre-M5 receivers that
    # do not know this field ignore it (additive, backward-compatible).
    corvin_id_jwt: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "TaskEnvelope":
        required = {
            "task_id", "nonce", "issued_at", "origin_id",
            "instruction", "result_schema", "ttl_s",
            "sender_instance_id", "attachments", "signature",
        }
        missing = required - set(d.keys())
        if missing:
            raise ValidationError(f"missing_fields:{','.join(sorted(missing))}")
        try:
            purpose_raw = d.get("purpose_id")
            attestation_raw = d.get("sender_attestation")
            net_att_raw = d.get("network_attestation")
            inst_att_raw = d.get("instance_attestation")
            corvin_id_jwt_raw = d.get("corvin_id_jwt")
            issued_at_val = float(d["issued_at"])
            if not math.isfinite(issued_at_val):
                raise ValidationError("issued_at_not_finite")
            # LOW-02 (ADR-0099): bound field lengths to prevent DB bloat and
            # audit-log pollution from authenticated origins sending oversized
            # values.  Limits are generous for any legitimate use case.
            task_id_val = str(d["task_id"])
            if len(task_id_val) > 256:
                raise ValidationError("task_id_too_long")
            nonce_val = str(d["nonce"])
            if len(nonce_val) > 256:
                raise ValidationError("nonce_too_long")
            origin_id_val = str(d["origin_id"])
            if len(origin_id_val) > 256:
                raise ValidationError("origin_id_too_long")
            sig_val = str(d["signature"])
            if len(sig_val) > 512:
                raise ValidationError("signature_too_long")
            sender_iid_val = str(d["sender_instance_id"])
            if len(sender_iid_val) > 128:
                raise ValidationError("sender_instance_id_too_long")
            ttl_s_val = int(d["ttl_s"])
            if ttl_s_val < 1:
                raise ValidationError("ttl_too_small")
            # Reject deeply-nested result_schema — prevents RecursionError before
            # HMAC is verified (ADR-0099 iter-4 finding LOW-IT4-06).
            import json as _json
            schema_raw = _json.dumps(d["result_schema"])
            if len(schema_raw) > 65536:
                raise ValidationError("result_schema_too_large")
            # ADR-0116 M4: sender_chain_tail (optional, additive).
            # HMAC-coverage already ensures integrity; we only sanitize length
            # and character set (printable ASCII, no whitespace or control chars).
            sct_raw = d.get("sender_chain_tail")
            sct_val: str | None = None
            if isinstance(sct_raw, str) and sct_raw:
                sct_clean = sct_raw[:64]
                if sct_clean.isprintable() and " " not in sct_clean:
                    sct_val = sct_clean
            # ADR-0117 M4: sender_genesis_hash (optional, additive).
            # Must be a 64-char lowercase hex string (SHA-256 of genesis block).
            sgh_raw = d.get("sender_genesis_hash")
            sgh_val: str | None = None
            if isinstance(sgh_raw, str) and len(sgh_raw) == 64:
                sgh_clean = sgh_raw.lower()
                if all(c in "0123456789abcdef" for c in sgh_clean):
                    sgh_val = sgh_clean
            return cls(
                task_id=task_id_val,
                nonce=nonce_val,
                issued_at=issued_at_val,
                origin_id=origin_id_val,
                instruction=str(d["instruction"]),
                result_schema=dict(d["result_schema"]),
                ttl_s=ttl_s_val,
                sender_instance_id=sender_iid_val,
                attachments=list(d["attachments"]),
                signature=sig_val,
                purpose_id=str(purpose_raw)[:64] if purpose_raw is not None else None,
                sender_attestation=dict(attestation_raw) if isinstance(attestation_raw, dict) else None,
                network_attestation=dict(net_att_raw) if isinstance(net_att_raw, dict) else None,
                sender_chain_tail=sct_val,
                sender_genesis_hash=sgh_val,
                instance_attestation=dict(inst_att_raw) if isinstance(inst_att_raw, dict) else None,
                corvin_id_jwt=str(corvin_id_jwt_raw)[:8192] if isinstance(corvin_id_jwt_raw, str) else None,
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError(f"type_error:{type(exc).__name__}") from exc

    def canonical_payload(self) -> bytes:
        d = asdict(self)
        d.pop("signature")
        # Omit None optional fields so older senders produce matching HMACs.
        if d.get("purpose_id") is None:
            d.pop("purpose_id", None)
        if d.get("sender_attestation") is None:
            d.pop("sender_attestation", None)
        if d.get("network_attestation") is None:
            d.pop("network_attestation", None)
        # ADR-0116 M4: omit sender_chain_tail when None (backward compat).
        if d.get("sender_chain_tail") is None:
            d.pop("sender_chain_tail", None)
        # ADR-0117 M4: omit sender_genesis_hash when None (backward compat).
        if d.get("sender_genesis_hash") is None:
            d.pop("sender_genesis_hash", None)
        # ADR-0145 M2: omit instance_attestation when None (backward compat with pre-IBC senders).
        if d.get("instance_attestation") is None:
            d.pop("instance_attestation", None)
        # ADR-0153 M5: omit corvin_id_jwt when None (backward compat with pre-v8 senders).
        if d.get("corvin_id_jwt") is None:
            d.pop("corvin_id_jwt", None)
        return json.dumps(
            d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()


@dataclass
class ResponseEnvelope:
    task_id: str
    origin_id: str
    issued_at: float
    instance_id: str   # receiver's local UUID (attests responder identity)
    status: str        # "ok" | "filtered" | "rejected" | "timeout"
    data: dict
    attachments: list  # v3: list of {name, mime, sha256, content_b64}
    signature: str
    # ADR-0116 M4: hash of receiver's last audit record after writing
    # A2A.envelope_received — included in HMAC for cross-peer anchoring.
    # Empty string = unavailable (best-effort). Pre-M4 senders ignore this field.
    receiver_chain_tail: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # ADR-0116 M4: omit receiver_chain_tail when absent for wire compat.
        if not d.get("receiver_chain_tail"):
            d.pop("receiver_chain_tail", None)
        return d

    def canonical_payload(self) -> bytes:
        d = asdict(self)
        d.pop("signature")
        # ADR-0116 M4: omit receiver_chain_tail from HMAC when absent so
        # pre-M4 senders/verifiers remain compatible (additive, backward-compat).
        if not d.get("receiver_chain_tail"):
            d.pop("receiver_chain_tail", None)
        return json.dumps(
            d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()


# ── NonceStore ────────────────────────────────────────────────────────────

class NonceStore:
    """Thread-safe in-memory nonce store.

    LRU-evicted at _NONCE_MAX entries. Nonces expire after _NONCE_TTL_S.
    Per-origin slot quota prevents one origin from filling the store and
    starving others (cross-origin availability attack — finding MED-IT4-07).
    Allows injecting a custom TTL for testing via the _ttl_s parameter.
    """

    # One origin may hold at most this many non-expired nonce slots.
    # With _NONCE_MAX=10_000 and 4 concurrent origins the budget splits evenly.
    _PER_ORIGIN_MAX: int = _NONCE_MAX // 4  # = 2_500

    def __init__(self, ttl_s: float | None = None) -> None:
        self._ttl_s = ttl_s if ttl_s is not None else _NONCE_TTL_S
        self._store: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._origin_map: dict[str, str] = {}   # nonce → origin_id
        self._origin_count: dict[str, int] = {}  # origin_id → live-slot count
        self._lock = threading.Lock()

    def check_and_add(self, nonce: str, origin_id: str = "") -> bool:
        """Return True if nonce is fresh (added). False = replay or quota exceeded."""
        now = time.time()
        with self._lock:
            self._expire(now)
            if nonce in self._store:
                return False
            # Per-origin quota — cap one origin's share of the global store.
            if origin_id:
                if self._origin_count.get(origin_id, 0) >= self._PER_ORIGIN_MAX:
                    return False
            # Global capacity guard: only evict expired entries.
            while len(self._store) >= _NONCE_MAX:
                oldest_k, oldest_exp = next(iter(self._store.items()))
                if oldest_exp <= now:
                    old_origin = self._origin_map.pop(oldest_k, "")
                    if old_origin:
                        self._origin_count[old_origin] = max(
                            0, self._origin_count.get(old_origin, 0) - 1
                        )
                    del self._store[oldest_k]
                else:
                    return False
            self._store[nonce] = now + self._ttl_s
            if origin_id:
                self._origin_map[nonce] = origin_id
                self._origin_count[origin_id] = self._origin_count.get(origin_id, 0) + 1
            return True

    def remove(self, nonce: str) -> None:
        """Remove a nonce — used to roll back after a failed audit-first write."""
        with self._lock:
            if nonce in self._store:
                del self._store[nonce]
                origin_id = self._origin_map.pop(nonce, "")
                if origin_id:
                    self._origin_count[origin_id] = max(
                        0, self._origin_count.get(origin_id, 0) - 1
                    )

    def _expire(self, now: float) -> None:
        expired = [k for k, exp in self._store.items() if exp <= now]
        for k in expired:
            origin_id = self._origin_map.pop(k, "")
            if origin_id:
                self._origin_count[origin_id] = max(
                    0, self._origin_count.get(origin_id, 0) - 1
                )
            del self._store[k]


# ── OriginRegistry ────────────────────────────────────────────────────────

class OriginRegistry:
    """Per-call origin config loader. No in-process cache (hot-rotation)."""

    def __init__(self, origins_dir: Path | str | None = None) -> None:
        env = os.environ.get(_REMOTE_ORIGINS_ENV)
        if env:
            self._dir = Path(env)
        elif origins_dir is not None:
            self._dir = Path(origins_dir)
        else:
            self._dir = _REMOTE_ORIGINS_DEFAULT

    def count_enabled(self) -> int:
        """Return the number of currently ENABLED origin files on disk.

        FND-23: this is only ever called on the new-peer registration path
        (``load()`` when the origin file does not yet exist), which is rare —
        NOT per inbound envelope — so parsing each file to honour its
        ``enabled`` flag is cheap and makes the a2a_peers_max gate accurate.
        The previous implementation counted every ``*.json`` (incl. disabled
        stubs), which could spuriously block a legitimate new peer when stale
        disabled files linger. A file that can't be parsed is counted
        conservatively (treated as present/enabled) so a corrupt file never
        opens the gate wider than intended."""
        if not self._dir.exists():
            return 0
        n = 0
        for p in self._dir.glob("*.json"):
            if not p.is_file():
                continue
            try:
                cfg = json.loads(p.read_text())
                if cfg.get("enabled", False):
                    n += 1
            except (OSError, ValueError):
                # Unreadable / malformed → count conservatively (present).
                n += 1
        return n

    def load(self, origin_id: str) -> dict:
        """Load and return origin config.

        Raises ValidationError for unknown, disabled, or insecure origins.
        ADR-0092: also raises ValidationError when the a2a_peers_max licence
        limit would be exceeded (checked against the current file count so
        existing origins are never blocked, only registration of new ones).
        """
        # Guard against path traversal
        if (
            not origin_id
            or "/" in origin_id
            or "\\" in origin_id
            or origin_id.startswith(".")
            or ":" in origin_id
        ):
            raise ValidationError("invalid_origin_id")

        path = self._dir / f"{origin_id}.json"

        # ADR-0092 — a2a_peers_max gate.
        # Only enforced when the origin file does NOT yet exist (new peer).
        # Existing origins are never blocked by a licence downgrade — that
        # would create a hard outage rather than a graceful degradation.
        if not path.exists():
            try:
                import sys as _sys
                import pathlib as _pl
                _lic_path = str(_pl.Path(__file__).resolve().parents[2])
                if _lic_path not in _sys.path:
                    _sys.path.insert(0, _lic_path)
                from license.validator import assert_limit as _lic_assert_limit  # type: ignore
                from license.limits import LicenseLimitError as _LicLimitError  # type: ignore
                current = self.count_enabled()
                try:
                    _lic_assert_limit("a2a_peers_max", current + 1)
                except _LicLimitError as _le:
                    raise ValidationError("a2a_peers_max_exceeded") from _le
            except ValidationError:
                raise
            except Exception as _lic_exc:  # noqa: BLE001
                # LOW-01 (ADR-0099): licence gate is advisory — fail-open on
                # import errors, but log visibly so operators know the gate
                # is inactive.  Silently passing could hide misconfiguration.
                import sys as _sys
                print(
                    f"[remote_trigger_receiver] WARNING: a2a_peers_max licence "
                    f"gate unavailable ({type(_lic_exc).__name__}) — peer count "
                    f"limit will not be enforced for this request.",
                    file=_sys.stderr, flush=True,
                )

        if not path.exists():
            raise ValidationError("unknown_origin")

        # Mode 0600: no group/other bits
        file_stat = path.stat()
        if file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValidationError("origin_file_world_readable")

        with path.open() as fh:
            config = json.load(fh)

        if not config.get("enabled", False):
            raise ValidationError("origin_disabled")

        # R1 finding: an enabled origin with a missing or non-hex hmac_key /
        # recv_key would later blow up in bytes.fromhex() OUTSIDE the validated
        # path, escaping receive()'s ValidationError handler and violating its
        # documented "never raises" contract (caller gets an unhandled exception
        # instead of a signed/audited rejection). Validate the key material here
        # so a malformed origin file surfaces as a clean ValidationError.
        for _k in ("hmac_key", "recv_key"):
            _v = config.get(_k)
            try:
                if not isinstance(_v, str) or not bytes.fromhex(_v):
                    raise ValueError
            except (TypeError, ValueError):
                raise ValidationError("origin_key_malformed")

        return config


# ── RemoteTriggerReceiver ─────────────────────────────────────────────────

class RemoteTriggerReceiver:
    """Validates signed TaskEnvelopes; returns signed ResponseEnvelopes.

    M1: validation + audit anchoring; no WorkerEngine spawn.
    M2: optional WorkerEngine spawn — opt-in per origin via
        ``spawn_worker: true`` in the origin config file. When off, the
        receiver behaves exactly as M1.

    Thread-safe: multiple requests may be handled concurrently.
    """

    def __init__(
        self,
        origins_dir: Path | None = None,
        nonce_store: Any = None,  # NonceStore | PersistentNonceStore | None
        *,
        engine_factory: Any = None,
        force_m1_only: bool | None = None,
        instance_id: str | None = None,
        tenant_home: Path | None = None,
        forge_se: Any = None,
    ) -> None:
        self._registry = OriginRegistry(origins_dir)
        if nonce_store is not None:
            self._nonces = nonce_store
        else:
            # ADR-0077 S-2: prefer persistent SQLite store; fall back to
            # in-memory when the DB path is not constructable.
            try:
                from a2a_nonce_store import default_nonce_store  # type: ignore[import-not-found]
                self._nonces = default_nonce_store(tenant_home)
            except Exception as _ns_exc:
                # The in-memory nonce store does NOT survive process restarts: a
                # signed envelope captured before a restart can be replayed within
                # the ±300 s window. FAIL-CLOSED by default (security-audit
                # 2026-06-25 #10) — refuse to construct the receiver rather than
                # silently degrade replay protection to per-process. Operators on
                # an isolated/dev host may opt in to the ephemeral store with
                # CORVIN_A2A_ALLOW_EPHEMERAL_NONCE=1. (ADR-0099 iter-4 CRIT-IT4-01.)
                import sys as _sys
                self._audit_nonce_fallback()
                if os.environ.get("CORVIN_A2A_ALLOW_EPHEMERAL_NONCE") != "1":
                    raise RuntimeError(
                        f"A2A persistent nonce store unavailable "
                        f"({type(_ns_exc).__name__}: {_ns_exc}) — refusing to start "
                        "(replay protection would be per-process only). Make "
                        "a2a_nonce_store importable and its DB path writable, or set "
                        "CORVIN_A2A_ALLOW_EPHEMERAL_NONCE=1 to accept the risk on an "
                        "isolated host."
                    ) from _ns_exc
                print(
                    f"[a2a] WARNING: persistent nonce store unavailable "
                    f"({type(_ns_exc).__name__}: {_ns_exc}) — "
                    "using IN-MEMORY nonce store (CORVIN_A2A_ALLOW_EPHEMERAL_NONCE=1). "
                    "Signed envelopes CAN BE REPLAYED after process restart.",
                    file=_sys.stderr, flush=True,
                )
                self._nonces = NonceStore()
        self._engine_factory = engine_factory
        if force_m1_only is None:
            force_m1_only = os.environ.get("CORVIN_A2A_M1_ONLY", "") == "1"
        self._force_m1_only = bool(force_m1_only)
        # ADR-0077 S-3: per-origin token-bucket rate limiters.
        # {origin_id: {"tokens": float, "last_refill": float}}
        # MED-05 (ADR-0099): bounded at _RATE_BUCKETS_MAX to prevent
        # unbounded memory growth from many short-lived origin_ids.
        self._rate_buckets: dict[str, dict] = {}
        self._rate_lock = threading.Lock()
        # Cache instance_id at construction time so two receivers in the
        # same process can hold distinct identities (bidirectional E2E).
        if instance_id is not None:
            self._instance_id = instance_id
        else:
            try:
                self._instance_id = get_instance_id()
            except Exception:
                self._instance_id = ""
        # LOW-03 (ADR-0099): an empty instance_id means all ResponseEnvelopes
        # carry instance_id="" which breaks endpoint pins. Log visibly so
        # operators can detect the misconfiguration before it causes silent
        # pin failures on the cloud-side sender.
        if not self._instance_id:
            import sys as _sys
            print(
                "[remote_trigger_receiver] WARNING: instance_id is empty "
                "(instance_id.json missing or unreadable). All ResponseEnvelopes "
                "will carry instance_id=\"\" which breaks endpoint instance_id "
                "pins. Run 'corvin-instance-id show' to regenerate.",
                file=_sys.stderr, flush=True,
            )
        # Injected forge_se for test isolation (avoids module-level patch conflicts).
        self._inst_forge_se = forge_se

    # ── Public API ────────────────────────────────────────────────────

    def receive(self, envelope_dict: dict) -> ResponseEnvelope:
        """Validate, audit, and return a signed ResponseEnvelope.

        This method NEVER raises — all errors produce a "rejected" response.
        """
        start = time.time()
        task_id = str(envelope_dict.get("task_id", ""))
        origin_id = str(envelope_dict.get("origin_id", ""))

        # Validate steps 1–6 (+ 2.5 purpose, 6.5 attachments)
        try:
            env, origin_config = self._validate(envelope_dict)
        except ValidationError as exc:
            # C-5: use exc.recv_key (set after HMAC verification) to sign
            # the rejection. Pre-HMAC failures have recv_key=None → unsigned.
            resp = self._rejected_response(task_id, origin_id, exc.recv_key)
            self._audit_best_effort(
                "A2A.request_rejected", "WARNING",
                {"task_id": task_id, "origin_id": origin_id,
                 "reason": exc.reason, "status": "rejected",
                 "duration_ms": _ms(start)},
            )
            return resp

        # Rate-limit check is now in _validate() step 5.6 (before nonce consumption).
        recv_key_bytes = bytes.fromhex(origin_config["recv_key"])

        # Step 7: Audit-first (strict — failure blocks the request)
        # Inbound attachment counts go into the audit details (no content).
        # Attachments were already validated inside _validate() (step 6.75).
        # Re-use env.attachments directly here to avoid a redundant second
        # validate_attachments() call (LOW finding: double-validation).
        _att_validation_error: str | None = None
        try:
            from a2a_attachments import (  # type: ignore[import-not-found]
                attachments_audit_details,
                effective_classification, classification_level,
                validate_attachments,
            )
            # env.attachments are raw dicts off the wire. The audit/
            # classification helpers below require typed ``Attachment``
            # instances (they read ``a.classification`` etc.), so coerce
            # here. The attachments already passed validate_attachments()
            # in _validate() step 6.75, so this re-parse cannot fail — it
            # only rebuilds the typed objects (the prior "reuse env.attachments
            # directly" shortcut fed dicts to object-expecting helpers and
            # raised AttributeError, fail-closing every attachment).
            _inbound_atts = (
                validate_attachments(env.attachments) if env.attachments else []
            )
            _att_details = attachments_audit_details(_inbound_atts)
        except Exception as _att_exc:
            _inbound_atts = []
            _att_validation_error = type(_att_exc).__name__
            _att_details = {"attachments_count": 0,
                            "attachments_total_bytes": 0,
                            "attachment_names": [],
                            "attachment_sha_prefixes": [],
                            "attachment_validation_error": _att_validation_error}

        try:
            audit_detail = {
                "task_id": env.task_id, "origin_id": env.origin_id,
                "nonce_prefix": env.nonce[:8], "ttl_s": env.ttl_s,
                "sender_instance_id": env.sender_instance_id,
                "status": "received",
                **_att_details,
            }
            if env.purpose_id is not None:
                audit_detail["purpose_id"] = env.purpose_id[:64]
            self._audit_strict("A2A.envelope_received", "INFO", audit_detail)
        except AuditWriteError as exc:
            # Roll back the nonce so the sender can retry — the nonce was
            # consumed in _validate() before the audit write; without rollback
            # a transient audit failure would permanently burn the nonce
            # (finding MED-IT4-06, audit-first invariant violation).
            self._nonces.remove(env.nonce)
            reason = f"audit_write_failed:{exc}"
            resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
            self._audit_best_effort(
                "A2A.request_rejected", "WARNING",
                {"task_id": env.task_id, "origin_id": env.origin_id,
                 "reason": reason, "status": "rejected",
                 "duration_ms": _ms(start)},
            )
            return resp

        if _att_validation_error is not None:
            resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
            self._audit_best_effort(
                "A2A.request_rejected", "WARNING",
                {"task_id": env.task_id, "origin_id": env.origin_id,
                 "reason": f"attachment_validation_error:{_att_validation_error}",
                 "status": "rejected", "duration_ms": _ms(start)},
            )
            return resp

        # ADR-0077 C-6: check attachment classification against origin cap.
        # Fail-closed on exception: a silently broken classification check
        # allows CONFIDENTIAL attachments through a PUBLIC-only origin
        # (HIGH-03, ADR-0099). An unexpected exception is treated as a
        # classification failure, not as "unable to determine → allow".
        try:
            from a2a_attachments import effective_classification as _eff_cls, classification_level as _cls_level  # type: ignore[import-not-found]
            max_cls = str(origin_config.get("max_data_classification", "INTERNAL")).upper()
            eff_cls = _eff_cls(_inbound_atts)
            if _cls_level(eff_cls) > _cls_level(max_cls):
                resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
                self._audit_best_effort(
                    "A2A.request_rejected", "WARNING",
                    {"task_id": env.task_id, "origin_id": env.origin_id,
                     "reason": "attachment_classification_exceeded",
                     "status": "rejected", "duration_ms": _ms(start)},
                )
                return resp
        except Exception as _cls_exc:
            resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
            self._audit_best_effort(
                "A2A.request_rejected", "WARNING",
                {"task_id": env.task_id, "origin_id": env.origin_id,
                 "reason": "classification_check_error",
                 "status": "rejected", "duration_ms": _ms(start)},
            )
            return resp

        # ADR-0116 M4: emit chain_anchor_received when the sender included
        # sender_chain_tail in the HMAC-verified TaskEnvelope.
        _sender_tail = getattr(env, "sender_chain_tail", None)
        if isinstance(_sender_tail, str) and _sender_tail:
            self._audit_best_effort(
                "A2A.chain_anchor_received", "INFO",
                {"task_id": env.task_id, "origin_id": env.origin_id,
                 "peer_chain_tail": _sender_tail[:16],
                 "nonce_prefix": env.nonce[:8]},
            )

        # ADR-0117 M4: verify sender chain DNA (genesis hash).
        _sgh = getattr(env, "sender_genesis_hash", None)
        try:
            from nbac import genesis_hash_matches, origin_genesis_hash  # type: ignore[import-not-found]
            _expected_genesis = origin_genesis_hash(origin_config)
            if _sgh and not genesis_hash_matches(_sgh, origin_config):
                # Hard mismatch — sender's chain belongs to a different network.
                self._audit_best_effort(
                    "A2A.chain_dna_mismatch", "WARNING",
                    {"task_id": env.task_id, "origin_id": env.origin_id,
                     "peer_genesis_hash_prefix": _sgh[:16],
                     "expected_prefix": _expected_genesis[:16] if _expected_genesis else ""},
                )
                # In grace-period mode: log but do not reject.  After the grace
                # period (configurable), this becomes a hard reject via M4 config.
                _dna_strict = bool(origin_config.get("nbac_strict", False))
                if _dna_strict:
                    resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
                    self._audit_best_effort(
                        "A2A.request_rejected", "WARNING",
                        {"task_id": env.task_id, "origin_id": env.origin_id,
                         "reason": "chain_dna_genesis_mismatch", "status": "rejected",
                         "duration_ms": _ms(start)},
                    )
                    return resp
            elif _sgh:
                # _sgh present and genesis_hash_matches() returned True — but it
                # also returns True when the origin has NO configured
                # peer_genesis_hash (treated as "unverifiable"). Under
                # nbac_strict the operator bound this origin to a known chain
                # DNA, so an UNVERIFIABLE match (no expected hash to compare
                # against) must fail-closed rather than log "verified" + allow:
                # otherwise a forked peer could send any _sgh and pass. (Review
                # 2026-06-17 — sibling of the R2-02 absent-field fix below.)
                _dna_strict = bool(origin_config.get("nbac_strict", False))
                if _dna_strict and _expected_genesis is None:
                    resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
                    self._audit_best_effort(
                        "A2A.request_rejected", "WARNING",
                        {"task_id": env.task_id, "origin_id": env.origin_id,
                         "reason": "chain_dna_no_expected_hash", "status": "rejected",
                         "duration_ms": _ms(start)},
                    )
                    return resp
                self._audit_best_effort(
                    "A2A.chain_dna_verified", "INFO",
                    {"task_id": env.task_id, "origin_id": env.origin_id,
                     "peer_genesis_hash_prefix": _sgh[:16]},
                )
            else:
                # R2-02: an ABSENT sender_genesis_hash previously always fell
                # through here (WARNING + allow), even with nbac_strict. The
                # mismatch branch's `_sgh and …` short-circuits on absence, so a
                # forked peer holding the leaked HMAC key could bypass the
                # chain-DNA binding simply by OMITTING the field. When nbac_strict
                # is set the operator has chosen to bind this origin to a known
                # chain DNA — treat absence as a hard reject, mirroring the
                # network_attestation_required gate.
                _dna_strict = bool(origin_config.get("nbac_strict", False))
                if _dna_strict:
                    resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
                    self._audit_best_effort(
                        "A2A.request_rejected", "WARNING",
                        {"task_id": env.task_id, "origin_id": env.origin_id,
                         "reason": "chain_dna_genesis_absent", "status": "rejected",
                         "duration_ms": _ms(start)},
                    )
                    return resp
                # Grace mode (default): sender pre-dates ADR-0117 — emit absent
                # event (best-effort) and allow.
                self._audit_best_effort(
                    "A2A.chain_dna_genesis_absent", "WARNING",
                    {"task_id": env.task_id, "origin_id": env.origin_id,
                     "nonce_prefix": env.nonce[:8]},
                )
        except ValidationError:
            raise
        except Exception:  # noqa: BLE001 — nbac import may be absent in older deployments
            pass

        # ADR-0078 Phase 1: emit attestation audit fields (best-effort).
        try:
            from instance_attestation import (  # type: ignore[import-not-found]
                verify_attestation as _va, get_ca_pubkey_bytes as _gcpb,
                attestation_audit_fields as _aaf, TrustLevel as _TL,
            )
            _att_level = _va(env.sender_attestation, _gcpb()) \
                if env.sender_attestation is not None else _TL.UNVERIFIED
            _att_audit = _aaf(env.sender_attestation, _att_level)
            _att_event = "instance.attestation_verified" \
                if _att_level > _TL.UNVERIFIED else "instance.attestation_failed" \
                if env.sender_attestation is not None else None
            if _att_event:
                self._audit_best_effort(
                    _att_event, "INFO" if _att_level > _TL.UNVERIFIED else "WARNING",
                    {"task_id": env.task_id, "origin_id": env.origin_id,
                     **_att_audit},
                )
        except Exception:  # noqa: BLE001
            pass

        # ADR-0077 C-1: per-origin consent gate for personal-data origins.
        if origin_config.get("personal_data", False):
            required_purposes = origin_config.get("required_consent_purposes") or []
            if required_purposes:
                subject_id = str(origin_config.get("data_subject_id", ""))
                consent_ok = self._check_consent(
                    subject_id, required_purposes, origin_id=env.origin_id
                )
                if not consent_ok:
                    resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
                    self._audit_best_effort(
                        "A2A.request_rejected", "WARNING",
                        {"task_id": env.task_id, "origin_id": env.origin_id,
                         "reason": "consent_not_granted", "status": "rejected",
                         "duration_ms": _ms(start)},
                    )
                    return resp

        # ADR-0133 CLAG M4 — chain integrity gate before instruction execution (L38).
        # Runs after A2A.envelope_received (audit-first); unique layer_id avoids
        # shadow conflicts from the many intermediate best-effort audit events.
        _clag_lid = f"L38.a2a_instruction.{secrets.token_hex(4)}"
        try:
            _clag_gate_a2a(_clag_lid)
        except Exception as _clag_exc:
            if "ChainIntegrityFailure" in type(_clag_exc).__name__:
                resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
                self._audit_best_effort(
                    "A2A.request_rejected", "WARNING",
                    {"task_id": env.task_id, "origin_id": env.origin_id,
                     "reason": "chain_integrity_failed", "status": "rejected",
                     "duration_ms": _ms(start)},
                )
                return resp
            raise

        # M1 vs M2: decide whether to spawn a worker.
        spawn_worker = (
            (not self._force_m1_only)
            and bool(origin_config.get("spawn_worker", False))
        )

        if spawn_worker:
            try:
                worker_status, worker_data, worker_attachments = (
                    self._spawn_and_filter(
                        env=env, origin_config=origin_config,
                        start=start, inbound_attachments=_inbound_atts,
                    )
                )
            except _InjectionRejected as exc:
                # C-5: sign the rejection with recv_key (we have it now)
                resp = self._rejected_response(env.task_id, env.origin_id, recv_key_bytes)
                self._audit_best_effort(
                    "A2A.request_rejected", "WARNING",
                    {"task_id": env.task_id, "origin_id": env.origin_id,
                     "reason": f"injection_attempt:{exc.reason}",
                     "status": "rejected",
                     "duration_ms": _ms(start)},
                )
                return resp
        else:
            # M1 fallback / opt-out: no spawn, empty data.
            self._audit_best_effort(
                "A2A.engine_spawned", "INFO",
                {"task_id": env.task_id, "origin_id": env.origin_id,
                 "status": "skipped_m1"},
            )
            worker_data = self._filter_result({}, env.result_schema)
            self._audit_best_effort(
                "A2A.result_filtered", "INFO",
                {"task_id": env.task_id, "origin_id": env.origin_id,
                 "filter_pass_count": len(worker_data),
                 "filter_reject_count": 0,
                 "status": "filtered"},
            )
            worker_status = "ok"
            worker_attachments = []

        # Build and sign response (recv_key_bytes already resolved above)
        recv_key = recv_key_bytes
        resp = self._build_response(
            env.task_id, env.origin_id, worker_status, worker_data, recv_key,
            attachments=worker_attachments,
        )

        try:
            from a2a_attachments import attachments_audit_details  # type: ignore[import-not-found]
            from a2a_attachments import Attachment as _Att  # type: ignore[import-not-found]
            _out_atts_for_audit = [
                _Att.from_dict(a) if isinstance(a, dict) else a
                for a in worker_attachments
            ]
            _out_audit = attachments_audit_details(_out_atts_for_audit)
        except Exception:
            _out_audit = {"attachments_count": len(worker_attachments)}

        self._audit_best_effort(
            "A2A.response_signed", "INFO",
            {"task_id": env.task_id, "origin_id": env.origin_id,
             "status": resp.status, "duration_ms": _ms(start),
             **_out_audit},
        )
        return resp

    # ── M2 worker spawn + filter ──────────────────────────────────────

    def _spawn_and_filter(
        self,
        *,
        env: TaskEnvelope,
        origin_config: dict,
        start: float,
        inbound_attachments: list,
    ) -> tuple[str, dict, list]:
        """Spawn the worker, apply the result filter, return
        (status, data, out_attachments).

        Raises :class:`_InjectionRejected` if the instruction trips the
        prompt-injection defences (caller maps to A2A.request_rejected).
        """
        # Lazy import — keeps M1-only test paths free of agents/ deps.
        try:
            from a2a_worker import (  # type: ignore[import-not-found]
                InjectionAttempt, spawn_a2a_worker,
            )
        except ImportError:
            _shared = Path(__file__).resolve().parent
            if str(_shared) not in sys.path:
                sys.path.insert(0, str(_shared))
            from a2a_worker import (  # type: ignore[import-not-found]
                InjectionAttempt, spawn_a2a_worker,
            )

        # Resolve persona — allowed_personas[0] is the active persona for
        # this origin. Empty list → operator misconfiguration; reject.
        allowed_personas = origin_config.get("allowed_personas") or []
        if not allowed_personas:
            raise _InjectionRejected("no_allowed_personas")
        persona = str(allowed_personas[0])

        # ADR-0144 B5/C7/C8/FC4: derive tool policy from origin config.
        # Defaults (structural defence against credential exfiltration):
        #   - Bash:      blocked unless allow_bash=true      (shell exfil)
        #   - WebFetch:  blocked unless allow_network=true   (network exfil)
        #   - WebSearch: blocked unless allow_network=true   (network exfil)
        #   - Read:      blocked unless allow_read_files=true (Read+schema exfil chain)
        #   - Write/Edit/MultiEdit/NotebookEdit: blocked unless allow_write_files=true
        #     (path_gate hooks are inactive for /tmp workers — see ADR-0144 C8)
        # Operators who need these tools must explicitly opt in per-origin.
        _a2a_allowed: list[str] | None = origin_config.get("allowed_tools")
        _base_disallowed: list[str] = list(origin_config.get("disallowed_tools") or [])
        if not origin_config.get("allow_bash"):
            if "Bash" not in _base_disallowed:
                _base_disallowed.insert(0, "Bash")
        if not origin_config.get("allow_network"):
            for _nt in ("WebFetch", "WebSearch"):
                if _nt not in _base_disallowed:
                    _base_disallowed.append(_nt)
        if not origin_config.get("allow_read_files"):
            for _rt in ("Read", "Grep", "Glob", "LS"):  # Grep/Glob/LS are Read-bypass
                if _rt not in _base_disallowed:
                    _base_disallowed.append(_rt)
        if not origin_config.get("allow_write_files"):
            for _wt in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                if _wt not in _base_disallowed:
                    _base_disallowed.append(_wt)
        if not origin_config.get("allow_subagents"):
            # Task spawns subagents that inherit DEFAULT permissions, not caller's
            # disallowed_tools list — blocks privilege escalation via subagent proxy.
            for _st in ("Task", "TodoWrite", "TodoRead"):
                if _st not in _base_disallowed:
                    _base_disallowed.append(_st)
        _a2a_disallowed: list[str] | None = _base_disallowed or None

        try:
            worker_result = spawn_a2a_worker(
                instruction=env.instruction,
                origin_id=env.origin_id,
                task_id=env.task_id,
                persona=persona,
                ttl_s=env.ttl_s,
                engine_factory=self._engine_factory,
                inbound_attachments=inbound_attachments,
                result_schema=env.result_schema,
                allowed_tools=_a2a_allowed,
                disallowed_tools=_a2a_disallowed,
            )
        except InjectionAttempt as exc:
            raise _InjectionRejected(exc.reason) from exc

        self._audit_best_effort(
            "A2A.engine_spawned", "INFO",
            {"task_id": env.task_id, "origin_id": env.origin_id,
             "persona": persona,
             "engine_id": worker_result.engine_name,
             "duration_ms": worker_result.duration_ms,
             "status": worker_result.status},
        )

        # If the worker failed structurally (timeout/engine error), emit
        # filtered with empty data and propagate the failure status.
        if worker_result.status != "ok":
            self._audit_best_effort(
                "A2A.result_filtered", "INFO",
                {"task_id": env.task_id, "origin_id": env.origin_id,
                 "filter_pass_count": 0, "filter_reject_count": 0,
                 "status": worker_result.status},
            )
            return worker_result.status, {}, []

        # Apply result_schema filter (properties whitelist).
        all_fields = worker_result.parsed_output
        filtered = self._filter_result(all_fields, env.result_schema)
        pass_count = len(filtered)
        reject_count = max(0, len(all_fields) - pass_count)
        # If after filtering we have zero fields AND no attachments, the
        # response is "filtered" (caller's schema accepted nothing).
        out_atts = list(getattr(worker_result, "out_attachments", []))
        if pass_count == 0 and not out_atts:
            status_out = "filtered"
        else:
            status_out = "ok"

        self._audit_best_effort(
            "A2A.result_filtered", "INFO",
            {"task_id": env.task_id, "origin_id": env.origin_id,
             "filter_pass_count": pass_count,
             "filter_reject_count": reject_count,
             "status": status_out,
             "attachments_out_count": len(out_atts)},
        )
        return status_out, filtered, out_atts

    # ── Validation steps 1–6 ─────────────────────────────────────────

    def _check_network_attestation(
        self,
        env: "TaskEnvelope",
        origin_config: dict,
        recv_key: bytes,
    ) -> None:
        """ADR-0103 M2: verify A2A network membership attestation.

        Called after HMAC is verified so the ``network_attestation`` block is
        authenticated before we spend cycles on RS256 verification.

        If ``network_attestation`` is present in the envelope: verify the RS256
        signature of ``sest_fp`` against the embedded ``a2a_network_pubkey.pem``,
        then check ``pairing_id`` and revocation status.

        If absent: consult the network manifest's ``attestation_mandatory_after``
        timestamp.  Before that timestamp the absence is allowed (grace period).
        After it the envelope is rejected.

        Fail-closed on unexpected errors — if the crypto library is absent or the
        pubkey is unreadable we treat it as a verification failure.  Operators
        who need to run without the pubkey (fully isolated networks) can set
        ``CORVIN_A2A_ATTESTATION_DISABLED=1``; this is logged at WARNING level.
        """
        # Allow-out: test mode or explicit operator disable
        if os.environ.get("CORVIN_A2A_ATTESTATION_DISABLED", "0") == "1":
            import sys as _sys
            print(
                "[remote_trigger_receiver] WARNING: CORVIN_A2A_ATTESTATION_DISABLED=1 "
                "— A2A network membership attestation is disabled. "
                "This is unsafe outside of isolated test environments.",
                file=_sys.stderr, flush=True,
            )
            # Emit audit event so the bypass is recorded in the hash chain.
            self._audit_best_effort(
                "A2A.attestation_disabled_bypass", "WARNING",
                {"origin_id": getattr(env, "origin_id", ""), "reason": "CORVIN_A2A_ATTESTATION_DISABLED"},
            )
            return

        na: dict | None = env.network_attestation

        if na is not None:
            # Network attestation present — verify it.
            sest_fp = str(na.get("sest_fp", ""))
            sest_sig = str(na.get("sest_sig", ""))
            pairing_id = str(na.get("pairing_id", ""))
            # R2-03: a non-numeric attested_at (list, dict, non-numeric string)
            # made float() raise ValueError/TypeError here. This runs inside
            # _validate (step 6.8) under receive()'s `except ValidationError`
            # handler only — an uncaught ValueError would escape receive(),
            # violating its never-raises contract (the caller, e.g. the HTTP
            # server, would 500 instead of returning a signed rejection). Coerce
            # safely and reject as a malformed attestation.
            try:
                attested_at = float(na.get("attested_at", 0) or 0)
                # NaN is a valid float but defeats the freshness window:
                # abs(now - nan) > 300 is False, so a NaN attested_at would slip
                # the ±300 s gate. Reject all non-finite values (NaN/Inf).
                if not math.isfinite(attested_at):
                    raise ValueError("non-finite attested_at")
            except (TypeError, ValueError):
                self._audit_best_effort(
                    "a2a.attestation_failed", "WARNING",
                    {"origin_id": env.origin_id, "reason": "attested_at_malformed"},
                )
                raise ValidationError("network_attestation_bad_attested_at", recv_key)

            # 1. attested_at within ±300 s
            if abs(time.time() - attested_at) > _TIME_WINDOW_S:
                self._audit_best_effort(
                    "a2a.attestation_failed", "WARNING",
                    {"origin_id": env.origin_id, "reason": "attested_at_out_of_window"},
                )
                raise ValidationError("network_attestation_time_window", recv_key)

            # 2. Verify RS256 sig
            try:
                self._verify_network_attestation_sig(sest_fp, sest_sig)
            except ValidationError:
                self._audit_best_effort(
                    "a2a.attestation_failed", "WARNING",
                    {"origin_id": env.origin_id,
                     "sest_fp_prefix": sest_fp[:16],
                     "reason": "bad_rs256_sig"},
                )
                raise ValidationError("network_attestation_bad_sig", recv_key)

            # 3. pairing_id matches stored pairing
            # Fail-closed: if the envelope carries a network_attestation (and
            # therefore a pairing_id claim) but the origin file has NO stored
            # pairing_id we cannot verify membership — reject rather than allow.
            # An origin without a stored pairing_id was provisioned without
            # network-membership binding; accepting its attestation claim would
            # let any valid HMAC key bypass the membership check.
            expected_pid = str(origin_config.get("pairing_id", ""))
            if not expected_pid:
                self._audit_best_effort(
                    "a2a.attestation_failed", "WARNING",
                    {"origin_id": env.origin_id,
                     "reason": "network_attestation_without_stored_pairing_id"},
                )
                raise ValidationError("network_attestation_no_stored_pairing_id", recv_key)
            if pairing_id != expected_pid:
                self._audit_best_effort(
                    "a2a.attestation_failed", "WARNING",
                    {"origin_id": env.origin_id, "reason": "pairing_id_mismatch"},
                )
                raise ValidationError("network_attestation_pairing_mismatch", recv_key)

            # 4. Revocation check via manifest
            if _load_a2a_manifest is not None:
                try:
                    manifest = _load_a2a_manifest()
                    if sest_fp in manifest.revoked_sest_fps:
                        self._audit_best_effort(
                            "a2a.attestation_failed", "WARNING",
                            {"origin_id": env.origin_id,
                             "sest_fp_prefix": sest_fp[:16],
                             "reason": "sest_revoked"},
                        )
                        raise ValidationError("network_attestation_revoked", recv_key)
                    if pairing_id and pairing_id in manifest.revoked_pairing_ids:
                        self._audit_best_effort(
                            "a2a.attestation_failed", "WARNING",
                            {"origin_id": env.origin_id, "reason": "pairing_revoked"},
                        )
                        raise ValidationError("network_attestation_pairing_revoked", recv_key)
                    # NOTE (R3-1): instance-level revocation (revoked_instance_ids)
                    # is enforced UNCONDITIONALLY in _validate() post-HMAC, not
                    # here — otherwise a revoked peer omitting network_attestation
                    # would skip this branch entirely. Do not re-add it here.
                except ValidationError:
                    raise
                except Exception:
                    pass  # manifest unavailable — skip revocation check

        else:
            # FND-16: a per-origin require_network_attestation flag forces
            # attestation-present regardless of the manifest grace deadline. The
            # empty-manifest default is "mandatory_after = never", so without a
            # fetched signed manifest the ADR-0103 fork closure is dormant; this
            # flag lets an operator mandate attestation for a sensitive peer
            # independently of the manifest.
            if origin_config.get("require_network_attestation", False):
                self._audit_best_effort(
                    "a2a.attestation_failed", "WARNING",
                    {"origin_id": env.origin_id,
                     "reason": "attestation_required_by_origin_config"},
                )
                raise ValidationError("network_attestation_required", recv_key)
            # Otherwise — check the manifest grace period.
            mandatory_after: float = 9_999_999_999.0
            if _load_a2a_manifest is not None:
                try:
                    manifest = _load_a2a_manifest()
                    mandatory_after = manifest.attestation_mandatory_after
                except Exception:
                    pass

            if time.time() >= mandatory_after:
                self._audit_best_effort(
                    "a2a.attestation_failed", "WARNING",
                    {"origin_id": env.origin_id,
                     "reason": "attestation_required_after_grace"},
                )
                raise ValidationError("network_attestation_required", recv_key)
            # Still in grace period — allow through with no audit noise.

    def _check_layer_integrity_attestation(
        self,
        env: "TaskEnvelope",
        origin_config: dict,
        recv_key: bytes,
    ) -> None:
        """ADR-0141 Tier 2 (step 6.85): verify the sender's ``layer_integrity_hash``.

        The hash travels inside the HMAC-authenticated ``network_attestation``
        block (Protocol v7), so it cannot be altered in transit. The receiver
        compares it against the aggregate derived from its OWN signed layer
        manifest — a sender running tampered code produces a different aggregate
        and is rejected.

        Enforcement is gated by the receiver's manifest, mirroring the network
        attestation grace mechanism:
          * receiver has no valid signed manifest (pre-rollout) -> cannot enforce
            -> allow (the receiver's own boot check surfaces the missing manifest);
          * envelope carries the hash -> must equal the manifest aggregate;
          * envelope omits the hash -> required iff ``require_layer_integrity`` on
            the origin OR the manifest's ``mandatory_after`` deadline has passed.

        Residual (documented, ADR-0141 "accepted residual risks"): an open-source
        fork can hard-code the public manifest aggregate while running modified
        code. The HMAC binds the value in transit; it does not prove the sender
        actually ran the pinned code. Tier 1 (the sender's own boot check) and the
        compensating controls (L10/bwrap/LSAD) cover the local threat.
        """
        if os.environ.get("CORVIN_A2A_ATTESTATION_DISABLED", "0") == "1":
            return  # bypass already audited in _check_network_attestation
        try:
            import layer_integrity as _li  # type: ignore
        except Exception:
            return  # verifier unavailable — cannot enforce, do not fail-open loudly

        manifest = _li.load_manifest()
        if manifest is None or not _li.verify_manifest_signature(manifest):
            return  # no trustworthy manifest on the receiver -> grace
        expected = _li.manifest_layer_integrity_hash(manifest)
        if not expected:
            return  # malformed manifest -> nothing to compare against

        na = env.network_attestation
        claimed = None
        if na is not None and isinstance(na, dict):
            claimed = na.get("layer_integrity_hash")

        if claimed:
            if str(claimed) != expected:
                self._audit_best_effort(
                    "a2a.layer_integrity_mismatch", "WARNING",
                    {"origin_id": env.origin_id, "reason": "hash_mismatch",
                     "protocol_version": 7},
                )
                raise ValidationError("layer_integrity_mismatch", recv_key)
            return  # hash matches the signed manifest -> ok

        # Hash absent — Protocol v7 grace / per-origin enforcement.
        mandatory_after = manifest.get("mandatory_after")
        require = bool(origin_config.get("require_layer_integrity", False))
        past_deadline = (
            isinstance(mandatory_after, (int, float))
            and not isinstance(mandatory_after, bool)
            and time.time() >= float(mandatory_after)
        )
        if require or past_deadline:
            self._audit_best_effort(
                "a2a.layer_integrity_mismatch", "WARNING",
                {"origin_id": env.origin_id, "reason": "layer_integrity_required",
                 "protocol_version": 7},
            )
            raise ValidationError("layer_integrity_required", recv_key)
        # still in grace -> allow

    @staticmethod
    def _verify_network_attestation_sig(sest_fp: str, sest_sig: str) -> None:
        """Raise ValidationError if sest_sig is not a valid RS256 sig over sest_fp.

        ``sest_fp`` = hex-encoded SHA-256(jwt_header + "." + jwt_payload).
        ``sest_sig`` = base64url-encoded RS256 signature bytes (from the JWT).

        Uses the embedded ``operator/license/a2a_network_pubkey.pem`` as the
        trust anchor.  Raises ValidationError on any failure (bad encoding,
        bad sig, missing pubkey, missing crypto library).
        """
        # Import cryptography symbols first — separate try so that ImportError
        # is caught cleanly without leaving InvalidSignature unbound in the
        # subsequent except clause (which would cause NameError in Python 3
        # when cryptography is absent, defeating the fail-closed intent).
        try:
            import base64 as _b64
            from cryptography.hazmat.primitives.serialization import (  # type: ignore
                load_pem_public_key,
            )
            from cryptography.hazmat.primitives.asymmetric import padding as _pad  # type: ignore
            from cryptography.hazmat.primitives import hashes as _hashes  # type: ignore
            from cryptography.hazmat.primitives.asymmetric.utils import (  # type: ignore
                Prehashed,
            )
            from cryptography.exceptions import InvalidSignature as _InvalidSignature  # type: ignore
        except ImportError as exc:
            raise ValidationError(
                f"network_attestation_crypto_error:ImportError"
            ) from exc

        try:
            # Decode inputs
            fp_bytes = bytes.fromhex(sest_fp)
            sig_bytes = _b64.urlsafe_b64decode(sest_sig + "==")

            # Load pubkey
            pubkey = load_pem_public_key(_A2A_NETWORK_PUBKEY_PATH.read_bytes())

            # Verify: fp_bytes IS SHA-256(header.payload) — Prehashed skips rehash
            pubkey.verify(
                sig_bytes,
                fp_bytes,
                _pad.PKCS1v15(),
                Prehashed(_hashes.SHA256()),
            )
        except (ValueError, TypeError):
            raise ValidationError("network_attestation_decode_error")
        except _InvalidSignature:
            raise ValidationError("network_attestation_sig_mismatch")
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(
                f"network_attestation_crypto_error:{type(exc).__name__}"
            ) from exc

    def _load_a2a_network_pubkey(self) -> str | None:
        """Return the A2A network public key PEM string, or None if unavailable.

        Search order: embedded path under operator/license/, then
        CORVIN_IBC_PUBKEY_PEM env var (useful in test environments).
        """
        # Try the well-known embedded path first (set at module level).
        if _A2A_NETWORK_PUBKEY_PATH.exists():
            return _A2A_NETWORK_PUBKEY_PATH.read_text()
        # Fallback: walk up from this file looking for the operator/license/ tree
        # (handles worktree layouts where __file__ is not at the shared/ level).
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "operator" / "license" / "a2a_network_pubkey.pem"
            if candidate.exists():
                return candidate.read_text()
        # Last resort: operator-supplied env var (test / air-gapped deployments).
        env_key = os.environ.get("CORVIN_IBC_PUBKEY_PEM")
        return env_key or None

    def _validate(self, d: dict) -> tuple[TaskEnvelope, dict]:
        # Step 1: Schema
        env = TaskEnvelope.from_dict(d)

        # Step 2: Origin
        origin_config = self._registry.load(env.origin_id)

        # Step 3: Time window
        if abs(time.time() - env.issued_at) > _TIME_WINDOW_S:
            raise ValidationError("time_window")

        # Step 4 (moved after HMAC — see Step 6): TTL cap pre-check
        # This cheap check runs before HMAC to avoid expensive key ops on
        # obviously-invalid envelopes, but the nonce is NOT consumed until
        # after authentication to prevent unauthenticated nonce-burning DoS.
        max_ttl = int(origin_config.get("max_ttl_s", 300))
        if env.ttl_s > max_ttl:
            raise ValidationError("ttl_exceeded")
        # Lower bound enforced in from_dict(); double-check here for defence-in-depth
        # (ADR-0099 iter-4 finding HIGH-IT4-02 — ttl_s=0 causes spin-cycle DoS).
        if env.ttl_s < 1:
            raise ValidationError("ttl_too_small")

        # Step 5: Signature (constant-time compare) — MUST run before nonce
        # is consumed so an unauthenticated flood cannot burn nonce slots.
        key = bytes.fromhex(origin_config["hmac_key"])
        expected = _hmac.new(key, env.canonical_payload(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(expected.lower(), env.signature.lower()):
            raise ValidationError("bad_signature")

        # HMAC verified — all subsequent ValidationErrors carry recv_key so
        # the caller can produce a signed rejection (ADR-0077 C-5).
        recv_key = bytes.fromhex(origin_config["recv_key"])

        # Step 5.5 (ADR-0077 C-2): purpose_id gate — AFTER HMAC so an
        # unauthenticated attacker cannot enumerate valid purpose_ids via
        # response-time differences (ADR-0099 iter-4 finding MED-IT4-03).
        allowed_purposes = origin_config.get("allowed_purposes") or []
        if allowed_purposes:
            if not env.purpose_id:
                raise ValidationError("purpose_id_required", recv_key)
            if env.purpose_id not in allowed_purposes:
                raise ValidationError("purpose_not_allowed", recv_key)

        # Step 5.6: Rate-limit check — AFTER HMAC, BEFORE nonce consumption.
        # Running after HMAC ensures only authenticated origins hit the limiter.
        # Running before nonce consumption ensures rate-limited requests don't
        # burn a nonce, so the sender can retry with the same nonce
        # (ADR-0099 iter-5 finding LOW-IT5-05).
        rate_limit_rpm = origin_config.get("rate_limit_rpm", _DEFAULT_RATE_LIMIT_RPM)
        if rate_limit_rpm is not None:
            try:
                rate_limit_rpm = int(rate_limit_rpm)
            except (TypeError, ValueError):
                rate_limit_rpm = None
        if rate_limit_rpm is not None and rate_limit_rpm > 0:
            if not self._check_rate_limit(env.origin_id, rate_limit_rpm):
                raise ValidationError("rate_limited", recv_key)

        # Step 6: Nonce — consumed only after HMAC is verified.
        # This ordering prevents an unauthenticated attacker from exhausting
        # nonce slots with invalid-HMAC envelopes (CRIT-02).
        # origin_id is passed so the per-origin quota (MED-IT4-07) is enforced.
        if not self._nonces.check_and_add(env.nonce, origin_id=env.origin_id):
            raise ValidationError("replay", recv_key)

        # Step 6.5 (v5, ADR-0078): min_trust / attestation check.
        # Runs AFTER HMAC so the attestation dict is authenticated before
        # we spend cycles on Ed25519 signature verification.
        min_trust_str = origin_config.get("min_trust")
        if min_trust_str:
            try:
                from instance_attestation import (  # type: ignore[import-not-found]
                    parse_min_trust, verify_attestation as _verify_att,
                    TrustLevel, get_ca_pubkey_bytes,
                )
                required = parse_min_trust(min_trust_str)
                if required > TrustLevel.UNVERIFIED:
                    if env.sender_attestation is None:
                        raise ValidationError("attestation_required", recv_key)
                    ca_pub = get_ca_pubkey_bytes()
                    if ca_pub is None:
                        # CA key not configured — fail-closed by default.
                        # An origin that declares min_trust expects
                        # cryptographic attestation; silently accepting
                        # without the CA key defeats the entire mechanism
                        # (CRIT-02, ADR-0099).  Operators who need a
                        # gradual rollout can set CORVIN_ATTESTATION_LENIENT=1
                        # to restore the old warn-and-continue behaviour.
                        import sys as _sys
                        _lenient = os.environ.get("CORVIN_ATTESTATION_LENIENT", "0")
                        if _lenient == "1":
                            print(
                                "[remote_trigger_receiver] WARNING: min_trust requires "
                                "attestation but CORVIN_CA_PUBKEY_HEX is not set. "
                                "Running in lenient mode (CORVIN_ATTESTATION_LENIENT=1). "
                                "Set CORVIN_CA_PUBKEY_HEX to enforce (ADR-0078/ADR-0099).",
                                file=_sys.stderr, flush=True,
                            )
                        else:
                            raise ValidationError("ca_not_configured", recv_key)
                    else:
                        level = _verify_att(env.sender_attestation, ca_pub)
                        if level < required:
                            raise ValidationError(
                                f"insufficient_trust_level:"
                                f"required={required.name},got={level.name}",
                                recv_key,
                            )
                        # R1 finding: verify_attestation only checks the CA sig +
                        # tier — it does NOT bind the IAC to THIS sender. Without
                        # this, any paired peer could REPLAY a higher-tier
                        # instance's IAC to satisfy min_trust. Bind it to the
                        # HMAC-covered (authenticated) sender_instance_id: the
                        # IAC's subject instance_id must equal the envelope's
                        # sender_instance_id. (Full per-instance-key binding is
                        # the IBC signing key, ADR-0145 — this is the available
                        # structural step that closes cross-instance IAC replay.)
                        _att_iid = ""
                        if isinstance(env.sender_attestation, dict):
                            _att_iid = str(env.sender_attestation.get("instance_id", ""))
                        if not _att_iid or _att_iid != (env.sender_instance_id or ""):
                            raise ValidationError(
                                "attestation_instance_mismatch", recv_key
                            )
            except (ImportError, Exception) as _att_exc:
                # ValidationError from the block above must propagate.
                if isinstance(_att_exc, ValidationError):
                    raise
                # Any other unexpected exception during attestation
                # verification is treated as a verification failure —
                # fail-closed (CRIT-02, ADR-0099). An attacker who can
                # crash the attestation module bypasses it otherwise.
                import sys as _sys
                print(
                    f"[remote_trigger_receiver] ERROR: attestation check "
                    f"failed unexpectedly — rejecting request: {_att_exc!r}",
                    file=_sys.stderr, flush=True,
                )
                raise ValidationError("attestation_check_error", recv_key) from _att_exc

        # Step 6.75 (v3+): attachment validation — caps, names, digests
        # Runs AFTER signature so we don't waste cycles on bad envelopes.
        try:
            from a2a_attachments import (  # type: ignore[import-not-found]
                AttachmentError, validate_attachments,
            )
        except ImportError:
            _shared_p = Path(__file__).resolve().parent
            if str(_shared_p) not in sys.path:
                sys.path.insert(0, str(_shared_p))
            from a2a_attachments import (  # type: ignore[import-not-found]
                AttachmentError, validate_attachments,
            )
        try:
            validate_attachments(env.attachments)
        except AttachmentError as exc:
            raise ValidationError(exc.reason, recv_key) from exc

        # Step 6.8 (v6, ADR-0103 M2): A2A network membership attestation.
        # Runs AFTER HMAC + nonce so only authenticated, non-replayed
        # senders are subject to the membership check. Fail-closed on
        # exception (see _check_network_attestation docstring).
        self._check_network_attestation(env, origin_config, recv_key)

        # Step 6.85 (v7, ADR-0141 Tier 2): Layer Integrity attestation.
        # Verify the sender's layer_integrity_hash against this receiver's
        # signed layer manifest. Runs after the membership check; shares its
        # HMAC-authenticated network_attestation block.
        self._check_layer_integrity_attestation(env, origin_config, recv_key)

        # Step 6.875 (v8, ADR-0153 M5): CorvinID JWT verification (best-effort).
        # If the sender included a corvin_id_jwt, attempt to verify its RS256
        # signature against the A2A network trust anchor. Missing PyJWT → skip
        # silently. Invalid signature → WARNING audit event; envelope is NOT
        # rejected (an invalid CorvinID cert is an attestation-quality signal,
        # not an auth failure — HMAC already authenticated the sender).
        if env.corvin_id_jwt and _IBC_JWT_OK:
            try:
                import jwt as _cid_jwt_mod  # noqa: PLC0415
                # Read the RS256 trust anchor (same key used for IBC and network attestation).
                _pubkey_pem: bytes | None = None
                if _A2A_NETWORK_PUBKEY_PATH.exists():
                    try:
                        _pubkey_pem = _A2A_NETWORK_PUBKEY_PATH.read_bytes()
                    except OSError:
                        _pubkey_pem = None
                if _pubkey_pem:
                    _cid_decoded = _cid_jwt_mod.decode(
                        env.corvin_id_jwt,
                        key=_pubkey_pem,
                        algorithms=["RS256"],
                    )
                    # Extract JTI for audit (16-char prefix; non-reversible — ADR-0145).
                    _cid_jti = str(_cid_decoded.get("jti", ""))[:16] if isinstance(_cid_decoded, dict) else ""
                    self._audit_best_effort(
                        "instance.ibc_verified", "INFO",
                        {
                            "origin_id": env.origin_id,
                            "ibc_jti": _cid_jti,
                            "sender_instance_id": (
                                env.sender_instance_id[:8]
                                if env.sender_instance_id else ""
                            ),
                        },
                    )
                # If _pubkey_pem is None, skip silently (trust anchor absent).
            except Exception as _cid_exc:  # noqa: BLE001
                self._audit_best_effort(
                    "instance.ibc_sig_failed", "WARNING",
                    {
                        "origin_id": env.origin_id,
                        "reason": "corvin_id_jwt_rs256_invalid",
                        "ibc_jti": "",  # JTI unknown on verification failure
                    },
                )
                # Best-effort only — do NOT raise; continue envelope processing.

        # Step 6.9: Verify instance_attestation if present (ADR-0145 Protocol v7).
        # The IBC (Instance Binding Certificate) lets the receiver cryptographically
        # confirm that the sender_instance_id was issued to the expected entity by
        # Corvin-Features (Ed25519, kid "ibc-vN" — reuses the session-signing
        # keypair, no separate trust anchor), and that the sender signed this
        # exact envelope with the Ed25519 key embedded in that IBC.
        # Runs AFTER HMAC (sender_instance_id is HMAC-covered) and AFTER the
        # network/layer checks so only fully-authenticated envelopes spend cycles here.
        instance_att = env.instance_attestation
        require_ibc = bool(origin_config.get("require_ibc", False))

        # ADR-0145 M2 security fix: if require_ibc=True but IBC verification
        # libraries are absent, a truthy-but-unverifiable attestation block would
        # skip both the if-branch (deps absent) and the elif (instance_att truthy).
        # Fail-closed: missing deps with require_ibc=True is always a hard reject.
        if require_ibc and not _IBC_VERIFY_AVAILABLE:
            raise ValidationError("ibc_library_unavailable", recv_key)

        if instance_att and _IBC_VERIFY_AVAILABLE:
            try:
                ibc_snapshot = instance_att.get("ibc_snapshot", "")
                ed25519_sig = instance_att.get("ed25519_sig", "")
                ibc_jti = instance_att.get("ibc_jti", "")

                # Verify the IBC's own Ed25519 signature and decode its claims
                # in one step (also enforces expiry). Raises IBCError on any
                # failure — caught by the except-Exception below, which audits
                # CRITICAL and only hard-rejects when require_ibc=True.
                ibc_decoded = _verify_ibc_ed25519(ibc_snapshot)

                # Assert IBC.sub == sender_instance_id (binds the cert to this sender).
                # An IBC belonging to a different instance is always invalid — an
                # attacker who compromised a peer can inject a stolen IBC into any
                # HMAC-signed envelope. Reject unconditionally, regardless of
                # require_ibc: we have the IBC, and it is demonstrably not for this sender.
                if ibc_decoded.get("sub") != env.sender_instance_id:
                    self._audit_best_effort(
                        "instance.ibc_sig_failed", "CRITICAL",
                        {"origin_id": env.origin_id,
                         "reason": "ibc_sub_mismatch",
                         "ibc_jti": (ibc_jti[:16] if ibc_jti else "")},
                    )
                    raise ValidationError(
                        "instance_attestation_failed:ibc_sub_mismatch", recv_key
                    )
                else:
                    # Verify Ed25519 signature over the canonical envelope payload.
                    instance_pubkey_b64 = ibc_decoded.get("instance_pubkey", "")
                    canonical = _build_ibc_canonical(
                        task_id=env.task_id,
                        origin_id=env.origin_id,
                        issued_at=env.issued_at,
                        nonce=env.nonce,
                        instruction=env.instruction,
                    )
                    if not _verify_ibc_sig(ed25519_sig, canonical, instance_pubkey_b64):
                        self._audit_best_effort(
                            "instance.ibc_sig_failed", "CRITICAL",
                            {"origin_id": env.origin_id,
                             "reason": "ed25519_verify_failed",
                             "ibc_jti": (ibc_jti[:16] if ibc_jti else "")},
                        )
                        if require_ibc:
                            raise ValidationError(
                                "instance_attestation_failed:ed25519_invalid", recv_key
                            )
                    else:
                        self._audit_best_effort(
                            "instance.ibc_verified", "INFO",
                            {"origin_id": env.origin_id,
                             "ibc_jti": (ibc_jti[:16] if ibc_jti else ""),
                             "sender_instance_id": (
                                 env.sender_instance_id[:8]
                                 if env.sender_instance_id else ""
                             )},
                        )
            except ValidationError:
                raise
            except Exception as _ibc_exc:
                self._audit_best_effort(
                    "instance.ibc_sig_failed", "CRITICAL",
                    {"origin_id": env.origin_id,
                     "reason": "ibc_verification_exception"},
                )
                if require_ibc:
                    raise ValidationError(
                        "instance_attestation_failed:exception", recv_key
                    ) from _ibc_exc
        elif require_ibc and not instance_att:
            # require_ibc=true but attestation absent from envelope — reject.
            raise ValidationError("instance_attestation_required_but_absent", recv_key)

        # Step 6.95: sender_instance_id pin — AFTER HMAC so the value is
        # authenticated. If the origin config declares a pin, the received
        # sender_instance_id must match exactly (prevents a relay from
        # impersonating the pinned sender behind the same HMAC key).
        sender_iid_pin = origin_config.get("sender_instance_id_pin")
        if sender_iid_pin and env.sender_instance_id != sender_iid_pin:
            raise ValidationError("sender_instance_id_mismatch", recv_key)

        # A2A-ATT-06 (ADR-0146): per-origin opt-in fail-closed escape hatch.
        # The revocation lists below are best-effort — a blocked/hard-stale/
        # signature-invalid manifest resolves to the empty permissive manifest, so
        # a revoked peer slides through (the accepted F-06 availability trade-off).
        # An operator who needs revocation to be load-bearing for a sensitive peer
        # sets ``a2a_manifest_required: true`` on the origin; the request is then
        # rejected whenever no AUTHENTIC (signature-verified, not-hard-stale)
        # manifest is available. The loader already downgrades a hard-stale or
        # sig-invalid cache to sig_verified=False, so sig_verified is the authentic
        # signal. Default is off — this never changes the permissive baseline.
        if origin_config.get("a2a_manifest_required", False):
            _authentic = False
            if _load_a2a_manifest is not None:
                try:
                    _mf_req = _load_a2a_manifest()
                    _authentic = bool(getattr(_mf_req, "sig_verified", False))
                except Exception:  # noqa: BLE001
                    _authentic = False
            if not _authentic:
                self._audit_best_effort(
                    "a2a.manifest_required_unavailable", "WARNING",
                    {"origin_id": env.origin_id, "reason": "no_authentic_manifest"},
                )
                raise ValidationError("a2a_manifest_required_unavailable", recv_key)

        # R3-1: instance-level revocation runs UNCONDITIONALLY here (post-HMAC),
        # NOT only inside the network_attestation branch. sender_instance_id is
        # HMAC-covered (Protocol v3), so a revoked/decommissioned peer cannot
        # evade revocation by simply OMITTING the network_attestation block — the
        # earlier in-branch check (now removed) left exactly that gap. Best-effort
        # on manifest availability (no manifest → cannot enforce, like the other
        # revocation lists).
        if _load_a2a_manifest is not None:
            try:
                _manifest = _load_a2a_manifest()
                _sid = env.sender_instance_id or ""
                _revoked_inst = getattr(_manifest, "revoked_instance_ids", None) or ()
                if _sid and _sid in _revoked_inst:
                    self._audit_best_effort(
                        "a2a.attestation_failed", "WARNING",
                        {"origin_id": env.origin_id, "reason": "instance_revoked"},
                    )
                    raise ValidationError("network_attestation_instance_revoked", recv_key)
            except ValidationError:
                raise
            except Exception:  # noqa: BLE001 — manifest unavailable → cannot enforce
                pass

        return env, origin_config

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _filter_result(data: dict, result_schema: dict) -> dict:
        """Apply JSONSchema property whitelist.

        Empty schema → {} (NOT pass-all, per ADR constraint).
        """
        if not result_schema:
            return {}
        properties = result_schema.get("properties", {})
        if not properties:
            return {}
        return {k: v for k, v in data.items() if k in properties}

    def _build_response(
        self, task_id: str, origin_id: str, status: str, data: dict,
        recv_key: bytes, attachments: list | None = None,
    ) -> ResponseEnvelope:
        # ADR-0116 M4: capture receiver chain tail AFTER the A2A.envelope_received
        # audit event has been written (audit-first invariant ensures the tail
        # reflects at minimum that event).  Best-effort — empty on I/O error.
        receiver_chain_tail = ""
        try:
            se = self._inst_forge_se if self._inst_forge_se is not None else _forge_se
            if se is not None:
                _tail = se.get_audit_chain_tail(audit_path())
                if isinstance(_tail, str):
                    receiver_chain_tail = _tail
        except Exception:  # noqa: BLE001
            pass

        resp = ResponseEnvelope(
            task_id=task_id,
            origin_id=origin_id,
            issued_at=time.time(),
            instance_id=self._instance_id,
            status=status,
            data=data,
            attachments=list(attachments or []),
            signature="",
            receiver_chain_tail=receiver_chain_tail,
        )
        sig = _hmac.new(recv_key, resp.canonical_payload(), hashlib.sha256).hexdigest()
        resp.signature = sig
        return resp

    def _rejected_response(
        self, task_id: str, origin_id: str, recv_key: bytes | None = None,
    ) -> ResponseEnvelope:
        # ADR-0077 C-5: sign rejected responses when we have the recv_key
        # so the caller can distinguish a genuine rejection from an
        # injected one. When no recv_key is available (unknown origin),
        # the response is unsigned — identical to the pre-v4 behaviour.
        resp = ResponseEnvelope(
            task_id=task_id,
            origin_id=origin_id,
            issued_at=time.time(),
            instance_id=self._instance_id,
            status="rejected",
            data={},
            attachments=[],
            signature="",
        )
        if recv_key:
            sig = _hmac.new(recv_key, resp.canonical_payload(), hashlib.sha256).hexdigest()
            resp.signature = sig
        return resp

    # ── ADR-0077 helpers ──────────────────────────────────────────────

    # Maximum number of per-origin rate-limit buckets held in memory.
    # Prevents unbounded growth if many distinct origin_ids are observed
    # (e.g. attacker spoofing origin_id — note: only post-HMAC, so
    # authenticated origins only; still worth bounding). (MED-05, ADR-0099)
    _RATE_BUCKETS_MAX = 1_000

    def _check_rate_limit(self, origin_id: str, rate_limit_rpm: int) -> bool:
        """Token-bucket rate limiter.  Returns True if allowed, False if rejected."""
        now = time.time()
        refill_rate = rate_limit_rpm / 60.0  # tokens per second
        with self._rate_lock:
            if origin_id not in self._rate_buckets:
                # Evict oldest bucket if at capacity (LRU by last_refill time).
                if len(self._rate_buckets) >= self._RATE_BUCKETS_MAX:
                    oldest = min(
                        self._rate_buckets,
                        key=lambda k: self._rate_buckets[k]["last_refill"],
                    )
                    del self._rate_buckets[oldest]
                self._rate_buckets[origin_id] = {
                    "tokens": float(rate_limit_rpm),
                    "last_refill": now,
                }
            bucket = self._rate_buckets[origin_id]
            # Clamp to zero: backward NTP/clock adjustments must not produce
            # negative elapsed, which would corrupt the token count (MED-IT6-01).
            elapsed = max(0.0, now - bucket["last_refill"])
            bucket["tokens"] = min(
                float(rate_limit_rpm),
                bucket["tokens"] + elapsed * refill_rate,
            )
            bucket["last_refill"] = now
            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
                return True
            return False

    @staticmethod
    def _check_consent(subject_id: str, required_purposes: list,
                       *, origin_id: str = "") -> bool:
        """Consent check via L16 is_granted().

        Returns True only when the data subject holds a valid consent entry.
        Fails CLOSED (returns False) when:
          - the consent module is not installed but the origin declares
            personal_data=True (operator misconfiguration — safer to block)
          - is_granted() raises any exception (HIGH-01, ADR-0099)

        An attacker who can crash the consent layer would bypass the GDPR
        Art. 6 gate entirely under the old fail-open design.
        """
        if not subject_id or not required_purposes:
            return True
        try:
            from consent import is_granted  # type: ignore[import-not-found]
        except ImportError:
            # Consent module not installed but this origin requires it.
            # Fail-closed: cannot verify consent without the module.
            import sys as _sys
            print(
                "[remote_trigger_receiver] ERROR: personal_data origin requires "
                "consent check but consent module is not installed — blocking "
                "request (HIGH-01, ADR-0099). Install the consent layer.",
                file=_sys.stderr, flush=True,
            )
            return False
        try:
            granted, _reason = is_granted("a2a", origin_id, subject_id)
            return granted
        except Exception as _ce:
            import sys as _sys
            print(
                f"[remote_trigger_receiver] ERROR: consent check raised "
                f"exception — blocking request (HIGH-01, ADR-0099): {_ce!r}",
                file=_sys.stderr, flush=True,
            )
            return False  # fail-closed on any consent layer error

    # ── Audit writers ─────────────────────────────────────────────────

    def _audit_strict(self, event_type: str, severity: str, details: dict) -> None:
        """Write audit event; raises AuditWriteError on any failure.

        Used for the audit-first invariant (A2A.envelope_received).
        """
        se = self._inst_forge_se if self._inst_forge_se is not None else _forge_se
        if se is None:
            raise AuditWriteError("forge_unavailable")
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            se.write_event(
                path, event_type,
                severity=severity, tool="", run_id="",
                details=details, hash_chain=True,
            )
        except AuditWriteError:
            raise
        except Exception as exc:
            raise AuditWriteError(str(exc)) from exc

    def _audit_nonce_fallback(self) -> None:
        """Best-effort WARNING audit event when nonce store falls back to in-memory."""
        self._audit_best_effort(
            "A2A.nonce_store_fallback", "WARNING",
            {"reason": "persistent_nonce_store_unavailable",
             "impact": "replay_protection_degraded_across_restarts"},
        )

    def _audit_best_effort(self, event_type: str, severity: str, details: dict) -> None:
        """Write audit event; silently ignored on failure."""
        try:
            se = self._inst_forge_se if self._inst_forge_se is not None else _forge_se
            if se is None:
                return
            path = audit_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            se.write_event(
                path, event_type,
                severity=severity, tool="", run_id="",
                details=details, hash_chain=True,
            )
        except Exception:
            pass


class _ChainIntegrityFailureGateUnavailable(RuntimeError):
    """Raised when clag is expected (forge present) but cannot be imported.

    The class name embeds ``ChainIntegrityFailure`` so the ``receive()``
    exception handler (which checks ``type(_e).__name__``) treats this as a
    fail-closed integrity event — the A2A instruction is rejected, not skipped.
    Mirrors the identical pattern in consent.py / disclosure.py.
    """


def _clag_gate_a2a(layer_id: str) -> None:
    """ADR-0133 CLAG M4 — verify chain integrity before A2A instruction execution (L38).

    Fail-closed when forge is present but clag is unimportable (broken gate is
    itself a security failure).  Fail-open only when forge is genuinely absent
    (minimal deployment).  Raises ChainIntegrityFailure (from clag module) on a
    broken chain — callers MUST NOT swallow it.
    """
    try:
        from forge import clag as _clag_mod  # type: ignore[import-not-found]
    except ImportError as _exc:
        # FND-17: tie the fail-open decision to the SAME signal as audit
        # availability. _forge_se is the module-level `from forge import
        # security_events`; if it imported, forge IS present (audits are being
        # written), so a missing clag is a BROKEN gate → fail-CLOSED. Only when
        # forge is genuinely absent (_forge_se is None) do we fail-open (minimal
        # deploy). The previous find_spec heuristic could resolve forge
        # ambiguously and fail-open while audits were still flowing.
        if _forge_se is not None:
            import logging as _log
            _log.getLogger("corvin.a2a").critical(
                "[CLAG] forge present (audit active) but clag unimportable (%s) "
                "— A2A instruction rejected (fail-closed)", _exc,
            )
            raise _ChainIntegrityFailureGateUnavailable(
                f"clag unimportable despite forge present: {_exc}"
            ) from _exc
        return  # forge genuinely absent — fail-open
    _clag_mod.gate(audit_path(), layer_id)


def _ms(start: float) -> int:
    return int((time.time() - start) * 1000)


def _safe_instance_id() -> str:
    """Return local instance_id, or "" if not resolvable.

    The receiver must remain operational even if the instance_id file is
    unreadable (e.g. fresh container before mkdir) — degrade to empty
    string rather than crash. The cloud-side caller can detect this and
    refuse to pin against "".
    """
    try:
        return get_instance_id()
    except Exception:
        return ""
