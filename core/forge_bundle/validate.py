"""Fail-closed Forge Bundle validation (ADR-2229 D3).

``validate_bundle`` runs six stages in order over the raw ZIP bytes and raises
:class:`BundleRejected` at the first defect. It extracts nothing, writes
nothing and reads no registry: the target install's inventory for the
staleness stage is injected by the caller.

  container  → ZIP is safe to read at all (paths, symlinks, sizes, ratio)
  envelope   → forge-bundle.json is a valid format-v1 manifest
  integrity  → every listed file exists with its size + sha256; nothing unlisted
  references → intra-bundle requirements resolve and form no cycle
  staleness  → requirements outside the bundle exist on the target
  secrets    → no credential-shaped string in any payload, at any nesting level

Every payload is scanned, binary or not: as raw bytes (latin-1, so one invalid
UTF-8 byte cannot switch the scan off), with NUL bytes removed (ASCII inside
UTF-16), and — when it parses as JSON — after unescaping (``\u0041KIA…``).
A payload that IS a ZIP archive (decided by its content, never by its name: a
wheel, a renamed ``.bin``) is opened and held to the container rules, and all
nested archives together share the bundle's uncompressed-size budget — they are
what SkillInstaller / StagingManager will later unpack.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Collection, Iterator, Mapping

from core.bundle_common import BundleCheckError
from core.bundle_common import names as _names
from core.bundle_common import ratio as _ratio
from core.bundle_common import secrets as _secrets
from core.pii.sensitive import detect_named_types

from .envelope import (
    ARTIFACTS_PREFIX,
    ENVELOPE_NAME,
    LIMITS,
    ArtifactEntry,
    BundleEnvelope,
    EnvelopeError,
    Requirement,
    parse_envelope,
)

STAGES = ("container", "envelope", "integrity", "references", "staleness", "secrets")

# The secret types live in core.bundle_common.secrets (ADR-2323 Decision 11); this name stays for one release.
_SECRET_TYPES = _secrets.SECRET_TYPES  # deprecated alias, remove after the next release

Known = Mapping[str, Mapping[str, Collection[str]]]


class BundleRejected(BundleCheckError):
    """The bundle failed a validation stage; nothing may be imported from it."""

    def __init__(self, stage: str, reason: str):
        super().__init__(stage, reason)


@dataclass(frozen=True)
class BundleReport:
    envelope: BundleEnvelope
    total_uncompressed_bytes: int
    # Requirements outside the bundle that could not be checked (no inventory given).
    unchecked_references: tuple[Requirement, ...]
    # ADR-2229 D4: format v1 carries no origin proof. Always False; render it.
    origin_verified: bool = False


def _reject(stage: str, reason: str) -> BundleRejected:
    return BundleRejected(stage, reason)


@contextlib.contextmanager
def _as_rejected() -> Iterator[None]:
    """A refusal of the shared validators (core.bundle_common) becomes this module's BundleRejected, reason unchanged."""
    try:
        yield
    except BundleRejected:
        raise
    except BundleCheckError as exc:
        raise BundleRejected(exc.stage, exc.reason) from None


