"""Offline pairing with out-of-band fingerprint verification (ADR-2295, L38).

Online pairing (``corvin-a2a pair``) is checked once against the hosted Corvin Labs
pairing service, which issues a pairing certificate. Offline pairing
(``corvin-a2a pair --offline-pair``) needs no service: the trust root is the two
operators, who compare a short fingerprint through a channel outside the network
(phone, paper) and each confirm it on their own installation.

The fingerprint is SHA-256 over the receiving instance id, the sending instance id
and a key id derived from the pair's two HMAC keys. Both installations compute it
from what they hold; a key swapped in transit, or the wrong peer instance, gives a
different value on each side. It is not a secret (it reveals nothing about the
keys), but the full value never goes into the audit chain — only an 8-character
prefix.

State on disk (both kept in the 0600 origin / endpoint files):

* ``oob_state: "pending"`` — written by ``pair --offline-pair`` (origin) and by
  ``pair-import`` / the printed endpoint file (endpoint). A pending file is
  ``enabled: false`` AND refused by the receiver / sender on ``oob_state`` alone,
  so flipping ``enabled`` by hand does not complete a pair.
* ``oob_state: "confirmed"`` + ``attestation: "attested_out_of_band"`` — written by
  ``pair-confirm`` after the operator typed the fingerprint the OTHER operator read
  out and it matched. Each side refuses its own unconfirmed half — the sender a
  pending endpoint, the receiver a pending origin — so two current builds exchange
  nothing until both confirmed. This is enforced per side: the receiver cannot see
  whether the sender confirmed (an older sender build does not check).

A confirmed origin is accepted by the receiver without the attestation grace
cut-off (``remote_trigger_receiver._check_network_attestation``), as long as the
fingerprint recomputed from the file's keys, the pinned sender instance and the
receiver's own instance id still equals the confirmed one. An offline pair from
before ADR-2295 (no ``oob_state``) keeps today's behaviour: accepted only until the
manifest's ``attestation_mandatory_after``.

Fingerprints are never reused: every confirmed (or imported) fingerprint is
recorded per tenant in ``<tenant>/global/a2a/offline_pair_fingerprints.json``, and
a fingerprint already in that ledger is refused — which is what stops a replayed
endpoint file from an earlier pairing.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac as _hmac
import json
import os
import re
import tempfile
import time
from pathlib import Path

STATE_PENDING = "pending"
STATE_CONFIRMED = "confirmed"
ATTESTED_OUT_OF_BAND = "attested_out_of_band"
ATTESTED_HOSTED = "hosted_service"
PAIRING_MODE_OFFLINE = "offline"
PAIRING_MODE_ONLINE = "online"

_FP_DOMAIN = "corvin-a2a-oob-fingerprint-v1"
_KID_DOMAIN = "corvin-a2a-oob-keyid-v1"
_FP_HEX_LEN = 20          # 80 bits, read aloud as five groups of four
PREFIX_LEN = 8            # what the audit chain carries
_IID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LEDGER_NAME = "offline_pair_fingerprints.json"


class OfflinePairError(ValueError):
    """A closed reason (machine-readable) for a refused offline-pair step."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def key_id(hmac_key: str, recv_key: str) -> str:
    """16 hex chars identifying the pair's key material (not reversible)."""
    raw = f"{_KID_DOMAIN}\0{hmac_key}\0{recv_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def fingerprint(receiver_iid: str, sender_iid: str, kid: str) -> str:
    """Short, readable fingerprint: ``XXXX-XXXX-XXXX-XXXX-XXXX`` (upper-case hex)."""
    raw = f"{_FP_DOMAIN}\0{receiver_iid}\0{sender_iid}\0{kid}".encode("utf-8")
    h = hashlib.sha256(raw).hexdigest().upper()[:_FP_HEX_LEN]
    return "-".join(h[i:i + 4] for i in range(0, _FP_HEX_LEN, 4))


