"""Layer 38 — large objects travel as signed references and are pulled over a signed op (ADR-2287).

An A2A envelope is a signed letter; inline attachments stop at 1 MiB in total (measured, T-0301). A
worker output file that does not fit inline becomes a REFERENCE in the signed response's existing
``attachments`` list::

    {"name", "mime", "size", "sha256", "classification", "ref_id", "inline": false}

no ``content_b64``, HMAC-covered like every other response field. The bytes stay on the HOLDER (the
instance whose worker wrote them) and move over ONE signed op, ``object-pull``: an extension of the
signed ping that already carries the ADR-2242 task-stage query, so direct HTTP and the relay carry it
with the same HMAC machinery. The side that wants the bytes asks, chunk by chunk
(``{ref_id, offset, length}``); every answer is signed with the pairing's ``recv_key``; the puller's
final digest must equal the signed ``sha256``.

Invariants (each has a test in ``test_a2a_large_object_reference_e2e.py``)
--------------------------------------------------------------------------

* **Origin-bound.** A reference is served only to the origin it was issued to, only inside its TTL
  (default 15 min, hard cap 24 h), only up to ``max_pulls`` times its size in bytes, and never listed.
* **No enumeration oracle.** An unknown ``ref_id``, an expired one and one issued to another origin all
  answer the same closed code, ``object_unavailable``.
* **No replay.** Every pull carries a fresh signed request id (the ping id); the holder records it on the
  reference BEFORE answering and refuses a second use (``object_pull_replayed``).
* **Same gates as inline.** :func:`object_gate` is the ONE gate for every output object, inline or by
  reference: name rules, size ceiling (per origin, under the operator's hard ceiling), L34
  classification against the origin's ``max_data_classification`` and L35 egress to the origin's host.
  A reference passes it at issue time AND again at every pull.
* **Audit first, metadata only.** ``A2A.object_issued`` / ``A2A.object_pulled`` / ``A2A.object_expired``
  / ``A2A.object_refused`` carry a ref-id prefix, size, sha256 prefix, classification, origin and closed
  reason codes: never a byte, never a file name. A chunk is not served unless its record committed.
* **Tenant-scoped, erasable.** The store is ``<tenant>/global/a2a_feed/objects/`` — outside every
  worker's cwd, inside the A2A feed's L36 erasure scope (``a2a_feed.erase_peer`` calls
  :func:`erase_origin`) — and expired references are swept on every issue and pull and at most once a
  minute from the authenticated ping path.

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

# ── Tunables ─────────────────────────────────────────────────────────────

OP = "object-pull"
#: one pull answers at most this many raw bytes (~1.4 MB of base64: inside the 4 MiB relay plaintext)
CHUNK_MAX_BYTES = 1024 * 1024
DEFAULT_TTL_S = 15 * 60
MIN_TTL_S = 60
MAX_TTL_S = 24 * 3600
#: served bytes per reference are capped at max_pulls x size ("at most N times")
DEFAULT_MAX_PULLS = 3
MAX_MAX_PULLS = 10
#: pull requests recorded per reference (replay ledger bound); more is refused as exhausted
MAX_PULL_REQUESTS = 4096
#: the operator's hard ceiling per object when ``spec.a2a.object_max_bytes`` is unset
DEFAULT_OPERATOR_MAX_BYTES = 256 * 1024 * 1024
#: no setting can raise the ceiling above this
ABSOLUTE_MAX_BYTES = 4 * 1024 * 1024 * 1024
#: all held objects of a tenant together
STORE_QUOTA_BYTES = 2 * 1024 * 1024 * 1024
#: content classification reads at most this much of an object
CLASSIFY_SCAN_BYTES = 1024 * 1024

REF_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_LEVELS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET")
REF_KEYS = frozenset({"name", "mime", "size", "sha256", "classification", "ref_id", "inline"})

#: every reason a reference can be refused, at issue, at pull or by the puller (closed vocabulary)
REFUSALS = frozenset({
    # holder: issue or pull (object_gate)
    "object_bad_name", "object_too_large", "object_classification_exceeded", "object_egress_denied",
    "object_gate_error",
    # holder: issue
    "object_store_full", "object_store_error", "object_audit_failed",
    # holder: pull
    "object_unavailable", "object_pull_replayed", "object_bad_range", "object_pull_budget_exhausted",
    # puller
    "object_pull_unsupported", "object_digest_mismatch", "object_bad_chunk", "object_bad_reference",
    "object_unreachable", "object_auth_failed",
})

_lock = threading.RLock()
_last_sweep: dict[str, float] = {}

Audit = Callable[[str, str, dict], None]


class ObjectAuditError(Exception):
    """The audit record for an issue/pull did not commit: nothing is issued or served."""


# ── Paths ────────────────────────────────────────────────────────────────

def store_dir(tenant_id: str | None = None) -> Path:
    """``<tenant>/global/a2a_feed/objects`` — the A2A feed's directory, so the feed's L36 erasure and
    retention scope covers it; never inside a worker's scratch directory."""
    import a2a_feed  # noqa: PLC0415

    return a2a_feed.feed_dir(tenant_id) / "objects"


