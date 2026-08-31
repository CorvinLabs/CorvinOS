"""`.corvin-pkg` package format — pack / unpack / sign / verify.

ADR-0007 Phase 5. The package format is a gzip-compressed tar with
a manifest at the root and a curated set of payload directories;
detached ed25519 signature alongside the archive.

Threat model
------------

* An operator builds a package on their own machine and signs it
  with an ed25519 private key. They publish the archive + the
  detached signature.
* An installer fetches both, verifies the signature against a
  trusted public key, unpacks the archive into the tenant's
  per-scope subtree.
* Tampering with the archive after signing invalidates the
  signature; the verifier refuses to unpack.

Sigstore is the *documented* production path — operators can keep
their private key in a transparency-logged context. The in-tree
verifier accepts any detached signature whose public key is in the
operator-managed keyring, so Sigstore clients plug in by writing
their verifiable key to the keyring directory.

Curated payload layout
----------------------

```
manifest.corvin.yaml
payload/
  skills/       (SkillForge skill bundles — directory per skill)
  personas/     (cowork personas — JSON files)
  tools/        (forge tool bundles — directory per tool)
```

The packer refuses to include any other top-level directory; the
unpacker refuses to extract symlinks, paths that escape the
extraction root, or files whose name contains path-traversal
sequences. Defence-in-depth against the tarbomb / symlink-escape
class.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import os
import re
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge.tenants import validate_tenant_id  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


# ── Event registration ───────────────────────────────────────────────


_PACKAGING_EVENTS = {
    "package.built":           "INFO",
    "package.verified":        "INFO",
    "package.verify_failed":   "WARNING",
    "package.installed":       "INFO",
    "package.install_failed":  "WARNING",
}
for _evt, _sev in _PACKAGING_EVENTS.items():
    _security_events.EVENT_SEVERITY.setdefault(_evt, _sev)


# ── Constants ────────────────────────────────────────────────────────


MANIFEST_FILENAME = "manifest.corvin.yaml"
PAYLOAD_DIRNAME = "payload"
ALLOWED_PAYLOAD_SUBDIRS = frozenset({"skills", "personas", "tools"})
SIGNATURE_SUFFIX = ".sig"
PACKAGE_SUFFIX = ".corvin-pkg"

_REQUIRED_MODE = 0o600
_PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_PUBLISHER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$")


# ── Exceptions ───────────────────────────────────────────────────────


class PackageError(Exception):
    """Base class for packaging failures."""


class PackageMalformed(PackageError):
    """Archive structure / manifest violates the schema."""


class PackageSignatureError(PackageError):
    """Signature missing / wrong key / tampered archive."""


class PackagePathEscape(PackageError):
    """Archive entry tries to escape the extraction root."""


# ── Manifest dataclass ──────────────────────────────────────────────


@dataclass(frozen=True)
class PackageManifest:
    name:          str
    publisher:     str
    version:       str
    created_at:    str
    contents:      dict[str, list[str]]
    runtime_min:   str
    payload_sha256: str

    def archive_basename(self) -> str:
        return f"{self.publisher}-{self.name}-{self.version}{PACKAGE_SUFFIX}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": "corvin/v1",
            "kind":       "Package",
            "metadata": {
                "name":       self.name,
                "publisher":  self.publisher,
                "version":    self.version,
                "created_at": self.created_at,
            },
            "spec": {
                "contents":     {k: list(v) for k, v in self.contents.items()},
                "dependencies": {
                    "corvin_apiversion": "corvin/v1",
                    "runtime_min":        self.runtime_min,
                },
                "checksums": {
                    "payload_sha256": self.payload_sha256,
                },
            },
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PackageManifest":
        if not isinstance(data, dict):
            raise PackageMalformed("manifest must be a mapping")
        if data.get("apiVersion") != "corvin/v1":
            raise PackageMalformed("apiVersion must be 'corvin/v1'")
        if data.get("kind") != "Package":
            raise PackageMalformed("kind must be 'Package'")
        md = data.get("metadata") or {}
        spec = data.get("spec") or {}
        for field, regex in (
            ("name",      _PACKAGE_NAME_RE),
            ("publisher", _PUBLISHER_RE),
            ("version",   _VERSION_RE),
        ):
            v = md.get(field)
            if not isinstance(v, str) or not regex.match(v):
                raise PackageMalformed(
                    f"metadata.{field}={v!r} fails {regex.pattern}"
                )
        contents_raw = (spec.get("contents") or {})
        if not isinstance(contents_raw, dict):
            raise PackageMalformed("spec.contents must be a mapping")
        contents: dict[str, list[str]] = {}
        for k in ALLOWED_PAYLOAD_SUBDIRS:
            v = contents_raw.get(k, [])
            if not isinstance(v, list):
                raise PackageMalformed(
                    f"spec.contents.{k} must be a list"
                )
            for item in v:
                if not isinstance(item, str) or not item:
                    raise PackageMalformed(
                        f"spec.contents.{k} entries must be non-empty strings"
                    )
            contents[k] = list(v)
        deps = spec.get("dependencies") or {}
        runtime_min = deps.get("runtime_min", "0.0")
        if not isinstance(runtime_min, str):
            raise PackageMalformed("runtime_min must be a string")
        cks = spec.get("checksums") or {}
        sha = cks.get("payload_sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            raise PackageMalformed(
                "checksums.payload_sha256 must be a 64-char hex string"
            )
        return cls(
            name=md["name"],
            publisher=md["publisher"],
            version=md["version"],
            created_at=str(md.get("created_at", "")),
            contents=contents,
            runtime_min=runtime_min,
            payload_sha256=sha,
        )


# ── Keypair helpers ──────────────────────────────────────────────────


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh ed25519 keypair. Returns (private_pem, public_pem)."""
    private = Ed25519PrivateKey.generate()
    pri_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pri_pem, pub_pem