def normalise(fp: str) -> str:
    """Strip separators/whitespace, upper-case; what a human typed → comparable form."""
    return re.sub(r"[^0-9A-Fa-f]", "", str(fp or "")).upper()


def fingerprints_equal(a: str, b: str) -> bool:
    na, nb = normalise(a), normalise(b)
    if len(na) != _FP_HEX_LEN or len(nb) != _FP_HEX_LEN:
        return False
    return _hmac.compare_digest(na, nb)


def prefix(fp: str) -> str:
    return normalise(fp)[:PREFIX_LEN]


def valid_instance_id(iid: str) -> bool:
    return bool(iid) and bool(_IID_RE.match(str(iid)))


def origin_fingerprint(origin_cfg: dict, receiver_iid: str, sender_iid: str) -> str:
    """Fingerprint as the RECEIVING side (origin file holder) computes it."""
    return fingerprint(receiver_iid, sender_iid,
                       key_id(str(origin_cfg.get("hmac_key", "")),
                              str(origin_cfg.get("recv_key", ""))))


def endpoint_fingerprint(endpoint_cfg: dict, sender_iid: str) -> str:
    """Fingerprint as the SENDING side (endpoint file holder) computes it."""
    return fingerprint(str(endpoint_cfg.get("instance_id", "")), sender_iid,
                       key_id(str(endpoint_cfg.get("hmac_key", "")),
                              str(endpoint_cfg.get("recv_key", ""))))


def is_pending(cfg: dict) -> bool:
    return cfg.get("oob_state") == STATE_PENDING


def is_unconfirmed(cfg: dict) -> bool:
    """True for an ADR-2295 offline pair that has not been confirmed.

    A record written by the new flow carries ``pairing_mode: offline`` AND
    ``oob_state``; it is unconfirmed unless ``oob_state == confirmed`` — so
    deleting ``oob_state`` from a pending file does not turn it into a legacy
    pair (review F3). A file with neither key (an online pair, or an offline
    pair from before ADR-2295) is NOT unconfirmed and keeps its old behaviour.
    The receiver and the sender inline this same rule.
    """
    if cfg.get("pairing_mode") != PAIRING_MODE_OFFLINE and "oob_state" not in cfg:
        return False
    return cfg.get("oob_state") != STATE_CONFIRMED


def origin_attested_out_of_band(origin_cfg: dict, receiver_iid: str, sender_iid: str) -> bool:
    """Receiver check: is this origin a CONFIRMED offline pair that still verifies?

    All of: ``attestation == attested_out_of_band``, ``oob_state == confirmed``, a
    pinned sender instance equal to the (HMAC-covered) envelope sender, and the
    fingerprint recomputed from the file's keys equal to the confirmed one. A
    hand-rotated key, a re-pointed pin or a different receiver identity breaks it.
    """
    if origin_cfg.get("attestation") != ATTESTED_OUT_OF_BAND:
        return False
    if origin_cfg.get("oob_state") != STATE_CONFIRMED:
        return False
    pin = str(origin_cfg.get("sender_instance_id_pin") or "")
    if not pin or not receiver_iid or pin != str(sender_iid or ""):
        return False
    stored = str(origin_cfg.get("oob_fingerprint") or "")
    return fingerprints_equal(stored, origin_fingerprint(origin_cfg, receiver_iid, pin))


# ── reuse ledger ──────────────────────────────────────────────────────────

def ledger_path() -> Path:
    import paths as _paths  # type: ignore[import-not-found]
    return _paths.tenant_global_dir() / "a2a" / _LEDGER_NAME


def _read_ledger(path: Path) -> list[dict]:
    """The ledger rows; ``ledger_unreadable`` unless it is ``{"fingerprints": [ {...}, ... ]}``.

    An unreadable OR structurally wrong ledger cannot prove a fingerprint is fresh, so it
    refuses instead of reading as empty (review F8). A missing file is an empty ledger.
    """
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise OfflinePairError("ledger_unreadable",
                               f"fingerprint ledger unreadable ({type(exc).__name__})") from exc
    rows = data.get("fingerprints") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise OfflinePairError("ledger_unreadable",
                               "fingerprint ledger unreadable (unexpected structure)")
    return rows


