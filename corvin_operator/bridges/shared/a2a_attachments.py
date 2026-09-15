"""Layer 38 v3 — Attachment handling (binary content over A2A).

Protocol v3 extends v2 with an ``attachments`` field on both
TaskEnvelope and ResponseEnvelope. Each attachment is a binary blob
identified by a sanitized filename, a MIME type, a SHA-256 digest, and
the content as base64-encoded bytes. The digest is signed into the
HMAC payload (the body is too, transitively via the digest match
verification), so any in-flight modification of a single byte fails
verification.

Structural rules
----------------

* **Total payload cap** — ``MAX_ATTACHMENTS_TOTAL_BYTES`` (1 MiB by
  default). Reject before HMAC verification: an attacker who could send
  arbitrary-sized envelopes could DoS receivers regardless of
  signature validity.
* **Per-envelope count cap** — ``MAX_ATTACHMENTS_COUNT`` (16). Same
  reasoning.
* **Per-attachment name** — ``[A-Za-z0-9._-]{1,128}``, no leading dot,
  no consecutive dots. Rejects path traversal (``../etc``), hidden
  files (``.ssh``), and SMB-style sneaky names.
* **Digest verification** — the SHA-256 stored in the envelope MUST
  match the SHA-256 of the b64-decoded content. Both sender and
  receiver MUST verify after decode.
* **Audit details** — only ``count``, ``total_bytes``, sanitized name
  list, and digest *prefix* (16 hex chars). NEVER content. NEVER full
  digest (a full digest leaks file identity to log readers).

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ── Data classification (ADR-0077 C-6) ───────────────────────────────────

# Four-level classification mirroring L34 DataClassification.
# Order matters: higher index = stricter classification.
_CLASSIFICATION_LEVELS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET")
_CLASSIFICATION_SET = frozenset(_CLASSIFICATION_LEVELS)


def classification_level(label: str | None) -> int:
    """Return the numeric level (0–3) for a classification label.

    Unknown labels and None are treated as ``INTERNAL`` (conservative
    default for attachments that predate C-6).
    """
    label = (label or "INTERNAL").upper()
    try:
        return _CLASSIFICATION_LEVELS.index(label)
    except ValueError:
        return _CLASSIFICATION_LEVELS.index("INTERNAL")


# ── Caps ──────────────────────────────────────────────────────────────────

MAX_ATTACHMENTS_COUNT = 16
MAX_ATTACHMENTS_TOTAL_BYTES = 1024 * 1024  # 1 MiB
MAX_ATTACHMENT_NAME_LEN = 128

# Allowed name pattern: alnum + dot + underscore + hyphen, no leading
# dot, no path separators. Rejects "..", "../foo", ".ssh", etc.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")


# ── Exceptions ────────────────────────────────────────────────────────────

class AttachmentError(Exception):
    """Raised on any attachment-validation failure.

    The receiver maps this to ``A2A.request_rejected`` with the
    exception's ``reason`` in the audit details — never in the response
    body (fail-silent contract).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── Dataclass ────────────────────────────────────────────────────────────