def _wrapped(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        with _as_rejected():
            return fn(*args, **kwargs)
    return inner


# The shared validators of ADR-2323 Decision 11, called with THIS module's LIMITS and detector (read at call time, so a
# test that replaces either keeps working). The private names are aliases kept for one release; remove after it.
@_wrapped
def _check_name(name: str, where: str) -> None:
    _names.check_name(name, where, limits=LIMITS)


@_wrapped
def _check_container(zf: zipfile.ZipFile, *, where: str) -> list[zipfile.ZipInfo]:
    """Container-level safety for one archive; returns its file (non-dir) entries."""
    return _names.check_container(zf, where=where, limits=LIMITS)


def _open_zip(data: bytes, *, where: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError) as exc:
        raise _reject("container", f"{where}: not a readable ZIP ({type(exc).__name__})") from exc


def _read(zf: zipfile.ZipFile, name: str, stage: str) -> bytes:
    try:
        return zf.read(name)
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as exc:
        # CRC mismatch, truncated stream, unsupported compression.
        raise _reject(stage, f"cannot read {name!r} ({type(exc).__name__})") from exc


def _load_envelope(zf: zipfile.ZipFile, names: set[str]) -> BundleEnvelope:
    if ENVELOPE_NAME not in names:
        raise _reject("envelope", f"{ENVELOPE_NAME} missing at the archive root")
    if zf.getinfo(ENVELOPE_NAME).file_size > LIMITS["max_envelope_bytes"]:
        raise _reject("envelope", f"{ENVELOPE_NAME} larger than {LIMITS['max_envelope_bytes']} bytes")
    raw = _read(zf, ENVELOPE_NAME, "envelope")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _reject("envelope", f"{ENVELOPE_NAME} is not UTF-8 JSON ({type(exc).__name__})") from None
    try:
        return parse_envelope(decoded)
    except EnvelopeError as exc:
        raise _reject("envelope", str(exc)) from exc
    except (RecursionError, TypeError, ValueError) as exc:
        raise _reject("envelope", f"{ENVELOPE_NAME} has an unexpected shape ({type(exc).__name__})") from None


def _check_integrity(zf: zipfile.ZipFile, envelope: BundleEnvelope, file_infos: list[zipfile.ZipInfo]) -> None:
    listed: dict[str, tuple[str, int]] = {}
    folded: set[str] = set()
    for art in envelope.artifacts:
        for f in art.files:
            if f.path.casefold() in folded:
                raise _reject("integrity", f"{f.path!r} listed twice")
            folded.add(f.path.casefold())
            listed[f.path] = (f.sha256, f.size)

    present = {i.filename for i in file_infos}
    unlisted = sorted(present - set(listed) - {ENVELOPE_NAME})
    if unlisted:
        raise _reject("integrity", f"files not declared in the envelope: {unlisted[:5]}")
    missing = sorted(set(listed) - present)
    if missing:
        raise _reject("integrity", f"declared files missing from the archive: {missing[:5]}")

    for path, (sha, size) in listed.items():
        data = _read(zf, path, "integrity")
        if len(data) != size:
            raise _reject("integrity", f"{path!r}: size {len(data)} != declared {size}")
        if hashlib.sha256(data).hexdigest() != sha:
            raise _reject("integrity", f"{path!r}: sha256 mismatch")


def _resolve_references(envelope: BundleEnvelope) -> list[Requirement]:
    """Check intra-bundle requirements; return the ones that point outside the bundle."""
    by_key: dict[tuple[str, str], ArtifactEntry] = {a.key: a for a in envelope.artifacts}
    external: list[Requirement] = []
    edges: dict[tuple[str, str], list[tuple[str, str]]] = {a.key: [] for a in envelope.artifacts}
    for art in envelope.artifacts:
        for req in art.requires:
            target = by_key.get((req.kind, req.id))
            if target is None:
                external.append(req)
                continue
            if req.version is not None and req.version != target.version:
                raise _reject(
                    "references",
                    f"{art.kind} {art.id!r} requires {req.kind} {req.id}@{req.version}, "
                    f"bundle carries {target.version}",
                )
            edges[art.key].append(target.key)

    # Iterative DFS: a recursive one is a stack-depth DoS on a hostile envelope.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {k: WHITE for k in edges}
    for start in edges:
        if colour[start] != WHITE:
            continue
        stack: list[tuple[tuple[str, str], Iterator[tuple[str, str]]]] = [(start, iter(edges[start]))]
        colour[start] = GREY
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = BLACK
                stack.pop()
            elif colour[nxt] == GREY:
                raise _reject("references", f"dependency cycle through {nxt[0]} {nxt[1]!r}")
            elif colour[nxt] == WHITE:
                colour[nxt] = GREY
                stack.append((nxt, iter(edges[nxt])))
    return external


def _check_staleness(external: list[Requirement], known: Known) -> None:
    stale = []
    for req in external:
        versions = known.get(req.kind, {}).get(req.id)
        if versions is None or (req.version is not None and req.version not in versions):
            stale.append(f"{req.kind} {req.id}" + (f"@{req.version}" if req.version else ""))
    if stale:
        raise _reject("staleness", f"requires what the target install does not have: {stale[:10]}")


_REGEX_SECRET_TYPES = _secrets.REGEX_SECRET_TYPES   # deprecated alias
_JWT = _secrets.JWT                                 # deprecated alias
_has_jwt = _secrets.has_jwt                         # deprecated alias
_ASCII = _secrets.ASCII                             # deprecated alias
_unescape = _secrets.unescape                       # deprecated alias
_ascii_view = _secrets.ascii_view                   # deprecated alias
_text_views = _secrets.text_views                   # deprecated alias
_WINDOW = _secrets.WINDOW                           # deprecated alias
_OVERLAP = _secrets.OVERLAP                         # deprecated alias
_decompressor = _ratio.decompressor                 # deprecated alias


@_wrapped
def _scan_text(text: str, where: str) -> None:
    _secrets.scan_text(text, where, detect=detect_named_types)


@_wrapped
def _scan_bytes(data: bytes, where: str) -> None:
    """Scan in overlapping 1 MiB windows: memory stays flat on a 200 MiB payload."""
    _secrets.scan_bytes(data, where, detect=detect_named_types)


@_wrapped
def _check_ratio(packed: int, unpacked: int, where: str) -> None:
    """The container rule (<= 100:1 beyond 1 MiB) for a whole nested archive or stream:
    caps how much scanning a small upload can buy."""
    _ratio.check_ratio(packed, unpacked, where, limits=LIMITS)


class _Budget(_ratio.Budget):
    """Uncompressed bytes left for every nested archive / stream of one bundle together."""

    def __init__(self, left: int) -> None:
        super().__init__(left, limits=LIMITS)

    def spend(self, n: int, where: str) -> None:
        with _as_rejected():
            super().spend(n, where)


@_wrapped
def _inflate(data: bytes, where: str, budget: _Budget) -> bytes | None:
    """Decompress EVERY member of a gzip/bz2/xz stream within the budget; None if it is not one."""
    return _ratio.inflate(data, where, budget, limits=LIMITS)


def _is_tar(data: bytes) -> bool:
    return len(data) >= 265 and data[257:262] == b"ustar"


def _scan_tar(data: bytes, where: str, depth: int, budget: _Budget) -> None:
    import tarfile

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
            members = [m for m in tf.getmembers() if m.isfile()]
            if len(members) > LIMITS["max_entries"]:
                raise _reject("container", f"{where}: more than {LIMITS['max_entries']} entries")
            budget.spend(sum(m.size for m in members), where)
            for m in members:
                fh = tf.extractfile(m)
                if fh is not None:
                    _scan_payload(fh.read(), f"{where}!{m.name}", depth + 1, budget)
    except BundleRejected:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise _reject("container", f"{where}: unreadable tar archive ({type(exc).__name__})") from None


def _scan_payload(data: bytes, where: str, depth: int, budget: _Budget) -> None:
    _scan_bytes(data, where)  # raw view: also covers bytes around/between archive members
    is_stream = _decompressor(data)[0] is not None
    is_archive = zipfile.is_zipfile(io.BytesIO(data)) or _is_tar(data)
    limit = LIMITS["max_nested_zip_depth"]
    if (is_archive and depth >= limit) or (is_stream and depth > limit):
        raise _reject("container", f"{where}: archive or compressed stream nested deeper than {limit}")
    inflated = _inflate(data, where, budget) if is_stream else None
    if inflated is not None:
        _scan_payload(inflated, f"{where}!decompressed", depth + 1, budget)
        return
    if _is_tar(data):
        _scan_tar(data, where, depth, budget)
        return
    if zipfile.is_zipfile(io.BytesIO(data)):
        inner = _open_zip(data, where=where)
        with inner:
            inner_files = _check_container(inner, where=where)
            _check_ratio(len(data), sum(i.file_size for i in inner_files), where)
            budget.spend(sum(i.file_size for i in inner_files), where)
            for info in inner_files:
                _scan_payload(_read(inner, info.filename, "secrets"), f"{where}!{info.filename}", depth + 1, budget)


def validate_bundle(data: bytes, *, known: Known | None = None) -> BundleReport:
    """Validate a Forge Bundle ZIP fail-closed.

    ``known`` is the target install's inventory, ``{kind: {id: versions}}``.
    Without it the staleness stage cannot run and every requirement pointing
    outside the bundle is returned in ``unchecked_references`` — a caller that
    imports must supply it.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise _reject("container", "bundle must be bytes")
    if len(data) > LIMITS["max_compressed_bytes"]:
        raise _reject("container", f"archive larger than {LIMITS['max_compressed_bytes']} bytes")

    with _open_zip(bytes(data), where="bundle") as zf:
        file_infos = _check_container(zf, where="bundle")
        names = {i.filename for i in file_infos}
        for name in names - {ENVELOPE_NAME}:
            if not name.startswith(ARTIFACTS_PREFIX):
                raise _reject("container", f"entry outside {ARTIFACTS_PREFIX!r}: {name!r}")

        envelope = _load_envelope(zf, names)
        _check_integrity(zf, envelope, file_infos)
        external = _resolve_references(envelope)
        if known is not None:
            _check_staleness(external, known)

        total = sum(i.file_size for i in file_infos)
        budget = _Budget(LIMITS["max_uncompressed_bytes"] - total)
        _scan_bytes(_read(zf, ENVELOPE_NAME, "secrets"), ENVELOPE_NAME)
        for art in envelope.artifacts:
            for f in art.files:
                _scan_payload(_read(zf, f.path, "secrets"), f.path, 0, budget)

        return BundleReport(
            envelope=envelope,
            total_uncompressed_bytes=total,
            unchecked_references=tuple(external) if known is None else (),
        )