def _row_matches(row: dict, n_fp: str, kid: str | None) -> bool:
    if normalise(row.get("fingerprint", "")) == n_fp:
        return True
    return bool(kid) and str(row.get("key_id") or "") == kid


def fingerprint_used(fp: str, path: Path | None = None, *, kid: str | None = None) -> bool:
    """True when ``fp`` — or, given, the key id ``kid`` — is already in the ledger.

    The key id catches a replayed endpoint file presented under another instance id,
    which yields a fresh fingerprint over the SAME keys (review F9).
    """
    n = normalise(fp)
    return any(_row_matches(r, n, kid) for r in _read_ledger(path or ledger_path()))


@contextlib.contextmanager
def _ledger_lock(directory: Path):
    """Cross-process exclusive lock for the ledger's read-check-write (review F1).

    The same primitive the A2A config files use (``a2a_friendship.config_file_lock``:
    ``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows, bounded wait). A busy lock
    raises ``FriendshipLockBusy`` — the caller refuses; nothing is written unlocked
    because of contention.
    """
    import a2a_friendship  # type: ignore[import-not-found]

    with a2a_friendship.config_file_lock(directory):
        yield


def record_fingerprint(fp: str, *, kid: str, side: str, peer_id: str, pairing_id: str = "",
                       path: Path | None = None) -> str:
    """Enter ``fp``/``kid`` in the tenant ledger under an exclusive lock.

    Returns ``"recorded"``, or ``"resumed"`` when a row for the SAME fingerprint, key id,
    side, peer and pairing id is already there — a confirm that died between the ledger
    row and the file write may be completed (review F10). Any other match raises
    ``fingerprint_reused``.
    """
    p = path or ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    n = normalise(fp)
    peer = str(peer_id)[:128]
    pid = str(pairing_id)[:64]
    with _ledger_lock(p.parent):
        rows = _read_ledger(p)
        hits = [r for r in rows if _row_matches(r, n, kid)]
        if hits:
            if all(normalise(r.get("fingerprint", "")) == n and r.get("key_id") == kid
                   and r.get("side") == side and r.get("peer_id") == peer
                   and r.get("pairing_id") == pid for r in hits):
                return "resumed"
            raise OfflinePairError("fingerprint_reused",
                                   "this fingerprint was already used by an earlier pairing")
        rows.append({"fingerprint": n, "key_id": kid, "side": side, "peer_id": peer,
                     "pairing_id": pid, "at": int(time.time())})
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".fp-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"fingerprints": rows}, fh, sort_keys=True, indent=2)
                fh.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return "recorded"


def resumable(fp: str, *, kid: str, side: str, peer_id: str, pairing_id: str,
              path: Path | None = None) -> bool:
    """True when every ledger match is this very pair's own row (see ``record_fingerprint``)."""
    n = normalise(fp)
    hits = [r for r in _read_ledger(path or ledger_path()) if _row_matches(r, n, kid)]
    return bool(hits) and all(
        normalise(r.get("fingerprint", "")) == n and r.get("key_id") == kid
        and r.get("side") == side and r.get("peer_id") == str(peer_id)[:128]
        and r.get("pairing_id") == str(pairing_id)[:64] for r in hits)


__all__ = [
    "ATTESTED_HOSTED", "ATTESTED_OUT_OF_BAND", "OfflinePairError", "PAIRING_MODE_OFFLINE",
    "PAIRING_MODE_ONLINE", "PREFIX_LEN", "STATE_CONFIRMED", "STATE_PENDING",
    "endpoint_fingerprint", "fingerprint", "fingerprint_used", "fingerprints_equal",
    "is_pending", "is_unconfirmed", "key_id", "ledger_path", "normalise",
    "origin_attested_out_of_band", "origin_fingerprint", "prefix", "record_fingerprint", "resumable",
    "valid_instance_id",
]