@dataclass
class Attachment:
    """A single binary blob carried inside an envelope.

    Fields:
      name:           sanitized filename (no path components)
      mime:           media type, e.g. "text/csv", "image/png"
      sha256:         hex digest of the raw (b64-decoded) content
      content_b64:    base64-encoded payload
      classification: ADR-0077 C-6 — optional data classification label.
                      One of PUBLIC / INTERNAL / CONFIDENTIAL / SECRET.
                      Absent or None → treated as INTERNAL (conservative).
    """
    name: str
    mime: str
    sha256: str
    content_b64: str
    classification: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Omit classification from wire format when None to stay backward-
        # compatible with Protocol v3 receivers that do not parse the field.
        if d.get("classification") is None:
            d.pop("classification", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Attachment":
        required = {"name", "mime", "sha256", "content_b64"}
        missing = required - set(d.keys())
        if missing:
            raise AttachmentError(
                f"attachment_missing_fields:{','.join(sorted(missing))}",
            )
        try:
            raw_cls = d.get("classification")
            if raw_cls is not None:
                raw_cls = str(raw_cls).upper()
                if raw_cls not in _CLASSIFICATION_SET:
                    raise AttachmentError(
                        f"attachment_bad_classification:{raw_cls}"
                    )
            return cls(
                name=str(d["name"]),
                mime=str(d["mime"]),
                sha256=str(d["sha256"]),
                content_b64=str(d["content_b64"]),
                classification=raw_cls,
            )
        except AttachmentError:
            raise
        except (TypeError, ValueError) as exc:
            raise AttachmentError(f"attachment_type_error:{exc}") from exc

    def decode(self) -> bytes:
        """Return the raw bytes. Verifies sha256 first (raises on mismatch)."""
        try:
            raw = base64.b64decode(self.content_b64, validate=True)
        except Exception as exc:
            raise AttachmentError(f"attachment_bad_b64:{exc}") from exc
        actual = hashlib.sha256(raw).hexdigest()
        # Constant-time comparison prevents timing oracle on digest values
        # (ADR-0099 iter-2 finding HIGH-A2A-ATTACH-01).
        if not _hmac.compare_digest(actual, self.sha256.lower()):
            raise AttachmentError("attachment_digest_mismatch")
        return raw

    @classmethod
    def from_bytes(
        cls, *, name: str, mime: str, content: bytes,
        classification: str | None = None,
    ) -> "Attachment":
        """Build an Attachment from raw bytes; computes the digest."""
        digest = hashlib.sha256(content).hexdigest()
        return cls(
            name=name,
            mime=mime,
            sha256=digest,
            content_b64=base64.b64encode(content).decode("ascii"),
            classification=classification,
        )

    @classmethod
    def from_file(
        cls, path: Path, *, mime: str | None = None,
        classification: str | None = None,
    ) -> "Attachment":
        """Build an Attachment from a filesystem path."""
        raw = path.read_bytes()
        mime_resolved = mime or _guess_mime(path)
        return cls.from_bytes(
            name=path.name, mime=mime_resolved, content=raw,
            classification=classification,
        )


# ── Validation ────────────────────────────────────────────────────────────

def validate_attachment_name(name: str) -> str:
    """Return the name if it passes; raise :class:`AttachmentError`."""
    if not isinstance(name, str):
        raise AttachmentError("attachment_name_not_string")
    if not name or len(name) > MAX_ATTACHMENT_NAME_LEN:
        raise AttachmentError("attachment_name_length")
    if not _NAME_RE.match(name):
        raise AttachmentError("attachment_name_chars")
    if ".." in name:
        # _NAME_RE already excludes "..", but defence-in-depth.
        raise AttachmentError("attachment_name_dotdot")
    return name


def validate_attachments(attachments: list) -> list[Attachment]:
    """Coerce + validate an incoming attachments list.

    Returns a list of Attachment instances. Raises AttachmentError on
    cap violations, name violations, b64 errors, or digest mismatch.
    """
    if not isinstance(attachments, list):
        raise AttachmentError("attachments_not_list")
    if len(attachments) > MAX_ATTACHMENTS_COUNT:
        raise AttachmentError("attachments_too_many")

    out: list[Attachment] = []
    total_bytes = 0
    seen_names: set[str] = set()

    for raw in attachments:
        if not isinstance(raw, dict):
            raise AttachmentError("attachment_not_object")
        att = Attachment.from_dict(raw)
        validate_attachment_name(att.name)
        if att.name in seen_names:
            raise AttachmentError("attachment_duplicate_name")
        seen_names.add(att.name)

        # Pre-check estimated size before decoding to prevent CPU/memory
        # exhaustion via 16 × 62.5 KiB base64 payloads (ADR-0099 iter-2
        # finding MED-A2A-ATTACH-02).  base64 expands by 4/3; we estimate
        # the exact decoded size by accounting for '=' padding characters,
        # so that a payload exactly at the 1 MiB boundary is not falsely
        # rejected.
        padding = att.content_b64.count("=")
        estimated = (len(att.content_b64) * 3 // 4) - padding
        if total_bytes + estimated > MAX_ATTACHMENTS_TOTAL_BYTES:
            raise AttachmentError("attachments_total_too_large")

        # Decode to verify b64 + digest, count bytes.
        decoded = att.decode()
        total_bytes += len(decoded)
        if total_bytes > MAX_ATTACHMENTS_TOTAL_BYTES:
            raise AttachmentError("attachments_total_too_large")

        out.append(att)

    return out


def effective_classification(attachments: list[Attachment]) -> str:
    """Return the highest (strictest) classification across all attachments.

    Absent / None classification is treated as INTERNAL (C-6 default).
    Returns ``"PUBLIC"`` for an empty list.
    """
    if not attachments:
        return "PUBLIC"
    level = max(classification_level(a.classification) for a in attachments)
    return _CLASSIFICATION_LEVELS[level]


def attachments_audit_details(attachments: list[Attachment]) -> dict[str, Any]:
    """Build the audit-allow-listed projection of an attachments list.

    Includes counts, total bytes, sanitized name list, and digest
    *prefix* (16 hex chars) — never content, never full digest.
    """
    total = 0
    names: list[str] = []
    digests: list[str] = []
    for att in attachments:
        # We trust the count_bytes from decode; in audit we recompute
        # cheaply from the b64 length (lower bound) to avoid re-decoding.
        # Use 3/4 of b64 length as a sufficient upper estimate.
        total += (len(att.content_b64) * 3) // 4
        names.append(att.name)
        digests.append(att.sha256[:16])
    return {
        "attachments_count":       len(attachments),
        "attachments_total_bytes": total,
        "attachment_names":        names,
        "attachment_sha_prefixes": digests,
    }


# ── MIME guessing (minimal stdlib) ────────────────────────────────────────

_MIME_BY_SUFFIX = {
    ".csv":  "text/csv",
    ".json": "application/json",
    ".txt":  "text/plain",
    ".md":   "text/markdown",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf":  "application/pdf",
    ".html": "text/html",
    ".xml":  "application/xml",
    ".yaml": "application/yaml",
    ".yml":  "application/yaml",
    ".log":  "text/plain",
}


def _guess_mime(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


__all__ = [
    "Attachment",
    "AttachmentError",
    "MAX_ATTACHMENTS_COUNT",
    "MAX_ATTACHMENTS_TOTAL_BYTES",
    "MAX_ATTACHMENT_NAME_LEN",
    "validate_attachment_name",
    "validate_attachments",
    "attachments_audit_details",
    "effective_classification",
    "classification_level",
]