def _load_private(pem: bytes) -> Ed25519PrivateKey:
    obj = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(obj, Ed25519PrivateKey):
        raise PackageError("expected ed25519 private key")
    return obj


def _load_public(pem: bytes) -> Ed25519PublicKey:
    obj = serialization.load_pem_public_key(pem)
    if not isinstance(obj, Ed25519PublicKey):
        raise PackageError("expected ed25519 public key")
    return obj


# ── Path-safety helpers ──────────────────────────────────────────────


def _safe_join(root: Path, member: str) -> Path:
    """Resolve member against root; reject if it escapes."""
    full = (root / member).resolve()
    root_resolved = root.resolve()
    try:
        full.relative_to(root_resolved)
    except ValueError as e:
        raise PackagePathEscape(
            f"path {member!r} escapes extraction root"
        ) from e
    return full


def _walk_payload(payload_dir: Path) -> list[tuple[str, Path]]:
    """Return a deterministic [(relpath, fullpath), ...] of the payload.

    Only files under the allowed sub-directories are admitted; any
    other top-level entry raises :class:`PackageMalformed`.
    """
    if not payload_dir.is_dir():
        raise PackageMalformed(f"payload root {payload_dir} not a directory")
    out: list[tuple[str, Path]] = []
    for child in sorted(payload_dir.iterdir()):
        if not child.is_dir():
            raise PackageMalformed(
                f"payload contains non-dir entry {child.name!r}"
            )
        if child.name not in ALLOWED_PAYLOAD_SUBDIRS:
            raise PackageMalformed(
                f"payload top-level {child.name!r} not in "
                f"{sorted(ALLOWED_PAYLOAD_SUBDIRS)}"
            )
        for path in sorted(child.rglob("*")):
            if path.is_symlink():
                raise PackageMalformed(
                    f"symlink not allowed: {path.relative_to(payload_dir)}"
                )
            if not path.is_file():
                continue
            rel = path.relative_to(payload_dir).as_posix()
            out.append((rel, path))
    return out


