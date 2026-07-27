"""License-Seeded Audit DNA (LSAD) — ADR-0132.

Every audit event carries a ``chain_dna`` field (16 hex chars) that
is derived from:

  DNA[0] = seed(license_jwt, instance_id)   # genesis value
  DNA[n] = HMAC(DNA[n-1], hash[n-1])        # evolves with each hash

Free-tier chains use a publicly-known HMAC key; paid-tier chains use a
key derived from the license JWT's bytes, which embed an RSA signature
that only CorvinLabs' private key can produce.

Properties:
  * Tier-distinguishable: free vs. paid DNA are cryptographically distinct.
  * Chain-bound: each event's DNA depends on all prior hashes — rewriting
    or inserting events produces a different DNA from that point on.
  * Unforgeable (paid): reproducing paid-tier DNA requires the exact JWT
    bytes, which contain an RSA signature over the customer claims.
  * Backward-compatible: events without ``chain_dna`` are skipped during
    verification (pre-LSAD legacy events).

Audit events: see EVENT_SEVERITY entries ``license.chain_dna_seeded``
and ``license.chain_dna_mismatch`` in security_events.py.

See ADR-0132 in Corvin-ADR/decisions/0132-lsad-audit-chain-dna.md.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import os as _os
import secrets as _secrets
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Public constant — everyone can verify free-tier chains.
_FREE_TIER_KEY = b"corvin:chain-dna:free-tier:v1"
_PAID_TIER_LABEL = b"corvin:chain-dna:paid-tier:v1"
_INSTANCE_TIER_LABEL = b"free-tier-v1"

# Short prefix stored per event: 32 hex chars = 128 bits of entropy.
# 128 bits raises birthday-collision cost to ~2^64 — computationally infeasible.
# Legacy chains written with DNA_PREFIX_LEN=16 are still verified correctly
# because verify_chain_dna() uses length-aware comparison (see _cmp_len below).
DNA_PREFIX_LEN = 32


def _is_valid_hex32(s: str) -> bool:
    """Return True if *s* is a valid 32-char hex string (upper or lower case)."""
    try:
        return len(bytes.fromhex(s)) == 16
    except ValueError:
        return False


def derive_seed_free() -> str:
    """Fixed seed for free-tier chains — publicly known, verifiable by anyone."""
    return _hmac.new(_FREE_TIER_KEY, b"seed", hashlib.sha256).hexdigest()


def ensure_instance_seed(seed_path: Path) -> str:
    """Return the 128-bit hex instance seed, creating it atomically if absent.

    Written mode 0600. If the file is corrupt or unreadable a new seed is
    generated and persisted. If the write also fails (e.g. read-only FS),
    an ephemeral seed is returned with a WARNING — the chain will diverge
    across restarts, but the function never raises.
    """
    if seed_path.exists():
        try:
            s = seed_path.read_text("utf-8").strip()
            if len(s) == 32 and _is_valid_hex32(s):
                return s
            _log.warning(
                "instance_seed.key exists but content is invalid (len=%d) — regenerating",
                len(s),
            )
        except OSError as exc:
            _log.warning("instance_seed.key unreadable (%s) — regenerating", exc)
    new_seed = _secrets.token_hex(16)  # 128 bits → 32 hex chars
    try:
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = seed_path.with_suffix(".tmp")
        tmp.write_text(new_seed, "utf-8")
        _os.chmod(tmp, 0o600)
        _os.replace(tmp, seed_path)
    except OSError as exc:
        _log.warning(
            "instance_seed.key could not be persisted (%s) — "
            "using ephemeral seed; chain DNA will diverge after restart",
            exc,
        )
    return new_seed


def derive_seed_instance(instance_seed_hex: str) -> str:
    """Instance-seeded free-tier DNA — not publicly reproducible (ADR-0136)."""
    return _hmac.new(
        bytes.fromhex(instance_seed_hex),
        _INSTANCE_TIER_LABEL,
        hashlib.sha256,
    ).hexdigest()


def derive_seed_paid(license_jwt: str, instance_id: str) -> str:
    """Paid-tier seed — derived from the JWT bytes (contains RSA signature).

    Without the exact license JWT (which requires CorvinLabs' private key
    to issue), the correct paid-tier DNA cannot be reproduced.
    """
    key = hashlib.sha256(license_jwt.encode("utf-8")).digest()
    return _hmac.new(key, instance_id.encode("utf-8"), hashlib.sha256).hexdigest()


def evolve(current_dna: str, prev_hash: str) -> str:
    """Evolve DNA by one step using the previous event's hash.

    DNA[n] = HMAC(DNA[n-1], hash[n-1])
    """
    return _hmac.new(
        current_dna.encode("utf-8"),
        prev_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_free_tier(seed: str) -> bool:
    """True if a seed matches the well-known free-tier constant.

    ``seed`` may be a full HMAC hex output (64 chars) or a stored chain prefix
    (16 or 32 chars).  Comparison uses the shorter of the two lengths so that
    legacy 16-char chain prefixes are handled correctly.
    """
    clen = min(len(seed), DNA_PREFIX_LEN)
    return seed[:clen] == derive_seed_free()[:clen]


def _cmp_len(stored: str) -> int:
    """Return the effective comparison length for a stored DNA value.

    Legacy events carry 16-hex DNA; current events carry 32-hex DNA.
    Comparing the full DNA_PREFIX_LEN against a 16-char stored value
    would always fail, so we use the shorter of the two lengths.
    This is safe: a shorter comparison is still cryptographically binding
    against the HMAC chain — it simply means the older event uses 64-bit
    instead of 128-bit protection, which was the original design.
    """
    return min(len(stored), DNA_PREFIX_LEN)


# ── Chain reading helpers ──────────────────────────────────────────────────────

#: Tail chunk size for the backwards read. A record is a few hundred bytes, so the
#: newest DNA-bearing entry is virtually always in the first chunk.
_TAIL_BLOCK = 8192


def last_dna_in_chain(path: Path) -> tuple[str, str]:
    """Return (last_chain_dna, last_hash) from the audit file, or ("", "").

    Reads BACKWARDS from the end and returns the newest entry that has BOTH
    ``details.chain_dna`` and ``hash`` populated — identical semantics to the forward
    scan this replaces, only travelling the other way.

    It used to scan the whole file forwards, json.loads()-ing every line, and it runs
    inside write_event()'s exclusive flock on every hash-chained write. Profiled on the
    live chain (120 875 records) 2026-07-27: 624 ms of write_event's 627 ms total. Same
    shape as security_events._last_hash, which was fixed in the same pass — together
    they made every authenticated console request cost ~0.8 s and serialise, so eight
    concurrent requests took eight times as long and Playwright specs timed out at 30 s.
    The cost grew with chain length without bound.
    """
    if not path.exists():
        return "", ""
    try:
        size = path.stat().st_size
    except OSError:
        return "", ""
    if size == 0:
        return "", ""

    try:
        with path.open("rb") as fh:
            pos = size
            tail = b""
            while pos > 0:
                step = min(_TAIL_BLOCK, pos)
                pos -= step
                fh.seek(pos)
                tail = fh.read(step) + tail
                lines = tail.split(b"\n")
                # The first element may be a fragment unless we reached the start.
                candidates = lines if pos == 0 else lines[1:]
                for raw in reversed(candidates):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(rec, dict):
                        continue
                    h = rec.get("hash", "")
                    dna = (rec.get("details") or {}).get("chain_dna", "")
                    if h and dna:
                        return dna, h
                tail = lines[0] if pos > 0 else b""
    except Exception:  # noqa: BLE001
        return "", ""
    return "", ""


# ── Verification ───────────────────────────────────────────────────────────────

class DnaMismatch(Exception):
    """Raised by verify_chain_dna when the DNA is inconsistent."""

    def __init__(self, index: int, expected: str, actual: str, event_type: str) -> None:
        self.index = index
        self.expected = expected
        self.actual = actual
        self.event_type = event_type
        super().__init__(
            f"LSAD DNA mismatch at event {index} ({event_type!r}): "
            f"expected {expected!r} got {actual!r}"
        )


def verify_chain_dna(
    events: list[dict[str, Any]],
    *,
    seed: str | None = None,
) -> list[DnaMismatch]:
    """Verify DNA consistency across a list of chain events.

    Events without ``chain_dna`` (pre-LSAD legacy) are skipped.
    Verification starts at the first event that carries a ``chain_dna``
    field and checks that all subsequent DNA-bearing events are consistent.

    Returns a (possibly empty) list of DnaMismatch objects — one per
    inconsistency found.
    """
    mismatches: list[DnaMismatch] = []
    current_dna: str | None = seed  # None means "not yet initialized"
    prev_hash = ""

    for i, ev in enumerate(events):
        dna = (ev.get("details") or {}).get("chain_dna", "")
        h = ev.get("hash", "")

        if not dna:
            # Pre-LSAD event — update prev_hash but don't check DNA
            if h:
                prev_hash = h
            continue

        if current_dna is None:
            # First DNA-bearing event — treat its value as the seed
            current_dna = dna
            prev_hash = h
            continue

        event_type = ev.get("event_type", "")

        # ``license.chain_dna_seeded`` events create an explicit seam: their
        # chain_dna IS the new seed value (not evolved from the previous DNA).
        # We validate the claimed seed value to block seam-injection attacks:
        # a mid-chain seam must claim either the known free-tier prefix or the
        # paid-tier prefix derivable from the provided ``seed``.
        if event_type == "license.chain_dna_seeded":
            clen = _cmp_len(dna)
            free_prefix = derive_seed_free()[:clen]
            if seed is not None:
                # Paid-tier verification (CorvinLabs audit): the seam must match
                # the paid seed, not the free-tier seed.
                expected_paid = seed[:clen]
                if dna != expected_paid and dna != free_prefix:
                    mismatches.append(
                        DnaMismatch(i, expected_paid, dna, event_type)
                    )
            elif dna != free_prefix:
                # Free-tier (self-) verification: encountering a paid-tier seam
                # without a paid seed to verify it is structurally suspicious —
                # flag it so the operator knows external verification is required.
                mismatches.append(
                    DnaMismatch(
                        i,
                        f"(free:{free_prefix} OR paid:<requires-paid-seed>)",
                        dna,
                        event_type,
                    )
                )
            current_dna = dna
            if h:
                prev_hash = h
            continue

        # Normal event — evolve expected DNA from current state.
        # Use _cmp_len(dna) so legacy 16-char events verify correctly against
        # the 32-char DNA_PREFIX_LEN constant (backward-compatible).
        clen = _cmp_len(dna)
        expected = evolve(current_dna, prev_hash)[:clen]
        if dna[:clen] != expected:
            mismatches.append(DnaMismatch(i, expected, dna, event_type or "?"))
            # Don't cascade: keep current_dna + prev_hash so we can continue
            # checking subsequent events independently.

        current_dna = dna
        if h:
            prev_hash = h

    return mismatches


__all__ = [
    "DNA_PREFIX_LEN",
    "DnaMismatch",
    "derive_seed_free",
    "derive_seed_instance",
    "derive_seed_paid",
    "ensure_instance_seed",
    "evolve",
    "is_free_tier",
    "last_dna_in_chain",
    "verify_chain_dna",
]
