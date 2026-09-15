"""flow_bundle.py — CorvinFlow M3: FlowBundle pack / install / verify.

.corvinflow archive format (ZIP):
  flow.yaml         — FlowDefinition (structurally validated on install)
  manifest.json     — id, version, author, corvinflow requirement, depends_on
  nodes.yaml        — capability requirements per slot (optional)
  flow.sig          — Ed25519 signature over SHA-256(flow.yaml + manifest.json)
                      base64url-encoded, signed by the bundle author's key

Security invariants:
  - Signature is verified BEFORE flow.yaml is parsed (fail-closed)
  - flow.yaml is structurally validated after signature (template allow-list)
  - No code execution at install time — YAML only
"""
from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from .flow_definition import FlowDefinition, FlowDefinitionError
except ImportError:
    from flow_definition import FlowDefinition, FlowDefinitionError  # type: ignore[no-redef]


class FlowBundleError(Exception):
    """Raised when a bundle fails structural or signature validation."""


@dataclass
class BundleManifest:
    id: str
    version: str
    author: str = "unknown"
    requires: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "author": self.author,
            "requires": self.requires,
            "depends_on": self.depends_on,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BundleManifest":
        return cls(
            id=d.get("id", ""),
            version=d.get("version", "0.0.0"),
            author=d.get("author", "unknown"),
            requires=d.get("requires", {}),
            depends_on=d.get("depends_on", []),
        )


class FlowBundle:
    """Pack, sign, install, and verify .corvinflow bundles."""

    BUNDLE_EXT = ".corvinflow"

    # ── packing ───────────────────────────────────────────────────────────────

    @classmethod
    def pack(
        cls,
        flow_dir: Path,
        output_dir: Path | None = None,
        signing_key_bytes: bytes | None = None,
    ) -> Path:
        """Create a .corvinflow archive from a directory.

        flow_dir must contain at minimum flow.yaml + manifest.json.
        Signing is optional for development; required for distribution.
        """
        flow_yaml_path = flow_dir / "flow.yaml"
        manifest_path = flow_dir / "manifest.json"

        if not flow_yaml_path.exists():
            raise FlowBundleError(f"flow.yaml not found in {flow_dir}")
        if not manifest_path.exists():
            raise FlowBundleError(f"manifest.json not found in {flow_dir}")

        flow_yaml = flow_yaml_path.read_text()
        manifest_text = manifest_path.read_text()
        manifest_dict = json.loads(manifest_text)
        bm = BundleManifest.from_dict(manifest_dict)

        # Structural validation before packing — catch bad flows early
        try:
            FlowDefinition.from_yaml(flow_yaml)
        except FlowDefinitionError as exc:
            raise FlowBundleError(f"flow.yaml is invalid: {exc}") from exc

        bundle_name = f"{bm.id}-{bm.version}{cls.BUNDLE_EXT}"
        dest = (output_dir or flow_dir.parent) / bundle_name

        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("flow.yaml", flow_yaml)
            zf.writestr("manifest.json", manifest_text)

            nodes_yaml_path = flow_dir / "nodes.yaml"
            if nodes_yaml_path.exists():
                zf.writestr("nodes.yaml", nodes_yaml_path.read_text())

            if signing_key_bytes is not None:
                sig = cls._sign(flow_yaml, manifest_text, signing_key_bytes)
                zf.writestr("flow.sig", sig)

        return dest

    # ── installation ──────────────────────────────────────────────────────────

    @classmethod
    def install(
        cls,
        bundle_path: Path,
        dest_dir: Path,
        pub_key_bytes: bytes | None = None,
    ) -> FlowDefinition:
        """Install a bundle: verify signature (if key given), then parse.

        Returns the validated FlowDefinition ready for use.
        Raises FlowBundleError on any structural or signature failure.
        """
        if pub_key_bytes is not None:
            cls.verify(bundle_path, pub_key_bytes)

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            if "flow.yaml" not in names:
                raise FlowBundleError("Bundle missing flow.yaml")
            if "manifest.json" not in names:
                raise FlowBundleError("Bundle missing manifest.json")

            flow_yaml = zf.read("flow.yaml").decode()
            manifest_text = zf.read("manifest.json").decode()
            manifest_dict = json.loads(manifest_text)

        bm = BundleManifest.from_dict(manifest_dict)

        try:
            fd = FlowDefinition.from_yaml(flow_yaml)
        except FlowDefinitionError as exc:
            raise FlowBundleError(f"Bundle flow.yaml invalid: {exc}") from exc

        # Write to dest_dir — preserve the original manifest_text byte-for-byte
        # so a post-install re-verify against the bundle's _payload() still passes.
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "flow.yaml").write_text(flow_yaml)
        (dest_dir / "manifest.json").write_text(manifest_text)

        return fd

    # ── verification ──────────────────────────────────────────────────────────

    @classmethod
    def verify(cls, bundle_path: Path, pub_key_bytes: bytes) -> None:
        """Verify the Ed25519 signature in flow.sig. Raises FlowBundleError on failure."""
        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            if "flow.sig" not in names:
                raise FlowBundleError("Bundle has no flow.sig — cannot verify")
            flow_yaml = zf.read("flow.yaml").decode()
            manifest_text = zf.read("manifest.json").decode()
            sig_b64 = zf.read("flow.sig").decode().strip()

        try:
            sig = base64.urlsafe_b64decode(sig_b64 + "==")
        except Exception as exc:
            raise FlowBundleError(f"flow.sig is not valid base64url: {exc}") from exc

        payload = cls._payload(flow_yaml, manifest_text)
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.exceptions import InvalidSignature
            pub = Ed25519PublicKey.from_public_bytes(pub_key_bytes)
            pub.verify(sig, payload)
        except InvalidSignature:
            raise FlowBundleError("flow.sig signature verification failed")
        except Exception as exc:
            raise FlowBundleError(f"Signature verification error: {exc}") from exc

    # ── helpers ───────────────────────────────────────────────────────────────

    @classmethod
    def _payload(cls, flow_yaml: str, manifest_text: str) -> bytes:
        # Fixed-length separator prevents boundary-shift collisions between the
        # two fields (e.g. flow_yaml="AB", manifest="CD" vs flow_yaml="ABCD",
        # manifest="" would otherwise hash identically).
        _SEP = b"\x00CORVINFLOW_MANIFEST\x00"
        return hashlib.sha256(flow_yaml.encode() + _SEP + manifest_text.encode()).digest()

    @classmethod
    def _sign(cls, flow_yaml: str, manifest_text: str, priv_key_bytes: bytes) -> str:
        payload = cls._payload(flow_yaml, manifest_text)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        priv = Ed25519PrivateKey.from_private_bytes(priv_key_bytes)
        sig = priv.sign(payload)
        return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    @classmethod
    def generate_keypair(cls) -> tuple[bytes, bytes]:
        """Generate an Ed25519 keypair for bundle signing. Returns (priv_32, pub_32)."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        priv = Ed25519PrivateKey.generate()
        return priv.private_bytes_raw(), priv.public_key().public_bytes_raw()