def _payload_sha256(payload_dir: Path) -> str:
    """Hash the payload deterministically: sorted relpath || NUL || bytes."""
    h = hashlib.sha256()
    for rel, full in _walk_payload(payload_dir):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(full.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()


# ── Pack ────────────────────────────────────────────────────────────


def build_package(
    *,
    source_dir: Path,
    name: str,
    publisher: str,
    version: str,
    runtime_min: str = "0.10",
    output_dir: Path,
    private_key_pem: bytes,
) -> tuple[Path, Path, PackageManifest]:
    """Pack ``<source_dir>/payload/`` into a signed `.corvin-pkg`.

    Returns ``(archive_path, signature_path, manifest)``.
    """
    if not source_dir.is_dir():
        raise PackageMalformed(f"source dir {source_dir} not found")
    if not _PACKAGE_NAME_RE.match(name):
        raise PackageMalformed(f"name {name!r} fails charset")
    if not _PUBLISHER_RE.match(publisher):
        raise PackageMalformed(f"publisher {publisher!r} fails charset")
    if not _VERSION_RE.match(version):
        raise PackageMalformed(f"version {version!r} fails semver-shape")
    payload_dir = source_dir / PAYLOAD_DIRNAME
    if not payload_dir.is_dir():
        raise PackageMalformed(
            f"source dir must contain a 'payload/' subdir"
        )
    # Walk + checksum + content discovery
    entries = _walk_payload(payload_dir)
    sha = _payload_sha256(payload_dir)
    contents: dict[str, list[str]] = {k: [] for k in ALLOWED_PAYLOAD_SUBDIRS}
    seen: dict[str, set[str]] = {k: set() for k in ALLOWED_PAYLOAD_SUBDIRS}
    for rel, _ in entries:
        # First path component is the sub-dir; second is the resource name
        parts = rel.split("/", 2)
        if len(parts) >= 2:
            sub, item = parts[0], parts[1]
            if sub in seen and item not in seen[sub]:
                seen[sub].add(item)
                contents[sub].append(item)
    manifest = PackageManifest(
        name=name, publisher=publisher, version=version,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        contents=contents,
        runtime_min=runtime_min,
        payload_sha256=sha,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / manifest.archive_basename()
    # Build the tar.gz
    with gzip.open(archive_path, "wb") as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            manifest_bytes = yaml.safe_dump(
                manifest.to_dict(), sort_keys=False, allow_unicode=True,
            ).encode("utf-8")
            info = tarfile.TarInfo(name=MANIFEST_FILENAME)
            info.size = len(manifest_bytes)
            info.mode = 0o644
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(manifest_bytes))
            for rel, full in entries:
                arcname = f"{PAYLOAD_DIRNAME}/{rel}"
                info = tarfile.TarInfo(name=arcname)
                data = full.read_bytes()
                info.size = len(data)
                info.mode = 0o644
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(data))
    os.chmod(archive_path, 0o644)
    # Sign
    private = _load_private(private_key_pem)
    archive_bytes = archive_path.read_bytes()
    sig = private.sign(archive_bytes)
    sig_path = archive_path.with_suffix(archive_path.suffix + SIGNATURE_SUFFIX)
    sig_path.write_bytes(base64.b64encode(sig))
    os.chmod(sig_path, 0o644)
    # Audit (no tenant binding yet; runs against the _default chain)
    _audit_default(
        "package.built",
        details={
            "name": name, "publisher": publisher, "version": version,
            "payload_sha256": sha,
            "size_bytes": archive_path.stat().st_size,
        },
    )
    return archive_path, sig_path, manifest


# ── Verify + unpack ──────────────────────────────────────────────────


def verify_signature(
    archive_path: Path,
    signature_path: Path,
    public_key_pem: bytes,
) -> bool:
    """Return True iff signature verifies. Never raises."""
    try:
        public = _load_public(public_key_pem)
        archive_bytes = archive_path.read_bytes()
        sig = base64.b64decode(signature_path.read_bytes())
        public.verify(sig, archive_bytes)
        return True
    except (InvalidSignature, Exception):
        return False


