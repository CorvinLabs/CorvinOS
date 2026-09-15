"""Chain-Locked Adaptive Gating (CLAG) — ADR-0133.

Every security-sensitive layer operation must call ``clag.gate(path, layer_id)``
before proceeding.  ``gate()`` verifies the last K chain events for hash-link
and DNA consistency, issues a short-lived ChainIntegrityToken (CIT), and writes
an ``audit.cit_issued`` event.  If the chain is broken it raises
``ChainIntegrityFailure`` (fail-closed).

Three interlocking protection mechanisms:

  1. **Per-operation CIT**: every sensitive call must obtain a fresh token whose
     HMAC is derived from the active LSAD seed (ADR-0132). Free-tier
     installations use a publicly-known key; paid-tier use a key derived from
     the license JWT, making CITs tier-distinguishable just like the chain DNA.

  2. **Per-layer shadow hash**: each layer tracks what it believes the current
     chain tail to be. On the next ``gate()`` call from the same layer, the
     stored expectation is checked before any I/O.  A mismatch means the chain
     was modified between this layer's last write and the new request — the
     operation is blocked immediately.

  3. **Epoch anchors**: every ``EPOCH_EVENTS`` chain-events an
     ``audit.epoch_anchor`` checkpoint is written. The anchor embeds the current
     tail prefix and the previous epoch's tail, bounding the blast-radius of any
     tampering to at most one epoch window.

Failure mode: **fail-closed**.  ``ChainIntegrityFailure`` is raised; a
``chain.integrity_failed`` (CRITICAL) audit event is emitted best-effort before
the raise.

Integration points (each gating layer calls ``gate()`` before its operation):
  L10 path-gate · L16 license activation · L22 engine spawn · L29 delegation
  · L38 A2A instruction execution

See ADR-0133 in Corvin-ADR/decisions/0133-clag.md.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

VERIFY_K_DEFAULT: int = 50        # look-back window == one full epoch (EPOCH_EVENTS)
EPOCH_EVENTS: int = 50            # chain-events between epoch anchors
CIT_TTL_SECONDS: int = 300        # 5 minutes — CIT lifetime (default for non-critical layers)

# ADR-0135 M3 — reduced TTL for security-critical layers to narrow the CIT
# validity window from 300 s to 60 s.  Layers not listed here use CIT_TTL_SECONDS.
# Prefix matching: a layer_id that starts with any listed prefix uses its TTL.
_LAYER_TTL_OVERRIDES: dict[str, int] = {
    "L10.path_gate":     60,
    "L16.license_load":  60,
    "L16.consent":       60,
    "L16.disclosure":    60,
    "L19.disclosure":    60,
    "L22.engine_spawn":  60,
    "L38.a2a":           60,
}

_CHAIN_ANCHOR_HMAC_LABEL = b"corvin:chain-anchor:v1"

# Label mixed into the DNA seed to derive the CIT signing key.
# Changing this label invalidates all outstanding CITs.
_CIT_KEY_LABEL = b"corvin:clag:cit-key:v1"


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ChainIntegrityFailure(Exception):
    """Raised by ``gate()`` when the audit chain is broken.

    Callers MUST propagate this exception — the protected operation MUST NOT
    proceed with a broken chain.  Swallowing it is a compliance violation.
    """

    def __init__(self, reason: str, layer_id: str = "", reason_code: str = "") -> None:
        self.reason = reason
        self.layer_id = layer_id
        self.reason_code = reason_code or "unknown"
        super().__init__(f"CLAG [{layer_id}] {self.reason_code}: {reason}")


# Human-readable, NON-PII explanations for every chain-integrity failure code.
# Used to tell a blocked user WHICH check failed and WHY (the reason code is
# structural metadata — never task content — so surfacing it does not violate
# the metadata-only audit floor). Covers both the CLAG gate() reason codes and
# the verify_chain() / continuity-check issue codes so a single helper serves
# the spawn-gate and the `voice-audit verify` notification path.
CHAIN_FAILURE_EXPLANATIONS: dict[str, str] = {
    # ── clag.gate() reason codes ──
    "hash_link_broken": "A hash link between two consecutive audit entries does "
                        "not verify — an entry was altered, inserted, or removed.",
    "shadow_mismatch":  "The audit-chain tail no longer matches the value this "
                        "process last recorded — an entry was written or changed "
                        "outside the audit writer.",
    "cit_tampered":     "The in-memory chain-integrity token failed re-verification "
                        "— the licence-tier seed changed mid-session or the token "
                        "was tampered with.",
    "chain_read_error": "The audit log could not be read for verification.",
    "cit_issue_write_failed": "The chain-integrity token could not be written — "
                              "the audit log is not writable (full / read-only).",
    # ── continuity-check codes (boot / rotation) ──
    "anchor_unreadable": "The chain anchor file is missing or unreadable.",
    "hmac_invalid":      "The chain anchor's signature did not verify — the anchor "
                         "or the chain tail it pins was modified.",
    "chain_deleted":     "The audit log is gone but a chain anchor exists — the log "
                         "was deleted or truncated.",
    # ── verify_chain() issue codes (security_events) ──
    "broken_chain":     "An entry's prev_hash does not point at the previous "
                        "entry's hash — the chain ordering was broken (an entry "
                        "was inserted, reordered, or removed).",
    "tampered":         "An entry's recorded hash does not match its content — the "
                        "entry was edited after it was written.",
    "mac_tampered":     "An entry's keyed MAC does not verify — the entry was "
                        "rewritten by someone without the out-of-tree anchor key.",
    "mac_missing":      "An entry past the MAC-enabled point is missing its keyed "
                        "MAC — the MAC field was stripped to hide an edit.",
    "mac_stripped_chain": "The whole live chain carries no MAC although MAC is "
                          "active on this host — every MAC was stripped.",
    "mac_unverifiable_key_absent": "An entry carries a MAC but the anchor key is "
                                   "unavailable to verify it (cross-host verify "
                                   "without the key).",
    "unknown":          "The audit-chain integrity check failed for an "
                        "unclassified reason.",
}


def explain_reason_code(reason_code: str) -> str:
    """Return a plain-language, PII-free explanation for a chain-failure code,
    or a safe generic fallback for an unknown code."""
    return CHAIN_FAILURE_EXPLANATIONS.get(
        reason_code or "unknown",
        "The audit-chain integrity check failed (code: "
        f"{reason_code or 'unknown'}).",
    )


# ── Chain Integrity Token ──────────────────────────────────────────────────────

@dataclass
class ChainIntegrityToken:
    """Short-lived proof that the audit chain was intact at issuance.

    The HMAC ties the token to the chain state AND the active license tier
    (via the LSAD DNA seed used as the HMAC key).  A fork running free-tier
    DNA cannot produce a valid paid-tier CIT, and vice versa.
    """

    epoch: int
    tail_hash: str     # 16-hex prefix of chain tail at issuance
    dna_at_tail: str   # 16-hex DNA value at tail
    layer_id: str
    issued_at: int     # unix seconds
    ttl: int           # seconds until expiry
    hmac_hex: str = "" # computed by ``gate()``; empty until filled

    # ── accessors ─────────────────────────────────────────────────────────────

    def fingerprint(self) -> str:
        """16-hex prefix safe for audit detail fields (never full HMAC)."""
        return self.hmac_hex[:16]

    def is_fresh(self) -> bool:
        return int(time.time()) < self.issued_at + self.ttl

    def verify_hmac(self, cit_key: bytes) -> bool:
        """Re-compute and compare — constant-time to prevent timing attacks."""
        expected = _compute_cit_hmac(self, cit_key)
        return _hmac.compare_digest(self.hmac_hex, expected)

    def to_dict(self) -> dict:
        """Audit-safe representation — fingerprint only, never full HMAC."""
        return {
            "epoch":       self.epoch,
            "tail_hash":   self.tail_hash,
            "dna_at_tail": self.dna_at_tail,
            "layer_id":    self.layer_id,
            "issued_at":   self.issued_at,
            "ttl":         self.ttl,
            "fp":          self.fingerprint(),
        }


def _compute_cit_hmac(cit: ChainIntegrityToken, cit_key: bytes) -> str:
    """Deterministic HMAC over all CIT fields (except hmac_hex itself)."""
    msg = (
        f"{cit.epoch}:{cit.tail_hash}:{cit.dna_at_tail}:"
        f"{cit.layer_id}:{cit.issued_at}:{cit.ttl}"
    ).encode("utf-8")
    return _hmac.new(cit_key, msg, hashlib.sha256).hexdigest()


# ── CIT key derivation ─────────────────────────────────────────────────────────

def _derive_cit_key(dna_seed: str | None) -> bytes:
    """Derive the CIT signing key from the active LSAD DNA seed.

    ``dna_seed=None``  → free-tier (publicly-known key, anyone can verify).
    ``dna_seed=paid``  → paid-tier (key derived from RS256-signed JWT bytes).

    This coupling means CITs are tier-distinguishable: a free-tier fork cannot
    produce CITs that pass verification in a paid-tier context.
    """
    try:
        from .chain_dna import derive_seed_free  # type: ignore[import]
    except ImportError:
        from chain_dna import derive_seed_free  # type: ignore[import]
    seed = dna_seed or derive_seed_free()
    return _hmac.new(seed.encode("utf-8"), _CIT_KEY_LABEL, hashlib.sha256).digest()


# ── Shadow hash tracking ───────────────────────────────────────────────────────

_shadow_lock = threading.Lock()
_shadow_hashes: dict[str, str] = {}  # layer_id → expected next chain tail hash
_shadow_paths: dict[str, str] = {}   # layer_id → audit path at shadow creation

# ── Per-layer CIT store (for self-verification) ────────────────────────────────
# Holds the most-recently issued CIT per static layer_id.  On each gate() call
# the prior CIT (if still fresh) is re-verified with the current resolved seed:
# any seed-tier drift (e.g. _active_dna_seed silently reset to None/free) is
# caught before the new operation proceeds.

_cit_lock = threading.Lock()
# Bounded to _MAX_LAYER_CITS entries (FIFO eviction) to prevent unbounded growth
# from per-spawn unique layer_ids (e.g. L22.engine_spawn.<hex>).  Each entry is
# ~200 bytes; 256 entries ≈ 50 KB ceiling.  Static layer_ids (L16/L19/L38) have
# long lifetimes and re-populate after eviction on the next gate() call.
_MAX_LAYER_CITS: int = 256
_layer_cits: "OrderedDict[str, ChainIntegrityToken]" = OrderedDict()


def _get_active_seed() -> str | None:
    """Retrieve the current LSAD DNA seed from security_events (lazy import)."""
    try:
        try:
            from .security_events import get_active_seed  # type: ignore[import]
        except ImportError:
            from security_events import get_active_seed  # type: ignore[import]
        return get_active_seed()
    except Exception:  # noqa: BLE001
        return None


def _shadow_check(layer_id: str, actual_tail: str, path: Path) -> str | None:
    """Compare the known-expected tail to the actual tail.

    Returns a human-readable description on mismatch; None if OK or first call.

    Path-aware: if the audit path changed since the shadow was set (e.g., the
    operator restarted with a different CORVIN_HOME), the shadow is ignored for
    this call — a new shadow will be set by ``_shadow_update``.
    """
    with _shadow_lock:
        expected = _shadow_hashes.get(layer_id)
        expected_path = _shadow_paths.get(layer_id)
    if expected is None:
        return None  # no shadow yet for this layer — first call
    if expected_path is not None and expected_path != str(path):
        return None  # path changed — new chain, no cross-chain shadow check
    if actual_tail and expected and actual_tail != expected:
        return (
            f"shadow hash mismatch for layer {layer_id!r}: "
            f"expected {expected[:16]!r}, got {actual_tail[:16]!r}"
        )
    return None


def _shadow_update(layer_id: str, new_tail: str, path: Path | None = None) -> None:
    with _shadow_lock:
        _shadow_hashes[layer_id] = new_tail
        if path is not None:
            _shadow_paths[layer_id] = str(path)


def clear_shadow_hashes() -> None:
    """Reset all per-layer CLAG state (session reset / test teardown).

    Clears both the shadow hashes and the per-layer CIT cache so that the
    next gate() call on any layer starts with a clean slate.
    """
    with _shadow_lock:
        _shadow_hashes.clear()
        _shadow_paths.clear()
    with _cit_lock:
        _layer_cits.clear()


def advance_layer_shadow(layer_id: str, path: Path) -> None:
    """Advance the shadow hash for *layer_id* to the current chain tail.

    Call this after a layer writes its own audit events between two gate()
    calls.  Without it, the next gate() call would see a shadow mismatch
    because the layer's own writes advanced the chain past what gate()
    recorded in step 8.

    Example: consent.is_granted() calls gate(), then writes consent.expired
    or consent.store_corrupted.  The next is_granted() call must not fail
    because of those legitimate writes.

    Fail-open: silent on any error (missing file, I/O error).
    """
    try:
        try:
            from .security_events import get_audit_chain_tail  # type: ignore[import]
        except ImportError:
            from security_events import get_audit_chain_tail  # type: ignore[import]
        tail = get_audit_chain_tail(path) or ""
    except Exception:  # noqa: BLE001
        return
    _shadow_update(layer_id, tail, path)


# ── Epoch state ────────────────────────────────────────────────────────────────

_epoch_lock = threading.Lock()
_epoch_event_count: int = 0
_epoch_number: int = 0
_epoch_last_anchor_tail: str = ""


def _current_epoch() -> int:
    with _epoch_lock:
        return _epoch_number


def _tick_epoch(new_tail: str) -> bool:
    """Increment the in-epoch event counter; return True if epoch boundary hit."""
    global _epoch_event_count, _epoch_number, _epoch_last_anchor_tail  # noqa: PLW0603
    with _epoch_lock:
        _epoch_event_count += 1
        if _epoch_event_count >= EPOCH_EVENTS:
            _epoch_event_count = 0
            _epoch_number += 1
            _epoch_last_anchor_tail = new_tail
            return True
    return False


# ── Chain verification ─────────────────────────────────────────────────────────

def _canonical(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"))


# Fields the writer appends AFTER computing the chain hash — must mirror
# security_events.CHAIN_HASH_EXCLUDED_FIELDS exactly. Imported lazily in
# _verify_hash_link from the canonical owner (single source of truth); this
# tuple is the standalone fallback if that import is unavailable.
_HASH_EXCLUDED_FALLBACK: tuple[str, ...] = (
    "hash", "mac", "instance_id", "instance_sig",
)


def _hash_excluded_fields() -> tuple[str, ...]:
    """Resolve the chain-hash exclusion set from the canonical writer.

    Falls back to the local tuple when security_events is not importable
    (standalone clag use / tests). Keeping CLAG's exclusion set sourced from
    security_events prevents the fail-closed drift that ADR-0137 M2 (mac) and
    ADR-0153 M3 (instance_id/instance_sig) each introduced when a post-hash
    field was added without updating this verifier.
    """
    try:
        try:
            from .security_events import CHAIN_HASH_EXCLUDED_FIELDS  # type: ignore[import]
        except ImportError:
            from security_events import CHAIN_HASH_EXCLUDED_FIELDS  # type: ignore[import]
        return CHAIN_HASH_EXCLUDED_FIELDS
    except Exception:  # noqa: BLE001
        return _HASH_EXCLUDED_FALLBACK


def _verify_hash_link(rec: dict, prev_hash: str) -> bool:
    """True when ``rec["hash"]`` matches the expected SHA-256 over prev + rec."""
    stored = rec.get("hash", "")
    if not stored:
        return True  # pre-hash-chain event — skip
    # Exclude every field the writer appends AFTER the hash (hash, mac,
    # instance_id, instance_sig). Excluding only a subset is a fail-closed
    # regression: every record carrying an un-excluded post-hash field fails
    # this check and trips the CLAG spawn gate even though the chain is intact.
    # ADR-0137 M2 (mac) and ADR-0153 M3 (instance_id/instance_sig) each hit this
    # exact trap — see security_events.CHAIN_HASH_EXCLUDED_FIELDS.
    _excluded = _hash_excluded_fields()
    probe = {k: v for k, v in rec.items() if k not in _excluded}
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\n")
    h.update(_canonical(probe).encode("utf-8"))
    return h.hexdigest()[:16] == stored


def verify_last_k(path: Path, k: int = VERIFY_K_DEFAULT) -> list[str]:
    """Verify hash-link integrity for the last *k* events.

    Returns a list of failure descriptions (empty = all OK).
    Pre-hash-chain events (no ``hash`` field) are silently skipped.
    """
    if not path.exists():
        return []
    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s:
                    lines.append(s)
    except OSError as e:
        return [f"chain read error: {e}"]

    # Take k+1 lines so we have a prev_hash anchor for the first verified event.
    tail_lines = lines[-(k + 1):]
    events: list[dict] = []
    non_json_count = 0
    for ln in tail_lines:
        try:
            events.append(json.loads(ln))
        except json.JSONDecodeError:
            # Do NOT silently skip: a non-JSON line in the tail window could be
            # an injected non-parseable entry used to hide a deleted real event
            # (deletion + re-chain attack).  Count and surface all such lines.
            non_json_count += 1

    failures: list[str] = []
    if non_json_count:
        failures.append(
            f"non-JSON lines in tail window: {non_json_count} "
            "(possible chain corruption or injection attack)"
        )
    # Track the prev anchor as the LAST HASH-BEARING event's hash — not simply
    # events[i-1].hash. Hashless meta-events (e.g. the CRITICAL
    # ``audit.chain_gap_detected`` the self-test emits, which is intentionally
    # written without a hash so it cannot recurse into the chain it reports on)
    # do NOT participate in the hash-link: the writer chains the NEXT event's
    # prev_hash past them to the previous hash-bearing tail. Using events[i-1].hash
    # would read "" for the event after such a gap and false-positive a
    # hash-link break, tripping the fail-closed spawn gate even though the chain
    # is intact. This mirrors verify_chain(), which skips ``"hash" not in rec``.
    prev_hash: str | None = None
    for i, rec in enumerate(events):
        if "hash" not in rec:
            continue  # hashless meta-event — not a chain link, leaves prev intact
        if prev_hash is not None and not _verify_hash_link(rec, prev_hash):
            et = rec.get("event_type", "?")
            stored = rec.get("hash", "?")
            failures.append(
                f"hash-link broken at position -{len(events) - i} "
                f"(event_type={et!r}, stored={stored[:8]!r})"
            )
        prev_hash = rec.get("hash", "")
    return failures


# ── Epoch anchor ───────────────────────────────────────────────────────────────

def write_epoch_anchor(path: Path, *, dna_seed: str | None = None) -> None:
    """Write an ``audit.epoch_anchor`` checkpoint event (best-effort).

    Called at adapter boot and automatically by ``gate()`` at epoch boundaries.
    The anchor records the tail prefix and the previous epoch's tail prefix —
    verifying a sequence of anchors bounds the blast-radius of any tampering to
    one epoch window.
    """
    try:
        try:
            from .security_events import get_audit_chain_tail, write_event  # type: ignore[import]
        except ImportError:
            from security_events import get_audit_chain_tail, write_event  # type: ignore[import]
        tail = get_audit_chain_tail(path) or ""
        with _epoch_lock:
            epoch = _epoch_number
            prev_anchor = _epoch_last_anchor_tail
        write_event(
            path,
            "audit.epoch_anchor",
            details={
                "epoch":               epoch,
                "tail_hash_prefix":    tail[:16],
                "prev_epoch_tail_prefix": prev_anchor[:16],
            },
        )
    except Exception:  # noqa: BLE001
        pass  # epoch anchors are best-effort — never block the caller


# ── Main public gate ───────────────────────────────────────────────────────────

def _resolve_layer_ttl(layer_id: str, caller_ttl: int | None) -> int:
    """Return the effective CIT TTL for *layer_id* (ADR-0135 M3).

    ``caller_ttl=None`` (the gate() default) means "apply the override table".
    An explicit int value is taken as-is (caller wins). Using None as the
    sentinel — rather than comparing against CIT_TTL_SECONDS — ensures the
    override table stays correct even if CIT_TTL_SECONDS changes in the future.
    """
    if caller_ttl is not None:
        return caller_ttl  # caller-specified value — respect it
    for prefix, override_ttl in _LAYER_TTL_OVERRIDES.items():
        if layer_id.startswith(prefix):
            return override_ttl
    return CIT_TTL_SECONDS


def gate(
    path: Path,
    layer_id: str,
    *,
    dna_seed: str | None = None,
    k: int = VERIFY_K_DEFAULT,
    ttl: int | None = None,
) -> ChainIntegrityToken:
    """Gate a security-sensitive layer operation against the audit chain.

    Steps (all fail-closed):
      1. Read current chain tail.
      2. Shadow-hash check — fast, no disk I/O beyond the tail read.
      3. Verify last *k* events for hash-link integrity.
      4. Read current DNA value at tail.
      5. Derive CIT key from *dna_seed* (LSAD tier coupling).
      6. Issue ``ChainIntegrityToken``.
      7. Write ``audit.cit_issued`` event (extends the chain).
      8. Update shadow hash for this layer with the new tail.
      9. Tick epoch counter; write anchor if boundary reached.

    Raises ``ChainIntegrityFailure`` if any step fails.  The caller MUST let
    the exception propagate — it MUST NOT proceed with a broken chain.
    """
    # ── 0. Resolve active DNA seed ────────────────────────────────────────────
    # Auto-populate from the process-wide LSAD seed when the caller omits it.
    # This ensures the CIT is tier-coupled (free vs paid) without requiring
    # every call site to explicitly import and thread the seed through.
    _resolved_seed: str | None = dna_seed
    if _resolved_seed is None:
        _resolved_seed = _get_active_seed()

    # ── 0.5. Self-verify previous CIT for this layer ─────────────────────────
    # If the same layer called gate() before and we have a cached CIT that is
    # still within its TTL, re-verify its HMAC against the currently-resolved
    # seed.  A mismatch reveals seed tier drift (e.g. _active_dna_seed silently
    # reset from paid to None/free) — a structural security violation.
    with _cit_lock:
        prev_cit = _layer_cits.get(layer_id)
    if prev_cit is not None and prev_cit.is_fresh():
        _prev_key = _derive_cit_key(_resolved_seed)
        if not prev_cit.verify_hmac(_prev_key):
            _emit_failure(path, layer_id, "cit_tampered")
            raise ChainIntegrityFailure(
                f"CIT for layer {layer_id!r} failed HMAC re-verification — "
                "possible LSAD seed tier drift or in-memory CIT tampering",
                layer_id,
                "cit_tampered",
            )

    # ── 1. Read tail ──────────────────────────────────────────────────────────
    try:
        try:
            from .security_events import get_audit_chain_tail  # type: ignore[import]
        except ImportError:
            from security_events import get_audit_chain_tail  # type: ignore[import]
        tail = get_audit_chain_tail(path) or ""
    except Exception as exc:
        _emit_failure(path, layer_id, "chain_read_error")
        raise ChainIntegrityFailure(
            str(exc), layer_id, "chain_read_error"
        ) from exc

    # ── 2. Shadow hash check ──────────────────────────────────────────────────
    shadow_issue = _shadow_check(layer_id, tail, path)
    if shadow_issue:
        _emit_failure(path, layer_id, "shadow_mismatch")
        raise ChainIntegrityFailure(shadow_issue, layer_id, "shadow_mismatch")

    # ── 3. Hash-link verification (full epoch window) ─────────────────────────
    failures = verify_last_k(path, k=k)
    if failures:
        _emit_failure(path, layer_id, "hash_link_broken")
        raise ChainIntegrityFailure(failures[0], layer_id, "hash_link_broken")

    # ── 4. Read DNA ───────────────────────────────────────────────────────────
    dna_at_tail = ""
    try:
        try:
            from .chain_dna import last_dna_in_chain  # type: ignore[import]
        except ImportError:
            from chain_dna import last_dna_in_chain  # type: ignore[import]
        dna_at_tail, _ = last_dna_in_chain(path)
    except Exception:  # noqa: BLE001
        pass  # DNA absent on legacy or empty chains — non-fatal here

    # ── 5+6. Derive key and issue CIT (tier-coupled via resolved seed) ─────────
    cit_key = _derive_cit_key(_resolved_seed)
    epoch = _current_epoch()
    now = int(time.time())
    effective_ttl = _resolve_layer_ttl(layer_id, ttl)
    cit = ChainIntegrityToken(
        epoch=epoch,
        tail_hash=tail[:16],
        dna_at_tail=dna_at_tail[:16],
        layer_id=layer_id,
        issued_at=now,
        ttl=effective_ttl,
    )
    cit.hmac_hex = _compute_cit_hmac(cit, cit_key)

    # ── 7. Write audit.cit_issued (audit-first) ───────────────────────────────
    try:
        try:
            from .security_events import write_event  # type: ignore[import]
        except ImportError:
            from security_events import write_event  # type: ignore[import]
        written = write_event(
            path,
            "audit.cit_issued",
            details={
                "layer_id":        layer_id,
                "epoch":           epoch,
                "tail_hash_prefix": tail[:16],
                "dna_prefix":       dna_at_tail[:16],
                "cit_fp":           cit.fingerprint(),
                "ttl":              effective_ttl,
            },
        )
        new_tail = written.get("hash", "")
    except Exception as exc:
        raise ChainIntegrityFailure(
            f"audit write failed: {exc}", layer_id, "audit_write_failed"
        ) from exc

    # ── 8. Update shadow ──────────────────────────────────────────────────────
    _shadow_update(layer_id, new_tail, path)

    # ── 8.5. Cache the issued CIT for self-verification on next call ──────────
    with _cit_lock:
        _layer_cits[layer_id] = cit
        # move_to_end() is required: OrderedDict.__setitem__ on an *existing*
        # key keeps it at its original insertion position, so without this call
        # a static layer (L16, L19) that was inserted first would always sit at
        # position 0 and be the next eviction victim — silently disabling its
        # step-0.5 seed-drift detection whenever spawn IDs flood the dict.
        _layer_cits.move_to_end(layer_id)
        if len(_layer_cits) > _MAX_LAYER_CITS:
            _layer_cits.popitem(last=False)  # evict true oldest (FIFO)

    # ── 9. Epoch tick (best-effort) ───────────────────────────────────────────
    if _tick_epoch(new_tail):
        write_epoch_anchor(path, dna_seed=_resolved_seed)

    return cit


def verify_cit(
    cit: ChainIntegrityToken,
    *,
    dna_seed: str | None = None,
) -> bool:
    """Verify a CIT's freshness and HMAC. Used by receiving layers.

    Returns False (not raises) so callers can choose their own error path.
    """
    if not cit.is_fresh():
        return False
    cit_key = _derive_cit_key(dna_seed)
    return cit.verify_hmac(cit_key)


# ── Internal helper ────────────────────────────────────────────────────────────

def _emit_failure(path: Path, layer_id: str, reason_code: str) -> None:
    """Best-effort CRITICAL audit event before raising ChainIntegrityFailure."""
    try:
        try:
            from .security_events import write_event  # type: ignore[import]
        except ImportError:
            from security_events import write_event  # type: ignore[import]
        write_event(
            path,
            "chain.integrity_failed",
            severity="CRITICAL",
            details={
                "layer_id":    layer_id,
                "reason_code": reason_code,
            },
        )
    except Exception:  # noqa: BLE001
        pass  # observability is best-effort; the exception still propagates


# ── Chain Continuity Anchor (ADR-0135) ────────────────────────────────────────

def _anchor_hmac_key(dna_seed: str | None) -> bytes:
    """Derive the anchor HMAC key from the active LSAD seed (tier-coupled)."""
    try:
        try:
            from .chain_dna import derive_seed_free  # type: ignore[import]
        except ImportError:
            from chain_dna import derive_seed_free  # type: ignore[import]
        base = (dna_seed or _get_active_seed() or derive_seed_free()).encode("utf-8")
    except Exception:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).error(
            "anchor_hmac_key: seed derivation failed — refusing to use constant fallback; "
            "anchor write will be skipped"
        )
        # Raise so callers (write_chain_anchor / verify_chain_anchor) handle the
        # absence gracefully rather than silently accepting a forgeable HMAC.
        raise RuntimeError("anchor HMAC key unavailable — chain_dna seed derivation failed")
    return _hmac.new(base, _CHAIN_ANCHOR_HMAC_LABEL, hashlib.sha256).digest()


def _read_chain_tail_and_count(path: Path) -> tuple[str, int]:
    """Return (tail_hash, event_count) with a single file scan (best-effort, ("", 0) on error)."""
    tail = ""
    count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for ln in fh:
                stripped = ln.strip()
                if not stripped:
                    continue
                count += 1
                try:
                    h = json.loads(stripped).get("hash", "")
                    if h:
                        tail = h
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return tail, count


def _tail_hash_at_count(path: Path, n: int) -> str:
    """Return the chain tail hash as it stood after the first ``n`` events — the
    last hash-bearing line at or before non-empty line ``n`` (same line-counting
    as _read_chain_tail_and_count, so it aligns with an anchor's event_count).

    Used to PROVE the anchored tail is a genuine ANCESTOR (prefix) of the current
    chain — i.e. the chain only grew after a stale anchor — versus a fork or a
    rewrite of pre-anchor history. Best-effort: returns '' on error or n <= 0,
    which callers treat as "cannot prove ancestry" → stays fail-closed."""
    if n <= 0:
        return ""
    tail = ""
    seen = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for ln in fh:
                stripped = ln.strip()
                if not stripped:
                    continue
                seen += 1
                if seen > n:
                    break
                try:
                    h = json.loads(stripped).get("hash", "")
                    if h:
                        tail = h
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return tail


def write_chain_anchor(
    audit_path: Path,
    anchor_path: Path,
    *,
    dna_seed: str | None = None,
) -> None:
    """Write a tamper-evident chain_anchor.json on clean shutdown (ADR-0135 M1).

    Best-effort — never raises, never blocks the caller.  The anchor embeds
    the current chain tail hash prefix, event count, timestamp, and an HMAC
    keyed from the active LSAD seed (tier-coupled).
    """
    try:
        try:
            from .security_events import write_event  # type: ignore[import]
        except ImportError:
            from security_events import write_event  # type: ignore[import]

        # Snapshot pre-event chain state for the audit record.
        pre_tail, pre_count = _read_chain_tail_and_count(audit_path)

        # Derive key BEFORE emitting event — ensures audit.chain_anchor_written
        # only appears in the chain when the anchor file can actually be written.
        # RuntimeError propagates to the outer except which logs a WARNING and
        # returns without emitting the event or writing the file (FND-30 fix).
        key = _anchor_hmac_key(dna_seed)

        # Emit audit event (audit-first: event precedes file write so a crash
        # between event and os.replace is detectable at next boot as absent anchor).
        try:
            write_event(
                audit_path,
                "audit.chain_anchor_written",
                details={"tail_hash_prefix": pre_tail[:16], "event_count": pre_count},
            )
        except Exception:  # noqa: BLE001
            pass  # best-effort; proceed to snapshot whatever state is in the chain now

        # Re-read tail + count after the anchor event (single scan).
        tail, event_count = _read_chain_tail_and_count(audit_path)
        now = int(time.time())
        payload = f"{tail[:16]}:{event_count}:{now}"
        anchor_mac = _hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

        anchor = {
            "tail_hash":   tail[:16],
            "event_count": event_count,
            "anchor_time": now,
            "hmac":        anchor_mac,
        }
        tmp = anchor_path.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(anchor), "utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, anchor_path)
    except Exception as _exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "write_chain_anchor: failed (%s: %s) — next boot will see absent anchor",
            type(_exc).__name__, _exc,
        )


def verify_chain_anchor(
    audit_path: Path,
    anchor_path: Path,
    *,
    dna_seed: str | None = None,
    emit: bool = True,
) -> tuple[str, str]:
    """Verify chain continuity at boot (ADR-0135 M1).

    Emits one of three audit events (best-effort) and returns
    ``(status, detail)`` where *status* is one of:
      "absent"  — no anchor file; first boot or legitimate reset (→ WARNING).
      "ok"      — anchor verified; chain is continuous (→ INFO). Also returned
                  when the anchor is stale (unclean shutdown) but the anchored
                  tail is a PROVEN ancestor of the current, only-grown chain —
                  emits audit.chain_anchor_stale (INFO), not a CRITICAL break.
      "failed"  — anchor present but HMAC or tail/count mismatch (→ CRITICAL).

    Pass ``emit=False`` when calling from the self-test to avoid writing audit
    events as a side effect of a diagnostic check.
    """
    def _emit(event: str, **details: object) -> None:
        if not emit:
            return
        try:
            try:
                from .security_events import write_event  # type: ignore[import]
            except ImportError:
                from security_events import write_event  # type: ignore[import]
            write_event(audit_path, event, details=dict(details))
        except Exception:  # noqa: BLE001
            pass  # audit emit is best-effort; verification result is authoritative

    if not anchor_path.exists():
        _emit("audit.chain_anchor_absent")
        return "absent", "no chain_anchor.json — first boot or legitimate reset"

    try:
        anchor = json.loads(anchor_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        detail = f"chain_anchor.json unreadable: {exc}"
        _emit("audit.chain_continuity_break", reason="anchor_unreadable")
        return "failed", detail

    _raw_mac = anchor.get("hmac", "")
    stored_mac: str = _raw_mac if isinstance(_raw_mac, str) else ""
    stored_tail = anchor.get("tail_hash", "")
    stored_count = anchor.get("event_count", 0)
    stored_time = anchor.get("anchor_time", 0)

    # Re-derive and verify HMAC (constant-time).
    # stored_mac is guaranteed str so compare_digest never raises TypeError;
    # a tampered non-string value becomes "" and triggers the mismatch branch
    # (which correctly emits audit.chain_continuity_break).
    try:
        key = _anchor_hmac_key(dna_seed)
    except RuntimeError as _key_exc:
        # Seed unavailable — cannot verify the anchor's HMAC without the key.
        # Treat as "absent" (WARNING) rather than CRITICAL: the anchor may be
        # valid, we simply cannot check it right now.
        _emit("audit.chain_anchor_absent")
        return "absent", f"cannot verify anchor — HMAC key unavailable: {_key_exc}"
    payload = f"{stored_tail}:{stored_count}:{stored_time}"
    expected_mac = _hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(stored_mac, expected_mac):
        detail = "chain_anchor.json HMAC invalid — anchor corrupted or LSAD seed changed"
        _emit("audit.chain_continuity_break", reason="hmac_invalid")
        return "failed", detail

    if not audit_path.exists():
        if stored_count == 0:
            _emit("audit.chain_anchor_verified", tail_hash_prefix=stored_tail)
            return "ok", "anchor verified (empty chain at anchor time)"
        detail = f"audit.jsonl absent but anchor says {stored_count} events — chain deleted"
        _emit("audit.chain_continuity_break", reason="chain_deleted")
        return "failed", detail

    # Single combined scan: tail + count in one file pass.
    current_tail, current_count = _read_chain_tail_and_count(audit_path)
    current_tail = current_tail[:16]

    if current_tail != stored_tail:
        # A differing tail is NOT automatically tampering. The anchor is written
        # only on clean shutdown; on an unclean kill (crash-loop) it goes stale
        # and the chain simply GROWS past the anchored tail. Before crying
        # CRITICAL, PROVE ancestry: if the chain didn't shrink AND the tail as it
        # stood at the anchored event_count still equals the anchored tail, then
        # the anchored state is a verified prefix of the current chain — benign
        # append-only growth, not truncation/replacement. This is rigorously
        # fail-closed: any rewrite of pre-anchor history changes the tail at
        # stored_count → mismatch → stays CRITICAL; any shrink → count_dropped
        # below → stays CRITICAL; an unreadable/empty ancestor tail → stays
        # CRITICAL. Only pure post-anchor growth is downgraded.
        if (
            stored_tail
            and current_count >= stored_count
            and _tail_hash_at_count(audit_path, int(stored_count)) == stored_tail
        ):
            _emit("audit.chain_anchor_stale",
                  anchored_tail=stored_tail, current_tail=current_tail,
                  anchored_count=stored_count, current_count=current_count)
            return "ok", (
                f"chain grew past a stale anchor (unclean shutdown): anchored tail "
                f"{stored_tail!r} is a verified ancestor of current {current_tail!r}; "
                f"events {stored_count}→{current_count}"
            )
        detail = (
            f"chain tail mismatch: anchored={stored_tail!r} current={current_tail!r} — "
            "chain may have been truncated or replaced"
        )
        _emit("audit.chain_continuity_break", reason="tail_mismatch",
              anchored_tail=stored_tail, current_tail=current_tail)
        return "failed", detail

    if current_count < stored_count:
        detail = (
            f"event count dropped: anchored={stored_count} current={current_count} — "
            "events may have been deleted"
        )
        _emit("audit.chain_continuity_break", reason="count_dropped",
              anchored_count=stored_count, current_count=current_count)
        return "failed", detail

    _emit("audit.chain_anchor_verified", tail_hash_prefix=stored_tail)
    return "ok", f"chain continuous (tail={stored_tail!r} events≥{stored_count})"


__all__ = [
    "VERIFY_K_DEFAULT",
    "EPOCH_EVENTS",
    "CIT_TTL_SECONDS",
    "ChainIntegrityFailure",
    "ChainIntegrityToken",
    "gate",
    "verify_cit",
    "verify_last_k",
    "write_chain_anchor",
    "verify_chain_anchor",
    "write_epoch_anchor",
    "advance_layer_shadow",
    "clear_shadow_hashes",
]