def _ensure(root: Path) -> None:
    (root / "staging").mkdir(parents=True, exist_ok=True)
    for d in (root.parent, root, root / "staging"):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _meta_path(root: Path, ref_id: str) -> Path:
    return root / f"{ref_id}.json"


def _bin_path(root: Path, ref_id: str) -> Path:
    return root / f"{ref_id}.bin"


def _write_meta(root: Path, meta: dict) -> None:
    path = _meta_path(root, meta["ref_id"])
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, json.dumps(meta, separators=(",", ":")).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _read_meta(root: Path, ref_id: str) -> dict | None:
    try:
        meta = json.loads(_meta_path(root, ref_id).read_text("utf-8"))
        return meta if isinstance(meta, dict) and meta.get("ref_id") == ref_id else None
    except (OSError, ValueError):
        return None


def _remove(root: Path, ref_id: str, *, keep_meta: bool = False) -> None:
    try:
        _bin_path(root, ref_id).unlink()
    except OSError:
        pass
    if not keep_meta:
        try:
            _meta_path(root, ref_id).unlink()
        except OSError:
            pass


# ── Small helpers ────────────────────────────────────────────────────────

def _level(label: Any) -> int:
    lab = str(label or "INTERNAL").upper()
    return _LEVELS.index(lab) if lab in _LEVELS else _LEVELS.index("INTERNAL")


def max_level(*labels: Any) -> str:
    return _LEVELS[max([_level(x) for x in labels if x is not None] or [0])]


def audit_fields(meta: dict) -> dict:
    """The metadata an object record carries: prefixes, size, classification, origin. Never a name."""
    return {"ref_prefix": str(meta.get("ref_id", ""))[:8], "size": int(meta.get("size") or 0),
            "sha_prefix": str(meta.get("sha256", ""))[:16],
            "classification": str(meta.get("classification") or "INTERNAL"),
            "origin_id": str(meta.get("origin_id", ""))[:128]}


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if isinstance(value, bool):
        return default
    return max(lo, min(hi, v))


def operator_max_bytes(tenant_id: str | None = None) -> int:
    """The operator's hard ceiling per object: ``spec.a2a.object_max_bytes`` in the tenant's
    ``tenant.corvin.yaml`` (default 256 MiB), never above ``ABSOLUTE_MAX_BYTES``. Unreadable config
    falls back to the default, never to "unlimited"."""
    try:
        import a2a_feed  # noqa: PLC0415
        cfg_path = a2a_feed._load_paths().tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
        if cfg_path.is_file():
            import yaml  # type: ignore  # noqa: PLC0415
            spec = (yaml.safe_load(cfg_path.read_text("utf-8")) or {}).get("spec") or {}
            raw = (spec.get("a2a") or {}).get("object_max_bytes")
            if raw is not None:
                return _clamp_int(raw, 1, ABSOLUTE_MAX_BYTES, DEFAULT_OPERATOR_MAX_BYTES)
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_OPERATOR_MAX_BYTES


def ceiling_for(origin_cfg: dict, tenant_id: str | None = None) -> int:
    """Per-origin ``max_object_bytes`` under the operator's hard ceiling."""
    op = operator_max_bytes(tenant_id)
    return _clamp_int((origin_cfg or {}).get("max_object_bytes", op), 1, op, op)


#: an object that is not text carries no label and nothing the text classifier can read: it is
#: INTERNAL, the same conservative default an unlabelled attachment gets (ADR-0077 C-6)
BINARY_CLASSIFICATION = "INTERNAL"


def _as_text(data: bytes) -> "str | None":
    """The scan window as text, or None for a binary object (NUL byte or not UTF-8; a multi-byte
    character cut by the window's end does not make it binary)."""
    window = data[:CLASSIFY_SCAN_BYTES]
    if b"\x00" in window:
        return None
    try:
        return window.decode("utf-8")
    except UnicodeDecodeError as exc:
        if len(data) > CLASSIFY_SCAN_BYTES and exc.start >= len(window) - 3:
            return window[:exc.start].decode("utf-8", errors="strict")
        return None


