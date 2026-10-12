"""The intake stages (ADR-2254 D2). Every stage fails CLOSED: an error or a timeout refuses the item, a check is never skipped.

Nothing in an ``ItemReport`` can carry a file name or a cell value: reasons are closed codes, PII is a count per class, the
hash is a prefix. The projection (what a generator may see) is the Layer 24 snapshot after PII detection and redaction under
the operator's data policy; the raw bytes stay where the upload route put them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

#: Initial caps - measured on real runs (T-0153): a projection is a few KB; see docs/claude-ref/forge-intake.md.
MAX_ITEMS_PER_REQUEST = 5
MAX_ITEM_BYTES = 25 * 1024 * 1024
PACK_MAX_BYTES = 24 * 1024
ALLOWED_EXTENSIONS = {".csv": "csv", ".tsv": "tsv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl"}
_SCAN_WINDOW = 1 << 20
_SCAN_OVERLAP = 4096

DOCUMENT_EXTENSIONS = {".md": "text", ".markdown": "text", ".txt": "text", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx", ".pdf": "pdf"}

# Reason codes (closed vocabulary - the only text a refusal carries).
R_NOT_FILE = "not_a_regular_file"
R_OUTSIDE = "outside_upload_area"
R_SYMLINK = "symlink"
R_TOO_BIG = "too_large"
R_TYPE = "unsupported_type"
R_ARCHIVE = "archive_not_supported"
R_MISMATCH = "format_mismatch"
R_UNREADABLE = "unreadable"
R_SECRET = "contains_secret"
R_CLASSIFIED = "classification_refused"
R_ERROR = "analysis_error"


@dataclass
class ItemReport:
    item_id: str
    verdict: str                      # admitted | admitted_redacted | refused
    reason_code: str = ""
    fmt: str = ""
    ext: str = ""
    size_bytes: int = 0
    sha256_prefix: str = ""
    data_class: str = ""              # what the gate decided about the projection ("" when it was not reached)
    pii_kinds: dict[str, int] = field(default_factory=dict)       # class -> number of columns
    redactions: dict[str, int] = field(default_factory=dict)      # strategy -> number of columns
    columns: int = 0
    rows: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("item_id", "verdict", "reason_code", "fmt", "ext", "size_bytes",
                                              "sha256_prefix", "data_class", "pii_kinds", "redactions", "columns", "rows")}


@dataclass
class AnalyzedItem:
    report: ItemReport
    projection: Optional[dict[str, Any]] = None   # None when refused


def _refuse(item_id: str, code: str, **kw: Any) -> AnalyzedItem:
    return AnalyzedItem(ItemReport(item_id=item_id, verdict="refused", reason_code=code, **kw))


def _content_family(head_text: str) -> str:
    """What the first bytes look like: 'json' (an object or array starts) or 'delimited' (anything else text-like)."""
    t = head_text.lstrip("\ufeff \t\r\n")
    return "json" if t[:1] in ("{", "[") else "delimited"


def _secret_hits(path: Path) -> set[str]:
    """Credential-shaped content anywhere in the file, in overlapping 1 MiB windows (flat memory). The same detectors the
    bundle validator uses; a scan that cannot finish raises, and the caller refuses."""
    from core.pii.jwt_scan import LinearJwtPattern
    from core.pii.sensitive import detect_named_types

    named = frozenset({"private_key_block", "aws_access_key", "aws_secret_key", "github_token", "github_pat",
                       "slack_token", "google_api_key", "prefixed_secret_key"})
    jwt = LinearJwtPattern(10, 10, 10, start_boundary=False, end_boundary=False)
    hits: set[str] = set()
    step = _SCAN_WINDOW - _SCAN_OVERLAP
    with open(path, "rb") as fh:
        off = 0
        while True:
            fh.seek(off)
            chunk = fh.read(_SCAN_WINDOW)
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace")
            hits.update(detect_named_types(text, named))
            if jwt.search(text) is not None:
                hits.add("jwt")
            if len(chunk) < _SCAN_WINDOW:
                break
            off += step
    return hits


def analyze_item(
    path: Path, *, item_id: str, upload_root: Path, tenant_id: str,
    gate: Callable[[str], Optional[str]],
) -> AnalyzedItem:
    """Run every stage on one stored upload and return its report (and projection when admitted).

    ``gate(text)`` is the caller's pre-spawn gate (L44/L34/L35 ...) applied to the text the engine would read: it returns a
    refusal string or None. An exception from it is a refusal.
    """
    try:
        return _analyze(Path(path), item_id=item_id, upload_root=Path(upload_root), tenant_id=tenant_id, gate=gate)
    except Exception:  # noqa: BLE001 - fail closed: an analysis that crashed is a refusal, never an allow
        return _refuse(item_id, R_ERROR)


def _analyze(path: Path, *, item_id: str, upload_root: Path, tenant_id: str,
             gate: Callable[[str], Optional[str]]) -> AnalyzedItem:
    # ── 0. admit ────────────────────────────────────────────────────────────────────────────────
    if path.is_symlink():
        return _refuse(item_id, R_SYMLINK)
    try:
        resolved = path.resolve(strict=True)
        root = upload_root.resolve(strict=True)
    except OSError:
        return _refuse(item_id, R_UNREADABLE)
    if not resolved.is_relative_to(root):
        return _refuse(item_id, R_OUTSIDE)
    if not resolved.is_file():
        return _refuse(item_id, R_NOT_FILE)
    ext = path.suffix.lower()
    size = resolved.stat().st_size
    sha = hashlib.sha256()
    with open(resolved, "rb") as fh:
        head = fh.read(8)
        fh.seek(0)
        head_text = fh.read(4096).decode("utf-8", "replace")
        fh.seek(0)
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
    base = dict(ext=ext, size_bytes=size, sha256_prefix=sha.hexdigest()[:16])
    if size > MAX_ITEM_BYTES:
        return _refuse(item_id, R_TOO_BIG, **base)
    doc_kind = DOCUMENT_EXTENSIONS.get(ext)
    # An archive is refused by its CONTENT, whatever its name says (a gzip renamed .csv is still a gzip). The OOXML
    # documents are zip containers by design: they are admitted to the document path, which checks their parts.
    if doc_kind in ("docx", "pptx", "xlsx") and head[:2] == b"PK":
        return _analyze_document(resolved, item_id=item_id, tenant_id=tenant_id, gate=gate, kind=doc_kind, base=base)
    if head[:2] in (b"PK", b"\x1f\x8b") or head[:3] == b"BZh" or head[:6] == b"\xfd7zXZ\x00" or head[:4] == b"7z\xbc\xaf":
        return _refuse(item_id, R_ARCHIVE, **base)
    if doc_kind in ("text", "pdf"):
        if doc_kind == "pdf" and head[:4] != b"%PDF":
            return _refuse(item_id, R_MISMATCH, **base)
        return _analyze_document(resolved, item_id=item_id, tenant_id=tenant_id, gate=gate, kind=doc_kind, base=base)
    expected = ALLOWED_EXTENSIONS.get(ext)
    if expected is None:
        return _refuse(item_id, R_ARCHIVE if ext in (".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".tar") else R_TYPE, **base)

    # ── 1. sniff and parse (Layer 24) ───────────────────────────────────────────────────────────
    _ensure_forge_path()
    from forge.corvin_data import (SnapshotError, SnapshotOptions, UnsupportedFormat, apply_pii_detection,
                                   apply_redaction, detection_summary, generate_snapshot, load_policy, sniff_format)
    try:
        fmt = sniff_format(resolved)
    except UnsupportedFormat:
        return _refuse(item_id, R_TYPE, **base)
    # The sniffer trusts the extension (its own docs say so), so the CONTENT is compared independently: a .csv whose
    # bytes are JSON, or a .json that is not, is mislabelled - refused, not guessed at.
    if _content_family(head_text) != ("json" if expected in ("json", "jsonl") else "delimited"):
        return _refuse(item_id, R_MISMATCH, fmt=fmt, **base)
    base["fmt"] = fmt

    # ── 2. classify: secrets (every byte) ───────────────────────────────────────────────────────
    if _secret_hits(resolved):
        return _refuse(item_id, R_SECRET, **base)

    # ── 3. project: snapshot → PII detection → redaction, under the operator's data policy ─────
    try:
        snap = generate_snapshot(resolved, format_hint=fmt, options=SnapshotOptions())
    except SnapshotError:
        return _refuse(item_id, R_UNREADABLE, **base)
    pol = load_policy()
    apply_pii_detection(snap, overrides=pol.column_pii_class or None, use_presidio=(pol.pii_backend == "presidio"))
    pii = {k: v for k, v in detection_summary(snap).items() if k != "<no_pii>"}   # the summary also counts the clean columns
    seed = _seed(tenant_id)
    apply_redaction(snap, pol.to_redaction_policy(), seed=seed)
    projection = _projection(item_id, snap, pol)

    # ── 4. classify: the gate on the text the engine will read ─────────────────────────────────
    refusal = gate(json.dumps(projection, ensure_ascii=False, default=str))
    if refusal:
        return _refuse(item_id, R_CLASSIFIED, **base, columns=len(snap.schema))
    strategies: dict[str, int] = {}
    for c in projection["columns"]:
        if c.get("strategy"):
            strategies[c["strategy"]] = strategies.get(c["strategy"], 0) + 1
    redacted = bool(pii) or bool(strategies)
    return AnalyzedItem(ItemReport(
        item_id=item_id, verdict="admitted_redacted" if redacted else "admitted", data_class="projection_cleared",
        pii_kinds=dict(pii), redactions=strategies, columns=len(snap.schema), rows=snap.file.rowcount, **base),
        projection)


def _analyze_document(path: Path, *, item_id: str, tenant_id: str, gate: Callable[[str], Optional[str]],
                      kind: str, base: dict[str, Any]) -> AnalyzedItem:
    """Documents: extract text (bounded) -> secret scan over ALL of it -> redact PII in the part a generator may see ->
    gate on that projection. Same fail-closed rules as the tabular path."""
    from . import documents as D  # noqa: PLC0415
    try:
        text = D.extract_text(path, kind)
    except D.DocumentRefused as refused:
        return _refuse(item_id, refused.code, **base)
    if not text.strip():
        return _refuse(item_id, R_UNREADABLE, **base)
    if _secret_hits_text(text):
        return _refuse(item_id, R_SECRET, **base)
    from corvin_operator.bridges.shared.conversation_recall import redact_text  # noqa: PLC0415
    shown = D.bound_runs(text)[: D.DOC_TEXT_MAX]
    redacted, counts = redact_text(shown)
    projection = {"item_id": item_id, "format": kind, "kind": "document", "chars_total": len(text),
                  "truncated": len(text) > len(shown), "text": redacted}
    refusal = gate(json.dumps(projection, ensure_ascii=False))
    if refusal:
        return _refuse(item_id, R_CLASSIFIED, **base)
    base = {**base, "fmt": kind}
    return AnalyzedItem(ItemReport(
        item_id=item_id, verdict="admitted_redacted" if counts else "admitted", data_class="projection_cleared",
        pii_kinds=dict(counts), redactions=({"redact": sum(counts.values())} if counts else {}), columns=0, rows=None, **base),
        projection)


def _secret_hits_text(text: str) -> set[str]:
    from core.pii.jwt_scan import LinearJwtPattern
    from core.pii.sensitive import detect_named_types
    named = frozenset({"private_key_block", "aws_access_key", "aws_secret_key", "github_token", "github_pat",
                       "slack_token", "google_api_key", "prefixed_secret_key"})
    jwt = LinearJwtPattern(10, 10, 10, start_boundary=False, end_boundary=False)
    hits: set[str] = set()
    step = _SCAN_WINDOW - _SCAN_OVERLAP
    for off in range(0, max(1, len(text)), step):
        chunk = text[off: off + _SCAN_WINDOW]
        hits.update(detect_named_types(chunk, named))
        if jwt.search(chunk) is not None:
            hits.add("jwt")
        if off + _SCAN_WINDOW >= len(text):
            break
    return hits


def _ensure_forge_path() -> None:
    """Layer 24 lives in ``corvin_operator/forge`` (importable as ``forge``); make sure that root is on the path."""
    root = str(Path(__file__).resolve().parents[2] / "corvin_operator" / "forge")
    if root not in sys.path:
        sys.path.insert(0, root)


def _seed(tenant_id: str) -> Optional[str]:
    try:
        from forge.corvin_data.pseudonymize import default_vault_loader, resolve_seed
        seed, _src = resolve_seed(tenant_id=tenant_id, vault_loader=default_vault_loader, allow_derived=True)
        return seed
    except Exception:  # noqa: BLE001 - only the pseudonymize strategy needs it; apply_redaction raises when it is required
        return None


def _projection(item_id: str, snap: Any, pol: Any) -> dict[str, Any]:
    """The LLM-facing projection: schema (with the PII class and the strategy applied), the redacted sample, the stats."""
    rp = pol.to_redaction_policy()
    cols = []
    for c in snap.schema:
        strategy = rp.strategy_for(c.name, c.pii_class) if c.pii_class else None
        cols.append({"name": c.name, "type": getattr(c, "type", None), "pii_class": c.pii_class, "strategy": strategy})
    d = snap.to_dict()
    return {"item_id": item_id, "format": d["file"].get("format"), "rows": d["file"].get("rowcount"),
            "columns": cols, "sample": d["sample"], "stats": d["stats"]}


def build_pack(items: list[AnalyzedItem]) -> dict[str, Any]:
    """The only thing a generator receives: the projections of the ADMITTED items, bounded to ``PACK_MAX_BYTES``.

    Over the cap the pack degrades in a fixed order - sample rows first, then stats - and says so (``degraded``); the schema
    is never dropped. Refused items contribute nothing, not even their reports.
    """
    admitted = [i.projection for i in items if i.projection is not None]
    pack: dict[str, Any] = {"items": admitted, "degraded": []}
    size = lambda p: len(json.dumps(p, ensure_ascii=False, default=str).encode("utf-8"))  # noqa: E731
    for what in ("sample", "stats"):
        if size(pack) <= PACK_MAX_BYTES:
            break
        for it in pack["items"]:
            if what in it:
                it[what] = [] if what == "sample" else {}
        pack["degraded"].append(what)
    for _ in range(6):  # document text is the only thing left to shrink when a pack is all documents
        if size(pack) <= PACK_MAX_BYTES:
            break
        shrunk = False
        for it in pack["items"]:
            if it.get("kind") == "document" and len(it["text"]) > 512:
                it["text"] = it["text"][: len(it["text"]) // 2]
                it["truncated"] = True
                shrunk = True
        if shrunk and "document_text" not in pack["degraded"]:
            pack["degraded"].append("document_text")
        if not shrunk:
            break
    if size(pack) > PACK_MAX_BYTES:  # even the schema alone is too wide: cut columns, never silently
        keep = max(1, len(pack["items"]))
        for it in pack["items"]:
            if "columns" in it:
                it["columns"] = it["columns"][: max(1, 60 // keep)]
        pack["degraded"].append("columns")
    return pack
