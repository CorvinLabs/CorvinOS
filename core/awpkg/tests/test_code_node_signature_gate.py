"""WF-A1(b): awpkg must not install a package whose workflow contains a `code`
node (arbitrary sandboxed Python) unless the package is signed with a verifying
signature OR the operator explicitly acknowledges the risk.

Run:  python3 -m pytest core/awpkg/tests/test_code_node_signature_gate.py
"""
from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG = _HERE.parent
sys.path.insert(0, str(_PKG))
# corvin_workflows must be importable so the install-time workflow validator runs.
sys.path.insert(0, str(_PKG.parents[1] / "core" / "workflows"))

import yaml  # noqa: E402

from awpkg import installer  # noqa: E402
from awpkg.installer import InstallError, install  # noqa: E402
from awpkg.manifest import manifest_signing_digest  # noqa: E402
from tests.helpers import make_awpkg  # noqa: E402

# A minimal valid AWP workflow whose graph contains a `code` node.
_CODE_WORKFLOW = {
    "awp": "1.0.0",
    "workflow": {"name": "codeflow", "description": "has a code node"},
    "orchestration": {
        "engine": "dag",
        "graph": [
            {
                "id": "compute",
                "type": "code",
                "language": "python3",
                "source": "def main(x: int) -> dict:\n    return {'y': x + 1}\n",
                "inputs": {"x": "x"},
                "outputs": ["y"],
                "depends_on": [],
            }
        ],
    },
}

_BASE_MANIFEST = {
    "awpkg": "1.0",
    "id": "test.codenode",
    "name": "Code Node Package",
    "version": "0.0.1",
    "description": "package that ships a code node",
    "components": {"workflows": ["workflows/codeflow.awp.yaml"]},
    "permissions": {"network": False},
}


def _extra():
    return {"workflows/codeflow.awp.yaml": yaml.dump(_CODE_WORKFLOW).encode("utf-8")}


class CodeNodeSignatureGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))

    def test_unsigned_code_node_package_rejected(self) -> None:
        pkg = make_awpkg(dict(_BASE_MANIFEST), _extra(), tmp_path=self._home)
        with self.assertRaises(InstallError) as ctx:
            install(pkg, corvin_home=self._home)
        self.assertIn("code", str(ctx.exception).lower())
        self.assertIn("signed", str(ctx.exception).lower())

    def test_operator_acknowledgment_allows_install(self) -> None:
        pkg = make_awpkg(dict(_BASE_MANIFEST), _extra(), tmp_path=self._home)
        result = install(pkg, corvin_home=self._home, allow_unsigned_code=True)
        self.assertEqual(result.id, "test.codenode")

    def test_valid_signature_allows_install(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        priv = Ed25519PrivateKey.generate()
        pub_der = priv.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def _b64url(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

        manifest = dict(_BASE_MANIFEST)
        digest = manifest_signing_digest(manifest)  # signature field absent → fine
        sig = priv.sign(digest)
        manifest["signature"] = {
            "algorithm": "ed25519",
            "public_key": _b64url(pub_der),
            "value": _b64url(sig),
        }
        pkg = make_awpkg(manifest, _extra(), tmp_path=self._home)
        result = install(pkg, corvin_home=self._home)  # no ack needed — signed
        self.assertEqual(result.id, "test.codenode")

    def test_tampered_signature_rejected(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        priv = Ed25519PrivateKey.generate()
        pub_der = priv.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def _b64url(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

        manifest = dict(_BASE_MANIFEST)
        sig = priv.sign(manifest_signing_digest(manifest))
        manifest["signature"] = {
            "algorithm": "ed25519",
            "public_key": _b64url(pub_der),
            "value": _b64url(sig),
        }
        # Tamper AFTER signing — the description change invalidates the digest.
        manifest["description"] = "malicious swap after signing"
        pkg = make_awpkg(manifest, _extra(), tmp_path=self._home)
        with self.assertRaises(InstallError):
            install(pkg, corvin_home=self._home)

    def test_non_code_package_unaffected(self) -> None:
        # A workflow with only agent nodes installs without signature/ack.
        wf = {
            "awp": "1.0.0",
            "workflow": {"name": "agentflow", "description": "no code"},
            "orchestration": {
                "engine": "dag",
                "graph": [
                    {"id": "a", "type": "agent", "agent": "x",
                     "instructions": "do", "depends_on": []}
                ],
            },
        }
        manifest = dict(_BASE_MANIFEST)
        manifest["id"] = "test.agentonly"
        manifest["components"] = {"workflows": ["workflows/agentflow.awp.yaml"]}
        extra = {"workflows/agentflow.awp.yaml": yaml.dump(wf).encode("utf-8")}
        pkg = make_awpkg(manifest, extra, tmp_path=self._home)
        result = install(pkg, corvin_home=self._home)
        self.assertEqual(result.id, "test.agentonly")


if __name__ == "__main__":
    unittest.main(verbosity=2)