def _read_manifest_from_archive(archive_path: Path) -> PackageManifest:
    with gzip.open(archive_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            try:
                member = tar.getmember(MANIFEST_FILENAME)
            except KeyError as e:
                raise PackageMalformed(
                    f"{archive_path}: missing {MANIFEST_FILENAME}"
                ) from e
            data = tar.extractfile(member).read()
    try:
        parsed = yaml.safe_load(data)
    except yaml.YAMLError as e:
        raise PackageMalformed(f"manifest YAML invalid: {e}") from e
    return PackageManifest.from_dict(parsed)


def verify_package(
    archive_path: Path,
    signature_path: Path,
    public_key_pem: bytes,
) -> PackageManifest:
    """Full verify: signature + manifest + payload checksum match.

    Raises :class:`PackageSignatureError` on signature failure,
    :class:`PackageMalformed` on manifest / checksum failure.
    """
    if not verify_signature(archive_path, signature_path, public_key_pem):
        _audit_default(
            "package.verify_failed",
            details={"path": str(archive_path), "reason": "signature"},
            severity="WARNING",
        )
        raise PackageSignatureError(
            f"signature verification failed: {archive_path}"
        )
    manifest = _read_manifest_from_archive(archive_path)
    # Walk the archive payload and re-checksum
    h = hashlib.sha256()
    with gzip.open(archive_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            payload_members = sorted(
                (m for m in tar.getmembers()
                 if m.name.startswith(f"{PAYLOAD_DIRNAME}/")),
                key=lambda m: m.name,
            )
            for member in payload_members:
                if member.issym() or member.islnk():
                    raise PackageMalformed(
                        f"forbidden link member {member.name!r}"
                    )
                if not member.isfile():
                    continue
                # Strip the leading payload/ for hashing parity with builder
                rel = member.name[len(PAYLOAD_DIRNAME) + 1:]
                h.update(rel.encode("utf-8"))
                h.update(b"\x00")
                h.update(tar.extractfile(member).read())
                h.update(b"\x00")
    if h.hexdigest() != manifest.payload_sha256:
        _audit_default(
            "package.verify_failed",
            details={"path": str(archive_path),
                     "reason": "payload-checksum-mismatch"},
            severity="WARNING",
        )
        raise PackageMalformed(
            f"payload sha256 mismatch: archive={h.hexdigest()} "
            f"manifest={manifest.payload_sha256}"
        )
    _audit_default(
        "package.verified",
        details={
            "name": manifest.name,
            "publisher": manifest.publisher,
            "version": manifest.version,
        },
    )
    return manifest


# ── Install ─────────────────────────────────────────────────────────


def install_package(
    archive_path: Path,
    signature_path: Path,
    public_key_pem: bytes,
    *,
    tenant_id: str,
    scope: str = "tenant",
) -> PackageManifest:
    """Verify + extract into the tenant's per-scope subtree.

    Scopes:
      * ``tenant`` — installs under ``<tenant_home>/global/packages/<publisher>-<name>-<version>/``
        and (best-effort) copies the payload into the right runtime
        locations (skills → skill-forge, personas → cowork,
        tools → forge).

    Other scopes (session, project) are reserved for a future
    extension when sub-scope packaging makes sense.
    """
    validate_tenant_id(tenant_id)
    if scope != "tenant":
        raise PackageError(f"unsupported scope: {scope!r}")
    manifest = verify_package(archive_path, signature_path, public_key_pem)
    tenant_global = _forge_paths.tenant_global_dir(tenant_id)
    if not tenant_global.parent.exists():
        raise PackageError(
            f"tenant directory missing: {tenant_global.parent} "
            f"(Phase 1.4 migration helper owns tenant creation)"
        )
    pkg_root = (
        tenant_global / "packages"
        / f"{manifest.publisher}-{manifest.name}-{manifest.version}"
    )
    pkg_root.mkdir(parents=True, exist_ok=True)
    try:
        with gzip.open(archive_path, "rb") as gz:
            with tarfile.open(fileobj=gz, mode="r") as tar:
                for member in tar.getmembers():
                    if member.issym() or member.islnk():
                        raise PackageMalformed(
                            f"forbidden link member {member.name!r}"
                        )
                    # path-traversal guard
                    target = _safe_join(pkg_root, member.name)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = tar.extractfile(member).read()
                    target.write_bytes(data)
                    os.chmod(target, 0o644)
    except (PackageMalformed, PackagePathEscape, PackageError):
        _audit_default(
            "package.install_failed",
            details={
                "path":      str(archive_path),
                "tenant_id": tenant_id,
                "reason":    "extract-failure",
            },
            severity="WARNING",
        )
        raise
    _audit_default(
        "package.installed",
        details={
            "tenant_id": tenant_id,
            "name":      manifest.name,
            "publisher": manifest.publisher,
            "version":   manifest.version,
            "scope":     scope,
        },
    )
    return manifest


# ── Audit helper ─────────────────────────────────────────────────────


def _audit_default(
    event_type: str,
    *,
    details: dict[str, Any] | None = None,
    severity: str | None = None,
) -> None:
    try:
        chain = (
            _forge_paths.tenant_global_dir("_default") / "forge" / "audit.jsonl"
        )
        _security_events.write_event(
            chain, event_type,
            severity=severity, details=dict(details or {}),
            hash_chain=True,
        )
    except Exception:
        pass