def classify_bytes(data: bytes) -> str:
    """L34 classification of an object's content: a TEXT object (its first ``CLASSIFY_SCAN_BYTES``)
    goes through the classifier every spawn gate uses (``data_classification.classify_task``: secret
    patterns, ``[class:…]`` marker, personal-data zone); a binary object is
    :data:`BINARY_CLASSIFICATION` (the text heuristics read noise as personal data). A classifier
    error is CONFIDENTIAL (fail-closed), never PUBLIC."""
    try:
        text = _as_text(data)
        if text is None:
            return BINARY_CLASSIFICATION
        from data_classification import classify_task  # type: ignore  # noqa: PLC0415

        return classify_task(text).name
    except Exception:  # noqa: BLE001
        return "CONFIDENTIAL"


def _classify_file(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            return classify_bytes(fh.read(CLASSIFY_SCAN_BYTES))
    except OSError:
        return "CONFIDENTIAL"


def peer_host(origin_id: str, origin_cfg: dict, endpoints_dir: "Path | None") -> str:
    """Host the object's bytes go to, for the L35 check: the origin's own ``peer_url``/``url``, else
    the pairing's endpoint file (a friendship holds both files under one kid). "" = unknown."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    for raw in ((origin_cfg or {}).get("peer_url"), (origin_cfg or {}).get("url")):
        if isinstance(raw, str) and raw:
            return (urlsplit(raw).hostname or "").lower()
    if endpoints_dir is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", origin_id or ""):
        try:
            ep = json.loads((Path(endpoints_dir) / f"{origin_id}.json").read_text("utf-8"))
            return (urlsplit(str(ep.get("url") or "")).hostname or "").lower()
        except (OSError, ValueError):
            pass
    return ""


def object_gate(*, origin_cfg: dict, origin_id: str, name: str, size: int, classification: str,
                tenant_id: str | None, endpoints_dir: "Path | None") -> str | None:
    """The ONE gate for an output object, inline or by reference, at issue and at pull.

    Returns a closed refusal code or None. Name rules, size ceiling, L34 classification against the
    origin's ``max_data_classification`` (default INTERNAL, like inbound attachments), L35 egress to
    the origin's host (no tenant egress policy = no restriction, exactly like every other L35 call
    site; a policy that cannot be evaluated refuses)."""
    from a2a_attachments import AttachmentError, validate_attachment_name  # noqa: PLC0415

    try:
        validate_attachment_name(name)
    except AttachmentError:
        return "object_bad_name"
    if not isinstance(size, int) or size < 0 or size > ceiling_for(origin_cfg, tenant_id):
        return "object_too_large"
    max_cls = (origin_cfg or {}).get("max_data_classification", "INTERNAL")
    if _level(classification) > _level(max_cls):
        return "object_classification_exceeded"
    try:
        from egress_gate import load_egress_gate_for_tenant  # type: ignore  # noqa: PLC0415

        gate = load_egress_gate_for_tenant(tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default")
        if gate is not None:
            host = peer_host(origin_id, origin_cfg, endpoints_dir) or "unknown"
            if not gate.validate(host, channel="a2a_object").allowed:
                return "object_egress_denied"
    except Exception:  # noqa: BLE001 — L35 that cannot be evaluated refuses
        return "object_egress_denied"
    return None


# ── Staging (worker side) ────────────────────────────────────────────────

def stage_file(src: Path, *, tenant_id: str | None = None) -> tuple[dict | None, str | None]:
    """Copy a worker output file into the holder's staging area, hashing while copying.

    The worker's scratch directory is deleted right after the harvest, so the bytes must move before
    the receiver decides; nothing staged is servable until :func:`issue` accepted it. Returns
    ``(staged, None)`` or ``(None, reason)``. The operator's hard ceiling and the store quota apply
    here already (the per-origin ceiling and every other gate apply in :func:`issue`)."""
    try:
        size = src.stat().st_size
    except OSError:
        return None, "unreadable"
    if size > operator_max_bytes(tenant_id):
        return None, "object_too_large"
    root = store_dir(tenant_id)
    dest = root / "staging" / f"{secrets.token_hex(16)}.part"
    try:
        with _lock:
            _ensure(root)
            if _store_bytes(root) + size > STORE_QUOTA_BYTES:
                return None, "object_store_full"
        h = hashlib.sha256()
        copied = 0
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with open(src, "rb") as fh:
                while True:
                    buf = fh.read(1024 * 1024)
                    if not buf:
                        break
                    copied += len(buf)
                    if copied > size:          # grew while we copied: refuse rather than guess
                        raise OSError("source changed")
                    h.update(buf)
                    view = memoryview(buf)
                    while view:
                        view = view[os.write(fd, view):]
        finally:
            os.close(fd)
        if copied != size:
            dest.unlink(missing_ok=True)
            return None, "unreadable"
    except OSError:
        dest.unlink(missing_ok=True)
        return None, "unreadable"
    except Exception:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        return None, "object_store_error"
    from a2a_attachments import _guess_mime  # noqa: PLC0415 — one MIME table

    return {"staged_path": str(dest), "name": src.name, "mime": _guess_mime(src),
            "size": size, "sha256": h.hexdigest()}, None


def discard_staged(staged: list[dict] | None) -> None:
    for s in staged or []:
        try:
            Path(str(s.get("staged_path", ""))).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _store_bytes(root: Path) -> int:
    total = 0
    for p in list(root.glob("*.bin")) + list((root / "staging").glob("*.part")):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


# ── Issue (holder) ───────────────────────────────────────────────────────

def issue(staged: dict, *, origin_id: str, origin_cfg: dict, task_id: str, floor: str = "PUBLIC",
          tenant_id: str | None = None, endpoints_dir: "Path | None" = None,
          audit_strict: Audit, audit: "Audit | None" = None) -> tuple[dict | None, str | None]:
    """Turn a staged file into a reference bound to ``origin_id``, or refuse it with a closed code.

    The classification is ``max(floor, L34 of the content)`` — ``floor`` is the task's inbound
    attachment classification. The reference is returned only after ``A2A.object_issued`` committed;
    on any refusal the staged bytes are deleted and ``A2A.object_refused`` is recorded."""
    root = store_dir(tenant_id)
    sp = Path(str(staged.get("staged_path", "")))
    name, size, sha = str(staged.get("name", "")), staged.get("size"), str(staged.get("sha256", ""))
    sweep_expired(tenant_id=tenant_id, audit=audit, force=True)
    classification = max_level(floor, _classify_file(sp))
    meta_view = {"ref_id": "", "size": size if isinstance(size, int) else 0, "sha256": sha,
                 "classification": classification, "origin_id": origin_id}

    def _refuse(reason: str) -> tuple[None, str]:
        sp.unlink(missing_ok=True)
        if audit is not None:
            audit("A2A.object_refused", "WARNING",
                  {**audit_fields(meta_view), "stage": "issue", "reason": reason, "task_id": task_id})
        return None, reason

    if not _HEX64_RE.fullmatch(sha) or not sp.is_file():
        return _refuse("object_store_error")
    reason = object_gate(origin_cfg=origin_cfg, origin_id=origin_id, name=name, size=size,
                         classification=classification, tenant_id=tenant_id, endpoints_dir=endpoints_dir)
    if reason:
        return _refuse(reason)
    ttl_s = _clamp_int((origin_cfg or {}).get("object_ttl_s", DEFAULT_TTL_S), MIN_TTL_S, MAX_TTL_S, DEFAULT_TTL_S)
    max_pulls = _clamp_int((origin_cfg or {}).get("object_max_pulls", DEFAULT_MAX_PULLS), 1, MAX_MAX_PULLS,
                           DEFAULT_MAX_PULLS)
    ref_id = secrets.token_urlsafe(24)
    now = time.time()
    meta = {"ref_id": ref_id, "origin_id": origin_id, "task_id": str(task_id)[:128], "name": name,
            "mime": str(staged.get("mime") or "application/octet-stream")[:128], "size": size,
            "sha256": sha, "classification": classification, "issued_at": now,
            "expires_at": now + ttl_s, "max_pulls": max_pulls, "served_bytes": 0, "pull_ids": [],
            "state": "issued"}
    try:
        with _lock:
            os.replace(sp, _bin_path(root, ref_id))
            _write_meta(root, meta)
    except OSError:
        _remove(root, ref_id)
        return _refuse("object_store_error")
    try:
        audit_strict("A2A.object_issued", "INFO",
                     {**audit_fields(meta), "task_id": meta["task_id"], "ttl_s": ttl_s, "max_pulls": max_pulls})
    except Exception:  # noqa: BLE001 — audit first: an unrecorded reference is never handed out
        _remove(root, ref_id)
        meta_view["ref_id"] = ref_id
        return None, "object_audit_failed"
    return {"name": name, "mime": meta["mime"], "size": size, "sha256": sha,
            "classification": classification, "ref_id": ref_id, "inline": False}, None


# ── Serve (holder, inside the signed ping) ───────────────────────────────

def pull_canonical(*, ping_id: str, issued_at: int, origin_id: str, ref_id: str, offset: int,
                   length: int) -> bytes:
    """What the requester's ``pull_sig`` covers: the ping's own three fields plus the op and its
    arguments — a captured pull cannot be re-aimed at another object or range."""
    return json.dumps({"ping_id": ping_id, "issued_at": issued_at, "origin_id": origin_id, "op": OP,
                       "ref_id": ref_id, "offset": offset, "length": length},
                      separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign_pull(hmac_key_hex: str, **fields: Any) -> str:
    return _hmac.new(bytes.fromhex(hmac_key_hex), pull_canonical(**fields), hashlib.sha256).hexdigest()


def _origin_matches(meta: dict, origin_id: str) -> bool:
    """A reference is served only to the origin it was issued to (constant-time compare)."""
    return _hmac.compare_digest(str(meta.get("origin_id", "")).encode(), str(origin_id).encode())


def serve(*, origin_id: str, origin_cfg: dict, ref_id: Any, offset: Any, length: Any, pull_id: str,
          tenant_id: str | None = None, endpoints_dir: "Path | None" = None,
          audit_strict: Audit, audit: "Audit | None" = None) -> tuple[dict | None, str | None]:
    """Answer one authenticated pull: ``({ref_id, offset, length, data_b64}, None)`` or
    ``(None, closed_code)``. The caller (``a2a_http_server.process_ping_request``) has verified the
    ping signature, its freshness and the pull signature; the answer it signs carries this result."""
    if not isinstance(ref_id, str) or not REF_ID_RE.fullmatch(ref_id):
        return None, "object_unavailable"
    root = store_dir(tenant_id)
    sweep_expired(tenant_id=tenant_id, audit=audit, force=True)
    with _lock:
        meta = _read_meta(root, ref_id)
        # unknown, expired (swept above) and another origin's reference: one answer
        if meta is None or not _origin_matches(meta, origin_id):
            return None, "object_unavailable"
        fields = audit_fields(meta)

        def _refuse(reason: str) -> tuple[None, str]:
            if audit is not None:
                audit("A2A.object_refused", "WARNING", {**fields, "stage": "pull", "reason": reason})
            return None, reason

        if pull_id in (meta.get("pull_ids") or []):
            return _refuse("object_pull_replayed")
        if meta.get("state") == "exhausted":
            return _refuse("object_pull_budget_exhausted")
        size = int(meta["size"])
        if (isinstance(offset, bool) or isinstance(length, bool) or not isinstance(offset, int)
                or not isinstance(length, int) or offset < 0 or length < 1 or length > CHUNK_MAX_BYTES
                or offset + length > size):
            return _refuse("object_bad_range")
        budget = int(meta.get("max_pulls") or DEFAULT_MAX_PULLS) * size
        if (int(meta.get("served_bytes") or 0) + length > budget
                or len(meta.get("pull_ids") or []) >= MAX_PULL_REQUESTS):
            return _refuse("object_pull_budget_exhausted")
        reason = object_gate(origin_cfg=origin_cfg, origin_id=origin_id, name=str(meta.get("name", "")),
                             size=size, classification=str(meta.get("classification")),
                             tenant_id=tenant_id, endpoints_dir=endpoints_dir)
        if reason:
            return _refuse(reason)
        try:
            with open(_bin_path(root, ref_id), "rb") as fh:
                fh.seek(offset)
                data = fh.read(length)
        except OSError:
            return None, "object_unavailable"
        if len(data) != length:
            return None, "object_unavailable"
        served = int(meta.get("served_bytes") or 0) + length
        try:
            audit_strict("A2A.object_pulled", "INFO",
                         {**fields, "offset": offset, "length": length, "served_bytes": served})
        except Exception:  # noqa: BLE001 — audit first: an unrecorded chunk is never served
            return None, "object_unavailable"
        meta["served_bytes"] = served
        meta["pull_ids"] = [*(meta.get("pull_ids") or []), str(pull_id)[:128]]
        exhausted = served >= budget
        if exhausted:
            meta["state"] = "exhausted"
        _write_meta(root, meta)          # the replay ledger is on disk before the chunk leaves
        if exhausted:
            _remove(root, ref_id, keep_meta=True)   # bytes go now; the tombstone answers until the TTL
            if audit is not None:
                audit("A2A.object_expired", "INFO", {**fields, "reason": "budget"})
    return {"ref_id": ref_id, "offset": offset, "length": length,
            "data_b64": base64.b64encode(data).decode("ascii")}, None


# ── Retention + erasure ──────────────────────────────────────────────────

def sweep_expired(*, tenant_id: str | None = None, audit: "Audit | None" = None, force: bool = False,
                  now: float | None = None) -> int:
    """Delete every reference whose TTL passed (bytes and tombstone), recording ``A2A.object_expired``
    for one that still held bytes. Unforced calls run at most once a minute per store."""
    root = store_dir(tenant_id)
    if not root.is_dir():
        return 0
    t = time.time() if now is None else now
    key = str(root)
    if not force and t - _last_sweep.get(key, 0.0) < 60:
        return 0
    _last_sweep[key] = t
    removed = 0
    with _lock:
        for mp in root.glob("*.json"):
            ref_id = mp.stem
            meta = _read_meta(root, ref_id)
            if meta is None:
                continue
            if float(meta.get("expires_at") or 0) > t:
                continue
            had_bytes = _bin_path(root, ref_id).exists()
            _remove(root, ref_id)
            removed += 1
            if had_bytes and audit is not None:
                audit("A2A.object_expired", "INFO", {**audit_fields(meta), "reason": "ttl"})
        # staging files older than the hard TTL belong to a receiver that died mid-issue
        for part in (root / "staging").glob("*.part") if (root / "staging").is_dir() else []:
            try:
                if t - part.stat().st_mtime > MAX_TTL_S:
                    part.unlink()
            except OSError:
                pass
    return removed


def erase_origin(origin_id: str, *, tenant_id: str | None = None) -> int:
    """GDPR Art. 17: every object held for ``origin_id`` (bytes and record). Returns the count."""
    root = store_dir(tenant_id)
    if not origin_id or not root.is_dir():
        return 0
    n = 0
    with _lock:
        for mp in root.glob("*.json"):
            meta = _read_meta(root, mp.stem)
            if meta is not None and meta.get("origin_id") == origin_id:
                _remove(root, mp.stem)
                n += 1
    return n


def clear(*, tenant_id: str | None = None) -> int:
    root = store_dir(tenant_id)
    if not root.is_dir():
        return 0
    with _lock:
        n = len(list(root.glob("*.json")))
        shutil.rmtree(root, ignore_errors=True)
    return n


# ── Requester side ───────────────────────────────────────────────────────

def is_reference(att: Any) -> bool:
    return isinstance(att, dict) and att.get("inline") is False


def validate_reference(d: Any) -> dict:
    """Shape check of a reference read from a SIGNED response. Raises ``ValueError`` with a closed
    reason. Exactly the reference keys; no inline content; a legal name; a sane size and digest."""
    from a2a_attachments import AttachmentError, validate_attachment_name  # noqa: PLC0415

    if not isinstance(d, dict) or set(d) != REF_KEYS or d.get("inline") is not False:
        raise ValueError("object_bad_reference")
    try:
        validate_attachment_name(d["name"])
    except AttachmentError as exc:
        raise ValueError("object_bad_reference") from exc
    size = d["size"]
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= ABSOLUTE_MAX_BYTES:
        raise ValueError("object_bad_reference")
    if not isinstance(d["sha256"], str) or not _HEX64_RE.fullmatch(d["sha256"]):
        raise ValueError("object_bad_reference")
    if d["classification"] not in _LEVELS or not isinstance(d["mime"], str) or len(d["mime"]) > 128:
        raise ValueError("object_bad_reference")
    if not isinstance(d["ref_id"], str) or not REF_ID_RE.fullmatch(d["ref_id"]):
        raise ValueError("object_bad_reference")
    return dict(d)


__all__ = [
    "OP", "CHUNK_MAX_BYTES", "DEFAULT_TTL_S", "MAX_TTL_S", "DEFAULT_MAX_PULLS", "REFUSALS",
    "store_dir", "stage_file", "discard_staged", "issue", "serve", "object_gate", "classify_bytes",
    "sweep_expired", "erase_origin", "clear", "validate_reference", "is_reference", "pull_canonical",
    "sign_pull", "ceiling_for", "operator_max_bytes",
]
